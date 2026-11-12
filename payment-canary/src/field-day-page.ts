// The Field-day review page: one form, one verdict, the public call ledger.
export const PAGE = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Field-day review</title>
<style>
:root { --bg:#f6f4ee; --ink:#1d2320; --muted:#5d6661; --line:#d9d5ca; --card:#fffdf8; --held:#256b3a; --failed:#9a3412; --accent:#2f5d50; }
@media (prefers-color-scheme: dark) { :root { --bg:#131614; --ink:#e7e6e0; --muted:#9aa29d; --line:#2c312e; --card:#1a1e1b; --held:#6fcf8d; --failed:#f29a6b; --accent:#8cc5b2; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width:760px; margin:0 auto; padding:32px 16px 64px; }
h1 { font-size:1.75rem; margin:0 0 4px; letter-spacing:-0.01em; }
.lede { color:var(--muted); margin:0 0 24px; max-width:60ch; }
form { display:grid; gap:14px; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:20px; }
label { display:grid; gap:4px; font-weight:600; font-size:0.9rem; }
input, select, button { font:inherit; padding:10px 12px; border-radius:8px; border:1px solid var(--line); background:var(--bg); color:var(--ink); }
button { background:var(--accent); color:var(--bg); border:0; font-weight:600; cursor:pointer; }
button:disabled { opacity:0.6; cursor:wait; }
.row { display:grid; gap:14px; grid-template-columns:1fr 1fr; }
@media (max-width:560px) { .row { grid-template-columns:1fr; } }
.note { color:var(--muted); font-size:0.85rem; margin:0; }
#out { margin:20px 0 0; }
.verdict { border-left:4px solid var(--line); padding:12px 16px; background:var(--card); border-radius:0 8px 8px 0; }
.verdict.held { border-color:var(--held); } .verdict.failed { border-color:var(--failed); }
.verdict strong { display:block; font-size:1.1rem; }
.held strong { color:var(--held); } .failed strong { color:var(--failed); }
.err { color:var(--failed); }
dl { display:grid; grid-template-columns:max-content 1fr; gap:2px 12px; font-size:0.85rem; margin:10px 0 0; }
dt { color:var(--muted); } dd { margin:0; overflow-wrap:anywhere; font-family:ui-monospace, monospace; }
h2 { font-size:1.1rem; margin:40px 0 8px; }
.table { overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-size:0.85rem; }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; }
a { color:var(--accent); }
</style>
</head>
<body>
<main>
<h1>Field-day review</h1>
<p class="lede">You planned a field job around a forecast. Did the weather actually hold? This buys the recorded day for that place through Telegraph and tells you, with the receipt attached.</p>
<form id="f">
  <label>Field job
    <select name="operation">
      <option value="spray">Spraying (needs a dry day)</option>
      <option value="harvest">Harvest (needs a dry day)</option>
      <option value="frost">Frost-sensitive work (needs the low above 0 °C)</option>
    </select>
  </label>
  <div class="row">
    <label>Place <input name="location" required minlength="2" maxlength="80" placeholder="Lleida, Spain" autocomplete="off"></label>
    <label>Day <input name="date" type="date" required></label>
  </div>
  <button type="submit">Review the day ($0.01 test USDC)</button>
  <p class="note">Telegraph requests are public: the place and day appear in Telegraph's public feed. Don't enter a private address. Days must be at least two days ago and within the last year.</p>
</form>
<div id="out" aria-live="polite"></div>
<h2>Every call this app has paid for</h2>
<p class="note">Paid from the app's Telegraph escrow on Ethereum Sepolia. The miner is whichever one Telegraph's router picked. Delivery proofs appear about an hour after a call, when its epoch is recorded.</p>
<div class="table"><table><thead><tr><th>When (UTC)</th><th>Place, day</th><th>Outcome</th><th>Miner</th><th>Proof</th></tr></thead><tbody id="ledger"><tr><td colspan="5">Loading…</td></tr></tbody></table></div>
</main>
<script>
const NODE = "https://devnode.telegraphprotocol.com";
const BASE = location.pathname.replace(/\\/?$/, "/");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" })[c]);
const f = document.getElementById("f"), out = document.getElementById("out");
const d = new Date(Date.now() - 2 * 864e5).toISOString().slice(0, 10);
f.date.max = d; f.date.value = d;
f.date.min = new Date(Date.now() - 365 * 864e5).toISOString().slice(0, 10);
function proof(r) {
  const links = [];
  if (r.signal_hash) links.push('<a href="' + NODE + '/engine/v1/signal/' + esc(r.signal_hash) + '">answer</a>');
  const rc = r.receipt || {};
  if (rc.receipt_hash && rc.epoch_id != null) links.push('<a href="' + NODE + '/v1/blocks/' + esc(rc.epoch_id) + '/deliveries/' + esc(rc.receipt_hash) + '">delivery</a>');
  return links.join(" · ") || "—";
}
async function loadLedger() {
  try {
    const { calls } = await (await fetch(BASE + "api/ledger")).json();
    document.getElementById("ledger").innerHTML = calls.length ? calls.map((r) =>
      "<tr><td>" + esc(r.at.slice(0, 16).replace("T", " ")) + "</td><td>" + esc(r.location) + ", " + esc(r.date) +
      "</td><td>" + esc(r.kind === "served" ? "served" : r.kind === "paid_refusal" ? "refused, not charged" : "unknown, counted") +
      "</td><td>" + esc(r.miner_id ?? "—") + "</td><td>" + proof(r) + "</td></tr>").join("") : '<tr><td colspan="5">No calls yet.</td></tr>';
  } catch { document.getElementById("ledger").innerHTML = '<tr><td colspan="5">Ledger unavailable.</td></tr>'; }
}
f.addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = f.querySelector("button"); btn.disabled = true; out.innerHTML = "<p class=note>Buying the recorded day through Telegraph…</p>";
  try {
    const res = await fetch(BASE + "api/review", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operation: f.operation.value, location: f.location.value, date: f.date.value }) });
    const r = await res.json();
    if (!res.ok) { out.innerHTML = '<p class="err">' + esc(r.error) + "</p>"; return; }
    const v = r.verdict, cls = v.held === true ? "held" : v.held === false ? "failed" : "";
    const x = r.result || {};
    out.innerHTML = '<div class="verdict ' + cls + '"><strong>' + esc(v.text) + "</strong>" +
      "<dl><dt>Recorded</dt><dd>" + esc([x.condition, x.temp_max_c != null && "high " + x.temp_max_c + " °C", x.temp_min_c != null && "low " + x.temp_min_c + " °C", x.precipitation_mm != null && x.precipitation_mm + " mm"].filter(Boolean).join(", ")) +
      "</dd><dt>Answered by</dt><dd>miner " + esc(r.miner_id) + (r.miner_name ? " (" + esc(r.miner_name) + ")" : "") +
      "</dd><dt>Receipt</dt><dd>" + esc((r.receipt || {}).receipt_hash || "—") +
      "</dd><dt>Proof</dt><dd>" + proof(r) + "</dd>" + (r.reused ? "<dt>Note</dt><dd>Reused an earlier paid answer for this place and day; no new charge.</dd>" : "") + "</dl></div>";
    loadLedger();
  } catch { out.innerHTML = '<p class="err">The request failed. Try again.</p>'; }
  finally { btn.disabled = false; }
});
loadLedger();
</script>
</body>
</html>`;
