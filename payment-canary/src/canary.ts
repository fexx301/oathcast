import { createHash } from "node:crypto";

import { base58 } from "@scure/base";
import {
  createKeyPairSignerFromBytes,
  createSolanaRpc,
  devnet,
} from "@solana/kit";
import {
  decodePaymentResponseHeader,
  type PaymentRequired,
  type PaymentRequirements,
  x402Client,
  x402HTTPClient,
} from "@x402/fetch";
import { ExactSvmScheme } from "@x402/svm/exact/client";

export const SOLANA_DEVNET_NETWORK =
  "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1" as const;
export const SOLANA_DEVNET_USDC =
  "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU" as const;
export const EXPECTED_PAY_TO =
  "G53EbeTZSNsAn7bj6iMFUQnq3zpDdEbHhKkPRywo8bix" as const;
export const EXPECTED_FEE_PAYER =
  "2wKupLR9q6wXYppw8Gr2NvWxKBUqm4PPJKkQfoxHDBg4" as const;
export const DEFAULT_MAX_AMOUNT = 10_000n;
export const FETCH_TIMEOUT_MS = 30_000;
export const RPC_TIMEOUT_MS = 30_000;
export const DEFAULT_RPC_URL = "https://api.devnet.solana.com";
export const DEFAULT_MINER_ID = "18";
export const DEFAULT_ENDPOINT_PATH = "predict";
export const LIVE_DEVNET_DISPATCHER_ORIGIN = "http://13.237.89.59:7044";
export const LIVE_DEVNET_DISPATCHER_PREFIX = "/miner-dispatcher";

const OPERATION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const MINER_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;
const PATH_SEGMENT_PATTERN = /^[A-Za-z0-9_~-]{1,128}$/;
const CONFIRMED_STATUSES = new Set(["confirmed", "finalized"]);

type JsonRecord = Record<string, unknown>;

export type FetchLike = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

export interface RpcRequestLike<T = unknown> {
  send(options?: { abortSignal?: AbortSignal }): Promise<T>;
}

/** The narrow RPC surface used by the independent post-settlement verifier. */
export interface SolanaRpcLike {
  getSignatureStatuses(signatures: readonly string[]): RpcRequestLike;
  getTransaction(
    signature: string,
    config: {
      commitment: "confirmed";
      encoding: "jsonParsed";
      maxSupportedTransactionVersion: 0;
    },
  ): RpcRequestLike;
}

export type SolanaSigner = Awaited<
  ReturnType<typeof createKeyPairSignerFromBytes>
>;

export interface PaymentHttpClientLike {
  createPaymentPayload(paymentRequired: PaymentRequired): Promise<unknown>;
  encodePaymentSignatureHeader(paymentPayload: unknown): Record<string, string>;
}

export interface CanaryOptions {
  /** One of dispatcherUrl or targetUrl must be supplied. */
  dispatcherUrl?: string;
  /** Direct target URL, useful for a registered route outside a dispatcher. */
  targetUrl?: string;
  minerId: string;
  endpointPath: string;
  operationId: string;
  execute: boolean;
  /** Temporary, pinned exception for Telegraph's current devnet dispatcher. */
  allowInsecureHttpDevnet?: boolean;
  maxAmount?: string | number | bigint;
  rpcUrl?: string;
  params?: Record<string, string>;
}

export interface CanaryDependencies {
  fetch?: FetchLike;
  env?: NodeJS.ProcessEnv;
  createSigner?: (secretKeyBytes: Uint8Array) => Promise<SolanaSigner>;
  createPaymentClient?: (
    signer: SolanaSigner,
    rpcUrl: string,
    selectedRequirement: PaymentRequirements,
  ) => PaymentHttpClientLike;
  createRpc?: (rpcUrl: string) => SolanaRpcLike;
}

export interface SelectedPaymentEvidence {
  network: string;
  asset: string;
  amount: string;
  pay_to: string;
  fee_payer: string;
}

export interface TokenMovementEvidence {
  status: "verified" | "mismatch" | "unavailable";
  expected_amount: string;
  payer_delta?: string;
  recipient_delta?: string;
  recipient: string;
  payer?: string;
}

export interface VerificationEvidence {
  rpc_url_sha256: string;
  confirmed_transaction: boolean;
  confirmation_status: "confirmed" | "finalized" | "processed" | "unknown";
  transaction_error: boolean | null;
  transaction_signature_matches: boolean | null;
  fee_payer_verified: boolean | null;
  token_movement: TokenMovementEvidence;
}

export interface CanaryEvidence {
  evidence_version: "oathcast.payment-canary.v1";
  ok: boolean;
  mode: "preflight" | "execute";
  operation_id?: string;
  target: {
    origin?: string;
    path?: string;
    miner_id?: string;
    endpoint_path?: string;
    request_url_sha256?: string;
  };
  preflight: {
    status: number | null;
    payment_required_header_sha256?: string;
    response_body_sha256?: string;
    challenge_sha256?: string;
    challenge_validated: boolean;
    selected?: SelectedPaymentEvidence;
    payment_attempted: boolean;
  };
  settlement?: {
    header_sha256: string;
    transaction_signature: string;
    success: boolean;
    network: string;
  };
  signer_address?: string;
  verification?: VerificationEvidence;
  error?: {
    code: string;
    message: string;
  };
}

