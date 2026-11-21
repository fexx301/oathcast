// Gold Rush (Telegraph S2 Apps & Agents; the team's Gold Rush challenge).
// Pick a Queensland gold working on a map; the app asks every relevant specialist Miner on Telegraph
// (direct asks, escrow rail) the same question, and reports each answer with its miner, cost and receipt,
// plus which parts of the question nobody answered. Every paid call lands in a public ledger.
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { privateKeyToAccount } from "viem/accounts";

import { OutcomeUnknown, escrowAsk, type Signer } from "./escrow.js";
import { InputError, Ledger, NODE, type Limits } from "./field-day.js";
import { PAGE } from "./gold-rush-page.js";

export const INTENT = "RESEARCH_QUERY_GENERAL";
const DAY_MS = 86_400_000;
const GSQ =
  "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/GeoscientificInformation/MiningResources/MapServer/16/query";
// A specialist names gold, mining, geology or Queensland in its catalogue entry; crypto "mining" is excluded.
const SPECIALIST = /\b(gold|goldfields?|mining|mines?|geolog\w*|queensland|minerals?|ore)\b/i;
const NOT_SPECIALIST = /\b(bitcoin|hashprice|hash ?rate|crypto|token|defi|blockchain)\b/i;
export const MAX_SPECIALISTS = 4;
const ROUTED = { id: "", name: "Telegraph ranked route", description: "" };

export interface Specialist {
  id: string;
  name: string;
  description: string;
  topics?: string[]; // extra questions it answers besides the site question (keys of EXTRA_TOPICS)
}

type SiteRef = Pick<Site, "site_no" | "name">;
const named = (s: SiteRef) => `${titled(s.name)} (site ${s.site_no})`;
// Extra questions a specialist may answer, each returning its own top-level rows (agreed with NFT KING, 21 Nov).
export const EXTRA_TOPICS: Record<string, { label: string; rows: string; re: RegExp; question: (s: SiteRef, year: number) => string }> = {
  price: { label: "gold price", rows: "gold_price", re: /gold[ -]price/i, question: (_s, year) => `Gold price in ${year}` },
  price_real: { label: "gold price then vs today", rows: "gold_price_real", re: /(closure|then) vs today|inflation-adjusted/i,
    question: (s) => `Gold price at closure vs today for ${named(s)}` },
  tenure: { label: "tenure", rows: "tenure", re: /current (mining )?tenure|tenure holder/i, question: (s) => `Who holds the tenure at ${named(s)}?` },
  native_title: { label: "native title", rows: "native_title", re: /native title/i, question: (s) => `Native title at ${named(s)}` },
  land_status: { label: "land status", rows: "land_status", re: /land status|protected area/i, question: (s) => `Land status of ${named(s)}` },
};
// shortcut: 7310050 answers all of these (NFT KING, 21 Nov) but its catalogue text doesn't say so yet; drop once it does.
const KNOWN_TOPICS: Record<string, string[]> = { "7310050": ["price", "price_real", "tenure", "native_title", "land_status"] };

// The year to price: the latest closure or last-working year this site's specialists reported for the site itself.
export function priceYear(rows: Record<string, unknown>[], site: SiteRef): number | null {
  const years = rows.filter((r) => r.type === "outcome" && r.kind === "served" && r.site_no === site.site_no && r.asked_miner && !r.topic)
    .flatMap((r) => { const c = (r.facts as Record<string, unknown> | null)?.closure; return Array.isArray(c) ? c : c ? [c] : []; })
    .filter((c) => aboutSite(c, site.name)).map((c) => Number((c as { year?: unknown }).year))
    .filter((y) => Number.isInteger(y) && y >= 1800 && y <= 2100);
  return years.length ? Math.max(...years) : null;
}

export interface Site {
  site_no: number;
  name: string;
  sheet: string;
  lat: number;
  lon: number;
  band: string | null;
}

