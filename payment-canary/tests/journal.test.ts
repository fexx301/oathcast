import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { describe, expect, it } from "vitest";

import { PaymentJournal, JournalBudgetExceeded, JournalConflict } from "../src/journal.js";

function withJournal(test: (journal: PaymentJournal) => void): void {
  const directory = mkdtempSync(join(tmpdir(), "oathcast-journal-"));
  const journal = new PaymentJournal(join(directory, "payments.sqlite3"));
  try {
    test(journal);
  } finally {
    journal.close();
    rmSync(directory, { recursive: true, force: true });
  }
}

function input(operation_id = "app-operation-1") {
  return {
    operation_id,
    principal_id: "principal-1",
    idempotency_key: `idem-${operation_id}`,
    request_fingerprint: "a".repeat(64),
    policy_sha256: "b".repeat(64),
    miner_id: "212",
    endpoint: "forecast",
    target_sha256: "c".repeat(64),
    challenge_sha256: "d".repeat(64),
    amount_micro_usdc: 10_000,
    max_total_micro_usdc: 10_000,
    max_requests: 1,
  } as const;
}

describe("private payment journal", () => {
  it("replays a settled idempotency key without reserving another budget unit", () => {
    withJournal((journal) => {
      const first = journal.reserve(input());
      expect(first.kind).toBe("reserved");
      journal.markSubmitted(input().operation_id);
      journal.markSettled(input().operation_id, {
        response_status: 200,
        response_body_text: '{"probability":0.4}',
        response_body_sha256: "e".repeat(64),
        response_body_is_json: true,
        settlement_artifact_sha256: "f".repeat(64),
        transaction_signature: "transaction-signature",
        verification_json: '{"confirmed_transaction":true}',
      });
      const replay = journal.reserve(input());
      expect(replay.kind).toBe("replay");
      expect(replay.record.status).toBe("settled_verified");
      expect(journal.list()).toHaveLength(1);
      expect(journal.integrityCheck()).toBe("ok");
    });
  });

  it("rejects a reused key with a different request binding and enforces the budget", () => {
    withJournal((journal) => {
      journal.reserve(input());
      expect(() => journal.reserve({ ...input(), request_fingerprint: "9".repeat(64) }))
        .toThrow(JournalConflict);
      expect(() => journal.reserve({ ...input("app-operation-2"), idempotency_key: "idem-2" }))
        .toThrow(JournalBudgetExceeded);
    });
  });

  it("recovers unfinished work as unknown and only permits explicit reconciliation", () => {
    const directory = mkdtempSync(join(tmpdir(), "oathcast-recovery-"));
    const path = join(directory, "payments.sqlite3");
    const first = new PaymentJournal(path);
    first.reserve(input());
    first.close();

    const recovered = new PaymentJournal(path);
    try {
      expect(recovered.recoverUnfinished()).toBe(1);
      expect(recovered.get(input().operation_id)?.status).toBe("unknown");
      const result = recovered.reconcileUnknown(input().operation_id, {
        verification_method: "solana_rpc",
        verification_artifact_sha256: "1".repeat(64),
        transaction_signature: "transaction-signature",
        verification_json: '{"confirmed_transaction":true}',
      });
      expect(result.status).toBe("reconciled_verified");
      const replay = recovered.reserve(input());
      expect(replay.kind).toBe("replay");
      expect(replay.record.status).toBe("reconciled_verified");
      expect(recovered.integrityCheck()).toBe("ok");
    } finally {
      recovered.close();
      rmSync(directory, { recursive: true, force: true });
    }
  });
});