export interface CanaryResult {
  ok: boolean;
  evidence: CanaryEvidence;
}

export type CanaryErrorCode =
  | "INVALID_OPERATION_ID"
  | "INVALID_TARGET"
  | "TARGET_URL_INVALID"
  | "TARGET_PATH_MISMATCH"
  | "PREFLIGHT_FETCH_FAILED"
  | "PREFLIGHT_NOT_PAYMENT_REQUIRED"
  | "INVALID_CHALLENGE"
  | "CHALLENGE_VERSION_MISMATCH"
  | "CHALLENGE_RESOURCE_MISMATCH"
  | "CHALLENGE_SCHEME_MISMATCH"
  | "CHALLENGE_NETWORK_MISMATCH"
  | "CHALLENGE_ASSET_MISMATCH"
  | "MAX_AMOUNT_INVALID"
  | "CHALLENGE_AMOUNT_INVALID"
  | "CHALLENGE_AMOUNT_EXCEEDS_CAP"
  | "CHALLENGE_PAY_TO_MISMATCH"
  | "CHALLENGE_FEE_PAYER_MISMATCH"
  | "CHALLENGE_OPTION_AMBIGUOUS"
  | "SIGNER_KEY_MISSING"
  | "SIGNER_KEY_INVALID"
  | "SIGNER_INITIALIZATION_FAILED"
  | "SIGNER_RECIPIENT_SAME"
  | "PAYLOAD_CREATION_FAILED"
  | "PAYMENT_HEADER_INVALID"
  | "PAID_REQUEST_OUTCOME_UNKNOWN"
  | "PAID_RESPONSE_INVALID"
  | "SETTLEMENT_HEADER_MISSING"
  | "SETTLEMENT_HEADER_INVALID"
  | "SETTLEMENT_FAILED"
  | "SETTLEMENT_NETWORK_MISMATCH"
  | "SETTLEMENT_AMOUNT_MISMATCH"
  | "SETTLEMENT_PAYER_MISMATCH"
  | "TRANSACTION_SIGNATURE_INVALID"
  | "RPC_QUERY_FAILED"
  | "TRANSACTION_NOT_CONFIRMED"
  | "TRANSACTION_FAILED"
  | "TRANSACTION_SIGNATURE_MISMATCH"
  | "FEE_PAYER_ONCHAIN_MISMATCH"
  | "TOKEN_BALANCE_METADATA_UNAVAILABLE"
  | "TOKEN_BALANCE_MOVEMENT_MISMATCH"
  | "CANARY_FAILED";

const ERROR_MESSAGES: Record<CanaryErrorCode, string> = {
  INVALID_OPERATION_ID: "operation id is required and must be a safe one-shot identifier",
  INVALID_TARGET: "exactly one approved dispatcher or target URL is required",
  TARGET_URL_INVALID: "target URL or target path is not an approved route",
  TARGET_PATH_MISMATCH: "the challenge resource does not exactly match the approved Miner/path",
  PREFLIGHT_FETCH_FAILED: "the unpaid preflight request failed before a challenge was received",
  PREFLIGHT_NOT_PAYMENT_REQUIRED: "the unpaid preflight did not return HTTP 402",
  INVALID_CHALLENGE: "the payment challenge could not be decoded as a valid x402 v2 response",
  CHALLENGE_VERSION_MISMATCH: "the challenge is not x402 version 2",
  CHALLENGE_RESOURCE_MISMATCH: "the challenge resource URL does not match the approved target route",
  CHALLENGE_SCHEME_MISMATCH: "the challenge does not offer exactly one approved exact payment option",
  CHALLENGE_NETWORK_MISMATCH: "the challenge network is not the approved Solana devnet network",
  CHALLENGE_ASSET_MISMATCH: "the challenge asset is not the approved Solana devnet USDC mint",
  MAX_AMOUNT_INVALID: "the configured one-shot cap must be a positive integer at or below the fixed safety ceiling",
  CHALLENGE_AMOUNT_INVALID: "the challenge amount is not a canonical positive integer amount",
  CHALLENGE_AMOUNT_EXCEEDS_CAP: "the challenge amount exceeds the configured one-shot cap",
  CHALLENGE_PAY_TO_MISMATCH: "the challenge recipient is not the approved recipient",
  CHALLENGE_FEE_PAYER_MISMATCH: "the challenge fee payer is not the approved fee payer",
  CHALLENGE_OPTION_AMBIGUOUS: "the challenge contains multiple indistinguishable approved options",
  SIGNER_KEY_MISSING: "--execute requires SOLANA_PRIVATE_KEY in the environment",
  SIGNER_KEY_INVALID: "SOLANA_PRIVATE_KEY is not a 64-byte base58 Solana secret key",
  SIGNER_INITIALIZATION_FAILED: "the Solana signer could not be initialized from SOLANA_PRIVATE_KEY",
  SIGNER_RECIPIENT_SAME: "the signer address must not equal the approved payment recipient",
  PAYLOAD_CREATION_FAILED: "the x402 Solana payment payload could not be created",
  PAYMENT_HEADER_INVALID: "the x402 payment signature header could not be encoded",
  PAID_REQUEST_OUTCOME_UNKNOWN: "the paid retry outcome is unknown; no second payment was attempted",
  PAID_RESPONSE_INVALID: "the paid retry did not return a successful HTTP response",
  SETTLEMENT_HEADER_MISSING: "the successful paid response did not include a settlement header",
  SETTLEMENT_HEADER_INVALID: "the settlement header could not be decoded",
  SETTLEMENT_FAILED: "the settlement response reported failure",
  SETTLEMENT_NETWORK_MISMATCH: "the settlement network is not the approved Solana devnet network",
  SETTLEMENT_AMOUNT_MISMATCH: "the settlement amount does not match the validated challenge amount",
  SETTLEMENT_PAYER_MISMATCH: "the settlement payer does not match the initialized signer",
  TRANSACTION_SIGNATURE_INVALID: "the settlement response did not contain a valid Solana transaction signature",
  RPC_QUERY_FAILED: "the Solana devnet RPC verification query failed",
  TRANSACTION_NOT_CONFIRMED: "the settlement transaction is not confirmed on Solana devnet",
  TRANSACTION_FAILED: "the settlement transaction has a non-null on-chain error",
  TRANSACTION_SIGNATURE_MISMATCH: "the RPC transaction does not contain the settlement signature",
  FEE_PAYER_ONCHAIN_MISMATCH: "the confirmed transaction fee payer does not match the challenge",
  TOKEN_BALANCE_METADATA_UNAVAILABLE: "the RPC response did not expose enough token metadata to verify movement",
  TOKEN_BALANCE_MOVEMENT_MISMATCH: "the confirmed transaction does not show the expected USDC movement to the recipient",
  CANARY_FAILED: "the payment canary failed before producing a more specific sanitized result",
};

