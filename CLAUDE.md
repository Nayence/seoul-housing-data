# Contexte projet — pour Claude Code

## Ce qu'est ce projet

Pipeline de données sur le marché locatif de Séoul, à partir des API publiques
coréennes (data.go.kr), et site public en français qui expose les résultats.

Deux objectifs simultanés :

1. **Portfolio d'ingénierie cloud** — objectif principal. Ce qui compte est la
   qualité de l'infrastructure et des décisions, pas la richesse fonctionnelle.
2. Un site utile en français, avec du trafic récurrent à terme.

Voir [README.md](README.md) pour l'architecture et [NOTES.md](NOTES.md) pour
l'historique des décisions et des bugs. **Lire NOTES.md avant de modifier la
couche de données** : plusieurs pièges y sont documentés.

## Langue

Code, commentaires et documentation en **français**, sans accents dans les
commentaires de code Python (compatibilité). Les identifiants restent en
anglais. Le site est en français, à destination d'un public francophone.

## Contraintes non négociables

- **Rester dans les limites Always Free d'AWS.** Aucun service facturé à
  l'heure : pas de NAT Gateway, pas d'ALB, pas de RDS, pas d'ECS Fargate, pas
  d'EKS. Si une solution en impose un, proposer une alternative serverless.
- **Aucun secret dans le dépôt.** La clé API vient de l'environnement en local,
  de Parameter Store en Lambda. Le déploiement GitHub Actions se fera en OIDC,
  sans clé d'accès longue durée.
- **Terraform pour toute l'infrastructure.** Aucune ressource créée à la main
  dans la console. L'état est dans S3 avec verrouillage natif.
- **La couche brute S3 est immuable.** On ne transforme jamais en écrasant la
  source. Tout retraitement repart du brut.
- **Aucune dépendance externe côté site.** Pas de framework, pas d'étape de
  build : les fichiers partent tels quels sur S3. Les graphiques sont du SVG
  écrit à la main.

## Principes de conception établis

Issus de problèmes réellement rencontrés, détaillés dans NOTES.md :

- **L'ingestion ne calcule que ce qui est local à un enregistrement.** Tout ce
  qui demande un agrégat appartient à la couche de transformation (entrée 6).
- **Ne jamais agréger un ensemble hétérogène.** Une médiane tous types et
  toutes surfaces confondus produit un chiffre plausible et faux (entrée 11).
- **Normaliser les entrées à la frontière du système** — strip, encodage,
  formats de date — plutôt que faire confiance au format reçu (entrées 3 et 4).
- **Réessais différenciés** : 5xx, réseau et 429 → réessai avec délai
  croissant. Autres 4xx → échec immédiat (entrée 2).
- **Idempotence par clé déterministe**, avec index d'occurrence pour les
  enregistrements strictement identiques, environ 6 % du volume (entrée 5).
- **Afficher des compteurs, pas des « OK »**. Le bug le plus grave du projet
  était silencieux, trouvé grâce à une ligne de statistiques.
- **Configuration en données, pas en code.** Sources d'API, arrondissements et
  tranches de surface sont des dictionnaires. Étendre = ajouter une ligne.
- **Mesurer plutôt que supposer**, et jamais sur un échantillon unique : une
  mesure isolée a produit deux conclusions successives fausses (entrée 13).

## Architecture

```
EventBridge (5 du mois, 3h)
    └─> Lambda scheduler ──> file SQS ──> file de rebut
                                │  (2 en parallèle)
                                └─> Lambda collector ──> S3 raw (privé, versionné)
                                                             │
EventBridge (5 du mois, 5h) ──> Lambda transformer <─────────┘
                                     └─> S3 site ──> CloudFront ──> public
```

Trois buckets aux rôles distincts :

| Bucket | Contenu | Régénérable | Versionné |
|---|---|---|---|
| `seoul-housing-raw-anice` | transactions brutes, 803 Mo | non | oui |
| `seoul-housing-site-anice` | agrégats publiés + site, ~2 Mo | oui | non |
| `seoul-housing-tfstate-anice` | état Terraform | non | oui |

## Arborescence

```
src/          code Python : collecte et transformation
  seoul_housing.py       bibliothèque de collecte
  transform.py           agrégats, médianes, ban-jeonse
  cli.py                 collecte en local
  cli_transform.py       transformation en local
  handler_collector.py   Lambda de collecte (SQS)
  handler_scheduler.py   Lambda de planification (EventBridge)
  handler_transformer.py Lambda de transformation (EventBridge)
terraform/    infrastructure complète
site/         front statique : index.html, app.js, styles.css, 404.html
data/         données locales, ignorées par Git
```

## État actuel

**Fait**
- Collecte des 4 types de biens, 25 arrondissements, 24 mois — 1,27 M transactions
- Pipeline serverless complet en Terraform, IAM au plus juste
- Transformation : médianes par segment, séries temporelles, classification
  ban-jeonse contextuelle, 27 fichiers d'agrégats
- CloudFront devant un bucket fermé, accès par OAC uniquement
- Première version du site statique

**Suivant, dans l'ordre**
1. Itérations sur le site, responsive, lisibilité
2. Pages statiques par arrondissement pour le référencement
3. Déploiement GitHub Actions en OIDC
4. Collecte quotidienne du taux EUR/KRW depuis la BCE
5. Observabilité : tableau de bord, alarmes, détection de dérive de schéma

## Environnement

- Région AWS : `ap-northeast-2` (Séoul), compte `250611152527`
- `DATA_GO_KR_KEY` dans l'environnement local (version Decoding)
- Python 3.13 en Lambda, 3.14 en local, macOS
- Terraform ≥ 1.9, provider AWS ~> 6.0

## Commandes

```bash
# collecte locale
cd src && python3 cli.py --district 11110 --month 202601 --out ../data

# transformation locale
cd src && python3 cli_transform.py --verbose

# infrastructure
cd terraform && terraform plan -out=tfplan && terraform apply tfplan

# déploiement du site
aws s3 sync site/ s3://seoul-housing-site-anice/ --exclude "data/*"
aws cloudfront create-invalidation --distribution-id <id> --paths "/*"

# invocation manuelle
aws lambda invoke --function-name seoul-housing-transformer \
  --cli-read-timeout 0 --cli-binary-format raw-in-base64-out \
  --payload '{}' --region ap-northeast-2 /dev/null
```

## Conventions

- Données en **JSON Lines**, jamais en tableau JSON indenté
- Montants en **wons entiers**, jamais en 만원 ni en flottants
- Aucun montant converti en euros stocké : conversion à l'affichage
- Dates en **ISO 8601**
- Les décisions discutables sont des constantes nommées et commentées
- Partitionnement Hive : `year=YYYY/month=MM/district=CODE/`

## Ce qu'il ne faut pas faire

- Ne pas ajouter de dépendance sans nécessité. Le code tourne sur la
  bibliothèque standard, c'est ce qui rend l'empaquetage Lambda trivial.
- Ne pas « simplifier » les commentaires qui expliquent un piège ou une
  décision : ils sont la valeur documentaire du projet.
- Ne pas créer de ressource AWS dans la console.
- Ne pas supposer qu'un champ est présent : les quatre sources ont des schémas
  différents et des champs vides contenant un espace.
- Ne pas lancer `aws s3 sync site/` sans `--exclude "data/*"` : cela
  effacerait les agrégats publiés.
