// Field-day review (Telegraph S2, Mission 06 Resources & Agriculture).
// A grower names a field job they planned for a past day; the app buys that
// day's recorded weather through Telegraph's ranked route (escrow rail) and
// says whether the weather window held. Every paid call lands in a public ledger.
import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { dirname } from "node:path";
import { privateKeyToAccount } from "viem/accounts";

import { OutcomeUnknown, escrowAsk, type AskOutcome, type Signer } from "./escrow.js";
import { PAGE } from "./field-day-page.js";

export const NODE = "https://devnode.telegraphprotocol.com";
export const INTENT = "WEATHER_FORECAST_VERIFY_GENERAL";
const DRY_MM = 1; // a "rain day" is 1 mm or more (WMO convention)
const DAY_MS = 86_400_000;

export const OPERATIONS = {
  spray: "Spraying (needs a dry day)",
  harvest: "Harvest (needs a dry day)",
  frost: "Frost-sensitive work (needs the low above 0 °C)",
} as const;
export type Operation = keyof typeof OPERATIONS;

export interface ReviewInput {
  operation: Operation;
  location: string;
  date: string;
}

export class InputError extends Error {}

export function validateInput(raw: unknown, now: number): ReviewInput {
  const r = (raw ?? {}) as Record<string, unknown>;
  const operation = r.operation;
  if (typeof operation !== "string" || !(operation in OPERATIONS)) throw new InputError("Choose spraying, harvest or frost-sensitive work.");
  const location = typeof r.location === "string" ? r.location.trim().replace(/\s+/g, " ") : "";
  if (!/^[\p{L}\p{M}0-9 ,.'()-]{2,80}$/u.test(location)) throw new InputError("Give a place name (2–80 letters, digits, spaces or , . ' ( ) -).");
  const date = typeof r.date === "string" ? r.date : "";
  const day = /^\d{4}-\d{2}-\d{2}$/.test(date) ? Date.parse(`${date}T00:00:00Z`) : NaN;
  if (Number.isNaN(day) || new Date(day).toISOString().slice(0, 10) !== date) throw new InputError("Give the date as YYYY-MM-DD.");
  const today = Math.floor(now / DAY_MS) * DAY_MS;
  // The recorded day must be over everywhere and in the archive: two full UTC days back.
  if (day > today - 2 * DAY_MS) throw new InputError("Pick a day at least two days ago; the record isn't final before then.");
  if (day < today - 365 * DAY_MS) throw new InputError("Pick a day within the last year.");
  return { operation: operation as Operation, location, date };
}

export interface Verdict {
  held: boolean | null;
  text: string;
}

export function verdict(operation: Operation, result: Record<string, unknown> | undefined): Verdict {
  const num = (k: string) => (typeof result?.[k] === "number" && Number.isFinite(result[k]) ? (result[k] as number) : null);
  if (operation === "frost") {
    const low = num("temp_min_c");
    if (low === null) return { held: null, text: "No recorded low came back, so the window can't be judged." };
    return low > 0
      ? { held: true, text: `Window held: the low was ${low.toFixed(1)} °C, above freezing.` }
      : { held: false, text: `Window failed: the low was ${low.toFixed(1)} °C. Check for frost damage and file the loss note.` };
  }
  const rain = num("precipitation_mm");
  if (rain === null) return { held: null, text: "No recorded precipitation came back, so the window can't be judged." };
  return rain < DRY_MM
    ? { held: true, text: `Window held: ${rain.toFixed(1)} mm recorded (dry means under ${DRY_MM} mm).` }
    : { held: false, text: `Window failed: ${rain.toFixed(1)} mm recorded. Reschedule and file the loss note.` };
}

// Append-only JSONL. An "attempt" row is written before the paid request leaves,
// so a crash mid-call still counts against the budget and is never resent.
export interface LedgerRow {
  type: "attempt" | "outcome";
  id: string;
  at: string;
  [key: string]: unknown;
}

export class Ledger {
  rows: LedgerRow[] = [];
  constructor(private path: string | null) {
    if (path && existsSync(path)) {
      this.rows = readFileSync(path, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
    }
    if (path) mkdirSync(dirname(path), { recursive: true });
  }
  append(row: LedgerRow): void {
    if (this.path) appendFileSync(this.path, JSON.stringify(row) + "\n", { mode: 0o600 });
    this.rows.push(row);
  }
  attempts(since = 0): number {
    return this.rows.filter((r) => r.type === "attempt" && Date.parse(r.at) >= since).length;
  }
  // A served answer for the same place and day is reused, so repeat clicks cost nothing.
  served(location: string, date: string): LedgerRow | undefined {
    const key = `${location.toLowerCase()}|${date}`;
    return this.rows.find((r) => r.type === "outcome" && r.kind === "served" && `${String(r.location).toLowerCase()}|${r.date}` === key);
  }
  // Public view: one entry per attempt, joined with its outcome (or "unknown").
  public(): Record<string, unknown>[] {
    const outcomes = new Map(this.rows.filter((r) => r.type === "outcome").map((r) => [r.id, r]));
    return this.rows
      .filter((r) => r.type === "attempt")
      .map((a) => ({ ...a, ...(outcomes.get(a.id) ?? { kind: "unknown" }), type: undefined }))
      .reverse();
  }
}

export interface Limits {
  maxCalls: number;
  maxPerDay: number;
  maxPerIpHour: number;
}

export class FieldDay {
  private queue: Promise<unknown> = Promise.resolve();
  private ipHits = new Map<string, number[]>();

  constructor(
    readonly ledger: Ledger,
    private signer: Signer,
    private limits: Limits,
    private fetchFn: typeof fetch = fetch,
    private now: () => number = Date.now,
  ) {}

  // Paid calls run one at a time so the caps can't be raced.
  review(raw: unknown, ip: string): Promise<{ status: number; body: Record<string, unknown> }> {
    const run = this.queue.then(() => this.reviewOne(raw, ip));
    this.queue = run.catch(() => undefined);
    return run;
  }

  private async reviewOne(raw: unknown, ip: string) {
    const now = this.now();
    let input: ReviewInput;
    try {
      input = validateInput(raw, now);
    } catch (error) {
      if (error instanceof InputError) return { status: 400, body: { error: error.message } };
      throw error;
    }
    const cached = this.ledger.served(input.location, input.date);
    if (cached) {
      const result = cached.result as Record<string, unknown> | undefined;
      return { status: 200, body: { ...cached, type: undefined, ...input, reused: true, verdict: verdict(input.operation, result) } };
    }
    if (this.ledger.attempts() >= this.limits.maxCalls) return { status: 503, body: { error: "This demo's call budget is used up. The ledger below shows every call it made." } };
    if (this.ledger.attempts(Math.floor(now / DAY_MS) * DAY_MS) >= this.limits.maxPerDay) return { status: 429, body: { error: "Today's call budget is used up. Try again after 00:00 UTC." } };
    const hits = (this.ipHits.get(ip) ?? []).filter((t) => t > now - 3_600_000);
    if (hits.length >= this.limits.maxPerIpHour) return { status: 429, body: { error: "Too many reviews from you this hour. Try again later." } };

    const id = `fd-${now}-${Math.random().toString(36).slice(2, 8)}`;
    const base = { id, location: input.location, date: input.date, operation: input.operation };
    let outcome: AskOutcome;
    try {
      outcome = await escrowAsk({
        node: NODE,
        signer: this.signer,
        body: { intent: INTENT, params: { location: input.location, date: input.date } },
        fetchFn: this.fetchFn,
        now: this.now,
        beforePay: (a) => {
          hits.push(now);
          this.ipHits.set(ip, hits);
          this.ledger.append({ type: "attempt", at: new Date(now).toISOString(), ...base, nonce: a.nonce, amount_uusdc: a.value });
        },
      });
    } catch (error) {
      if (error instanceof OutcomeUnknown) {
        this.ledger.append({ type: "outcome", at: new Date(this.now()).toISOString(), id, kind: "unknown", reason: error.message });
        return { status: 502, body: { error: "Telegraph didn't answer in time. This call is counted as possibly charged and won't be retried." } };
      }
      return { status: 502, body: { error: `Telegraph's payment offer failed our checks: ${(error as Error).message}` } };
    }
    if (outcome.kind === "unpaid_refusal") {
      return { status: 502, body: { error: `Telegraph refused before payment (nothing charged): ${outcome.reason}` } };
    }
    const at = new Date(this.now()).toISOString();
    if (outcome.kind === "paid_refusal") {
      this.ledger.append({ type: "outcome", at, ...base, kind: "paid_refusal", status: outcome.status, reason: outcome.reason, charged: false });
      return { status: 502, body: { error: `Telegraph refused the paid call (no receipt, so not charged): ${outcome.reason}` } };
    }
    const b = outcome.body;
    const result = (b.result && typeof b.result === "object" ? b.result : undefined) as Record<string, unknown> | undefined;
    const row = {
      ...base,
      kind: "served",
      status: outcome.status,
      miner_id: b.miner_id ?? null,
      miner_name: b.miner_name ?? null,
      intent: b.intent ?? INTENT,
      signal_hash: b.signal_hash ?? null,
      result_shape: b.result_shape ?? null,
      result: result ?? null,
      receipt: outcome.receipt,
    };
    this.ledger.append({ type: "outcome", at, ...row });
    return { status: 200, body: { ...row, verdict: verdict(input.operation, result) } };
  }
}

function send(res: ServerResponse, status: number, body: string, type: string): void {
  res.writeHead(status, {
    "Content-Type": type,
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'",
  });
  res.end(body);
}

async function readBody(req: IncomingMessage): Promise<unknown> {
  let size = 0;
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 4096) throw new InputError("Request too large.");
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new InputError("Request must be JSON.");
  }
}

