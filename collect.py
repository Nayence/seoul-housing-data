#!/usr/bin/env python3
"""
Collecteur de transactions locatives (전월세) - data.go.kr

Recupere les transactions declarees pour un arrondissement et un mois donnes,
sur les 4 types de biens, et les normalise vers un schema unifie.

Usage:
    export DATA_GO_KR_KEY="ta_cle_decoding"
    python collect.py --district 11110 --month 202601
    python collect.py --district 11110 --month 202601 --type officetel
    python collect.py --all-seoul --month 202601 --out ./data

La cle n'est JAMAIS ecrite dans le code : elle vient de l'environnement.
"""

import argparse
import hashlib
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

BASE_URL = "https://apis.data.go.kr/1613000"
PAGE_SIZE = 1000
MAX_RETRIES = 4
RETRY_BACKOFF = 2  # secondes, double a chaque tentative
THROTTLE = 0.2     # pause entre deux appels, pour rester poli

# PIEGE : le serveur de data.go.kr rejette avec un 403 les requetes portant
# le User-Agent par defaut de Python ("Python-urllib/3.x"). Le navigateur
# passait, le script non. On envoie donc un User-Agent explicite.
USER_AGENT = "seoul-housing-data/1.0 (personal non-commercial project)"

DEBUG = False  # active par --debug

# Taux de conversion depot -> equivalent mensuel (전월세전환율), en % annuel.
# DECISION DOCUMENTEE : la loi coreenne encadre ce taux et il evolue.
# A verifier et mettre a jour ; la valeur ici sert de defaut explicite.
CONVERSION_RATE_ANNUAL = 5.5

# NOTE : le ban-jeonse n'est pas classifie ici. Voir classify_lease() —
# il se derive dans la couche de transformation, qui dispose des medianes.


# --- Configuration des sources -------------------------------------------
# Une entree par API. Ajouter un type de bien = ajouter une ligne ici,
# jamais toucher a la logique.

SOURCES = {
    "apartment": {
        "service": "RTMSDataSvcAptRent",
        "name_field": "aptNm",
        "area_field": "excluUseAr",
        "area_type": "exclusive",
        "has_floor": True,
        "has_jibun": True,
    },
    "officetel": {
        "service": "RTMSDataSvcOffiRent",
        "name_field": "offiNm",
        "area_field": "excluUseAr",
        "area_type": "exclusive",
        "has_floor": True,
        "has_jibun": True,
    },
    "villa": {
        "service": "RTMSDataSvcRHRent",
        "name_field": "mhouseNm",
        "area_field": "excluUseAr",
        "area_type": "exclusive",
        "has_floor": True,
        "has_jibun": True,
    },
    "house": {
        "service": "RTMSDataSvcSHRent",
        "name_field": None,        # les maisons n'ont pas de nom de residence
        "area_field": "totalFloorAr",
        "area_type": "total",
        "has_floor": False,
        "has_jibun": False,
    },
}

# Codes legaux des 25 arrondissements de Seoul.
# Externalises pour pouvoir etendre a Busan (26xxx), Incheon (28xxx), etc.
# sans modifier une ligne de code.
SEOUL_DISTRICTS = {
    "11110": "종로구",   "11140": "중구",     "11170": "용산구",
    "11200": "성동구",   "11215": "광진구",   "11230": "동대문구",
    "11260": "중랑구",   "11290": "성북구",   "11305": "강북구",
    "11320": "도봉구",   "11350": "노원구",   "11380": "은평구",
    "11410": "서대문구", "11440": "마포구",   "11470": "양천구",
    "11500": "강서구",   "11530": "구로구",   "11545": "금천구",
    "11560": "영등포구", "11590": "동작구",   "11620": "관악구",
    "11650": "서초구",   "11680": "강남구",   "11710": "송파구",
    "11740": "강동구",
}


# --- Nettoyage ------------------------------------------------------------
# PIEGE : les champs "vides" de cette API contiennent un espace, pas du vide.
# Un `if value:` naif serait vrai pour " ". D'ou le strip systematique.