export class CanaryError extends Error {
  readonly code: CanaryErrorCode;

  constructor(code: CanaryErrorCode) {
    super(ERROR_MESSAGES[code]);
    this.name = "CanaryError";
    this.code = code;
  }
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
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

function safeOperationId(value: string): string | undefined {
  return OPERATION_ID_PATTERN.test(value) ? value : undefined;
}

function parseAmount(value: unknown, code: CanaryErrorCode): bigint {
  if (typeof value !== "string" || !/^(0|[1-9][0-9]*)$/.test(value)) {
    throw new CanaryError(code);
  }
  try {
    return BigInt(value);
  } catch {
    throw new CanaryError(code);
  }
}

function parseCap(value: string | number | bigint | undefined): bigint {
  if (value === undefined) return DEFAULT_MAX_AMOUNT;
  let cap: bigint;
  try {
    if (typeof value === "bigint") {
      cap = value;
    } else if (typeof value === "string" && /^(0|[1-9][0-9]*)$/.test(value)) {
      cap = BigInt(value);
    } else if (typeof value === "number" && Number.isSafeInteger(value)) {
      cap = BigInt(value);
    } else {
      throw new Error("invalid cap");
    }
  } catch {
    throw new CanaryError("MAX_AMOUNT_INVALID");
  }
  if (cap <= 0n || cap > DEFAULT_MAX_AMOUNT) {
    throw new CanaryError("MAX_AMOUNT_INVALID");
  }
  return cap;
}

function assertSolanaAddress(value: unknown, code: CanaryErrorCode): asserts value is string {
  if (typeof value !== "string") throw new CanaryError(code);
  try {
    if (base58.decode(value).byteLength !== 32) throw new Error("invalid length");
  } catch {
    throw new CanaryError(code);
  }
}

function assertTargetPathParts(minerId: string, endpointPath: string): string[] {
  if (!MINER_ID_PATTERN.test(minerId)) {
    throw new CanaryError("TARGET_URL_INVALID");
  }
  const normalized = endpointPath.replace(/^\/+|\/+$/g, "");
  const parts = normalized.split("/");
  if (
    !normalized ||
    parts.some(
      (part) =>
        !PATH_SEGMENT_PATTERN.test(part) || part === "." || part === "..",
    )
  ) {
    throw new CanaryError("TARGET_URL_INVALID");
  }
  return parts;
}

function parseUrl(
  value: string,
  code: CanaryErrorCode,
  allowInsecureHttpDevnet = false,
): URL {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new CanaryError(code);
  }
  const isHttps = parsed.protocol === "https:";
  const isPinnedDevnetHttp =
    allowInsecureHttpDevnet && parsed.origin === LIVE_DEVNET_DISPATCHER_ORIGIN;
  if ((!isHttps && !isPinnedDevnetHttp) || parsed.username || parsed.password || parsed.hash) {
    throw new CanaryError(code);
  }
  return parsed;
}

function setSortedParams(url: URL, params: Record<string, string> | undefined): void {
  if (!params) return;
  for (const [key, value] of Object.entries(params).sort(([a], [b]) =>
    a.localeCompare(b),
  )) {
    if (!key || key.includes("\0")) throw new CanaryError("TARGET_URL_INVALID");
    url.searchParams.set(key, value);
  }
  url.searchParams.sort();
}