export function handler(app: FieldDay) {
  return async (req: IncomingMessage, res: ServerResponse) => {
    const path = (req.url ?? "/").split("?")[0];
    try {
      if (req.method === "GET" && (path === "/field-day" || path === "/field-day/")) return send(res, 200, PAGE, "text/html; charset=utf-8");
      if (req.method === "GET" && path === "/field-day/api/ledger") return send(res, 200, JSON.stringify({ node: NODE, intent: INTENT, calls: app.ledger.public() }), "application/json");
      if (req.method === "POST" && path === "/field-day/api/review") {
        if (!String(req.headers["content-type"] ?? "").startsWith("application/json")) return send(res, 415, JSON.stringify({ error: "Send JSON." }), "application/json");
        const ip = String(req.headers["x-real-ip"] ?? req.socket.remoteAddress ?? "unknown");
        const out = await app.review(await readBody(req), ip);
        return send(res, out.status, JSON.stringify(out.body), "application/json");
      }
      return send(res, 404, JSON.stringify({ error: "not found" }), "application/json");
    } catch (error) {
      if (error instanceof InputError) return send(res, 400, JSON.stringify({ error: error.message }), "application/json");
      console.error("field-day error", (error as Error).message);
      return send(res, 500, JSON.stringify({ error: "internal error" }), "application/json");
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
  // Fail closed if the injected key isn't the wallet we funded.
  if (account.address.toLowerCase() !== expected.toLowerCase()) throw new Error("escrow key does not match OATHCAST_S2_ESCROW_ADDRESS");
  delete process.env.OATHCAST_S2_ESCROW_PRIVATE_KEY;
  const ledger = new Ledger(process.env.FIELD_DAY_LEDGER ?? "/data/field-day-ledger.jsonl");
  const app = new FieldDay(ledger, account, {
    maxCalls: envInt("FIELD_DAY_MAX_CALLS", 40),
    maxPerDay: envInt("FIELD_DAY_MAX_PER_DAY", 12),
    maxPerIpHour: envInt("FIELD_DAY_MAX_PER_IP_HOUR", 4),
  });
  const port = envInt("FIELD_DAY_PORT", 8791);
  const host = process.env.FIELD_DAY_HOST ?? "127.0.0.1";
  createServer(handler(app)).listen(port, host, () => console.log(`field-day on ${host}:${port}, payer ${account.address}`));
}

if (import.meta.url === `file://${process.argv[1]}`) main();
