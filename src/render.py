"""
Rendu HTML statique.

Prend les structures deja assemblees par transform.assemble() (la vue
d'ensemble et les fiches par arrondissement) et produit des pages ou le
contenu utile est deja present dans le HTML : un robot d'indexation ou un
apercu de lien n'a pas besoin d'executer de JavaScript pour voir les noms
d'arrondissement et les prix. Le JavaScript (site/app.js) reste charge sur
ces memes pages pour l'interactivite, mais il enrichit ce balisage au lieu
de le creer.

Aucune dependance : echappement via html.escape (bibliotheque standard),
gabarits en f-strings. Genere par handler_transformer.py (Lambda) et par
cli_transform.py (local) a partir du meme dictionnaire de sortie.
"""

import html

# Domaine CloudFront par defaut. A remplacer si un domaine personnalise est
# branche devant la distribution (terraform output site_url fait foi).
SITE_BASE_URL = "https://d2znef9949fpyj.cloudfront.net"

REPO_URL = "https://github.com/nayence/seoul-housing-data"

# Duplique volontairement les tableaux TYPES/AREAS de site/app.js : il n'y a
# pas d'etape de build permettant de partager une source unique entre Python
# et JavaScript. Toute modification des libelles cote client doit etre
# reportee ici, et inversement.
TYPES = [
    ("officetel", "Officetel"),
    ("apartment", "Appartement"),
    ("villa", "Villa"),
    ("house", "Maison"),
]

AREAS = [
    ("studio", "moins de 20 m²"),
    ("petit", "20 à 30 m²"),
    ("moyen", "30 à 45 m²"),
    ("grand", "45 à 60 m²"),
    ("familial", "60 à 85 m²"),
    ("tres_grand", "plus de 85 m²"),
]

TYPE_LABELS = dict(TYPES)
AREA_LABELS = dict(AREAS)

# Doit rester synchronise avec WON_PER_EUR dans site/app.js.
WON_PER_EUR = 1560

# Segment mis en avant sur la page d'accueil. Doit rester synchronise avec
# l'etat initial (state.type / state.area) de site/app.js.
DEFAULT_TYPE = "officetel"
DEFAULT_AREA = "studio"


# --- Formatage --------------------------------------------------------------

def esc(value):
    return html.escape(str(value), quote=True)


def group(n):
    """
    Regroupe les milliers avec une espace fine insecable, comme le
    Intl.NumberFormat("fr-FR") utilise cote client (site/app.js). Ecrit a
    la main plutot que via le module locale, dont la disponibilite depend
    des locales installees sur le systeme hote de la Lambda.
    """
    sign = "-" if n < 0 else ""
    digits = str(abs(int(n)))
    groups = []
    while len(digits) > 3:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    groups.insert(0, digits)
    return sign + " ".join(groups)


def won(v):
    return f"{group(round(v))} ₩"


def eur(v):
    return f"≈ {group(round(v / WON_PER_EUR))} €"


def month_label(m):
    return f"{m[4:]}/{m[2:4]}"


def slugify(name_fr):
    """'Gangnam-gu' -> 'gangnam-gu'. Les noms francais sont deja des formes
    romanisees ASCII a tirets (voir transform.DISTRICT_NAMES_FR) : une
    simple mise en minuscule suffit, pas besoin de translitteration."""
    return name_fr.lower()


# --- Graphique SVG ------------------------------------------------------
# Portage a l'identique de renderChart() dans site/app.js : memes calculs,
# meme mise en page, pour que le rendu ne bouge pas quand le JavaScript
# reprend la main sur un autre segment.

