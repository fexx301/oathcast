// The Field-day Settle page. One diagram per job: both verifiers' readings on the rule's scale, and where the fee is
// now (buyer, escrow or contractor), with every transaction linked and auditable. ?tx=<sig> opens an audit; ?job=<id>
// opens a job. An inline script inside a template string: backslashes doubled, no template literals in the script.
export const PAGE = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Field-day Settle</title>
<style>
/* Hallmark · genre: modern-minimal · macrostructure: Map / Diagram · tone: utilitarian instrument
 * theme: custom (vibe: "field ledger, instrument panel, plain daylight") · paper oklch(97% 0.008 85) · accent oklch(45% 0.09 250)
 * display + body: system-ui (CSP allows no external fonts) · numbers: ui-monospace · nav: N1a (one destination) · footer: Ft1 line
 */
:root {
  --color-paper: oklch(97% 0.008 85); --color-paper-2: oklch(99.5% 0.004 85); --color-ink: oklch(24% 0.015 85);
  --color-ink-2: oklch(46% 0.015 85); --color-rule: oklch(88% 0.012 85); --color-accent: oklch(45% 0.09 250);
  --color-release: oklch(50% 0.12 150); --color-refund: oklch(52% 0.15 35); --color-hold: oklch(55% 0.12 75);
  --color-release-bg: oklch(94% 0.04 150); --color-refund-bg: oklch(94% 0.04 35); --color-hold-bg: oklch(95% 0.05 85);
  --color-focus: oklch(55% 0.17 250); --color-on-accent: oklch(98% 0.005 85);
  --font-body: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; --font-mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --space-3xs: 0.25rem; --space-2xs: 0.5rem; --space-xs: 0.75rem; --space-sm: 1rem; --space-md: 1.5rem; --space-lg: 2rem; --space-xl: 3rem; --space-2xl: 4.5rem;
  --text-xs: 0.75rem; --text-sm: 0.875rem; --text-md: 1rem; --text-lg: 1.25rem; --text-xl: 1.75rem; --text-2xl: clamp(1.9rem, 4vw, 2.6rem);
  --radius: 10px; --ease-out: cubic-bezier(0.16, 1, 0.3, 1); --dur-short: 200ms;
}
@media (prefers-color-scheme: dark) { :root {
  --color-paper: oklch(19% 0.01 85); --color-paper-2: oklch(23% 0.01 85); --color-ink: oklch(93% 0.01 85);
  --color-ink-2: oklch(72% 0.012 85); --color-rule: oklch(33% 0.012 85); --color-accent: oklch(76% 0.09 250);
  --color-release: oklch(76% 0.14 150); --color-refund: oklch(74% 0.13 35); --color-hold: oklch(82% 0.13 85);
  --color-release-bg: oklch(30% 0.05 150); --color-refund-bg: oklch(30% 0.05 35); --color-hold-bg: oklch(31% 0.05 85);
  --color-focus: oklch(78% 0.13 250); --color-on-accent: oklch(18% 0.01 85);
} }
* { box-sizing: border-box; }
html, body { overflow-x: clip; }
body { margin: 0; background: var(--color-paper); color: var(--color-ink); font: var(--text-md)/1.55 var(--font-body); }
main { max-width: 1040px; margin: 0 auto; padding: var(--space-lg) var(--space-sm) var(--space-2xl); }
a { color: var(--color-accent); text-underline-offset: 2px; }
:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }
.mono, code { font-family: var(--font-mono); font-size: 0.9em; overflow-wrap: anywhere; }
.muted { color: var(--color-ink-2); }
header.top { display: flex; justify-content: space-between; align-items: baseline; gap: var(--space-sm); flex-wrap: wrap; border-bottom: 1px solid var(--color-rule); padding-bottom: var(--space-xs); }
.mark { font-weight: 700; letter-spacing: -0.01em; }
.net { font-size: var(--text-sm); color: var(--color-ink-2); }
h1 { font-size: var(--text-2xl); line-height: 1.1; letter-spacing: -0.02em; margin: var(--space-lg) 0 var(--space-xs); max-width: 22ch; overflow-wrap: anywhere; }
.lede { color: var(--color-ink-2); max-width: 62ch; margin: 0 0 var(--space-lg); }
h2 { font-size: var(--text-lg); margin: var(--space-2xl) 0 var(--space-xs); letter-spacing: -0.01em; }

