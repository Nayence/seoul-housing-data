"""
Lambda de collecte. Declenchee par SQS.

Chaque message SQS represente une unite de travail :
    {"district": "11110", "month": "202601"}

Soit environ 4 appels API et une dizaine de secondes — tres loin de la
limite de 15 minutes, et rejouable sans risque de depassement de quota.

Sortie : un fichier JSON Lines par unite de travail, dans S3.
"""

import json
import logging
import os

import boto3

from seoul_housing import CollectorError, collect_district_month, to_jsonl

log = logging.getLogger()
log.setLevel(logging.INFO)

RAW_BUCKET = os.environ["RAW_BUCKET"]
PARAMETER_NAME = os.environ["API_KEY_PARAMETER"]

# Les clients boto3 sont crees au niveau du module, pas dans le handler.
# Lambda reutilise le meme environnement d'execution entre deux invocations
# proches : ce qui est initialise ici survit et evite de repayer le cout de
# connexion a chaque appel.
s3 = boto3.client("s3")
ssm = boto3.client("ssm")

# Meme logique pour la cle API : un appel SSM par environnement d'execution
# plutot qu'un par message. Sur une reprise historique de 3 000 messages,
# la difference est loin d'etre negligeable.
_api_key_cache = None


def get_api_key():
    global _api_key_cache
    if _api_key_cache is None:
        response = ssm.get_parameter(Name=PARAMETER_NAME, WithDecryption=True)
        _api_key_cache = response["Parameter"]["Value"]
        log.info("cle API chargee depuis %s", PARAMETER_NAME)
    return _api_key_cache


def s3_key(district, month):
    """
    Partitionnement Hive : Athena reconnait le format cle=valeur et ne lit
    que les partitions necessaires a une requete.

    La cle est deterministe : rejouer la meme unite de travail ecrase le
    meme objet au lieu d'en creer un second. C'est l'idempotence au niveau
    du stockage, complementaire de celle des identifiants de transaction.
    """
    return (f"normalized/rent/year={month[:4]}/month={month[4:]}"
            f"/district={district}/rent.jsonl")


def process_one(api_key, district, month):
    records, stats = collect_district_month(api_key, district, month)

    if not records:
        log.warning("aucune transaction pour %s %s", district, month)
        return stats

    key = s3_key(district, month)
    s3.put_object(
        Bucket=RAW_BUCKET,
        Key=key,
        Body=to_jsonl(records).encode("utf-8"),
        ContentType="application/x-ndjson",
    )

    # Log structure : exploitable par CloudWatch Logs Insights pour tracer
    # l'evolution du taux de collision ou du volume dans le temps.
    log.info(json.dumps({
        "event": "collected",
        "district": district,
        "month": month,
        "total": stats["total"],
        "collisions": stats["collisions"],
        "by_type": stats["by_type"],
        "s3_key": key,
    }))
    return stats


def handler(event, context):
    """
    Reponse partielle de lot : on renvoie la liste des messages en echec.
    Sans ca, un seul message defaillant ferait rejouer TOUT le lot, y
    compris les messages deja traites avec succes.
    """
    failures = []

    for record in event.get("Records", []):
        message_id = record["messageId"]
        try:
            body = json.loads(record["body"])
            process_one(get_api_key(), body["district"], body["month"])

        except CollectorError as exc:
            # Echec metier : source indisponible, quota, reseau.
            # Rejouable — on laisse SQS reprogrammer le message.
            log.error("echec collecte %s : %s", message_id, exc)
            failures.append({"itemIdentifier": message_id})

        except (KeyError, json.JSONDecodeError) as exc:
            # Message malforme : le rejouer ne changera rien. On le laisse
            # partir vers la file de rebut plutot que boucler indefiniment.
            log.error("message invalide %s : %s — %s",
                      message_id, exc, record.get("body"))
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}
