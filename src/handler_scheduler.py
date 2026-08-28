"""
Lambda de planification. Declenchee par EventBridge Scheduler.

Calcule les unites de travail a traiter et les depose dans SQS. Elle ne
collecte rien elle-meme : son role est de decouper le travail en morceaux
assez petits pour tenir dans une invocation.

Deux modes :

  Automatique (EventBridge, mensuel)
    Reingere les LOOKBACK_MONTHS derniers mois pour les 25 arrondissements.

  Manuel (invocation avec charge utile) pour la reprise historique
    {"months": ["202501", "202502"], "districts": ["11110"]}
    {"from_month": "201601", "to_month": "202601"}
"""

import json
import logging
import os
from datetime import date

import boto3

from seoul_housing import SEOUL_DISTRICTS

log = logging.getLogger()
log.setLevel(logging.INFO)

QUEUE_URL = os.environ["QUEUE_URL"]

# POURQUOI PLUSIEURS MOIS ET PAS SEULEMENT LE DERNIER :
# les transactions sont publiees avec un delai de declaration. Un mois reste
# incomplet pendant plusieurs semaines et continue de se remplir. Reingerer
# les 3 derniers mois garantit que les donnees se consolident.
# La cle S3 etant deterministe, la reingestion ecrase proprement.
LOOKBACK_MONTHS = int(os.environ.get("LOOKBACK_MONTHS", "3"))

sqs = boto3.client("sqs")


def month_key(year, month):
    return f"{year:04d}{month:02d}"


def previous_months(count, today=None):
    """Les `count` derniers mois, du plus recent au plus ancien."""
    today = today or date.today()
    year, month = today.year, today.month
    months = []
    for _ in range(count):
        months.append(month_key(year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return months


def month_range(from_month, to_month):
    """Tous les mois entre deux bornes incluses, format AAAAMM."""
    year, month = int(from_month[:4]), int(from_month[4:])
    end_year, end_month = int(to_month[:4]), int(to_month[4:])
    months = []
    while (year, month) <= (end_year, end_month):
        months.append(month_key(year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


def resolve_months(event):
    if "months" in event:
        return event["months"]
    if "from_month" in event and "to_month" in event:
        return month_range(event["from_month"], event["to_month"])
    return previous_months(LOOKBACK_MONTHS)


def enqueue(units):
    """
    SQS accepte 10 messages par envoi groupe. Envoyer un par un
    fonctionnerait, mais couterait 10 fois plus d'appels reseau — sensible
    sur une reprise historique de plusieurs milliers d'unites.
    """
    sent = 0
    for start in range(0, len(units), 10):
        batch = units[start:start + 10]
        entries = [
            {"Id": str(index), "MessageBody": json.dumps(unit)}
            for index, unit in enumerate(batch)
        ]
        response = sqs.send_message_batch(QueueUrl=QUEUE_URL, Entries=entries)
        sent += len(response.get("Successful", []))

        for failure in response.get("Failed", []):
            log.error("echec d'enfilement : %s", failure)

    return sent


def handler(event, context):
    event = event or {}
    months = resolve_months(event)
    districts = event.get("districts") or list(SEOUL_DISTRICTS)

    units = [
        {"district": district, "month": month}
        for month in months
        for district in districts
    ]

    sent = enqueue(units)

    log.info(json.dumps({
        "event": "scheduled",
        "months": months,
        "districts": len(districts),
        "units": len(units),
        "sent": sent,
    }))

    # ALERTE POTENTIELLE : un ecart entre units et sent signifie que des
    # unites de travail ont ete perdues silencieusement. Candidat naturel
    # a une alarme CloudWatch.
    return {"units": len(units), "sent": sent, "months": months}
