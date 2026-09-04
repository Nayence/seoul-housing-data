# Marché locatif de Séoul — pipeline de données

Collecte, normalise et publie les **transactions locatives réellement déclarées**
à Séoul, à partir des données ouvertes du ministère coréen du Territoire.

L'objectif n'est pas de republier des fourchettes de prix approximatives comme le
font les guides existants, mais de rendre lisibles des transactions officielles :
par quartier, par type de bien, par mois, avec leur distribution et leur évolution.

---

## Pourquoi

Les ressources francophones sur le logement à Séoul annoncent des fourchettes très
larges, sans source ni date, et souvent contradictoires entre elles. La donnée
officielle existe pourtant : elle est publique, gratuite et exhaustive — mais en
coréen, en XML, exprimée en 만원, et structurée autour du **jeonse**, un régime
locatif sans équivalent en Europe.

Le travail consiste donc autant à traduire qu'à collecter.

---

## Sources

Quatre API du 국토교통부 (ministère du Territoire, de l'Infrastructure et des
Transports), exposées via le portail national [data.go.kr](https://www.data.go.kr).

| Type de bien | Service | Champ nom | Champ surface |
|---|---|---|---|
| Appartements | `RTMSDataSvcAptRent` | `aptNm` | `excluUseAr` |
| Officetels | `RTMSDataSvcOffiRent` | `offiNm` | `excluUseAr` |
| Villas / 연립다세대 | `RTMSDataSvcRHRent` | `mhouseNm` | `excluUseAr` |
| Maisons / 단독다가구 | `RTMSDataSvcSHRent` | *(absent)* | `totalFloorAr` |

Les quatre partagent le même mécanisme de requête — code légal d'arrondissement
sur 5 chiffres et mois de contrat sur 6 chiffres — mais **pas le même schéma**.
La normalisation de ces écarts constitue l'essentiel du travail d'ingestion.

**Volume observé** — Jongno-gu, janvier 2026 :

| Type | Transactions |
|---|---|
| Appartements | 263 |
| Officetels | 236 |
| Villas | 264 |
| Maisons | 396 |
| **Total** | **1 159** |

Les appartements ne représentent que 23 % du marché locatif. Un pipeline limité à
cette seule source manquerait les trois quarts des transactions — et notamment les
officetels, qui correspondent au logement type d'un expatrié.

Extrapolation : environ 2 Go pour l'ensemble de Séoul sur dix ans.

---

## Architecture

### État actuel

```
collect.py (local)
    ├── appelle les 4 API
    ├── normalise vers un schéma unifié
    └── écrit du JSON Lines
              ↓
        S3 (couche brute)
        normalized/rent/year=YYYY/month=MM/
```

### Cible

```
EventBridge Scheduler
    ↓
Lambda collecteur ──→ S3 brut (XML tel quel, horodaté)
                          ↓
                   Lambda transformation
                          ↓
                    DynamoDB / agrégats JSON
                          ↓
              CloudFront ──→ site statique
```

Choix structurants :

- **Aucun service facturé à l'heure.** Ni NAT Gateway, ni ALB, ni RDS, ni EKS.
  L'ensemble reste dans les limites *Always Free* d'AWS.
- **Couche brute conservée intacte.** Un bug de transformation découvert dans six
  mois se corrige en rejouant depuis le brut, sans perte.
- **Partitionnement Hive** (`year=YYYY/month=MM`) reconnu nativement par Athena.
- **Agrégats précalculés.** Les données ne changent qu'une fois par mois : aucune
  raison de recalculer une médiane à chaque visite. Le site sert des fichiers
  figés depuis le cache CloudFront.

---

## Schéma unifié

```
transaction_id          clé déterministe (SHA-256 tronqué)
property_type           apartment | officetel | villa | house
transaction_type        rent
sido_code               "11"
sigungu_code            "11110"
sigungu_name            "종로구"
dong_name               "숭인동"
jibun                   "207-32"          null pour les maisons
building_name           "영하우스"          null pour les maisons
build_year              2015              null si absent
floor                   3                 null pour les maisons
area_sqm                20.27
area_type               exclusive | total
deposit_krw             48640000          wons entiers
monthly_rent_krw        440000            wons entiers
lease_type              jeonse | wolse
monthly_equivalent_krw  662933
contract_term           "26.01~27.01"     null si absent
contract_type           new | renewal     null si absent
house_subtype           "다가구"           villas et maisons uniquement
deal_date               "2026-01-14"
is_cancelled            false
source_api              "RTMSDataSvcAptRent"
ingested_at             "2026-08-28T11:37:33+00:00"
```

### Comprendre les trois régimes locatifs

| Régime | Dépôt | Loyer mensuel |
|---|---|---|
| **전세** jeonse | 50 à 80 % de la valeur du bien | aucun |
| **월세** wolse | modeste | mensuel classique |
| **반전세** ban-jeonse | proche du jeonse local | complément réduit |

`monthly_equivalent_krw` convertit les trois en une unité comparable, en imputant
au dépôt un coût d'opportunité annuel. C'est ce qui permet à un lecteur européen
de comparer des offres qui, telles quelles, sont incomparables.

---

## Utilisation

```bash
export DATA_GO_KR_KEY="clé_depuis_data.go.kr"

# un arrondissement, un mois
python3 collect.py --district 11110 --month 202601

# un seul type de bien
python3 collect.py --district 11110 --month 202601 --type officetel

# les 25 arrondissements de Séoul
python3 collect.py --all-seoul --month 202601

# diagnostic (affiche l'URL construite, clé masquée)
python3 collect.py --district 11110 --month 202601 --debug
```

Aucune dépendance externe : bibliothèque standard uniquement.

**Envoi vers S3**

```bash
aws s3 cp data/rent_202601.jsonl \
  s3://<bucket>/normalized/rent/year=2026/month=01/rent_202601.jsonl
```

**Quota** : 10 000 appels par jour en compte de développement. Une année complète
sur les 25 arrondissements et les 4 types coûte 1 200 appels. L'ingestion
mensuelle courante en coûte 100.

---

## Décisions techniques

Le détail des diagnostics figure dans [NOTES.md](NOTES.md). En résumé :

**Montants stockés en wons entiers.** L'API renvoie des 만원 sous forme de chaînes
avec séparateurs (`"47,500"`). La conversion a lieu une fois, à l'ingestion.
Aucune ambiguïté d'unité, aucun flottant sur des montants.

**Aucun montant en euros dans la base.** Un taux de change figé rendrait fausse
toute la base six mois plus tard. La conversion se fait à l'affichage, à partir
d'un taux collecté quotidiennement et archivé.

**Identifiant déterministe fabriqué.** L'API ne fournit aucune clé de transaction.
Un hash des attributs métier garantit qu'une réingestion ne duplique rien. Environ
6 % des enregistrements sont strictement identiques sur tous les champs
disponibles ; un index d'occurrence les distingue sans casser l'idempotence.

**Le ban-jeonse n'est pas classifié à l'ingestion.** Le déterminer suppose de
connaître la médiane jeonse locale, donc l'ensemble du jeu de données. Une
première version utilisait un ratio dépôt/loyer et se trompait. Règle retenue :
*l'ingestion ne calcule que ce qui est local à l'enregistrement.*

**Réessais différenciés.** Les erreurs 5xx et réseau sont réessayées avec un délai
croissant ; les 4xx échouent immédiatement, à l'exception du 429. Réessayer une
requête invalide ne peut pas la rendre valide.

---

## Limites connues

À afficher sur le site, pas seulement dans le dépôt.

1. **Décalage de déclaration.** Le mois en cours est incomplet et se remplit
   pendant plusieurs semaines. Les deux ou trois derniers mois doivent être
   réingérés régulièrement. Aucune affirmation possible sur « le loyer aujourd'hui ».
2. **Goshiwon et 하숙 absents.** Ce ne sont pas des baux immobiliers enregistrés.
3. **Le 원룸 n'est pas une catégorie légale.** Il se capture indirectement en
   filtrant sur la surface (moins de 30 m²), quel que soit le type déclaré.
4. **Surfaces non homogènes.** `exclusive` pour les copropriétés, `total` pour les
   maisons individuelles. Les comparer sans le signaler serait trompeur — d'où
   le champ `area_type`.
5. **Une médiane n'est pas un prix.** Deux biens voisins peuvent varier de 30 %.
   Le site donne un ordre de grandeur et une distribution, jamais une prédiction.
6. **Taux de conversion à vérifier.** `CONVERSION_RATE_ANNUAL` est encadré par la
   loi coréenne et évolue. La valeur du code est un défaut explicite, pas une
   vérité établie.

---

## Feuille de route

- [x] Collecteur local, 4 sources, schéma unifié
- [x] Couche brute S3, partitionnée, versionnée, avec cycle de vie
- [ ] Terraform — reprise de l'existant en infrastructure as code
- [ ] Lambda + EventBridge — reprise historique et ingestion incrémentale
- [ ] Couche de transformation — agrégats, médianes, classification ban-jeonse
- [ ] Collecte quotidienne du taux EUR/KRW (BCE)
- [ ] Site statique + déploiement GitHub Actions via OIDC
- [ ] Observabilité — tableau de bord, alarmes, détection de dérive de schéma
- [ ] Extension aux transactions de vente, puis hors Séoul

---

## Licence des données

Données publiques du 국토교통부 via data.go.kr, sous licence sans restriction
d'usage (이용허락범위 제한 없음). Usage non commercial dans le cadre de ce projet.
