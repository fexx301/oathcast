import { describe, expect, it } from "vitest";
import { privateKeyToAccount } from "viem/accounts";

import { DIAMOND } from "../src/escrow.js";
import { InputError, Ledger } from "../src/field-day.js";
import { GoldRush, keyWord, questionFor, specialistsFrom, topicsCovered } from "../src/gold-rush.js";

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
    if (url.includes("/engine/v1/signal/")) return json(200, { payload: { response: { answer: "Mount Leyshon raw answer, site 38940.", production: [] } } });
    asked.push(url);
    if (!(init?.headers as Record<string, string>)?.["X-PAYMENT"]) return json(402, CHALLENGE);
    paid++;
    const direct = url.endsWith("/64176");
    const receipt = Buffer.from(JSON.stringify({ receipt_hash: `0x${paid}`, epoch_id: 23, miner: direct ? "64176" : "20260828" })).toString("base64");
    return json(200, { signal_hash: direct ? "0x" + "ab".repeat(32) : undefined, miner_id: direct ? "64176" : "20260828", miner_name: direct ? "OathCast Queensland Gold Mines" : "Generalist",
      result: { answer: direct ? "Mount Leyshon (site 38940) is an abandoned mine; the record holds no owners or closure dates." : "Mount Leyshon was owned by a company. Mount Leyshon produced 30,000 oz. Other mines closed.", sources: ["https://example.test/a"] } },
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

  it("only credits sentences that name the mine", () => {
    expect(keyWord("MOUNT MORGAN")).toBe("MORGAN");
    expect(topicsCovered("The Mount Morgan mine history. Charters Towers was owned by many companies.", "MOUNT MORGAN").ownership).toBe(false);
    expect(topicsCovered("Mount Morgan Ltd owned the Mount Morgan mine until 1990.", "MOUNT MORGAN").ownership).toBe(true);
  });

  it("asks a clean question and drops 'near' when the sheet is the mine's own name", () => {
    expect(questionFor({ name: "MOUNT MORGAN", sheet: "MOUNT MORGAN" })).toMatch(/^Tell me about the Mount Morgan gold mine, Queensland:/);
    expect(questionFor({ name: "MOUNT LEYSHON", sheet: "CHARTERS TOWERS" })).toMatch(/Mount Leyshon gold mine near Charters Towers,/);
    expect(questionFor({ name: "MOUNT MORGAN", sheet: "MOUNT MORGAN", site_no: 494270 })).toMatch(/Mount Morgan gold mine \(site 494270\), Queensland:/);
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
    expect(answers[0].answer).toBe("Mount Leyshon raw answer, site 38940."); // the miner's own words, not the reshaped text
    expect(answers[1].shaped_answer).toBeNull(); // no signal hash: the reshaped answer is all there is
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

describe("specialist facts", () => {
  it("reads the miner's raw answer and structured fields from the signal record", async () => {
    const { structured, factTopics } = await import("../src/gold-rush.js");
    const raw = { answer: "Mount Morgan reported 7.6 Moz gold.", production: [{ commodity: "gold", amount: 7.6, unit: "Moz" }],
      closure: { year: 1990, reason_class: "economic" }, not_found: ["gold_price"], secret: "ignored" };
    const f = structured(raw);
    expect(Object.keys(f ?? {})).toEqual(["production", "closure", "not_found"]);
    expect(factTopics(f)).toEqual({ production: true, closure: true });
    expect(factTopics(structured({ ownership: [{ owner: "Mount Morgan Gold Mining Co" }], closure: [{ year: 1990 }] }))).toEqual({ ownership: true, closure: true });
    expect(structured({ answer: "x" })).toBeNull();
  });
});

describe("review fixes (Astra, 2026-11-21)", () => {
  it("never pays twice for a site and miner whose earlier payment has no settled outcome", async () => {
    const w = world();
    const ledger = new Ledger(null);
    // A paid attempt that timed out: logged before paying, then only an "unknown" outcome.
    ledger.append({ type: "attempt", at: new Date(1_800_000_000_000).toISOString(), id: "gr-1", site_no: 38940, site_name: "MOUNT LEYSHON", asked_miner: "64176", question: "q" });
    ledger.append({ type: "outcome", at: new Date(1_800_000_000_000).toISOString(), id: "gr-1", kind: "unknown", reason: "timeout" });
    const app = new GoldRush(ledger, account, { maxCalls: 10, maxPerDay: 10, maxPerIpHour: 10 }, w.fetchFn, () => 1_800_000_000_000);
    const r = await app.ask({ site_no: 38940, miner: "64176" }, "ip");
    expect(r.body.kind).toBe("unknown");
    expect(w.paidCount()).toBe(0);
    const ranked = await app.ask({ site_no: 38940, miner: "ranked" }, "ip"); // other miners are unaffected
    expect(ranked.body.kind).toBe("served");
  });

  it("counts a topic only from facts about this mine, not a neighbouring working", async () => {
    const { factTopics, aboutSite } = await import("../src/gold-rush.js");
    const facts = { ownership: [{ owner: "Brilliant Deep Levels GM Co", entity: "Brilliant Deeps" }],
      production: [{ commodity: "gold", amount: 51482.73, unit: "kg", entity: "Brilliant Lode" }], closure: [{ year: 1917, entity: "Brilliant Extended" }] };
    expect(factTopics(facts, "BRILLIANT LODE")).toEqual({ production: true });
    expect(factTopics(facts)).toEqual({ ownership: true, production: true, closure: true }); // no site name: unchanged behaviour
    expect(aboutSite({ entity: "Mount Morgan mine" }, "MOUNT MORGAN")).toBe(true);
    expect(aboutSite({}, "MOUNT MORGAN")).toBe(true);
  });

  it("drops structured fields that name a different survey site", async () => {
    const w = world();
    const fetchFn = (async (url: string, init?: RequestInit) => url.includes("/engine/v1/signal/")
      ? json(200, { payload: { response: { answer: "x", site_no: 1, ownership: [{ owner: "Someone" }] } } })
      : w.fetchFn(url, init)) as unknown as typeof fetch;
    const app = new GoldRush(new Ledger(null), account, { maxCalls: 10, maxPerDay: 10, maxPerIpHour: 10 }, fetchFn, () => 1_800_000_000_000);
    const r = await app.ask({ site_no: 38940, miner: "64176" }, "ip");
    expect(r.body.facts).toBeNull();
  });

  it("lists each active specialist once", () => {
    const dup = [...CATALOGUE, { ...CATALOGUE[0] }, { ...CATALOGUE[0], id: "65000", activation_status: "pending" }];
    expect(specialistsFrom(dup).map((m) => m.id)).toEqual(["64176"]);
  });
});

describe("delivery records", () => {
  it("checks only this app's receipts and reads recorded, pending and unknown from the node", async () => {
    const w = world();
    let reply: () => Response = () => json(200, {});
    let nodeAsks = 0;
    const fetchFn = (async (url: string, init?: RequestInit) => {
      if (url.includes("/v1/blocks/")) { nodeAsks++; return reply(); }
      return w.fetchFn(url, init);
    }) as unknown as typeof fetch;
    let t = 1_800_000_000_000;
    const app = new GoldRush(new Ledger(null), account, { maxCalls: 10, maxPerDay: 10, maxPerIpHour: 10 }, fetchFn, () => t);
    await app.ask({ site_no: 38940, miner: "64176" }, "ip"); // receipt 0x1 in the fake node: not a valid hash
    app.ledger.append({ type: "outcome", at: new Date(t).toISOString(), id: "x", kind: "served", receipt: { receipt_hash: "0x" + "cd".repeat(32), epoch_id: 29 } });
    await expect(app.delivery("nope")).rejects.toBeInstanceOf(InputError);
    expect((await app.delivery("0x" + "ef".repeat(32))).status).toBe(404); // not ours: never sent to the node
    expect(nodeAsks).toBe(0);
    reply = () => new Response("no block recorded for this epoch", { status: 404 });
    expect((await app.delivery("0x" + "cd".repeat(32))).body.status).toBe("pending");
    t += 400_000; // pending is re-asked after 5 minutes
    reply = () => json(500, {});
    expect((await app.delivery("0x" + "cd".repeat(32))).body.status).toBe("unknown");
    reply = () => json(200, { receipt_hash: "0x" + "cd".repeat(32) });
    expect((await app.delivery("0x" + "cd".repeat(32))).body.status).toBe("recorded");
    reply = () => json(500, {});
    expect((await app.delivery("0x" + "cd".repeat(32))).body.status).toBe("recorded"); // kept for good
    expect(nodeAsks).toBe(3);
  });
});

describe("step-by-step investigation", () => {
  it("plans the specialists plus the ranked route, then asks one at a time", async () => {
    const w = world();
    const app = new GoldRush(new Ledger(null), account, { maxCalls: 10, maxPerDay: 10, maxPerIpHour: 10 }, w.fetchFn, () => 1_800_000_000_000);
    const plan = await app.plan({ site_no: "38940" });
    expect(plan.status).toBe(200);
    expect((plan.body.steps as { miner: string }[]).map((s) => s.miner)).toEqual(["64176", "ranked"]);
    expect(w.paidCount()).toBe(0); // planning is free
    const one = await app.ask({ site_no: 38940, miner: "64176" }, "ip");
    expect([one.body.kind, one.body.path, one.body.miner_id]).toEqual(["served", "direct", "64176"]);
    const routed = await app.ask({ site_no: 38940, miner: "ranked" }, "ip");
    expect([routed.body.kind, routed.body.path]).toEqual(["served", "ranked route"]);
    expect(w.paidCount()).toBe(2);
  });

  it("refuses to pay a miner that isn't a current specialist", async () => {
    const w = world();
    const app = new GoldRush(new Ledger(null), account, { maxCalls: 10, maxPerDay: 10, maxPerIpHour: 10 }, w.fetchFn);
    await expect(app.ask({ site_no: 38940, miner: "9" }, "ip")).rejects.toBeInstanceOf(InputError); // in the catalogue, not a specialist
    await expect(app.ask({ site_no: 38940, miner: "../x" }, "ip")).rejects.toBeInstanceOf(InputError);
    expect(w.paidCount()).toBe(0);
  });
});
