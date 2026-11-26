// Field-day Settle (Telegraph S2, on Solana devnet): weather-verified settlement for field work.
//
// A buyer books a contractor for a field job on a given day (spraying, harvest, frost-sensitive work) and deposits
// the fee into escrow. After the day, the app buys the recorded weather twice through Telegraph, paying each call on
// Solana with x402: once on the ranked route (Telegraph picks the verifier) and once from a different team's verifier
// as a cross-check. Code, not a model, then decides:
//   both sources say the window held  -> RELEASE the fee to the contractor
//   both say it failed                -> REFUND the buyer
//   they disagree, or one has no data -> HOLD in escrow for a person to resolve
// Every step is a devnet transaction whose memo carries the job and the evidence (each verifier's reading and its
// Telegraph signal hash), so anyone can audit a settlement from its transaction alone: GET /api/audit?tx=<signature>.
//
// Honest limits: devnet only; the escrow is a custodial agent wallet the app holds, not an on-chain program; the
// demo buyer is the app's own operator wallet.
import { createHash } from "node:crypto";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";

import { base58 } from "@scure/base";
import {
  AccountRole, address, appendTransactionMessageInstructions, createKeyPairSignerFromBytes, createSolanaRpc,
  createSolanaRpcSubscriptions, createTransactionMessage, getSignatureFromTransaction, isAddress, pipe,
  sendAndConfirmTransactionFactory, setTransactionMessageFeePayerSigner, setTransactionMessageLifetimeUsingBlockhash,
  signTransactionMessageWithSigners, type Address, type Instruction, type KeyPairSigner,
} from "@solana/kit";
import {
  findAssociatedTokenPda, getCreateAssociatedTokenIdempotentInstructionAsync, getTransferCheckedInstruction,
  TOKEN_PROGRAM_ADDRESS,
} from "@solana-program/token";
import { decodePaymentResponseHeader, x402Client, x402HTTPClient, type PaymentRequirements } from "@x402/fetch";
import { ExactSvmScheme } from "@x402/svm/exact/client";

import { EXPECTED_PAY_TO, SOLANA_DEVNET_NETWORK, SOLANA_DEVNET_USDC } from "./canary.js";
import { INTENT, InputError, Ledger, NODE, OPERATIONS, validateInput, verdict, type Operation, type ReviewInput } from "./field-day.js";
import { PAGE } from "./field-day-solana-page.js";

export const RPC_URL = "https://api.devnet.solana.com";
export const WS_URL = "wss://api.devnet.solana.com";
export const MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr";
export const APP = "oathcast-field-day";
export const MAX_ASK_UNITS = 10_000n; // $0.01 (6-decimal USDC): refuse any dearer Telegraph offer
export const MIN_FEE_UNITS = 10_000n; // job fees between 0.01 and 0.05 devnet USDC
export const MAX_FEE_UNITS = 50_000n;
// Cross-check verifiers, tried in order (skipping the miner the ranked route chose) until one answers.
// 77031 (SecondSky) reads NASA POWER: satellite and MERRA-2 data, independent of the ERA5 archive that 64174 and
// 731004 both read (they agreed to the decimal on every test day). 731004 is Counterproof's; 64174 is ours, last.
export const CROSS_CHECK = ["77031", "731004", "64174"];
const DAY_MS = 86_400_000;

export type Decision = "RELEASE" | "REFUND" | "HOLD";
export type Step = "BOOKED" | Decision;

export interface Job extends ReviewInput {
  contractor: string;
  fee_units: bigint;
}

export interface Source {
  role: "ranked" | "cross-check";
  miner_id: string;
  result: Record<string, unknown>;
  signal_hash: string;
  payment_tx: string;
}

/** The memo on every transaction: compact JSON, parsed back by the audit. */
export interface Memo {
  v: 1;
  app: typeof APP;
  job: string;
  step: Step;
  op: Operation;
  loc: string;
  date: string;
  fee: string; // units of 6-decimal USDC
  src?: [string, number | null, string][]; // [miner, reading, signal hash]
}