// The four things the challenge asks about a mine; the report says which ones an answer touched.
export const TOPICS: Record<string, RegExp> = {
  "ownership": /\b(owned|owner|owners|company|companies|proprietor|syndicate|lease(holder)?)\b/i,
  "production": /\b(\d[\d,.]*\s*(oz|ounces?|kg|tonnes? of gold|t of gold))\b|\bproduc(ed|tion)\b/i,
  "closure": /\b(closed|closure|ceased|shut|abandoned in|worked until)\b/i,
  "gold price": /\b(gold price|price of gold|per ounce|\$\s?\d)/i,
};

// A sentence saying the fact is missing ("holds no owners", "not production figures") doesn't answer it.
const SAYS_MISSING = /\b(holds?|has|gives?|contains?|records?) no\b|\bno (owners?|ownership|production|closure)\b|\bnot (production|owners?|closure)\b|\bunknown\b/i;

const GENERIC = new Set(["MOUNT", "MT", "NO", "LODE", "THE", "GOLD", "MINE", "REEF", "NORTH", "SOUTH", "EAST", "WEST", "AND", "OF"]);

// The most distinctive word of a site name ("MOUNT MORGAN" -> "MORGAN"), to find sentences about this mine.
export function keyWord(name: string): string {
  const words = name.toUpperCase().split(/[^A-Z]+/).filter((w) => w.length > 2 && !GENERIC.has(w));
  return words.sort((a, b) => b.length - a.length)[0] ?? name.toUpperCase();
}

// A topic counts only in a sentence that names this mine and doesn't say the fact is missing.
export function topicsCovered(answer: string, siteName = ""): Record<string, boolean> {
  const key = siteName ? keyWord(siteName) : "";
  const kept = answer.split(/(?<=[.;!?])\s+/)
    .filter((s) => !SAYS_MISSING.test(s) && (!key || s.toUpperCase().includes(key))).join(" ");
  return Object.fromEntries(Object.entries(TOPICS).map(([k, re]) => [k, re.test(kept)]));
}

export function specialistsFrom(catalogue: unknown): Specialist[] {
  const items = Array.isArray(catalogue) ? catalogue : ((catalogue as { miners?: unknown[] })?.miners ?? []);
  return (items as Record<string, unknown>[])
    .filter((m) => Array.isArray(m.supported_intents) && m.supported_intents.includes(INTENT) && m.activation_status === "active")
    .map((m) => ({ id: String(m.id), name: String(m.name ?? ""), description: String(m.description ?? "") }))
    .map((m) => ({ ...m, topics: Object.keys(EXTRA_TOPICS).filter((t) => KNOWN_TOPICS[m.id]?.includes(t) || EXTRA_TOPICS[t].re.test(m.description)) }))
    .filter((m) => /^\d+$/.test(m.id) && SPECIALIST.test(`${m.name} ${m.description}`) && !NOT_SPECIALIST.test(`${m.name} ${m.description}`))
    .filter((m, i, all) => all.findIndex((x) => x.id === m.id) === i)
    .slice(0, MAX_SPECIALISTS);
}

export function titled(s: string): string {
  return s.toLowerCase().replace(/\b[a-z]/g, (c) => c.toUpperCase());
}

// Structured fields a specialist may return beside answer/sources (agreed with the NFT KING team, 2026-11-20).
export const FACT_KEYS = ["site_no", "production", "closure", "ownership", "years_worked", "references", "conflicts", "gold_price", "not_found"] as const;
// The answer envelope, not facts. Any other top-level rows (tenure, events, people, ...) are kept, so a new miner's fields show up.
const NOT_FACTS = new Set(["answer", "sources", "source_count", "brief", "question", "intent", "params"]);

