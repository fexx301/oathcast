import { createKeyPairSignerFromPrivateKeyBytes } from "@solana/kit";
import { base58 } from "@scure/base";

export interface DerivedWallet {
  address: string;
  encodedSecret: string;
}

/** Derive the standard 64-byte Solana secret (seed + public key). */
export async function deriveWalletFromSeed(seed: Uint8Array): Promise<DerivedWallet> {
  if (seed.byteLength !== 32) throw new Error("wallet seed must be exactly 32 bytes");
  const signer = await createKeyPairSignerFromPrivateKeyBytes(seed);
  const publicKey = new Uint8Array(
    await globalThis.crypto.subtle.exportKey("raw", signer.keyPair.publicKey),
  );
  if (publicKey.byteLength !== 32) throw new Error("unexpected Solana public key length");
  const secret = new Uint8Array(64);
  secret.set(seed, 0);
  secret.set(publicKey, 32);
  return {
    address: String(signer.address),
    encodedSecret: base58.encode(secret),
  };
}