def render_chart_svg(points):
    usable = [p for p in points if not p.get("insufficient")]
    if len(usable) < 3:
        return '<p class="muted">Série trop courte pour être affichée.</p>'

    W, H, PAD_L, PAD_R, PAD_T, PAD_B = 900, 260, 96, 20, 20, 36
    values = [p["median"] for p in usable]
    lo = min(values) * 0.94
    hi = max(values) * 1.06
    span = (hi - lo) or 1

    def x(i):
        return PAD_L + (i / (len(usable) - 1)) * (W - PAD_L - PAD_R)

    def y(v):
        return PAD_T + (1 - (v - lo) / span) * (H - PAD_T - PAD_B)

    line = "".join(
        f"{'L' if i else 'M'}{x(i):.1f},{y(p['median']):.1f}"
        for i, p in enumerate(usable)
    )

    gridlines = "".join(
        f'<line x1="{PAD_L}" y1="{y(v):.1f}" x2="{W - PAD_R}" y2="{y(v):.1f}" '
        f'stroke="var(--rule)" stroke-width="1"/>'
        f'<text x="{PAD_L - 10}" y="{y(v) + 4:.1f}" text-anchor="end">{group(round(v))}</text>'
        for v in (lo, (lo + hi) / 2, hi)
    )

    dots = "".join(
        f'<circle cx="{x(i):.1f}" cy="{y(p["median"]):.1f}" r="3.5" '
        f'fill="{"var(--paper)" if p.get("provisional") else "var(--celadon)"}" '
        f'stroke="var(--celadon)" stroke-width="1.5">'
        f'<title>{esc(month_label(p["month"]))} — {esc(won(p["median"]))}'
        f'{" (provisoire)" if p.get("provisional") else ""}</title></circle>'
        for i, p in enumerate(usable)
    )

    step = -(-len(usable) // 6)  # ceil(len / 6) sans importer math
    labels = "".join(
        f'<text x="{x(i):.1f}" y="{H - 12}" text-anchor="middle">{esc(month_label(p["month"]))}</text>'
        for i, p in enumerate(usable)
        if i % step == 0
    )

    return (
        f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="Évolution mensuelle du coût équivalent, points évidés pour les mois provisoires">'
        f'{gridlines}'
        f'<path d="{line}" fill="none" stroke="var(--celadon)" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'{dots}{labels}</svg>'
        f'<p class="muted" style="font-size:14px">Les points évidés sont des mois provisoires.</p>'
    )


# --- Blocs reutilises entre la page d'accueil et les fiches ---------------

def render_type_grid(breakdown, property_type):
    """Une grille par surface, pour un type de logement donne. Portage de
    renderBuckets() dans site/app.js."""
    by_bucket = {
        b["area_bucket"]: b for b in breakdown if b["property_type"] == property_type
    }
    cells = []
    for area_id, area_label in AREAS:
        entry = by_bucket.get(area_id)
        if not entry or entry.get("insufficient"):
            cells.append(
                f'<div class="cell"><div class="label">{esc(area_label)}</div>'
                f'<div class="value muted" style="font-size:15px">trop peu de données</div></div>'
            )
        else:
            cells.append(
                f'<div class="cell"><div class="label">{esc(area_label)}</div>'
                f'<div class="value">{esc(won(entry["median"]))}</div>'
                f'<div class="label">{esc(eur(entry["median"]))} · {group(entry["count"])} transactions</div></div>'
            )
    return f'<div class="grid">{"".join(cells)}</div>'


def render_lease_mix(lease_mix, property_type):
    """Portage de renderMix() dans site/app.js."""
    counts = lease_mix.get(property_type)
    total = sum(counts.values()) if counts else 0
    if not total:
        return '<p class="muted">Pas de données pour ce type.</p>'

    def pct(key):
        return round(counts.get(key, 0) / total * 100)

    parts = [
        ("jeonse", "jeonse", pct("jeonse")),
        ("ban", "ban-jeonse", pct("ban_jeonse")),
        ("wolse", "wolse", pct("wolse")),
    ]
    parts = [p for p in parts if p[2] > 0]

    bars = "".join(
        f'<span class="{cls}" style="width:{p}%">{f"{p} %" if p >= 8 else ""}</span>'
        for cls, _, p in parts
    )
    color_var = {"jeonse": "var(--celadon)", "ban": "var(--slate)", "wolse": "var(--ink-faint)"}
    legend = "".join(
        f'<span><i class="{cls}" style="background:{color_var[cls]}"></i>{esc(label)}</span>'
        for cls, label, _ in parts
    )
    return f'<div class="mix">{bars}</div><div class="legend">{legend}</div>'


def dominant_type(breakdown):
    """Type de logement le plus represente dans cet arrondissement, sur la
    fenetre courante. Sert de segment par defaut pour la fiche statique :
    afficher par defaut un segment quasi absent localement (voir NOTES.md
    entree 10, l'ecart d'un facteur 36 sur les officetels selon le
    quartier) serait a la fois peu utile et malhonnete."""
    totals = {}
    for b in breakdown:
        totals[b["property_type"]] = totals.get(b["property_type"], 0) + b.get("count", 0)
    if not totals:
        return DEFAULT_TYPE
    return max(totals, key=totals.get)


def dominant_bucket(breakdown, property_type):
    candidates = [b for b in breakdown if b["property_type"] == property_type]
    if not candidates:
        return DEFAULT_AREA
    return max(candidates, key=lambda b: b.get("count", 0))["area_bucket"]


def find_segment(timeseries_by_segment, property_type, area_bucket_id):
    for seg in timeseries_by_segment:
        if seg["property_type"] == property_type and seg["area_bucket"] == area_bucket_id:
            return seg
    return None


def render_controls(selected_type, selected_area):
    def button(option_id, label, selected):
        pressed = "true" if option_id == selected else "false"
        return f'<button type="button" aria-pressed="{pressed}">{esc(label)}</button>'

    type_buttons = "".join(button(i, l, selected_type) for i, l in TYPES)
    area_buttons = "".join(button(i, l, selected_area) for i, l in AREAS)

    return f"""<div class="control-group">
    <span id="lbl-type">Type de logement</span>
    <div class="choices" id="types" role="group" aria-labelledby="lbl-type">{type_buttons}</div>
  </div>
  <div class="control-group">
    <span id="lbl-area">Surface</span>
    <div class="choices" id="areas" role="group" aria-labelledby="lbl-area">{area_buttons}</div>
  </div>"""


METHOD_SECTION = f"""<section class="method">
  <h2>Comment lire ces chiffres</h2>
  <p>
    En Corée, un logement se loue selon trois régimes. Le <strong>jeonse</strong> : un
    dépôt énorme, souvent la moitié de la valeur du bien, et aucun loyer mensuel. Le
    <strong>wolse</strong> : un dépôt modeste et un loyer mensuel, comme en France. Et le
    <strong>ban-jeonse</strong>, entre les deux.
  </p>
  <p>
    Impossible de comparer directement un dépôt de 280 millions de wons et un loyer de
    650 000 wons par mois. Le <strong>coût mensuel équivalent</strong> affiché ici ramène
    les trois à une même unité, en imputant au dépôt un coût d'opportunité annuel.
  </p>
  <ul>
    <li>Les surfaces sont des surfaces privatives, plus petites que celles annoncées dans les annonces coréennes.</li>
    <li>Les deux derniers mois sont provisoires : les déclarations continuent d'arriver après coup.</li>
    <li>Les médianes sont masquées en dessous de dix transactions.</li>
    <li>Les goshiwon et les colocations n'apparaissent pas : ce ne sont pas des baux enregistrés.</li>
    <li>Une médiane donne un ordre de grandeur, jamais une prédiction. Deux logements voisins peuvent varier de 30 %.</li>
  </ul>
  <p>
    Source : données ouvertes du 국토교통부 via
    <a href="https://www.data.go.kr">data.go.kr</a>.
    Le code du pipeline est public sur
    <a href="{REPO_URL}">GitHub</a>.
  </p>
</section>"""


# --- Squelette de page ------------------------------------------------------

def page_head(title, description, canonical_path):
    canonical = f"{SITE_BASE_URL}{canonical_path}"
    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Loyers réels à Séoul">
<meta property="og:locale" content="fr_FR">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(canonical)}">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(description)}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/styles.css">"""


def html_document(head, body, body_attrs=""):
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
{head}
</head>
<body{body_attrs}>
<div class="wrap">
{body}
</div>
<script src="/app.js"></script>
</body>
</html>
"""