.chips { display: flex; gap: var(--space-2xs); flex-wrap: wrap; margin-bottom: var(--space-sm); }
.chip { font: inherit; font-size: var(--text-sm); border: 1px solid var(--color-rule); background: var(--color-paper-2); color: var(--color-ink); border-radius: 999px; padding: var(--space-3xs) var(--space-xs); cursor: pointer; white-space: nowrap; display: inline-flex; gap: var(--space-2xs); align-items: center; transition: border-color var(--dur-short) var(--ease-out); }
.chip:hover { border-color: var(--color-ink-2); }
.chip[aria-pressed="true"] { border-color: var(--color-ink); box-shadow: inset 0 0 0 1px var(--color-ink); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-ink-2); flex: none; }
.dot.RELEASE { background: var(--color-release); } .dot.REFUND { background: var(--color-refund); } .dot.HOLD { background: var(--color-hold); }

.board { background: var(--color-paper-2); border: 1px solid var(--color-rule); border-radius: var(--radius); padding: var(--space-md); }
.board-head { display: flex; justify-content: space-between; gap: var(--space-sm); flex-wrap: wrap; align-items: start; }
.job-title { font-size: var(--text-xl); font-weight: 650; letter-spacing: -0.015em; margin: 0; line-height: 1.2; }
.job-sub { color: var(--color-ink-2); font-size: var(--text-sm); margin: var(--space-3xs) 0 0; }
.verdict { font-weight: 700; font-size: var(--text-sm); letter-spacing: 0.02em; padding: var(--space-3xs) var(--space-xs); border-radius: 6px; white-space: nowrap; }
.verdict.RELEASE { color: var(--color-release); background: var(--color-release-bg); } .verdict.REFUND { color: var(--color-refund); background: var(--color-refund-bg); } .verdict.HOLD, .verdict.booked { color: var(--color-hold); background: var(--color-hold-bg); }
.why { margin: var(--space-sm) 0 0; max-width: 70ch; }
.gauge { margin: var(--space-md) 0 var(--space-2xs); }
.gauge svg { width: 100%; height: auto; display: block; overflow: visible; }
.gauge text { font-family: var(--font-mono); font-size: 11px; fill: var(--color-ink-2); }
.gauge .lbl { font-family: var(--font-body); font-size: 13px; fill: var(--color-ink); font-weight: 600; }
.gauge .thr { stroke: var(--color-ink); stroke-width: 1.5; stroke-dasharray: 3 3; }
.gauge .axis { stroke: var(--color-rule); stroke-width: 1; }
.gauge .zone-ok { fill: var(--color-release-bg); } .gauge .zone-bad { fill: var(--color-refund-bg); }
.gauge .pin { stroke: var(--color-ink); stroke-width: 1.5; } .gauge .pin-dot { fill: var(--color-ink); }
.gauge-note { font-size: var(--text-xs); color: var(--color-ink-2); margin: 0; }
/* The SVG scales with its width; on phones its text is set in larger viewBox units so it renders at about 10-11 px. */
@media (max-width: 560px) { .gauge text { font-size: 20px; } .gauge .lbl { font-size: 24px; } }

.flow { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) auto minmax(0, 1fr); gap: var(--space-2xs); align-items: stretch; margin-top: var(--space-md); }
.node { border: 1px solid var(--color-rule); border-radius: var(--radius); padding: var(--space-xs) var(--space-sm); background: var(--color-paper); min-width: 0; }
.node b { display: block; font-size: var(--text-sm); }
.node .addr { font-family: var(--font-mono); font-size: var(--text-xs); color: var(--color-ink-2); overflow-wrap: anywhere; }
.node .here { display: none; margin-top: var(--space-2xs); font-weight: 700; font-size: var(--text-sm); }
.node.has { border-color: currentColor; border-width: 2px; } .node.has .here { display: block; }
.node.has.RELEASE { color: var(--color-release); } .node.has.REFUND { color: var(--color-refund); } .node.has.HOLD, .node.has.booked { color: var(--color-hold); }
.node.has b { color: var(--color-ink); }
.arrow { display: grid; place-items: center; align-content: center; font-size: var(--text-xs); color: var(--color-ink-2); text-align: center; min-width: 72px; }
.arrow span { display: block; font-size: var(--text-lg); line-height: 1; color: var(--color-ink); }
.arrow.off span { color: var(--color-rule); }
@media (max-width: 640px) { .flow { grid-template-columns: minmax(0, 1fr); } .arrow { min-width: 0; } .arrow span { transform: rotate(90deg); } }

