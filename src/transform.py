"""
Couche de transformation.

Lit les enregistrements bruts et produit les agregats servis par le site :
medianes, distributions, series temporelles, parts de marche.

C'est ici que vit tout ce qui a besoin de voir l'ENSEMBLE des donnees, par
opposition a l'ingestion qui ne voit qu'un enregistrement a la fois. La
classification ban-jeonse, reportee depuis la premiere session, en est
l'exemple : voir NOTES.md entree 6.

Ce module ne connait ni AWS ni le systeme de fichiers : il recoit un
iterateur d'enregistrements et retourne un dictionnaire de fichiers a ecrire.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone

log = logging.getLogger(__name__)


# --- Parametres d'analyse -------------------------------------------------

# En dessous de ce nombre de transactions, une mediane n'a aucun sens
# statistique. On publie alors le comptage mais pas les prix : mieux vaut
# afficher "donnees insuffisantes" qu'un chiffre trompeur.
MIN_SAMPLE = 10

# Fenetre consideree comme "actuelle" pour les instantanes de prix.
# 12 mois : assez de volume pour des medianes solides par quartier, assez
# court pour rester representatif du marche.
CURRENT_WINDOW_MONTHS = 12

# Les 2 derniers mois sont marques comme provisoires : le delai de
# declaration fait qu'ils continuent de se remplir apres coup.
PROVISIONAL_MONTHS = 2

# Tranches de surface, en m2 de surface privative.
# Bornes choisies sur les usages coreens : 85 m2 est le seuil du logement
# national reglementaire, 60 m2 celui des aides au logement.
AREA_BUCKETS = [
    ("studio", 0, 20),        # one-room typique
    ("petit", 20, 30),        # studio confortable / T1
    ("moyen", 30, 45),        # T2
    ("grand", 45, 60),        # T3
    ("familial", 60, 85),     # logement familial
    ("tres_grand", 85, 9999),
]

# Noms francophones des arrondissements, pour l'affichage sur le site.
# L'API ne renvoie que le nom coreen (sigungu_name) : ce dictionnaire est la
# seule source du nom francophone, il n'existe nulle part ailleurs. Forme
# alignee sur Wikipedia francophone ("Jongno-gu", "Jung-gu"...) : le suffixe
# -gu est conserve car plusieurs noms courts (Jung, Guro) sont ambigus ou
# se lisent comme un mot francais une fois isoles.
DISTRICT_NAMES_FR = {
    "11110": "Jongno-gu",       "11140": "Jung-gu",
    "11170": "Yongsan-gu",      "11200": "Seongdong-gu",
    "11215": "Gwangjin-gu",     "11230": "Dongdaemun-gu",
    "11260": "Jungnang-gu",     "11290": "Seongbuk-gu",
    "11305": "Gangbuk-gu",      "11320": "Dobong-gu",
    "11350": "Nowon-gu",        "11380": "Eunpyeong-gu",
    "11410": "Seodaemun-gu",    "11440": "Mapo-gu",
    "11470": "Yangcheon-gu",    "11500": "Gangseo-gu",
    "11530": "Guro-gu",         "11545": "Geumcheon-gu",
    "11560": "Yeongdeungpo-gu", "11590": "Dongjak-gu",
    "11620": "Gwanak-gu",       "11650": "Seocho-gu",
    "11680": "Gangnam-gu",      "11710": "Songpa-gu",
    "11740": "Gangdong-gu",
}

# Seuil de classification ban-jeonse : un depot superieur a cette fraction
# du jeonse median comparable, assorti d'un loyer mensuel.
#
# DECISION DOCUMENTEE : le seuil est discutable. 60 % separe nettement le
# "gros depot proche du jeonse local" du wolse ordinaire a petit depot.
# Il est calcule par rapport a un COMPARABLE (meme arrondissement, meme
# type de bien, meme tranche de surface), pas par un ratio depot/loyer.
BAN_JEONSE_THRESHOLD = 0.60


def area_bucket(area_sqm):
    if area_sqm is None:
        return None
    for name, low, high in AREA_BUCKETS:
        if low <= area_sqm < high:
            return name
    return None


def percentile(sorted_values, fraction):
    """
    Percentile par interpolation lineaire. Implemente a la main plutot
    qu'avec numpy : une dependance en moins a empaqueter dans la Lambda,
    pour un calcul trivial.
    """
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]

    position = (len(sorted_values) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    weight = position - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def distribution(values):
    """
    Resume statistique d'un ensemble de prix.

    On publie p25 / mediane / p75 plutot qu'une moyenne : le marche
    immobilier est fortement asymetrique, quelques transactions de luxe
    tirent une moyenne vers le haut et la rendent trompeuse. L'ecart
    interquartile dit aussi quelque chose d'utile : un quartier homogene
    ou tres disparate.
    """
    if len(values) < MIN_SAMPLE:
        return {"count": len(values), "insufficient": True}

    values = sorted(values)
    return {
        "count": len(values),
        "p25": round(percentile(values, 0.25)),
        "median": round(percentile(values, 0.50)),
        "p75": round(percentile(values, 0.75)),
        "min": round(values[0]),
        "max": round(values[-1]),
    }


# --- Classification ban-jeonse -------------------------------------------

def build_jeonse_reference(records):
    """
    Calcule le depot jeonse median par comparable, pour servir de reference.

    Comparable = arrondissement + type de bien + tranche de surface.
    On ne segmente pas par mois : le volume deviendrait trop faible sur
    beaucoup de combinaisons, et le niveau du jeonse bouge lentement.

    C'est exactement le contexte qui manquait a l'ingestion.

    Retourne (reference, nombre d'enregistrements lus).
    """
    buckets = defaultdict(list)
    seen = 0

    for record in records:
        seen += 1
        if record.get("is_cancelled") or record["lease_type"] != "jeonse":
            continue
        bucket = area_bucket(record["area_sqm"])
        if not bucket or not record["deposit_krw"]:
            continue
        key = (record["sigungu_code"], record["property_type"], bucket)
        buckets[key].append(record["deposit_krw"])

    reference = {}
    for key, deposits in buckets.items():
        if len(deposits) >= MIN_SAMPLE:
            reference[key] = percentile(sorted(deposits), 0.50)

    log.info("reference jeonse construite sur %d comparables", len(reference))
    return reference, seen


def refine_lease_type(record, reference):
    """
    Affine la classification a l'aide du contexte.

    jeonse     : aucun loyer mensuel
    ban_jeonse : loyer mensuel ET depot proche du jeonse comparable
    wolse      : loyer mensuel et depot modeste
    inconnu    : pas de reference disponible pour ce comparable
    """
    if record["lease_type"] == "jeonse":
        return "jeonse"

    bucket = area_bucket(record["area_sqm"])
    key = (record["sigungu_code"], record["property_type"], bucket)
    jeonse_median = reference.get(key)

    if not jeonse_median or not record["deposit_krw"]:
        return "wolse"

    if record["deposit_krw"] >= jeonse_median * BAN_JEONSE_THRESHOLD:
        return "ban_jeonse"
    return "wolse"


# --- Agregation -----------------------------------------------------------

def month_of(record):
    """'2026-01-14' -> '202601'"""
    date = record["deal_date"]
    return date[:4] + date[5:7] if date else None


def transform(record_source):
    """
    Point d'entree. Recoit une FONCTION qui produit un flux
    d'enregistrements, et non une liste.

    POURQUOI CETTE SIGNATURE : la classification ban-jeonse exige deux
    passes — l'une pour construire la reference jeonse, l'autre pour
    agreger. Charger 1,27 million d'enregistrements en memoire pour les
    parcourir deux fois represente plusieurs Go en Python, ce qui rend
    l'execution en Lambda couteuse et fragile.

    En recevant une fabrique de flux, on relit la source deux fois et on
    n'accumule que les valeurs necessaires aux calculs. La memoire devient
    proportionnelle au nombre de groupes, pas au nombre d'enregistrements.

    Retourne un dictionnaire {chemin_de_sortie: contenu}.
    """
    # --- Passe 1 : reference jeonse ---------------------------------------
    reference, seen = build_jeonse_reference(record_source())
    log.info("passe 1 terminee : %d enregistrements", seen)

    # --- Passe 2 : agregation ---------------------------------------------
    months = set()
    by_district_type_area = defaultdict(list)
    by_district_month = defaultdict(list)
    by_segment_month = defaultdict(list)
    by_district_dong = defaultdict(list)
    lease_counts = defaultdict(lambda: defaultdict(int))
    district_names = {}
    staged = []

    # La fenetre "recente" depend des mois presents, qu'on ne connait
    # qu'apres avoir tout lu. On met donc de cote les elements concernes
    # sous forme compacte, et on tranche a la fin.
    for record in record_source():
        if record.get("is_cancelled"):
            continue

        month = month_of(record)
        if not month:
            continue
        months.add(month)

        district = record["sigungu_code"]
        district_names[district] = record["sigungu_name"]
        equivalent = record["monthly_equivalent_krw"]
        bucket = area_bucket(record["area_sqm"])

        by_district_month[(district, month)].append(equivalent)
        if bucket:
            by_segment_month[(district, record["property_type"], bucket, month)].append(equivalent)

        staged.append((
            month, district, record["property_type"], bucket,
            record["dong_name"], equivalent,
            refine_lease_type(record, reference),
        ))

    months = sorted(months)
    latest_months = set(months[-CURRENT_WINDOW_MONTHS:])
    provisional = set(months[-PROVISIONAL_MONTHS:])

    for month, district, property_type, bucket, dong, equivalent, lease in staged:
        if month not in latest_months:
            continue
        if bucket:
            by_district_type_area[(district, property_type, bucket)].append(equivalent)
        if dong:
            by_district_dong[(district, dong)].append(equivalent)
        lease_counts[(district, property_type)][lease] += 1

    log.info("passe 2 terminee : %d mois, %d arrondissements",
             len(months), len(district_names))

    return assemble(
        district_names, months, provisional,
        by_district_type_area, by_district_month, by_segment_month,
        by_district_dong, lease_counts,
    )


def assemble(district_names, months, provisional,
             by_type_area, by_month, by_segment_month,
             by_dong, lease_counts):
    """Met en forme les fichiers de sortie destines au site."""
    outputs = {}
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # --- Vue d'ensemble, une ligne par arrondissement ---------------------
    overview = []
    for district, name in sorted(district_names.items()):
        prices = {}
        for (d, property_type, bucket), values in by_type_area.items():
            if d != district:
                continue
            stats = distribution(values)
            if not stats.get("insufficient"):
                # count voyage avec median : le site a besoin des deux pour
                # la colonne "Transactions" du classement, et distribution()
                # le calcule deja - il ne restait qu'a le transmettre.
                prices.setdefault(property_type, {})[bucket] = {
                    "median": stats["median"],
                    "count": stats["count"],
                }

        overview.append({
            "code": district,
            "name": name,
            "name_fr": DISTRICT_NAMES_FR.get(district, name),
            "median_by_type_area": prices,
        })

    outputs["districts.json"] = {
        "generated_at": generated_at,
        "months_covered": months,
        "provisional_months": sorted(provisional),
        "window_months": CURRENT_WINDOW_MONTHS,
        "districts": overview,
    }

    # --- Fiche detaillee par arrondissement -------------------------------
    for district, name in district_names.items():
        timeseries = [
            {
                "month": month,
                "provisional": month in provisional,
                **distribution(values),
            }
            for (d, month), values in sorted(by_month.items())
            if d == district
        ]

        # Series segmentees : une courbe par couple type x tranche.
        # C'est la vue exploitable — "l'evolution du studio en officetel"
        # plutot qu'un agregat qui melange studios et logements familiaux
        # et dont les variations refletent surtout la composition du mois.
        segments = defaultdict(list)
        for (d, property_type, bucket, month), values in sorted(by_segment_month.items()):
            if d != district:
                continue
            segments[(property_type, bucket)].append({
                "month": month,
                "provisional": month in provisional,
                **distribution(values),
            })

        segmented = [
            {
                "property_type": property_type,
                "area_bucket": bucket,
                # Une serie n'est publiee que si la majorite de ses points
                # sont exploitables. Une courbe pleine de trous induit
                # davantage en erreur qu'elle n'informe.
                "points": points,
            }
            for (property_type, bucket), points in sorted(segments.items())
            if sum(1 for p in points if not p.get("insufficient")) >= len(points) / 2
        ]

        breakdown = [
            {
                "property_type": property_type,
                "area_bucket": bucket,
                **distribution(values),
            }
            for (d, property_type, bucket), values in sorted(by_type_area.items())
            if d == district
        ]

        dongs = [
            {"dong": dong, **distribution(values)}
            for (d, dong), values in sorted(by_dong.items())
            if d == district
        ]

        lease_mix = {
            property_type: dict(counts)
            for (d, property_type), counts in lease_counts.items()
            if d == district
        }

        outputs[f"district/{district}.json"] = {
            "generated_at": generated_at,
            "code": district,
            "name": name,
            "name_fr": DISTRICT_NAMES_FR.get(district, name),
            "timeseries": timeseries,
            "timeseries_by_segment": segmented,
            "breakdown": breakdown,
            "dongs": dongs,
            "lease_mix": lease_mix,
        }

    # --- Metadonnees et methodologie --------------------------------------
    outputs["meta.json"] = {
        "generated_at": generated_at,
        "months_covered": months,
        "provisional_months": sorted(provisional),
        "district_count": len(district_names),
        "methodology": {
            "min_sample": MIN_SAMPLE,
            "current_window_months": CURRENT_WINDOW_MONTHS,
            "ban_jeonse_threshold": BAN_JEONSE_THRESHOLD,
            "area_buckets": [
                {"name": n, "min_sqm": lo, "max_sqm": hi}
                for n, lo, hi in AREA_BUCKETS
            ],
            "notes": [
                "Transactions declarees au ministere du Territoire.",
                "Les 2 derniers mois sont provisoires : delai de declaration.",
                "Medianes masquees en dessous de 10 transactions.",
                "monthly_equivalent convertit le depot en cout mensuel.",
            ],
        },
    }

    return outputs
