#!/usr/bin/env python3
"""
Lance la transformation en local, sur les donnees rapatriees depuis S3.
Ecrit aussi les pages HTML (accueil + une par arrondissement), pour les
relire dans un navigateur avant de deployer.

    aws s3 sync s3://seoul-housing-raw-anice/normalized/ ./data/normalized/
    python3 cli_transform.py --input ../data/normalized --out ../data/site

Le meme code metier tourne en Lambda ; ce fichier ne contient que la
lecture disque et l'affichage.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import render
from transform import transform


def stream_records(input_dir):
    """
    Generateur : lit les .jsonl ligne par ligne sans tout charger.

    transform() appelle cette fabrique DEUX fois (reference jeonse, puis
    agregation). Un generateur s'epuisant apres un parcours, on passe la
    fonction qui le cree, et non le generateur lui-meme.
    """
    files = sorted(Path(input_dir).rglob("*.jsonl"))
    if not files:
        sys.exit(f"Aucun fichier .jsonl trouve dans {input_dir}")

    for path in files:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # Une ligne corrompue ne doit pas faire echouer les
                    # 1,27 million d'autres.
                    continue


def main():
    parser = argparse.ArgumentParser(description="Transformation des donnees")
    parser.add_argument("--input", default="../data/normalized")
    parser.add_argument("--out", default="../data/site")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    file_count = len(list(Path(args.input).rglob("*.jsonl")))
    print(f"{file_count} fichiers a lire (deux passes)...")

    outputs = transform(lambda: stream_records(args.input))
    pages = render.render_all_pages(outputs)

    out_dir = Path(args.out)
    for relative_path, content in outputs.items():
        target = out_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(content, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    for relative_path, content in pages.items():
        target = out_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    all_files = {**outputs, **pages}
    total_size = sum((out_dir / p).stat().st_size for p in all_files)
    print(f"\n{len(all_files)} fichiers ecrits ({total_size / 1024:.0f} Ko), "
          f"dont {len(pages)} pages HTML")
    print(f"-> {out_dir}")

    meta = outputs["meta.json"]
    print(f"\nPeriode couverte : {meta['months_covered'][0]} "
          f"a {meta['months_covered'][-1]}")
    print(f"Mois provisoires : {', '.join(meta['provisional_months'])}")


if __name__ == "__main__":
    main()
