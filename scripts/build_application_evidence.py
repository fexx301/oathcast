#!/usr/bin/env python3
"""Build a sanitized, read-only evidence bundle for one Track 3 request.

The source databases contain raw upstream responses and payment-owned material
that must not be copied into a submission artifact.  This script verifies the
stored hashes and cross-database bindings, then emits only allow-listed public
correlation fields.  It never initializes a database, contacts Telegraph or
Solana, and never claims official Telegraph demand.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from oathcast.artifacts import atomic_write_text


ROOT = Path(__file__).resolve().parents[1]
HEX64 = set("0123456789abcdef")


class EvidenceError(RuntimeError):
    """Raised when the retained evidence cannot be verified safely."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_text(_canonical_json(value))


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX64


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def _issue_or_raise(
    issues: list[str],
    message: str,
    *,
    allow_incomplete: bool,
) -> None:
    """Keep known evidence gaps visible while strict mode remains fail-closed."""

    if not allow_incomplete:
        raise EvidenceError(message)
    if message not in issues:
        issues.append(message)


def _read_json(value: Any, *, field: str) -> Any:
    if not isinstance(value, str):
        raise EvidenceError(f"{field} is not stored as JSON text")
    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise EvidenceError(f"{field} contains invalid JSON") from error


def _readonly_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise EvidenceError(f"evidence database does not exist: {path}")
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _integrity_check(connection: sqlite3.Connection, *, label: str) -> None:
    result = connection.execute("PRAGMA integrity_check").fetchone()
    _require(result is not None and result[0] == "ok", f"{label} SQLite integrity check failed")


def _verify_event_hashes(
    connection: sqlite3.Connection,
    *,
    table: str,
    label: str,
) -> int:
    rows = connection.execute(
        f"SELECT event_json, event_sha256 FROM {table} ORDER BY rowid"
    ).fetchall()
    for row in rows:
        event_json = row["event_json"]
        stored = row["event_sha256"]
        _require(
            isinstance(event_json, str) and _sha256_text(event_json) == stored,
            f"{label} event hash verification failed",
        )
    return len(rows)


