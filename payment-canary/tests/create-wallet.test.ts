import {
  chmod,
  mkdtemp,
  open,
  readFile,
  readdir,
  rm,
  stat,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  installWalletSecret,
  type WalletFileHandle,
  type WalletFileOperations,
} from "../src/create-wallet.js";

const temporaryRoots: string[] = [];

async function outputFixture(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "oathcast-wallet-test-"));
  temporaryRoots.push(root);
  return join(root, ".secrets", "solana-canary.env");
}

async function directoryEntries(outputPath: string): Promise<string[]> {
  return readdir(dirname(outputPath));
}

afterEach(async () => {
  await Promise.all(
    temporaryRoots
      .splice(0)
      .map((root) => rm(root, { recursive: true, force: true })),
  );
});

describe("wallet secret installation", () => {
  it("atomically installs an owner-only wallet file and removes its sibling temp", async () => {
    const outputPath = await outputFixture();
    const syncedHandles: Array<"r" | "wx"> = [];
    const trackingOpen: WalletFileOperations["open"] = async (
      path,
      flags,
      mode,
    ) => {
      const handle = await open(path, flags, mode);
      return {
        writeFile: async (data, options) => handle.writeFile(data, options),
        chmod: async (requestedMode) => handle.chmod(requestedMode),
        sync: async () => {
          syncedHandles.push(flags);
          await handle.sync();
        },
        close: async () => handle.close(),
      };
    };

    await installWalletSecret(outputPath, "fixture-secret", {
      open: trackingOpen,
    });

    expect(await readFile(outputPath, "utf8")).toBe(
      "SOLANA_PRIVATE_KEY=fixture-secret\n",
    );
    expect((await stat(outputPath)).mode & 0o777).toBe(0o600);
    expect((await stat(dirname(outputPath))).mode & 0o777).toBe(0o700);
    expect(await directoryEntries(outputPath)).toEqual(["solana-canary.env"]);
    expect(syncedHandles).toEqual(["wx", "r"]);
  });

  it("refuses to overwrite an existing wallet and preserves its content and mode", async () => {
    const outputPath = await outputFixture();
    await installWalletSecret(outputPath, "original-secret");
    await chmod(outputPath, 0o640);

    await expect(
      installWalletSecret(outputPath, "replacement-secret"),
    ).rejects.toThrow(
      "dedicated canary wallet already exists; refusing to overwrite it",
    );

    expect(await readFile(outputPath, "utf8")).toBe(
      "SOLANA_PRIVATE_KEY=original-secret\n",
    );
    expect((await stat(outputPath)).mode & 0o777).toBe(0o640);
    expect(await directoryEntries(outputPath)).toEqual(["solana-canary.env"]);
  });

  it("cleans up the temporary file when the atomic install fails", async () => {
    const outputPath = await outputFixture();
    const installFailure = Object.assign(new Error("simulated install failure"), {
      code: "EIO",
    });

    await expect(
      installWalletSecret(outputPath, "fixture-secret", {
        link: vi.fn(async () => {
          throw installFailure;
        }),
      }),
    ).rejects.toBe(installFailure);

    expect(await directoryEntries(outputPath)).toEqual([]);
  });

  it("surfaces an install error and warns when temp cleanup also fails", async () => {
    const outputPath = await outputFixture();
    const installFailure = Object.assign(new Error("simulated install failure"), {
      code: "EIO",
    });
    const cleanupFailure = Object.assign(new Error("simulated unlink failure"), {
      code: "EACCES",
    });
    const unlink: WalletFileOperations["unlink"] = async () => {
      throw cleanupFailure;
    };

    let thrown: unknown;
    try {
      await installWalletSecret(outputPath, "fixture-secret", {
        link: async () => {
          throw installFailure;
        },
        unlink,
      });
    } catch (error) {
      thrown = error;
    }

    expect(thrown).toBeInstanceOf(AggregateError);
    const aggregate = thrown as AggregateError;
    expect(aggregate.errors).toEqual([installFailure, cleanupFailure]);
    expect(aggregate.message).toContain("temporary wallet file may remain");
    const entries = await directoryEntries(outputPath);
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatch(/^\.solana-canary\.env\..+\.tmp$/);
  });

  it("cleans up the temporary file when writing fails", async () => {
    const outputPath = await outputFixture();
    const writeFailure = Object.assign(new Error("simulated write failure"), {
      code: "EIO",
    });
    const link = vi.fn<WalletFileOperations["link"]>();
    const failingOpen: WalletFileOperations["open"] = async (path, flags, mode) => {
      const handle = await open(path, flags, mode);
      if (flags !== "wx") return handle;
      const failingHandle: WalletFileHandle = {
        writeFile: async () => {
          throw writeFailure;
        },
        chmod: async (requestedMode) => handle.chmod(requestedMode),
        sync: async () => handle.sync(),
        close: async () => handle.close(),
      };
      return failingHandle;
    };

    await expect(
      installWalletSecret(outputPath, "fixture-secret", {
        open: failingOpen,
        link,
      }),
    ).rejects.toBe(writeFailure);

    expect(link).not.toHaveBeenCalled();
    expect(await directoryEntries(outputPath)).toEqual([]);
  });
});