export interface BuiltTarget {
  requestUrl: string;
  origin: string;
  path: string;
  minerId: string;
  endpointPath: string;
}

export function buildTarget(options: Pick<CanaryOptions, "dispatcherUrl" | "targetUrl" | "minerId" | "endpointPath" | "params" | "allowInsecureHttpDevnet">): BuiltTarget {
  if ((options.dispatcherUrl && options.targetUrl) || (!options.dispatcherUrl && !options.targetUrl)) {
    throw new CanaryError("INVALID_TARGET");
  }
  const parts = assertTargetPathParts(options.minerId, options.endpointPath);
  const normalizedEndpointPath = parts.join("/");
  let target: URL;

  if (options.targetUrl) {
    target = parseUrl(
      options.targetUrl,
      "TARGET_URL_INVALID",
      options.allowInsecureHttpDevnet,
    );
    const expectedSuffix = `/v1/${options.minerId}/${normalizedEndpointPath}`;
    if (target.pathname !== expectedSuffix) {
      throw new CanaryError("TARGET_PATH_MISMATCH");
    }
  } else {
    const dispatcher = parseUrl(
      options.dispatcherUrl!,
      "TARGET_URL_INVALID",
      options.allowInsecureHttpDevnet,
    );
    if (dispatcher.search) throw new CanaryError("TARGET_URL_INVALID");
    const basePath = dispatcher.pathname.replace(/\/+$/g, "");
    target = new URL(dispatcher.toString());
    target.pathname = `${basePath}/v1/${options.minerId}/${normalizedEndpointPath}`;
  }

  setSortedParams(target, options.params);
  return {
    requestUrl: target.toString(),
    origin: target.origin,
    path: target.pathname,
    minerId: options.minerId,
    endpointPath: normalizedEndpointPath,
  };
}

function challengeResourceMatches(resource: unknown, target: BuiltTarget): boolean {
  if (typeof resource !== "string") return false;

  let parsed: URL;
  try {
    parsed = new URL(resource);
  } catch {
    return false;
  }
  if (parsed.username || parsed.password || parsed.hash) return false;
  if (parsed.toString() === target.requestUrl) return true;

  // The live devnet gateway advertises the canonical Miner URL without its
  // dispatcher prefix. Keep that alias pinned to one authority, one exact
  // prefix transformation, and an identical query string.
  if (target.origin !== LIVE_DEVNET_DISPATCHER_ORIGIN) return false;
  const requested = new URL(target.requestUrl);
  if (parsed.origin !== target.origin || parsed.search !== requested.search) return false;
  if (!target.path.startsWith(`${LIVE_DEVNET_DISPATCHER_PREFIX}/v1/`)) return false;
  return parsed.pathname === target.path.slice(LIVE_DEVNET_DISPATCHER_PREFIX.length);
}

function baseEvidence(options: CanaryOptions): CanaryEvidence {
  return {
    evidence_version: "oathcast.payment-canary.v1",
    ok: false,
    mode: options.execute ? "execute" : "preflight",
    operation_id: safeOperationId(options.operationId),
    target: {},
    preflight: {
      status: null,
      challenge_validated: false,
      payment_attempted: false,
    },
  };
}

function publicError(code: CanaryErrorCode): { code: string; message: string } {
  return { code, message: ERROR_MESSAGES[code] };
}

function fail(evidence: CanaryEvidence, error: CanaryErrorCode): CanaryResult {
  evidence.ok = false;
  evidence.error = publicError(error);
  return { ok: false, evidence };
}

function getHeader(headers: Headers, name: string): string | null {
  return headers.get(name);
}

function firstHeader(headers: Headers, names: readonly string[]): string | null {
  for (const name of names) {
    const value = getHeader(headers, name);
    if (value) return value;
  }
  return null;
}

async function responseBodyDigest(response: Response): Promise<string> {
  const body = await response.text();
  return sha256Text(body);
}

function decodeBodyIfJson(text: string): unknown {
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
}

function selectedEvidence(requirement: PaymentRequirements): SelectedPaymentEvidence {
  return {
    network: requirement.network,
    asset: requirement.asset,
    amount: requirement.amount,
    pay_to: requirement.payTo,
    fee_payer: String(requirement.extra.feePayer),
  };
}

