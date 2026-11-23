import { describe, expect, it } from "vitest";
import { privateKeyToAccount } from "viem/accounts";

import { DIAMOND } from "../src/escrow.js";
import { InputError, Ledger } from "../src/field-day.js";
import { controllerFacts, GoldRush, keyWord, questionFor, siteHolder, specialistsFrom, takarEvents, topicsCovered } from "../src/gold-rush.js";

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

describe("specialist list", () => {
  it("never caches an empty or failed catalogue, and keeps the last good list through a node hiccup", async () => {
    let t = 1_800_000_000_000, mode: "ok" | "empty" | "error" | "down" = "empty";
    const fetchFn = (async (url: string) => {
      if (mode === "down") throw new Error("unreachable");
      if (mode === "error") return json(502, { error: "bad gateway" });
      return json(200, mode === "ok" ? CATALOGUE : []);
    }) as unknown as typeof fetch;
    const app = new GoldRush(new Ledger(null), account, { maxCalls: 10, maxPerDay: 10, maxPerIpHour: 10 }, fetchFn, () => t);
    expect(await app.specialists()).toEqual([]); // empty at first: nothing to fall back on
    mode = "ok";
    expect((await app.specialists()).map((m) => m.id)).toEqual(["64176"]); // the empty answer wasn't cached
    for (const m of ["empty", "error", "down"] as const) {
      mode = m; t += 700_000; // past the 10-minute cache
      expect((await app.specialists()).map((x) => x.id)).toEqual(["64176"]);
    }
    t += 3_600_000; // the last good list is over an hour old
    expect(await app.specialists()).toEqual([]);
  });
});

