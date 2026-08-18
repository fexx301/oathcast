#!/usr/bin/env node

import { randomBytes } from "node:crypto";
import { chmod, link, mkdir, open, unlink } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { deriveWalletFromSeed } from "./wallet.js";

const PACKAGE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUTPUT_PATH = resolve(PACKAGE_ROOT, "..", ".secrets", "solana-canary.env");
const TEMP_FILE_ATTEMPTS = 16;

export interface WalletFileHandle {
  writeFile(data: string, options: { encoding: BufferEncoding }): Promise<void>;
  chmod(mode: number): Promise<void>;
  sync(): Promise<void>;
  close(): Promise<void>;
}

export interface WalletFileOperations {
  mkdir(
    path: string,
    options: { recursive: true; mode: number },
  ): Promise<string | undefined>;
  chmod(path: string, mode: number): Promise<void>;
  open(path: string, flags: "r" | "wx", mode?: number): Promise<WalletFileHandle>;
  link(existingPath: string, newPath: string): Promise<void>;
  unlink(path: string): Promise<void>;
}

const DEFAULT_FILE_OPERATIONS: WalletFileOperations = {
  mkdir: async (path, options) => mkdir(path, options),
  chmod: async (path, mode) => chmod(path, mode),
  open: async (path, flags, mode) => open(path, flags, mode),
  link: async (existingPath, newPath) => link(existingPath, newPath),
  unlink: async (path) => unlink(path),
};

function errorCode(error: unknown): string | undefined {
  return (error as NodeJS.ErrnoException).code;
}

async function openUniqueTemporaryFile(
  outputPath: string,
  operations: WalletFileOperations,
): Promise<{ path: string; handle: WalletFileHandle }> {
  const directory = dirname(outputPath);
  const filename = basename(outputPath);

  for (let attempt = 0; attempt < TEMP_FILE_ATTEMPTS; attempt += 1) {
    const temporaryPath = join(
      directory,
      `.${filename}.${process.pid}.${randomBytes(16).toString("hex")}.tmp`,
    );
    try {
      return {
        path: temporaryPath,
        handle: await operations.open(temporaryPath, "wx", 0o600),
      };
    } catch (error) {
      if (errorCode(error) !== "EEXIST") throw error;
    }
  }

  throw new Error("could not allocate a unique temporary wallet file");
}

async function fsyncDirectory(
  directory: string,
  operations: WalletFileOperations,
): Promise<void> {
  const handle = await operations.open(directory, "r");
  let operationError: unknown;
  try {
    await handle.sync();
  } catch (error) {
    operationError = error;
  }
  try {
    await handle.close();
  } catch (error) {
    operationError ??= error;
  }
  if (operationError) throw operationError;
}

export async function installWalletSecret(
  outputPath: string,
  encodedSecret: string,
  overrides: Partial<WalletFileOperations> = {},
): Promise<void> {
  const operations: WalletFileOperations = {
    ...DEFAULT_FILE_OPERATIONS,
    ...overrides,
  };
  const outputDirectory = dirname(outputPath);
  await operations.mkdir(outputDirectory, { recursive: true, mode: 0o700 });
  await operations.chmod(outputDirectory, 0o700);

  let temporaryPath: string | undefined;
  let temporaryHandle: WalletFileHandle | undefined;
  let operationError: unknown;
  let operationFailed = false;

  try {
    const temporary = await openUniqueTemporaryFile(outputPath, operations);
    temporaryPath = temporary.path;
    temporaryHandle = temporary.handle;
    await temporaryHandle.writeFile(`SOLANA_PRIVATE_KEY=${encodedSecret}\n`, {
      encoding: "utf8",
    });
    await temporaryHandle.chmod(0o600);
    await temporaryHandle.sync();
    await temporaryHandle.close();
    temporaryHandle = undefined;

    try {
      await operations.link(temporaryPath, outputPath);
    } catch (error) {
      if (errorCode(error) === "EEXIST") {
        throw new Error(
          "dedicated canary wallet already exists; refusing to overwrite it",
        );
      }
      throw error;
    }
  } catch (error) {
    operationError = error;
    operationFailed = true;
  }

  const cleanupErrors: unknown[] = [];
  let temporaryCleanupFailed = false;
  if (temporaryHandle) {
    try {
      await temporaryHandle.close();
    } catch (error) {
      cleanupErrors.push(error);
    }
  }
  if (temporaryPath) {
    try {
      await operations.unlink(temporaryPath);
    } catch (error) {
      if (errorCode(error) !== "ENOENT") {
        cleanupErrors.push(error);
        temporaryCleanupFailed = true;
      }
    }
    try {
      await fsyncDirectory(outputDirectory, operations);
    } catch (error) {
      cleanupErrors.push(error);
    }
  }

  if (operationFailed && cleanupErrors.length > 0) {
    const message = temporaryCleanupFailed
      ? "wallet installation failed and a temporary wallet file may remain because cleanup also failed"
      : "wallet installation failed and cleanup or directory fsync also failed";
    throw new AggregateError([operationError, ...cleanupErrors], message);
  }
  if (operationFailed) throw operationError;
  if (cleanupErrors.length === 1) throw cleanupErrors[0];
  if (cleanupErrors.length > 1) {
    throw new AggregateError(cleanupErrors, "wallet cleanup failed");
  }
}

export async function main(): Promise<void> {
  const wallet = await deriveWalletFromSeed(randomBytes(32));
  await installWalletSecret(OUTPUT_PATH, wallet.encodedSecret);
  console.log(JSON.stringify({
    created: true,
    network: "solana-devnet",
    address: wallet.address,
    secret_path: OUTPUT_PATH,
  }, null, 2));
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  await main();
}