function validateChallenge(
  paymentRequired: PaymentRequired,
  target: BuiltTarget,
  maxAmount: bigint,
): PaymentRequirements {
  if (!isRecord(paymentRequired) || paymentRequired.x402Version !== 2) {
    throw new CanaryError("CHALLENGE_VERSION_MISMATCH");
  }
  if (
    !isRecord(paymentRequired.resource) ||
    !challengeResourceMatches(paymentRequired.resource.url, target)
  ) {
    throw new CanaryError("CHALLENGE_RESOURCE_MISMATCH");
  }

  const accepts = paymentRequired.accepts;
  if (!Array.isArray(accepts)) throw new CanaryError("CHALLENGE_SCHEME_MISMATCH");
  const exactOptions = accepts.filter(
    (entry): entry is PaymentRequirements =>
      isRecord(entry) && entry.scheme === "exact",
  );
  if (exactOptions.length === 0) throw new CanaryError("CHALLENGE_SCHEME_MISMATCH");

  const networkOptions = exactOptions.filter(
    (entry) => entry.network === SOLANA_DEVNET_NETWORK,
  );
  if (networkOptions.length === 0) throw new CanaryError("CHALLENGE_NETWORK_MISMATCH");
  const assetOptions = networkOptions.filter(
    (entry) => entry.asset === SOLANA_DEVNET_USDC,
  );
  if (assetOptions.length === 0) throw new CanaryError("CHALLENGE_ASSET_MISMATCH");
  if (assetOptions.length !== 1) throw new CanaryError("CHALLENGE_OPTION_AMBIGUOUS");

  const selected = assetOptions[0];
  const amount = parseAmount(selected.amount, "CHALLENGE_AMOUNT_INVALID");
  if (amount === 0n) throw new CanaryError("CHALLENGE_AMOUNT_INVALID");
  if (maxAmount <= 0n || amount > maxAmount) {
    throw new CanaryError("CHALLENGE_AMOUNT_EXCEEDS_CAP");
  }
  if (selected.payTo !== EXPECTED_PAY_TO) {
    throw new CanaryError("CHALLENGE_PAY_TO_MISMATCH");
  }
  assertSolanaAddress(selected.payTo, "CHALLENGE_PAY_TO_MISMATCH");
  if (!isRecord(selected.extra) || selected.extra.feePayer !== EXPECTED_FEE_PAYER) {
    throw new CanaryError("CHALLENGE_FEE_PAYER_MISMATCH");
  }
  assertSolanaAddress(selected.extra.feePayer, "CHALLENGE_FEE_PAYER_MISMATCH");

  const optionResource = (selected as unknown as JsonRecord).resource;
  if (optionResource !== undefined && !challengeResourceMatches(optionResource, target)) {
    throw new CanaryError("TARGET_PATH_MISMATCH");
  }
  return selected;
}

function defaultCreateSigner(secretKeyBytes: Uint8Array): Promise<SolanaSigner> {
  return createKeyPairSignerFromBytes(secretKeyBytes);
}

function defaultCreateRpc(rpcUrl: string): SolanaRpcLike {
  return createSolanaRpc(devnet(rpcUrl)) as unknown as SolanaRpcLike;
}

function defaultCreatePaymentClient(
  signer: SolanaSigner,
  rpcUrl: string,
  selectedRequirement: PaymentRequirements,
): PaymentHttpClientLike {
  const selectedJson = canonicalJson(selectedRequirement);
  const selector = (_version: number, requirements: PaymentRequirements[]) => {
    const matches = requirements.filter(
      (requirement) => canonicalJson(requirement) === selectedJson,
    );
    if (matches.length !== 1) throw new Error("validated requirement was not selected");
    return matches[0];
  };
  const client = new x402Client(selector).register(
    SOLANA_DEVNET_NETWORK,
    new ExactSvmScheme(signer, { rpcUrl }),
  );
  return new x402HTTPClient(client);
}

function loadSecretKeyBytes(environment: NodeJS.ProcessEnv): Uint8Array {
  const encoded = environment.SOLANA_PRIVATE_KEY;
  if (!encoded) throw new CanaryError("SIGNER_KEY_MISSING");
  try {
    const decoded = base58.decode(encoded);
    if (decoded.byteLength !== 64) throw new Error("wrong secret key length");
    return decoded;
  } catch {
    throw new CanaryError("SIGNER_KEY_INVALID");
  }
}

