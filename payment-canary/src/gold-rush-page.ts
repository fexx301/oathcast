// The Gold Rush page: a Queensland map of historical gold workings, search and quick picks, a site panel and the
// investigation report. Single file: inline CSS/JS, Leaflet from cdnjs, OpenStreetMap tiles darkened with a CSS filter.
// The Gold Rush mark (user-supplied, 2026-11-20): a folded map with a mine entrance and track.
const MARK_PATHS = `<g fill="#F6E8D0"><path d="M158 430 271.5 384.5V586.5L158 633Z"/><path d="M280 380.5 288 377 416 421Q421 422.5 421 427V615 L392.5 576V496L371 472H326.5L304.5 496V570L290.5 589.5 L280 586Z"/><path d="M430 420 516.5 381Q519 380 523 384V585L434 623 Q432 624 430 622Z"/></g><path fill="#D7A548" d="M252 640.5H269.5L324.5 563H333.5L329 586 L305 640.5H388L366 585L363 563H372.5L422 640.5H439V658H252Z"/>`;
const MARK = `<svg viewBox="150 370 380 296" aria-hidden="true">${MARK_PATHS}</svg>`;
// Tab icon: the same mark on a dark rounded square, so the cream stays visible on light browser tabs.
const FAVICON = "data:image/svg+xml," + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="130 308 420 420"><rect x="130" y="308" width="420" height="420" rx="84" fill="#1c1914"/>${MARK_PATHS}</svg>`);

export const PAGE = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gold Rush · Queensland's historical gold mines</title>
<meta name="description" content="Investigate Queensland's historical gold mines. Every answer bought from specialist Telegraph miners, with its source and receipt.">
<link rel="icon" href="${FAVICON}">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
:root {
  --paper: oklch(17% 0.012 75); --panel: oklch(21% 0.014 75); --raise: oklch(25% 0.016 75); --line: oklch(32% 0.018 75);
  --ink: oklch(94% 0.012 85); --mute: oklch(74% 0.02 80); --faint: oklch(58% 0.02 80);
  --gold: oklch(80% 0.14 82); --gold-deep: oklch(66% 0.13 70); --giant: oklch(70% 0.17 40);
  --ok: oklch(80% 0.11 150); --bad: oklch(72% 0.13 30); --focus: oklch(86% 0.15 90);
  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px; --space-5: 24px; --space-6: 32px;
  --radius: 12px; --ease-out: cubic-bezier(.2,.7,.2,1);
  --font: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html, body { overflow-x: clip; }
body { margin: 0; background: var(--paper); color: var(--ink); font: 16px/1.5 var(--font); }
a { color: var(--gold); text-underline-offset: 2px; }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

header { display: flex; align-items: center; gap: var(--space-4); padding: var(--space-4) var(--space-5); flex-wrap: wrap; }
.brand { display: flex; align-items: center; gap: var(--space-3); text-decoration: none; color: var(--ink); }
.brand svg { width: 44px; height: 34px; }
.brand b { font-size: 24px; letter-spacing: -0.02em; font-weight: 800; } .brand b span { color: var(--gold); }
header p { margin: 0; color: var(--mute); font-size: 15px; flex: 1 1 360px; min-width: 0; }

.toolbar { display: flex; gap: var(--space-3); align-items: center; flex-wrap: wrap; padding: 0 var(--space-5) var(--space-3); }
.search { position: relative; z-index: 2000; flex: 1 1 280px; max-width: 420px; min-width: 0; }
.search input { width: 100%; background: var(--panel); color: var(--ink); border: 1px solid var(--line); border-radius: 10px;
  padding: 10px 12px 10px 36px; font: inherit; font-size: 15px; }
.search input::placeholder { color: var(--faint); }
.search svg { position: absolute; left: 11px; top: 50%; width: 16px; height: 16px; transform: translateY(-50%); stroke: var(--faint); fill: none; }
.results { position: absolute; z-index: 2000; left: 0; right: 0; top: calc(100% + 4px); background: var(--raise); border: 1px solid var(--line);
  border-radius: 10px; margin: 0; padding: var(--space-1); list-style: none; max-height: 320px; overflow: auto; box-shadow: 0 12px 30px oklch(0% 0 0 / .45); }
.results[hidden] { display: none; }
.results button { width: 100%; display: flex; justify-content: space-between; gap: var(--space-3); text-align: left; background: none; color: var(--ink);
  border: 0; border-radius: 8px; padding: 8px 10px; font: inherit; font-size: 14px; cursor: pointer; }
.results button:hover, .results button.active { background: var(--line); }
.results small { color: var(--mute); white-space: nowrap; }
.chips { display: flex; gap: var(--space-2); flex-wrap: wrap; }
.chip { display: inline-flex; align-items: center; gap: 6px; background: var(--panel); color: var(--mute); border: 1px solid var(--line);
  border-radius: 999px; padding: 6px 12px; font: inherit; font-size: 13px; cursor: pointer; white-space: nowrap; }
.chip:hover { color: var(--ink); border-color: var(--faint); }
.chip[aria-pressed="false"] { opacity: .55; } .chip[aria-pressed="false"] .dot { background: transparent !important; }
.chip .n { color: var(--faint); font-variant-numeric: tabular-nums; }
.dot { width: 10px; height: 10px; border-radius: 50%; border: 1.5px solid currentColor; display: inline-block; }

main { display: grid; grid-template-columns: minmax(0, 3fr) minmax(340px, 2fr); gap: var(--space-4); padding: 0 var(--space-5) var(--space-5); }
#map { height: 72vh; min-height: 460px; border-radius: var(--radius); border: 1px solid var(--line); background: var(--paper); }
.leaflet-container { font: 13px/1.4 var(--font); }
/* OpenStreetMap tiles, darkened in the browser so the gold sites stand out (no API key, no extra provider). */
.leaflet-tile-pane { filter: invert(1) hue-rotate(180deg) brightness(0.82) contrast(0.88) saturate(0.35); }
.leaflet-tooltip { background: var(--raise); color: var(--ink); border: 1px solid var(--line); box-shadow: none; }
.leaflet-tooltip::before { display: none; }
.leaflet-control-attribution { background: oklch(17% 0.012 75 / .8) !important; color: var(--faint); }
.leaflet-control-attribution a { color: var(--mute); }
.leaflet-bar a { background: var(--raise); color: var(--ink); border-color: var(--line); }

aside { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-5); max-height: 72vh; overflow: auto; min-width: 0; }
h1, h2 { font-size: 22px; letter-spacing: -0.01em; margin: 0 0 var(--space-2); line-height: 1.2; }
h3 { font-size: 15px; margin: var(--space-5) 0 var(--space-2); color: var(--mute); font-weight: 700; }
.meta { color: var(--mute); font-size: 14px; margin: 0 0 var(--space-4); display: flex; flex-wrap: wrap; gap: 4px 12px; }
.meta b { color: var(--ink); font-weight: 600; }
.steps { margin: var(--space-4) 0 var(--space-5); padding: 0; list-style: none; counter-reset: s; }
.steps li { counter-increment: s; padding-left: 30px; position: relative; margin: var(--space-2) 0; color: var(--mute); font-size: 15px; }
.steps li::before { content: counter(s); position: absolute; left: 0; top: 1px; width: 20px; height: 20px; border-radius: 50%; background: var(--raise);
  color: var(--gold); font-size: 12px; font-weight: 800; display: grid; place-items: center; }
.picks { display: flex; flex-direction: column; gap: var(--space-2); }
.pick { display: flex; justify-content: space-between; align-items: center; gap: var(--space-3); background: var(--raise); color: var(--ink);
  border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px; font: inherit; font-size: 15px; cursor: pointer; text-align: left; }
.pick:hover { border-color: var(--gold-deep); }
.pick small { color: var(--mute); white-space: nowrap; }
.go { background: var(--gold); color: oklch(20% 0.02 75); border: 0; border-radius: 10px; padding: 12px 16px; font: inherit; font-weight: 800;
  font-size: 15px; cursor: pointer; transition: transform .12s var(--ease-out), background .12s var(--ease-out); }
.go:hover { background: oklch(84% 0.14 85); } .go:active { transform: translateY(1px); }
.go:disabled { opacity: .6; cursor: progress; }
.back { background: none; border: 0; color: var(--mute); font: inherit; font-size: 14px; padding: 0; margin-bottom: var(--space-3); cursor: pointer; }
.back:hover { color: var(--ink); }
.card { border: 1px solid var(--line); border-radius: 10px; padding: var(--space-4); margin: var(--space-3) 0; background: oklch(19% 0.013 75); }
.card .who { font-weight: 700; } .card p { margin: var(--space-2) 0; overflow-wrap: anywhere; }
.tag { font-size: 12px; border: 1px solid var(--line); border-radius: 999px; padding: 1px 8px; margin-left: 6px; color: var(--mute); font-weight: 500; }
.mono { font-family: var(--mono); font-size: 12px; color: var(--mute); overflow-wrap: anywhere; }
table { width: 100%; border-collapse: collapse; font-size: 14px; } td, th { text-align: left; padding: 8px 4px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--faint); font-weight: 600; font-size: 13px; }
.yes { color: var(--ok); } .no { color: var(--bad); } .mute { color: var(--mute); } .small { font-size: 13px; }

.q { color: var(--mute); font-size: 14px; margin: 0 0 var(--space-3); }
.run { list-style: none; padding: 0; margin: 0 0 var(--space-4); }
.run li { display: flex; gap: var(--space-3); padding: var(--space-2) 0; border-bottom: 1px solid var(--line); font-size: 14px; }
.run li small { color: var(--mute); overflow-wrap: anywhere; }
.run .st { width: 18px; flex: none; text-align: center; font-weight: 800; color: var(--faint); }
.run .busy .st { color: var(--gold); } .run .done .st { color: var(--ok); } .run .fail .st { color: var(--bad); }
.verdict { background: var(--raise); border: 1px solid var(--line); border-radius: 10px; padding: var(--space-3) var(--space-4); margin: var(--space-3) 0; }
.verdict p { margin: 4px 0; font-size: 14px; } .verdict span { font-weight: 800; margin-right: 6px; }
.warn { color: var(--gold); } .facts tr.flag td { background: oklch(80% 0.14 82 / .06); }
.facts small { color: var(--mute); }
.ghost { background: none; color: var(--ink); border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px; font: inherit; font-size: 14px; cursor: pointer; }
.ghost:hover { border-color: var(--gold-deep); }
footer { padding: 0 var(--space-5) var(--space-6); color: var(--faint); font-size: 13px; max-width: 1100px; }
footer strong { color: var(--mute); }

@media (max-width: 900px) {
  header, .toolbar { padding-left: var(--space-4); padding-right: var(--space-4); }
  main { grid-template-columns: minmax(0, 1fr); padding: 0 var(--space-4) var(--space-4); }
  #map { height: 52vh; min-height: 320px; }
  aside { max-height: none; padding: var(--space-4); }
  .search { max-width: none; flex-basis: 100%; }
  footer { padding: 0 var(--space-4) var(--space-5); }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
</head>
<body>
<header>
  <a class="brand" href="/gold-rush" aria-label="Gold Rush home">${MARK}<b>Gold <span>Rush</span></b></a>
  <p>Investigate Queensland's historical gold mines. Every answer is bought from specialist Telegraph miners, with its source and receipt.</p>
</header>
<div class="toolbar">
  <div class="search">
    <label class="sr-only" for="q">Search a mine, lode or goldfield</label>
    <svg viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
    <input id="q" type="search" autocomplete="off" placeholder="Search a mine or goldfield" aria-controls="results" aria-expanded="false">
    <ul class="results" id="results" role="listbox" hidden></ul>
  </div>
  <div class="chips" id="sizes" role="group" aria-label="Show gold workings by size class"></div>
</div>
<main>
  <div id="map" role="region" aria-label="Map of Queensland historical gold workings"></div>
  <aside aria-live="polite">
    <div id="panel"></div>
    <div id="report"></div>
  </aside>
</main>
<footer>
  <p><strong>How it pays.</strong> Each answer is one $0.01 test-USDC call from this app's Telegraph escrow on Ethereum
  Sepolia (testnet). A repeat question to the same Miner reuses the earlier paid answer at no charge. Specialists are
  the Miners whose catalogue entry for RESEARCH_QUERY_GENERAL names gold, mining, geology or Queensland; they're asked
  directly. <strong>Disclosure:</strong> this app is run by the OathCast team, which also runs Miner 64176, one of the
  specialists, so those calls are related-party.</p>
  <p><strong>Proof.</strong> The escrow rail has no blockchain transaction per answer: each call is charged to the app's
  escrow and settled at the epoch close. Its proof is the receipt and the node's delivery record (linked on each
  answer; it appears after the epoch is recorded). The app wallet's on-chain funding:
  <a href="https://sepolia.etherscan.io/tx/0xf9afd87bbf3675d875867adcf5259f523f5f9a526ced432c02e29964f2f3c756" target="_blank" rel="noopener">deposit tx 0xf9afd87b…c756</a>
  (2 test USDC into the Telegraph Diamond, 20 Nov 2026).</p>
  <p>Map data: Geological Survey of Queensland, MINOCC historical workings, CC BY 4.0, State of Queensland. Base map ©
  OpenStreetMap contributors. Every paid call: <a href="/gold-rush/api/ledger">public ledger</a> ·
  <a href="https://github.com/fexx301/oathcast/blob/s2-gold-rush/docs/gold-rush.md" target="_blank" rel="noopener">how it works</a>.</p>
</footer>
<script>
const BASE = "/gold-rush/";
const LINKED = Number((/site=(\\d+)/.exec(location.hash) || [])[1]); // read before home() clears the URL
const BANDS = ["giant", "large", "medium", "small", "very small"];
const COLORS = { giant: "oklch(70% 0.17 40)", large: "oklch(80% 0.14 82)", medium: "oklch(74% 0.09 88)", small: "oklch(60% 0.04 80)", "very small": "oklch(48% 0.02 80)" };
const RADIUS = { giant: 11, large: 9, medium: 7, small: 4.5, "very small": 3 };
const PICKS = [[494270, "the one giant"], [38722, "Charters Towers"], [38940, "Charters Towers"], [40172, "Gympie"]];
const QLD = [[-29.2, 137.9], [-10.1, 153.6]];
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const title = (s) => String(s || "").toLowerCase().replace(/\\b\\w/g, (c) => c.toUpperCase());

const map = L.map("map", { preferCanvas: true, zoomSnap: 0.5 });
map.fitBounds(QLD, { padding: [8, 8] });
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 16, attribution: "© OpenStreetMap contributors" }).addTo(map);

let specialists = [], sites = [], byNo = new Map(), ring = null;
const layers = Object.fromEntries(BANDS.map((b) => [b, L.layerGroup()]));
const shown = { giant: true, large: true, medium: true, small: true, "very small": false };
// "very small" (most of the 8,000) is hidden at state scale but appears when zoomed in on a goldfield,
// unless the viewer has toggled it themselves.
let vsTouched = false;
const CLOSE = 9;
function syncVerySmall() {
  if (vsTouched) return;
  const on = map.getZoom() >= CLOSE;
  if (on === shown["very small"]) return;
  shown["very small"] = on;
  on ? layers["very small"].addTo(map) : layers["very small"].remove();
  const c = document.querySelector('.chip[data-band="very small"]');
  if (c) c.setAttribute("aria-pressed", on);
}

function home() {
  history.replaceState(null, "", location.pathname);
  if (ring) { ring.remove(); ring = null; }
  document.getElementById("report").innerHTML = "";
  document.getElementById("panel").innerHTML =
    "<h1>Pick a gold working</h1>" +
    '<p class="mute">' + (sites.length ? sites.length.toLocaleString() + " historical workings with gold, from the Geological Survey of Queensland." : "Loading the survey's sites…") + "</p>" +
    '<ol class="steps"><li>Pick a site on the map, search above, or try one below.</li>' +
    "<li>Investigate: the app asks every specialist miner on Telegraph who owned it, how much gold it produced, and when and why it closed.</li>" +
    "<li>Read who answered what, with sources and receipts, and what's still missing.</li></ol>" +
    '<div class="picks">' + PICKS.filter(([n]) => byNo.has(n)).map(([n, note]) => {
      const s = byNo.get(n);
      return '<button class="pick" data-site="' + n + '"><span>' + esc(title(s.name)) + "</span><small>" + esc(s.band || "") + " · " + esc(note) + "</small></button>";
    }).join("") + "</div>";
  document.querySelectorAll(".pick").forEach((b) => b.addEventListener("click", () => focusSite(byNo.get(Number(b.dataset.site)))));
}

function focusSite(s) {
  if (!s) return;
  const z = Math.max(map.getZoom(), 9);
  REDUCED ? map.setView([s.lat, s.lon], z) : map.flyTo([s.lat, s.lon], z, { duration: 0.8 });
  select(s);
}

function select(s) {
  const n = specialists.length;
  history.replaceState(null, "", "#site=" + s.site_no);
  if (ring) ring.remove();
  ring = L.circleMarker([s.lat, s.lon], { radius: (RADIUS[s.band || "very small"]) + 7, color: "oklch(94% 0.012 85)", weight: 2, fill: false, interactive: false }).addTo(map);
  document.getElementById("report").innerHTML = "";
  document.getElementById("panel").innerHTML =
    '<button class="back" id="back">← All sites</button>' +
    "<h2>" + esc(title(s.name)) + "</h2>" +
    '<p class="meta"><span>Survey site <b>' + s.site_no + "</b></span><span>Gold <b>" + esc(s.band || "unsized") + "</b></span><span>" +
    esc(title(s.sheet)) + " sheet</span><span>" + s.lat.toFixed(4) + ", " + s.lon.toFixed(4) + "</span></p>" +
    "<p>Ask " + n + " specialist" + (n === 1 ? "" : "s") + " and Telegraph's ranked route: who owned it, how much gold it produced, and when and why it closed.</p>" +
    '<button class="go" id="go">Investigate · up to $' + ((n + 1) * 0.01).toFixed(2) + " test USDC</button>";
  document.getElementById("go").onclick = () => investigate(s);
  document.getElementById("back").onclick = home;
}

// Step-by-step investigation: plan (free), then one paid ask per miner, each rendered as it lands.
let current = null;
async function investigate(s) {
  const btn = document.getElementById("go");
  btn.disabled = true; btn.textContent = "Investigating…";
  const rep = document.getElementById("report");
  try {
    const pr = await fetch(BASE + "api/plan?site_no=" + s.site_no);
    const plan = await pr.json();
    if (!pr.ok) { rep.innerHTML = '<p class="no">' + esc(plan.error) + "</p>"; return; }
    current = { site: s, question: plan.question, answers: [], started: new Date().toISOString() };
    rep.innerHTML = '<h3>Investigation</h3><p class="q">“' + esc(plan.question) + '”</p><ol class="run" id="run">' +
      plan.steps.map((st, i) => '<li id="step-' + i + '" class="wait"><span class="st">○</span><span><b>' + esc(st.miner_name) +
        '</b> <span class="tag">' + esc(st.path) + '</span><br><small>waiting</small></span></li>').join("") + "</ol>" +
      '<div id="summary"></div><div id="answers"></div>';
    for (let i = 0; i < plan.steps.length; i++) {
      const st = plan.steps[i], row = document.getElementById("step-" + i);
      row.className = "busy"; row.querySelector(".st").textContent = "◌"; row.querySelector("small").textContent = "asking through Telegraph…";
      let a;
      try {
        const res = await fetch(BASE + "api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ site_no: s.site_no, miner: st.miner }) });
        a = await res.json();
        if (!res.ok) a = { kind: "refused", reason: a.error, miner_name: st.miner_name, path: st.path };
      } catch { a = { kind: "refused", reason: "The request failed.", miner_name: st.miner_name, path: st.path }; }
      current.answers.push(a);
      const rc = a.receipt || {};
      row.className = a.kind === "served" ? "done" : "fail";
      row.querySelector(".st").textContent = a.kind === "served" ? "✓" : "✕";
      row.querySelector("small").textContent = a.kind === "served"
        ? (a.reused ? "answered · reused, no new charge" : "answered · paid $0.01") + (rc.receipt_hash ? " · receipt " + rc.receipt_hash.slice(0, 10) + "…" : "") +
          (a.path === "ranked route" && a.miner_name ? " · router picked " + a.miner_name : "")
        : (a.reason || a.kind) + (a.kind === "paid_refusal" || a.kind === "refused" ? " · not charged" : "");
      renderReport();
    }
    document.getElementById("answers").insertAdjacentHTML("afterend", '<p><button class="ghost" id="dl">Download report (JSON)</button></p>');
    document.getElementById("dl").onclick = download;
  } catch { rep.innerHTML = '<p class="no">The request failed. Try again.</p>'; }
  finally { btn.disabled = false; btn.textContent = "Investigate again · reuses paid answers"; }
}

// Survey size classes in tonnes of gold, for checking reported production against the record.
const BAND_T = { "very small": [0, 0.5], small: [0.5, 5], medium: [5, 50], large: [50, 150], giant: [150, Infinity] };
const OZ_PER_T = 32150.7466;
function tonnes(p) {
  const n = Number(p.amount), u = String(p.unit || "").toLowerCase();
  if (!isFinite(n)) return null;
  return u === "t" || u === "tonnes" ? n : u === "kg" ? n / 1000 : u === "oz" ? n / OZ_PER_T : u === "koz" ? n * 1e3 / OZ_PER_T : u === "moz" ? n * 1e6 / OZ_PER_T : null;
}
const fmtT = (t) => t >= 10 ? t.toFixed(0) + " t" : t >= 1 ? t.toFixed(1) + " t" : (t * 1000).toFixed(0) + " kg";

// The report: what's known and missing, every structured fact with its miner and source, conflicts flagged, then the answers.
function renderReport() {
  const r = current, s = r.site, served = r.answers.filter((a) => a.kind === "served");
  const TOP = ["ownership", "production", "closure", "gold price"];
  const who = Object.fromEntries(TOP.map((t) => [t, served.filter((a) => (a.topics || {})[t]).map((a) => a.miner_name)]));
  const known = ["location and size class (survey record)"].concat(TOP.filter((t) => who[t].length));
  const missing = TOP.filter((t) => !who[t].length);
  const rows = [], flags = [];
  rows.push(["size class", (s.band || "unsized") + (BAND_T[s.band] ? " (" + (BAND_T[s.band][1] === Infinity ? "over " + BAND_T[s.band][0] : BAND_T[s.band][0] + "–" + BAND_T[s.band][1]) + " t of gold)" : ""), "GSQ MINOCC record", ""]);
  const prod = [];
  for (const a of served) {
    const f = a.facts || {};
    for (const p of (Array.isArray(f.production) ? f.production : [])) {
      const t = tonnes(p), period = (p.period_from || "") + "–" + (p.period_to || "");
      prod.push({ t, a, period });
      const range = BAND_T[s.band];
      let flag = "";
      // The survey's class is the mine's lifetime total: a figure above it is suspect; a lifetime figure (no period) below it too.
      if (t != null && range && (t > range[1] || (period === "–" && t < range[0]))) {
        flag = t > range[1] ? "more than the survey's size class allows" : "below the survey's size class for a lifetime total";
        flags.push(a.miner_name + ": " + fmtT(t) + " reported vs " + s.band + " in the survey");
      }
      rows.push(["production", [p.amount, p.unit, p.commodity, (p.period_from || "") + (p.period_to ? "–" + p.period_to : ""), p.basis].filter((x) => x !== undefined && x !== "").join(" ") + (t != null ? " ≈ " + fmtT(t) : ""), a.miner_name, p.source_url || p.source_ref || "", flag]);
    }
    if (f.closure && typeof f.closure === "object") rows.push(["closure", [f.closure.year, f.closure.reason_class, f.closure.reason_text ? "“" + f.closure.reason_text + "”" : ""].filter(Boolean).join(" · "), a.miner_name, f.closure.source_ref || "", ""]);
    if (f.gold_price && typeof f.gold_price === "object") rows.push(["gold price", [f.gold_price.month, f.gold_price.usd_per_oz && "US$" + f.gold_price.usd_per_oz + "/oz", f.gold_price.aud_per_oz && "A$" + f.gold_price.aud_per_oz + "/oz"].filter(Boolean).join(" · "), a.miner_name, f.gold_price.source_url || f.gold_price.series || "", ""]);
  }
  // Different miners, same period, values more than 10% apart: a real disagreement.
  const byPeriod = {};
  for (const x of prod) if (x.t != null) (byPeriod[x.period] = byPeriod[x.period] || []).push(x);
  for (const [period, xs] of Object.entries(byPeriod)) {
    const names = new Set(xs.map((x) => x.a.miner_name));
    const ts = xs.map((x) => x.t);
    if (names.size > 1 && Math.max(...ts) > Math.min(...ts) * 1.1)
      flags.push("miners disagree on production" + (period !== "–" ? " for " + period : "") + ": " + xs.map((x) => x.a.miner_name + " " + fmtT(x.t)).join(" vs "));
  }
  const link = (u) => /^https?:/.test(u) ? '<a href="' + esc(u) + '" target="_blank" rel="noopener">source</a>' : esc(u);
  document.getElementById("summary").innerHTML =
    '<div class="verdict"><p><span class="yes">Known</span> ' + esc(known.join(" · ")) + "</p>" +
    '<p><span class="no">Missing</span> ' + (missing.length ? esc(missing.join(" · ")) : "nothing from the four questions") + "</p>" +
    (flags.length ? '<p><span class="warn">Check</span> ' + esc(flags.join("; ")) + "</p>" : "") + "</div>" +
    '<table class="facts"><tr><th>Fact</th><th>Value</th><th>From</th></tr>' + rows.map(([k, v, m, src, flag]) =>
      "<tr" + (flag ? ' class="flag"' : "") + "><td>" + esc(k) + "</td><td>" + esc(v) + (flag ? '<br><small class="warn">⚠ ' + esc(flag) + "</small>" : "") +
      "</td><td>" + esc(m) + (src ? "<br><small>" + link(src) + "</small>" : "") + "</td></tr>").join("") + "</table>" +
    '<p class="mute small">Topics without structured facts come from a keyword check of sentences that name the mine. Read the answers below.</p>';
  const card = (a) => {
    const head = '<div class="who">' + esc(a.miner_name || "—") + (a.miner_id ? " · " + esc(a.miner_id) : "") +
      '<span class="tag">' + esc(a.path) + "</span>" + (a.reused ? '<span class="tag">reused, no new charge</span>' : "") + "</div>";
    if (a.kind !== "served") return '<div class="card">' + head + '<p class="no small">' + esc(a.reason || a.kind) + "</p></div>";
    const src = (a.sources || []).map((x) => /^https?:/.test(x) ? '<a href="' + esc(x) + '" rel="noopener" target="_blank">' + esc(x.slice(0, 70)) + "</a>" : esc(x)).join("<br>");
    return '<div class="card">' + head + "<p>" + esc(a.answer) + "</p>" + (src ? '<p class="small">' + src + "</p>" : "") + proof(a) + "</div>";
  };
  const spec = r.answers.filter((a) => a.path === "direct"), ranked = r.answers.filter((a) => a.path === "ranked route");
  document.getElementById("answers").innerHTML = (spec.length ? "<h3>Specialist answers</h3>" + spec.map(card).join("") : "") +
    (ranked.length ? '<h3>Ranked-route comparison</h3><p class="mute small">The same question with Telegraph choosing the miner. Shown for contrast.</p>' + ranked.map(card).join("") : "");
}

function download() {
  const r = current;
  const out = { app: "Gold Rush", url: location.origin + "/gold-rush#site=" + r.site.site_no, generated_at: new Date().toISOString(),
    network: "Ethereum Sepolia testnet · Telegraph escrow", site: r.site, question: r.question,
    answers: r.answers.map((a) => ({ path: a.path, kind: a.kind, miner_id: a.miner_id ?? null, miner_name: a.miner_name ?? null, reused: !!a.reused,
      answer: a.answer ?? null, sources: a.sources ?? [], facts: a.facts ?? null, topics: a.topics ?? null, reason: a.reason ?? null,
      receipt: a.receipt ?? null, signal_hash: a.signal_hash ?? null })) };
  const url = URL.createObjectURL(new Blob([JSON.stringify(out, null, 2)], { type: "application/json" }));
  const el = document.createElement("a"); el.href = url; el.download = "gold-rush-" + r.site.site_no + ".json";
  document.body.appendChild(el); el.click(); el.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Request, miner, amount, network and clickable proof for one paid answer.
const NODE = "https://devnode.telegraphprotocol.com";
function proof(a) {
  const rc = a.receipt || {};
  const amt = rc.amount_uusdc ? "$" + (Number(rc.amount_uusdc) / 1e6).toFixed(2) : "$0.01";
  const links = [];
  if (rc.receipt_hash && rc.epoch_id != null && /^0x[0-9a-f]{64}$/i.test(rc.receipt_hash))
    links.push('<a href="' + NODE + "/v1/blocks/" + encodeURIComponent(rc.epoch_id) + "/deliveries/" + rc.receipt_hash + '" target="_blank" rel="noopener">delivery record</a>');
  if (a.signal_hash && /^0x[0-9a-f]{64}$/i.test(a.signal_hash))
    links.push('<a href="' + NODE + "/engine/v1/signal/" + a.signal_hash + '" target="_blank" rel="noopener">signal record</a>');
  return '<p class="mono">' + esc(amt) + " test USDC · Ethereum Sepolia testnet · escrow · receipt " + esc(rc.receipt_hash || "—") +
    " · epoch " + esc(rc.epoch_id ?? "—") + (links.length ? " · " + links.join(" · ") : "") + "</p>";
}

// Size filter: the survey's gold classes; "very small" (most of the 8,000) starts hidden so the larger sites stand out.
function sizeChips() {
  const count = Object.fromEntries(BANDS.map((b) => [b, 0]));
  for (const s of sites) count[s.band || "very small"]++;
  const el = document.getElementById("sizes");
  el.innerHTML = BANDS.map((b) => '<button class="chip" aria-pressed="' + shown[b] + '" data-band="' + b + '"><i class="dot" style="color:' +
    COLORS[b] + ";background:" + COLORS[b] + '"></i>' + b + ' <span class="n">' + count[b].toLocaleString() + "</span></button>").join("");
  el.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
    const b = c.dataset.band; shown[b] = !shown[b]; c.setAttribute("aria-pressed", shown[b]);
    if (b === "very small") vsTouched = true;
    shown[b] ? layers[b].addTo(map) : layers[b].remove();
  }));
}

// Search by mine name or map sheet; larger sites first.
const q = document.getElementById("q"), list = document.getElementById("results");
let hits = [], cursor = -1;
function showResults() {
  const t = q.value.trim().toLowerCase();
  if (t.length < 2) { list.hidden = true; q.setAttribute("aria-expanded", "false"); return; }
  const rank = (s) => BANDS.indexOf(s.band || "very small");
  hits = sites.filter((s) => s.name.toLowerCase().includes(t) || s.sheet.toLowerCase().includes(t))
    .sort((a, b) => (b.name.toLowerCase().startsWith(t) - a.name.toLowerCase().startsWith(t)) || rank(a) - rank(b)).slice(0, 8);
  cursor = -1;
  list.innerHTML = hits.length ? hits.map((s, i) => '<li role="option"><button data-i="' + i + '"><span>' + esc(title(s.name)) + "</span><small>" +
    esc(s.band || "unsized") + " · " + esc(title(s.sheet)) + "</small></button></li>").join("") : '<li class="mute small" style="padding:8px 10px">No gold working matches.</li>';
  list.hidden = false; q.setAttribute("aria-expanded", "true");
  list.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => pick(hits[Number(b.dataset.i)])));
}
function pick(s) { list.hidden = true; q.setAttribute("aria-expanded", "false"); q.value = title(s.name); focusSite(s); }
q.addEventListener("input", showResults);
q.addEventListener("keydown", (e) => {
  const btns = [...list.querySelectorAll("button")];
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault(); if (!btns.length) return;
    cursor = (cursor + (e.key === "ArrowDown" ? 1 : -1) + btns.length) % btns.length;
    btns.forEach((b, i) => b.classList.toggle("active", i === cursor));
  } else if (e.key === "Enter" && hits.length) { e.preventDefault(); pick(hits[Math.max(cursor, 0)]); }
  else if (e.key === "Escape") { list.hidden = true; q.setAttribute("aria-expanded", "false"); }
});
document.addEventListener("click", (e) => { if (!e.target.closest(".search")) list.hidden = true; });

(async () => {
  home();
  try {
    specialists = (await (await fetch(BASE + "api/specialists")).json()).specialists || [];
    sites = (await (await fetch(BASE + "api/sites")).json()).sites || [];
    byNo = new Map(sites.map((s) => [s.site_no, s]));
    for (const s of sites) {
      const b = s.band || "very small";
      L.circleMarker([s.lat, s.lon], { radius: RADIUS[b], color: COLORS[b], weight: 1, fillColor: COLORS[b], fillOpacity: b === "very small" ? 0.6 : 0.85 })
        .bindTooltip(esc(title(s.name)) + " · " + esc(b)).on("click", () => select(s)).addTo(layers[b]);
    }
    // Small classes first, so the large ones are drawn on top and stay clickable.
    [...BANDS].reverse().forEach((b) => { if (shown[b]) layers[b].addTo(map); });
    sizeChips();
    map.on("zoomend", syncVerySmall);
    if (byNo.has(LINKED)) focusSite(byNo.get(LINKED)); else home();
  } catch { document.getElementById("panel").innerHTML = '<p class="no">Could not load the survey sites. Reload to try again.</p>'; }
})();
</script>
</body>
</html>`;
