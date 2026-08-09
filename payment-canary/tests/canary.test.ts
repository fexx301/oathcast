import { base58 } from "@scure/base";
import { describe, expect, it, vi } from "vitest";

import {
  EXPECTED_FEE_PAYER,
  EXPECTED_PAY_TO,
  LIVE_DEVNET_DISPATCHER_ORIGIN,
  SOLANA_DEVNET_NETWORK,
  SOLANA_DEVNET_USDC,
  buildTarget,
  runCanary,
} from "../src/canary.js";
import { deriveWalletFromSeed } from "../src/wallet.js";

const payerAddress = base58.encode(new Uint8Array(32).fill(3));
const transactionSignature = base58.encode(new Uint8Array(64).fill(7));
const privateKey = base58.encode(new Uint8Array(64).fill(9));

function encodeHeader(value: unknown): string {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64");
}

function challengeFor(url: string, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    x402Version: 2,
    resource: { url },
    accepts: [
      {
        scheme: "exact",
        network: SOLANA_DEVNET_NETWORK,
        asset: SOLANA_DEVNET_USDC,
        amount: "10000",
        payTo: EXPECTED_PAY_TO,
        maxTimeoutSeconds: 60,
        extra: { feePayer: EXPECTED_FEE_PAYER },
      },
    ],
    ...overrides,
  };
}

function options(execute = false) {
  return {
    dispatcherUrl: "https://dispatcher.test/miner-dispatcher",
    minerId: "18",
    endpointPath: "predict",
    operationId: "test-operation-001",
    execute,
  } as const;
}

function preflightFetch(challenge: Record<string, unknown>) {
  return vi.fn(async () =>
    new Response("", {
      status: 402,
      headers: { "PAYMENT-REQUIRED": encodeHeader(challenge) },
    }),
  );
}