export async function loadSignerFromEnvironment(
  environment: NodeJS.ProcessEnv = process.env,
  signerFactory: (secretKeyBytes: Uint8Array) => Promise<SolanaSigner> = defaultCreateSigner,
): Promise<SolanaSigner> {
  const secretKeyBytes = loadSecretKeyBytes(environment);
  try {
    return await signerFactory(secretKeyBytes);
  } catch {
    throw new CanaryError("SIGNER_INITIALIZATION_FAILED");
  }
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function knownConfirmationStatus(value: unknown): VerificationEvidence["confirmation_status"] {
  if (value === "confirmed" || value === "finalized" || value === "processed") {
    return value;
  }
  return "unknown";
}

function onChainSignatureMatches(transaction: JsonRecord, signature: string): boolean | null {
  const transactionBody = transaction.transaction;
  if (!isRecord(transactionBody) || !Array.isArray(transactionBody.signatures)) return null;
  return transactionBody.signatures.includes(signature);
}

function firstAccountKey(transaction: JsonRecord): string | undefined {
  const transactionBody = transaction.transaction;
  if (!isRecord(transactionBody) || !isRecord(transactionBody.message)) return undefined;
  const accountKeys = transactionBody.message.accountKeys;
  if (!Array.isArray(accountKeys) || accountKeys.length === 0) return undefined;
  const first = accountKeys[0];
  return typeof first === "string"
    ? first
    : isRecord(first)
      ? asString(first.pubkey)
      : undefined;
}

interface AggregatedTokenBalances {
  available: boolean;
  sawExpectedMint: boolean;
  byOwner: Map<string, bigint>;
}

function aggregateTokenBalances(
  value: unknown,
  mint: string,
): AggregatedTokenBalances {
  if (!Array.isArray(value)) {
    return { available: false, sawExpectedMint: false, byOwner: new Map() };
  }
  const byOwner = new Map<string, bigint>();
  let sawExpectedMint = false;
  for (const entry of value) {
    if (!isRecord(entry) || entry.mint !== mint) continue;
    sawExpectedMint = true;
    const owner = asString(entry.owner);
    const tokenAmount = isRecord(entry.uiTokenAmount)
      ? entry.uiTokenAmount.amount
      : undefined;
    if (!owner || typeof tokenAmount !== "string" || !/^\d+$/.test(tokenAmount)) {
      return { available: false, sawExpectedMint, byOwner: new Map() };
    }
    const amount = BigInt(tokenAmount);
    byOwner.set(owner, (byOwner.get(owner) ?? 0n) + amount);
  }
  return { available: true, sawExpectedMint, byOwner };
}

async function verifyOnChain(
  rpc: SolanaRpcLike,
  transactionSignature: string,
  signerAddress: string,
  amount: bigint,
  abortSignal: AbortSignal,
): Promise<VerificationEvidence> {
  const statusResponse = await rpc
    .getSignatureStatuses([transactionSignature])
    .send({ abortSignal });
  const statusValue = isRecord(statusResponse) ? statusResponse.value : undefined;
  const status = Array.isArray(statusValue) && isRecord(statusValue[0])
    ? statusValue[0]
    : undefined;
  const confirmationStatus = knownConfirmationStatus(status?.confirmationStatus);

  const transactionResponse = await rpc
    .getTransaction(transactionSignature, {
      commitment: "confirmed",
      encoding: "jsonParsed",
      maxSupportedTransactionVersion: 0,
    })
    .send({ abortSignal });
  const transaction = isRecord(transactionResponse) ? transactionResponse : undefined;
  const meta = transaction && isRecord(transaction.meta) ? transaction.meta : undefined;
  const statusError = status && "err" in status ? status.err !== null : null;
  const transactionError = meta && "err" in meta ? meta.err !== null : null;
  const confirmed =
    Boolean(transaction) &&
    meta !== undefined &&
    transactionError === false &&
    statusError === false &&
    CONFIRMED_STATUSES.has(confirmationStatus);
  const signatureMatches = transaction
    ? onChainSignatureMatches(transaction, transactionSignature)
    : null;
  const feePayer = transaction ? firstAccountKey(transaction) : undefined;
  const feePayerVerified = feePayer ? feePayer === EXPECTED_FEE_PAYER : null;

  const preBalances = aggregateTokenBalances(
    meta?.preTokenBalances,
    SOLANA_DEVNET_USDC,
  );
  const postBalances = aggregateTokenBalances(
    meta?.postTokenBalances,
    SOLANA_DEVNET_USDC,
  );
  const movementAvailable =
    preBalances.available &&
    postBalances.available &&
    (preBalances.sawExpectedMint || postBalances.sawExpectedMint) &&
    preBalances.byOwner.has(signerAddress) &&
    preBalances.byOwner.has(EXPECTED_PAY_TO) &&
    postBalances.byOwner.has(signerAddress) &&
    postBalances.byOwner.has(EXPECTED_PAY_TO);
  const payerBefore = preBalances.byOwner.get(signerAddress) ?? 0n;
  const payerAfter = postBalances.byOwner.get(signerAddress) ?? 0n;
  const recipientBefore = preBalances.byOwner.get(EXPECTED_PAY_TO) ?? 0n;
  const recipientAfter = postBalances.byOwner.get(EXPECTED_PAY_TO) ?? 0n;
  const payerDelta = payerAfter - payerBefore;
  const recipientDelta = recipientAfter - recipientBefore;
  const movementStatus: TokenMovementEvidence["status"] = !movementAvailable
    ? "unavailable"
    : payerDelta === -amount && recipientDelta === amount
      ? "verified"
      : "mismatch";

  return {
    rpc_url_sha256: "",
    confirmed_transaction: confirmed,
    confirmation_status: confirmationStatus,
    transaction_error: transactionError,
    transaction_signature_matches: signatureMatches,
    fee_payer_verified: feePayerVerified,
    token_movement: {
      status: movementStatus,
      expected_amount: amount.toString(),
      ...(movementAvailable
        ? {
            payer_delta: payerDelta.toString(),
            recipient_delta: recipientDelta.toString(),
          }
        : {}),
      recipient: EXPECTED_PAY_TO,
      payer: signerAddress,
    },
  };
}

async function withAbortTimeout<T>(
  timeoutMs: number,
  operation: (abortSignal: AbortSignal) => Promise<T>,
): Promise<T> {
  const controller = new AbortController();
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_resolve, reject) => {
    timeoutId = setTimeout(() => {
      const error = new Error("operation timed out");
      controller.abort(error);
      reject(error);
    }, timeoutMs);
  });

  try {
    return await Promise.race([operation(controller.signal), timeout]);
  } finally {
    if (timeoutId !== undefined) clearTimeout(timeoutId);
  }
}