// ---------- pure rules ----------

/** Same job, same id: a repeat request resumes or shows the job instead of paying again. */
export function jobId(job: Job): string {
  const key = [job.operation, job.location.toLowerCase(), job.date, job.contractor, job.fee_units.toString()].join("|");
  return "fds-" + createHash("sha256").update(key).digest("hex").slice(0, 12);
}

export function parseFee(value: unknown): bigint {
  const text = typeof value === "number" ? value.toString() : typeof value === "string" ? value.trim() : "";
  if (!/^\d+(\.\d{1,6})?$/.test(text)) throw new InputError("Give the fee in USDC, e.g. 0.02.");
  const [whole, frac = ""] = text.split(".");
  const units = BigInt(whole) * 1_000_000n + BigInt(frac.padEnd(6, "0"));
  if (units < MIN_FEE_UNITS || units > MAX_FEE_UNITS) throw new InputError("The fee must be between 0.01 and 0.05 devnet USDC.");
  return units;
}

export function validateJob(raw: unknown, now: number): Job {
  const r = (raw ?? {}) as Record<string, unknown>;
  const input = validateInput(r, now);
  const contractor = typeof r.contractor === "string" ? r.contractor.trim() : "";
  if (!isAddress(contractor)) throw new InputError("Give the contractor's Solana address.");
  return { ...input, contractor, fee_units: parseFee(r.fee) };
}

/** The one number the rule reads: the day's low for frost work, else the day's precipitation.
 *  Signal records can store numbers as strings ("0.12"), so plain numeric strings count too. */
export function reading(op: Operation, result: Record<string, unknown> | undefined): number | null {
  const v = result?.[op === "frost" ? "temp_min_c" : "precipitation_mm"];
  const n = typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v.trim()) ? Number(v) : v;
  return typeof n === "number" && Number.isFinite(n) ? n : null;
}

/** Code decides: only two agreeing sources move money; anything else stays in escrow. */
export function decide(op: Operation, a: Record<string, unknown> | undefined, b: Record<string, unknown> | undefined): { decision: Decision; text: string } {
  const va = verdict(op, a);
  const vb = verdict(op, b);
  if (va.held === null || vb.held === null) return { decision: "HOLD", text: "A verifier returned no reading, so the fee stays in escrow." };
  if (va.held !== vb.held) return { decision: "HOLD", text: `The verifiers disagree (${va.text} / ${vb.text}), so the fee stays in escrow for review.` };
  return va.held
    ? { decision: "RELEASE", text: `Both verifiers agree. ${va.text} The fee goes to the contractor.` }
    : { decision: "REFUND", text: `Both verifiers agree. ${va.text} The fee goes back to the buyer.` };
}

/** Recomputes a settlement's decision from its memo's own readings, with the same thresholds as verdict(). */
export function decideFromReadings(op: Operation, readings: (number | null)[]): Decision {
  const field = op === "frost" ? "temp_min_c" : "precipitation_mm";
  const [a, b] = readings.map((x) => (x === null ? undefined : { [field]: x }));
  return decide(op, a, b).decision;
}

export function memoFor(id: string, step: Step, job: Job, sources?: Source[]): string {
  const m: Memo = { v: 1, app: APP, job: id, step, op: job.operation, loc: job.location, date: job.date, fee: job.fee_units.toString() };
  if (sources) m.src = sources.map((s) => [s.miner_id, reading(job.operation, s.result), s.signal_hash]);
  const text = JSON.stringify(m);
  if (Buffer.byteLength(text) > 560) throw new Error("memo too long");
  return text;
}

export function parseMemo(text: string): Memo | null {
  try {
    const m = JSON.parse(text) as Memo;
    if (m.v !== 1 || m.app !== APP || typeof m.job !== "string" || !["BOOKED", "RELEASE", "REFUND", "HOLD"].includes(m.step)) return null;
    if (!(m.op in OPERATIONS) || !/^\d+$/.test(m.fee)) return null;
    return m;
  } catch {
    return null;
  }
}

