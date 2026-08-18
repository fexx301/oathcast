from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from oathcast.demand import DemandEvent, DemandLedger


class DemandLedgerTests(unittest.TestCase):
    def event(self, **overrides):
        values = {
            "question_event_id": "forecast-1",
            "application_request_id": "app-1",
            "miner_id": "211",
            "endpoint": "forecast",
            "transport": "telegraph",
            "routed_through_telegraph": True,
            "payment_method": "x402",
            "payment_status": "settled",
            "payment_evidence": "x402_settlement",
            "http_status": 200,
            "is_fixture": False,
            "source": "application",
            "settlement_verification": "verified",
        }
        values.update(overrides)
        return DemandEvent.create(**values)

    def test_naive_occurrence_time_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.event(occurred_at=datetime(2026, 8, 18, 12, 0))

    def test_only_settled_application_telegraph_response_is_a_local_candidate(self):
        self.assertTrue(self.event().local_candidate)
        self.assertFalse(
            self.event(
                transport="direct_http",
                routed_through_telegraph=False,
            ).local_candidate
        )
        self.assertFalse(
            self.event(
                is_fixture=True,
                source="development-fixture",
                transport="fixture",
                routed_through_telegraph=False,
                payment_method="none",
                payment_status="unpaid",
                payment_evidence="none",
            ).local_candidate
        )
        self.assertFalse(
            self.event(
                payment_status="paid_unverified",
                payment_evidence="none",
            ).local_candidate
        )

    def test_ledger_persists_immutable_hashed_events_and_never_claims_official_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demand.sqlite3"
            ledger = DemandLedger(path)
            first = ledger.append(self.event())
            second = ledger.append(
                self.event(
                    application_request_id="app-2",
                    question_event_id="forecast-2",
                )
            )
            events = ledger.list_events()
            self.assertEqual({item["demand_id"] for item in events}, {first.demand_id, second.demand_id})
            self.assertTrue(all(len(item["event_sha256"]) == 64 for item in events))
            summary = ledger.summary()
            self.assertEqual(summary["total_events"], 2)
            self.assertEqual(summary["local_candidate_events"], 2)
            self.assertIsNone(summary["official_telegraph_count"])
            self.assertEqual(ledger.integrity_check(), "ok")

    def test_events_are_listed_in_append_order_not_wall_clock_order(self):
        ledger = DemandLedger(":memory:")
        first = ledger.append(
            self.event(
                application_request_id="app-later",
                occurred_at=datetime(2026, 8, 18, 13, tzinfo=timezone.utc),
            )
        )
        second = ledger.append(
            self.event(
                application_request_id="app-earlier",
                occurred_at=datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
            )
        )

        self.assertEqual(
            [event["demand_id"] for event in ledger.list_events()],
            [first.demand_id, second.demand_id],
        )

    def test_integrity_check_hashes_exact_stored_event_bytes(self):
        ledger = DemandLedger(":memory:")
        event = ledger.append(self.event())
        connection = ledger._connection()
        stored = connection.execute(
            "SELECT event_json FROM demand_events WHERE demand_id = ?",
            (event.demand_id,),
        ).fetchone()[0]
        connection.execute("DROP TRIGGER demand_events_no_update")
        connection.execute(
            "UPDATE demand_events SET event_json = ? WHERE demand_id = ?",
            (json.dumps(json.loads(stored), indent=2, sort_keys=True), event.demand_id),
        )
        connection.commit()

        with self.assertRaisesRegex(RuntimeError, "hash verification failed"):
            ledger.integrity_check()

    def test_append_begins_write_transaction_before_conflict_check(self):
        ledger = DemandLedger(":memory:")
        statements = []
        ledger._connection().set_trace_callback(statements.append)

        ledger.append(self.event())

        begin = next(
            index
            for index, statement in enumerate(statements)
            if statement.strip().upper().startswith("BEGIN IMMEDIATE")
        )
        conflict_check = next(
            index
            for index, statement in enumerate(statements)
            if statement.strip().upper().startswith("SELECT EVENT_JSON")
        )
        self.assertLess(begin, conflict_check)

    def test_unverified_settlement_artifact_is_retained_but_not_qualifying(self):
        event = self.event(
            settlement_verification="unverified",
            payment_status="paid_unverified",
            payment_evidence="x402_header_unverified",
            settlement_artifact_sha256="a" * 64,
            protocol_receipt_sha256="b" * 64,
        )
        self.assertFalse(event.local_candidate)
        self.assertEqual(event.settlement_artifact_sha256, "a" * 64)

    def test_demand_events_are_immutable(self):
        ledger = DemandLedger(":memory:")
        event = ledger.append(self.event())
        connection = ledger._connection()
        try:
            with self.assertRaises(Exception):
                connection.execute(
                    "UPDATE demand_events SET payment_status = 'failed' WHERE demand_id = ?",
                    (event.demand_id,),
                )
            with self.assertRaises(Exception):
                connection.execute(
                    "DELETE FROM demand_events WHERE demand_id = ?",
                    (event.demand_id,),
                )
        finally:
            if ledger._memory_connection is None:
                connection.close()

    def test_fixture_source_must_be_explicit(self):
        with self.assertRaises(ValueError):
            self.event(is_fixture=True)


if __name__ == "__main__":
    unittest.main()
