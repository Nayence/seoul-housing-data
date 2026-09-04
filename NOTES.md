# Journal d'ingénierie

Les problèmes rencontrés, leur diagnostic, et ce qu'ils ont appris. Écrit au fil
du développement plutôt que reconstitué après coup.

---

## 1 — Le 403 qui n'avait rien à voir avec l'authentification

**Symptôme.** L'URL fonctionnait parfaitement dans le navigateur. Le même appel
depuis Python renvoyait `HTTP 403 Forbidden`, immédiatement, sur les quatre API.

**Fausse piste.** Vérification des autorisations sur le portail, de la validité de
la clé, du délai de propagation. Tout était en ordre.

**Cause.** `urllib` envoie par défaut l'en-tête `User-Agent: Python-urllib/3.14`.
Le serveur de data.go.kr rejette cet en-tête. Le navigateur passait précisément
parce qu'il s'annonce comme un navigateur.

**Correctif.** Un `User-Agent` explicite via un objet `Request`.

**Leçon.** Quand un appel réussit dans un client et échoue dans un autre avec la
même URL, la différence est dans les en-têtes, pas dans l'URL. Le premier réflexe
devrait être de comparer les requêtes complètes, pas de suspecter les droits.

---

## 2 — Le message d'erreur masqué

**Symptôme.** Le correctif précédent n'a pas suffi. Toujours un 403.

**Ce qui manquait.** Le code affichait `HTTP Error 403: Forbidden` et rien d'autre.
Or le serveur renvoyait un corps de réponse détaillé, jeté sans être lu.

**Correctif.** Lecture et affichage du corps sur erreur HTTP. Est alors apparu :

```xml
<errMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</errMsg>
<returnAuthMsg>등록되지 않은 서비스키</returnAuthMsg>
```

**Leçon.** Un message d'erreur amputé coûte plus cher que le bug lui-même. La
première chose à améliorer face à un problème opaque n'est pas le code fautif,
c'est ce que le code raconte quand il échoue.

**Effet de bord vertueux.** Ce correctif a révélé un défaut de conception adjacent :
le code réessayait quatre fois, avec un délai croissant, une erreur 4xx. Or un 4xx
signifie que *la requête* est invalide, pas que le serveur a un incident passager.
La réessayer ne peut pas la réparer. Désormais : réessai sur 5xx, réseau et 429 ;
échec immédiat sur les autres 4xx.

---

## 3 — Le double encodage de la clé

**Contexte.** Le portail fournit la même clé sous deux formes :

- **Decoding** — caractères bruts : `...JUxVQ==`
- **Encoding** — déjà percent-encodée : `...JUxVQ%3D%3D`

**Le piège.** `urlencode()` encode ce qu'on lui passe. Avec la version Encoding,
le `%` devient `%25`, et le serveur reçoit `%253D%253D`. Il décode une fois,
obtient `%3D%3D` au lieu de `==`, et ne reconnaît plus la clé.

**Correctif.** `quote(unquote(clé), safe="")`. `unquote` ramène à la forme brute —
sans effet sur une clé déjà brute — puis `quote` encode une seule fois. Les deux
formes deviennent équivalentes.

**Leçon.** Deux couches encodaient la même donnée, chacune correctement de son
point de vue. Personne n'avait tort isolément. Le réflexe qui sauve : **normaliser
l'entrée à la frontière du système** plutôt que faire confiance au format reçu.

---

## 4 — L'espace invisible

**Symptôme.** Malgré les correctifs 1 à 3, toujours `SERVICE_KEY_IS_NOT_REGISTERED`.

**Diagnostic.** Ajout d'un mode `--debug` affichant la longueur de la clé reçue,
la clé effectivement envoyée, et l'URL construite avec la clé masquée.

**Cause.** Un espace ou un retour à la ligne collé avec la clé lors du
copier-coller. `quote()` l'encodait en `%20`, invalidant la clé. Rigoureusement
invisible dans le terminal.

**Correctif.** `.strip()` avant toute normalisation.

**Leçon.** Trois bugs empilés, chacun masquant le suivant. On ne les a démêlés
qu'en ajoutant de l'observabilité, par couches successives. Deviner coûte plus
cher qu'instrumenter.

---

## 5 — Le bug silencieux, plus grave que les trois autres

**Découverte.** Une fois le collecteur fonctionnel : `263 brutes → 245 uniques`.
18 transactions disparues, soit 7 %. **Aucune erreur, aucun avertissement.**