function extractSettlementHeader(response: Response): string | null {
  return firstHeader(response.headers, [
    "PAYMENT-RESPONSE",
    "X-PAYMENT-RESPONSE",
    "X-PAYMENT-SETTLE-RESPONSE",
  ]);
}

function decodeSettlement(header: string): JsonRecord {
  try {
    const decoded = decodePaymentResponseHeader(header);
    if (!isRecord(decoded)) throw new Error("not an object");
    return decoded;
  } catch {
    throw new CanaryError("SETTLEMENT_HEADER_INVALID");
  }
}

function settlementAmountMatches(value: unknown, expected: string): boolean {
  let parsed: bigint;
  try {
    if (typeof value === "string" && /^(0|[1-9][0-9]*)$/.test(value)) {
      parsed = BigInt(value);
    } else if (
      typeof value === "number" &&
      Number.isSafeInteger(value) &&
      value >= 0
    ) {
      parsed = BigInt(value);
    } else {
      return false;
    }
    return parsed === BigInt(expected);
  } catch {
    return false;
  }
}

function validateTransactionSignature(value: unknown): string {
  if (typeof value !== "string" || !value) {
    throw new CanaryError("TRANSACTION_SIGNATURE_INVALID");
  }
  try {
    const decoded = base58.decode(value);
    if (decoded.byteLength !== 64 || base58.encode(decoded) !== value) {
      throw new Error("invalid signature");
    }
  } catch {
    throw new CanaryError("TRANSACTION_SIGNATURE_INVALID");
  }
  return value;
}