// ---------- Telegraph over Solana x402 ----------

export interface Telegraph {
  ask(job: ReviewInput, minerId: string | undefined, beforePay: () => void): Promise<Omit<Source, "role">>;
  signal(hash: string): Promise<Record<string, unknown> | null>;
}

function pickSolanaOffer(accepts: PaymentRequirements[]): PaymentRequirements {
  const sol = accepts.filter((r) => r.network === SOLANA_DEVNET_NETWORK);
  if (sol.length !== 1) throw new Error("Telegraph offered no single Solana devnet option");
  const o = sol[0];
  if (o.scheme !== "exact" || o.payTo !== EXPECTED_PAY_TO || o.asset !== SOLANA_DEVNET_USDC || BigInt(o.amount) > MAX_ASK_UNITS) {
    throw new Error(`Telegraph's Solana offer failed our checks: ${JSON.stringify(o)}`);
  }
  return o;
}

/** The answer must be for the place and day asked, in the intent's shape, with a signal hash. */
export function checkAnswer(job: ReviewInput, body: unknown): Omit<Source, "role" | "payment_tx"> {
  const b = (body ?? {}) as Record<string, unknown>;
  const result = b.result as Record<string, unknown> | undefined;
  const input = (b.input ?? {}) as Record<string, unknown>;
  if (b.intent !== INTENT || !result || typeof b.signal_hash !== "string" || !/^0x[0-9a-f]{64}$/i.test(b.signal_hash)) {
    throw new Error("Telegraph's answer is missing the intent, result or signal hash");
  }
  if (input.date !== job.date || result.date !== job.date || String(input.location).toLowerCase() !== job.location.toLowerCase()) {
    throw new Error("Telegraph's answer is for a different place or day");
  }
  return { miner_id: String(b.miner_id), result, signal_hash: b.signal_hash };
}

export function solanaTelegraph(signer: KeyPairSigner, fetchFn: typeof fetch = fetch): Telegraph {
  return {
    async ask(job, minerId, beforePay) {
      const body = JSON.stringify({ intent: INTENT, params: { location: job.location, date: job.date } });
      const post = (h: Record<string, string> = {}) => fetchFn(`${NODE}/engine/v1/ask${minerId ? `/${minerId}` : ""}`, {
        method: "POST", headers: { "content-type": "application/json", ...h }, body, redirect: "error", signal: AbortSignal.timeout(90_000),
      });
      const pre = await post();
      if (pre.status !== 402) throw new Error(`Telegraph's unpaid ask returned ${pre.status}, not 402`);
      const required = new x402HTTPClient(new x402Client()).getPaymentRequiredResponse((n) => pre.headers.get(n), await pre.json());
      const offer = pickSolanaOffer(required.accepts as PaymentRequirements[]);
      const client = new x402HTTPClient(new x402Client(() => offer).register(SOLANA_DEVNET_NETWORK, new ExactSvmScheme(signer, { rpcUrl: RPC_URL })));
      const headers = client.encodePaymentSignatureHeader(await client.createPaymentPayload(required));
      beforePay();
      const paid = await post(headers);
      const text = await paid.text();
      if (paid.status !== 200) throw new Error(`Telegraph's paid ask returned ${paid.status}: ${text.slice(0, 200)}`);
      const header = paid.headers.get("PAYMENT-RESPONSE") ?? paid.headers.get("X-PAYMENT-RESPONSE");
      const s = header ? decodePaymentResponseHeader(header) : null;
      if (!s?.success || s.network !== SOLANA_DEVNET_NETWORK || s.payer !== signer.address || !s.transaction) {
        throw new Error("Telegraph's payment response isn't a settled Solana devnet payment from us");
      }
      return { ...checkAnswer(job, JSON.parse(text)), payment_tx: s.transaction };
    },
    // GET /engine/v1/signal/{hash}: the node's record of the answer. Free; null when unreachable.
    async signal(hash) {
      try {
        const r = await fetchFn(`${NODE}/engine/v1/signal/${hash}`, { signal: AbortSignal.timeout(20_000) });
        return r.ok ? ((await r.json()) as Record<string, unknown>) : null;
      } catch {
        return null;
      }
    },
  };
}