**Cause.** La clé déterministe reposait sur les attributs métier : quartier,
parcelle, nom du bien, surface, étage, date, dépôt, loyer. Dans une grande
résidence, plusieurs bâtiments ont des plans identiques. Deux logements de même
surface, même étage, loués le même jour au même prix sont **indiscernables** avec
les champs disponibles — l'API des locations ne renvoie pas `aptDong`,
contrairement à celle des ventes.

Mesure sur les quatre types : **6,1 %** d'enregistrements strictement identiques.

**Correctif.** Un index d'occurrence distingue les enregistrements identiques.
L'idempotence tient toujours : rejouer produit le même **ensemble** de clés, et
peu importe lequel des deux logements identiques reçoit l'index 0 ou 1, puisqu'ils
sont interchangeables par construction.

Le taux de collision est désormais **affiché comme métrique**. Une hausse brutale
signalerait un changement de comportement de la source — candidat naturel à une
alarme CloudWatch.

**Leçon.** Les bugs qui plantent sont les gentils. Celui-ci supprimait 6 % des
données sans aucun signal. Il n'a été trouvé qu'en lisant la ligne de résumé —
d'où l'intérêt d'afficher des compteurs plutôt que de se contenter d'un « OK ».

---

## 6 — Un calcul au mauvais endroit

**Symptôme.** Inspection manuelle de trois enregistrements réels. Deux logements
identiques de la résidence 이지마루종로, 25,55 m² :

| Dépôt | Loyer | Classé | Réalité |
|---|---|---|---|
| 280 M | 0 | jeonse | jeonse |
| 270 M | 100 000 | ban_jeonse | ban-jeonse ✔ |
| 48 M | 440 000 | ban_jeonse | **wolse ordinaire** ✘ |

**Cause.** La règle mesurait un ratio dépôt/loyer. Or le ban-jeonse se définit par
rapport à la **valeur jeonse d'un bien comparable** : un dépôt proche du jeonse du
marché, complété d'un petit loyer. Le troisième cas est un wolse banal pour un
petit logement — le ratio les confondait.

**Le vrai problème est architectural.** La classification exigeait un contexte que
la fonction n'avait pas : la médiane jeonse du quartier, pour ce type de bien et
cette tranche de surface. Une fonction qui voit une seule ligne ne peut pas
répondre à une question qui porte sur l'ensemble.

**Correctif.** `lease_type` se limite à `jeonse` et `wolse` à l'ingestion. Le
ban-jeonse sera dérivé dans la couche de transformation, qui dispose des agrégats.

**Leçon.** Ne jamais calculer à l'entrée ce qui dépend du reste du jeu de données.
Ce défaut est invisible aux tests unitaires — la fonction faisait exactement ce
que le code décrivait. Il n'apparaît qu'en **regardant les vraies données**, à
l'œil, quelques lignes à la fois.

---

## 7 — Format de sortie

Première version : un tableau JSON avec `indent=2`. Illisible, non concaténable,
et nécessitant de tout charger en mémoire.

Passage au **JSON Lines** : un objet par ligne, sans indentation. Format standard
des pipelines de données — lisible en flux, ajoutable en fin de fichier,
directement exploitable par S3 et Athena.

```bash
head -3 data/rent_202601.jsonl
wc -l data/rent_202601.jsonl
```

---

## 8 — Dimensionner l'unité de travail

**Le calcul qui a décidé de l'architecture.**

Un mois complet représente 25 arrondissements × 4 types = 100 appels API,
soit environ 4 minutes. Une Lambda plafonne à 15 minutes : ça passe pour
l'ingestion courante.

La reprise historique sur dix ans, en revanche, c'est 120 mois × 100 appels
= 12 000 appels. Impossible dans une seule invocation.

Deuxième contrainte, plus sournoise : le quota du portail est de 10 000
appels par jour. Lancer 3 000 unités en parallèle épuiserait le quota en
quelques minutes tout en matraquant un service public.

**Conséquence.** L'unité de travail est le couple *un arrondissement, un
mois* — 4 appels, une dizaine de secondes. Petite, rejouable, sans risque.
Une file SQS sépare la planification de l'exécution, et la concurrence du
branchement est bridée à 2 invocations simultanées.

**Ce que ça a validé.** Songpa en août 2024 : 4 311 transactions. Jongno le
même mois : 754. Un facteur 6 entre arrondissements. Un découpage par mois
seul aurait produit des unités très inégales ; le découpage par
arrondissement garde même la plus lourde loin des limites.

**Résultat.** 600 unités traitées en une dizaine de minutes, zéro message en
file de rebut, aucun réessai déclenché.

---

## 9 — La concurrence réservée impossible sur un compte neuf

**Symptôme.** Terraform échoue à la création de la Lambda :

