"""
Lambda de transformation. Declenchee par EventBridge, apres la collecte.

Lit la couche brute depuis S3, calcule les agregats, et ecrit les fichiers
JSON servis par le site.

Rapport de compression typique : 1,27 million de transactions (~800 Mo)
condensees en ~3 Mo de fichiers prets a l'emploi. C'est tout l'interet du
precalcul : le visiteur ne declenche aucun calcul, il telecharge un fichier
deja pret depuis le cache.
"""

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import boto3

from transform import transform

log = logging.getLogger()
log.setLevel(logging.INFO)

RAW_BUCKET = os.environ["RAW_BUCKET"]
SITE_BUCKET = os.environ["SITE_BUCKET"]
RAW_PREFIX = os.environ.get("RAW_PREFIX", "normalized/rent/")
SITE_PREFIX = os.environ.get("SITE_PREFIX", "data/")

# Lire 600 objets un par un coute environ 100 ms de latence chacun, soit
# une minute par passe et deux minutes au total. Un pool de threads ramene
# cela a quelques secondes. La taille du lot borne la memoire : on ne
# charge jamais plus de LOT fichiers simultanement.
DOWNLOAD_THREADS = 16
BATCH_SIZE = 32

s3 = boto3.client("s3")


def list_keys():
    """Liste toutes les cles de la couche brute, en paginant."""
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=RAW_BUCKET, Prefix=RAW_PREFIX):
        for item in page.get("Contents", []):
            if item["Key"].endswith(".jsonl"):
                keys.append(item["Key"])
    return sorted(keys)


def read_one(key):
    """Telecharge un objet et retourne ses enregistrements."""
    response = s3.get_object(Bucket=RAW_BUCKET, Key=key)
    records = []
    for line in response["Body"].iter_lines():
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            # Une ligne corrompue ne doit pas faire echouer le lot entier.
            log.warning("ligne illisible dans %s", key)
    return records


def stream_records(keys):
    """
    Generateur d'enregistrements, par lots telecharges en parallele.

    On traite lot par lot plutot que de tout soumettre d'un coup : cela
    borne la memoire a BATCH_SIZE fichiers, au lieu de charger les 600.
    """
    with ThreadPoolExecutor(max_workers=DOWNLOAD_THREADS) as pool:
        for start in range(0, len(keys), BATCH_SIZE):
            batch = keys[start:start + BATCH_SIZE]
            for records in pool.map(read_one, batch):
                yield from records


def write_outputs(outputs):
    """Ecrit les fichiers d'agregats dans le bucket du site."""
    for relative_path, content in outputs.items():
        key = SITE_PREFIX + relative_path
        s3.put_object(
            Bucket=SITE_BUCKET,
            Key=key,
            Body=json.dumps(content, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json; charset=utf-8",
            # Les donnees ne changent qu'une fois par mois. Une heure de
            # cache navigateur est prudente : le CDN sera invalide au
            # deploiement, ce qui rend la fraicheur immediate cote CDN.
            CacheControl="public, max-age=3600",
        )
    log.info("%d fichiers ecrits dans s3://%s/%s",
             len(outputs), SITE_BUCKET, SITE_PREFIX)


def handler(event, context):
    keys = list_keys()
    if not keys:
        raise RuntimeError(f"aucun fichier sous s3://{RAW_BUCKET}/{RAW_PREFIX}")

    log.info("%d fichiers a lire (deux passes)", len(keys))

    # transform() appelle cette fabrique deux fois : une passe pour la
    # reference jeonse, une passe pour l'agregation. Passer un generateur
    # deja cree ne fonctionnerait pas — il s'epuise apres un parcours.
    outputs = transform(lambda: stream_records(keys))

    write_outputs(outputs)

    meta = outputs["meta.json"]
    summary = {
        "event": "transformed",
        "source_files": len(keys),
        "output_files": len(outputs),
        "months": len(meta["months_covered"]),
        "period": f"{meta['months_covered'][0]}-{meta['months_covered'][-1]}",
        "districts": meta["district_count"],
    }
    log.info(json.dumps(summary))
    return summary