export function structured(raw: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!raw) return null;
  const extra = Object.keys(raw).filter((k) => !NOT_FACTS.has(k) && !(FACT_KEYS as readonly string[]).includes(k) && /^[a-z][a-z0-9_]{0,40}$/.test(k)
    && raw[k] !== null && typeof raw[k] === "object");
  const out: Record<string, unknown> = Object.fromEntries([...FACT_KEYS, ...extra].filter((k) => raw[k] !== undefined && raw[k] !== null).map((k) => [k, raw[k]]));
  // A register can list the same record twice (e.g. one tenure in two layers): keep one row per record_id.
  // Register rows only (they say where they sit relative to the site); fact rows may share a record legitimately.
  for (const [k, v] of Object.entries(out)) if (Array.isArray(v) && v.some((x) => (x as { relation?: unknown } | null)?.relation)) {
    const seen = new Set<string>();
    out[k] = v.filter((x) => { const id = (x as { record_id?: unknown } | null)?.record_id; if (typeof id !== "string" || !id) return true; if (seen.has(id)) return false; seen.add(id); return true; });
  }
  // Keep the ledger row bounded: the long narrative and diagnostic fields go first, the facts stay.
  const LIMIT = 24_576, size = () => JSON.stringify(out).length;
  for (const k of ["years_worked", "references", "checks", "licences", "buffer", "gsq_record", "categories", "series_coverage"]) if (size() > LIMIT) delete out[k];
  // Still too big (e.g. 30 tenures within 2 km): keep rows over the site point first, then fewer nearby ones, and say how many were left out.
  const before = Object.fromEntries(Object.entries(out).filter(([, v]) => Array.isArray(v)).map(([k, v]) => [k, (v as unknown[]).length]));
  const over = (x: unknown) => Number((x as { relation?: unknown } | null)?.relation === "contains_site_point");
  for (const keep of [12, 6, 3]) {
    if (size() <= LIMIT) break;
    for (const [k, v] of Object.entries(out)) if (Array.isArray(v) && v.length > keep) out[k] = [...v].sort((a, b) => over(b) - over(a)).slice(0, keep);
  }
  const omitted = Object.fromEntries(Object.entries(before).map(([k, n]) => [k, n - (out[k] as unknown[]).length]).filter(([, d]) => (d as number) > 0));
  if (Object.keys(omitted).length) out.omitted = omitted;
  return Object.keys(out).length && size() <= LIMIT ? out : null;
}

const nonEmpty = (v: unknown) => (Array.isArray(v) ? v.length > 0 : Boolean(v && typeof v === "object" && Object.keys(v).length));

// A fact row is about this mine unless the miner names a different working (e.g. Brilliant Deeps beside Brilliant Lode).
export function aboutSite(row: unknown, siteName = ""): boolean {
  const e = (row as { entity?: unknown } | null)?.entity;
  return !siteName || typeof e !== "string" || !e.trim() || e.toLowerCase().includes(siteName.toLowerCase());
}

// An extra-topic answer covers only its own rows; a site answer is checked by sentence and by its structured fields.
function topicsOf(answer: string, facts: Record<string, unknown> | null, siteName: string, topic?: string): Record<string, boolean> {
  const t = topic ? EXTRA_TOPICS[topic] : undefined;
  if (t) return { [t.label]: Boolean(facts && nonEmpty(facts[t.rows])) };
  return { ...topicsCovered(answer, siteName), ...factTopics(facts, siteName) };
}

export function factTopics(facts: Record<string, unknown> | null, siteName = ""): Record<string, boolean> {
  if (!facts) return {};
  const rows = (v: unknown) => (Array.isArray(v) ? v : v && typeof v === "object" ? [v] : []).filter((x) => aboutSite(x, siteName));
  return Object.fromEntries(Object.entries({ ownership: "ownership", production: "production", closure: "closure", "gold price": "gold_price" })
    .filter(([, k]) => (k === "gold_price" ? nonEmpty(facts[k]) : rows(facts[k]).length > 0)).map(([t]) => [t, true]));
}

export function questionFor(site: Pick<Site, "name" | "sheet"> & { site_no?: number }): string {
  const near = site.sheet && site.sheet.toUpperCase() !== site.name.toUpperCase() ? ` near ${titled(site.sheet)}` : "";
  // The GSQ site number lets specialists match the exact record (same-name sites exist on different sheets).
  const no = site.site_no ? ` (site ${site.site_no})` : "";
  const q = `Tell me about the ${titled(site.name)} gold mine${no}${near}, Queensland: who owned it, how much gold it produced, ` +
    "and when and why it closed.";
  return q.length <= 256 ? q : q.slice(0, 256);
}

