import { mkdtempSync, rmSync, statSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createConnection } from "node:net";
import { describe, expect, it } from "vitest";
import { base58 } from "@scure/base";

import {
  ApplicationPaymentSidecar,
  type SidecarConfig,
} from "../src/application-sidecar.js";
import { buildTarget, type CanaryResult } from "../src/canary.js";
import { createHash } from "node:crypto";

function config(directory: string): SidecarConfig {
  return {
    socketPath: join(directory, "payment.sock"),
    journalPath: join(directory, "payments.sqlite3"),
    authToken: "sidecar-token-" + "x".repeat(24),
    dispatcherUrl: "https://dispatcher.test/miner-dispatcher",
    rpcUrl: "https://api.devnet.solana.com",
    allowedMinerIds: ["212"],
    allowedEndpoints: ["forecast"],
    maxAmountMicroUsdc: 10_000n,
    maxTotalMicroUsdc: 10_000n,
    maxRequests: 1,
    allowInsecureHttpDevnet: false,
  };
}

function fingerprint(payload: {
  principal_id: string;
  idempotency_key: string;
  miner_id: string;
  endpoint: string;
  params: Record<string, string>;
}): string {
  const canonical = JSON.stringify({
    endpoint: payload.endpoint,
    idempotency_key: payload.idempotency_key,
    miner_id: payload.miner_id,
    params: payload.params,
    principal_id: payload.principal_id,
    version: 1,
  });
  return createHash("sha256").update(canonical).digest("hex");
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(",")}}`;
}

function hashText(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function evidence(ok: boolean, operationId: string): CanaryResult {
  return {
    ok,
    evidence: {
      evidence_version: "oathcast.payment-canary.v1",
      ok,
      mode: "execute",
      operation_id: operationId,
      target: {
        miner_id: "212",
        endpoint_path: "forecast",
        request_url_sha256: "c".repeat(64),
      },
      preflight: {
        status: 402,
        challenge_sha256: "d".repeat(64),
        challenge_validated: true,
        selected: {
          network: "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1",
          asset: "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
          amount: "10000",
          pay_to: "G53EbeTZSNsAn7bj6iMFUQnq3zpDdEbHhKkPRywo8bix",
          fee_payer: "2wKupLR9q6wXYppw8Gr2NvWxKBUqm4PPJKkQfoxHDBg4",
        },
        payment_attempted: ok,
      },
      settlement: ok
        ? {
            header_sha256: "e".repeat(64),
            transaction_signature: "fixture-transaction",
            success: true,
            network: "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1",
          }
        : undefined,
      verification: ok ? ({ confirmed_transaction: true } as never) : undefined,
    },
    ...(ok
      ? {
          paid_response_status: 200,
          paid_response_body_text: '{"probability":0.8}',
          paid_response_body: { probability: 0.8 },
          paid_response_body_sha256: "f".repeat(64),
        }
      : {}),
  };
}

describe("application payment sidecar", () => {
  it("keeps auth failures and idempotent replay inside the private boundary", async () => {
    const directory = mkdtempSync(join(tmpdir(), "oathcast-sidecar-"));
    let executeCalls = 0;
    try {
      const sidecarConfig = config(directory);
      const sidecar = new ApplicationPaymentSidecar(sidecarConfig, {
        buildTarget: (() => ({
          requestUrl: "https://dispatcher.test/miner-dispatcher/v1/212/forecast?q=x",
          origin: "https://dispatcher.test",
          path: "/miner-dispatcher/v1/212/forecast",
          minerId: "212",
          endpointPath: "forecast",
        })) as typeof buildTarget,
        runCanary: (async (options, dependencies) => {
          const operationId = options.operationId;
          if (!options.execute) return evidence(true, operationId);
          executeCalls += 1;
          await dependencies?.beforePaidRequest?.();
          return evidence(true, operationId);
        }) as typeof import("../src/canary.js").runCanary,
      });
      const request = {
        version: 1,
        kind: "paid_miner_request",
        authorization: sidecarConfig.authToken,
        principal_id: "user-1",
        idempotency_key: "request-1",
        miner_id: "212",
        endpoint: "forecast",
        params: { q: "6.524400,3.379200" },
      } as const;
      const fullRequest = {
        ...request,
        request_fingerprint: fingerprint(request),
      };
      const denied = await sidecar.handle({ ...fullRequest, authorization: "wrong" });
      expect(denied).toMatchObject({ ok: false, error: { code: "AUTHORIZATION_FAILED" } });
      const first = await sidecar.handle(fullRequest);
      expect(first).toMatchObject({ ok: true, status: 200 });
      const replay = await sidecar.handle(fullRequest);
      expect(replay).toMatchObject({ ok: true, status: 200 });
      expect(executeCalls).toBe(1);
      await sidecar.close();
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("serves only the bounded newline protocol on a mode-0600 Unix socket", async () => {
    const directory = mkdtempSync(join(tmpdir(), "oathcast-sidecar-socket-"));
    const sidecarConfig = config(directory);
    const sidecar = new ApplicationPaymentSidecar(sidecarConfig, {
      buildTarget: (() => ({
        requestUrl: "https://dispatcher.test/miner-dispatcher/v1/212/forecast?q=x",
        origin: "https://dispatcher.test",
        path: "/miner-dispatcher/v1/212/forecast",
        minerId: "212",
        endpointPath: "forecast",
      })) as typeof buildTarget,
      runCanary: (async (options, dependencies) => {
        if (!options.execute) return evidence(true, options.operationId);
        await dependencies?.beforePaidRequest?.();
        return evidence(true, options.operationId);
      }) as typeof import("../src/canary.js").runCanary,
    });
    const request = {
      version: 1,
      kind: "paid_miner_request",
      authorization: sidecarConfig.authToken,
      principal_id: "user-2",
      idempotency_key: "request-2",
      miner_id: "212",
      endpoint: "forecast",
      params: { q: "6.524400,3.379200" },
    } as const;
    const fullRequest = {
      ...request,
      request_fingerprint: fingerprint(request),
    };
    try {
      await sidecar.listen();
      expect(statSync(sidecarConfig.socketPath).mode & 0o777).toBe(0o600);
      const response = await new Promise<string>((resolve, reject) => {
        const chunks: Buffer[] = [];
        const connection = createConnection(sidecarConfig.socketPath, () => {
          connection.end(JSON.stringify(fullRequest) + "\n");
        });
        connection.on("data", (chunk) => chunks.push(chunk));
        connection.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
        connection.on("error", reject);
      });
      expect(JSON.parse(response)).toMatchObject({ ok: true, status: 200 });
    } finally {
      await sidecar.close();
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("records an ambiguous paid outcome and never retries the idempotency key", async () => {
    const directory = mkdtempSync(join(tmpdir(), "oathcast-sidecar-unknown-"));
    let executeCalls = 0;
    try {
      const sidecarConfig = config(directory);
      const sidecar = new ApplicationPaymentSidecar(sidecarConfig, {
        buildTarget: (() => ({
          requestUrl: "https://dispatcher.test/miner-dispatcher/v1/212/forecast?q=x",
          origin: "https://dispatcher.test",
          path: "/miner-dispatcher/v1/212/forecast",
          minerId: "212",
          endpointPath: "forecast",
        })) as typeof buildTarget,
        runCanary: (async (options, dependencies) => {
          if (!options.execute) return evidence(true, options.operationId);
          executeCalls += 1;
          await dependencies?.beforePaidRequest?.();
          return evidence(false, options.operationId);
        }) as typeof import("../src/canary.js").runCanary,
      });
      const request = {
        version: 1,
        kind: "paid_miner_request",
        authorization: sidecarConfig.authToken,
        principal_id: "user-unknown",
        idempotency_key: "request-unknown",
        miner_id: "212",
        endpoint: "forecast",
        params: { q: "6.524400,3.379200" },
      } as const;
      const fullRequest = {
        ...request,
        request_fingerprint: fingerprint(request),
      };
      const first = await sidecar.handle(fullRequest);
      expect(first).toMatchObject({
        ok: false,
        error: { code: "PAYMENT_OUTCOME_UNKNOWN" },
      });
      const second = await sidecar.handle(fullRequest);
      expect(second).toMatchObject({
        ok: false,
        error: { code: "PAYMENT_OUTCOME_UNKNOWN" },
      });
      expect(executeCalls).toBe(1);
      expect(sidecar.journal.list()[0]?.status).toBe("unknown");
      await sidecar.close();
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("requires sidecar-owned RPC verification before reconciling an unknown payment", async () => {
    const directory = mkdtempSync(join(tmpdir(), "oathcast-sidecar-reconcile-"));
    const payerAddress = base58.encode(new Uint8Array(32).fill(3));
    const transactionSignature = base58.encode(new Uint8Array(64).fill(7));
    try {
      const sidecarConfig = config(directory);
      const verification = {
        rpc_url_sha256: hashText(sidecarConfig.rpcUrl),
        confirmed_transaction: true,
        confirmation_status: "confirmed",
        transaction_error: false,
        transaction_signature_matches: true,
        fee_payer_verified: true,
        token_movement: {
          status: "verified",
          expected_amount: "10000",
          payer_delta: "-10000",
          recipient_delta: "10000",
          recipient: "G53EbeTZSNsAn7bj6iMFUQnq3zpDdEbHhKkPRywo8bix",
          payer: payerAddress,
        },
      } as const;
      const sidecar = new ApplicationPaymentSidecar(sidecarConfig, {
        buildTarget: (() => ({
          requestUrl: "https://dispatcher.test/miner-dispatcher/v1/212/forecast?q=x",
          origin: "https://dispatcher.test",
          path: "/miner-dispatcher/v1/212/forecast",
          minerId: "212",
          endpointPath: "forecast",
        })) as typeof buildTarget,
        runCanary: (async (options, dependencies) => {
          if (!options.execute) return evidence(true, options.operationId);
          await dependencies?.beforePaidRequest?.();
          return evidence(false, options.operationId);
        }) as typeof import("../src/canary.js").runCanary,
        loadSigner: async () => ({ address: payerAddress }),
        verifyPaymentOnChain: async () => ({ ...verification }),
      });
      const request = {
        version: 1,
        kind: "paid_miner_request",
        authorization: sidecarConfig.authToken,
        principal_id: "user-reconcile",
        idempotency_key: "request-reconcile",
        miner_id: "212",
        endpoint: "forecast",
        params: { q: "6.524400,3.379200" },
      } as const;
      const fullRequest = {
        ...request,
        request_fingerprint: fingerprint(request),
      };
      const unknown = await sidecar.handle(fullRequest);
      expect(unknown).toMatchObject({ ok: false, error: { code: "PAYMENT_OUTCOME_UNKNOWN" } });

      const verificationArtifactSha256 = hashText(canonicalJson(verification));
      const wrong = await sidecar.handle({
        version: 1,
        kind: "reconcile_unknown",
        authorization: sidecarConfig.authToken,
        principal_id: request.principal_id,
        idempotency_key: request.idempotency_key,
        operation_id: (unknown as { operation_id: string }).operation_id,
        verification_method: "solana_rpc",
        verification_artifact_sha256: "0".repeat(64),
        transaction_signature: transactionSignature,
      });
      expect(wrong).toMatchObject({
        ok: false,
        error: { code: "RECONCILIATION_ARTIFACT_MISMATCH" },
      });
      expect(sidecar.journal.list()[0]?.status).toBe("unknown");

      const reconciled = await sidecar.handle({
        version: 1,
        kind: "reconcile_unknown",
        authorization: sidecarConfig.authToken,
        principal_id: request.principal_id,
        idempotency_key: request.idempotency_key,
        operation_id: (unknown as { operation_id: string }).operation_id,
        verification_method: "solana_rpc",
        verification_artifact_sha256: verificationArtifactSha256,
        transaction_signature: transactionSignature,
      });
      expect(reconciled).toMatchObject({ ok: true, transaction_signature: transactionSignature });
      expect(sidecar.journal.list()[0]?.status).toBe("reconciled_verified");
      await sidecar.close();
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });
});