const pick = (o: unknown, ...path: string[]) => path.reduce<unknown>((x, k) => (x && typeof x === "object" ? (x as Record<string, unknown>)[k] : undefined), o);

/** A signal record's answer: the miner's raw response is payload.response (its fields at the top, or under result). */
export function signalResult(record: Record<string, unknown> | null): Record<string, unknown> | undefined {
  const r = pick(record, "payload", "response", "result") ?? pick(record, "payload", "response");
  return r && typeof r === "object" ? (r as Record<string, unknown>) : undefined;
}

/** The miner that answered, as the node recorded it (signal.subnet_id). */
export function signalMiner(record: Record<string, unknown> | null): string | undefined {
  const m = pick(record, "signal", "subnet_id") ?? pick(record, "payload", "subnet_id");
  return m === undefined ? undefined : String(m);
}

// ---------- Solana devnet ----------

export interface ParsedTx {
  err: unknown;
  memos: string[];
  moves: { owner: string; delta: bigint }[]; // devnet-USDC balance changes by owner
}

export interface Chain {
  operator: string; // pays fees and Telegraph; the demo buyer
  agent: string; // the escrow agent wallet
  deposit(fee: bigint, memo: string): Promise<string>; // operator -> agent
  pay(to: string, fee: bigint, memo: string): Promise<string>; // agent -> to
  note(memo: string): Promise<string>; // memo only (HOLD)
  tx(signature: string): Promise<ParsedTx | null>;
}

export function solanaChain(operator: KeyPairSigner, agent: KeyPairSigner): Chain {
  const rpc = createSolanaRpc(RPC_URL);
  const mint = address(SOLANA_DEVNET_USDC);
  const ata = async (owner: Address) => (await findAssociatedTokenPda({ owner, mint, tokenProgram: TOKEN_PROGRAM_ADDRESS }))[0];
  const memoIx = (memo: string, signer: KeyPairSigner): Instruction =>
    ({ programAddress: address(MEMO_PROGRAM), accounts: [{ address: signer.address, role: AccountRole.READONLY_SIGNER, signer }],
      data: new TextEncoder().encode(memo) }) as unknown as Instruction;
  const send = async (ixs: Instruction[]) => {
    const { value: blockhash } = await rpc.getLatestBlockhash().send();
    const msg = pipe(createTransactionMessage({ version: 0 }), (m) => setTransactionMessageFeePayerSigner(operator, m),
      (m) => setTransactionMessageLifetimeUsingBlockhash(blockhash, m), (m) => appendTransactionMessageInstructions(ixs, m));
    const tx = await signTransactionMessageWithSigners(msg);
    await sendAndConfirmTransactionFactory({ rpc, rpcSubscriptions: createSolanaRpcSubscriptions(WS_URL) })(tx as never, { commitment: "confirmed" });
    return getSignatureFromTransaction(tx);
  };
  const transfer = async (from: KeyPairSigner, to: Address, amount: bigint) => [
    await getCreateAssociatedTokenIdempotentInstructionAsync({ payer: operator, owner: to, mint }),
    getTransferCheckedInstruction({ source: await ata(from.address), mint, destination: await ata(to), authority: from, amount, decimals: 6 }),
  ];
  return {
    operator: operator.address,
    agent: agent.address,
    deposit: async (fee, memo) => send([...(await transfer(operator, agent.address, fee)), memoIx(memo, operator)]),
    pay: async (to, fee, memo) => send([...(await transfer(agent, address(to), fee)), memoIx(memo, agent)]),
    note: async (memo) => send([memoIx(memo, agent)]),
    async tx(signature) {
      const res = (await rpc.getTransaction(signature as never, { encoding: "jsonParsed", commitment: "confirmed", maxSupportedTransactionVersion: 0 }).send()) as unknown as {
        meta: { err: unknown; preTokenBalances: TokenBalance[]; postTokenBalances: TokenBalance[] };
        transaction: { message: { instructions: { programId: string; parsed?: unknown }[] } };
      } | null;
      if (!res) return null;
      const memos = res.transaction.message.instructions.filter((i) => i.programId === MEMO_PROGRAM && typeof i.parsed === "string").map((i) => i.parsed as string);
      return { err: res.meta.err, memos, moves: tokenMoves(res.meta.preTokenBalances, res.meta.postTokenBalances) };
    },
  };
}

