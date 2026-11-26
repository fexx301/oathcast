import vm from "node:vm";
import { describe, expect, it } from "vitest";

import { InputError, Ledger } from "../src/field-day.js";
import { PAGE } from "../src/field-day-solana-page.js";
import {
  decide, decideFromReadings, FieldDaySettle, jobId, memoFor, parseFee, parseMemo, tokenMoves, validateJob,
  type Chain, type Job, type ParsedTx, type Source, type Telegraph,
} from "../src/field-day-solana.js";

const NOW = Date.parse("2026-11-26T12:00:00Z");
const OPERATOR = "2BGoFrhXmt6sDR9N6CNfgKtepbvkjJB6TuV53Tpvv5yw";
const AGENT = "G53EbeTZSNsAn7bj6iMFUQnq3zpDdEbHhKkPRywo8bix";
const CONTRACTOR = "EdXXxivUbLJNN1qqnYrKn3T6mfUmg5E3o9i6W2GXj1Qb";
const SIG = (n: number) => "5".repeat(80) + String(n).padStart(8, "1").replace(/0/g, "1");
const HASH = (c: string) => "0x" + c.repeat(64);
const day = (mm: number | null, low = 5) => ({ date: "2026-11-20", precipitation_mm: mm, temp_min_c: low, temp_max_c: 17 });
const RAW = { operation: "spray", location: "Lleida, Spain", date: "2026-11-20", contractor: CONTRACTOR, fee: "0.02" };

// A fake chain that remembers what it sent and can replay it as parsed transactions for the audit.
function fakeChain() {
  const sent: { kind: string; to?: string; fee?: bigint; memo: string; sig: string }[] = [];
  const chain: Chain = {
    operator: OPERATOR,
    agent: AGENT,
    deposit: async (fee, memo) => { const sig = SIG(sent.length + 1); sent.push({ kind: "deposit", fee, memo, sig }); return sig; },
    pay: async (to, fee, memo) => { const sig = SIG(sent.length + 1); sent.push({ kind: "pay", to, fee, memo, sig }); return sig; },
    note: async (memo) => { const sig = SIG(sent.length + 1); sent.push({ kind: "note", memo, sig }); return sig; },
    async tx(signature): Promise<ParsedTx | null> {
      const s = sent.find((x) => x.sig === signature);
      if (!s) return null;
      const moves = s.kind === "deposit" ? [{ owner: OPERATOR, delta: -s.fee! }, { owner: AGENT, delta: s.fee! }]
        : s.kind === "pay" ? [{ owner: AGENT, delta: -s.fee! }, { owner: s.to!, delta: s.fee! }] : [];
      return { err: null, memos: [s.memo], moves };
    },
  };
  return { chain, sent };
}

function fakeTelegraph(answers: Record<string, ReturnType<typeof day>>, ranked = "64174", failCross = 0) {
  const asks: string[] = [];
  let fails = failCross;
  const sources = new Map<string, Record<string, unknown>>();
  const tg: Telegraph = {
    async ask(_job, minerId, beforePay) {
      const miner = minerId ?? ranked;
      asks.push(minerId ? `direct:${minerId}` : "ranked");
      if (minerId && fails > 0) { fails--; throw new Error("Telegraph's paid ask returned 500"); }
      beforePay();
      const hash = HASH(miner === "64174" ? "a" : "b");
      sources.set(hash, { payload: { response: { result: answers[miner] } } });
      return { miner_id: miner, result: answers[miner], signal_hash: hash, payment_tx: SIG(900 + asks.length) };
    },
    signal: async (hash) => sources.get(hash) ?? null,
  };
  return { tg, asks, sources };
}

const app = (chain: Chain, tg: Telegraph, ledger = new Ledger(null)) =>
  new FieldDaySettle(ledger, chain, tg, { maxJobs: 10, maxPerDay: 5, maxPerIpHour: 3 }, () => NOW);

describe("decision rule", () => {
  it("moves money only when both sources agree", () => {
    expect(decide("spray", day(0), day(0.4)).decision).toBe("RELEASE");
    expect(decide("spray", day(3), day(1)).decision).toBe("REFUND");
    expect(decide("spray", day(0), day(2)).decision).toBe("HOLD");
    expect(decide("spray", day(0), day(null)).decision).toBe("HOLD");
    expect(decide("spray", day(0), undefined).decision).toBe("HOLD");
    expect(decide("frost", day(5, 2), day(9, 0.5)).decision).toBe("RELEASE");
    expect(decide("frost", day(0, -1), day(0, 0)).decision).toBe("REFUND");
    expect(decide("frost", day(0, 1), day(0, -1)).decision).toBe("HOLD");
  });

  it("recomputes the same decision from a memo's readings", () => {
    expect(decideFromReadings("spray", [0, 0.9])).toBe("RELEASE");
    expect(decideFromReadings("harvest", [1, 4])).toBe("REFUND");
    expect(decideFromReadings("frost", [2, -0.5])).toBe("HOLD");
    expect(decideFromReadings("spray", [0, null])).toBe("HOLD");
  });
});

