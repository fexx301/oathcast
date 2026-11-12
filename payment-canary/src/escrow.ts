// Telegraph escrow rail: read the 402 challenge, sign its template with
// personal_sign, resend with X-PAYMENT. Follows docs/using/escrow-inference.
// No per-call chain transaction; the node bills the deposit at epoch close.

export const SEPOLIA = "eip155:11155111";
// The Telegraph Diamond on Sepolia, lowercased as the node compares it.
export const DIAMOND = "0x3624eb012b5e998bb57b6be6c9cfc9c4bc5eb99b";
export const MAX_AMOUNT_UUSDC = 10_000n;
const MAX_WINDOW_SECONDS = 100; // the node's limit is 120

export interface Signer {
  address: string;
  signMessage(args: { message: string }): Promise<string>;
}

export interface Authorization {
  from: string;
  to: string;
  value: string;
  validAfter: string;
  validBefore: string;
  nonce: string;
}

export interface EscrowReceipt {
  receipt_hash?: string;
  miner?: string;
  epoch_id?: number;
  amount_uusdc?: string;
  [key: string]: unknown;
}

export type AskOutcome =
  // Refused before payment (bad params, no miner, no escrow offer): nothing billed.
  | { kind: "unpaid_refusal"; status: number; reason: string }
  // Paid attempt answered without a receipt: the docs say it is not charged.
  | { kind: "paid_refusal"; status: number; reason: string; nonce: string }
  | { kind: "served"; status: number; body: Record<string, unknown>; receipt: EscrowReceipt | null; nonce: string };

// Thrown when the paid request may have reached the node but no answer came back.
// The caller must count it as possibly spent and never resend the same authorization.
export class OutcomeUnknown extends Error {
  constructor(message: string, readonly nonce: string) {
    super(message);
  }
}

export interface AskOptions {
  node: string;
  signer: Signer;
  body: Record<string, unknown>;
  fetchFn?: typeof fetch;
  now?: () => number;
  nonce?: () => string;
  // Called after signing and before the paid request leaves, so the ledger counts it first.
  beforePay?: (authorization: Authorization) => void;
}

type Json = Record<string, unknown>;

function reasonOf(body: unknown, fallback: string): string {
  if (body && typeof body === "object") {
    const b = body as Json;
    for (const key of ["reason", "error", "message"]) {
      if (typeof b[key] === "string") return (b[key] as string).slice(0, 300);
    }
  }
  return fallback;
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    return { error: text.slice(0, 300) };
  }
}

// Picks the escrow offer and checks every field we are about to sign against our pins.
export function escrowOffer(challenge: unknown): { template: string; payTo: string; value: string } {
  const accepts = (challenge as Json | null)?.accepts;
  if (!Array.isArray(accepts)) throw new Error("challenge has no accepts list");
  const offer = accepts.find((a) => (a as Json)?.scheme === "escrow") as Json | undefined;
  if (!offer) throw new Error("node does not offer escrow");
  if (offer.network !== SEPOLIA) throw new Error(`escrow network ${String(offer.network)} is not ${SEPOLIA}`);
  const payTo = String(offer.payTo ?? "").toLowerCase();
  if (payTo !== DIAMOND) throw new Error("escrow payTo is not the pinned Diamond");
  const template = (offer.extra as Json | undefined)?.message;
  if (typeof template !== "string") throw new Error("escrow offer has no message template");
  const amount = /Amount:\s*(\d+)\s*uUSDC/.exec(template)?.[1];
  if (!amount) throw new Error("template has no Amount line");
  if (BigInt(amount) > MAX_AMOUNT_UUSDC) throw new Error(`price ${amount} uUSDC is above the ${MAX_AMOUNT_UUSDC} cap`);
  if (!template.includes(`Pay To: ${DIAMOND}`)) throw new Error("template Pay To is not the pinned Diamond");
  if (!template.includes(`Network: ${SEPOLIA}`)) throw new Error("template Network is not Sepolia");
  return { template, payTo, value: amount };
}

export function fillTemplate(template: string, a: Authorization): string {
  const message = template
    .replace("{from}", a.from)
    .replace("{nonce}", a.nonce)
    .replace("{validAfter}", a.validAfter)
    .replace("{validBefore}", a.validBefore);
  if (/\{(from|nonce|validAfter|validBefore)\}/.test(message)) throw new Error("template placeholder left unfilled");
  return message;
}

export function decodeReceipt(header: string | null): EscrowReceipt | null {
  if (!header) return null;
  try {
    return JSON.parse(Buffer.from(header, "base64").toString("utf8")) as EscrowReceipt;
  } catch {
    return null;
  }
}

export async function escrowAsk(options: AskOptions): Promise<AskOutcome> {
  const fetchFn = options.fetchFn ?? fetch;
  const url = `${options.node}/engine/v1/ask`;
  const body = JSON.stringify(options.body);
  const headers = { "Content-Type": "application/json" };

  const first = await fetchFn(url, { method: "POST", headers, body, signal: AbortSignal.timeout(30_000) });
  const challenge = await readJson(first);
  if (first.status !== 402) {
    return { kind: "unpaid_refusal", status: first.status, reason: reasonOf(challenge, `expected 402, got ${first.status}`) };
  }
  const offer = escrowOffer(challenge);

  const now = Math.floor((options.now ?? Date.now)() / 1000);
  const authorization: Authorization = {
    from: options.signer.address.toLowerCase(),
    to: offer.payTo,
    value: offer.value,
    validAfter: String(now - 5),
    validBefore: String(now + MAX_WINDOW_SECONDS - 5),
    nonce: (options.nonce ?? (() => crypto.randomUUID()))(),
  };
  const signature = await options.signer.signMessage({ message: fillTemplate(offer.template, authorization) });
  const payment = Buffer.from(
    JSON.stringify({ x402Version: 2, scheme: "escrow", network: SEPOLIA, payload: { signature, authorization } }),
  ).toString("base64");

  options.beforePay?.(authorization);
  let paid: Response;
  let paidBody: unknown;
  try {
    paid = await fetchFn(url, {
      method: "POST",
      headers: { ...headers, "X-PAYMENT": payment },
      body,
      signal: AbortSignal.timeout(60_000),
    });
    paidBody = await readJson(paid);
  } catch (error) {
    throw new OutcomeUnknown(`paid request did not complete: ${(error as Error).message}`, authorization.nonce);
  }
  const receipt = decodeReceipt(paid.headers.get("X-PAYMENT-RESPONSE"));
  // A 2xx with an object body, or any 2xx carrying a receipt, was billed.
  const isObject = Boolean(paidBody) && typeof paidBody === "object" && !Array.isArray(paidBody);
  if (paid.status >= 200 && paid.status < 300 && (isObject || receipt)) {
    return { kind: "served", status: paid.status, body: isObject ? (paidBody as Json) : {}, receipt, nonce: authorization.nonce };
  }
  return { kind: "paid_refusal", status: paid.status, reason: reasonOf(paidBody, `status ${paid.status}`), nonce: authorization.nonce };
}