interface TokenBalance { mint: string; owner?: string; uiTokenAmount: { amount: string } }

export function tokenMoves(pre: TokenBalance[], post: TokenBalance[]): { owner: string; delta: bigint }[] {
  const sum = (rows: TokenBalance[]) => {
    const m = new Map<string, bigint>();
    for (const r of rows) if (r.mint === SOLANA_DEVNET_USDC && r.owner) m.set(r.owner, (m.get(r.owner) ?? 0n) + BigInt(r.uiTokenAmount.amount));
    return m;
  };
  const a = sum(pre), b = sum(post);
  return [...new Set([...a.keys(), ...b.keys()])]
    .map((owner) => ({ owner, delta: (b.get(owner) ?? 0n) - (a.get(owner) ?? 0n) }))
    .filter((m) => m.delta !== 0n);
}

// ---------- the app ----------

export interface Limits { maxJobs: number; maxPerDay: number; maxPerIpHour: number }

export interface Check { name: string; ok: boolean | null; detail: string }

export class FieldDaySettle {
  private queue: Promise<unknown> = Promise.resolve();
  private ipHits = new Map<string, number[]>();

  constructor(readonly ledger: Ledger, private chain: Chain, private telegraph: Telegraph, private limits: Limits,
    private now: () => number = Date.now) {}

  // Jobs run one at a time so budgets can't be raced and a job never runs twice at once.
  review(raw: unknown, ip: string): Promise<{ status: number; body: Record<string, unknown> }> {
    const run = this.queue.then(() => this.reviewOne(raw, ip));
    this.queue = run.catch(() => undefined);
    return run;
  }

  /** The job's state, rebuilt from the append-only ledger. */
  job(id: string): Record<string, unknown> | null {
    const rows = this.ledger.rows.filter((r) => r.id === id);
    if (!rows.length) return null;
    const out: Record<string, unknown> = { id, sources: [] as unknown[] };
    for (const r of rows) {
      if (r.type === "attempt" && r.step === "book") Object.assign(out, { operation: r.operation, location: r.location, date: r.date, contractor: r.contractor, fee_units: r.fee_units, state: "booking" });
      if (r.type === "outcome" && r.kind === "booked") Object.assign(out, { state: "booked", deposit_tx: r.tx });
      if (r.type === "outcome" && r.kind === "source") (out.sources as unknown[]).push({ role: r.role, miner_id: r.miner_id, result: r.result, signal_hash: r.signal_hash, payment_tx: r.payment_tx });
      if (r.type === "outcome" && r.kind === "failed") Object.assign(out, { error: r.reason });
      if (r.type === "outcome" && r.kind === "settled") Object.assign(out, { state: String(r.decision).toLowerCase(), decision: r.decision, text: r.text, settle_tx: r.tx, error: undefined });
    }
    return out;
  }

  jobs(): Record<string, unknown>[] {
    const ids = [...new Set(this.ledger.rows.filter((r) => r.type === "attempt" && r.step === "book").map((r) => r.id))];
    return ids.reverse().map((id) => this.job(id)!);
  }