describe("inputs and memos", () => {
  it("validates the job", () => {
    const job = validateJob(RAW, NOW);
    expect(job.fee_units).toBe(20_000n);
    expect(() => validateJob({ ...RAW, contractor: "not-an-address" }, NOW)).toThrow(InputError);
    expect(() => validateJob({ ...RAW, date: "2026-11-25" }, NOW)).toThrow(InputError); // record not final yet
    expect(() => parseFee("0.5")).toThrow(InputError);
    expect(() => parseFee("0.001")).toThrow(InputError);
    expect(() => parseFee("1e-2")).toThrow(InputError);
    expect(parseFee(0.05)).toBe(50_000n);
  });

  it("writes memos the audit can read back, and rejects others", () => {
    const job = validateJob(RAW, NOW);
    const src: Source[] = [
      { role: "ranked", miner_id: "64174", result: day(0), signal_hash: HASH("a"), payment_tx: SIG(1) },
      { role: "cross-check", miner_id: "731004", result: day(0.2), signal_hash: HASH("b"), payment_tx: SIG(2) },
    ];
    const memo = memoFor(jobId(job), "RELEASE", job, src);
    expect(Buffer.byteLength(memo)).toBeLessThan(560);
    expect(parseMemo(memo)).toMatchObject({ step: "RELEASE", op: "spray", fee: "20000", src: [["64174", 0, HASH("a")], ["731004", 0.2, HASH("b")]] });
    expect(parseMemo("hello")).toBeNull();
    expect(parseMemo(JSON.stringify({ v: 1, app: "someone-else", job: "x", step: "RELEASE", op: "spray", fee: "1" }))).toBeNull();
  });

  it("gives the same job the same id", () => {
    const a = validateJob(RAW, NOW);
    expect(jobId(a)).toBe(jobId(validateJob({ ...RAW, location: "LLEIDA, SPAIN" }, NOW)));
    expect(jobId(a)).not.toBe(jobId(validateJob({ ...RAW, fee: "0.03" }, NOW)));
  });

  it("sums token moves by owner for devnet USDC only", () => {
    const usdc = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU";
    const bal = (owner: string, amount: string, mint = usdc) => ({ mint, owner, uiTokenAmount: { amount } });
    expect(tokenMoves([bal(AGENT, "50000"), bal(CONTRACTOR, "0", "other")], [bal(AGENT, "30000"), bal(CONTRACTOR, "20000"), bal(CONTRACTOR, "9", "other")]))
      .toEqual([{ owner: AGENT, delta: -20_000n }, { owner: CONTRACTOR, delta: 20_000n }]);
  });
});