.facts { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: var(--space-3xs) var(--space-sm); font-size: var(--text-sm); margin: var(--space-md) 0 0; border-top: 1px solid var(--color-rule); padding-top: var(--space-sm); }
.facts dt { color: var(--color-ink-2); } .facts dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
@media (max-width: 560px) { .facts { grid-template-columns: minmax(0, 1fr); } .facts dd { margin-bottom: var(--space-2xs); } }

.two { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr); gap: var(--space-lg); align-items: start; }
@media (max-width: 800px) { .two { grid-template-columns: minmax(0, 1fr); } }
form { display: grid; gap: var(--space-sm); }
label { display: grid; gap: var(--space-3xs); font-weight: 600; font-size: var(--text-sm); }
input, select { font: inherit; padding: var(--space-2xs) var(--space-xs); border-radius: 8px; border: 1px solid var(--color-rule); background: var(--color-paper-2); color: var(--color-ink); min-width: 0; width: 100%; }
input:hover, select:hover { border-color: var(--color-ink-2); }
input[aria-invalid="true"] { border-color: var(--color-refund); }
.row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: var(--space-sm); }
@media (max-width: 560px) { .row { grid-template-columns: minmax(0, 1fr); } }
button.primary, button.secondary { font: inherit; font-weight: 600; border-radius: 8px; padding: var(--space-2xs) var(--space-sm); cursor: pointer; white-space: nowrap; transition: opacity var(--dur-short) var(--ease-out); }
button.primary { background: var(--color-accent); color: var(--color-on-accent); border: 1px solid var(--color-accent); }
button.secondary { background: transparent; color: var(--color-ink); border: 1px solid var(--color-rule); }
button.primary:hover, button.secondary:hover { opacity: 0.88; } button:active { transform: translateY(1px); }
button:disabled { opacity: 0.55; cursor: progress; }
.note { font-size: var(--text-sm); color: var(--color-ink-2); margin: 0; }
.status { font-size: var(--text-sm); margin: 0; min-height: 1.5em; }
.status.err { color: var(--color-refund); }
.rule-table { width: 100%; border-collapse: collapse; font-size: var(--text-sm); }
.rule-table th, .rule-table td { text-align: left; padding: var(--space-2xs) var(--space-2xs) var(--space-2xs) 0; border-bottom: 1px solid var(--color-rule); vertical-align: top; }
.rule-table th { color: var(--color-ink-2); font-weight: 600; }
.t-release { color: var(--color-release); } .t-refund { color: var(--color-refund); } .t-hold { color: var(--color-hold); }
.audit-bar { display: flex; gap: var(--space-2xs); margin-top: var(--space-xs); }
.audit-bar input { flex: 1; font-family: var(--font-mono); font-size: var(--text-sm); }
.checks { list-style: none; padding: 0; margin: var(--space-sm) 0 0; display: grid; gap: var(--space-2xs); font-size: var(--text-sm); }
.checks li { display: grid; grid-template-columns: 1.4em minmax(0, 1fr); gap: var(--space-2xs); overflow-wrap: anywhere; }
.ok { color: var(--color-release); font-weight: 700; } .bad { color: var(--color-refund); font-weight: 700; } .unk { color: var(--color-hold); font-weight: 700; }
.audit-head { display: flex; justify-content: space-between; gap: var(--space-sm); flex-wrap: wrap; align-items: center; margin-top: var(--space-sm); font-size: var(--text-sm); }