const BAND: Record<string, string> = { vs: "very small", s: "small", m: "medium", l: "large", g: "giant" };

export class GoldRush {
  private queue: Promise<unknown> = Promise.resolve();
  private ipHits = new Map<string, number[]>();
  private sitesCache: { at: number; sites: Site[] } | null = null;
  private specialistsCache: { at: number; list: Specialist[] } | null = null;
  private deliveryCache = new Map<string, { at: number; status: string }>();

  constructor(
    readonly ledger: Ledger,
    private signer: Signer,
    private limits: Limits,
    private fetchFn: typeof fetch = fetch,
    private now: () => number = Date.now,
  ) {}

  // Every Queensland historical working with gold, for the map. Public CC BY 4.0 data; presentation only.
  async sites(): Promise<Site[]> {
    if (this.sitesCache && this.now() - this.sitesCache.at < DAY_MS) return this.sitesCache.sites;
    const out: Site[] = [];
    for (let offset = 0; offset < 20_000; offset += 2000) {
      const q = new URLSearchParams({
        where: "all_commodities LIKE '%Au(%'", outFields: "site_no,occur_name,sheet_100_name,latitude,longitude,all_commodities",
        returnGeometry: "false", orderByFields: "site_no", resultOffset: String(offset), resultRecordCount: "2000", f: "json",
      });
      const r = await this.fetchFn(`${GSQ}?${q}`, { signal: AbortSignal.timeout(30_000) });
      const body = (await r.json()) as { features?: { attributes: Record<string, unknown> }[]; exceededTransferLimit?: boolean };
      for (const { attributes: a } of body.features ?? []) {
        const band = /\bAu\((vs|s|m|l|g)\)/.exec(String(a.all_commodities ?? ""))?.[1] ?? null;
        out.push({ site_no: Number(a.site_no), name: String(a.occur_name ?? ""), sheet: String(a.sheet_100_name ?? ""),
          lat: Number(a.latitude), lon: Number(a.longitude), band: band ? BAND[band] : null });
      }
      if (!body.exceededTransferLimit) break;
    }
    if (out.length) this.sitesCache = { at: this.now(), sites: out };
    return out;
  }

  async specialists(): Promise<Specialist[]> {
    if (this.specialistsCache && this.now() - this.specialistsCache.at < 600_000) return this.specialistsCache.list;
    const r = await this.fetchFn(`${NODE}/api/miners?intent=${INTENT}`, { signal: AbortSignal.timeout(30_000) });
    const list = specialistsFrom(await r.json());
    this.specialistsCache = { at: this.now(), list };
    return list;
  }

  // Paid calls run one at a time so the caps can't be raced.
  investigate(raw: unknown, ip: string): Promise<{ status: number; body: Record<string, unknown> }> {
    const run = this.queue.then(() => this.investigateOne(raw, ip));
    this.queue = run.catch(() => undefined);
    return run;
  }

  private async siteFor(raw: unknown): Promise<Site> {
    const siteNo = Number((raw as { site_no?: unknown })?.site_no);
    if (!Number.isInteger(siteNo) || siteNo <= 0) throw new InputError("site_no must be a positive integer.");
    const site = (await this.sites()).find((s) => s.site_no === siteNo);
    if (!site) throw new InputError("No Queensland gold working has that site number.");
    return site;
  }

  // Step-by-step investigation, part 1: who will be asked, and the question. Free.
  async plan(raw: unknown): Promise<{ status: number; body: Record<string, unknown> }> {
    const site = await this.siteFor(raw);
    const specialists = await this.specialists();
    if (!specialists.length) return { status: 503, body: { error: "No specialist Miner for Queensland gold mines is registered right now." } };
    const steps = [...specialists.map((m) => ({ miner: m.id, miner_name: m.name, path: "direct" })),
      // After the site answers, so the gold-price year comes from them.
      ...specialists.flatMap((m) => (m.topics ?? []).map((t) => ({ miner: m.id, miner_name: m.name, path: "direct", topic: t, label: EXTRA_TOPICS[t].label }))),
      { miner: "ranked", miner_name: ROUTED.name, path: "ranked route" }];
    return { status: 200, body: { site, question: questionFor(site), steps } };
  }

