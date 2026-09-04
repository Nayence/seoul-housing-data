// Site des loyers reels a Seoul.
//
// Aucune dependance, aucune etape de build : le fichier part tel quel sur S3.
// Les donnees sont servies depuis la meme origine que la page, donc pas de CORS.
//
// Deux types de page partagent ce script :
//   - l'accueil (/)                         -> classement des 25 arrondissements
//   - une fiche (/arrondissement/*.html)     -> detail d'un seul arrondissement
// Les deux sont deja rendues cote serveur par src/render.py (Lambda de
// transformation) : ce script enrichit ce contenu existant (tri interactif,
// changement de segment) au lieu de le creer. Le mode est detecte via
// data-district-code sur <body>.

const TYPES = [
  { id: "officetel", label: "Officetel" },
  { id: "apartment", label: "Appartement" },
  { id: "villa",     label: "Villa" },
  { id: "house",     label: "Maison" },
];

const AREAS = [
  { id: "studio",     label: "moins de 20 m²" },
  { id: "petit",      label: "20 à 30 m²" },
  { id: "moyen",      label: "30 à 45 m²" },
  { id: "grand",      label: "45 à 60 m²" },
  { id: "familial",   label: "60 à 85 m²" },
  { id: "tres_grand", label: "plus de 85 m²" },
];

// TODO : remplacer par le taux quotidien collecte depuis la BCE.
// Valeur approximative, affichee comme telle.
const WON_PER_EUR = 1560;

const state = {
  type: "officetel",
  area: "studio",
  districts: [],
  districtData: null,
  selected: null,
};

const nf = new Intl.NumberFormat("fr-FR");

const won = (v) => nf.format(Math.round(v)) + " ₩";
const eur = (v) => "≈ " + nf.format(Math.round(v / WON_PER_EUR)) + " €";

const monthLabel = (m) => `${m.slice(4)}/${m.slice(2, 4)}`;

// 'Gangnam-gu' -> 'gangnam-gu'. Les noms francais sont deja des formes
// romanisees ASCII a tirets (voir transform.DISTRICT_NAMES_FR cote Python).
const slugify = (nameFr) => nameFr.toLowerCase();

// --- Demarrage --------------------------------------------------------------

async function boot() {
  const districtCode = document.body.dataset.districtCode;

  // Sur une fiche arrondissement, le segment initial est deja fixe par le
  // rendu serveur (le type de logement le plus represente localement, voir
  // render.dominant_type) : on aligne l'etat client dessus pour eviter que
  // les boutons changent de selection au premier rendu JS.
  if (districtCode) {
    state.type = document.body.dataset.defaultType || state.type;
    state.area = document.body.dataset.defaultArea || state.area;
  }

  const onSegmentChange = districtCode
    ? () => renderDistrictSegment(state.districtData)
    : () => { renderRanking(); if (state.selected) renderDetail(state.selected); };

  renderChoices("types", TYPES, "type", onSegmentChange);
  renderChoices("areas", AREAS, "area", onSegmentChange);

  if (districtCode) {
    await bootDistrict(districtCode);
  } else {
    await bootHome();
  }
}

async function bootHome() {
  try {
    const [meta, districts] = await Promise.all([
      fetch("/data/meta.json").then((r) => r.json()),
      fetch("/data/districts.json").then((r) => r.json()),
    ]);

    state.districts = districts.districts;
    renderFreshness(meta);
    renderRanking();
  } catch (error) {
    // Un echec de chargement doit dire quoi faire, pas seulement s'excuser.
    document.getElementById("freshness").textContent =
      "Les données n'ont pas pu être chargées. Réessayez dans un instant.";
    console.error(error);
  }
}

async function bootDistrict(code) {
  try {
    const data = await fetch(`/data/district/${code}.json`).then((r) => r.json());
    state.districtData = data;
    // Le reste de la page (titre, grilles par surface) est deja rendu cote
    // serveur avec ces memes donnees : seuls le mix et le graphique
    // dependent du segment choisi, donc seuls eux se rafraichissent ici.
    renderDistrictSegment(data);
  } catch (error) {
    console.error(error);
  }
}

function renderFreshness(meta) {
  const months = meta.months_covered;
  const first = monthLabel(months[0]);
  const last = monthLabel(months[months.length - 1]);
  document.getElementById("freshness").textContent =
    `${months.length} mois de transactions, de ${first} à ${last}, sur les 25 arrondissements de Séoul. Les 12 derniers mois servent aux prix affichés.`;
}

// --- Selecteurs -----------------------------------------------------------

function renderChoices(containerId, options, key, onChange) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";

  options.forEach((option) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = option.label;
    button.setAttribute("aria-pressed", state[key] === option.id);
    button.addEventListener("click", () => {
      state[key] = option.id;
      renderChoices(containerId, options, key, onChange);
      onChange();
    });
    container.appendChild(button);
  });
}