.table { overflow-x: auto; }
table.jobs { width: 100%; border-collapse: collapse; font-size: var(--text-sm); }
table.jobs th, table.jobs td { text-align: left; padding: var(--space-2xs); border-bottom: 1px solid var(--color-rule); vertical-align: top; }
table.jobs th { color: var(--color-ink-2); font-weight: 600; }
table.jobs tr[data-id] { cursor: pointer; } table.jobs tr[data-id]:hover td { background: var(--color-paper-2); }
.inline-dot { display: inline-block; margin-right: var(--space-2xs); }
footer { margin-top: var(--space-2xl); border-top: 1px solid var(--color-rule); padding-top: var(--space-sm); font-size: var(--text-sm); color: var(--color-ink-2); }
footer p { margin: 0 0 var(--space-2xs); max-width: 80ch; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
</head>
<body>
<main>
<header class="top"><span class="mark">Field-day Settle</span><span class="net">Solana devnet · Telegraph verifiers · test USDC</span></header>

<h1>Pay for field work only if the weather held.</h1>
<p class="lede">The fee waits in escrow. After the day, two independent weather verifiers on Telegraph report what was recorded, each paid on Solana. Code releases the fee, refunds it, or holds it when they disagree, and every step is a transaction anyone can audit.</p>

<div class="chips" id="chips" role="group" aria-label="Jobs"></div>
<section class="board" id="board" aria-live="polite"><p class="muted">Loading jobs…</p></section>

<div class="two">
  <section>
    <h2 id="run">Run a job</h2>
    <form id="f" novalidate>
      <label>Field job
        <select name="operation">
          <option value="spray">Spraying: needs a dry day</option>
          <option value="harvest">Harvest: needs a dry day</option>
          <option value="frost">Frost-sensitive work: needs the low above 0 °C</option>
        </select>
      </label>
      <div class="row">
        <label>Place <input name="location" required minlength="2" maxlength="80" placeholder="Lleida, Spain" autocomplete="off"></label>
        <label>Day of the job <input name="date" type="date" required></label>
      </div>
      <div class="row">
        <label>Contractor's Solana address <input name="contractor" required autocomplete="off" spellcheck="false" class="mono"></label>
        <label>Fee, devnet USDC <input name="fee" required value="0.02" inputmode="decimal"></label>
      </div>
      <div><button class="primary" type="submit">Book and settle</button></div>
      <p class="status" id="status" role="status"></p>
      <p class="note">Days at least two days ago (the record is final by then) and within the last year, so booking and settling run back to back. Each job pays Telegraph $0.02 for two readings. Telegraph requests are public: use a town, not a private address. Sending the same job again shows it rather than paying twice.</p>
    </form>
  </section>
  <section>
    <h2>The rule</h2>
    <table class="rule-table">
      <thead><tr><th>Ranked route</th><th>Cross-check</th><th>Fee</th></tr></thead>
      <tbody>
        <tr><td>held</td><td>held</td><td><b class="t-release">Release</b> to the contractor</td></tr>
        <tr><td>failed</td><td>failed</td><td><b class="t-refund">Refund</b> to the buyer</td></tr>
        <tr><td colspan="2">disagree, or no reading</td><td><b class="t-hold">Hold</b> in escrow until a person resolves it, on the record</td></tr>
      </tbody>
    </table>
    <p class="note" style="margin-top:var(--space-xs)">A rain day is 1 mm or more; frost is a low at or below 0 °C. The ranked route lets Telegraph pick the verifier. The cross-check is another operator's miner, chosen for different data: SecondSky's 77031 reads NASA POWER (satellite and MERRA-2), while the ranked route's usual pick, our 64174, reads the ERA5 archive.</p>
  </section>
</div>

<h2 id="audit">Audit a transaction</h2>
<p class="note">Any Field-day transaction, on its own: was it confirmed, does the decision follow from the readings in its memo, did the money go where the decision says, and does each reading match its Telegraph record?</p>
<div class="audit-bar"><input id="sig" placeholder="transaction signature" spellcheck="false" autocomplete="off" aria-label="Transaction signature"><button class="secondary" id="go" type="button">Audit</button></div>
<div id="auditOut" aria-live="polite"></div>

<h2>All jobs</h2>
<div class="table"><table class="jobs"><thead><tr><th>Job</th><th>Place and day</th><th>Readings</th><th>Outcome</th></tr></thead><tbody id="rows"><tr><td colspan="4" class="muted">Loading…</td></tr></tbody></table></div>

<footer>
  <p id="wallets"></p>
  <p>Limits: Solana devnet and test USDC only. The escrow is a wallet this app holds, not an on-chain program; the demo buyer is the app's own operator wallet. The verifiers read gridded weather data, not a rain gauge at the field. OathCast runs miner 64174. <a href="https://github.com/fexx301/oathcast/blob/main/docs/field-day-solana.md">How it works</a> · <a href="/field-day-solana/api/ledger">Ledger (JSON)</a></p>
</footer>
</main>
<script>
var BASE = "/field-day-solana";
var OPS = { spray: "Spraying", harvest: "Harvest", frost: "Frost-sensitive work" };
var WORD = { RELEASE: "Released", REFUND: "Refunded", HOLD: "Held in escrow" };
var JOBS = [], INFO = {}, CURRENT = null;

function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
function usdc(u) { return (Number(u) / 1e6).toFixed(2) + " USDC"; }
function short(s) { return s ? s.slice(0, 6) + "…" + s.slice(-4) : ""; }
function txa(sig, label) { return sig ? '<a href="https://explorer.solana.com/tx/' + encodeURIComponent(sig) + '?cluster=devnet" target="_blank" rel="noopener">' + esc(label || short(sig)) + "</a>" : ""; }
function auditA(sig) { return sig ? '<a href="?tx=' + encodeURIComponent(sig) + '" data-audit="' + esc(sig) + '">audit</a>' : ""; }
function frost(j) { return j.operation === "frost"; }
function val(j, s) { var v = s && s.result ? s.result[frost(j) ? "temp_min_c" : "precipitation_mm"] : null; v = typeof v === "string" ? Number(v) : v; return typeof v === "number" && isFinite(v) ? v : null; }
function unit(j) { return frost(j) ? " °C low" : " mm"; }
function outcome(j) { return j.decision || (j.deposit_tx ? "booked" : "booking"); }
function srcName(s) { return { "64174": "OathCast 64174, ERA5", "731004": "Counterproof 731004, ERA5", "77031": "SecondSky 77031, NASA POWER" }[s.miner_id] || ("miner " + s.miner_id); }

// Both readings on the rule's scale: rain on a square-root scale from 0, frost on a linear °C scale.
function gauge(j) {
  var src = j.sources || [], W = 720, H = 150, L = 16, R = W - 16, Y = 76;
  var vals = src.map(function (s) { return val(j, s); }).filter(function (v) { return v !== null; });
  var lo, hi, x, thr = frost(j) ? 0 : 1, ticks;
  if (frost(j)) {
    lo = Math.min(-5, Math.floor(Math.min.apply(null, vals.concat([0])) - 2)); hi = Math.max(10, Math.ceil(Math.max.apply(null, vals.concat([0])) + 2));
    x = function (v) { return L + (v - lo) / (hi - lo) * (R - L); };
    ticks = [lo, 0, hi];
  } else {
    hi = Math.max(5, Math.ceil(Math.max.apply(null, vals.concat([0])) * 1.25)); lo = 0;
    x = function (v) { return L + Math.sqrt(Math.max(0, v) / hi) * (R - L); };
    ticks = [0, 1, 5, 10, 20, 50].filter(function (t) { return t <= hi; });
  }
  var tx = x(thr), okLeft = !frost(j);
  var out = '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(src.map(function (s) { return s.miner_id + ": " + val(j, s) + unit(j); }).join(", ") + "; threshold " + thr + unit(j)) + '">';
  out += '<rect class="' + (okLeft ? "zone-ok" : "zone-bad") + '" x="' + L + '" y="' + (Y - 14) + '" width="' + (tx - L) + '" height="28" rx="4"/>';
  out += '<rect class="' + (okLeft ? "zone-bad" : "zone-ok") + '" x="' + tx + '" y="' + (Y - 14) + '" width="' + (R - tx) + '" height="28" rx="4"/>';
  out += '<line class="axis" x1="' + L + '" x2="' + R + '" y1="' + Y + '" y2="' + Y + '"/>';
  out += '<text x="' + (L + 6) + '" y="' + (Y + 4) + '">' + (okLeft ? "window held" : "frost") + "</text>";
  out += '<text x="' + (R - 6) + '" y="' + (Y + 4) + '" text-anchor="end">' + (okLeft ? "rain day" : "window held") + "</text>";
  out += '<line class="thr" x1="' + tx + '" x2="' + tx + '" y1="' + (Y - 30) + '" y2="' + (Y + 30) + '"/>';
  ticks.forEach(function (t) { out += '<text x="' + x(t) + '" y="' + (H - 4) + '" text-anchor="middle"' + (t === thr ? ' style="fill:var(--color-ink);font-weight:700"' : "") + ">" + t + (t === thr ? (frost(j) ? " °C line" : " mm line") : frost(j) ? "°" : "") + "</text>"; });
  src.forEach(function (s, i) {
    var v = val(j, s); if (v === null) return;
    var px = x(v), up = i === 0, y2 = up ? Y - 38 : Y + 38, anchor = px > W * 0.7 ? "end" : px < W * 0.3 ? "start" : "middle";
    out += '<line class="pin" x1="' + px + '" x2="' + px + '" y1="' + Y + '" y2="' + y2 + '"/><circle class="pin-dot" cx="' + px + '" cy="' + Y + '" r="5"/>';
    out += '<text class="lbl" x="' + px + '" y="' + (up ? y2 - 6 : y2 + 14) + '" text-anchor="' + anchor + '">' + esc((s.role === "ranked" ? "Ranked " : "Cross-check ") + s.miner_id + ": " + v + (frost(j) ? " °C" : " mm")) + "</text>";
  });
  return out + "</svg>";
}

// One plain sentence from the readings themselves.
function story(j) {
  var src = j.sources || [], a = src[0], b = src[1];
  if (!a || !b) return j.text || "";
  var bad = function (v) { return frost(j) ? v <= 0 : v >= 1; };
  var say = function (s) { var v = val(j, s); return v === null ? s.miner_id + " returned no reading" : s.miner_id + " recorded " + (frost(j) ? "a low of " + v + " °C" : v + " mm") + (bad(v) ? (frost(j) ? ", frost" : ", a rain day") : (frost(j) ? ", above freezing" : ", dry")); };
  if (j.decision === "HOLD") return "The verifiers disagree: the ranked route's " + say(a) + "; the cross-check " + say(b) + ". The fee stays in escrow until a person resolves it.";
  var both = frost(j) ? "a low of " + val(j, a) + " and " + val(j, b) + " °C" : val(j, a) + " and " + val(j, b) + " mm";
  if (j.decision === "RELEASE") return "Both verifiers agree the window held (" + both + "), so the fee went to the contractor.";
  if (j.decision === "REFUND") return "Both verifiers agree the window failed (" + both + "), so the fee went back to the buyer.";
  return j.text || "";
}

function where(j) {
  if (j.resolution) return j.resolution.to === "contractor" ? "contractor" : "buyer";
  return j.decision === "RELEASE" ? "contractor" : j.decision === "REFUND" ? "buyer" : j.deposit_tx ? "escrow" : "buyer";
}

function board(j) {
  var o = outcome(j), at = where(j), src = j.sources || [];
  var tone = j.resolution ? (j.resolution.to === "contractor" ? "RELEASE" : "REFUND") : o;
  var label = j.resolution ? "Resolved: " + (j.resolution.to === "contractor" ? "paid" : "refunded") : (WORD[o] || "Booked");
  var node = function (key, title, addr) {
    return '<div class="node ' + (at === key ? "has " + tone : "") + '"><b>' + title + '</b><span class="addr">' + esc(short(addr)) + '</span><span class="here">' + usdc(j.fee_units) + " is here</span></div>";
  };
  var settleTx = j.resolution ? j.resolution.tx : j.settle_tx;
  var outLabel = j.resolution ? "resolved" : j.decision === "HOLD" ? "held" : j.decision ? j.decision.toLowerCase() : "pending";
  var h = '<div class="board-head"><div><p class="job-title">' + esc(OPS[j.operation]) + ", " + esc(j.location) + ", " + esc(j.date) + '</p><p class="job-sub">Job <span class="mono">' + esc(j.id) + "</span> · fee " + usdc(j.fee_units) + '</p></div><span class="verdict ' + esc(tone) + '">' + esc(label) + "</span></div>";
  if (j.decision) h += '<p class="why">' + esc(story(j)) + (j.resolution ? " Resolved by hand: " + esc(j.resolution.why) : "") + "</p>";
  if (j.error) h += '<p class="why status err">' + esc(j.error) + "</p>";
  if (src.length) h += '<div class="gauge">' + gauge(j) + '</div><p class="gauge-note">' + (frost(j) ? "Daily low, °C." : "Recorded rain on a square-root scale, so the 1 mm line stays readable.") + " Ranked route above the line, cross-check below.</p>";
  h += '<div class="flow">' + node("buyer", "Buyer", INFO.operator) +
    '<div class="arrow ' + (j.deposit_tx ? "" : "off") + '"><span>→</span>' + (j.deposit_tx ? txa(j.deposit_tx, "deposit") : "deposit") + "</div>" +
    node("escrow", "Escrow agent", INFO.escrow_agent) +
    '<div class="arrow ' + (settleTx && outLabel !== "held" ? "" : "off") + '"><span>→</span>' + (settleTx ? txa(settleTx, outLabel) : "settle") + "</div>" +
    node("contractor", "Contractor", j.contractor) + "</div>";
  h += '<dl class="facts">';
  src.forEach(function (s) { h += "<dt>" + (s.role === "ranked" ? "Ranked route" : "Cross-check") + "</dt><dd>" + esc(srcName(s)) + ": " + esc(val(j, s)) + esc(unit(j)) + " · paid " + txa(s.payment_tx) + ' · signal <span class="mono">' + esc(short(s.signal_hash)) + "</span></dd>"; });
  if (j.deposit_tx) h += "<dt>Deposit</dt><dd>" + txa(j.deposit_tx, short(j.deposit_tx)) + " · " + auditA(j.deposit_tx) + "</dd>";
  if (j.settle_tx) h += "<dt>Settlement</dt><dd>" + txa(j.settle_tx, short(j.settle_tx)) + " · " + auditA(j.settle_tx) + "</dd>";
  if (j.resolution) h += "<dt>Resolution</dt><dd>" + txa(j.resolution.tx, short(j.resolution.tx)) + " · " + auditA(j.resolution.tx) + "</dd>";
  return h + "</dl>";
}

function select(id, push) {
  var j = JOBS.find(function (x) { return x.id === id; }); if (!j) return;
  CURRENT = id;
  document.getElementById("board").innerHTML = board(j);
  Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (c) { c.setAttribute("aria-pressed", c.dataset.id === id ? "true" : "false"); });
  if (push) history.replaceState(null, "", "?job=" + encodeURIComponent(id));
}

