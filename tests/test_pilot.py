from datetime import datetime, timezone
import unittest

from oathcast.pilot import (
    PilotIntakeStore,
    PilotValidationError,
    build_pilot_plan,
    make_pilot_handler,
    render_pilot_html,
)


PAYLOAD = {
    "use_case": "Move a Saturday market setup indoors if rain is likely.",
    "location_name": "Lagos",
    "latitude": "6.5244",
    "longitude": "3.3792",
    "forecast_cutoff": "2026-08-17T12:00:00Z",
    "horizon_start": "2026-08-17T15:00:00Z",
    "horizon_end": "2026-08-17T16:00:00Z",
}


class PilotTests(unittest.TestCase):
    def test_plan_identity_is_stable_and_contract_is_explicit(self):
        first = build_pilot_plan(
            PAYLOAD,
            submitted_at=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
        )
        second = build_pilot_plan(
            PAYLOAD,
            submitted_at=datetime(2026, 8, 2, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(first.request_id, second.request_id)
        self.assertEqual(first.question.event_id, second.question.event_id)
        self.assertEqual(first.question.threshold_mm, 0.1)
        self.assertEqual(first.question.operator, ">")

    def test_invalid_window_is_rejected_as_a_pilot_validation_error(self):
        invalid = {**PAYLOAD, "horizon_end": PAYLOAD["horizon_start"]}
        with self.assertRaises(PilotValidationError):
            build_pilot_plan(invalid)

    def test_store_is_idempotent_and_lists_only_queued_requests(self):
        store = PilotIntakeStore(":memory:")
        try:
            plan = build_pilot_plan(PAYLOAD)
            first = store.save(plan)
            second = store.save(plan)
            self.assertEqual(first["request_id"], second["request_id"])
            self.assertEqual(first["request_sha256"], second["request_sha256"])
            self.assertEqual(len(store.list_requests(status="queued")), 1)
        finally:
            store.close()

    def test_html_marks_local_intake_boundary(self):
        rendered = render_pilot_html()
        self.assertIn("OathCast Planning Desk", rendered)
        self.assertIn("Preparation mode", rendered)
        self.assertIn("does not contact Miners", rendered)
        self.assertIn("/api/pilot-requests", rendered)

    def test_http_handler_is_bound_to_the_intake_surface(self):
        store = PilotIntakeStore(":memory:")
        try:
            handler = make_pilot_handler(store)
            self.assertEqual(handler.server_version, "OathCastPilot/1")
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