  private async reviewOne(raw: unknown, ip: string) {
    const now = this.now();
    const job = validateJob(raw, now);
    const id = jobId(job);
    const existing = this.job(id);
    if (existing?.settle_tx) return { status: 200, body: { ...existing, reused: true } };
    const bookings = this.ledger.rows.filter((r) => r.type === "attempt" && r.step === "book");
    if (!existing) {
      if (bookings.length >= this.limits.maxJobs) return { status: 503, body: { error: "This demo's job budget is used up. Every job it ran is listed below." } };
      if (bookings.filter((r) => Date.parse(r.at) >= Math.floor(now / DAY_MS) * DAY_MS).length >= this.limits.maxPerDay) {
        return { status: 429, body: { error: "Today's job budget is used up. Try again after 00:00 UTC." } };
      }
      const hits = (this.ipHits.get(ip) ?? []).filter((t) => t > now - 3_600_000);
      if (hits.length >= this.limits.maxPerIpHour) return { status: 429, body: { error: "Too many jobs from you this hour. Try again later." } };
      this.ipHits.set(ip, [...hits, now]);
    }
    const at = () => new Date(this.now()).toISOString();
    const fail = (step: string, error: unknown) => {
      const reason = `${step}: ${(error as Error).message}`;
      this.ledger.append({ type: "outcome", id, at: at(), kind: "failed", reason });
      return { status: 502, body: { ...this.job(id), error: reason } };
    };

    // 1. Book: the fee moves into escrow. Never deposited twice: an unconfirmed booking needs a person.
    if (!existing) {
      this.ledger.append({ type: "attempt", id, at: at(), step: "book", operation: job.operation, location: job.location, date: job.date, contractor: job.contractor, fee_units: job.fee_units.toString() });
      try {
        const tx = await this.chain.deposit(job.fee_units, memoFor(id, "BOOKED", job));
        this.ledger.append({ type: "outcome", id, at: at(), kind: "booked", tx });
      } catch (error) {
        return fail("deposit", error);
      }
    } else if (!existing.deposit_tx) {
      return { status: 409, body: { ...existing, error: "This job's deposit was sent but never confirmed. Check the chain before trying again." } };
    }

    // 2. Verify twice, paying Telegraph on Solana each time. Sources already paid for are reused.
    const sources = ((this.job(id)!.sources as Source[]) ?? []).slice();
    try {
      if (!sources.some((s) => s.role === "ranked")) {
        const s = await this.telegraph.ask(job, undefined, () => this.ledger.append({ type: "attempt", id, at: at(), step: "ask-ranked" }));
        sources.push({ role: "ranked", ...s });
        this.ledger.append({ type: "outcome", id, at: at(), kind: "source", role: "ranked", ...s });
      }
    } catch (error) {
      return fail("verify", error);
    }
    if (!sources.some((s) => s.role === "cross-check")) {
      const ranked = sources.find((s) => s.role === "ranked")!;
      const errors: string[] = [];
      for (const miner of CROSS_CHECK.filter((m) => m !== ranked.miner_id)) {
        try {
          const s = await this.telegraph.ask(job, miner, () => this.ledger.append({ type: "attempt", id, at: at(), step: "ask-cross-check", miner_id: miner }));
          sources.push({ role: "cross-check", ...s });
          this.ledger.append({ type: "outcome", id, at: at(), kind: "source", role: "cross-check", ...s });
          break;
        } catch (error) {
          errors.push(`${miner}: ${(error as Error).message}`);
        }
      }
      if (!sources.some((s) => s.role === "cross-check")) return fail("verify", new Error(`no cross-check verifier answered (${errors.join("; ")})`));
    }

    // 3. Settle by rule, with the evidence in the memo.
    const ordered = [sources.find((s) => s.role === "ranked")!, sources.find((s) => s.role === "cross-check")!];
    const { decision, text } = decide(job.operation, ordered[0].result, ordered[1].result);
    const memo = memoFor(id, decision, job, ordered);
    this.ledger.append({ type: "attempt", id, at: at(), step: "settle", decision });
    try {
      const tx = decision === "RELEASE" ? await this.chain.pay(job.contractor, job.fee_units, memo)
        : decision === "REFUND" ? await this.chain.pay(this.chain.operator, job.fee_units, memo)
        : await this.chain.note(memo);
      this.ledger.append({ type: "outcome", id, at: at(), kind: "settled", decision, text, tx, memo });
    } catch (error) {
      return fail("settle", error);
    }
    return { status: 200, body: this.job(id)! };
  }