function render() {
  document.getElementById("chips").innerHTML = JOBS.map(function (j) {
    return '<button class="chip" type="button" data-id="' + esc(j.id) + '" aria-pressed="false"><span class="dot ' + esc(outcome(j)) + '"></span>' + esc(j.location.split(",")[0]) + " " + esc(j.date.slice(5)) + " · " + esc(j.resolution ? "Resolved" : WORD[j.decision] || "Booked") + "</button>";
  }).join("");
  document.getElementById("rows").innerHTML = JOBS.map(function (j) {
    return '<tr data-id="' + esc(j.id) + '"><td class="mono">' + esc(j.id) + "<br>" + esc(OPS[j.operation]) + "</td><td>" + esc(j.location) + "<br>" + esc(j.date) + "</td><td>" +
      (j.sources || []).map(function (s) { return esc(s.miner_id) + ": " + esc(val(j, s)) + esc(unit(j)); }).join("<br>") + '</td><td><span class="dot inline-dot ' + esc(outcome(j)) + '"></span>' + esc(j.resolution ? "Resolved" : WORD[j.decision] || outcome(j)) + "</td></tr>";
  }).join("") || '<tr><td colspan="4" class="muted">No jobs yet.</td></tr>';
  document.getElementById("wallets").innerHTML = 'Escrow agent <span class="mono">' + esc(INFO.escrow_agent) + '</span> · operator and demo buyer <span class="mono">' + esc(INFO.operator) + "</span>";
}