// --- Classement (accueil) --------------------------------------------------

function medianFor(district) {
  const byType = district.median_by_type_area[state.type];
  return byType ? byType[state.area] : undefined;
}

function renderRanking() {
  const typeLabel = TYPES.find((t) => t.id === state.type).label.toLowerCase();
  const areaLabel = AREAS.find((a) => a.id === state.area).label;

  document.getElementById("ranking-caption").textContent =
    `Un ${typeLabel} de ${areaLabel}, classé du moins cher au plus cher.`;

  const rows = state.districts
    .map((d) => ({ ...d, stats: medianFor(d) }))
    .filter((d) => d.stats !== undefined)
    .sort((a, b) => a.stats.median - b.stats.median);

  const tbody = document.getElementById("ranking");
  tbody.innerHTML = "";

  if (!rows.length) {
    tbody.innerHTML =
      `<tr><td colspan="4" class="muted">Trop peu de transactions sur ce segment pour publier des médianes. Essayez une autre surface.</td></tr>`;
    return;
  }

  const max = rows[rows.length - 1].stats.median;

  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.setAttribute("aria-selected", state.selected === row.code);

    const width = Math.round((row.stats.median / max) * 100);
    const slug = row.name_fr ? slugify(row.name_fr) : null;
    const nameHtml = slug
      ? `<a href="/arrondissement/${slug}.html">${row.name_fr}</a>`
      : (row.name_fr || row.code);

    tr.innerHTML = `
      <td>${nameHtml}<span class="korean">${row.name}</span></td>
      <td>
        <span class="price">${won(row.stats.median)}</span><br>
        <span class="euro">${eur(row.stats.median)}</span>
      </td>
      <td class="hide-narrow"><span class="bar" style="width:${width}%"></span></td>
      <td>${nf.format(row.stats.count)}</td>`;

    // La ligne se selectionne via son lien : un clic simple deroule le
    // detail sur place (comportement enrichi), un clic modifie (milieu,
    // ctrl/cmd, etc.) ouvre la fiche statique normalement, comme tout lien.
    const link = tr.querySelector("a");
    if (link) {
      link.addEventListener("click", (e) => {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        state.selected = row.code;
        renderRanking();
        renderDetail(row.code);
      });
    }

    tbody.appendChild(tr);
  });
}

// --- Fiche detaillee (accueil, deroulee sur clic) --------------------------