  // Part 2: one paid ask. Only a current specialist or "ranked", so the page can't be used to pay arbitrary miners.
  ask(raw: unknown, ip: string): Promise<{ status: number; body: Record<string, unknown> }> {
    const run = this.queue.then(async () => {
      const site = await this.siteFor(raw);
      const want = String((raw as { miner?: unknown })?.miner ?? "");
      const miner = want === "ranked" ? ROUTED : (await this.specialists()).find((m) => m.id === want);
      if (!miner) throw new InputError("miner must be a current specialist or \"ranked\".");
      const topic = (raw as { topic?: unknown })?.topic;
      if (topic !== undefined && topic !== null && topic !== "") {
        if (typeof topic !== "string" || !EXTRA_TOPICS[topic] || !(miner as Specialist).topics?.includes(topic)) throw new InputError("That miner doesn't answer that question.");
        // Site answers stored before their rows fit the size cap are re-read from their signal records (free) for the year.
        const siteRows = await Promise.all(this.ledger.rows.filter((r) => r.type === "outcome" && r.kind === "served" && r.site_no === site.site_no && r.asked_miner && !r.topic)
          .map(async (r) => (r.facts || !r.signal_hash ? r : { ...r, facts: structured(await this.rawResponse(r.signal_hash)) })));
        const year = priceYear(siteRows, site);
        if (topic === "price" && year === null) return { status: 200, body: { kind: "skipped", topic, miner_id: miner.id, miner_name: miner.name, path: "direct",
          reason: "No closure or last-working year in this site's answers yet, so there's no year to price (nothing charged)." } };
        return { status: 200, body: { ...(await this.askOne(site, miner, EXTRA_TOPICS[topic].question(site, year ?? 0), ip, topic)), path: "direct" } };
      }
      const answer = await this.askOne(site, miner, questionFor(site), ip);
      return { status: 200, body: { ...answer, path: want === "ranked" ? "ranked route" : "direct" } };
    });
    this.queue = run.catch(() => undefined);
    return run;
  }

  private async investigateOne(raw: unknown, ip: string) {
    const site = await this.siteFor(raw);
    const specialists = await this.specialists();
    if (!specialists.length) return { status: 503, body: { error: "No specialist Miner for Queensland gold mines is registered right now." } };
    const question = questionFor(site);
    const answers: Record<string, unknown>[] = [];
    for (const miner of specialists) answers.push({ ...(await this.askOne(site, miner, question, ip)), path: "direct" });
    // One answer from Telegraph's ranked route on the same question, for comparison.
    answers.push({ ...(await this.askOne(site, ROUTED, question, ip)), path: "ranked route" });
    const covered = Object.fromEntries(Object.keys(TOPICS).map((t) => [t,
      answers.filter((a) => a.kind === "served" && (a.topics as Record<string, boolean>)?.[t]).map((a) => a.miner_name)]));
    return { status: 200, body: { site, question, answers, covered } };
  }

  // Has the node published the delivery record for one of our receipts? Only receipts in this app's ledger are checked.
  // recorded (200) is kept for good; pending (no block for the epoch yet) and missing for 5 minutes; unknown not kept.
  async delivery(raw: unknown): Promise<{ status: number; body: Record<string, unknown> }> {
    const receipt = String(raw ?? "");
    if (!/^0x[0-9a-f]{64}$/i.test(receipt)) throw new InputError("Give a receipt hash.");
    const row = this.ledger.rows.find((r) => r.type === "outcome" && (r.receipt as { receipt_hash?: string } | undefined)?.receipt_hash?.toLowerCase() === receipt.toLowerCase());
    const epoch = (row?.receipt as { epoch_id?: unknown } | undefined)?.epoch_id;
    if (!row || !/^\d{1,9}$/.test(String(epoch))) return { status: 404, body: { error: "Not a receipt from this app." } };
    const url = `${NODE}/v1/blocks/${epoch}/deliveries/${receipt}`;
    const hit = this.deliveryCache.get(receipt);
    if (hit && (hit.status === "recorded" || this.now() - hit.at < 300_000)) return { status: 200, body: { receipt, epoch, url, status: hit.status } };
    let status = "unknown";
    try {
      const r = await this.fetchFn(url, { signal: AbortSignal.timeout(10_000) });
      const text = r.status === 404 ? await r.text() : "";
      status = r.ok ? "recorded" : r.status === 404 ? (/no block recorded/i.test(text) ? "pending" : "missing") : "unknown";
    } catch { /* node unreachable: unknown */ }
    if (status !== "unknown") this.deliveryCache.set(receipt, { at: this.now(), status });
    return { status: 200, body: { receipt, epoch, url, status } };
  }

