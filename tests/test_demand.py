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
