import { createHash } from "node:crypto";
import { chmodSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { DatabaseSync } from "node:sqlite";

export const JOURNAL_STATUSES = [
  "reserved",
  "submitted",
  "unknown",
  "settled_verified",
  "reconciled_verified",
  "aborted",
] as const;

export type JournalStatus = (typeof JOURNAL_STATUSES)[number];

export interface PaymentJournalRecord {
  operation_id: string;
  principal_id: string;
  idempotency_key: string;
  request_fingerprint: string;
  policy_sha256: string;
  miner_id: string;
  endpoint: string;
  target_sha256: string;
  challenge_sha256: string;
  amount_micro_usdc: number;
  status: JournalStatus;
  response_status: number | null;
  response_body_text: string | null;
  response_body_sha256: string | null;
  response_body_is_json: boolean | null;
  settlement_artifact_sha256: string | null;
  verification_artifact_sha256: string | null;
  transaction_signature: string | null;
  verification_json: string | null;
  error_code: string | null;
  created_at: string;
  updated_at: string;
}

export type ReserveResult =
  | { kind: "reserved"; record: PaymentJournalRecord }
  | { kind: "replay"; record: PaymentJournalRecord };

export class JournalConflict extends Error {}
export class JournalBudgetExceeded extends Error {}
export class JournalDuplicate extends Error {}
export class JournalInProgress extends Error {}
export class JournalOutcomeUnknown extends Error {}

type JsonRecord = Record<string, unknown>;

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

function sha256Json(value: unknown): string {
  return createHash("sha256").update(canonicalJson(value), "utf8").digest("hex");
}

function timestamp(): string {
  return new Date().toISOString();
}

function recordFromRow(value: unknown): PaymentJournalRecord | undefined {
  if (!isRecord(value)) return undefined;
  const status = value.status;
  if (typeof status !== "string" || !JOURNAL_STATUSES.includes(status as JournalStatus)) {
    throw new Error("payment journal contains an unsupported status");
  }
  return {
    operation_id: String(value.operation_id),
    principal_id: String(value.principal_id),
    idempotency_key: String(value.idempotency_key),
    request_fingerprint: String(value.request_fingerprint),
    policy_sha256: String(value.policy_sha256),
    miner_id: String(value.miner_id),
    endpoint: String(value.endpoint),
    target_sha256: String(value.target_sha256),
    challenge_sha256: String(value.challenge_sha256),
    amount_micro_usdc: Number(value.amount_micro_usdc),
    status: status as JournalStatus,
    response_status: value.response_status === null ? null : Number(value.response_status),
    response_body_text:
      typeof value.response_body_text === "string" ? value.response_body_text : null,
    response_body_sha256:
      typeof value.response_body_sha256 === "string" ? value.response_body_sha256 : null,
    response_body_is_json:
      value.response_body_is_json === null ? null : Boolean(value.response_body_is_json),
    settlement_artifact_sha256:
      typeof value.settlement_artifact_sha256 === "string"
        ? value.settlement_artifact_sha256
        : null,
    verification_artifact_sha256:
      typeof value.verification_artifact_sha256 === "string"
        ? value.verification_artifact_sha256
        : null,
    transaction_signature:
      typeof value.transaction_signature === "string" ? value.transaction_signature : null,
    verification_json:
      typeof value.verification_json === "string" ? value.verification_json : null,
    error_code: typeof value.error_code === "string" ? value.error_code : null,
    created_at: String(value.created_at),
    updated_at: String(value.updated_at),
  };
}

function safeErrorCode(value: string): string {
  return /^[A-Z][A-Z0-9_]{0,63}$/.test(value) ? value : "SIDECAR_ERROR";
}

/**
 * Durable, sidecar-owned payment state. The current row is mutable only
 * through the transition methods below; every transition also appends an
 * immutable, hashed event for post-incident review.
 */
export class PaymentJournal {
  readonly path: string;
  private readonly database: DatabaseSync;

  constructor(path: string) {
    this.path = path;
    mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
    chmodSync(dirname(path), 0o700);
    this.database = new DatabaseSync(path);
    chmodSync(path, 0o600);
    this.database.exec("PRAGMA busy_timeout = 10000; PRAGMA foreign_keys = ON;");
    this.database.exec(`
      CREATE TABLE IF NOT EXISTS payment_attempts (
        operation_id TEXT PRIMARY KEY,
        principal_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_fingerprint TEXT NOT NULL,
        policy_sha256 TEXT NOT NULL,
        miner_id TEXT NOT NULL,
        endpoint TEXT NOT NULL,
        target_sha256 TEXT NOT NULL,
        challenge_sha256 TEXT NOT NULL,
        amount_micro_usdc INTEGER NOT NULL CHECK (amount_micro_usdc > 0),
        status TEXT NOT NULL CHECK (
          status IN ('reserved', 'submitted', 'unknown', 'settled_verified',
                     'reconciled_verified', 'aborted')
        ),
        response_status INTEGER,
        response_body_text TEXT,
        response_body_sha256 TEXT,
        response_body_is_json INTEGER,
        settlement_artifact_sha256 TEXT,
        verification_artifact_sha256 TEXT,
        transaction_signature TEXT,
        verification_json TEXT,
        error_code TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );

      CREATE UNIQUE INDEX IF NOT EXISTS payment_attempts_principal_key
        ON payment_attempts (principal_id, idempotency_key);

      CREATE UNIQUE INDEX IF NOT EXISTS payment_attempts_transaction_signature
        ON payment_attempts (transaction_signature)
        WHERE transaction_signature IS NOT NULL;

      CREATE TABLE IF NOT EXISTS payment_attempt_events (
        event_id TEXT PRIMARY KEY,
        operation_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        event_json TEXT NOT NULL,
        event_sha256 TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        FOREIGN KEY (operation_id) REFERENCES payment_attempts(operation_id)
      );

      CREATE TRIGGER IF NOT EXISTS payment_attempt_events_no_update
        BEFORE UPDATE ON payment_attempt_events
        BEGIN SELECT RAISE(ABORT, 'payment journal events are immutable'); END;
      CREATE TRIGGER IF NOT EXISTS payment_attempt_events_no_delete
        BEFORE DELETE ON payment_attempt_events
        BEGIN SELECT RAISE(ABORT, 'payment journal events are immutable'); END;
    `);
    const columns = new Set(
      (this.database.prepare("PRAGMA table_info(payment_attempts)").all() as unknown[])
        .filter(isRecord)
        .map((row) => String(row.name)),
    );
    if (!columns.has("verification_artifact_sha256")) {
      this.database.exec(
        "ALTER TABLE payment_attempts ADD COLUMN verification_artifact_sha256 TEXT",
      );
    }
    this.database.exec(
      `CREATE UNIQUE INDEX IF NOT EXISTS payment_attempts_transaction_signature
       ON payment_attempts (transaction_signature)
       WHERE transaction_signature IS NOT NULL`,
    );
  }

  close(): void {
    this.database.close();
  }

  private rowByOperation(operationId: string): PaymentJournalRecord | undefined {
    return recordFromRow(
      this.database
        .prepare("SELECT * FROM payment_attempts WHERE operation_id = ?")
        .get(operationId),
    );
  }

  private rowByPrincipalKey(
    principalId: string,
    idempotencyKey: string,
  ): PaymentJournalRecord | undefined {
    return recordFromRow(
      this.database
        .prepare(
          "SELECT * FROM payment_attempts WHERE principal_id = ? AND idempotency_key = ?",
        )
        .get(principalId, idempotencyKey),
    );
  }

  private appendEvent(
    operationId: string,
    eventType: string,
    details: JsonRecord = {},
  ): void {
    const event = {
      operation_id: operationId,
      event_type: eventType,
      details,
      occurred_at: timestamp(),
    };
    const eventJson = canonicalJson(event);
    this.database
      .prepare(
        `INSERT INTO payment_attempt_events
          (event_id, operation_id, event_type, event_json, event_sha256, occurred_at)
         VALUES (?, ?, ?, ?, ?, ?)`,
      )
      .run(
        sha256Json(event),
        operationId,
        eventType,
        eventJson,
        createHash("sha256").update(eventJson, "utf8").digest("hex"),
        event.occurred_at,
      );
  }

  private transition(
    operationId: string,
    from: JournalStatus,
    to: JournalStatus,
    details: JsonRecord = {},
    updates: Record<string, unknown> = {},
  ): PaymentJournalRecord {
    const current = this.rowByOperation(operationId);
    if (!current) throw new JournalConflict("payment attempt does not exist");
    if (current.status !== from) {
      throw new JournalConflict(`payment attempt is not in ${from} state`);
    }
    const updatedAt = timestamp();
    const assignments = ["status = ?", "updated_at = ?"];
    const values: unknown[] = [to, updatedAt];
    for (const [column, value] of Object.entries(updates)) {
      if (!/^[a-z0-9_]+$/.test(column)) throw new Error("invalid journal column");
      assignments.push(`${column} = ?`);
      values.push(typeof value === "boolean" ? (value ? 1 : 0) : value);
    }
    values.push(operationId, from);

    this.database.exec("BEGIN IMMEDIATE");
    try {
      const result = this.database
        .prepare(
          `UPDATE payment_attempts SET ${assignments.join(", ")}
           WHERE operation_id = ? AND status = ?`,
        )
        .run(...(values as any[]));
      if (Number(result.changes) !== 1) {
        throw new JournalConflict("payment attempt transition lost a race");
      }
      this.appendEvent(operationId, to, details);
      this.database.exec("COMMIT");
    } catch (error) {
      this.database.exec("ROLLBACK");
      throw error;
    }
    return this.rowByOperation(operationId)!;
  }

  private classifyExisting(
    existing: PaymentJournalRecord,
    input: {
      request_fingerprint: string;
      policy_sha256: string;
      miner_id: string;
      endpoint: string;
    },
  ): ReserveResult {
    if (
      existing.request_fingerprint !== input.request_fingerprint ||
      existing.policy_sha256 !== input.policy_sha256 ||
      existing.miner_id !== input.miner_id ||
      existing.endpoint !== input.endpoint
    ) {
      throw new JournalConflict("idempotency key is bound to different request or policy");
    }
    if (existing.status === "settled_verified" || existing.status === "reconciled_verified") {
      return { kind: "replay", record: existing };
    }
    if (existing.status === "unknown" || existing.status === "submitted") {
      throw new JournalOutcomeUnknown("payment outcome is unknown; reconciliation is required");
    }
    if (existing.status === "reserved") {
      throw new JournalInProgress("payment attempt is already reserved");
    }
    throw new JournalDuplicate("idempotency key has already been consumed");
  }

  reserve(input: {
    operation_id: string;
    principal_id: string;
    idempotency_key: string;
    request_fingerprint: string;
    policy_sha256: string;
    miner_id: string;
    endpoint: string;
    target_sha256: string;
    challenge_sha256: string;
    amount_micro_usdc: number;
    max_total_micro_usdc: number;
    max_requests: number;
  }): ReserveResult {
    const existing = this.rowByPrincipalKey(input.principal_id, input.idempotency_key);
    if (existing) {
      return this.classifyExisting(existing, input);
    }

    if (!Number.isSafeInteger(input.amount_micro_usdc) || input.amount_micro_usdc <= 0) {
      throw new JournalConflict("payment amount must be a positive safe integer");
    }
    if (
      !Number.isSafeInteger(input.max_total_micro_usdc) ||
      input.max_total_micro_usdc <= 0 ||
      !Number.isSafeInteger(input.max_requests) ||
      input.max_requests <= 0
    ) {
      throw new JournalConflict("payment budget is invalid");
    }

    this.database.exec("BEGIN IMMEDIATE");
    try {
      const countRow = this.database
        .prepare(
          `SELECT COUNT(*) AS count, COALESCE(SUM(amount_micro_usdc), 0) AS total
           FROM payment_attempts
           WHERE status <> 'aborted'`,
        )
        .get() as JsonRecord | undefined;
      const count = Number(countRow?.count ?? 0);
      const total = Number(countRow?.total ?? 0);
      if (count >= input.max_requests || total + input.amount_micro_usdc > input.max_total_micro_usdc) {
        throw new JournalBudgetExceeded("payment budget is exhausted");
      }
      const createdAt = timestamp();
      this.database
        .prepare(
          `INSERT INTO payment_attempts (
            operation_id, principal_id, idempotency_key, request_fingerprint,
            policy_sha256, miner_id, endpoint, target_sha256, challenge_sha256,
            amount_micro_usdc, status, response_status, response_body_text,
            response_body_sha256, response_body_is_json, settlement_artifact_sha256,
            transaction_signature, verification_json, error_code, created_at, updated_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', NULL, NULL, NULL, NULL,
                    NULL, NULL, NULL, NULL, ?, ?)`,
        )
        .run(
          input.operation_id,
          input.principal_id,
          input.idempotency_key,
          input.request_fingerprint,
          input.policy_sha256,
          input.miner_id,
          input.endpoint,
          input.target_sha256,
          input.challenge_sha256,
          input.amount_micro_usdc,
          createdAt,
          createdAt,
        );
      this.appendEvent(input.operation_id, "reserved", {
        amount_micro_usdc: input.amount_micro_usdc,
        challenge_sha256: input.challenge_sha256,
        policy_sha256: input.policy_sha256,
      });
      this.database.exec("COMMIT");
    } catch (error) {
      this.database.exec("ROLLBACK");
      if (error instanceof Error && /UNIQUE constraint failed/i.test(error.message)) {
        const raced = this.rowByPrincipalKey(input.principal_id, input.idempotency_key);
        if (raced) return this.classifyExisting(raced, input);
      }
      throw error;
    }
    return { kind: "reserved", record: this.rowByOperation(input.operation_id)! };
  }

  markSubmitted(operationId: string): PaymentJournalRecord {
    return this.transition(operationId, "reserved", "submitted");
  }

  markSettled(
    operationId: string,
    input: {
      response_status: number;
      response_body_text: string;
      response_body_sha256: string;
      response_body_is_json: boolean;
      settlement_artifact_sha256: string;
      transaction_signature: string;
      verification_json: string;
    },
  ): PaymentJournalRecord {
    return this.transition(
      operationId,
      "submitted",
      "settled_verified",
      {
        response_status: input.response_status,
        response_body_sha256: input.response_body_sha256,
        settlement_artifact_sha256: input.settlement_artifact_sha256,
        transaction_signature: input.transaction_signature,
      },
      input,
    );
  }

  markUnknown(
    operationId: string,
    errorCode: string,
    input?: {
      response_status?: number | null;
      response_body_text?: string | null;
      response_body_sha256?: string | null;
      response_body_is_json?: boolean | null;
      settlement_artifact_sha256?: string | null;
      transaction_signature?: string | null;
      verification_json?: string | null;
    },
  ): PaymentJournalRecord {
    const safeCode = safeErrorCode(errorCode);
    return this.transition(
      operationId,
      "submitted",
      "unknown",
      { error_code: safeCode, ...(input ?? {}) },
      {
        error_code: safeCode,
        ...(input
          ? {
              response_status: input.response_status ?? null,
              response_body_text: input.response_body_text ?? null,
              response_body_sha256: input.response_body_sha256 ?? null,
              response_body_is_json:
                input.response_body_is_json === undefined
                  ? null
                  : input.response_body_is_json,
              settlement_artifact_sha256: input.settlement_artifact_sha256 ?? null,
              transaction_signature: input.transaction_signature ?? null,
              verification_json: input.verification_json ?? null,
            }
          : {}),
      },
    );
  }

  abort(operationId: string, errorCode: string): PaymentJournalRecord {
    return this.transition(operationId, "reserved", "aborted", {
      error_code: safeErrorCode(errorCode),
    }, { error_code: safeErrorCode(errorCode) });
  }

  reconcileUnknown(
    operationId: string,
    input: {
      verification_method: "solana_rpc";
      verification_artifact_sha256: string;
      transaction_signature: string;
      verification_json: string;
      response_body_sha256?: string;
    },
  ): PaymentJournalRecord {
    return this.transition(
      operationId,
      "unknown",
      "reconciled_verified",
        {
          verification_method: input.verification_method,
          verification_artifact_sha256: input.verification_artifact_sha256,
          transaction_signature: input.transaction_signature,
        },
        {
          transaction_signature: input.transaction_signature,
          verification_artifact_sha256: input.verification_artifact_sha256,
          ...(input.response_body_sha256 === undefined
            ? {}
            : { response_body_sha256: input.response_body_sha256 }),
          verification_json: input.verification_json,
          error_code: null,
        },
    );
  }

  recoverUnfinished(): number {
    const rows = this.database
      .prepare(
        "SELECT operation_id FROM payment_attempts WHERE status IN ('reserved', 'submitted')",
      )
      .all() as unknown[];
    let recovered = 0;
    for (const row of rows) {
      const operationId = isRecord(row) ? String(row.operation_id) : "";
      if (!operationId) continue;
      const current = this.rowByOperation(operationId);
      if (!current) continue;
      const from = current.status;
      const updatedAt = timestamp();
      this.database.exec("BEGIN IMMEDIATE");
      try {
        const result = this.database
          .prepare(
            "UPDATE payment_attempts SET status = 'unknown', error_code = ?, updated_at = ? WHERE operation_id = ? AND status = ?",
          )
          .run("PROCESS_RECOVERY_UNKNOWN", updatedAt, operationId, from);
        if (Number(result.changes) !== 1) {
          this.database.exec("COMMIT");
          continue;
        }
        this.appendEvent(operationId, "recovered_unknown", { previous_status: from });
        this.database.exec("COMMIT");
        recovered += 1;
      } catch (error) {
        this.database.exec("ROLLBACK");
        throw error;
      }
    }
    return recovered;
  }

  get(operationId: string): PaymentJournalRecord | undefined {
    return this.rowByOperation(operationId);
  }

  getByPrincipalKey(
    principalId: string,
    idempotencyKey: string,
  ): PaymentJournalRecord | undefined {
    return this.rowByPrincipalKey(principalId, idempotencyKey);
  }

  list(): PaymentJournalRecord[] {
    return (this.database
      .prepare("SELECT * FROM payment_attempts ORDER BY created_at, operation_id")
      .all() as unknown[])
      .map(recordFromRow)
      .filter((record): record is PaymentJournalRecord => record !== undefined);
  }

  integrityCheck(): string {
    const result = this.database.prepare("PRAGMA integrity_check").get() as JsonRecord | undefined;
    const status = String(result?.integrity_check ?? "");
    if (status !== "ok") throw new Error(`payment journal integrity check failed: ${status}`);
    const events = this.database
      .prepare("SELECT event_json, event_sha256 FROM payment_attempt_events")
      .all() as unknown[];
    for (const event of events) {
      if (!isRecord(event)) throw new Error("payment journal event is malformed");
      const eventJson = String(event.event_json);
      const digest = createHash("sha256").update(eventJson, "utf8").digest("hex");
      if (digest !== String(event.event_sha256)) {
        throw new Error("payment journal event hash verification failed");
      }
    }
    return status;
  }
}