describe("gold price step", () => {
  // A catalogue where 64176 says it answers gold prices; its site answer reports a 1917 closure for this site and a 1990 one elsewhere.
  function priceWorld(closures: unknown[]) {
    const w = world();
    const asked: string[] = [];
    const fetchFn = (async (url: string, init?: RequestInit) => {
      if (url.includes("/api/miners")) return json(200, [{ ...CATALOGUE[0], description: "Queensland gold mines; also the gold price for a year" }]);
      if (url.includes("/engine/v1/signal/")) return json(200, { payload: { response: { answer: "x", closure: closures, gold_price: [{ month: "1917-01", usd_per_oz: 20.67 }] } } });
      if (init?.body) asked.push(JSON.parse(String(init.body)).params.question);
      return w.fetchFn(url, init);
    }) as unknown as typeof fetch;
    return { w, asked, fetchFn };
  }

  it("prices the site's own last recorded year, after the site answer, cached apart from it", async () => {
    const { w, asked, fetchFn } = priceWorld([{ year: 1917, entity: "Mount Leyshon" }, { year: 1990, entity: "Brilliant Extended" }]);
    const app = new GoldRush(new Ledger(null), account, { maxCalls: 10, maxPerDay: 10, maxPerIpHour: 10 }, fetchFn, () => 1_800_000_000_000);
    const plan = await app.plan({ site_no: 38940 });
    expect((plan.body.steps as { miner: string; topic?: string }[]).map((s) => [s.miner, s.topic])).toEqual([["64176", undefined], ["64176", "price"], ["ranked", undefined]]);
    const early = await app.ask({ site_no: 38940, miner: "64176", topic: "price" }, "ip"); // no site answer yet: no year
    expect(early.body.kind).toBe("skipped");
    expect(w.paidCount()).toBe(0);
    await expect(app.ask({ site_no: 38940, miner: "64176", topic: "land" }, "ip")).rejects.toBeInstanceOf(InputError); // not one of its topics
    await app.ask({ site_no: 38940, miner: "64176" }, "ip");
    const price = await app.ask({ site_no: 38940, miner: "64176", topic: "price" }, "ip");
    expect([price.body.kind, price.body.topic, price.body.question]).toEqual(["served", "price", "Gold price in 1917"]); // 1990 is another working
    expect(price.body.topics).toEqual({ "gold price": true });
    expect(asked.filter((q) => q === "Gold price in 1917").length).toBe(2); // 402, then paid
    expect(w.paidCount()).toBe(2);
    const again = await app.ask({ site_no: 38940, miner: "64176", topic: "price" }, "ip");
    expect(again.body.reused).toBe(true);
    const site = await app.ask({ site_no: 38940, miner: "64176" }, "ip");
    expect([site.body.reused, site.body.topic]).toEqual([true, undefined]);
    expect(w.paidCount()).toBe(2);
  });

  it("asks NFT KING's miner the land and rights questions, keeps their rows, and counts each as its own topic", async () => {
    const asked: string[] = [];
    const w = world();
    const fetchFn = (async (url: string, init?: RequestInit) => {
      if (url.includes("/api/miners")) return json(200, [...CATALOGUE, { id: "7310050", name: "Gold Rush Queensland Mine History", description: "Queensland gold mines history", supported_intents: ["RESEARCH_QUERY_GENERAL"], activation_status: "active" }]);
      if (url.includes("/engine/v1/signal/")) return json(200, { payload: { response: { answer: "x", tenure: [{ holder: "Citigold", permit: "ML 10208", checked_at: "2026-11-21" }], events: [{ year: 1917, what: "flood" }] } } });
      if (init?.body) asked.push(JSON.parse(String(init.body)).params.question);
      if (url.endsWith("/ask/7310050") && (init?.headers as Record<string, string>)?.["X-PAYMENT"]) {
        const r = await w.fetchFn(url.replace("7310050", "64176"), init);
        return r;
      }
      return w.fetchFn(url, init);
    }) as unknown as typeof fetch;
    const app = new GoldRush(new Ledger(null), account, { maxCalls: 20, maxPerDay: 20, maxPerIpHour: 20 }, fetchFn, () => 1_800_000_000_000);
    const plan = await app.plan({ site_no: 38940 });
    expect((plan.body.steps as { miner: string; topic?: string }[]).filter((s) => s.miner === "7310050").map((s) => s.topic))
      .toEqual([undefined, "price", "price_real", "land"]);
    const t = await app.ask({ site_no: 38940, miner: "7310050", topic: "land" }, "ip");
    expect(asked).toContain("Land and rights at Mount Leyshon (site 38940)");
    expect(t.body.topics).toEqual({ "land and rights": true });
    expect(Object.keys(t.body.facts as object)).toEqual(["tenure", "events"]); // a new field (events) is kept too
  });

  it("trims an oversized answer: diagnostics first, then rows over the site point kept ahead of nearby ones", async () => {
    const { structured } = await import("../src/gold-rush.js");
    const row = (i: number, relation: string) => ({ record_id: `EPM ${i}`, relation, holders: ["X"], source_url: "https://example.test/" + "q".repeat(900) });
    const raw = { answer: "x", checks: [{ big: "c".repeat(6000) }],
      tenure: [...Array.from({ length: 28 }, (_, i) => row(i, "within_buffer")), row(100, "contains_site_point"), row(101, "contains_site_point")] };
    const f = structured(raw) as { tenure: { record_id: string }[]; omitted: Record<string, number>; checks?: unknown };
    expect(f.checks).toBeUndefined();
    expect(f.tenure.slice(0, 2).map((t) => t.record_id)).toEqual(["EPM 100", "EPM 101"]);
    expect(f.tenure.length + f.omitted.tenure).toBe(30);
    const dup = structured({ tenure: [row(1, "contains_site_point"), row(1, "contains_site_point"), row(2, "within_buffer")] }) as { tenure: unknown[] };
    expect(dup.tenure.length).toBe(2); // one row per record_id
    expect(JSON.stringify(f).length).toBeLessThanOrEqual(24_576);
  });

  it("refuses price questions to a miner that doesn't answer them", async () => {
    const w = world();
    const app = new GoldRush(new Ledger(null), account, { maxCalls: 10, maxPerDay: 10, maxPerIpHour: 10 }, w.fetchFn);
    await expect(app.ask({ site_no: 38940, miner: "64176", topic: "price" }, "ip")).rejects.toBeInstanceOf(InputError);
    expect(w.paidCount()).toBe(0);
  });
});

