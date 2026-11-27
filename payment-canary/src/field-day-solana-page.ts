// The Field-day Settle page: book a job, watch it settle in three steps, audit any settlement from its transaction.
// An inline script inside a template string: backslashes must be doubled.
export const PAGE = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Field-day Settle</title>
<style>
:root { --bg:#f6f4ee; --ink:#1d2320; --muted:#5d6661; --line:#d9d5ca; --card:#fffdf8; --ok:#256b3a; --bad:#9a3412; --hold:#8a6d1a; --accent:#2f5d50; }
@media (prefers-color-scheme: dark) { :root { --bg:#131614; --ink:#e7e6e0; --muted:#9aa29d; --line:#2c312e; --card:#1a1e1b; --ok:#6fcf8d; --bad:#f29a6b; --hold:#e2c46b; --accent:#8cc5b2; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width:820px; margin:0 auto; padding:32px 16px 64px; }
h1 { font-size:1.75rem; margin:0 0 4px; letter-spacing:-0.01em; }
.lede { color:var(--muted); margin:0 0 20px; max-width:64ch; }
.rules { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin:0 0 24px; }
.rules div { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 12px; font-size:0.85rem; }
.rules b { display:block; font-size:0.95rem; }
@media (max-width:620px) { .rules { grid-template-columns:1fr; } }
form { display:grid; gap:14px; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:20px; }
label { display:grid; gap:4px; font-weight:600; font-size:0.9rem; }
input, select, button { font:inherit; padding:10px 12px; border-radius:8px; border:1px solid var(--line); background:var(--bg); color:var(--ink); min-width:0; }
button { background:var(--accent); color:var(--bg); border:0; font-weight:600; cursor:pointer; }
button:disabled { opacity:0.6; cursor:wait; }
.row { display:grid; gap:14px; grid-template-columns:1fr 1fr; }
@media (max-width:560px) { .row { grid-template-columns:1fr; } }
.note { color:var(--muted); font-size:0.85rem; margin:0; }
.steps { list-style:none; padding:0; margin:20px 0 0; display:grid; gap:10px; }
.steps li { background:var(--card); border:1px solid var(--line); border-left:4px solid var(--line); border-radius:0 10px 10px 0; padding:12px 16px; }
.steps li.done { border-left-color:var(--accent); }
.steps li.RELEASE { border-left-color:var(--ok); } .steps li.REFUND { border-left-color:var(--bad); } .steps li.HOLD { border-left-color:var(--hold); }
.steps b { display:block; }
.RELEASE b.d { color:var(--ok); } .REFUND b.d { color:var(--bad); } .HOLD b.d { color:var(--hold); }
.err { color:var(--bad); }
dl { display:grid; grid-template-columns:max-content 1fr; gap:2px 12px; font-size:0.85rem; margin:8px 0 0; }
dt { color:var(--muted); } dd { margin:0; overflow-wrap:anywhere; font-family:ui-monospace, monospace; }
h2 { font-size:1.1rem; margin:40px 0 8px; }
.table { overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-size:0.85rem; }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; }
.checks { list-style:none; padding:0; margin:12px 0 0; display:grid; gap:6px; font-size:0.9rem; }
.checks li { overflow-wrap:anywhere; } .checks .y { color:var(--ok); } .checks .n { color:var(--bad); } .checks .q { color:var(--hold); }
.inline { display:flex; gap:8px; } .inline input { flex:1; }
a { color:var(--accent); }
code { font-family:ui-monospace, monospace; font-size:0.85em; overflow-wrap:anywhere; }
</style>
</head>
<body>
<main>
<h1>Field-day Settle</h1>
<p class="lede">Pay a contractor for field work only if the weather window actually held. The fee waits in escrow on Solana; after the day, two independent Telegraph verifiers report what was recorded, and code (not a person, not a model) releases, refunds or holds the money. Every step is a Solana transaction carrying its evidence.</p>
<div class="rules">
  <div><b>Release</b>Both verifiers agree the window held: the fee goes to the contractor.</div>
  <div><b>Refund</b>Both agree it failed (rain of 1 mm or more; a low at or below 0 °C for frost work): the fee goes back to the buyer.</div>
  <div><b>Hold</b>They disagree, or one has no reading: the fee stays in escrow until a person resolves it, and the resolution is recorded on-chain with its reason.</div>
</div>
<p class="note" style="margin:-12px 0 20px">The ranked route picks the first verifier (today that is our own miner 64174, which reads the ERA5 archive). The cross-check is SecondSky's 77031, which reads NASA POWER (satellite and MERRA-2 data), so the two can genuinely disagree; Counterproof's 731004 is the fallback. Limits: Solana devnet only; the escrow is a custodial agent wallet run by this app, not an on-chain program; the demo buyer is the app's operator wallet.</p>
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
    <label>Day of the job <input name="date" type="date" required></label>
  </div>
  <div class="row">
    <label>Contractor's Solana address <input name="contractor" required autocomplete="off" spellcheck="false"></label>
    <label>Fee (devnet USDC) <input name="fee" required value="0.02" inputmode="decimal"></label>
  </div>
  <button type="submit">Book and settle</button>
  <p class="note">Solana devnet test USDC only. The day must be at least two days ago (the record is final by then) and within the last year; booking and settling then run back to back. Each job pays Telegraph $0.02 for the two verifications. Requests to Telegraph are public, so don't enter a private address as the place.</p>
</form>
<div id="out" aria-live="polite"></div>

<h2>Audit a settlement</h2>
<p class="note">Paste any Field-day transaction signature. The check reads the transaction from Solana devnet, recomputes the decision from the readings in its memo, checks where the money went, and fetches each reading's Telegraph record by its signal hash.</p>
<div class="inline" style="margin-top:10px"><input id="sig" placeholder="transaction signature" spellcheck="false" autocomplete="off"><button id="go" type="button">Audit</button></div>
<div id="audit" aria-live="polite"></div>

<h2>Jobs</h2>
<div class="table"><table><thead><tr><th>Job</th><th>Place and day</th><th>Readings</th><th>Outcome</th><th>Transactions</th></tr></thead><tbody id="jobs"><tr><td colspan="5">Loading…</td></tr></tbody></table></div>
<p class="note" id="info"></p>
</main>
<script>
const BASE = "/field-day-solana";
const OPS = { spray: "Spraying", harvest: "Harvest", frost: "Frost-sensitive work" };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const tx = (sig, label) => sig ? '<a href="https://explorer.solana.com/tx/' + encodeURIComponent(sig) + '?cluster=devnet" target="_blank" rel="noopener">' + esc(label || (sig.slice(0, 8) + "…")) + "</a>" : "";
const usdc = (units) => (Number(units) / 1e6).toFixed(2) + " USDC";
const reading = (op, r) => !r ? "none" : op === "frost" ? ("low " + r.temp_min_c + " °C") : ((r.precipitation_mm ?? "?") + " mm rain");
const auditLink = (sig) => sig ? ' · <a href="#" data-audit="' + esc(sig) + '">audit</a>' : "";

function steps(j) {
  const src = j.sources || [];
  const one = (s) => "<dt>" + esc(s.role === "ranked" ? "Ranked route" : "Cross-check") + "</dt><dd>miner " + esc(s.miner_id) + ": " + esc(reading(j.operation, s.result)) + " · paid " + tx(s.payment_tx) + " · signal " + esc(s.signal_hash.slice(0, 12)) + "…</dd>";
  const settled = j.decision ? '<li class="' + esc(j.decision) + '"><b class="d">3. ' + esc(j.decision) + "</b>" + esc(j.text) + "<dl><dt>Transaction</dt><dd>" + tx(j.settle_tx, j.settle_tx) + auditLink(j.settle_tx) + "</dd></dl></li>"
    : '<li><b>3. Settle</b>' + (j.error ? '<span class="err">' + esc(j.error) + "</span> The fee stays in escrow; sending the same job again resumes it without paying twice." : "Waiting…") + "</li>";
  return '<ol class="steps">' +
    '<li class="' + (j.deposit_tx ? "done" : "") + '"><b>1. Booked</b>' + esc(OPS[j.operation]) + " at " + esc(j.location) + " on " + esc(j.date) + ", " + usdc(j.fee_units) + " into escrow." +
      (j.deposit_tx ? "<dl><dt>Deposit</dt><dd>" + tx(j.deposit_tx, j.deposit_tx) + auditLink(j.deposit_tx) + "</dd></dl>" : "") + "</li>" +
    '<li class="' + (src.length === 2 ? "done" : "") + '"><b>2. Verified twice through Telegraph</b>Each reading bought with an x402 payment on Solana.<dl>' + src.map(one).join("") + "</dl></li>" +
    settled + (j.resolution ? '<li class="' + (j.resolution.to === "contractor" ? "RELEASE" : "REFUND") + '"><b class="d">4. Resolved by hand: ' + (j.resolution.to === "contractor" ? "paid to the contractor" : "refunded to the buyer") + "</b>" + esc(j.resolution.why) + "<dl><dt>Transaction</dt><dd>" + tx(j.resolution.tx, j.resolution.tx) + auditLink(j.resolution.tx) + "</dd></dl></li>" : "") + "</ol>";
}

async function load() {
  try {
    const d = await (await fetch(BASE + "/api/jobs")).json();
    JOBS = d.jobs;
    document.getElementById("info").innerHTML = "Escrow agent <code>" + esc(d.escrow_agent) + "</code> · operator (pays Telegraph and fees; the demo buyer) <code>" + esc(d.operator) + "</code> · cross-check verifiers " + esc((d.cross_check || []).join(", ")) + " · <a href=\\"" + BASE + "/api/ledger\\">full ledger (JSON)</a>";
    const rows = d.jobs.map((j) => "<tr><td><code>" + esc(j.id) + "</code><br>" + esc(OPS[j.operation]) + ", " + usdc(j.fee_units) + "</td><td>" + esc(j.location) + "<br>" + esc(j.date) + "</td><td>" +
      (j.sources || []).map((s) => esc(s.miner_id) + ": " + esc(reading(j.operation, s.result))).join("<br>") + "</td><td>" + esc(j.decision || j.state) + "</td><td>" +
      [j.deposit_tx && tx(j.deposit_tx, "deposit"), j.settle_tx && tx(j.settle_tx, "settlement") + auditLink(j.settle_tx), j.resolution && tx(j.resolution.tx, "resolution") + auditLink(j.resolution.tx)].filter(Boolean).join("<br>") + "</td></tr>");
    document.getElementById("jobs").innerHTML = rows.join("") || '<tr><td colspan="5">No jobs yet.</td></tr>';
  } catch (e) { document.getElementById("jobs").innerHTML = '<tr><td colspan="5" class="err">Could not load the jobs.</td></tr>'; }
}

async function audit(sig) {
  const out = document.getElementById("audit");
  document.getElementById("sig").value = sig;
  out.innerHTML = '<p class="note">Reading devnet and Telegraph…</p>';
  try {
    const r = await fetch(BASE + "/api/audit?tx=" + encodeURIComponent(sig));
    const d = await r.json();
    if (!r.ok) { out.innerHTML = '<p class="err">' + esc(d.error) + "</p>"; return; }
    const m = d.memo;
    out.innerHTML = '<p class="note" style="margin-top:12px">' + tx(d.signature, "Transaction") + ": job <code>" + esc(m.job) + "</code>, " + esc(m.step) + ", " + esc(OPS[m.op]) + " at " + esc(m.loc) + " on " + esc(m.date) + ". <button type=\"button\" data-job=\"" + esc(m.job) + "\" style=\"padding:2px 8px;font-size:0.8rem\">show the job</button></p>" +
      '<ul class="checks">' + d.checks.map((c) => '<li><span class="' + (c.ok === true ? "y" : c.ok === false ? "n" : "q") + '">' + (c.ok === true ? "✓" : c.ok === false ? "✗" : "?") + "</span> <b>" + esc(c.name) + "</b>: " + esc(c.detail) + "</li>").join("") + "</ul>";
  } catch (e) { out.innerHTML = '<p class="err">The audit failed to load.</p>'; }
}

document.getElementById("go").addEventListener("click", () => { const s = document.getElementById("sig").value.trim(); if (s) audit(s); });
let JOBS = [];
document.addEventListener("click", (e) => { const b = e.target.closest("[data-job]"); if (b) { const j = JOBS.find((x) => x.id === b.dataset.job); if (j) { document.getElementById("out").innerHTML = steps(j); document.getElementById("out").scrollIntoView({ behavior: "smooth" }); } } });
document.addEventListener("click", (e) => { const a = e.target.closest("[data-audit]"); if (a) { e.preventDefault(); audit(a.dataset.audit); document.getElementById("audit").scrollIntoView({ behavior: "smooth" }); } });

const f = document.getElementById("f");
const d = new Date(Date.now() - 3 * 86400000);
f.date.value = d.toISOString().slice(0, 10);
f.date.max = new Date(Date.now() - 2 * 86400000).toISOString().slice(0, 10);
f.addEventListener("submit", async (e) => {
  e.preventDefault();
  const b = f.querySelector("button"), out = document.getElementById("out");
  b.disabled = true;
  out.innerHTML = '<p class="note">Booking, verifying twice and settling on Solana devnet. This takes up to a minute…</p>';
  try {
    const r = await fetch(BASE + "/api/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(f))) });
    const j = await r.json();
    out.innerHTML = j.operation ? steps(j) : '<p class="err">' + esc(j.error) + "</p>";
  } catch (err) { out.innerHTML = '<p class="err">The request failed. Check the jobs list before trying again.</p>'; }
  b.disabled = false;
  load();
});
load();
</script>
</body>
</html>`;