def _file_reference(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        display = str(resolved.relative_to(ROOT))
    except ValueError:
        display = f"<external>/{resolved.name}"
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": display, "sha256": digest.hexdigest(), "bytes": resolved.stat().st_size}


def _one_row(rows: list[sqlite3.Row], *, label: str) -> sqlite3.Row:
    _require(len(rows) == 1, f"expected exactly one {label}, found {len(rows)}")
    return rows[0]


def _load_payment(
    path: Path,
    *,
    payment_attempt_id: str | None,
    max_amount_micro_usdc: int,
    allow_incomplete: bool,
    issues: list[str],
) -> tuple[dict[str, Any], dict[str, Any], int]:
    connection = _readonly_connection(path)
    try:
        _integrity_check(connection, label="payment journal")
        event_count = _verify_event_hashes(
            connection,
            table="payment_attempt_events",
            label="payment journal",
        )
        query = "SELECT * FROM payment_attempts"
        parameters: tuple[Any, ...] = ()
        if payment_attempt_id:
            query += " WHERE operation_id = ?"
            parameters = (payment_attempt_id,)
        query += " ORDER BY created_at, operation_id"
        row = _one_row(
            list(connection.execute(query, parameters).fetchall()),
            label="payment attempt",
        )
        record = dict(row)
        _require(
            record["status"] in {"settled_verified", "reconciled_verified"},
            "payment attempt is not in a verified settled state",
        )
        amount = int(record["amount_micro_usdc"])
        _require(0 < amount <= max_amount_micro_usdc, "payment amount exceeds the reviewed cap")
        response_status = int(record["response_status"])
        _require(200 <= response_status < 300, "paid response was not successful")
        for field in (
            "operation_id",
            "challenge_sha256",
            "target_sha256",
            "settlement_artifact_sha256",
            "transaction_signature",
            "response_body_sha256",
        ):
            _require(bool(record[field]), f"payment attempt is missing {field}")
        for field in (
            "challenge_sha256",
            "target_sha256",
            "settlement_artifact_sha256",
            "response_body_sha256",
        ):
            _require(_is_hash(record[field]), f"payment {field} is malformed")
        verification = _read_json(record["verification_json"], field="verification_json")
        _require(isinstance(verification, Mapping), "payment verification evidence is not an object")
        _require(
            verification.get("confirmed_transaction") is True
            and verification.get("transaction_error") is False
            and verification.get("transaction_signature_matches") is True
            and verification.get("fee_payer_verified") is True,
            "Solana transaction verification flags are incomplete",
        )
        movement = verification.get("token_movement")
        _require(isinstance(movement, Mapping), "token movement verification is missing")
        _require(
            movement.get("status") == "verified"
            and movement.get("expected_amount") == str(amount),
            "verified token movement does not match the journaled amount",
        )
        computed_verification_artifact_sha256 = _sha256_text(_canonical_json(verification))
        stored_verification_artifact_sha256 = record["verification_artifact_sha256"]
        verification_artifact_persisted = bool(stored_verification_artifact_sha256)
        if verification_artifact_persisted:
            _require(
                _is_hash(stored_verification_artifact_sha256),
                "payment verification artifact hash is malformed",
            )
            _require(
                stored_verification_artifact_sha256 == computed_verification_artifact_sha256,
                "verification artifact hash does not match the stored verification JSON",
            )
        else:
            _issue_or_raise(
                issues,
                "payment journal does not persist verification_artifact_sha256 for this settled attempt",
                allow_incomplete=allow_incomplete,
            )
        selected = {
            "status": record["status"],
            "amount_micro_usdc": amount,
            "response_status": response_status,
            "operation_id": record["operation_id"],
            "principal_id": record["principal_id"],
            "idempotency_key": record["idempotency_key"],
            "miner_id": record["miner_id"],
            "endpoint": record["endpoint"],
            "challenge_sha256": record["challenge_sha256"],
            "target_sha256": record["target_sha256"],
            "response_body_sha256": record["response_body_sha256"],
            "settlement_artifact_sha256": record["settlement_artifact_sha256"],
            "verification_artifact_sha256": stored_verification_artifact_sha256,
            "computed_verification_artifact_sha256": computed_verification_artifact_sha256,
            "verification_artifact_persisted": verification_artifact_persisted,
            "transaction_signature": record["transaction_signature"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
        }
        return selected, dict(verification), event_count
    finally:
        connection.close()


def _load_demand(path: Path, *, payment_attempt_id: str) -> tuple[dict[str, Any], int]:
    connection = _readonly_connection(path)
    try:
        _integrity_check(connection, label="demand ledger")
        event_count = _verify_event_hashes(connection, table="demand_events", label="demand ledger")
        rows = list(
            connection.execute(
                "SELECT * FROM demand_events WHERE payment_attempt_id = ?",
                (payment_attempt_id,),
            ).fetchall()
        )
        row = _one_row(rows, label="demand event for the payment attempt")
        event = dict(row)
        _require(
            event["local_candidate"] == 1
            and event["is_fixture"] == 0
            and event["source"] == "application"
            and event["transport"] == "telegraph"
            and event["routed_through_telegraph"] == 1
            and event["payment_status"] in {"settled", "paid"}
            and event["payment_evidence"] in {"x402_settlement", "supported_method_receipt"}
            and event["settlement_verification"] == "verified"
            and 200 <= int(event["http_status"]) < 300,
            "demand event does not satisfy the conservative local-candidate predicate",
        )
        return {
            "demand_id": event["demand_id"],
            "question_event_id": event["question_event_id"],
            "application_request_id": event["application_request_id"],
            "miner_id": event["miner_id"],
            "endpoint": event["endpoint"],
            "occurred_at": event["occurred_at"],
            "payment_attempt_id": event["payment_attempt_id"],
            "settlement_artifact_sha256": event["settlement_artifact_sha256"],
            "protocol_receipt_sha256": event["protocol_receipt_sha256"],
            "http_status": event["http_status"],
        }, event_count
    finally:
        connection.close()


def _load_application(
    path: Path,
    *,
    event_id: str,
    application_request_id: str,
    payment: Mapping[str, Any],
    allow_incomplete: bool,
    issues: list[str],
) -> dict[str, Any]:
    connection = _readonly_connection(path)
    try:
        _integrity_check(connection, label="application case store")
        case = connection.execute(
            "SELECT * FROM application_cases WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        _require(case is not None, "application case is missing")
        case = dict(case)
        question = _read_json(case["question_json"], field="question_json")
        _require(_sha256_json(question) == case["question_sha256"], "question hash does not match")
        decision_row = connection.execute(
            "SELECT * FROM decisions WHERE event_id = ? ORDER BY decided_at, decision_id",
            (event_id,),
        ).fetchall()
        decision_record = dict(_one_row(list(decision_row), label="application decision"))
        decision = _read_json(decision_record["decision_json"], field="decision_json")
        _require(_sha256_json(decision) == decision_record["decision_sha256"], "decision hash does not match")
        _require(decision.get("application_request_id") == application_request_id, "application request id does not match")
        _require(decision.get("used_external_miner") is True, "decision did not use an external Miner")
        _require(decision.get("external_influence") is True, "external Miner did not influence the decision")

        reply_rows = connection.execute(
            "SELECT * FROM miner_replies WHERE event_id = ? ORDER BY received_at, reply_id",
            (event_id,),
        ).fetchall()
        external_replies = [row for row in reply_rows if str(row["miner_id"]) == "212" and not row["owned"]]
        reply = dict(_one_row(external_replies, label="external Miner reply"))
        _require(reply["protocol_receipt_json"], "external reply has no protocol receipt")
        receipt = _read_json(reply["protocol_receipt_json"], field="protocol_receipt_json")
        _require(isinstance(receipt, Mapping), "protocol receipt is not an object")
        for field in (
            "challenge_sha256",
            "settlement_artifact_sha256",
            "signal_receipt_sha256",
            "registry_snapshot_sha256",
            "response_sha256",
            "response_body_sha256",
            "request_url_sha256",
        ):
            value = receipt.get(field)
            if value is not None and not _is_hash(value):
                _issue_or_raise(
                    issues,
                    f"application protocol receipt {field} is malformed",
                    allow_incomplete=allow_incomplete,
                )
        receipt_hash = _sha256_json(receipt)
        _require(
            receipt.get("settlement_verification") == "verified"
            and receipt.get("response_body_sha256") == payment["response_body_sha256"]
            and receipt.get("payment_attempt_id") == payment["operation_id"]
            and receipt.get("settlement_transaction_signature") == payment["transaction_signature"],
            "application protocol receipt is not bound to the verified payment",
        )
        _require(reply["response_body_sha256"] == payment["response_body_sha256"], "reply body hash does not match payment")
        _require(reply["request_id"] == application_request_id, "reply request id does not match application request")
        if receipt.get("registry_snapshot_sha256") is None:
            _issue_or_raise(
                issues,
                "application protocol receipt has no registry snapshot hash",
                allow_incomplete=allow_incomplete,
            )
        if receipt.get("signal_receipt_sha256") is None:
            _issue_or_raise(
                issues,
                "application protocol receipt has no signal receipt hash",
                allow_incomplete=allow_incomplete,
            )
        resolution_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM resolutions WHERE event_id = ?",
                (event_id,),
            ).fetchone()[0]
        )
        resolved = bool(case["ground_truth_json"] and case["resolved_at"] and resolution_count)
        if not resolved:
            _issue_or_raise(
                issues,
                "application case has no independent observation and resolution",
                allow_incomplete=allow_incomplete,
            )
        return {
            "event_id": event_id,
            "question": {
                key: question.get(key)
                for key in (
                    "location_name",
                    "latitude",
                    "longitude",
                    "horizon_start",
                    "horizon_end",
                    "forecast_cutoff",
                )
            },
            "decision": {
                key: decision.get(key)
                for key in (
                    "recommended_action",
                    "aggregate_probability",
                    "decision_threshold",
                    "event_likely",
                    "used_external_miner",
                    "external_influence",
                    "decided_at",
                )
            },
            "miner_reply": {
                "miner_id": reply["miner_id"],
                "slug": reply["slug"],
                "probability_x10000": reply["probability_x10000"],
                "parser_version": reply["parser_version"],
                "request_id": reply["request_id"],
                "response_body_sha256": reply["response_body_sha256"],
                "protocol_receipt_sha256": receipt_hash,
                "protocol": {
                    key: receipt.get(key)
                    for key in (
                        "route_mode",
                        "response_status",
                        "received_at",
                        "challenge_sha256",
                        "payment_attempt_id",
                        "settlement_artifact_sha256",
                        "settlement_verification",
                        "signal_receipt_sha256",
                        "registry_snapshot_sha256",
                        "response_sha256",
                        "response_body_sha256",
                        "request_url_sha256",
                        "settlement_transaction_signature",
                    )
                },
            },
            "resolution": {
                "status": "resolved" if resolved else "pending",
                "observation_count": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM observations WHERE event_id = ?",
                        (event_id,),
                    ).fetchone()[0]
                ),
                "resolution_count": resolution_count,
            },
        }
    finally:
        connection.close()