> InvalidParameterValueException: Specified ReservedConcurrentExecutions
> decreases account's UnreservedConcurrentExecution below its minimum
> value of [10].

**Cause.** Un compte AWS récent est plafonné à 10 exécutions Lambda
simultanées, au lieu des 1 000 d'un compte établi. AWS impose d'en laisser
au moins 10 non réservées. Réserver quoi que ce soit est donc impossible.

**Correctif.** Retrait de `reserved_concurrent_executions`. Le bridage du
débit reste assuré côté file par `maximum_concurrency` sur le branchement
SQS. On perd la double sécurité, pas la sécurité.

**Leçon.** Les quotas de service diffèrent selon l'ancienneté du compte : un
code Terraform valide peut échouer ici et passer ailleurs. Sur un projet
réel, la hausse de quota se demande **avant** le déploiement.

---

## 10 — Ce que les logs ont révélé sur le marché

Observation faite en regardant défiler la reprise historique, pas en
analysant les données.

Nombre d'officetels loués par mois, selon l'arrondissement :

| Arrondissement | Officetels |
|---|---|
| Gangseo (11500) | 904 |
| Yeongdeungpo (11560) | 783 |
| Dongjak (11590) | 40 |
| Nowon (11350) | 25 |

Un facteur 36. L'officetel étant le logement type de l'expatrié, cela
signifie que certains quartiers de Séoul n'ont tout simplement pas de marché
pour ce public — une information qu'aucun guide francophone ne donne, et qui
sera une page à part entière du site.

**Leçon collatérale.** Un log structuré n'est pas seulement un outil de
diagnostic : ici, il a produit un résultat métier avant même que la couche
d'analyse existe.

---

## 11 — L'agrégat hétérogène, un chiffre juste qui ne veut rien dire

**Symptôme.** La première version des séries temporelles donnait, pour Mapo :
médiane 1 058 750 wons, p25 à 705 833, p75 à 2 154 167.

Aucune erreur, aucun avertissement, des nombres parfaitement plausibles.

**Cause.** Cet agrégat mélangeait un studio en officetel et un appartement
familial en jeonse converti. La médiane ne décrit alors rien de réel : elle
mesure surtout la **composition** des transactions du mois. Un mois avec plus
d'appartements familiaux fait « monter les prix » sans qu'aucun prix n'ait bougé.

Et personne ne cherche « le loyer médian de Mapo, tous types confondus ». On
cherche « un studio à Mapo ».

**Correctif.** Séries segmentées par type de bien et tranche de surface. Une
courbe « officetel studio à Mapo » a un sens ; une courbe « tout Mapo » n'en a
aucun.

**Résultat.** La courbe segmentée est propre et exploitable : 745 833 wons en
août 2024, 815 833 en avril 2026, soit +9,4 % en deux ans, sans aberration.

**Leçon.** C'est le même défaut que l'entrée 6, sous une autre forme : un calcul
appliqué à un ensemble hétérogène. Mais celui-ci est plus dangereux, car il ne
produit ni erreur ni valeur absurde — juste des chiffres crédibles et faux.

---

## 12 — Deux passes, et pourquoi la signature a changé

**Contrainte.** La classification ban-jeonse exige de connaître la médiane
jeonse des biens comparables, donc de lire toutes les données avant de pouvoir
classer quoi que ce soit. Deux passes sont inévitables.

En local, charger les 1,27 million d'enregistrements en mémoire fonctionne. En
Lambda, cela représente plusieurs Go — coûteux et fragile.

**Solution.** `transform()` ne reçoit plus une liste mais une **fonction qui
fabrique un flux**, appelable deux fois. Un générateur Python s'épuise après un
parcours : impossible de le relire, d'où la fabrique plutôt que le générateur.

En local elle relit les fichiers du disque, en Lambda les objets S3. Le code
métier ne voit aucune différence.

Les enregistrements retenus entre les deux passes sont stockés sous forme de
tuples de sept valeurs plutôt que de dictionnaires de vingt-quatre clés.

**Validation.** Sortie identique au Ko près avant et après refactorisation :
3 227 Ko, 582 comparables, 24 mois, 25 arrondissements. Une refactorisation qui
change le résultat n'est pas une refactorisation.

---

## 13 — Power tuning : deux hypothèses fausses avant la bonne

Mesure de la Lambda de transformation à différentes allocations mémoire.

| Mémoire | Durée facturée | Go-secondes | Échantillons |
|---|---|---|---|
| 1 024 Mo | 137,2 s | **137** | 3 |
| 2 048 Mo | 96,6 s | **198** | 2 |
| 3 008 Mo | 94,2 s | **283** | 1 |