async function load(pick) {
  try {
    var d = await (await fetch(BASE + "/api/jobs")).json();
    INFO = d; JOBS = d.jobs || [];
    render();
    var q = new URLSearchParams(location.search).get("job");
    var first = pick || (JOBS.find(function (x) { return x.id === q; }) || {}).id || (JOBS.find(function (x) { return x.decision === "HOLD" && !x.resolution; }) || JOBS[0] || {}).id;
    if (first) select(first, false); else document.getElementById("board").innerHTML = '<p class="muted">No jobs yet. Run one below.</p>';
  } catch (e) { document.getElementById("board").innerHTML = '<p class="status err">Could not load the jobs.</p>'; }
}

async function audit(sig) {
  var out = document.getElementById("auditOut");
  document.getElementById("sig").value = sig;
  history.replaceState(null, "", "?tx=" + encodeURIComponent(sig));
  out.innerHTML = '<p class="status">Reading Solana devnet and Telegraph…</p>';
  try {
    var r = await fetch(BASE + "/api/audit?tx=" + encodeURIComponent(sig)), d = await r.json();
    if (!r.ok) { out.innerHTML = '<p class="status err">' + esc(d.error) + "</p>"; return; }
    var m = d.memo, pass = d.checks.every(function (c) { return c.ok === true; });
    out.innerHTML = '<div class="audit-head"><span><b class="' + (pass ? "ok" : "bad") + '">' + (pass ? "Every check passes" : "A check did not pass") + "</b> · " + esc(m.step) + " · job " +
      '<a href="?job=' + encodeURIComponent(m.job) + '" data-job="' + esc(m.job) + '">' + esc(m.job) + "</a> · " + txa(d.signature, "on the explorer") + '</span><button class="secondary" type="button" id="copy">Copy audit link</button></div>' +
      '<ul class="checks">' + d.checks.map(function (c) { var k = c.ok === true ? ["ok", "✓", "passes"] : c.ok === false ? ["bad", "✗", "fails"] : ["unk", "?", "unknown"]; return '<li><span class="' + k[0] + '" aria-label="' + k[2] + '">' + k[1] + "</span><span><b>" + esc(c.name) + ".</b> " + esc(c.detail) + "</span></li>"; }).join("") + "</ul>";
  } catch (e) { out.innerHTML = '<p class="status err">The audit failed to load.</p>'; }
}