describe("lifecycle", () => {
  it("books, verifies twice and releases to the contractor; a repeat pays nothing", async () => {
    const { chain, sent } = fakeChain();
    const { tg, asks } = fakeTelegraph({ "64174": day(0), "731004": day(0) });
    const a = app(chain, tg);
    const out = await a.review(RAW, "1.1.1.1");
    expect(out.status).toBe(200);
    expect(out.body).toMatchObject({ state: "release", decision: "RELEASE" });
    expect(asks).toEqual(["ranked", "direct:731004"]);
    expect(sent.map((s) => [s.kind, s.to ?? null, s.fee ?? null])).toEqual([["deposit", null, 20_000n], ["pay", CONTRACTOR, 20_000n]]);
    expect(parseMemo(sent[0].memo)?.step).toBe("BOOKED");

    const again = await a.review(RAW, "1.1.1.1");
    expect(again.body.reused).toBe(true);
    expect(sent).toHaveLength(2);
    expect(asks).toHaveLength(2);
  });

  it("refunds the buyer when both say the window failed, and holds on disagreement", async () => {
    const r = fakeChain();
    await app(r.chain, fakeTelegraph({ "64174": day(6), "731004": day(4.5) }).tg).review(RAW, "1.1.1.1");
    expect(r.sent[1]).toMatchObject({ kind: "pay", to: OPERATOR, fee: 20_000n });

    const h = fakeChain();
    const out = await app(h.chain, fakeTelegraph({ "64174": day(0), "731004": day(2) }).tg).review(RAW, "1.1.1.1");
    expect(out.body.decision).toBe("HOLD");
    expect(h.sent.map((s) => s.kind)).toEqual(["deposit", "note"]);
  });

  it("cross-checks with a different miner from the one the ranked route chose", async () => {
    const { chain } = fakeChain();
    const { tg, asks } = fakeTelegraph({ "731004": day(0), "64174": day(0) }, "731004");
    await app(chain, tg).review(RAW, "1.1.1.1");
    expect(asks).toEqual(["ranked", "direct:64174"]);
  });

  it("resumes a failed job without a second deposit or a second ranked payment", async () => {
    const { chain, sent } = fakeChain();
    const { tg, asks } = fakeTelegraph({ "64174": day(0), "731004": day(0) }, "64174", 1);
    const a = app(chain, tg);
    const first = await a.review(RAW, "1.1.1.1");
    expect(first.status).toBe(502);
    expect(first.body).toMatchObject({ state: "booked", error: expect.stringContaining("verify") });
    const second = await a.review(RAW, "1.1.1.1");
    expect(second.body.decision).toBe("RELEASE");
    expect(sent.filter((s) => s.kind === "deposit")).toHaveLength(1);
    expect(asks.filter((x) => x === "ranked")).toHaveLength(1);
  });

  it("refuses a job whose deposit never confirmed", async () => {
    const { chain, sent } = fakeChain();
    chain.deposit = async () => { throw new Error("blockhash expired"); };
    const a = app(chain, fakeTelegraph({ "64174": day(0), "731004": day(0) }).tg);
    expect((await a.review(RAW, "1.1.1.1")).status).toBe(502);
    // A failed deposit leaves an attempt with no booking: the next try must not send money again.
    const again = await a.review(RAW, "1.1.1.1");
    expect(again.status).toBe(409);
    expect(sent).toHaveLength(0);
  });

  it("caps jobs per IP per hour", async () => {
    const { chain } = fakeChain();
    const a = app(chain, fakeTelegraph({ "64174": day(0), "731004": day(0) }).tg);
    for (const fee of ["0.01", "0.02", "0.03"]) expect((await a.review({ ...RAW, fee }, "9.9.9.9")).status).toBe(200);
    expect((await a.review({ ...RAW, fee: "0.04" }, "9.9.9.9")).status).toBe(429);
  });
});

describe("audit", () => {
  async function settled(answers: Record<string, ReturnType<typeof day>>) {
    const c = fakeChain();
    const t = fakeTelegraph(answers);
    const a = app(c.chain, t.tg);
    const out = await a.review(RAW, "1.1.1.1");
    return { a, c, t, out };
  }

  it("passes a genuine release and a genuine deposit", async () => {
    const { a, c } = await settled({ "64174": day(0), "731004": day(0) });
    const rel = await a.audit(c.sent[1].sig);
    expect(rel.body.checks).toHaveLength(5);
    expect((rel.body.checks as { ok: boolean }[]).every((x) => x.ok === true)).toBe(true);
    const dep = await a.audit(c.sent[0].sig);
    expect((dep.body.checks as { ok: boolean }[]).every((x) => x.ok === true)).toBe(true);
  });

  it("fails a memo whose decision doesn't follow from its readings", async () => {
    const { a, c } = await settled({ "64174": day(0), "731004": day(2) }); // a HOLD
    const memo = JSON.parse(c.sent[1].memo);
    c.sent[1].memo = JSON.stringify({ ...memo, step: "RELEASE" });
    const out = await a.audit(c.sent[1].sig);
    expect((out.body.checks as { name: string; ok: boolean }[]).find((x) => x.name.startsWith("Decision"))?.ok).toBe(false);
  });

  it("fails a reading that doesn't match Telegraph's record, and says so when the node is down", async () => {
    const { a, c, t } = await settled({ "64174": day(0), "731004": day(0) });
    t.sources.set(HASH("b"), { payload: { response: { result: day(7) } } });
    t.sources.delete(HASH("a"));
    const checks = (await a.audit(c.sent[1].sig)).body.checks as { name: string; ok: boolean | null }[];
    expect(checks.find((x) => x.name.includes("731004"))?.ok).toBe(false);
    expect(checks.find((x) => x.name.includes("64174"))?.ok).toBeNull();
  });

  it("rejects bad input and foreign transactions", async () => {
    const { a } = await settled({ "64174": day(0), "731004": day(0) });
    expect((await a.audit("nope")).status).toBe(400);
    expect((await a.audit(SIG(77))).status).toBe(404);
  });
});

describe("page", () => {
  it("serves inline scripts that compile", () => {
    const scripts = [...PAGE.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
    expect(scripts.length).toBeGreaterThan(0);
    for (const s of scripts) expect(() => new vm.Script(s)).not.toThrow();
  });
});

export type { Job };
