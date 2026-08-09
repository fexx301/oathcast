#!/usr/bin/env node

import { randomBytes } from "node:crypto";
import { chmod, mkdir, open } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { deriveWalletFromSeed } from "./wallet.js";

const PACKAGE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUTPUT_PATH = resolve(PACKAGE_ROOT, "..", ".secrets", "solana-canary.env");

async function main(): Promise<void> {
  const wallet = await deriveWalletFromSeed(randomBytes(32));
  await mkdir(dirname(OUTPUT_PATH), { recursive: true, mode: 0o700 });

  let handle;
  try {
    handle = await open(OUTPUT_PATH, "wx", 0o600);
    await handle.writeFile(`SOLANA_PRIVATE_KEY=${wallet.encodedSecret}\n`, {
      encoding: "utf8",
    });
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "EEXIST") {
      throw new Error("dedicated canary wallet already exists; refusing to overwrite it");
    }
    throw error;
  } finally {
    await handle?.close();
  }
  await chmod(OUTPUT_PATH, 0o600);
  console.log(JSON.stringify({
    created: true,
    network: "solana-devnet",
    address: wallet.address,
    secret_path: OUTPUT_PATH,
  }, null, 2));
}

await main();
