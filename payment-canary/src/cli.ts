#!/usr/bin/env node

import { runCanary, type CanaryOptions } from "./canary.js";
import { fileURLToPath } from "node:url";

class CliError extends Error {}

function usage(): string {
  return `Usage:
  npm run canary -- --dispatcher-url <url> --operation-id <id> [options]

Required:
  --dispatcher-url <url>    HTTPS Telegraph dispatcher base URL
  --operation-id <id>       One-shot idempotency identifier

Optional:
  --target-url <url>        Direct HTTPS route; mutually exclusive with dispatcher URL
  --miner-id <id>           Miner id (default: 18)
  --path <path>             Miner endpoint path (default: predict)
  --param key=value         Query parameter; may be repeated
  --max-amount <integer>    Maximum USDC base units (default: 10000)
  --rpc-url <https-url>     Solana devnet RPC (default: api.devnet.solana.com)
  --allow-insecure-http-devnet
                            Permit only the pinned live devnet HTTP dispatcher
  --execute                 Sign exactly one validated challenge and retry once
  --help                    Show this help

Preflight is the default and never reads SOLANA_PRIVATE_KEY. Execution reads
the key only from SOLANA_PRIVATE_KEY in the process environment; no key file is
created or loaded.
`;
}

function requireValue(argv: string[], index: number, flag: string): string {
  const value = argv[index + 1];
  if (!value || value.startsWith("--")) throw new CliError(`${flag} requires a value`);
  return value;
}

function parseParam(value: string): [string, string] {
  const separator = value.indexOf("=");
  if (separator <= 0) throw new CliError("--param must use key=value");
  return [value.slice(0, separator), value.slice(separator + 1)];
}

export function parseCliArgs(argv: string[], environment: NodeJS.ProcessEnv = process.env): CanaryOptions {
  let dispatcherUrl: string | undefined;
  let dispatcherFlagProvided = false;
  let targetUrl: string | undefined;
  let minerId = "18";
  let endpointPath = "predict";
  let operationId: string | undefined;
  let execute = false;
  let allowInsecureHttpDevnet = false;
  let maxAmount: string | undefined;
  let rpcUrl: string | undefined;
  const params: Record<string, string> = {};

  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    switch (flag) {
      case "--help":
        throw new CliError("__HELP__");
      case "--dispatcher-url":
        dispatcherUrl = requireValue(argv, index, flag);
        dispatcherFlagProvided = true;
        index += 1;
        break;
      case "--target-url":
        targetUrl = requireValue(argv, index, flag);
        index += 1;
        break;
      case "--miner-id":
        minerId = requireValue(argv, index, flag);
        index += 1;
        break;
      case "--path":
        endpointPath = requireValue(argv, index, flag);
        index += 1;
        break;
      case "--operation-id":
        operationId = requireValue(argv, index, flag);
        index += 1;
        break;
      case "--max-amount":
        maxAmount = requireValue(argv, index, flag);
        index += 1;
        break;
      case "--rpc-url":
        rpcUrl = requireValue(argv, index, flag);
        index += 1;
        break;
      case "--param": {
        const [key, value] = parseParam(requireValue(argv, index, flag));
        params[key] = value;
        index += 1;
        break;
      }
      case "--execute":
        execute = true;
        break;
      case "--allow-insecure-http-devnet":
        allowInsecureHttpDevnet = true;
        break;
      default:
        throw new CliError(`unknown option: ${flag}`);
    }
  }

  if (!dispatcherFlagProvided && !targetUrl) {
    dispatcherUrl = environment.TELEGRAPH_DISPATCHER_URL;
  }
  if (!operationId) throw new CliError("--operation-id is required");
  if (!dispatcherUrl && !targetUrl) {
    throw new CliError("--dispatcher-url or --target-url is required");
  }
  if (dispatcherUrl && targetUrl) {
    throw new CliError("--dispatcher-url and --target-url are mutually exclusive");
  }
  return {
    dispatcherUrl,
    targetUrl,
    minerId,
    endpointPath,
    operationId,
    execute,
    allowInsecureHttpDevnet,
    maxAmount,
    rpcUrl,
    params: Object.keys(params).length > 0 ? params : undefined,
  };
}

function cliFailure(message: string): Record<string, unknown> {
  return {
    evidence_version: "oathcast.payment-canary.v1",
    ok: false,
    mode: "preflight",
    preflight: {
      status: null,
      challenge_validated: false,
      payment_attempted: false,
    },
    target: {},
    error: {
      code: "CLI_ARGUMENT_ERROR",
      message: message === "__HELP__" ? "help requested" : "invalid command line arguments",
    },
  };
}

export async function main(argv: string[] = process.argv.slice(2)): Promise<void> {
  let options: CanaryOptions;
  try {
    options = parseCliArgs(argv);
  } catch (error) {
    if (error instanceof CliError && error.message === "__HELP__") {
      console.log(usage());
      return;
    }
    const message = error instanceof CliError ? error.message : "invalid command line";
    console.log(JSON.stringify(cliFailure(message), null, 2));
    process.exitCode = 2;
    return;
  }

  const result = await runCanary(options);
  console.log(JSON.stringify(result.evidence, null, 2));
  if (!result.ok) process.exitCode = 1;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  await main();
}