document.addEventListener("click", function (e) {
  var c = e.target.closest(".chip, tr[data-id], [data-job]");
  if (c) { e.preventDefault(); select(c.dataset.id || c.dataset.job, true); document.getElementById("board").scrollIntoView({ block: "nearest" }); return; }
  var a = e.target.closest("[data-audit]");
  if (a) { e.preventDefault(); audit(a.dataset.audit); document.getElementById("audit").scrollIntoView({ behavior: "smooth" }); return; }
  if (e.target.id === "copy") { navigator.clipboard.writeText(location.href).then(function () { e.target.textContent = "Copied"; }, function () { e.target.textContent = "Copy failed"; }); }
});
document.getElementById("go").addEventListener("click", function () { var s = document.getElementById("sig").value.trim(); if (s) audit(s); });
document.getElementById("sig").addEventListener("keydown", function (e) { if (e.key === "Enter") document.getElementById("go").click(); });

var f = document.getElementById("f");
f.date.value = new Date(Date.now() - 3 * 86400000).toISOString().slice(0, 10);
f.date.max = new Date(Date.now() - 2 * 86400000).toISOString().slice(0, 10);
f.addEventListener("submit", async function (e) {
  e.preventDefault();
  var b = f.querySelector("button"), st = document.getElementById("status");
  Array.prototype.forEach.call(f.querySelectorAll("input"), function (i) { i.setAttribute("aria-invalid", i.checkValidity() ? "false" : "true"); });
  if (!f.checkValidity()) { st.className = "status err"; st.textContent = "Fill in the place, day, contractor address and fee."; return; }
  b.disabled = true; b.textContent = "Settling…"; st.className = "status";
  st.textContent = "Depositing into escrow, buying two readings through Telegraph, settling on Solana. Up to a minute.";
  try {
    var r = await fetch(BASE + "/api/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(f))) });
    var j = await r.json();
    if (j.id) { st.textContent = j.reused ? "This job already ran; showing it." : ""; await load(j.id); history.replaceState(null, "", "?job=" + encodeURIComponent(j.id)); document.getElementById("board").scrollIntoView({ behavior: "smooth" }); }
    if (!r.ok) { st.className = "status err"; st.textContent = j.error || "The job did not complete."; }
  } catch (err) { st.className = "status err"; st.textContent = "The request failed. Check All jobs before trying again."; }
  b.disabled = false; b.textContent = "Book and settle";
});

load().then(function () { var t = new URLSearchParams(location.search).get("tx"); if (t) { audit(t); document.getElementById("audit").scrollIntoView(); } });
</script>
</body>
</html>`;
