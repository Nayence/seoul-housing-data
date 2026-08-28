"""
Bibliotheque de collecte des transactions locatives coreennes (전월세).

Ce module ne connait ni la ligne de commande, ni AWS, ni le systeme de
fichiers. Il expose une fonction principale, collect_district_month(), qui
renvoie une liste d'enregistrements normalises.

Cette separation permet deux points d'entree sur le meme code metier :
  - cli.py               pour l'usage local
  - handler_collector.py pour l'execution en Lambda

Un module unique plutot qu'un paquet decoupe : ~350 lignes, une seule
responsabilite, et un empaquetage Lambda trivial.
"""

import hashlib
import json
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

log = logging.getLogger(__name__)

BASE_URL = "https://apis.data.go.kr/1613000"
PAGE_SIZE = 1000
MAX_RETRIES = 4
RETRY_BACKOFF = 2
THROTTLE = 0.2

# PIEGE : le serveur data.go.kr rejette avec un 403 les requetes portant le
# User-Agent par defaut de Python. Voir NOTES.md, entree 1.
USER_AGENT = "seoul-housing-data/1.0 (personal non-commercial project)"

# Taux de conversion depot -> equivalent mensuel (전월세전환율), en % annuel.
# DECISION DOCUMENTEE : encadre par la loi coreenne et evolutif.
# A verifier avant toute publication.
CONVERSION_RATE_ANNUAL = 5.5


# --- Configuration des sources -------------------------------------------
# Ajouter un type de bien = ajouter une entree. Jamais toucher a la logique.

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
        "name_field": None,
        "area_field": "totalFloorAr",
        "area_type": "total",
        "has_floor": False,
        "has_jibun": False,
    },
}

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


class CollectorError(RuntimeError):
    """Echec de collecte. Distinguee des erreurs de programmation."""


# --- Nettoyage ------------------------------------------------------------
# PIEGE : les champs "vides" contiennent un espace, pas du vide.
# Un `if value:` naif serait vrai pour " ". D'ou le strip systematique.

def text(node, tag):
    child = node.find(tag)
    if child is None or child.text is None:
        return None
    return child.text.strip() or None


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

    PAS de ban_jeonse ici : le determiner suppose de connaitre la mediane
    jeonse locale, donc l'ensemble du jeu de donnees. Voir NOTES.md,
    entree 6. Regle generale : l'ingestion ne calcule que ce qui est local
    a l'enregistrement.
    """
    return "jeonse" if not monthly_won else "wolse"


def monthly_equivalent(deposit_won, monthly_won):
    """
    Ramene les regimes locatifs a un cout mensuel comparable, en imputant
    au depot un cout d'opportunite annuel. C'est ce qui rend le jeonse
    lisible pour un lecteur europeen.
    """
    deposit_won = deposit_won or 0
    monthly_won = monthly_won or 0
    return round(monthly_won + deposit_won * CONVERSION_RATE_ANNUAL / 100 / 12)


def normalize_contract_type(raw):
    return {"신규": "new", "갱신": "renewal"}.get(raw)


def build_deal_date(node):
    """
    PIEGE : date eclatee en 3 champs sans zero devant (dealMonth = '1').
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
    L'API ne fournit aucun identifiant de transaction. On fabrique une cle
    deterministe pour garantir l'idempotence.

    L'index d'occurrence distingue les enregistrements strictement
    identiques, environ 6 % du volume. Voir NOTES.md, entree 5.
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
    Attribue les identifiants en comptant les occurrences d'enregistrements
    identiques. Retourne le nombre de collisions : c'est une metrique de
    qualite, une hausse brutale signalerait un changement de la source.
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
    # PIEGE : en jeonse, monthlyRent vaut "0". Ce n'est pas une absence.
    if monthly is None:
        monthly = 0

    sigungu_code = text(node, "sggCd") or district_code

    return {
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
        "house_subtype": text(node, "houseType"),
        "deal_date": build_deal_date(node),
        "is_cancelled": False,
        "source_api": config["service"],
        "ingested_at": ingested_at,
    }


