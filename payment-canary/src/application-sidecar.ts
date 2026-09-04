#!/usr/bin/env node

/**
 * Private Track 3 payment sidecar.
 *
 * The Python Application is the control plane. This process is the only
 * component that owns SOLANA_PRIVATE_KEY and the durable payment journal. It
 * accepts one bounded newline-delimited JSON message over a private Unix
 * socket, performs a fresh unpaid challenge check, and can sign at most the
 * configured budget. It never listens on TCP and never returns credentials or
 * payment authorization headers.
 */

import { createHash, timingSafeEqual } from "node:crypto";
import {
  chmodSync,
  existsSync,
  lstatSync,
  mkdirSync,
  unlinkSync,
} from "node:fs";
import { createServer, type Server, type Socket } from "node:net";
import { dirname, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";
import { base58 } from "@scure/base";

import {
  DEFAULT_MAX_AMOUNT,
  DEFAULT_RPC_URL,
  EXPECTED_FEE_PAYER,
  EXPECTED_PAY_TO,
  RPC_TIMEOUT_MS,
  SOLANA_DEVNET_NETWORK,
  SOLANA_DEVNET_USDC,
  buildTarget,
  createDevnetRpc,
  loadSignerFromEnvironment,
  runCanary,
  verifyPaymentOnChain,
  type CanaryEvidence,
  type CanaryResult,
  type SolanaRpcLike,
} from "./canary.js";
import {
  JournalBudgetExceeded,
  JournalConflict,
  JournalDuplicate,
  JournalInProgress,
  JournalOutcomeUnknown,
  PaymentJournal,
  type PaymentJournalRecord,
} from "./journal.js";

const MAX_INPUT_BYTES = 128 * 1024;
const MAX_PARAMS = 32;
const MAX_PARAM_NAME = 64;
const MAX_PARAM_VALUE = 512;
const MAX_AUTH_TOKEN_BYTES = 512;
const HEX64 = /^[0-9a-f]{64}$/;
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$/;
const SAFE_OPERATION_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const SAFE_MINER_ID = /^[A-Za-z0-9_-]{1,64}$/;
const SAFE_ENDPOINT = /^[A-Za-z0-9_~-]{1,128}$/;

type JsonRecord = Record<string, unknown>;

export interface SidecarConfig {
  socketPath: string;
  journalPath: string;
  authToken: string;
  dispatcherUrl: string;
  rpcUrl: string;
  allowedMinerIds: readonly string[];
  allowedEndpoints: readonly string[];
  maxAmountMicroUsdc: bigint;
  maxTotalMicroUsdc: bigint;
  maxRequests: number;
  allowInsecureHttpDevnet: boolean;
}

interface PaidMinerRequest {
  version: 1;
  kind: "paid_miner_request";
  authorization: string;
  principal_id: string;
  idempotency_key: string;
  request_fingerprint: string;
  miner_id: string;
  endpoint: string;
  params: Record<string, string>;
}

interface ReconcileRequest {
  version: 1;
  kind: "reconcile_unknown";
  authorization: string;
  principal_id: string;
  idempotency_key: string;
  operation_id: string;
  verification_method: "solana_rpc";
  verification_artifact_sha256: string;
  transaction_signature: string;
  response_body_sha256?: string;
}

export type SidecarRequest = PaidMinerRequest | ReconcileRequest;

export interface SidecarSuccess {
  version: 1;
  ok: true;
  operation_id: string;
  payment_attempt_id: string;
  status: number;
  body: unknown;
  body_sha256: string;
  challenge_sha256: string;
  target_sha256: string;
  settlement_artifact_sha256: string;
  transaction_signature: string;
  received_at: string;
  verification: unknown;
  evidence: CanaryEvidence;
}

export interface SidecarErrorResponse {
  version: 1;
  ok: false;
  operation_id?: string;
  error: { code: string; message: string };
  evidence?: CanaryEvidence;
}

export type SidecarResponse = SidecarSuccess | SidecarErrorResponse;

export class SidecarConfigError extends Error {}

export interface SidecarDependencies {
  buildTarget?: typeof buildTarget;
  runCanary?: typeof runCanary;
  loadSigner?: () => Promise<{ address: string }>;
  createRpc?: (rpcUrl: string) => SolanaRpcLike;
  verifyPaymentOnChain?: typeof verifyPaymentOnChain;
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) {
    return `[${value.map((entry) => canonicalJson(entry)).join(",")}]`;
  }
  const record = value as JsonRecord;
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
    .join(",")}}`;
}

function sha256Text(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function sha256Json(value: unknown): string {
  return sha256Text(canonicalJson(value));
}

function publicError(
  code: string,
  message: string,
  operationId?: string,
  evidence?: CanaryEvidence,
): SidecarErrorResponse {
  return {
    version: 1,
    ok: false,
    ...(operationId ? { operation_id: operationId } : {}),
    error: { code, message },
    ...(evidence ? { evidence } : {}),
  };
}

function constantTimeEqual(expected: string, actual: string): boolean {
  const expectedBytes = Buffer.from(expected, "utf8");
  const actualBytes = Buffer.from(actual, "utf8");
  if (expectedBytes.length !== actualBytes.length) return false;
  return timingSafeEqual(expectedBytes, actualBytes);
}

function boundedString(
  value: unknown,
  name: string,
  maximum: number,
  pattern?: RegExp,
): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum) {
    throw new SidecarConfigError(`${name} is invalid`);
  }
  if ([...value].some((character) => {
    const code = character.codePointAt(0) ?? 0;
    return code < 32 || code === 127;
  })) {
    throw new SidecarConfigError(`${name} contains a control character`);
  }
  if (pattern && !pattern.test(value)) throw new SidecarConfigError(`${name} is invalid`);
  return value;
}

function positiveInteger(value: unknown, name: string): bigint {
  const text = typeof value === "bigint" ? value.toString() : String(value ?? "");
  if (!/^[1-9][0-9]*$/.test(text)) throw new SidecarConfigError(`${name} is invalid`);
  const parsed = BigInt(text);
  if (parsed <= 0n) throw new SidecarConfigError(`${name} is invalid`);
  return parsed;
}

function parseBoolean(value: string | undefined): boolean {
  return value === "true";
}

function parseList(value: string | undefined, fallback: string[], name: string): string[] {
  const values = (value ?? fallback.join(","))
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
  if (values.length === 0 || new Set(values).size !== values.length) {
    throw new SidecarConfigError(`${name} is invalid`);
  }
  return values;
}

function parseEnvInteger(value: string | undefined, fallback: number, name: string): number {
  const parsed = Number(value ?? fallback);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new SidecarConfigError(`${name} is invalid`);
  }
  return parsed;
}

function validateConfig(config: SidecarConfig): void {
  if (!isAbsolute(config.socketPath) || !isAbsolute(config.journalPath)) {
    throw new SidecarConfigError("socket and journal paths must be absolute");
  }
  if (config.socketPath === config.journalPath) {
    throw new SidecarConfigError("socket and journal paths must be different");
  }
  boundedString(config.authToken, "auth token", MAX_AUTH_TOKEN_BYTES);
  const authTokenBytes = Buffer.byteLength(config.authToken, "utf8");
  if (authTokenBytes < 32 || authTokenBytes > MAX_AUTH_TOKEN_BYTES) {
    throw new SidecarConfigError("auth token must be 32-512 bytes");
  }
  boundedString(config.dispatcherUrl, "dispatcher URL", 2048);
  boundedString(config.rpcUrl, "RPC URL", 2048);
  if (config.allowedMinerIds.length === 0 || config.allowedEndpoints.length === 0) {
    throw new SidecarConfigError("an allowlist is required");
  }
  if (
    new Set(config.allowedMinerIds).size !== config.allowedMinerIds.length ||
    new Set(config.allowedEndpoints).size !== config.allowedEndpoints.length
  ) {
    throw new SidecarConfigError("allowlist entries must be unique");
  }
  config.allowedMinerIds.forEach((value) => boundedString(value, "Miner id", 64, SAFE_MINER_ID));
  config.allowedEndpoints.forEach((value) => boundedString(value, "endpoint", 128, SAFE_ENDPOINT));
  if (
    config.maxAmountMicroUsdc <= 0n ||
    config.maxAmountMicroUsdc > DEFAULT_MAX_AMOUNT
  ) {
    throw new SidecarConfigError("payment cap is invalid");
  }
  if (
    config.maxTotalMicroUsdc < config.maxAmountMicroUsdc ||
    config.maxTotalMicroUsdc > BigInt(Number.MAX_SAFE_INTEGER) ||
    config.maxRequests <= 0 ||
    !Number.isSafeInteger(config.maxRequests)
  ) {
    throw new SidecarConfigError("payment budget is invalid");
  }
}

export function configFromEnvironment(
  environment: NodeJS.ProcessEnv = process.env,
): SidecarConfig {
  if (environment.OATHCAST_APPLICATION_ENABLE_PAID !== "true") {
    throw new SidecarConfigError(
      "OATHCAST_APPLICATION_ENABLE_PAID must be true to start the paid sidecar",
    );
  }
  const socketPath = environment.OATHCAST_APPLICATION_SOCKET ??
    "/var/run/oathcast/application-payment.sock";
  const journalPath = environment.OATHCAST_APPLICATION_PAYMENT_JOURNAL ??
    environment.OATHCAST_PAYMENT_JOURNAL ??
    "/var/lib/oathcast/application/payment-journal.sqlite3";
  if (!isAbsolute(socketPath) || !isAbsolute(journalPath)) {
    throw new SidecarConfigError("socket and journal paths must be absolute");
  }
  const authToken = environment.OATHCAST_APPLICATION_SIDECAR_TOKEN ?? "";
  if (Buffer.byteLength(authToken, "utf8") < 32 || Buffer.byteLength(authToken, "utf8") > MAX_AUTH_TOKEN_BYTES) {
    throw new SidecarConfigError("OATHCAST_APPLICATION_SIDECAR_TOKEN must be 32-512 bytes");
  }
  const dispatcherUrl = environment.OATHCAST_DISPATCHER_URL ??
    environment.TELEGRAPH_DISPATCHER_URL ?? "";
  if (!dispatcherUrl) throw new SidecarConfigError("a dispatcher URL is required");
  const maxAmountMicroUsdc = positiveInteger(
    environment.OATHCAST_MAX_PAYMENT_MICRO_USDC ?? DEFAULT_MAX_AMOUNT.toString(),
    "OATHCAST_MAX_PAYMENT_MICRO_USDC",
  );
  if (maxAmountMicroUsdc > DEFAULT_MAX_AMOUNT) {
    throw new SidecarConfigError("one-shot payment cap exceeds the fixed safety ceiling");
  }
  const maxTotalMicroUsdc = positiveInteger(
    environment.OATHCAST_MAX_TOTAL_PAYMENT_MICRO_USDC ?? maxAmountMicroUsdc.toString(),
    "OATHCAST_MAX_TOTAL_PAYMENT_MICRO_USDC",
  );
  if (maxTotalMicroUsdc < maxAmountMicroUsdc) {
    throw new SidecarConfigError("total payment cap is below one-shot cap");
  }
  if (maxTotalMicroUsdc > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new SidecarConfigError("total payment cap exceeds the journal integer ceiling");
  }
  const allowedMinerIds = parseList(
    environment.OATHCAST_APPLICATION_ALLOWED_MINER_IDS,
    ["212"],
    "allowed Miner ids",
  );
  const allowedEndpoints = parseList(
    environment.OATHCAST_APPLICATION_ALLOWED_ENDPOINTS,
    ["forecast"],
    "allowed endpoints",
  );
  allowedMinerIds.forEach((value) => boundedString(value, "Miner id", 64, SAFE_MINER_ID));
  allowedEndpoints.forEach((value) => boundedString(value, "endpoint", 128, SAFE_ENDPOINT));
  return {
    socketPath,
    journalPath,
    authToken,
    dispatcherUrl,
    rpcUrl: environment.SOLANA_RPC_URL ?? DEFAULT_RPC_URL,
    allowedMinerIds,
    allowedEndpoints,
    maxAmountMicroUsdc,
    maxTotalMicroUsdc,
    maxRequests: parseEnvInteger(
      environment.OATHCAST_APPLICATION_MAX_REQUESTS,
      1,
      "OATHCAST_APPLICATION_MAX_REQUESTS",
    ),
    allowInsecureHttpDevnet: parseBoolean(
      environment.OATHCAST_APPLICATION_ALLOW_INSECURE_HTTP_DEVNET,
    ),
  };
}

function validateParams(value: unknown): Record<string, string> {
  if (!isRecord(value) || Object.keys(value).length > MAX_PARAMS) {
    throw new SidecarConfigError("params are invalid");
  }
  const params: Record<string, string> = {};
  for (const [key, entry] of Object.entries(value)) {
    boundedString(key, "parameter name", MAX_PARAM_NAME, /^[A-Za-z0-9_.~-]+$/);
    params[key] = boundedString(entry, "parameter value", MAX_PARAM_VALUE);
  }
  return params;
}

function validatePaidRequest(
  value: unknown,
  authToken: string,
): PaidMinerRequest {
  if (!isRecord(value) || value.version !== 1 || value.kind !== "paid_miner_request") {
    throw new SidecarConfigError("request kind is invalid");
  }
  const authorization = boundedString(value.authorization, "authorization", MAX_AUTH_TOKEN_BYTES);
  if (!constantTimeEqual(authToken, authorization)) {
    throw new SidecarConfigError("authorization failed");
  }
  const principal_id = boundedString(value.principal_id, "principal_id", 128, SAFE_ID);
  const idempotency_key = boundedString(value.idempotency_key, "idempotency_key", 128, SAFE_OPERATION_ID);
  const request_fingerprint = boundedString(
    value.request_fingerprint,
    "request_fingerprint",
    64,
    HEX64,
  );
  const miner_id = boundedString(value.miner_id, "miner_id", 64, SAFE_MINER_ID);
  const endpoint = boundedString(value.endpoint, "endpoint", 128, SAFE_ENDPOINT);
  return {
    version: 1,
    kind: "paid_miner_request",
    authorization,
    principal_id,
    idempotency_key,
    request_fingerprint,
    miner_id,
    endpoint,
    params: validateParams(value.params),
  };
}

function validateReconcileRequest(
  value: unknown,
  authToken: string,
): ReconcileRequest {
  if (!isRecord(value) || value.version !== 1 || value.kind !== "reconcile_unknown") {
    throw new SidecarConfigError("request kind is invalid");
  }
  const authorization = boundedString(value.authorization, "authorization", MAX_AUTH_TOKEN_BYTES);
  if (!constantTimeEqual(authToken, authorization)) {
    throw new SidecarConfigError("authorization failed");
  }
  const principal_id = boundedString(value.principal_id, "principal_id", 128, SAFE_ID);
  const idempotency_key = boundedString(value.idempotency_key, "idempotency_key", 128, SAFE_OPERATION_ID);
  const operation_id = boundedString(value.operation_id, "operation_id", 128, SAFE_OPERATION_ID);
  if (value.verification_method !== "solana_rpc") {
    throw new SidecarConfigError("verification_method must be solana_rpc");
  }
  const verification_artifact_sha256 = boundedString(
    value.verification_artifact_sha256,
    "verification_artifact_sha256",
    64,
    HEX64,
  );
  const transaction_signature = boundedString(
    value.transaction_signature,
    "transaction_signature",
    128,
  );
  try {
    if (base58.decode(transaction_signature).byteLength !== 64) throw new Error("signature");
  } catch {
    throw new SidecarConfigError("transaction_signature is invalid");
  }
  const response_body_sha256 = value.response_body_sha256 === undefined
    ? undefined
    : boundedString(value.response_body_sha256, "response_body_sha256", 64, HEX64);
  return {
    version: 1,
    kind: "reconcile_unknown",
    authorization,
    principal_id,
    idempotency_key,
    operation_id,
    verification_method: "solana_rpc",
    verification_artifact_sha256,
    transaction_signature,
    ...(response_body_sha256 ? { response_body_sha256 } : {}),
  };
}

function requestFingerprint(request: PaidMinerRequest): string {
  return sha256Json({
    version: 1,
    principal_id: request.principal_id,
    idempotency_key: request.idempotency_key,
    miner_id: request.miner_id,
    endpoint: request.endpoint,
    params: request.params,
  });
}

function operationIdFor(request: PaidMinerRequest, targetSha256: string): string {
  return `app-${sha256Json({
    principal_id: request.principal_id,
    idempotency_key: request.idempotency_key,
    request_fingerprint: request.request_fingerprint,
    target_sha256: targetSha256,
  }).slice(0, 60)}`;
}

function policySha256(config: SidecarConfig, targetSha256: string): string {
  return sha256Json({
    policy_version: "oathcast.application.payment.v1",
    target_sha256: targetSha256,
    dispatcher_url_sha256: sha256Text(config.dispatcherUrl),
    network: SOLANA_DEVNET_NETWORK,
    asset: SOLANA_DEVNET_USDC,
    pay_to: EXPECTED_PAY_TO,
    fee_payer: EXPECTED_FEE_PAYER,
    allowed_miner_ids: [...config.allowedMinerIds].sort(),
    allowed_endpoints: [...config.allowedEndpoints].sort(),
    max_amount_micro_usdc: config.maxAmountMicroUsdc.toString(),
    max_total_micro_usdc: config.maxTotalMicroUsdc.toString(),
    max_requests: config.maxRequests,
  });
}

function decodeStoredBody(record: PaymentJournalRecord): unknown {
  if (record.response_body_text === null) return undefined;
  if (record.response_body_is_json) {
    try {
      return JSON.parse(record.response_body_text) as unknown;
    } catch {
      return record.response_body_text;
    }
  }
  return record.response_body_text;
}

function replayResponse(
  record: PaymentJournalRecord,
  targetSha256: string,
): SidecarResponse {
  if (record.status !== "settled_verified") {
    return publicError(
      "RECONCILED_PAYMENT_RESPONSE_UNAVAILABLE",
      "the payment was reconciled but its paid response is not replayable",
      record.operation_id,
    );
  }
  const verification = record.verification_json
    ? (() => {
        try {
          return JSON.parse(record.verification_json) as unknown;
        } catch {
          return undefined;
        }
      })()
    : undefined;
  const evidence: CanaryEvidence = {
    evidence_version: "oathcast.payment-canary.v1",
    ok: true,
    mode: "execute",
    operation_id: record.operation_id,
    target: {
      miner_id: record.miner_id,
      endpoint_path: record.endpoint,
      request_url_sha256: targetSha256,
    },
    preflight: {
      status: 402,
      challenge_sha256: record.challenge_sha256,
      challenge_validated: true,
      selected: {
        network: SOLANA_DEVNET_NETWORK,
        asset: SOLANA_DEVNET_USDC,
        amount: record.amount_micro_usdc.toString(),
        pay_to: EXPECTED_PAY_TO,
        fee_payer: EXPECTED_FEE_PAYER,
      },
      payment_attempted: true,
    },
    paid_response_status: record.response_status ?? undefined,
    paid_response_body_sha256: record.response_body_sha256 ?? undefined,
    settlement: {
      header_sha256: record.settlement_artifact_sha256 ?? "",
      transaction_signature: record.transaction_signature ?? "",
      success: true,
      network: SOLANA_DEVNET_NETWORK,
    },
    verification: verification as CanaryEvidence["verification"],
  };
  return {
    version: 1,
    ok: true,
    operation_id: record.operation_id,
    payment_attempt_id: record.operation_id,
    status: record.response_status ?? 200,
    body: decodeStoredBody(record),
    body_sha256: record.response_body_sha256 ?? sha256Text(record.response_body_text ?? ""),
    challenge_sha256: record.challenge_sha256,
    target_sha256: record.target_sha256,
    settlement_artifact_sha256: record.settlement_artifact_sha256 ?? "",
    transaction_signature: record.transaction_signature ?? "",
    received_at: record.updated_at,
    verification,
    evidence,
  };
}

function evidenceErrorCode(result: CanaryResult): string {
  return result.evidence.error?.code ?? "CANARY_FAILED";
}

function evidenceErrorMessage(result: CanaryResult): string {
  return result.evidence.error?.message ?? "the payment attempt failed";
}

function bodyIsJson(bodyText: string): boolean {
  try {
    JSON.parse(bodyText);
    return true;
  } catch {
    return false;
  }
}

export class ApplicationPaymentSidecar {
  readonly config: SidecarConfig;
  readonly journal: PaymentJournal;
  private readonly dependencies: SidecarDependencies;
  private server: Server | undefined;
  private socketCreated = false;

  constructor(config: SidecarConfig, dependencies: SidecarDependencies = {}) {
    this.config = config;
    this.dependencies = dependencies;
    validateConfig(config);
    this.journal = new PaymentJournal(config.journalPath);
    this.journal.recoverUnfinished();
  }

  async handle(value: unknown): Promise<SidecarResponse> {
    let request: PaidMinerRequest | ReconcileRequest;
    try {
      if (isRecord(value) && value.kind === "reconcile_unknown") {
        request = validateReconcileRequest(value, this.config.authToken);
      } else {
        request = validatePaidRequest(value, this.config.authToken);
      }
    } catch (error) {
      if (error instanceof SidecarConfigError && error.message === "authorization failed") {
        return publicError("AUTHORIZATION_FAILED", "sidecar authorization failed");
      }
      return publicError("INVALID_REQUEST", "the sidecar request is invalid");
    }
    if (request.kind === "reconcile_unknown") return this.handleReconcile(request);
    return this.handlePaidRequest(request);
  }

  private async handleReconcile(request: ReconcileRequest): Promise<SidecarResponse> {
    const record = this.journal.getByPrincipalKey(request.principal_id, request.idempotency_key);
    if (!record || record.operation_id !== request.operation_id) {
      return publicError("RECONCILIATION_NOT_FOUND", "the payment attempt was not found", request.operation_id);
    }
    if (record.status !== "unknown") {
      return publicError("RECONCILIATION_STATE_INVALID", "the payment attempt is not awaiting reconciliation", request.operation_id);
    }
    if (
      request.response_body_sha256 !== undefined &&
      record.response_body_sha256 !== request.response_body_sha256
    ) {
      return publicError(
        "RECONCILIATION_RESPONSE_MISMATCH",
        "the supplied response evidence does not match the journal",
        request.operation_id,
      );
    }
    if (
      record.transaction_signature !== null &&
      record.transaction_signature !== request.transaction_signature
    ) {
      return publicError(
        "RECONCILIATION_TRANSACTION_MISMATCH",
        "the supplied transaction does not match the journal",
        request.operation_id,
      );
    }

    let verification;
    try {
      const signer = this.dependencies.loadSigner
        ? await this.dependencies.loadSigner()
        : await loadSignerFromEnvironment();
      const signerAddress = String(signer.address);
      const rpc = (this.dependencies.createRpc ?? createDevnetRpc)(this.config.rpcUrl);
      verification = await (this.dependencies.verifyPaymentOnChain ?? verifyPaymentOnChain)(
        rpc,
        request.transaction_signature,
        signerAddress,
        BigInt(record.amount_micro_usdc),
        AbortSignal.timeout(RPC_TIMEOUT_MS),
      );
      verification.rpc_url_sha256 = sha256Text(this.config.rpcUrl);
    } catch {
      return publicError(
        "RECONCILIATION_RPC_FAILED",
        "the payment could not be independently verified",
        request.operation_id,
      );
    }
    if (
      verification.confirmed_transaction !== true ||
      verification.transaction_error !== false ||
      verification.transaction_signature_matches !== true ||
      verification.fee_payer_verified !== true ||
      verification.token_movement.status !== "verified"
    ) {
      return publicError(
        "RECONCILIATION_VERIFICATION_FAILED",
        "the chain evidence does not verify the authorized payment",
        request.operation_id,
      );
    }
    const verificationJson = canonicalJson(verification);
    if (sha256Text(verificationJson) !== request.verification_artifact_sha256) {
      return publicError(
        "RECONCILIATION_ARTIFACT_MISMATCH",
        "the supplied verification artifact does not match the sidecar result",
        request.operation_id,
      );
    }
    try {
      const updated = this.journal.reconcileUnknown(request.operation_id, {
        verification_method: "solana_rpc",
        verification_artifact_sha256: request.verification_artifact_sha256,
        transaction_signature: request.transaction_signature,
        verification_json: verificationJson,
        response_body_sha256: request.response_body_sha256,
      });
      const evidence: CanaryEvidence = {
        evidence_version: "oathcast.payment-canary.v1",
        ok: true,
        mode: "execute",
        operation_id: updated.operation_id,
        target: {
          miner_id: updated.miner_id,
          endpoint_path: updated.endpoint,
          request_url_sha256: updated.target_sha256,
        },
        preflight: {
          status: 402,
          challenge_sha256: updated.challenge_sha256,
          challenge_validated: true,
          selected: {
            network: SOLANA_DEVNET_NETWORK,
            asset: SOLANA_DEVNET_USDC,
            amount: updated.amount_micro_usdc.toString(),
            pay_to: EXPECTED_PAY_TO,
            fee_payer: EXPECTED_FEE_PAYER,
          },
          payment_attempted: true,
        },
        ...(updated.response_status === null
          ? {}
          : { paid_response_status: updated.response_status }),
        ...(updated.response_body_sha256 === null
          ? {}
          : { paid_response_body_sha256: updated.response_body_sha256 }),
        settlement: {
          header_sha256: updated.settlement_artifact_sha256 ?? "",
          transaction_signature: updated.transaction_signature ?? request.transaction_signature,
          success: true,
          network: SOLANA_DEVNET_NETWORK,
        },
        verification,
      };
      return {
        version: 1,
        ok: true,
        operation_id: updated.operation_id,
        payment_attempt_id: updated.operation_id,
        status: updated.response_status ?? 0,
        body: decodeStoredBody(updated),
        body_sha256: updated.response_body_sha256 ?? "",
        challenge_sha256: updated.challenge_sha256,
        target_sha256: updated.target_sha256,
        settlement_artifact_sha256: updated.settlement_artifact_sha256 ?? "",
        transaction_signature: updated.transaction_signature ?? request.transaction_signature,
        received_at: updated.updated_at,
        verification,
        evidence,
      };
    } catch {
      return publicError("RECONCILIATION_FAILED", "the payment attempt could not be reconciled", request.operation_id);
    }
  }

  private async handlePaidRequest(request: PaidMinerRequest): Promise<SidecarResponse> {
    if (!this.config.allowedMinerIds.includes(request.miner_id) ||
        !this.config.allowedEndpoints.includes(request.endpoint)) {
      return publicError("ALLOWLIST_REJECTED", "the Miner or endpoint is not allowlisted");
    }
    if (requestFingerprint(request) !== request.request_fingerprint) {
      return publicError("REQUEST_FINGERPRINT_MISMATCH", "the request fingerprint is invalid");
    }

    let targetSha256: string;
    let operationId: string;
    try {
      const preflightOptions = {
        dispatcherUrl: this.config.dispatcherUrl,
        minerId: request.miner_id,
        endpointPath: request.endpoint,
        operationId: "preflight-" + request.idempotency_key,
        execute: false,
        allowInsecureHttpDevnet: this.config.allowInsecureHttpDevnet,
        maxAmount: this.config.maxAmountMicroUsdc.toString(),
        rpcUrl: this.config.rpcUrl,
        params: request.params,
      } as const;
      const builtTarget = (this.dependencies.buildTarget ?? buildTarget)(preflightOptions);
      targetSha256 = sha256Text(builtTarget.requestUrl);
      operationId = operationIdFor(request, targetSha256);
    } catch {
      return publicError("TARGET_REJECTED", "the requested Miner route is invalid");
    }

    const policyHash = policySha256(this.config, targetSha256);
    const existing = this.journal.getByPrincipalKey(request.principal_id, request.idempotency_key);
    if (existing) {
      if (
        existing.operation_id !== operationId ||
        existing.request_fingerprint !== request.request_fingerprint ||
        existing.policy_sha256 !== policyHash ||
        existing.miner_id !== request.miner_id ||
        existing.endpoint !== request.endpoint
      ) {
        return publicError("IDEMPOTENCY_CONFLICT", "the idempotency key is bound to a different request", operationId);
      }
      if (existing.status === "settled_verified" || existing.status === "reconciled_verified") {
        return replayResponse(existing, targetSha256);
      }
      if (existing.status === "unknown" || existing.status === "submitted") {
        return publicError("PAYMENT_OUTCOME_UNKNOWN", "the payment outcome requires explicit reconciliation", operationId);
      }
      if (existing.status === "reserved") {
        return publicError("PAYMENT_IN_PROGRESS", "the payment attempt is already in progress", operationId);
      }
      return publicError("IDEMPOTENCY_CONSUMED", "the idempotency key has already been consumed", operationId);
    }

    const run = this.dependencies.runCanary ?? runCanary;
    let preflight: CanaryResult;
    try {
      preflight = await run({
        dispatcherUrl: this.config.dispatcherUrl,
        minerId: request.miner_id,
        endpointPath: request.endpoint,
        operationId,
        execute: false,
        allowInsecureHttpDevnet: this.config.allowInsecureHttpDevnet,
        maxAmount: this.config.maxAmountMicroUsdc.toString(),
        rpcUrl: this.config.rpcUrl,
        params: request.params,
      });
    } catch {
      return publicError("PREFLIGHT_FETCH_FAILED", "the unpaid Miner preflight failed", operationId);
    }
    if (!preflight.ok || !preflight.evidence.preflight.selected || !preflight.evidence.preflight.challenge_sha256) {
      return publicError(
        evidenceErrorCode(preflight),
        evidenceErrorMessage(preflight),
        operationId,
        preflight.evidence,
      );
    }
    const amount = Number(preflight.evidence.preflight.selected.amount);
    if (!Number.isSafeInteger(amount) || amount <= 0) {
      return publicError("CHALLENGE_AMOUNT_INVALID", "the challenge amount is invalid", operationId, preflight.evidence);
    }
    let reservation: PaymentJournalRecord;
    try {
      const reserved = this.journal.reserve({
        operation_id: operationId,
        principal_id: request.principal_id,
        idempotency_key: request.idempotency_key,
        request_fingerprint: request.request_fingerprint,
        policy_sha256: policyHash,
        miner_id: request.miner_id,
        endpoint: request.endpoint,
        target_sha256: targetSha256,
        challenge_sha256: preflight.evidence.preflight.challenge_sha256,
        amount_micro_usdc: amount,
        max_total_micro_usdc: Number(this.config.maxTotalMicroUsdc),
        max_requests: this.config.maxRequests,
      });
      if (reserved.kind === "replay") return replayResponse(reserved.record, targetSha256);
      reservation = reserved.record;
    } catch (error) {
      if (error instanceof JournalBudgetExceeded) return publicError("PAYMENT_BUDGET_EXHAUSTED", "the payment budget is exhausted", operationId);
      if (error instanceof JournalOutcomeUnknown) return publicError("PAYMENT_OUTCOME_UNKNOWN", "the payment outcome requires explicit reconciliation", operationId);
      if (error instanceof JournalInProgress) return publicError("PAYMENT_IN_PROGRESS", "the payment attempt is already in progress", operationId);
      if (error instanceof JournalDuplicate) return publicError("IDEMPOTENCY_CONSUMED", "the idempotency key has already been consumed", operationId);
      if (error instanceof JournalConflict) return publicError("IDEMPOTENCY_CONFLICT", "the payment reservation conflicts with stored evidence", operationId);
      return publicError("JOURNAL_FAILURE", "the payment journal is unavailable", operationId);
    }

    let execute: CanaryResult;
    try {
      execute = await run({
        dispatcherUrl: this.config.dispatcherUrl,
        minerId: request.miner_id,
        endpointPath: request.endpoint,
        operationId,
        execute: true,
        expectedChallengeSha256: reservation.challenge_sha256,
        allowInsecureHttpDevnet: this.config.allowInsecureHttpDevnet,
        maxAmount: this.config.maxAmountMicroUsdc.toString(),
        rpcUrl: this.config.rpcUrl,
        params: request.params,
        }, {
          beforePaidRequest: () => {
            this.journal.markSubmitted(operationId);
          },
        });
    } catch {
      const current = this.journal.get(operationId);
      if (current?.status === "submitted") {
        try {
          this.journal.markUnknown(operationId, "CANARY_EXECUTION_UNKNOWN");
        } catch {
          // A submitted attempt remains non-retryable even if its diagnostic
          // transition cannot be written in this process.
        }
        return publicError("PAYMENT_OUTCOME_UNKNOWN", "the paid request outcome requires explicit reconciliation", operationId);
      }
      try {
        if (current?.status === "reserved") this.journal.abort(operationId, "CANARY_EXECUTION_FAILED");
      } catch {
        // Keep the failure fail-closed; a reserved row is still budgeted.
      }
      return publicError("PREFLIGHT_FETCH_FAILED", "the payment attempt could not be prepared", operationId);
    }
    const current = this.journal.get(operationId);
    const paidAttempted = execute.evidence.preflight.payment_attempted || current?.status === "submitted";
    if (!execute.ok) {
      if (paidAttempted) {
        try {
          if (current?.status === "submitted") {
            this.journal.markUnknown(operationId, "PAID_REQUEST_OUTCOME_UNKNOWN", {
              response_status: execute.paid_response_status ?? null,
              response_body_text: execute.paid_response_body_text ?? null,
              response_body_sha256: execute.paid_response_body_sha256 ?? null,
              response_body_is_json: execute.paid_response_body_text === undefined
                ? null
                : bodyIsJson(execute.paid_response_body_text),
              settlement_artifact_sha256: execute.evidence.settlement?.header_sha256 ?? null,
              transaction_signature: execute.evidence.settlement?.transaction_signature || null,
              verification_json: execute.evidence.verification ? canonicalJson(execute.evidence.verification) : null,
            });
          }
        } catch {
          // A submitted attempt remains non-retryable even if its diagnostic
          // transition cannot be written in this process.
        }
        return publicError("PAYMENT_OUTCOME_UNKNOWN", "the paid request outcome requires explicit reconciliation", operationId, execute.evidence);
      }
      try {
        if (this.journal.get(operationId)?.status === "reserved") {
          this.journal.abort(operationId, evidenceErrorCode(execute));
        }
      } catch {
        // Keep the failure fail-closed; a reserved row is still budgeted.
      }
      return publicError(evidenceErrorCode(execute), evidenceErrorMessage(execute), operationId, execute.evidence);
    }

    const paidBodyText = execute.paid_response_body_text;
    const settlement = execute.evidence.settlement;
    const verification = execute.evidence.verification;
    if (
      paidBodyText === undefined ||
      execute.paid_response_status === undefined ||
      !settlement?.header_sha256 ||
      !settlement.transaction_signature ||
      !verification
    ) {
      try {
        if (this.journal.get(operationId)?.status === "submitted") {
          this.journal.markUnknown(operationId, "INCOMPLETE_SETTLEMENT_EVIDENCE", {
            response_status: execute.paid_response_status ?? null,
            response_body_text: paidBodyText ?? null,
            response_body_sha256: execute.paid_response_body_sha256 ?? null,
            response_body_is_json: paidBodyText === undefined ? null : bodyIsJson(paidBodyText),
            settlement_artifact_sha256: settlement?.header_sha256 ?? null,
            transaction_signature: settlement?.transaction_signature || null,
            verification_json: verification ? canonicalJson(verification) : null,
          });
        }
      } catch {
        // Preserve the non-retryable submitted state.
      }
      return publicError("INCOMPLETE_SETTLEMENT_EVIDENCE", "the paid response did not produce complete settlement evidence", operationId, execute.evidence);
    }
    let settled: PaymentJournalRecord;
    try {
      settled = this.journal.markSettled(operationId, {
        response_status: execute.paid_response_status,
        response_body_text: paidBodyText,
        response_body_sha256: execute.paid_response_body_sha256 ?? sha256Text(paidBodyText),
        response_body_is_json: bodyIsJson(paidBodyText),
        settlement_artifact_sha256: settlement.header_sha256,
        transaction_signature: settlement.transaction_signature,
        verification_json: canonicalJson(verification),
      });
    } catch {
      try {
        if (this.journal.get(operationId)?.status === "submitted") {
          this.journal.markUnknown(operationId, "JOURNAL_SETTLEMENT_WRITE_FAILED", {
            response_status: execute.paid_response_status,
            response_body_text: paidBodyText,
            response_body_sha256: execute.paid_response_body_sha256 ?? sha256Text(paidBodyText),
            response_body_is_json: bodyIsJson(paidBodyText),
            settlement_artifact_sha256: settlement.header_sha256,
            transaction_signature: settlement.transaction_signature,
            verification_json: canonicalJson(verification),
          });
        }
      } catch {
        // Do not retry: a submitted payment remains potentially spent.
      }
      return publicError("JOURNAL_FAILURE", "the payment result could not be committed", operationId, execute.evidence);
    }
    return {
      version: 1,
      ok: true,
      operation_id: settled.operation_id,
      payment_attempt_id: settled.operation_id,
      status: settled.response_status!,
      body: execute.paid_response_body,
      body_sha256: settled.response_body_sha256!,
      challenge_sha256: settled.challenge_sha256,
      target_sha256: settled.target_sha256,
      settlement_artifact_sha256: settled.settlement_artifact_sha256!,
      transaction_signature: settled.transaction_signature!,
      received_at: settled.updated_at,
      verification,
      evidence: execute.evidence,
    };
  }

  listen(): Promise<void> {
    if (this.server) throw new Error("sidecar is already listening");
    mkdirSync(dirname(this.config.socketPath), { recursive: true, mode: 0o700 });
    chmodSync(dirname(this.config.socketPath), 0o700);
    if (existsSync(this.config.socketPath)) {
      const stat = lstatSync(this.config.socketPath);
      if (!stat.isSocket()) throw new SidecarConfigError("configured socket path is not a Unix socket");
      unlinkSync(this.config.socketPath);
    }
    this.server = createServer((socket) => this.handleSocket(socket));
    return new Promise((resolve, reject) => {
      const server = this.server!;
      server.once("error", reject);
      server.listen(this.config.socketPath, () => {
        server.removeListener("error", reject);
        chmodSync(this.config.socketPath, 0o600);
        this.socketCreated = true;
        resolve();
      });
    });
  }

  private handleSocket(socket: Socket): void {
    socket.setNoDelay(true);
    let buffer = Buffer.alloc(0);
    let handled = false;
    const respond = async (line: Buffer) => {
      if (handled) return;
      handled = true;
      let response: SidecarResponse;
      try {
        const value = JSON.parse(line.toString("utf8")) as unknown;
        response = await this.handle(value);
      } catch {
        response = publicError("INVALID_REQUEST", "the sidecar request is invalid");
      }
      const encoded = Buffer.from(JSON.stringify(response) + "\n", "utf8");
      socket.end(encoded);
    };
    socket.on("data", (chunk: Buffer) => {
      if (handled) return;
      buffer = Buffer.concat([buffer, chunk]);
      if (buffer.length > MAX_INPUT_BYTES) {
        handled = true;
        socket.destroy();
        return;
      }
      const newline = buffer.indexOf(10);
      if (newline >= 0) void respond(buffer.subarray(0, newline));
    });
    socket.on("error", () => undefined);
  }

  async close(): Promise<void> {
    if (this.server) {
      await new Promise<void>((resolve) => this.server!.close(() => resolve()));
      this.server = undefined;
    }
    this.journal.close();
    if (this.socketCreated && existsSync(this.config.socketPath)) {
      const stat = lstatSync(this.config.socketPath);
      if (stat.isSocket()) unlinkSync(this.config.socketPath);
    }
    this.socketCreated = false;
  }
}

export async function main(): Promise<void> {
  try {
    const config = configFromEnvironment();
    if (!process.env.SOLANA_PRIVATE_KEY) {
      throw new SidecarConfigError("SOLANA_PRIVATE_KEY is required for the paid sidecar");
    }
    const sidecar = new ApplicationPaymentSidecar(config);
    await sidecar.listen();
    const shutdown = () => {
      void sidecar.close().finally(() => process.exit(0));
    };
    process.once("SIGINT", shutdown);
    process.once("SIGTERM", shutdown);
    await new Promise<void>(() => undefined);
  } catch (error) {
    console.error(error instanceof SidecarConfigError ? error.message : "paid sidecar failed to start");
    process.exitCode = 1;
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  await main();
}