**Première erreur.** Un relevé initial isolé donnait 51,6 s à 2 048 Mo. Sur
cette base, conclusion : « charge dominée par le CPU, réduire la mémoire coûte
33 % plus cher ». Raisonnement cohérent, bien argumenté, et **faux** — la mesure
était aberrante.

**Ce que le doute a révélé.** 3 008 Mo apparaissait plus lent que 2 048 Mo, ce
qui est physiquement impossible : plus de mémoire ne peut pas ralentir. Cette
incohérence a fait remesurer, et la vraie valeur à 2 048 Mo est 96,6 s.

**Conclusion réelle.** En Go-secondes, 1 024 Mo est le moins cher. Au-delà de
2 048 Mo, on paye 43 % de plus pour 2 % de gain — signe d'un plateau : la charge
est limitée par le débit S3, que le CPU supplémentaire ne peut pas accélérer.

**Réglage retenu : 2 048 Mo.** Pas pour le coût, mais pour la marge : 96 s
laisse de l'air sous le timeout de 900 s quand le volume aura doublé. L'écart
de coût réel est de quelques centimes par an à une exécution mensuelle.

**Leçon, et c'est la principale de cette session.** Une mesure unique sur une
infrastructure partagée ne prouve rien : réseau variable, voisins bruyants,
allocation CPU fluctuante. Trois relevés minimum, on retient la médiane, et on
se méfie de toute valeur qui ne s'inscrit pas dans une progression cohérente.

---

## 14 — Trois timeouts sur le même chemin

**Symptôme.** `Read timeout on endpoint URL` lors de l'invocation d'une Lambda.

**Cause.** L'AWS CLI abandonne l'attente au bout de 60 secondes. La Lambda, elle,
continuait de tourner et a abouti normalement.

**Correctif.** `--cli-read-timeout 0`.

**Leçon.** Il existe trois timeouts indépendants sur ce chemin : celui du client
(60 s), celui de la Lambda (900 s ici), et celui d'API Gateway (30 s) s'il y en
a une devant. Un timeout côté client ne dit **rien** de l'état réel de
l'exécution. Source classique de faux diagnostics.

---

## Points ouverts

- **Taux de conversion légal** (`CONVERSION_RATE_ANNUAL`) — encadré par la loi
  coréenne, évolutif. La valeur du code est un défaut explicite à vérifier avant
  toute publication.
- **Fiabilité des données récentes** — le délai de déclaration n'a jamais été
  mesuré, seulement supposé. La fenêtre de réingestion de trois mois est un
  paramètre choisi au jugé. Premier indice contradictoire : sur les studios en
  officetel à Mapo, les deux mois marqués provisoires (n=205, n=188) ne montrent
  aucune chute par rapport aux mois définitifs (138 à 331). Méthode de mesure :
  comparer le `count` d'un même mois entre deux exécutions mensuelles
  successives. À vérifier dans deux mois.
- **Dérive de schéma** — aucune détection pour l'instant. Un champ qui
  disparaîtrait ou changerait de nom passerait inaperçu. À traiter dans la couche
  de transformation.
- **Codes 법정동** — `umdNm` est un libellé, pas un code stable. Un renommage
  administratif casserait les séries historiques.
- **Concurrence réservée** — impossible tant que le compte est plafonné à 10
  exécutions simultanées. Demander une hausse via Service Quotas, puis rétablir
  `reserved_concurrent_executions` pour retrouver la double sécurité sur le
  bridage du débit.
- **Anomalie villa à Mapo** — la tranche `petit` (20-30 m², médiane 1 045 000)
  est plus chère que la tranche `moyen` (30-45 m², 987 500). Progression non
  monotone, contrairement à tous les autres types. Hypothèse : parc récent et
  bien situé dans les petites surfaces, parc ancien dans les moyennes. À creuser
  — ce genre de contre-intuition ferait un bon article.
- **Doublons S3** — les fichiers issus des envois manuels
  (`rent_YYYYMM.jsonl`, tout Séoul) coexistent avec ceux produits par la Lambda
  (`district=.../rent.jsonl`). Toute requête lisant ces dossiers compterait deux
  fois les mêmes transactions. À supprimer une fois la reprise validée.
- **Estimation de volume erronée** — la taille totale de la couche brute avait
  été extrapolée depuis Jongno (140 Mo attendus), le réel est 803 Mo. Jongno est
  l'un des plus petits arrondissements ; Songpa fait six fois son volume. Ne
  jamais extrapoler depuis l'échantillon le plus petit.