describe("who controls it today", () => {
  const NFT = { id: "7310050", name: "Gold Rush Queensland Mine History", description: "Queensland gold mines history", supported_intents: ["RESEARCH_QUERY_GENERAL"], activation_status: "active" };
  const LAND = { answer: "land", tenure: [
    { layer: "Exploration permit for mineral (EPM)", holders: ["CITIGOLD CORPORATION LIMITED"], relation: "contains_site_point" },
    { layer: "Mining lease (ML)", holders: ["OTHER MINING PTY LTD"], relation: "within_buffer" },
    { layer: "Mining lease (ML)", holders: ["CITIGOLD CORPORATION LIMITED "], relation: "contains_site_point" }] };
  const IDENTITY = { found: true, legal_name: "CITIGOLD CORPORATION LIMITED", lei: "529900C4YHWJ2XJ8A350", jurisdiction: "AU", status: "ACTIVE", headquarters: "Fortitude Valley, AU", source: "the Global LEI Index" };
  const SCREEN = { label: "CLEAR", name: "CITIGOLD CORPORATION LIMITED", screened_at: "2026-11-23T10:37:09Z", lists: [{ regime: "US", published_at: "2026-11-09T19:36:18Z" }, { regime: "UK", published_at: "2026-11-10" }] };

  it("reads the screen's list dates in the shape Telegraph delivers and in the miner's own shape", () => {
    const viaNode = { label: "CLEAR", name: "X", list_versions: { EU: "2026-10-23", UK: "2026-11-10", UN: "2026-11-12", US: "2026-11-09" },
      lists: ["US OFAC SDN and non-SDN Consolidated lists (published 2026-11-09)"] };
    const lists = (raw: Record<string, unknown>) => ((controllerFacts("screen", raw, "X") as { controller_screen: { lists: string }[] }).controller_screen[0].lists);
    expect(lists(viaNode)).toMatch(/^US 2026-11-09, UN 2026-11-12, EU 2026-10-23, UK 2026-11-10 \(/);
    expect(lists({ label: "CLEAR", lists: ["UK Sanctions List (published 2026-11-10)"] })).toMatch(/^UK Sanctions List \(published 2026-11-10\) \(/);
    expect(lists(SCREEN)).toMatch(/^US 2026-11-09, UK 2026-11-10 \(/);
  });

  it("takes the holder over the site point, mining leases first", () => {
    expect(siteHolder(LAND)).toBe("CITIGOLD CORPORATION LIMITED");
    expect(siteHolder({ tenure: [{ holders: ["NEARBY LTD"], relation: "within_buffer" }] })).toBeNull();
    expect(siteHolder(null)).toBeNull();
  });

  it("asks 731005 for the holder's registered entity, then 731002 to screen that entity, each on its own intent with its canonical params, only after a land answer", async () => {
    const bodies: { url: string; body: { intent: string; params: Record<string, unknown> } }[] = [];
    let n = 0;
    const SIG: Record<string, unknown> = {};
    const fetchFn = (async (url: string, init?: RequestInit) => {
      if (url.includes("arcgis")) return json(200, GSQ_ROWS);
      if (url.includes("/api/miners")) return json(200, [...CATALOGUE, NFT]);
      if (url.includes("/engine/v1/signal/")) return json(200, { payload: { response: SIG[url.split("/").pop()!] } });
      if (!(init?.headers as Record<string, string>)?.["X-PAYMENT"]) return json(402, CHALLENGE);
      const body = JSON.parse(String(init!.body));
      bodies.push({ url, body });
      const id = url.split("/").pop()!, hash = "0x" + (++n).toString(16).padStart(64, "0");
      SIG[hash] = id === "731005" ? IDENTITY : id === "731002" ? SCREEN : body.params.question?.startsWith("Land") ? LAND : { answer: "x" };
      const receipt = Buffer.from(JSON.stringify({ receipt_hash: `0x${n}`, epoch_id: 44, miner: id })).toString("base64");
      return json(200, { signal_hash: hash, miner_id: id, miner_name: id, result: { answer: "ok" } }, { "X-PAYMENT-RESPONSE": receipt });
    }) as unknown as typeof fetch;
    const app = new GoldRush(new Ledger(null), account, { maxCalls: 20, maxPerDay: 20, maxPerIpHour: 20 }, fetchFn, () => 1_800_000_000_000);
    const steps = (await app.plan({ site_no: 38940 })).body.steps as { miner: string; topic?: string }[];
    expect(steps.map((s) => s.topic ?? s.miner).slice(-4)).toEqual(["land", "controller", "screen", "ranked"]);

    const early = await app.ask({ site_no: 38940, miner: "731005", topic: "controller" }, "ip");
    expect(early.body.kind).toBe("skipped"); // no land answer yet: nobody to look up, nothing paid
    expect(bodies).toHaveLength(0);
    await expect(app.ask({ site_no: 38940, miner: "731005", topic: "screen" }, "ip")).rejects.toBeInstanceOf(InputError);

    await app.ask({ site_no: 38940, miner: "7310050", topic: "land" }, "ip");
    const who = await app.ask({ site_no: 38940, miner: "731005", topic: "controller" }, "ip");
    expect(bodies[1]).toMatchObject({ url: expect.stringMatching(/\/ask\/731005$/), body: { intent: "B2B_IDENTITY_ENRICHMENT_GENERAL", params: { company: "CITIGOLD CORPORATION LIMITED" } } });
    expect(who.body.topics).toEqual({ "who controls it today": true });
    expect((who.body.facts as { controller_identity: Record<string, unknown>[] }).controller_identity[0])
      .toMatchObject({ record_id: "LEI 529900C4YHWJ2XJ8A350", status: "ACTIVE", jurisdiction: "AU", source_url: "https://search.gleif.org/#/record/529900C4YHWJ2XJ8A350" });

    const screen = await app.ask({ site_no: 38940, miner: "731002", topic: "screen" }, "ip");
    expect(bodies[2].body).toEqual({ intent: "SANCTIONS_SCREENING_MATCH_GENERAL", params: { name: "CITIGOLD CORPORATION LIMITED" } }); // the intent's canonical params only
    expect((screen.body.facts as { controller_screen: Record<string, unknown>[] }).controller_screen[0]).toMatchObject({ status: "CLEAR", lists: "US 2026-11-09, UK 2026-11-10 (official lists as published; not ownership or adverse media)" });

    const again = await app.ask({ site_no: 38940, miner: "731005", topic: "controller" }, "ip");
    expect(again.body.reused).toBe(true); // reused, not paid twice
    expect(bodies).toHaveLength(3);
  });
});

describe("cross-checks", () => {
  it("links another team's dossier by site number, with only well-formed slugs, and keeps the last good list on failure", async () => {
    let up = true;
    const fetchFn = (async (url: string) => {
      if (!url.includes("takar")) throw new Error("unexpected " + url);
      if (!up) throw new Error("down");
      return json(200, [{ slug: "mount-morgan", site_no: 494270 }, { slug: "bad\"><script>", site_no: 1 }, { slug: "x", site_no: "2" }]);
    }) as unknown as typeof fetch;
    let t = 1_800_000_000_000;
    const app = new GoldRush(new Ledger(null), account, { maxCalls: 1, maxPerDay: 1, maxPerIpHour: 1 }, fetchFn, () => t);
    expect(await app.crossChecks()).toEqual({ "494270": "https://takar-telegraph.vercel.app/goldrush.html#mount-morgan" });
    up = false; t += 4_000_000;
    expect(Object.keys(await app.crossChecks())).toEqual(["494270"]);
  });
});

describe("Takar events and people", () => {
  const FILE = { title: "Mount Leyshon: events", gaps: ["No accident is recorded."], people: ["A. Person"], capsules: [
    { finding: { year: 1928, types: ["restart"], people: ["Boyd"], quote: "  Work restarted\n in 1928. ", status: "recorded (one register entry)",
      source: { register: "Queensland Heritage Register", record_id: "600751", entry: "Mount Morgan Mine", url: "https://apps.des.qld.gov.au/heritage-register/detail/?id=600751" } },
      attestation: { signature: "sig" } },
    { finding: { year: "not a year", types: "fire", quote: "x".repeat(900), source: { url: "javascript:alert(1)" } } },
    { finding: { year: 1900, quote: "" } }] };

  it("keeps only safe, bounded fields from another team's file", () => {
    const out = takarEvents(FILE, 38940, "https://takar.test/events.json") as { events: Record<string, unknown>[]; gaps: string[] };
    expect(out.events).toHaveLength(2); // the one with no quote is dropped
    expect(out.events[0]).toMatchObject({ year: 1928, types: ["restart"], quote: "Work restarted in 1928.", record_id: "600751", signed: true,
      url: "https://apps.des.qld.gov.au/heritage-register/detail/?id=600751" });
    expect(out.events[1]).toMatchObject({ year: null, types: [], url: null, signed: false });
    expect((out.events[1].quote as string).length).toBe(600);
    expect(out.gaps).toEqual(["No accident is recorded."]);
    expect(takarEvents("garbage", 1, "u")).toMatchObject({ available: true, events: [], gaps: [] });
  });

  it("finds the file by Takar's dossier slug and keeps the last good copy when a refresh fails", async () => {
    let t = 1_800_000_000_000, down = false;
    const fetchFn = (async (url: string) => {
      if (url.includes("arcgis")) return json(200, GSQ_ROWS);
      if (url.endsWith("/dossiers/index.json")) return json(200, [{ site_no: 38940, slug: "mount-leyshon" }]);
      if (down) throw new Error("unreachable");
      if (url.endsWith("/receipts/events-mount-leyshon.json")) return json(200, FILE);
      return json(404, {});
    }) as unknown as typeof fetch;
    const app = new GoldRush(new Ledger(null), account, { maxCalls: 1, maxPerDay: 1, maxPerIpHour: 1 }, fetchFn, () => t);
    const first = await app.events({ site_no: 38940 });
    expect(first.body).toMatchObject({ available: true, source_url: "https://takar-telegraph.vercel.app/receipts/events-mount-leyshon.json" });
    down = true; t += 3_700_000;
    expect((await app.events({ site_no: 38940 })).body).toEqual(first.body);
    await expect(app.events({ site_no: "x" })).rejects.toBeInstanceOf(InputError);
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