export async function runCanary(
  options: CanaryOptions,
  dependencies: CanaryDependencies = {},
): Promise<CanaryResult> {
  const evidence = baseEvidence(options);
  let target: BuiltTarget | undefined;
  let selected: PaymentRequirements | undefined;
  let paymentClient: PaymentHttpClientLike | undefined;
  let paidRequestWasSent = false;

  try {
    if (!safeOperationId(options.operationId)) {
      throw new CanaryError("INVALID_OPERATION_ID");
    }
    target = buildTarget(options);
    evidence.target = {
      origin: target.origin,
      path: target.path,
      miner_id: target.minerId,
      endpoint_path: target.endpointPath,
      request_url_sha256: sha256Text(target.requestUrl),
    };
    const maxAmount = parseCap(options.maxAmount);

    const fetchFn = dependencies.fetch ?? globalThis.fetch;
    if (typeof fetchFn !== "function") throw new CanaryError("PREFLIGHT_FETCH_FAILED");
    const operationHeaders = {
      Accept: "application/json",
      "Idempotency-Key": options.operationId,
      "X-Payment-Canary-Operation-ID": options.operationId,
    };
    let preflightResponse: Response;
    try {
      preflightResponse = await fetchFn(target.requestUrl, {
        method: "GET",
        headers: operationHeaders,
        redirect: "error",
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
      });
    } catch {
      throw new CanaryError("PREFLIGHT_FETCH_FAILED");
    }
    evidence.preflight.status = preflightResponse.status;
    const preflightBody = await preflightResponse.text();
    evidence.preflight.response_body_sha256 = sha256Text(preflightBody);

    const encodedChallenge = getHeader(preflightResponse.headers, "PAYMENT-REQUIRED");
    if (encodedChallenge) {
      evidence.preflight.payment_required_header_sha256 = sha256Text(encodedChallenge);
    }
    if (preflightResponse.status !== 402) {
      throw new CanaryError("PREFLIGHT_NOT_PAYMENT_REQUIRED");
    }

    let paymentRequired: PaymentRequired;
    try {
      const challengeBody = decodeBodyIfJson(preflightBody);
      const challengeParser = new x402HTTPClient(new x402Client());
      paymentRequired = challengeParser.getPaymentRequiredResponse(
        (name) => getHeader(preflightResponse.headers, name),
        challengeBody,
      );
    } catch {
      throw new CanaryError("INVALID_CHALLENGE");
    }
    evidence.preflight.challenge_sha256 = sha256Json(paymentRequired);
    selected = validateChallenge(paymentRequired, target, maxAmount);
    evidence.preflight.challenge_validated = true;
    evidence.preflight.selected = selectedEvidence(selected);

    if (!options.execute) {
      evidence.ok = true;
      return { ok: true, evidence };
    }

    const environment = dependencies.env ?? process.env;
    const signer = await loadSignerFromEnvironment(
      environment,
      dependencies.createSigner ?? defaultCreateSigner,
    );
    const signerAddress = String(signer.address);
    assertSolanaAddress(signerAddress, "SIGNER_INITIALIZATION_FAILED");
    evidence.signer_address = signerAddress;
    if (signerAddress === EXPECTED_PAY_TO) {
      throw new CanaryError("SIGNER_RECIPIENT_SAME");
    }

    const rpcUrl = options.rpcUrl ?? environment.SOLANA_RPC_URL ?? DEFAULT_RPC_URL;
    const rpc = (dependencies.createRpc ?? defaultCreateRpc)(rpcUrl);
    evidence.verification = {
      rpc_url_sha256: sha256Text(rpcUrl),
      confirmed_transaction: false,
      confirmation_status: "unknown",
      transaction_error: null,
      transaction_signature_matches: null,
      fee_payer_verified: null,
      token_movement: {
        status: "unavailable",
        expected_amount: selected.amount,
        recipient: EXPECTED_PAY_TO,
        payer: signerAddress,
      },
    };

    paymentClient = (dependencies.createPaymentClient ?? defaultCreatePaymentClient)(
      signer,
      rpcUrl,
      selected,
    );
    let paymentPayload: unknown;
    try {
      paymentPayload = await paymentClient.createPaymentPayload(paymentRequired);
    } catch {
      throw new CanaryError("PAYLOAD_CREATION_FAILED");
    }
    let paymentHeaders: Record<string, string>;
    try {
      paymentHeaders = paymentClient.encodePaymentSignatureHeader(paymentPayload);
      if (
        typeof paymentHeaders["PAYMENT-SIGNATURE"] !== "string" ||
        !paymentHeaders["PAYMENT-SIGNATURE"]
      ) {
        throw new Error("missing v2 signature header");
      }
    } catch {
      throw new CanaryError("PAYMENT_HEADER_INVALID");
    }

    let paidResponse: Response;
    try {
      paidRequestWasSent = true;
      evidence.preflight.payment_attempted = true;
      paidResponse = await fetchFn(target.requestUrl, {
        method: "GET",
        redirect: "error",
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
        headers: {
          ...operationHeaders,
          ...paymentHeaders,
          "Access-Control-Expose-Headers": "PAYMENT-RESPONSE,X-PAYMENT-RESPONSE",
        },
      });
    } catch {
      throw new CanaryError("PAID_REQUEST_OUTCOME_UNKNOWN");
    }

    if (paidResponse.status < 200 || paidResponse.status >= 300) {
      await responseBodyDigest(paidResponse);
      throw new CanaryError("PAID_RESPONSE_INVALID");
    }
    const settlementHeader = extractSettlementHeader(paidResponse);
    if (!settlementHeader) throw new CanaryError("SETTLEMENT_HEADER_MISSING");
    evidence.settlement = {
      header_sha256: sha256Text(settlementHeader),
      transaction_signature: "",
      success: false,
      network: "",
    };
    const settlement = decodeSettlement(settlementHeader);
    if (settlement.success !== true) throw new CanaryError("SETTLEMENT_FAILED");
    if (settlement.network !== SOLANA_DEVNET_NETWORK) {
      throw new CanaryError("SETTLEMENT_NETWORK_MISMATCH");
    }
    if (
      settlement.amount !== undefined &&
      !settlementAmountMatches(settlement.amount, selected.amount)
    ) {
      throw new CanaryError("SETTLEMENT_AMOUNT_MISMATCH");
    }
    if (
      settlement.payer !== undefined &&
      (typeof settlement.payer !== "string" || settlement.payer !== signerAddress)
    ) {
      throw new CanaryError("SETTLEMENT_PAYER_MISMATCH");
    }
    const transactionSignature = validateTransactionSignature(settlement.transaction);
    evidence.settlement = {
      header_sha256: sha256Text(settlementHeader),
      transaction_signature: transactionSignature,
      success: true,
      network: SOLANA_DEVNET_NETWORK,
    };

    const verifiedAmount = BigInt(selected.amount);
    let verification: VerificationEvidence;
    try {
      verification = await withAbortTimeout(
        RPC_TIMEOUT_MS,
        (abortSignal) => verifyOnChain(
          rpc,
          transactionSignature,
          signerAddress,
          verifiedAmount,
          abortSignal,
        ),
      );
    } catch {
      throw new CanaryError("RPC_QUERY_FAILED");
    }
    verification.rpc_url_sha256 = sha256Text(rpcUrl);
    evidence.verification = verification;
    if (!verification.confirmed_transaction) {
      if (verification.transaction_error === true) {
        throw new CanaryError("TRANSACTION_FAILED");
      }
      throw new CanaryError("TRANSACTION_NOT_CONFIRMED");
    }
    if (verification.transaction_signature_matches === false) {
      throw new CanaryError("TRANSACTION_SIGNATURE_MISMATCH");
    }
    if (verification.fee_payer_verified === false) {
      throw new CanaryError("FEE_PAYER_ONCHAIN_MISMATCH");
    }
    if (verification.token_movement.status === "unavailable") {
      throw new CanaryError("TOKEN_BALANCE_METADATA_UNAVAILABLE");
    }
    if (verification.token_movement.status === "mismatch") {
      throw new CanaryError("TOKEN_BALANCE_MOVEMENT_MISMATCH");
    }
    evidence.ok = true;
    return { ok: true, evidence };
  } catch (error) {
    if (error instanceof CanaryError) return fail(evidence, error.code);
    return fail(evidence, paidRequestWasSent ? "PAID_REQUEST_OUTCOME_UNKNOWN" : "CANARY_FAILED");
  }
}
