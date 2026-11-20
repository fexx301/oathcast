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

export function topicsCovered(answer: string): Record<string, boolean> {
  const kept = answer.split(/(?<=[.;!?])\s+/).filter((s) => !SAYS_MISSING.test(s)).join(" ");
  return Object.fromEntries(Object.entries(TOPICS).map(([k, re]) => [k, re.test(kept)]));
}

export function specialistsFrom(catalogue: unknown): Specialist[] {
  const items = Array.isArray(catalogue) ? catalogue : ((catalogue as { miners?: unknown[] })?.miners ?? []);
  return (items as Record<string, unknown>[])
    .filter((m) => Array.isArray(m.supported_intents) && m.supported_intents.includes(INTENT) && m.activation_status !== "rejected")
    .map((m) => ({ id: String(m.id), name: String(m.name ?? ""), description: String(m.description ?? "") }))
    .filter((m) => /^\d+$/.test(m.id) && SPECIALIST.test(`${m.name} ${m.description}`) && !NOT_SPECIALIST.test(`${m.name} ${m.description}`))
    .slice(0, MAX_SPECIALISTS);
}

export function questionFor(site: Pick<Site, "name" | "sheet">): string {
  const q = `Tell me about the ${site.name} gold mine near ${site.sheet}, Queensland: who owned it, how much gold it produced, ` +
    "and when and why it closed.";
  return q.length <= 256 ? q : q.slice(0, 256);
}

const BAND: Record<string, string> = { vs: "very small", s: "small", m: "medium", l: "large", g: "giant" };

export class GoldRush {
  private queue: Promise<unknown> = Promise.resolve();
  private ipHits = new Map<string, number[]>();
  private sitesCache: { at: number; sites: Site[] } | null = null;
  private specialistsCache: { at: number; list: Specialist[] } | null = null;

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

  private async investigateOne(raw: unknown, ip: string) {
    const siteNo = Number((raw as { site_no?: unknown })?.site_no);
    if (!Number.isInteger(siteNo) || siteNo <= 0) throw new InputError("site_no must be a positive integer.");
    const site = (await this.sites()).find((s) => s.site_no === siteNo);
    if (!site) throw new InputError("No Queensland gold working has that site number.");
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

  private async askOne(site: Site, miner: Specialist, question: string, ip: string): Promise<Record<string, unknown>> {
    const cached = this.ledger.rows.find((r) => r.type === "outcome" && r.kind === "served" && r.site_no === site.site_no && r.asked_miner === miner.id);
    if (cached) return { ...cached, type: undefined, reused: true };
    const now = this.now();
    if (this.ledger.attempts() >= this.limits.maxCalls) return { kind: "skipped", miner_id: miner.id, miner_name: miner.name, reason: "This demo's call budget is used up." };
    if (this.ledger.attempts(Math.floor(now / DAY_MS) * DAY_MS) >= this.limits.maxPerDay) return { kind: "skipped", miner_id: miner.id, miner_name: miner.name, reason: "Today's call budget is used up." };
    const hits = (this.ipHits.get(ip) ?? []).filter((t) => t > now - 3_600_000);
    if (hits.length >= this.limits.maxPerIpHour) return { kind: "skipped", miner_id: miner.id, miner_name: miner.name, reason: "Too many paid calls from you this hour." };

    const id = `gr-${now}-${Math.random().toString(36).slice(2, 8)}`;
    const base = { id, site_no: site.site_no, site_name: site.name, asked_miner: miner.id, question };
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
      const answer = String(result.answer ?? "");
      const row = {
        ...base, kind: "served", miner_id: b.miner_id ?? miner.id, miner_name: b.miner_name ?? miner.name,
        answer, brief: result.brief ?? null, sources: Array.isArray(result.sources) ? result.sources.slice(0, 10) : [],
        topics: topicsCovered(answer), signal_hash: b.signal_hash ?? null, receipt: outcome.receipt,
      };
      this.ledger.append({ type: "outcome", at, ...row });
      return row;
    } catch (error) {
      if (error instanceof OutcomeUnknown) {
        this.ledger.append({ type: "outcome", at: new Date(this.now()).toISOString(), id, kind: "unknown", reason: error.message });
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
    maxCalls: envInt("GOLD_RUSH_MAX_CALLS", 80),
    maxPerDay: envInt("GOLD_RUSH_MAX_PER_DAY", 24),
    maxPerIpHour: envInt("GOLD_RUSH_MAX_PER_IP_HOUR", 8),
  });
  const port = envInt("GOLD_RUSH_PORT", 8792);
  const host = process.env.GOLD_RUSH_HOST ?? "127.0.0.1";
  createServer(handler(app)).listen(port, host, () => console.log(`gold-rush on ${host}:${port}, payer ${account.address}`));
  app.sites().catch((e) => console.error("gold-rush: site prefetch failed", (e as Error).message)); // warm the map's cache
}

if (import.meta.url === `file://${process.argv[1]}`) main();