  // GET /engine/v1/signal/{hash} -> payload.response: the miner's raw answer, extra fields included. Free; best effort.
  private async rawResponse(hash: unknown): Promise<Record<string, unknown> | null> {
    if (typeof hash !== "string" || !/^0x[0-9a-f]{64}$/i.test(hash)) return null;
    try {
      const r = await this.fetchFn(`${NODE}/engine/v1/signal/${hash}`, { signal: AbortSignal.timeout(15_000) });
      const body = (await r.json()) as { payload?: { response?: unknown } };
      const resp = body?.payload?.response;
      return resp && typeof resp === "object" && !Array.isArray(resp) ? (resp as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  }

  private async askOne(site: Site, miner: Specialist, question: string, ip: string, topic?: string): Promise<Record<string, unknown>> {
    // A site's price answer is cached apart from its site answer (same miner, different question).
    const mine = (r: Record<string, unknown>) => r.site_no === site.site_no && r.asked_miner === miner.id && (r.topic ?? undefined) === topic;
    const cached = this.ledger.rows.find((r) => r.type === "outcome" && r.kind === "served" && mine(r));
    // A reused answer is re-checked with the same topic rule as a fresh one.
    if (cached) {
      let facts = (cached.facts ?? null) as Record<string, unknown> | null;
      // Stored before its rows fit the size cap: re-read them from the miner's signal record (free, not charged again).
      if (!facts && cached.signal_hash) facts = structured(await this.rawResponse(cached.signal_hash));
      return { ...cached, facts, type: undefined, reused: true, topics: topicsOf(String(cached.answer ?? ""), facts, site.name, topic) };
    }
    // An earlier payment for this site and miner with no settled outcome may have been charged: never pay for it twice.
    // shortcut: it stays blocked until the ledger row is reconciled by hand; automate if timeouts become common.
    const settled = new Set(this.ledger.rows.filter((r) => r.type === "outcome" && r.kind !== "unknown").map((r) => r.id));
    if (this.ledger.rows.some((r) => r.type === "attempt" && mine(r) && !settled.has(r.id)))
      return { kind: "unknown", miner_id: miner.id, miner_name: miner.name, reason: "An earlier payment for this question has no recorded outcome, so it isn't paid again." };
    const now = this.now();
    if (this.ledger.attempts() >= this.limits.maxCalls) return { kind: "skipped", miner_id: miner.id, miner_name: miner.name, reason: "This demo's call budget is used up." };
    if (this.ledger.attempts(Math.floor(now / DAY_MS) * DAY_MS) >= this.limits.maxPerDay) return { kind: "skipped", miner_id: miner.id, miner_name: miner.name, reason: "Today's call budget is used up." };
    const hits = (this.ipHits.get(ip) ?? []).filter((t) => t > now - 3_600_000);
    if (hits.length >= this.limits.maxPerIpHour) return { kind: "skipped", miner_id: miner.id, miner_name: miner.name, reason: "Too many paid calls from you this hour." };

    const id = `gr-${now}-${Math.random().toString(36).slice(2, 8)}`;
    const base = { id, site_no: site.site_no, site_name: site.name, asked_miner: miner.id, question, ...(topic ? { topic } : {}) };
    try {
      const outcome = await escrowAsk({
        node: NODE, signer: this.signer, minerId: miner.id || undefined, fetchFn: this.fetchFn, now: this.now,
        body: { intent: INTENT, params: { question } },
        beforePay: (a) => {
          hits.push(now);
          this.ipHits.set(ip, hits);
          this.ledger.append({ type: "attempt", at: new Date(now).toISOString(), ...base, nonce: a.nonce, amount_uusdc: a.value });
        },
      });
      const at = new Date(this.now()).toISOString();
      if (outcome.kind === "unpaid_refusal") return { ...base, kind: "refused", miner_id: miner.id, miner_name: miner.name, reason: `refused before payment (nothing charged): ${outcome.reason}` };
      if (outcome.kind === "paid_refusal") {
        const row = { ...base, kind: "paid_refusal", miner_id: miner.id, miner_name: miner.name, reason: outcome.reason, charged: false };
        this.ledger.append({ type: "outcome", at, ...row });
        return row;
      }
      const b = outcome.body;
      const result = (b.result && typeof b.result === "object" ? b.result : {}) as Record<string, unknown>;
      // The node reshapes (and may reword) the answer; the miner's own response is kept under its signal hash.
      const raw = await this.rawResponse(b.signal_hash);
      const answer = String(raw?.answer ?? result.answer ?? "");
      const parsed = structured(raw);
      // Structured fields for a different survey site number are dropped rather than credited to this one.
      const facts = parsed && parsed.site_no != null && Number(parsed.site_no) !== site.site_no ? null : parsed;
      const row = {
        ...base, kind: "served", miner_id: b.miner_id ?? miner.id, miner_name: b.miner_name ?? miner.name,
        answer, shaped_answer: raw?.answer ? String(result.answer ?? "") : null, brief: result.brief ?? null,
        sources: Array.isArray(raw?.sources ?? result.sources) ? ((raw?.sources ?? result.sources) as unknown[]).slice(0, 10) : [],
        facts, topics: topicsOf(answer, facts, site.name, topic),
        signal_hash: b.signal_hash ?? null, receipt: outcome.receipt,
      };
      this.ledger.append({ type: "outcome", at, ...row });
      return row;
    } catch (error) {
      if (error instanceof OutcomeUnknown) {
        this.ledger.append({ type: "outcome", at: new Date(this.now()).toISOString(), ...base, kind: "unknown", reason: error.message });
        return { ...base, kind: "unknown", miner_id: miner.id, miner_name: miner.name, reason: "No answer in time; counted as possibly charged." };
      }
      return { ...base, kind: "refused", miner_id: miner.id, miner_name: miner.name, reason: `payment offer failed our checks: ${(error as Error).message}` };
    }
  }
}

function send(res: ServerResponse, status: number, body: string, type: string): void {
  res.writeHead(status, {
    "Content-Type": type,
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline' https://cdnjs.cloudflare.com; script-src 'unsafe-inline' https://cdnjs.cloudflare.com; img-src https://*.tile.openstreetmap.org data:; connect-src 'self'; base-uri 'none'; form-action 'none'",
  });
  res.end(body);
}

async function readBody(req: IncomingMessage): Promise<unknown> {
  let size = 0;
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 1024) throw new InputError("Request too large.");
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new InputError("Request must be JSON.");
  }
}