  /** Audits a settlement from its transaction alone: the chain, the memo, and Telegraph's records. */
  async audit(signature: string): Promise<{ status: number; body: Record<string, unknown> }> {
    if (!/^[1-9A-HJ-NP-Za-km-z]{80,90}$/.test(signature)) return { status: 400, body: { error: "Give a Solana transaction signature." } };
    const tx = await this.chain.tx(signature);
    if (!tx) return { status: 404, body: { error: "Devnet has no confirmed transaction with that signature." } };
    const memo = tx.memos.map(parseMemo).find(Boolean) ?? null;
    if (!memo) return { status: 422, body: { error: "That transaction carries no Field-day memo." } };
    const checks: Check[] = [{ name: "Transaction succeeded", ok: tx.err === null, detail: tx.err === null ? "confirmed, no error" : JSON.stringify(tx.err) }];
    const fee = BigInt(memo.fee);
    const usdc = (n: bigint) => `${Number(n) / 1e6} USDC`;
    const moved = (owner: string) => tx.moves.find((m) => m.owner === owner)?.delta ?? 0n;
    const agent = this.chain.agent;
    if (memo.step === "BOOKED") {
      checks.push({ name: "Fee deposited into escrow", ok: moved(agent) === fee, detail: `escrow agent ${agent} received ${usdc(moved(agent))}; the memo says ${usdc(fee)}` });
      return { status: 200, body: { signature, memo, checks } };
    }
    const src = memo.src ?? [];
    const recomputed = src.length === 2 ? decideFromReadings(memo.op, src.map((s) => s[1])) : null;
    checks.push({ name: "Decision follows the published rule", ok: recomputed === memo.step,
      detail: `readings ${src.map((s) => `${s[0]}: ${s[1] ?? "none"}`).join(", ")} give ${recomputed ?? "nothing"}; the memo says ${memo.step}` });
    if (memo.step === "HOLD") {
      checks.push({ name: "No money moved", ok: tx.moves.length === 0, detail: tx.moves.length ? JSON.stringify(tx.moves, (_k, v) => (typeof v === "bigint" ? v.toString() : v)) : "no USDC balance changed; the fee stays in escrow" });
    } else {
      const to = tx.moves.find((m) => m.delta > 0n);
      const toOk = memo.step === "REFUND" ? to?.owner === this.chain.operator : Boolean(to) && to!.owner !== this.chain.operator;
      checks.push({ name: memo.step === "RELEASE" ? "Fee paid from escrow to the contractor" : "Fee refunded from escrow to the buyer",
        ok: moved(agent) === -fee && to?.delta === fee && toOk, detail: `escrow ${usdc(moved(agent))}; ${to ? `${to.owner} +${usdc(to.delta)}` : "no recipient"}` });
    }
    for (const [miner, value, hash] of src) {
      const rec = await this.telegraph.signal(hash);
      const r = signalResult(rec);
      const theirs = reading(memo.op, r);
      checks.push(rec === null
        ? { name: `Telegraph record for ${miner}`, ok: null, detail: `${hash}: the node didn't answer; try again later` }
        : { name: `Telegraph record for ${miner}`, ok: theirs === value && r?.date === memo.date && signalMiner(rec) === miner,
            detail: `${hash}: miner ${signalMiner(rec) ?? "?"}, reading ${theirs ?? "none"} on ${String(r?.date)}; the memo says ${miner}, ${value ?? "none"} on ${memo.date}` });
    }
    return { status: 200, body: { signature, memo, checks } };
  }
}

// ---------- HTTP ----------

function send(res: ServerResponse, status: number, body: string, type: string): void {
  res.writeHead(status, {
    "Content-Type": type,
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'",
  });
  res.end(body);
}