async function renderDetail(code) {
  const target = document.getElementById("detail");
  target.innerHTML = `<p class="muted">Chargement…</p>`;

  let data;
  try {
    data = await fetch(`/data/district/${code}.json`).then((r) => r.json());
  } catch (error) {
    target.innerHTML = `<p class="muted">Cet arrondissement n'a pas pu être chargé.</p>`;
    return;
  }

  const segment = data.timeseries_by_segment.find(
    (s) => s.property_type === state.type && s.area_bucket === state.area
  );

  target.innerHTML = `
    <h2>${data.name_fr || data.name}<span class="korean">${data.name}</span></h2>
    <p class="muted">Détail des loyers sur les douze derniers mois.</p>
    <h3>Par surface, pour ce type de logement</h3>
    ${renderBuckets(data)}
    <h3>Répartition des régimes locatifs</h3>
    ${renderMix(data)}
    ${segment ? `<h3>Évolution du coût mensuel équivalent</h3>${renderChart(segment)}` : ""}`;

  target.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderBuckets(data) {
  const cells = AREAS.map((area) => {
    const entry = data.breakdown.find(
      (b) => b.property_type === state.type && b.area_bucket === area.id
    );

    if (!entry || entry.insufficient) {
      return `<div class="cell">
        <div class="label">${area.label}</div>
        <div class="value muted" style="font-size:15px">trop peu de données</div>
      </div>`;
    }

    return `<div class="cell">
      <div class="label">${area.label}</div>
      <div class="value">${won(entry.median)}</div>
      <div class="label">${eur(entry.median)} · ${nf.format(entry.count)} transactions</div>
    </div>`;
  }).join("");

  return `<div class="grid">${cells}</div>`;
}

// --- Segment d'une fiche arrondissement -------------------------------

// Sur une fiche arrondissement, les grilles par type/surface sont deja
// completes (tous les types, toutes les surfaces) dans le HTML rendu : elles
// ne dependent pas du segment choisi et n'ont donc pas besoin d'etre
// regenerees ici. Seuls le mix des regimes locatifs et le graphique
// d'evolution sont propres a un segment (type + surface) : ce sont eux que
// l'interactivite met a jour.
function renderDistrictSegment(data) {
  if (!data) return;

  const typeLabel = TYPES.find((t) => t.id === state.type).label;
  const areaLabel = AREAS.find((a) => a.id === state.area).label;

  const mixBlock = document.getElementById("lease-mix-block");
  if (mixBlock) {
    mixBlock.innerHTML =
      `<h3>Répartition des régimes locatifs — ${typeLabel.toLowerCase()}</h3>${renderMix(data)}`;
  }

  const chartBlock = document.getElementById("chart-block");
  if (chartBlock) {
    const segment = data.timeseries_by_segment.find(
      (s) => s.property_type === state.type && s.area_bucket === state.area
    );
    chartBlock.innerHTML = segment
      ? `<h3>Évolution du coût mensuel équivalent — ${typeLabel.toLowerCase()}, ${areaLabel}</h3>${renderChart(segment)}`
      : `<h3>Évolution du coût mensuel équivalent</h3><p class="muted">Série indisponible pour ce segment.</p>`;
  }
}

function renderMix(data) {
  const counts = data.lease_mix[state.type];
  if (!counts) return `<p class="muted">Pas de données pour ce type.</p>`;

  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const pct = (k) => Math.round(((counts[k] || 0) / total) * 100);

  const parts = [
    ["jeonse", "jeonse", pct("jeonse")],
    ["ban", "ban-jeonse", pct("ban_jeonse")],
    ["wolse", "wolse", pct("wolse")],
  ].filter(([, , p]) => p > 0);

  const bars = parts
    .map(([cls, , p]) => `<span class="${cls}" style="width:${p}%">${p >= 8 ? p + " %" : ""}</span>`)
    .join("");

  const legend = parts
    .map(([cls, label]) => `<span><i class="${cls}" style="background:${
      cls === "jeonse" ? "var(--celadon)" : cls === "ban" ? "var(--slate)" : "var(--ink-faint)"
    }"></i>${label}</span>`)
    .join("");

  return `<div class="mix">${bars}</div><div class="legend">${legend}</div>`;
}

// Graphique dessine a la main en SVG : pas de bibliotheque, donc pas
// d'etape de build ni de 200 Ko de JavaScript a telecharger. Porte a
// l'identique en Python dans src/render.render_chart_svg pour le rendu
// initial des fiches arrondissement : les deux doivent rester en phase.
function renderChart(segment) {
  const points = segment.points.filter((p) => !p.insufficient);
  if (points.length < 3) return `<p class="muted">Série trop courte pour être affichée.</p>`;

  const W = 900, H = 260, PAD_L = 96, PAD_R = 20, PAD_T = 20, PAD_B = 36;
  const values = points.map((p) => p.median);
  const lo = Math.min(...values) * 0.94;
  const hi = Math.max(...values) * 1.06;

  const x = (i) => PAD_L + (i / (points.length - 1)) * (W - PAD_L - PAD_R);
  const y = (v) => PAD_T + (1 - (v - lo) / (hi - lo)) * (H - PAD_T - PAD_B);

  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.median).toFixed(1)}`).join("");

  const gridlines = [lo, (lo + hi) / 2, hi]
    .map((v) => `<line x1="${PAD_L}" y1="${y(v)}" x2="${W - PAD_R}" y2="${y(v)}"
       stroke="var(--rule)" stroke-width="1"/>
       <text x="${PAD_L - 10}" y="${y(v) + 4}" text-anchor="end">${nf.format(Math.round(v))}</text>`)
    .join("");

  // Les points provisoires sont evides : le lecteur doit voir d'un coup d'oeil
  // que les deux derniers mois ne sont pas consolides.
  const dots = points
    .map((p, i) => `<circle cx="${x(i)}" cy="${y(p.median)}" r="3.5"
       fill="${p.provisional ? "var(--paper)" : "var(--celadon)"}"
       stroke="var(--celadon)" stroke-width="1.5"><title>${monthLabel(p.month)} — ${won(p.median)}${p.provisional ? " (provisoire)" : ""}</title></circle>`)
    .join("");

  const step = Math.ceil(points.length / 6);
  const labels = points
    .map((p, i) => (i % step === 0 ? `<text x="${x(i)}" y="${H - 12}" text-anchor="middle">${monthLabel(p.month)}</text>` : ""))
    .join("");

  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Évolution mensuelle du coût équivalent, points évidés pour les mois provisoires">
    ${gridlines}
    <path d="${line}" fill="none" stroke="var(--celadon)" stroke-width="2"
      stroke-linejoin="round" stroke-linecap="round"/>
    ${dots}${labels}
  </svg>
  <p class="muted" style="font-size:14px">Les points évidés sont des mois provisoires.</p>`;
}

boot();