# --- Appel API ------------------------------------------------------------

def normalize_service_key(api_key):
    """
    PIEGE 1 : une cle copiee-collee traine souvent un espace invisible.
    PIEGE 2 : le portail fournit une version brute et une version deja
    percent-encodee de la meme cle. Re-encoder la seconde produit %25 et
    invalide la cle.

    unquote() ramene toujours a la forme brute, quote() encode une seule
    fois. Les deux versions deviennent equivalentes. Voir NOTES.md, 3 et 4.
    """
    return quote(unquote(api_key.strip()), safe="")


def fetch_page(service, api_key, district_code, deal_ym, page):
    params = urlencode({
        "LAWD_CD": district_code,
        "DEAL_YMD": deal_ym,
        "numOfRows": PAGE_SIZE,
        "pageNo": page,
    })
    service_key = normalize_service_key(api_key)
    url = f"{BASE_URL}/{service}/get{service}?serviceKey={service_key}&{params}"
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            with urlopen(request, timeout=30) as response:
                return ET.fromstring(response.read())

        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:400]
            # Reessayer une 4xx est inutile : la requete est mauvaise, pas
            # le serveur. Seul le 429 (quota momentane) merite une chance.
            if 400 <= exc.code < 500 and exc.code != 429:
                raise CollectorError(
                    f"HTTP {exc.code} sur {service} {district_code} "
                    f"{deal_ym} — {body}"
                ) from exc
            last_error = f"HTTP {exc.code} — {body}"

        except (URLError, ET.ParseError) as exc:
            last_error = str(exc)

        wait = RETRY_BACKOFF ** attempt
        log.warning("tentative %d echouee (%s), retry dans %ds",
                    attempt + 1, last_error, wait)
        time.sleep(wait)

    raise CollectorError(
        f"echec definitif sur {service} {district_code} {deal_ym}: {last_error}"
    )


def fetch_all(service, api_key, district_code, deal_ym):
    """Pagine jusqu'a avoir recupere totalCount elements."""
    items, page, total = [], 1, None

    while True:
        root = fetch_page(service, api_key, district_code, deal_ym, page)

        code = root.findtext(".//resultCode")
        if code not in ("000", "00"):
            message = (root.findtext(".//resultMsg")
                       or root.findtext(".//returnAuthMsg"))
            raise CollectorError(f"erreur API {code}: {message}")

        if total is None:
            total = int(root.findtext(".//totalCount") or 0)

        page_items = root.findall(".//item")
        items.extend(page_items)

        if len(items) >= total or not page_items:
            break
        page += 1
        time.sleep(THROTTLE)

    return items, total


# --- Point d'entree metier ------------------------------------------------

def collect_district_month(api_key, district_code, deal_ym, property_types=None):
    """
    Collecte une unite de travail : un arrondissement, un mois, N types.

    C'est l'unite choisie pour l'execution en Lambda : environ 4 appels et
    une dizaine de secondes, donc tres loin de la limite de 15 minutes, et
    rejouable sans risque de depassement de quota.

    Retourne (enregistrements, statistiques).
    """
    property_types = property_types or list(SOURCES)
    ingested_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = []
    stats = {"by_type": {}, "district": district_code, "month": deal_ym}

    for property_type in property_types:
        service = SOURCES[property_type]["service"]
        nodes, total = fetch_all(service, api_key, district_code, deal_ym)

        for node in nodes:
            records.append(
                normalize(node, property_type, district_code, ingested_at)
            )

        stats["by_type"][property_type] = total
        log.info("%s %s %s : %d transactions",
                 property_type, district_code, deal_ym, total)
        time.sleep(THROTTLE)

    stats["collisions"] = assign_ids(records)
    stats["total"] = len(records)
    return records, stats


def to_jsonl(records):
    """
    Serialise en JSON Lines : un objet par ligne, sans indentation.
    Format standard des pipelines — lisible en flux, concatenable,
    directement exploitable par S3 et Athena.
    """
    return "\n".join(
        json.dumps(record, ensure_ascii=False) for record in records
    ) + "\n"