const json = (v: unknown) => JSON.stringify(v, (_k, x) => (typeof x === "bigint" ? x.toString() : x));

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

export const BASE = "/field-day-solana";

export function handler(app: FieldDaySettle, info: Record<string, unknown>) {
  return async (req: IncomingMessage, res: ServerResponse) => {
    const url = new URL(req.url ?? "/", "http://x");
    const path = url.pathname;
    try {
      if (req.method === "GET" && (path === BASE || path === `${BASE}/`)) return send(res, 200, PAGE, "text/html; charset=utf-8");
      if (req.method === "GET" && path === `${BASE}/api/jobs`) return send(res, 200, json({ ...info, jobs: app.jobs() }), "application/json");
      if (req.method === "GET" && path === `${BASE}/api/ledger`) return send(res, 200, json({ ...info, rows: app.ledger.rows }), "application/json");
      if (req.method === "GET" && path === `${BASE}/api/audit`) {
        const out = await app.audit(url.searchParams.get("tx") ?? "");
        return send(res, out.status, json(out.body), "application/json");
      }
      if (req.method === "POST" && path === `${BASE}/api/review`) {
        if (!String(req.headers["content-type"] ?? "").startsWith("application/json")) return send(res, 415, json({ error: "Send JSON." }), "application/json");
        const ip = String(req.headers["x-real-ip"] ?? req.socket.remoteAddress ?? "unknown");
        const out = await app.review(await readBody(req), ip);
        return send(res, out.status, json(out.body), "application/json");
      }
      return send(res, 404, json({ error: "not found" }), "application/json");
    } catch (error) {
      if (error instanceof InputError) return send(res, 400, json({ error: error.message }), "application/json");
      console.error("field-day-solana error", (error as Error).message);
      return send(res, 500, json({ error: "internal error" }), "application/json");
    }
  };
}

async function signerFrom(name: string): Promise<KeyPairSigner> {
  const raw = (process.env[name] ?? "").trim();
  if (!raw) throw new Error(`${name} is required`);
  delete process.env[name];
  return createKeyPairSignerFromBytes(raw.startsWith("[") ? Uint8Array.from(JSON.parse(raw)) : base58.decode(raw));
}

function envInt(name: string, fallback: number): number {
  const v = Number(process.env[name] ?? fallback);
  if (!Number.isInteger(v) || v < 0) throw new Error(`${name} must be a non-negative integer`);
  return v;
}

export async function main(): Promise<void> {
  const operator = await signerFrom("SOLANA_PRIVATE_KEY");
  const agent = await signerFrom("FIELD_DAY_AGENT_KEY");
  if (operator.address === agent.address) throw new Error("the escrow agent must be a different wallet from the operator");
  const ledger = new Ledger(process.env.FIELD_DAY_SOLANA_LEDGER ?? "/data/field-day-solana-ledger.jsonl");
  const app = new FieldDaySettle(ledger, solanaChain(operator, agent), solanaTelegraph(operator), {
    maxJobs: envInt("FIELD_DAY_SOLANA_MAX_JOBS", 60),
    maxPerDay: envInt("FIELD_DAY_SOLANA_MAX_PER_DAY", 15),
    maxPerIpHour: envInt("FIELD_DAY_SOLANA_MAX_PER_IP_HOUR", 4),
  });
  const info = { network: SOLANA_DEVNET_NETWORK, usdc: SOLANA_DEVNET_USDC, node: NODE, intent: INTENT, operator: operator.address, escrow_agent: agent.address, cross_check: CROSS_CHECK };
  const port = envInt("FIELD_DAY_SOLANA_PORT", 8793);
  const host = process.env.FIELD_DAY_SOLANA_HOST ?? "127.0.0.1";
  createServer(handler(app, info)).listen(port, host, () => console.log(`field-day-solana on ${host}:${port}, operator ${operator.address}, escrow ${agent.address}`));
}

if (import.meta.url === `file://${process.argv[1]}`) main().catch((e) => { console.error(e.message); process.exit(1); });