export function handler(app: GoldRush) {
  return async (req: IncomingMessage, res: ServerResponse) => {
    const path = (req.url ?? "/").split("?")[0];
    const jsonType = "application/json";
    try {
      if (req.method === "GET" && (path === "/gold-rush" || path === "/gold-rush/")) return send(res, 200, PAGE, "text/html; charset=utf-8");
      if (req.method === "GET" && path === "/gold-rush/api/sites") return send(res, 200, JSON.stringify({ source: "Geological Survey of Queensland, MINOCC historical workings (CC BY 4.0)", sites: await app.sites() }), jsonType);
      if (req.method === "GET" && path === "/gold-rush/api/specialists") return send(res, 200, JSON.stringify({ intent: INTENT, specialists: await app.specialists() }), jsonType);
      if (req.method === "GET" && path === "/gold-rush/api/ledger") return send(res, 200, JSON.stringify({ node: NODE, intent: INTENT, calls: app.ledger.public() }), jsonType);
      if (req.method === "GET" && path === "/gold-rush/api/delivery") {
        const out = await app.delivery(new URL(req.url ?? "/", "http://x").searchParams.get("receipt"));
        return send(res, out.status, JSON.stringify(out.body), jsonType);
      }
      if (req.method === "GET" && path === "/gold-rush/api/plan") {
        const out = await app.plan({ site_no: new URL(req.url ?? "/", "http://x").searchParams.get("site_no") });
        return send(res, out.status, JSON.stringify(out.body), jsonType);
      }
      if (req.method === "POST" && path === "/gold-rush/api/ask") {
        if (!String(req.headers["content-type"] ?? "").startsWith(jsonType)) return send(res, 415, JSON.stringify({ error: "Send JSON." }), jsonType);
        const ip = String(req.headers["x-real-ip"] ?? req.socket.remoteAddress ?? "unknown");
        const out = await app.ask(await readBody(req), ip);
        return send(res, out.status, JSON.stringify(out.body), jsonType);
      }
      if (req.method === "POST" && path === "/gold-rush/api/investigate") {
        if (!String(req.headers["content-type"] ?? "").startsWith(jsonType)) return send(res, 415, JSON.stringify({ error: "Send JSON." }), jsonType);
        const ip = String(req.headers["x-real-ip"] ?? req.socket.remoteAddress ?? "unknown");
        const out = await app.investigate(await readBody(req), ip);
        return send(res, out.status, JSON.stringify(out.body), jsonType);
      }
      return send(res, 404, JSON.stringify({ error: "not found" }), jsonType);
    } catch (error) {
      if (error instanceof InputError) return send(res, 400, JSON.stringify({ error: error.message }), jsonType);
      console.error("gold-rush error", (error as Error).message);
      return send(res, 500, JSON.stringify({ error: "internal error" }), jsonType);
    }
  };
}