# --- Pages -------------------------------------------------------------

def render_index_html(districts_json):
    districts = districts_json["districts"]
    months = districts_json["months_covered"]
    window = districts_json["window_months"]

    freshness = (
        f"{len(months)} mois de transactions, de {month_label(months[0])} "
        f"à {month_label(months[-1])}, sur les 25 arrondissements de Séoul. "
        f"Les {window} derniers mois servent aux prix affichés."
    )

    type_label = TYPE_LABELS[DEFAULT_TYPE].lower()
    area_label = AREA_LABELS[DEFAULT_AREA]
    caption = f"Un {type_label} de {area_label}, classé du moins cher au plus cher."

    rows = []
    for d in districts:
        stats = d.get("median_by_type_area", {}).get(DEFAULT_TYPE, {}).get(DEFAULT_AREA)
        if stats:
            rows.append((d, stats))
    rows.sort(key=lambda r: r[1]["median"])

    if rows:
        max_median = rows[-1][1]["median"]
        row_html = []
        for d, stats in rows:
            width = round(stats["median"] / max_median * 100)
            slug = slugify(d["name_fr"])
            row_html.append(f"""    <tr>
      <td><a href="/arrondissement/{slug}.html">{esc(d['name_fr'])}</a><span class="korean">{esc(d['name'])}</span></td>
      <td>
        <span class="price">{esc(won(stats['median']))}</span><br>
        <span class="euro">{esc(eur(stats['median']))}</span>
      </td>
      <td class="hide-narrow"><span class="bar" style="width:{width}%"></span></td>
      <td>{group(stats['count'])}</td>
    </tr>""")
        ranking_html = "\n".join(row_html)
    else:
        ranking_html = (
            '    <tr><td colspan="4" class="muted">Trop peu de transactions sur ce '
            'segment pour publier des médianes. Essayez une autre surface.</td></tr>'
        )

    body = f"""
<header>
  <h1>Ce que coûte vraiment un logement à Séoul</h1>
  <p class="lede">
    Les loyers réellement déclarés au ministère coréen du Territoire, arrondissement
    par arrondissement. Pas des estimations, pas des annonces : des contrats signés.
  </p>
  <p class="freshness" id="freshness">{esc(freshness)}</p>
</header>

<div class="controls">
  {render_controls(DEFAULT_TYPE, DEFAULT_AREA)}
</div>

<table>
  <caption id="ranking-caption">{esc(caption)}</caption>
  <thead>
    <tr>
      <th scope="col">Arrondissement</th>
      <th scope="col">Coût mensuel équivalent</th>
      <th scope="col" class="hide-narrow">Fourchette courante</th>
      <th scope="col">Transactions</th>
    </tr>
  </thead>
  <tbody id="ranking">
{ranking_html}
  </tbody>
</table>

<section class="detail" id="detail" aria-live="polite"></section>

{METHOD_SECTION}
"""

    title = "Loyers réels à Séoul, par quartier"
    description = (
        "Les loyers réellement déclarés à Séoul, par arrondissement et par type de "
        "logement. Données officielles du ministère coréen du Territoire, expliquées en français."
    )
    return html_document(page_head(title, description, "/"), body)


