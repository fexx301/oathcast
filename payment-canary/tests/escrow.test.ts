import { describe, expect, it } from "vitest";
import { privateKeyToAccount } from "viem/accounts";
import { recoverMessageAddress } from "viem";

import { DIAMOND, OutcomeUnknown, escrowAsk, escrowOffer, fillTemplate } from "../src/escrow.js";

// Throwaway key from the viem/anvil test set; never funded.
const account = privateKeyToAccount("0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80");

const TEMPLATE =
  `Telegraph Escrow Payment\n\nWallet: {from}\nPay To: ${DIAMOND}\nNetwork: eip155:11155111\n` +
  "Amount: 10000 uUSDC\nResource: /v1/ask\nNonce: {nonce}\nValid After: {validAfter}\nValid Before: {validBefore}";

function challenge(overrides: Record<string, unknown> = {}, message = TEMPLATE) {
  return {
    accepts: [
      { scheme: "exact", network: "eip155:11155111", payTo: "0xother" },
      { scheme: "escrow", network: "eip155:11155111", payTo: DIAMOND.toUpperCase().replace("0X", "0x"), extra: { message }, ...overrides },
    ],
  };
}

function json(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

describe("escrow offer pins", () => {
  it("accepts the live challenge shape", () => {
    expect(escrowOffer(challenge()).value).toBe("10000");
  });
  it("refuses another network, another payee, or a price above the cap", () => {
    expect(() => escrowOffer(challenge({ network: "eip155:1" }))).toThrow(/network/);
    expect(() => escrowOffer(challenge({ payTo: "0x0000000000000000000000000000000000000001" }))).toThrow(/payTo/);
    expect(() => escrowOffer(challenge({}, TEMPLATE.replace("10000 uUSDC", "20000 uUSDC")))).toThrow(/cap/);
    expect(() => escrowOffer({ accepts: [] })).toThrow(/escrow/);
  });
});

describe("escrowAsk", () => {
  it("signs the template verbatim with a lowercase address and sends one X-PAYMENT", async () => {
    const calls: RequestInit[] = [];
    let counted = 0;
    const receipt = { receipt_hash: "0xabc", miner: "64174", epoch_id: 7, amount_uusdc: "10000" };
    const fetchFn = (async (_url: string, init: RequestInit) => {
      calls.push(init);
      if (calls.length === 1) return json(402, challenge());
      return json(200, { miner_id: "64174", result: { precipitation_mm: 0 } }, {
        "X-PAYMENT-RESPONSE": Buffer.from(JSON.stringify(receipt)).toString("base64"),
      });
    }) as typeof fetch;

    const out = await escrowAsk({
      node: "https://node.test",
      signer: account,
      body: { intent: "WEATHER_FORECAST_VERIFY_GENERAL", params: { location: "Lleida", date: "2026-11-03" } },
      fetchFn,
      now: () => 1_800_000_000_000,
      nonce: () => "n-1",
      beforePay: () => counted++,
    });

    expect(out.kind).toBe("served");
    if (out.kind === "served") expect(out.receipt?.miner).toBe("64174");
    expect(counted).toBe(1);
    const header = (calls[1].headers as Record<string, string>)["X-PAYMENT"];
    const payment = JSON.parse(Buffer.from(header, "base64").toString());
    const a = payment.payload.authorization;
    expect(a.from).toBe(account.address.toLowerCase());
    expect(a.to).toBe(DIAMOND);
    expect(Number(a.validBefore) - Number(a.validAfter)).toBeLessThanOrEqual(120);
    const signed = fillTemplate(TEMPLATE, a);
    expect(signed).toContain(`Wallet: ${account.address.toLowerCase()}`);
    expect(await recoverMessageAddress({ message: signed, signature: payment.payload.signature })).toBe(account.address);
    expect(calls[1].body).toBe(calls[0].body);
  });

  it("does not sign when the unpaid ask is refused (bad params cost nothing)", async () => {
    let calls = 0;
    const fetchFn = (async () => {
      calls++;
      return json(400, { error: "invalid_params" });
    }) as unknown as typeof fetch;
    const out = await escrowAsk({ node: "https://node.test", signer: account, body: {}, fetchFn });
    expect(out).toMatchObject({ kind: "unpaid_refusal", status: 400, reason: "invalid_params" });
    expect(calls).toBe(1);
  });

  it("reports a paid refusal without a receipt as not charged", async () => {
    let calls = 0;
    const fetchFn = (async () =>
      ++calls === 1 ? json(402, challenge()) : json(402, { reason: "insufficient escrow for delivery: committed=0" })) as unknown as typeof fetch;
    const out = await escrowAsk({ node: "https://node.test", signer: account, body: {}, fetchFn });
    expect(out).toMatchObject({ kind: "paid_refusal", status: 402 });
  });

  it("turns a dropped paid request into OutcomeUnknown", async () => {
    let calls = 0;
    const fetchFn = (async () => {
      if (++calls === 1) return json(402, challenge());
      throw new Error("socket hang up");
    }) as unknown as typeof fetch;
    await expect(escrowAsk({ node: "https://node.test", signer: account, body: {}, fetchFn })).rejects.toBeInstanceOf(OutcomeUnknown);
  });
});
