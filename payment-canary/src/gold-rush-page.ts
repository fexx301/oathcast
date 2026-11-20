// The Gold Rush page: a Queensland map of historical gold workings, a site panel and the investigation report.
export const PAGE = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gold Rush</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
:root { --bg:#14120e; --panel:#1d1a14; --ink:#f1ece0; --mute:#b2a993; --gold:#e3b341; --line:#3a3428; --bad:#e0745f; --ok:#8fc49a; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 system-ui, -apple-system, Segoe UI, sans-serif; }
header { padding:18px 20px 10px; }
h1 { margin:0; font-size:26px; letter-spacing:-0.01em; } h1 span { color:var(--gold); }
header p { margin:6px 0 0; color:var(--mute); max-width:900px; }
main { display:grid; grid-template-columns:minmax(0,3fr) minmax(320px,2fr); gap:16px; padding:0 20px 20px; }
#map { height:72vh; min-height:420px; border-radius:12px; border:1px solid var(--line); }
aside { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px; max-height:72vh; overflow:auto; }
h2 { font-size:18px; margin:0 0 8px; } h3 { font-size:15px; margin:14px 0 6px; }
.mute { color:var(--mute); } .small { font-size:13px; }
button { background:var(--gold); color:#1d1a14; border:0; border-radius:8px; padding:10px 14px; font-weight:700; font-size:15px; cursor:pointer; }
button:disabled { opacity:.55; cursor:wait; }
.card { border:1px solid var(--line); border-radius:10px; padding:12px; margin:10px 0; }
.card .who { font-weight:700; } .tag { font-size:12px; border:1px solid var(--line); border-radius:999px; padding:1px 8px; margin-left:6px; color:var(--mute); }
.mono { font-family:ui-monospace, Menlo, monospace; font-size:12px; word-break:break-all; }
table { width:100%; border-collapse:collapse; font-size:14px; } td, th { text-align:left; padding:6px 4px; border-bottom:1px solid var(--line); vertical-align:top; }
.yes { color:var(--ok); } .no { color:var(--bad); }
.legend { display:flex; gap:12px; flex-wrap:wrap; font-size:13px; color:var(--mute); margin-top:8px; }
.dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:4px; vertical-align:middle; }
footer { padding:0 20px 24px; color:var(--mute); font-size:13px; max-width:1100px; }
a { color:var(--gold); }
@media (max-width: 900px) { main { grid-template-columns:1fr; } #map { height:55vh; } aside { max-height:none; } }
</style>
</head>
<body>
<header>
  <h1>Gold <span>Rush</span></h1>
  <p>Queensland's historical gold workings, from the Geological Survey of Queensland. Pick one and the app asks every
  specialist Miner on Telegraph about it, plus Telegraph's ranked route for comparison. Each answer shows who gave it,
  what it cost and its receipt, and the report says which questions nobody could answer.</p>
  <div class="legend" id="legend"></div>
</header>
<main>
  <div id="map" role="region" aria-label="Map of Queensland historical gold workings"></div>
  <aside aria-live="polite">
    <div id="panel"><h2>Pick a gold working</h2><p class="mute"><span id="count">Loading the survey's sites…</span>
    Larger circles are larger gold deposits in the survey's size classes.</p></div>
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
  answer; it appears about an hour after the call). The app wallet's on-chain funding:
  <a href="https://sepolia.etherscan.io/tx/0xf9afd87bbf3675d875867adcf5259f523f5f9a526ced432c02e29964f2f3c756" target="_blank" rel="noopener">deposit tx 0xf9afd87b…c756</a>
  (2 test USDC into the Telegraph Diamond, 20 Nov 2026).</p>
  <p>Map data: Geological Survey of Queensland, MINOCC historical workings, CC BY 4.0, State of Queensland. Tiles ©
  OpenStreetMap contributors. Every paid call: <a href="/gold-rush/api/ledger">public ledger</a>.</p>
</footer>
<script>
const BASE = "/gold-rush/";
const COLORS = { giant:"#ff6b3d", large:"#e3b341", medium:"#d9c27a", small:"#8c8572", "very small":"#5f5a4c" };
const RADIUS = { giant:11, large:9, medium:7, small:4, "very small":3 };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
const title = (s) => String(s || "").toLowerCase().replace(/\\b\\w/g, (c) => c.toUpperCase());
document.getElementById("legend").innerHTML = Object.keys(COLORS).map((k) =>
  '<span><i class="dot" style="background:' + COLORS[k] + '"></i>' + k + "</span>").join("");

const map = L.map("map", { preferCanvas: true }).setView([-20.5, 145.5], 5);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 15, attribution: "© OpenStreetMap contributors" }).addTo(map);
let specialists = [];

function select(s) {
  const n = specialists.length;
  document.getElementById("report").innerHTML = "";
  document.getElementById("panel").innerHTML =
    "<h2>" + esc(title(s.name)) + "</h2>" +
    '<p class="mute small">Survey site ' + s.site_no + " · " + esc(title(s.sheet)) + " sheet · gold " + esc(s.band || "unsized") +
    " · " + s.lat.toFixed(4) + ", " + s.lon.toFixed(4) + "</p>" +
    "<p>Ask " + n + " specialist" + (n === 1 ? "" : "s") + " and the ranked route: who owned it, how much gold it produced, and when and why it closed.</p>" +
    '<button id="go">Investigate (up to $' + ((n + 1) * 0.01).toFixed(2) + " test USDC)</button>";
  document.getElementById("go").onclick = () => investigate(s);
}

async function investigate(s) {
  const btn = document.getElementById("go");
  btn.disabled = true;
  document.getElementById("report").innerHTML = '<p class="mute">Buying answers through Telegraph…</p>';
  try {
    const res = await fetch(BASE + "api/investigate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ site_no: s.site_no }) });
    const r = await res.json();
    if (!res.ok) { document.getElementById("report").innerHTML = '<p class="no">' + esc(r.error) + "</p>"; return; }
    render(r);
  } catch { document.getElementById("report").innerHTML = '<p class="no">The request failed. Try again.</p>'; }
  finally { btn.disabled = false; }
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
    '<p class="mute small">A keyword check of each answer. Read the answers; "nobody" marks a gap for the next Miner to fill.</p>' +
    "<h3>Answers</h3>" + cards;
}

(async () => {
  try {
    specialists = (await (await fetch(BASE + "api/specialists")).json()).specialists || [];
    const { sites } = await (await fetch(BASE + "api/sites")).json();
    document.getElementById("count").textContent = sites.length.toLocaleString() + " historical workings with gold.";
    // Small sites first, so the large ones are drawn on top and stay clickable.
    sites.sort((a, b) => RADIUS[a.band || "very small"] - RADIUS[b.band || "very small"]);
    for (const s of sites) {
      const b = s.band || "very small";
      L.circleMarker([s.lat, s.lon], { radius: RADIUS[b], color: COLORS[b], weight: 1, fillOpacity: 0.75 })
        .bindTooltip(esc(title(s.name)) + " · " + esc(b)).on("click", () => select(s)).addTo(map);
    }
  } catch { document.getElementById("panel").innerHTML = '<p class="no">Could not load the survey sites. Reload to try again.</p>'; }
})();
</script>
</body>
</html>`;