def text(node, tag):
    """Texte d'un sous-element, None si absent ou vide apres strip."""
    child = node.find(tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def to_int(raw):
    """'47,500' -> 47500. Les montants arrivent en chaine avec virgules."""
    if raw is None:
        return None
    try:
        return int(raw.replace(",", "").strip())
    except ValueError:
        return None


def to_float(raw):
    if raw is None:
        return None
    try:
        return float(raw.replace(",", "").strip())
    except ValueError:
        return None


def manwon_to_won(value):
    """Les montants sont exprimes en 만원 (10 000 wons)."""
    return value * 10_000 if value is not None else None


# --- Regles metier --------------------------------------------------------

def classify_lease(monthly_won):
    """
    jeonse : depot seul, aucun loyer mensuel
    wolse  : depot + loyer mensuel

    PAS de ban_jeonse ici, et c'est une decision de conception.

    Le ban-jeonse se definit par rapport a la valeur jeonse d'un bien
    COMPARABLE : un gros depot proche du jeonse du marche, complete d'un
    petit loyer. Le determiner suppose donc de connaitre la mediane jeonse
    du quartier, pour ce type de bien et cette tranche de surface.

    Une premiere version utilisait un simple ratio depot/loyer. Elle
    classait en ban_jeonse un wolse tout a fait ordinaire (depot 48 M,
    loyer 440 000) parce que le ratio depassait 100 — alors que le vrai
    ban-jeonse observe dans le meme jeu de donnees etait a 270 M de depot
    pour un jeonse comparable de 280 M.

    REGLE GENERALE : l'ingestion ne calcule que ce qui est local a
    l'enregistrement. Tout ce qui demande un agregat appartient a la couche
    de transformation, qui voit l'ensemble des donnees.
    """
    return "jeonse" if not monthly_won else "wolse"


def monthly_equivalent(deposit_won, monthly_won):
    """
    Convertit un bail en cout mensuel comparable, en imputant au depot
    un cout d'opportunite annuel. C'est ce qui rend jeonse, ban-jeonse
    et wolse comparables entre eux pour un lecteur non coreen.
    """
    deposit_won = deposit_won or 0
    monthly_won = monthly_won or 0
    return round(monthly_won + deposit_won * CONVERSION_RATE_ANNUAL / 100 / 12)


def normalize_contract_type(raw):
    if raw == "신규":
        return "new"
    if raw == "갱신":
        return "renewal"
    return None


def build_deal_date(node):
    """
    PIEGE : la date est eclatee en 3 champs sans zero devant (dealMonth = '1').
    Normalisation en ISO des l'ingestion.
    """
    year = to_int(text(node, "dealYear"))
    month = to_int(text(node, "dealMonth"))
    day = to_int(text(node, "dealDay"))
    if not (year and month and day):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def make_transaction_id(record, occurrence=0):
    """
    PIEGE MAJEUR : l'API ne fournit aucun identifiant de transaction.
    On fabrique une cle deterministe pour garantir l'idempotence
    (rejouer une ingestion ne doit pas creer de doublon).

    PROBLEME MESURE : sur Jongno-gu / janvier 2026, cette cle fusionnait
    18 transactions sur 263, soit 7 %. Cause : dans une grande residence,
    plusieurs batiments ont des plans identiques. Deux logements de meme
    surface, meme etage, loues le meme jour au meme prix sont indiscernables
    avec les champs disponibles (l'API des locations ne renvoie pas aptDong,
    contrairement a celle des ventes).

    SOLUTION : un index d'occurrence distingue les enregistrements dont tous
    les champs sont identiques. L'idempotence tient toujours, car rejouer la
    meme ingestion produit le meme ensemble de cles : peu importe lequel des
    deux logements identiques recoit l'index 0 ou 1, ils sont interchangeables
    par construction.
    """
    parts = [
        record["property_type"],
        record["sigungu_code"] or "",
        record["dong_name"] or "",
        record["jibun"] or "",
        record["building_name"] or "",
        str(record["area_sqm"] or ""),
        str(record["floor"] or ""),
        record["deal_date"] or "",
        str(record["deposit_krw"] or ""),
        str(record["monthly_rent_krw"] or ""),
        str(occurrence),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def assign_ids(records):
    """
    Attribue les identifiants en comptant les occurrences des enregistrements
    identiques. Retourne aussi le nombre de collisions rencontrees, qui est
    une metrique de qualite a surveiller : une hausse brutale signalerait un
    changement de comportement de la source.
    """
    seen = {}
    collisions = 0

    for record in records:
        base = make_transaction_id(record, 0)
        occurrence = seen.get(base, 0)
        if occurrence:
            collisions += 1
        seen[base] = occurrence + 1
        record["transaction_id"] = make_transaction_id(record, occurrence)

    return collisions


def normalize(node, property_type, district_code, ingested_at):
    """Un <item> XML -> un enregistrement du schema unifie."""
    config = SOURCES[property_type]

    deposit = manwon_to_won(to_int(text(node, "deposit")))
    monthly = manwon_to_won(to_int(text(node, "monthlyRent")))
    # PIEGE : en jeonse, monthlyRent vaut "0", ce n'est pas une valeur manquante.
    if monthly is None:
        monthly = 0

    sigungu_code = text(node, "sggCd") or district_code

    record = {
        "property_type": property_type,
        "transaction_type": "rent",
        "sido_code": sigungu_code[:2] if sigungu_code else None,
        "sigungu_code": sigungu_code,
        "sigungu_name": text(node, "sggNm") or SEOUL_DISTRICTS.get(sigungu_code),
        "dong_name": text(node, "umdNm"),
        "jibun": text(node, "jibun") if config["has_jibun"] else None,
        "building_name": text(node, config["name_field"]) if config["name_field"] else None,
        "build_year": to_int(text(node, "buildYear")),
        "floor": to_int(text(node, "floor")) if config["has_floor"] else None,
        "area_sqm": to_float(text(node, config["area_field"])),
        "area_type": config["area_type"],
        "deposit_krw": deposit,
        "monthly_rent_krw": monthly,
        "lease_type": classify_lease(monthly),
        "monthly_equivalent_krw": monthly_equivalent(deposit, monthly),
        "contract_term": text(node, "contractTerm"),
        "contract_type": normalize_contract_type(text(node, "contractType")),
        "house_subtype": text(node, "houseType"),   # present villa/house seulement
        "deal_date": build_deal_date(node),
        "is_cancelled": False,                       # champ prevu pour les ventes
        "source_api": config["service"],
        "ingested_at": ingested_at,
    }
    return record


# --- Appel API ------------------------------------------------------------

def normalize_service_key(api_key):
    """
    PIEGE 1 : une cle copiee-collee traine souvent un espace ou un retour a
    la ligne invisible. quote() l'encoderait en %20 ou %0A et le serveur
    rejetterait la cle. D'ou le strip() prealable.

    PIEGE 2 : le portail fournit deux versions de la meme cle.
      - Decoding : caracteres bruts       -> ...JUxVQ==
      - Encoding : deja percent-encodee   -> ...JUxVQ%3D%3D

    Si on laisse urlencode traiter une cle deja encodee, le '%' devient '%25'
    et le serveur repond SERVICE_KEY_IS_NOT_REGISTERED_ERROR.

    unquote() ramene toujours a la forme brute (sans effet sur une cle deja
    brute), puis quote() encode une seule fois. Les deux versions marchent.
    """
    return quote(unquote(api_key.strip()), safe="")


def fetch_page(service, api_key, district_code, deal_ym, page):
    params = urlencode({
        "LAWD_CD": district_code,
        "DEAL_YMD": deal_ym,
        "numOfRows": PAGE_SIZE,
        "pageNo": page,
    })
    # La cle est concatenee a la main, apres normalisation, pour echapper
    # au traitement automatique d'urlencode.
    service_key = normalize_service_key(api_key)
    url = f"{BASE_URL}/{service}/get{service}?serviceKey={service_key}&{params}"

    if DEBUG:
        masked = f"{service_key[:8]}...{service_key[-12:]}"
        print(f"\n    [debug] cle recue   : {len(api_key)} caracteres, "
              f"se termine par {api_key.strip()[-8:]!r}", file=sys.stderr)
        print(f"    [debug] cle envoyee : {len(service_key)} caracteres, "
              f"se termine par {service_key[-12:]!r}", file=sys.stderr)
        print(f"    [debug] URL         : "
              f"{url.replace(service_key, masked)}", file=sys.stderr)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            with urlopen(request, timeout=30) as response:
                return ET.fromstring(response.read())

        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:400]
            # Reessayer une 4xx est inutile : la requete est mauvaise, pas le
            # serveur. Seul le 429 (quota momentane) merite une nouvelle chance.
            if 400 <= exc.code < 500 and exc.code != 429:
                raise RuntimeError(
                    f"HTTP {exc.code} sur {service} {district_code} {deal_ym}\n"
                    f"    reponse du serveur : {body}"
                ) from exc
            last_error = f"HTTP {exc.code} — {body}"

        except (URLError, ET.ParseError) as exc:
            last_error = exc

        wait = RETRY_BACKOFF ** attempt
        print(f"    tentative {attempt + 1} echouee ({last_error}), retry dans {wait}s",
              file=sys.stderr)
        time.sleep(wait)

    raise RuntimeError(f"echec definitif sur {service} {district_code} {deal_ym}: {last_error}")


def fetch_all(service, api_key, district_code, deal_ym):
    """Pagine jusqu'a avoir recupere totalCount elements."""
    items, page, total = [], 1, None

    while True:
        root = fetch_page(service, api_key, district_code, deal_ym, page)

        code = root.findtext(".//resultCode")
        if code not in ("000", "00"):
            message = root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg")
            raise RuntimeError(f"erreur API {code}: {message}")

        if total is None:
            total = int(root.findtext(".//totalCount") or 0)

        page_items = root.findall(".//item")
        items.extend(page_items)

        if len(items) >= total or not page_items:
            break
        page += 1
        time.sleep(THROTTLE)

    return items, total


# --- Orchestration --------------------------------------------------------

def collect(api_key, district_code, deal_ym, property_types):
    ingested_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = []

    for property_type in property_types:
        service = SOURCES[property_type]["service"]
        label = SEOUL_DISTRICTS.get(district_code, district_code)
        print(f"  {property_type:<10} {label} {deal_ym} ...", end=" ", flush=True)

        try:
            nodes, total = fetch_all(service, api_key, district_code, deal_ym)
        except RuntimeError as exc:
            print(f"ECHEC — {exc}")
            continue

        for node in nodes:
            records.append(normalize(node, property_type, district_code, ingested_at))
        print(f"{total} transactions")
        time.sleep(THROTTLE)

    return records


def main():
    parser = argparse.ArgumentParser(description="Collecteur 전월세 data.go.kr")
    parser.add_argument("--district", help="code legal, ex 11110")
    parser.add_argument("--all-seoul", action="store_true",
                        help="boucle sur les 25 arrondissements")
    parser.add_argument("--month", required=True, help="AAAAMM, ex 202601")
    parser.add_argument("--type", choices=list(SOURCES), action="append",
                        help="type de bien, repetable ; defaut = les 4")
    parser.add_argument("--out", default="./data", help="dossier de sortie")
    parser.add_argument("--debug", action="store_true",
                        help="affiche l'URL construite et l'etat de la cle")
    args = parser.parse_args()

    global DEBUG
    DEBUG = args.debug

    api_key = os.environ.get("DATA_GO_KR_KEY")
    if not api_key:
        sys.exit("DATA_GO_KR_KEY absent de l'environnement.")

    if not args.district and not args.all_seoul:
        sys.exit("Precise --district ou --all-seoul.")

    districts = list(SEOUL_DISTRICTS) if args.all_seoul else [args.district]
    property_types = args.type or list(SOURCES)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_records = []
    for district_code in districts:
        all_records.extend(collect(api_key, district_code, args.month, property_types))

    # Les identifiants sont attribues apres coup, pour pouvoir compter les
    # occurrences d'enregistrements strictement identiques.
    collisions = assign_ids(all_records)

    # Deduplication : rejouer une ingestion ne cree pas de doublon.
    unique = {record["transaction_id"]: record for record in all_records}

    out_file = out_dir / f"rent_{args.month}.jsonl"
    with out_file.open("w", encoding="utf-8") as handle:
        for record in unique.values():
            # JSON Lines : un objet par ligne, sans indentation.
            # Format standard des pipelines de donnees — lisible en flux,
            # concatenable, et directement exploitable par S3 et Athena.
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"\n{len(unique)} transactions ecrites")
    if collisions:
        ratio = collisions / len(all_records) * 100
        print(f"{collisions} enregistrements strictement identiques "
              f"({ratio:.1f} %), distingues par index d'occurrence")
    print(f"-> {out_file}")


if __name__ == "__main__":
    main()