def _load_discovery(path: Path, *, miner_id: str, endpoint: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError("discovery snapshot is missing or invalid") from error
    _require(payload.get("source") == "telegraph_integrations_read_only", "discovery is not a Telegraph read-only snapshot")
    _require(_is_hash(payload.get("payload_sha256")), "discovery payload hash is malformed")
    capabilities = payload.get("weather_capabilities")
    _require(isinstance(capabilities, list), "discovery snapshot has no capability list")
    matches = [item for item in capabilities if isinstance(item, Mapping) and str(item.get("id")) == miner_id and item.get("endpoint") == endpoint]
    _require(len(matches) == 1, "discovery snapshot does not contain exactly one reviewed Miner capability")
    match = matches[0]
    return {
        "observed_at": payload.get("observed_at"),
        "source": payload.get("source"),
        "payload_sha256": payload.get("payload_sha256"),
        "miner": {
            "id": str(match.get("id")),
            "slug": match.get("slug"),
            "name": match.get("name"),
            "endpoint": match.get("endpoint"),
            "minimum_price_micro_usdc": match.get("minimum_price_micro_usdc"),
        },
    }


def build_bundle(
    *,
    payment_journal: Path,
    application_db: Path,
    demand_db: Path,
    discovery_snapshot: Path,
    payment_attempt_id: str | None = None,
    max_payment_micro_usdc: int = 10_000,
    allow_incomplete: bool = False,
) -> dict[str, Any]:
    issues: list[str] = []
    payment, verification, payment_event_count = _load_payment(
        payment_journal,
        payment_attempt_id=payment_attempt_id,
        max_amount_micro_usdc=max_payment_micro_usdc,
        allow_incomplete=allow_incomplete,
        issues=issues,
    )
    demand, demand_event_count = _load_demand(
        demand_db,
        payment_attempt_id=payment["operation_id"],
    )
    _require(demand["miner_id"] == payment["miner_id"], "demand Miner id does not match payment")
    _require(demand["endpoint"] == payment["endpoint"], "demand endpoint does not match payment")
    application = _load_application(
        application_db,
        event_id=demand["question_event_id"],
        application_request_id=demand["application_request_id"],
        payment=payment,
        allow_incomplete=allow_incomplete,
        issues=issues,
    )
    _require(application["miner_reply"]["miner_id"] == payment["miner_id"], "application reply Miner id does not match payment")
    discovery = _load_discovery(
        discovery_snapshot,
        miner_id=payment["miner_id"],
        endpoint=payment["endpoint"],
    )
    _require(
        discovery["miner"]["minimum_price_micro_usdc"] is None
        or int(discovery["miner"]["minimum_price_micro_usdc"]) <= payment["amount_micro_usdc"],
        "payment is below the discovered Miner price",
    )

    stored_registry_snapshot_sha256 = application["miner_reply"]["protocol"]["registry_snapshot_sha256"]
    if stored_registry_snapshot_sha256 != discovery["payload_sha256"]:
        _issue_or_raise(
            issues,
            "application protocol receipt registry snapshot does not match the fresh discovery snapshot",
            allow_incomplete=allow_incomplete,
        )

    checks = {
        "payment_journal_integrity": True,
        "payment_events_hashed": payment_event_count >= 3,
        "solana_settlement_verified": verification.get("token_movement", {}).get("status") == "verified",
        "demand_ledger_integrity": True,
        "demand_event_is_local_candidate": True,
        "application_external_influence": application["decision"]["external_influence"] is True,
        "application_receipt_bound_to_payment": True,
        "fresh_discovery_contains_reviewed_miner": True,
        "verification_artifact_persisted": payment["verification_artifact_persisted"],
        "registry_snapshot_linked_to_discovery": stored_registry_snapshot_sha256 == discovery["payload_sha256"],
        "signal_receipt_present": application["miner_reply"]["protocol"]["signal_receipt_sha256"] is not None,
        "application_resolved": application["resolution"]["status"] == "resolved",
    }
    _require(payment_event_count >= 3, "payment journal has fewer than three hashed transition events")
    _require(demand_event_count >= 1, "demand ledger has no hashed events")
    all_checks_passed = all(checks.values()) and not issues
    if not all_checks_passed and not allow_incomplete:
        raise EvidenceError("one or more evidence checks failed")

    files = {
        name: _file_reference(path)
        for name, path in (
            ("payment_journal", payment_journal),
            ("application_db", application_db),
            ("demand_db", demand_db),
            ("discovery_snapshot", discovery_snapshot),
        )
    }
    return {
        "schema_version": 2,
        "evidence_kind": "track3_application_payment",
        "official_demand_claimed": False,
        "local_candidate_observed": True,
        "qualification_note": (
            "This bundle proves one locally verified Application-to-Telegraph payment and response. "
            "It does not establish Telegraph's official demand count or user adoption."
        ),
        "verification": {
            "all_checks_passed": all_checks_passed,
            "checks": checks,
            "issues": issues,
            "payment_verification": {
                "confirmed_transaction": verification.get("confirmed_transaction"),
                "transaction_error": verification.get("transaction_error"),
                "transaction_signature_matches": verification.get("transaction_signature_matches"),
                "fee_payer_verified": verification.get("fee_payer_verified"),
                "token_movement": {
                    key: verification.get("token_movement", {}).get(key)
                    for key in (
                        "status",
                        "expected_amount",
                        "payer_delta",
                        "recipient_delta",
                        "payer",
                        "recipient",
                    )
                },
            },
        },
        "application": {
            **application,
            "application_request_id": demand["application_request_id"],
        },
        "telegraph": {
            "route_mode": application["miner_reply"]["protocol"]["route_mode"],
            "miner_id": payment["miner_id"],
            "endpoint": payment["endpoint"],
            "response_status": payment["response_status"],
            "response_body_sha256": payment["response_body_sha256"],
            "challenge_sha256": payment["challenge_sha256"],
            "target_sha256": payment["target_sha256"],
            "registry_snapshot_sha256": discovery["payload_sha256"],
            "protocol_registry_snapshot_sha256": stored_registry_snapshot_sha256,
            "signal_receipt_sha256": application["miner_reply"]["protocol"]["signal_receipt_sha256"],
        },
        "payment": {
            "method": "x402",
            "network": "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1",
            "asset": "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
            "amount_micro_usdc": payment["amount_micro_usdc"],
            "settlement_verification": "verified",
            "payment_attempt_id": payment["operation_id"],
            "settlement_artifact_sha256": payment["settlement_artifact_sha256"],
            "verification_artifact_sha256": payment["verification_artifact_sha256"],
            "computed_verification_artifact_sha256": payment["computed_verification_artifact_sha256"],
            "verification_artifact_persisted": payment["verification_artifact_persisted"],
            "transaction_signature": payment["transaction_signature"],
        },
        "discovery": discovery,
        "demand": {
            "demand_id": demand["demand_id"],
            "occurred_at": demand["occurred_at"],
            "http_status": demand["http_status"],
            "local_candidate": True,
            "official_telegraph_count": None,
            "protocol_receipt_sha256": demand["protocol_receipt_sha256"],
        },
        "files": files,
    }


def render_markdown(bundle: Mapping[str, Any]) -> str:
    """Render a deterministic, operator-readable view of the sanitized bundle."""

    verification = bundle["verification"]
    checks = verification["checks"]
    issues = verification["issues"]
    payment = bundle["payment"]
    application = bundle["application"]
    telegraph = bundle["telegraph"]
    discovery = bundle["discovery"]
    lines = [
        "# Track 3 application evidence",
        "",
        f"- Verification complete: {'yes' if verification['all_checks_passed'] else 'no'}",
        f"- Official Telegraph demand claimed: {'yes' if bundle['official_demand_claimed'] else 'no'}",
        f"- Local candidate observed: {'yes' if bundle['local_candidate_observed'] else 'no'}",
        "",
        "## Request",
        "",
        f"- Miner: `{telegraph['miner_id']}` / `{telegraph['endpoint']}`",
        f"- HTTP status: `{telegraph['response_status']}`",
        f"- Response body SHA-256: `{telegraph['response_body_sha256']}`",
        f"- Application request: `{application['application_request_id']}`",
        f"- Resolution status: `{application['resolution']['status']}`",
        "",
        "## Payment",
        "",
        f"- Network: `{payment['network']}`",
        f"- Asset: `{payment['asset']}`",
        f"- Amount: `{payment['amount_micro_usdc']}` micro-USDC",
        f"- Settlement: `{payment['settlement_verification']}`",
        f"- Transaction: `{payment['transaction_signature']}`",
        f"- Verification artifact persisted in journal: `{'yes' if payment['verification_artifact_persisted'] else 'no'}`",
        f"- Recomputed verification artifact SHA-256: `{payment['computed_verification_artifact_sha256']}`",
        "",
        "## Discovery",
        "",
        f"- Observed: `{discovery['observed_at']}`",
        f"- Snapshot SHA-256: `{discovery['payload_sha256']}`",
        f"- Registry hash linked in protocol receipt: `{telegraph['protocol_registry_snapshot_sha256']}`",
        f"- Signal receipt hash: `{telegraph['signal_receipt_sha256'] or 'not present'}`",
        "",
        "## Verification checks",
        "",
    ]
    lines.extend(f"- [{'x' if value else ' '}] {name}" for name, value in checks.items())
    if issues:
        lines.extend(["", "## Open evidence gaps", ""])
        lines.extend(f"- {issue}" for issue in issues)
    lines.extend(
        [
            "",
            "This sanitized artifact excludes the raw paid response, payment authorization headers, private keys, and principal/idempotency secrets. It is local payment evidence, not an official Telegraph demand or adoption count.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payment-journal", type=Path, required=True)
    parser.add_argument("--application-db", type=Path, required=True)
    parser.add_argument("--demand-db", type=Path, required=True)
    parser.add_argument("--discovery-snapshot", type=Path, required=True)
    parser.add_argument("--payment-attempt-id")
    parser.add_argument("--max-payment-micro-usdc", type=int, default=10_000)
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="write a sanitized partial bundle while listing unresolved evidence gaps",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        bundle = build_bundle(
            payment_journal=args.payment_journal,
            application_db=args.application_db,
            demand_db=args.demand_db,
            discovery_snapshot=args.discovery_snapshot,
            payment_attempt_id=args.payment_attempt_id,
            max_payment_micro_usdc=args.max_payment_micro_usdc,
            allow_incomplete=args.allow_incomplete,
        )
        if args.output.exists():
            raise EvidenceError(f"refusing to overwrite existing evidence artifact: {args.output}")
        if args.markdown_output and args.markdown_output.exists():
            raise EvidenceError(
                f"refusing to overwrite existing evidence artifact: {args.markdown_output}"
            )
        encoded = json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        atomic_write_text(args.output, encoded)
        if args.markdown_output:
            atomic_write_text(args.markdown_output, render_markdown(bundle))
        print(
            f"wrote sanitized evidence bundle: {args.output} "
            f"({'complete' if bundle['verification']['all_checks_passed'] else 'incomplete'})"
        )
        return 0
    except (EvidenceError, OSError, ValueError) as error:
        print(f"evidence build failed: {error}", file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
