#!/usr/bin/env python3
"""
Point d'entree en ligne de commande.

Ce fichier ne contient QUE ce qui est propre a l'usage local : lecture des
arguments, ecriture sur disque, affichage. Toute la logique metier vit dans
seoul_housing.py, partagee avec les handlers Lambda.

Usage:
    export DATA_GO_KR_KEY="cle_data.go.kr"
    python3 cli.py --district 11110 --month 202601
    python3 cli.py --all-seoul --month 202601
    python3 cli.py --all-seoul --month 202601 --out ./data
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from seoul_housing import (
    SEOUL_DISTRICTS,
    SOURCES,
    CollectorError,
    collect_district_month,
    to_jsonl,
)


def main():
    parser = argparse.ArgumentParser(description="Collecteur 전월세 data.go.kr")
    parser.add_argument("--district", help="code legal, ex 11110")
    parser.add_argument("--all-seoul", action="store_true",
                        help="boucle sur les 25 arrondissements")
    parser.add_argument("--month", required=True, help="AAAAMM, ex 202601")
    parser.add_argument("--type", choices=list(SOURCES), action="append",
                        help="type de bien, repetable ; defaut = les 4")
    parser.add_argument("--out", default="./data", help="dossier de sortie")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    api_key = os.environ.get("DATA_GO_KR_KEY")
    if not api_key:
        sys.exit("DATA_GO_KR_KEY absent de l'environnement.")

    if not args.district and not args.all_seoul:
        sys.exit("Precise --district ou --all-seoul.")

    districts = list(SEOUL_DISTRICTS) if args.all_seoul else [args.district]

    all_records = []
    failures = []

    for district_code in districts:
        label = SEOUL_DISTRICTS.get(district_code, district_code)
        print(f"  {label:<10} {args.month} ...", end=" ", flush=True)
        try:
            records, stats = collect_district_month(
                api_key, district_code, args.month, args.type
            )
        except CollectorError as exc:
            # Un arrondissement en echec ne doit pas interrompre les 24
            # autres. On collecte les echecs et on les rapporte a la fin.
            print(f"ECHEC — {exc}")
            failures.append((district_code, str(exc)))
            continue

        all_records.extend(records)
        print(f"{stats['total']} transactions "
              f"({stats['collisions']} identiques)")

    if not all_records:
        sys.exit("Aucune donnee collectee.")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"rent_{args.month}.jsonl"
    out_file.write_text(to_jsonl(all_records), encoding="utf-8")

    print(f"\n{len(all_records)} transactions ecrites")
    print(f"-> {out_file}")

    if failures:
        print(f"\n{len(failures)} arrondissement(s) en echec :")
        for code, message in failures:
            print(f"  {code} — {message}")
        # Code de sortie non nul : un script appelant peut le detecter.
        sys.exit(1)


if __name__ == "__main__":
    main()