describe("payment canary", () => {
  it("derives a loadable 64-byte Solana secret without exposing it as evidence", async () => {
    const wallet = await deriveWalletFromSeed(new Uint8Array(32).fill(11));
    expect(base58.decode(wallet.encodedSecret)).toHaveLength(64);
    expect(wallet.address).toMatch(/^[1-9A-HJ-NP-Za-km-z]+$/);
  });

  it("rejects HTTP unless it is the explicitly enabled pinned devnet dispatcher", () => {
    expect(() =>
      buildTarget({
        ...options(),
        dispatcherUrl: `${LIVE_DEVNET_DISPATCHER_ORIGIN}/miner-dispatcher`,
      }),
    ).toThrow();
    expect(() =>
      buildTarget({
        ...options(),
        dispatcherUrl: "http://dispatcher.test/miner-dispatcher",
        allowInsecureHttpDevnet: true,
      }),
    ).toThrow();
  });

  it("accepts only the pinned gateway's canonical prefix-free resource alias", async () => {
    const liveOptions = {
      ...options(),
      dispatcherUrl: `${LIVE_DEVNET_DISPATCHER_ORIGIN}/miner-dispatcher`,
      allowInsecureHttpDevnet: true,
      params: { lat: "6.5244", lon: "3.3792" },
    };
    const target = buildTarget(liveOptions);
    const canonicalResource = target.requestUrl.replace("/miner-dispatcher/v1/", "/v1/");
    const fetch = preflightFetch(challengeFor(canonicalResource));

    const result = await runCanary(liveOptions, { fetch });

    expect(result.ok).toBe(true);
    expect(result.evidence.preflight.challenge_validated).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("rejects a gateway resource alias whose query differs", async () => {
    const liveOptions = {
      ...options(),
      dispatcherUrl: `${LIVE_DEVNET_DISPATCHER_ORIGIN}/miner-dispatcher`,
      allowInsecureHttpDevnet: true,
      params: { lat: "6.5244", lon: "3.3792" },
    };
    const target = buildTarget(liveOptions);
    const wrongResource = target.requestUrl
      .replace("/miner-dispatcher/v1/", "/v1/")
      .replace("lat=6.5244", "lat=0");
    const createSigner = vi.fn();

    const result = await runCanary(liveOptions, {
      fetch: preflightFetch(challengeFor(wrongResource)),
      createSigner,
    });

    expect(result.ok).toBe(false);
    expect(result.evidence.error?.code).toBe("CHALLENGE_RESOURCE_MISMATCH");
    expect(createSigner).not.toHaveBeenCalled();
  });

  it("preflights without reading a key or attempting payment", async () => {
    const target = buildTarget(options());
    const fetch = preflightFetch(challengeFor(target.requestUrl));
    const createSigner = vi.fn();

    const result = await runCanary(options(), { fetch, createSigner });

    expect(result.ok).toBe(true);
    expect(result.evidence.preflight.challenge_validated).toBe(true);
    expect(result.evidence.preflight.payment_attempted).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(createSigner).not.toHaveBeenCalled();
    expect(result.evidence).not.toHaveProperty("settlement");
  });

  it("rejects a challenge mismatch before signer initialization", async () => {
    const target = buildTarget(options());
    const challenge = challengeFor(target.requestUrl, {
      accepts: [
        {
          scheme: "exact",
          network: "solana:wrong-devnet",
          asset: SOLANA_DEVNET_USDC,
          amount: "10000",
          payTo: EXPECTED_PAY_TO,
          maxTimeoutSeconds: 60,
          extra: { feePayer: EXPECTED_FEE_PAYER },
        },
      ],
    });
    const fetch = preflightFetch(challenge);
    const createSigner = vi.fn();

    const result = await runCanary(options(), { fetch, createSigner });

    expect(result.ok).toBe(false);
    expect(result.evidence.error?.code).toBe("CHALLENGE_NETWORK_MISMATCH");
    expect(result.evidence.preflight.payment_attempted).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(createSigner).not.toHaveBeenCalled();
  });

  it("fails closed on execute when SOLANA_PRIVATE_KEY is absent", async () => {
    const target = buildTarget(options(true));
    const fetch = preflightFetch(challengeFor(target.requestUrl));
    const createPaymentClient = vi.fn();

    const result = await runCanary(options(true), {
      fetch,
      env: {},
      createPaymentClient,
    });

    expect(result.ok).toBe(false);
    expect(result.evidence.error?.code).toBe("SIGNER_KEY_MISSING");
    expect(result.evidence.preflight.payment_attempted).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(createPaymentClient).not.toHaveBeenCalled();
  });

  it("executes one validated retry and verifies confirmed USDC movement", async () => {
    const target = buildTarget(options(true));
    const challenge = challengeFor(target.requestUrl);
    const settlement = {
      success: true,
      transaction: transactionSignature,
      network: SOLANA_DEVNET_NETWORK,
    };
    const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ input, init });
      if (calls.length === 1) {
        return new Response("", {
          status: 402,
          headers: { "PAYMENT-REQUIRED": encodeHeader(challenge) },
        });
      }
      return new Response('{"content":"paid"}', {
        status: 200,
        headers: { "PAYMENT-RESPONSE": encodeHeader(settlement) },
      });
    });
    const rpc = {
      getSignatureStatuses: vi.fn(() => ({
        send: async () => ({ value: [{ confirmationStatus: "confirmed", err: null }] }),
      })),
      getTransaction: vi.fn(() => ({
        send: async () => ({
          meta: {
            err: null,
            preTokenBalances: [
              { mint: SOLANA_DEVNET_USDC, owner: payerAddress, uiTokenAmount: { amount: "50000" } },
              { mint: SOLANA_DEVNET_USDC, owner: EXPECTED_PAY_TO, uiTokenAmount: { amount: "0" } },
            ],
            postTokenBalances: [
              { mint: SOLANA_DEVNET_USDC, owner: payerAddress, uiTokenAmount: { amount: "40000" } },
              { mint: SOLANA_DEVNET_USDC, owner: EXPECTED_PAY_TO, uiTokenAmount: { amount: "10000" } },
            ],
          },
          transaction: {
            signatures: [transactionSignature],
            message: { accountKeys: [{ pubkey: EXPECTED_FEE_PAYER }] },
          },
        }),
      })),
    };
    const paymentClient = {
      createPaymentPayload: vi.fn(async () => ({
        x402Version: 2,
        accepted: (challenge.accepts as unknown[])[0],
        payload: { transaction: "test-only-unsigned-fixture" },
      })),
      encodePaymentSignatureHeader: vi.fn(() => ({
        "PAYMENT-SIGNATURE": "test-only-payment-proof",
      })),
    };

    const result = await runCanary(options(true), {
      fetch,
      env: { SOLANA_PRIVATE_KEY: privateKey },
      createSigner: async () => ({ address: payerAddress } as never),
      createPaymentClient: () => paymentClient,
      createRpc: () => rpc,
    });

    expect(result.ok).toBe(true);
    expect(result.evidence.preflight.payment_attempted).toBe(true);
    expect(result.evidence.settlement?.transaction_signature).toBe(transactionSignature);
    expect(result.evidence.verification?.confirmed_transaction).toBe(true);
    expect(result.evidence.verification?.token_movement.status).toBe("verified");
    expect(fetch).toHaveBeenCalledTimes(2);
    const retryHeaders = calls[1].init?.headers as Record<string, string>;
    expect(retryHeaders["PAYMENT-SIGNATURE"]).toBe("test-only-payment-proof");
    expect(retryHeaders["Idempotency-Key"]).toBe("test-operation-001");
    expect(JSON.stringify(result.evidence)).not.toContain("test-only-payment-proof");
    expect(rpc.getSignatureStatuses).toHaveBeenCalledTimes(1);
    expect(rpc.getTransaction).toHaveBeenCalledTimes(1);
  });

  it("does not claim success when confirmed metadata shows the wrong recipient movement", async () => {
    const target = buildTarget(options(true));
    const challenge = challengeFor(target.requestUrl);
    const settlement = {
      success: true,
      transaction: transactionSignature,
      network: SOLANA_DEVNET_NETWORK,
    };
    let requestCount = 0;
    const fetch = vi.fn(async () => {
      requestCount += 1;
      return requestCount === 1
        ? new Response("", {
            status: 402,
            headers: { "PAYMENT-REQUIRED": encodeHeader(challenge) },
          })
        : new Response("paid", {
            status: 200,
            headers: { "PAYMENT-RESPONSE": encodeHeader(settlement) },
          });
    });
    const rpc = {
      getSignatureStatuses: () => ({
        send: async () => ({ value: [{ confirmationStatus: "finalized", err: null }] }),
      }),
      getTransaction: () => ({
        send: async () => ({
          meta: {
            err: null,
            preTokenBalances: [
              { mint: SOLANA_DEVNET_USDC, owner: payerAddress, uiTokenAmount: { amount: "50000" } },
              { mint: SOLANA_DEVNET_USDC, owner: EXPECTED_PAY_TO, uiTokenAmount: { amount: "0" } },
            ],
            postTokenBalances: [
              { mint: SOLANA_DEVNET_USDC, owner: payerAddress, uiTokenAmount: { amount: "40000" } },
              { mint: SOLANA_DEVNET_USDC, owner: EXPECTED_PAY_TO, uiTokenAmount: { amount: "9999" } },
            ],
          },
          transaction: {
            signatures: [transactionSignature],
            message: { accountKeys: [{ pubkey: EXPECTED_FEE_PAYER }] },
          },
        }),
      }),
    };

    const result = await runCanary(options(true), {
      fetch,
      env: { SOLANA_PRIVATE_KEY: privateKey },
      createSigner: async () => ({ address: payerAddress } as never),
      createPaymentClient: () => ({
        createPaymentPayload: async () => ({}),
        encodePaymentSignatureHeader: () => ({ "PAYMENT-SIGNATURE": "fixture-proof" }),
      }),
      createRpc: () => rpc,
    });

    expect(result.ok).toBe(false);
    expect(result.evidence.error?.code).toBe("TOKEN_BALANCE_MOVEMENT_MISMATCH");
    expect(result.evidence.preflight.payment_attempted).toBe(true);
  });
});
