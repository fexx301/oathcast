// The Gold Rush page: a Queensland map of historical gold workings, search and quick picks, a site panel and the
// investigation report. Single file: inline CSS/JS, Leaflet from cdnjs, OpenStreetMap tiles darkened with a CSS filter.
const MARK = `<svg viewBox="0 0 32 32" aria-hidden="true"><path d="M6 19 11 8l10-2 6 9-4 11-12 1z" fill="#c9952b"/><path d="M11 8l10-2 6 9-9 2z" fill="#f2c45a"/><path d="M11 8l7 9-7 10-5-8z" fill="#a8761c"/><path d="M18 17l9-2-4 11-12 1z" fill="#e0ad3f"/></svg>`;
const FAVICON = "data:image/svg+xml," + encodeURIComponent(MARK.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" '));

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
.brand svg { width: 34px; height: 34px; }
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

async function investigate(s) {
  const btn = document.getElementById("go");
  btn.disabled = true; btn.textContent = "Buying answers through Telegraph…";
  document.getElementById("report").innerHTML = "";
  try {
    const res = await fetch(BASE + "api/investigate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ site_no: s.site_no }) });
    const r = await res.json();
    if (!res.ok) { document.getElementById("report").innerHTML = '<p class="no">' + esc(r.error) + "</p>"; return; }
    render(r);
  } catch { document.getElementById("report").innerHTML = '<p class="no">The request failed. Try again.</p>'; }
  finally { btn.disabled = false; btn.textContent = "Investigate again (reuses paid answers)"; }
}

// Structured fields a specialist returned (production, closure, gold price, not found), shown as rows.
function facts(f) {
  if (!f) return "";
  const rows = [];
  for (const p of (Array.isArray(f.production) ? f.production : [])) rows.push(["production", [p.amount, p.unit, p.commodity, (p.period_from || "") + (p.period_to ? "–" + p.period_to : ""), p.basis].filter((x) => x !== undefined && x !== "").join(" "), p.source_url || p.source_ref]);
  if (f.closure && typeof f.closure === "object") rows.push(["closure", [f.closure.year, f.closure.reason_class, f.closure.reason_text ? "“" + f.closure.reason_text + "”" : ""].filter(Boolean).join(" · "), f.closure.source_ref]);
  if (f.gold_price && typeof f.gold_price === "object") rows.push(["gold price", [f.gold_price.month, f.gold_price.usd_per_oz && "US$" + f.gold_price.usd_per_oz + "/oz", f.gold_price.aud_per_oz && "A$" + f.gold_price.aud_per_oz + "/oz"].filter(Boolean).join(" · "), f.gold_price.source_url || f.gold_price.series]);
  if (Array.isArray(f.not_found) && f.not_found.length) rows.push(["looked, not found", f.not_found.join(", "), ""]);
  if (!rows.length) return "";
  return '<table class="small">' + rows.map(([k, v, s]) => "<tr><td>" + esc(k) + "</td><td>" + esc(v) + (s ? '<br><span class="mute">' + esc(s) + "</span>" : "") + "</td></tr>").join("") + "</table>";
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

function render(r) {
  const rows = Object.entries(r.covered).map(([t, who]) =>
    "<tr><td>" + esc(t) + "</td><td>" + (who.length ? '<span class="yes">' + esc(who.join(", ")) + "</span>" : '<span class="no">nobody</span>') + "</td></tr>").join("");
  const cards = r.answers.map((a) => {
    const head = '<div class="who">' + esc(a.miner_name || "—") + (a.miner_id ? " · " + esc(a.miner_id) : "") +
      '<span class="tag">' + esc(a.path) + "</span>" + (a.reused ? '<span class="tag">reused, no new charge</span>' : "") + "</div>";
    if (a.kind !== "served") return '<div class="card">' + head + '<p class="no small">' + esc(a.reason || a.kind) + "</p></div>";
    const src = (a.sources || []).map((x) => /^https?:/.test(x) ? '<a href="' + esc(x) + '" rel="noopener" target="_blank">' + esc(x.slice(0, 70)) + "</a>" : esc(x)).join("<br>");
    return '<div class="card">' + head + "<p>" + esc(a.answer) + "</p>" + facts(a.facts) + (src ? '<p class="small">' + src + "</p>" : "") +
      proof(a) + "</div>";
  }).join("");
  document.getElementById("report").innerHTML =
    "<h3>Who answered what</h3><table><tr><th>Question</th><th>Answered by</th></tr>" + rows + "</table>" +
    '<p class="mute small">A keyword check of each answer, plus any structured fields. Read the answers; "nobody" marks a gap for the next miner to fill.</p>' +
    "<h3>Answers</h3>" + cards;
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