function envInt(name: string, fallback: number): number {
  const v = Number(process.env[name] ?? fallback);
  if (!Number.isInteger(v) || v < 0) throw new Error(`${name} must be a non-negative integer`);
  return v;
}

export function main(): void {
  const key = process.env.OATHCAST_S2_ESCROW_PRIVATE_KEY;
  const expected = process.env.OATHCAST_S2_ESCROW_ADDRESS;
  if (!key || !expected) throw new Error("OATHCAST_S2_ESCROW_PRIVATE_KEY and OATHCAST_S2_ESCROW_ADDRESS are required");
  const account = privateKeyToAccount(key as `0x${string}`);
  if (account.address.toLowerCase() !== expected.toLowerCase()) throw new Error("escrow key does not match OATHCAST_S2_ESCROW_ADDRESS");
  delete process.env.OATHCAST_S2_ESCROW_PRIVATE_KEY;
  const ledger = new Ledger(process.env.GOLD_RUSH_LEDGER ?? "/data/gold-rush-ledger.jsonl");
  const app = new GoldRush(ledger, account, {
    // A full report is up to 8 calls (site, gold price, land and rights, ranked): about 20 new sites in all, 7 a day.
    maxCalls: envInt("GOLD_RUSH_MAX_CALLS", 180),
    maxPerDay: envInt("GOLD_RUSH_MAX_PER_DAY", 60),
    maxPerIpHour: envInt("GOLD_RUSH_MAX_PER_IP_HOUR", 20),
  });
  const port = envInt("GOLD_RUSH_PORT", 8792);
  const host = process.env.GOLD_RUSH_HOST ?? "127.0.0.1";
  createServer(handler(app)).listen(port, host, () => console.log(`gold-rush on ${host}:${port}, payer ${account.address}`));
  app.sites().catch((e) => console.error("gold-rush: site prefetch failed", (e as Error).message)); // warm the map's cache
}

if (import.meta.url === `file://${process.argv[1]}`) main();