def render_district_html(district):
    name = district["name"]
    name_fr = district["name_fr"]
    code = district["code"]
    breakdown = district["breakdown"]
    lease_mix = district["lease_mix"]
    timeseries_by_segment = district["timeseries_by_segment"]

    dom_type = dominant_type(breakdown)
    dom_bucket = dominant_bucket(breakdown, dom_type)
    dom_type_label = TYPE_LABELS.get(dom_type, dom_type)
    dom_area_label = AREA_LABELS.get(dom_bucket, dom_bucket)

    segment = find_segment(timeseries_by_segment, dom_type, dom_bucket)
    chart_html = (
        render_chart_svg(segment["points"]) if segment
        else '<p class="muted">Série indisponible pour ce segment.</p>'
    )

    type_sections = "".join(
        f'<h4>{esc(label)}</h4>{render_type_grid(breakdown, type_id)}'
        for type_id, label in TYPES
    )

    mix_html = render_lease_mix(lease_mix, dom_type)

    body = f"""
<p class="back-link"><a href="/">← Tous les arrondissements</a></p>

<header>
  <h1>Loyers à {esc(name_fr)}<span class="korean">{esc(name)}</span></h1>
  <p class="lede">
    Prix réellement déclarés au ministère coréen du Territoire pour {esc(name_fr)}
    ({esc(name)}), par type de logement et par surface, sur les douze derniers mois.
  </p>
</header>

<div class="controls">
  {render_controls(dom_type, dom_bucket)}
</div>

<section class="detail">
  <h3>Par type de logement et par surface</h3>
  {type_sections}

  <div id="lease-mix-block">
    <h3>Répartition des régimes locatifs — {esc(dom_type_label.lower())}</h3>
    {mix_html}
  </div>

  <div id="chart-block">
    <h3>Évolution du coût mensuel équivalent — {esc(dom_type_label.lower())}, {esc(dom_area_label)}</h3>
    {chart_html}
  </div>
</section>

{METHOD_SECTION}
"""

    slug = slugify(name_fr)
    title = f"Loyers à {name_fr} : prix réels par surface et type de logement"
    description = (
        f"Loyers réellement déclarés à {name_fr} ({name}), Séoul : médianes par type "
        f"de logement et par surface, calculées sur les contrats enregistrés au "
        f"ministère coréen du Territoire."
    )
    body_attrs = (
        f' data-district-code="{esc(code)}"'
        f' data-default-type="{esc(dom_type)}"'
        f' data-default-area="{esc(dom_bucket)}"'
    )
    head = page_head(title, description, f"/arrondissement/{slug}.html")
    return html_document(head, body, body_attrs)


def render_all_pages(outputs):
    """Point d'entree. Prend le dictionnaire retourne par transform.transform()
    et retourne un dictionnaire {chemin_relatif_au_site: HTML}, cle par cle
    en plus des fichiers JSON deja existants. A ecrire a la racine du bucket
    du site, jamais sous le prefixe data/."""
    pages = {"index.html": render_index_html(outputs["districts.json"])}
    for key, content in outputs.items():
        if key.startswith("district/") and key.endswith(".json"):
            slug = slugify(content["name_fr"])
            pages[f"arrondissement/{slug}.html"] = render_district_html(content)
    return pages
