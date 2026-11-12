import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { describe, expect, it } from "vitest";
import { privateKeyToAccount } from "viem/accounts";

import { DIAMOND } from "../src/escrow.js";
import { FieldDay, Ledger, validateInput, verdict } from "../src/field-day.js";

const NOW = Date.parse("2026-11-10T12:00:00Z");
const account = privateKeyToAccount("0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80");
const TEMPLATE =
  `Telegraph Escrow Payment\n\nWallet: {from}\nPay To: ${DIAMOND}\nNetwork: eip155:11155111\n` +
  "Amount: 10000 uUSDC\nResource: /v1/ask\nNonce: {nonce}\nValid After: {validAfter}\nValid Before: {validBefore}";

function json(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

// A node that challenges unpaid calls and serves paid ones as `miner`.
function node(miner = "64174", paid: () => Response | Promise<Response> = () => served(miner)) {
  const log: string[] = [];
  const fetchFn = (async (_url: string, init: RequestInit) => {
    const isPaid = Boolean((init.headers as Record<string, string>)["X-PAYMENT"]);
    log.push(isPaid ? "paid" : "challenge");
    if (!isPaid) return json(402, { accepts: [{ scheme: "escrow", network: "eip155:11155111", payTo: DIAMOND, extra: { message: TEMPLATE } }] });
    return paid();
  }) as typeof fetch;
  return { fetchFn, log };
}

function served(miner: string) {
  const receipt = { receipt_hash: "0xfeed", miner, epoch_id: 9, amount_uusdc: "10000" };
  return json(200, { miner_id: miner, intent: "WEATHER_FORECAST_VERIFY_GENERAL", signal_hash: "0xsig", result: { precipitation_mm: 0.2, temp_min_c: -1.5, temp_max_c: 12, condition: "Clear" } }, {
    "X-PAYMENT-RESPONSE": Buffer.from(JSON.stringify(receipt)).toString("base64"),
  });
}

const LIMITS = { maxCalls: 3, maxPerDay: 3, maxPerIpHour: 3 };
const input = (location = "Lleida, Spain", date = "2026-11-03", operation = "spray") => ({ operation, location, date });

describe("input validation", () => {
  it("accepts a past day and a plain place name", () => {
    expect(validateInput(input("São Paulo", "2026-11-08"), NOW)).toEqual({ operation: "spray", location: "São Paulo", date: "2026-11-08" });
  });
  it("refuses days that aren't final, too old, malformed, odd places and unknown jobs", () => {
    expect(() => validateInput(input("Lleida", "2026-11-09"), NOW)).toThrow(/two days/);
    expect(() => validateInput(input("Lleida", "2025-11-01"), NOW)).toThrow(/last year/);
    expect(() => validateInput(input("Lleida", "2026-02-30"), NOW)).toThrow(/YYYY-MM-DD/);
    expect(() => validateInput(input("<script>", "2026-11-03"), NOW)).toThrow(/place/);
    expect(() => validateInput(input("Lleida", "2026-11-03", "plough"), NOW)).toThrow(/Choose/);
  });
});

describe("verdict", () => {
  it("judges dry jobs on precipitation and frost work on the low", () => {
    expect(verdict("spray", { precipitation_mm: 0.4 }).held).toBe(true);
    expect(verdict("harvest", { precipitation_mm: 6 }).held).toBe(false);
    expect(verdict("frost", { temp_min_c: 0 }).held).toBe(false);
    expect(verdict("frost", { temp_min_c: 2.1 }).held).toBe(true);
    expect(verdict("spray", {}).held).toBeNull();
  });
});

describe("FieldDay", () => {
  it("pays once, records the receipt, and reuses the answer for the same place and day", async () => {
    const dir = mkdtempSync(join(tmpdir(), "field-day-"));
    try {
      const path = join(dir, "ledger.jsonl");
      const { fetchFn, log } = node();
      const app = new FieldDay(new Ledger(path), account, LIMITS, fetchFn, () => NOW);

      const first = await app.review(input(), "1.1.1.1");
      expect(first.status).toBe(200);
      expect(first.body).toMatchObject({ miner_id: "64174", signal_hash: "0xsig", receipt: { receipt_hash: "0xfeed", epoch_id: 9 } });
      expect((first.body.verdict as { held: boolean }).held).toBe(true);

      const again = await app.review(input("lleida, spain", "2026-11-03", "frost"), "1.1.1.1");
      expect(again.body).toMatchObject({ reused: true, operation: "frost" });
      expect((again.body.verdict as { held: boolean }).held).toBe(false);
      expect(log).toEqual(["challenge", "paid"]);

      // The ledger survives a restart and is public without IPs.
      const reloaded = new Ledger(path);
      expect(reloaded.attempts()).toBe(1);
      expect(reloaded.public()[0]).toMatchObject({ kind: "served", miner_id: "64174" });
      expect(readFileSync(path, "utf8")).not.toContain("1.1.1.1");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("stops at the total cap and the per-IP cap", async () => {
    const { fetchFn } = node();
    const app = new FieldDay(new Ledger(null), account, { maxCalls: 2, maxPerDay: 5, maxPerIpHour: 1 }, fetchFn, () => NOW);
    expect((await app.review(input("A1"), "ip1")).status).toBe(200);
    expect((await app.review(input("A2"), "ip1")).status).toBe(429);
    expect((await app.review(input("A3"), "ip2")).status).toBe(200);
    expect((await app.review(input("A4"), "ip3")).status).toBe(503);
  });

  it("counts a dropped paid call as unknown and never resends it", async () => {
    const { fetchFn, log } = node("64174", () => {
      throw new Error("socket hang up");
    });
    const ledger = new Ledger(null);
    const app = new FieldDay(ledger, account, LIMITS, fetchFn, () => NOW);
    const out = await app.review(input(), "ip");
    expect(out.status).toBe(502);
    expect(log).toEqual(["challenge", "paid"]);
    expect(ledger.attempts()).toBe(1);
    expect(ledger.public()[0]).toMatchObject({ kind: "unknown" });
  });

  it("records a refused paid call as not charged", async () => {
    const { fetchFn } = node("64174", () => json(402, { reason: "insufficient escrow for delivery" }));
    const ledger = new Ledger(null);
    const out = await new FieldDay(ledger, account, LIMITS, fetchFn, () => NOW).review(input(), "ip");
    expect(out.status).toBe(502);
    expect(ledger.public()[0]).toMatchObject({ kind: "paid_refusal", charged: false });
  });

  it("does not pay for invalid input", async () => {
    const { fetchFn, log } = node();
    const out = await new FieldDay(new Ledger(null), account, LIMITS, fetchFn, () => NOW).review(input("Lleida", "2026-11-10"), "ip");
    expect(out.status).toBe(400);
    expect(log).toEqual([]);
  });
});
