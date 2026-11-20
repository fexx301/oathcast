import { describe, expect, it } from "vitest";
import { privateKeyToAccount } from "viem/accounts";

import { DIAMOND } from "../src/escrow.js";
import { InputError, Ledger } from "../src/field-day.js";
import { GoldRush, questionFor, specialistsFrom, topicsCovered } from "../src/gold-rush.js";

// Throwaway key from the viem/anvil test set; never funded.
const account = privateKeyToAccount("0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80");
const TEMPLATE = `Telegraph Escrow Payment\n\nWallet: {from}\nPay To: ${DIAMOND}\nNetwork: eip155:11155111\n` +
  "Amount: 10000 uUSDC\nResource: /v1/ask\nNonce: {nonce}\nValid After: {validAfter}\nValid Before: {validBefore}";
const CHALLENGE = { accepts: [{ scheme: "escrow", network: "eip155:11155111", payTo: DIAMOND, extra: { message: TEMPLATE } }] };

const CATALOGUE = [
  { id: "64176", name: "OathCast Queensland Gold Mines", description: "Queensland historical gold mines", supported_intents: ["RESEARCH_QUERY_GENERAL"], activation_status: "active" },
  { id: "7", name: "Hashprice", description: "Bitcoin mining hashprice", supported_intents: ["RESEARCH_QUERY_GENERAL"], activation_status: "active" },
  { id: "8", name: "Gold Prices", description: "historical gold price per ounce", supported_intents: ["RESEARCH_QUERY_GENERAL"], activation_status: "rejected" },
  { id: "9", name: "Scholar", description: "papers from OpenAlex", supported_intents: ["RESEARCH_QUERY_GENERAL"], activation_status: "active" },
];
const GSQ_ROWS = { features: [{ attributes: { site_no: 38940, occur_name: "MOUNT LEYSHON", sheet_100_name: "CHARTERS TOWERS", latitude: -20.29, longitude: 146.27, all_commodities: "Au(l);Ag(s)" } }] };

function json(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

// Fake survey, catalogue and node. Records every node URL asked.
function world() {
  const asked: string[] = [];
  let paid = 0;
  const fetchFn = (async (url: string, init?: RequestInit) => {
    if (url.includes("arcgis")) return json(200, GSQ_ROWS);
    if (url.includes("/api/miners")) return json(200, CATALOGUE);
    asked.push(url);
    if (!(init?.headers as Record<string, string>)?.["X-PAYMENT"]) return json(402, CHALLENGE);
    paid++;
    const direct = url.endsWith("/64176");
    const receipt = Buffer.from(JSON.stringify({ receipt_hash: `0x${paid}`, epoch_id: 23, miner: direct ? "64176" : "20260828" })).toString("base64");
    return json(200, { miner_id: direct ? "64176" : "20260828", miner_name: direct ? "OathCast Queensland Gold Mines" : "Generalist",
      result: { answer: direct ? "Mount Leyshon (site 38940) is an abandoned mine; the record holds no owners or closure dates." : "Owned by a company; produced 30,000 oz.", sources: ["https://example.test/a"] } },
    { "X-PAYMENT-RESPONSE": receipt });
  }) as unknown as typeof fetch;
  return { fetchFn, asked, paidCount: () => paid };
}

describe("gold rush", () => {
  it("picks specialists by catalogue wording, skipping crypto mining and rejected miners", () => {
    expect(specialistsFrom(CATALOGUE).map((m) => m.id)).toEqual(["64176"]);
  });

  it("checks which parts of the question an answer touched", () => {
    expect(topicsCovered("Owned by Mount Leyshon Gold Mines Ltd; produced 3.4 Moz; closed in 2002")).toEqual({
      ownership: true, production: true, closure: true, "gold price": false });
    expect(topicsCovered("gold classed large, 50-150 t").production).toBe(false); // a size band is not production
    expect(topicsCovered("Site 38940 is abandoned. The record gives a size band, not production figures, and holds no owners or closure dates."))
      .toEqual({ ownership: false, production: false, closure: false, "gold price": false });
  });

  it("keeps the question within the intent's 256 bytes", () => {
    expect(questionFor({ name: "X".repeat(300), sheet: "Y" }).length).toBeLessThanOrEqual(256);
  });

  it("asks each specialist directly plus the ranked route, logs before paying, and reuses paid answers", async () => {
    const w = world();
    const ledger = new Ledger(null);
    const app = new GoldRush(ledger, account, { maxCalls: 10, maxPerDay: 10, maxPerIpHour: 10 }, w.fetchFn, () => 1_800_000_000_000);
    const r = await app.investigate({ site_no: 38940 }, "1.2.3.4");
    expect(r.status).toBe(200);
    const answers = r.body.answers as Record<string, unknown>[];
    expect(answers.map((a) => [a.path, a.kind, a.miner_id])).toEqual([["direct", "served", "64176"], ["ranked route", "served", "20260828"]]);
    expect(w.asked.filter((u) => u.endsWith("/engine/v1/ask/64176")).length).toBe(2); // 402 then paid
    expect(w.asked.filter((u) => u.endsWith("/engine/v1/ask")).length).toBe(2);
    expect((r.body.covered as Record<string, string[]>).ownership).toEqual(["Generalist"]);
    expect(ledger.attempts()).toBe(2);
    expect(w.paidCount()).toBe(2);
    const again = await app.investigate({ site_no: 38940 }, "1.2.3.4");
    expect((again.body.answers as Record<string, unknown>[]).every((a) => a.reused)).toBe(true);
    expect(w.paidCount()).toBe(2);
  });

  it("stops at the caps before signing", async () => {
    const w = world();
    const app = new GoldRush(new Ledger(null), account, { maxCalls: 1, maxPerDay: 10, maxPerIpHour: 10 }, w.fetchFn, () => 1_800_000_000_000);
    const r = await app.investigate({ site_no: 38940 }, "1.2.3.4");
    const kinds = (r.body.answers as Record<string, unknown>[]).map((a) => a.kind);
    expect(kinds).toEqual(["served", "skipped"]);
    expect(w.paidCount()).toBe(1);
  });

  it("rejects unknown or malformed sites without paying", async () => {
    const w = world();
    const app = new GoldRush(new Ledger(null), account, { maxCalls: 10, maxPerDay: 10, maxPerIpHour: 10 }, w.fetchFn);
    await expect(app.investigate({ site_no: "1; drop" }, "ip")).rejects.toBeInstanceOf(InputError);
    await expect(app.investigate({ site_no: 1 }, "ip")).rejects.toThrow("No Queensland gold working");
    expect(w.paidCount()).toBe(0);
  });
});
