import io
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

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


def post_to_handler(store, body: bytes, *, content_type: str, content_length: str | None = None):
    handler_type = make_pilot_handler(store)
    handler = object.__new__(handler_type)
    handler.path = "/api/pilot-requests"
    handler.headers = {
        "Content-Length": content_length if content_length is not None else str(len(body)),
        "Content-Type": content_type,
    }
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler.send_response = lambda status: setattr(handler, "status", status)
    handler.send_header = lambda name, value: None
    handler.end_headers = lambda: None
    handler.do_POST()
    return handler.status, json.loads(handler.wfile.getvalue())


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
        with tempfile.TemporaryDirectory() as directory:
            for database in (":memory:", str(Path(directory) / "pilot.sqlite3")):
                with self.subTest(database=database):
                    store = PilotIntakeStore(database)
                    try:
                        plan = build_pilot_plan(PAYLOAD)
                        first = store.save(plan)
                        second = store.save(plan)
                        self.assertEqual(first["request_id"], second["request_id"])
                        self.assertEqual(
                            first["request_sha256"], second["request_sha256"]
                        )
                        self.assertEqual(
                            len(store.list_requests(status="queued")), 1
                        )
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

    def test_store_failure_response_does_not_expose_exception_text(self):
        class FailingStore:
            def save(self, plan):
                raise OSError("secret database path")

        handler_type = make_pilot_handler(FailingStore())
        handler = object.__new__(handler_type)
        encoded = json.dumps(PAYLOAD).encode("utf-8")
        handler.path = "/api/pilot-requests"
        handler.headers = {
            "Content-Length": str(len(encoded)),
            "Content-Type": "application/json",
        }
        handler.rfile = io.BytesIO(encoded)
        handler.wfile = io.BytesIO()
        handler.send_response = lambda status: setattr(handler, "status", status)
        handler.send_header = lambda name, value: None
        handler.end_headers = lambda: None

        with patch("oathcast.pilot.LOGGER.exception") as log_exception:
            handler.do_POST()

        self.assertEqual(handler.status, 500)
        self.assertEqual(
            json.loads(handler.wfile.getvalue()),
            {"error": "pilot_store_error"},
        )
        self.assertNotIn(
            "secret database path",
            handler.wfile.getvalue().decode("utf-8"),
        )
        log_exception.assert_called_once_with("pilot request storage failed")

    def test_non_numeric_content_length_is_a_sanitized_client_error(self):
        class UnusedStore:
            def save(self, plan):
                raise AssertionError("malformed length must fail before storage")

        status, payload = post_to_handler(
            UnusedStore(),
            b"{}",
            content_type="application/json",
            content_length="not-a-number",
        )

        self.assertEqual(status, 400)
        self.assertEqual(
            payload,
            {"error": "Content-Length must be a base-10 integer"},
        )

    def test_duplicate_form_fields_are_rejected_instead_of_last_wins(self):
        class UnusedStore:
            def save(self, plan):
                raise AssertionError("duplicate form fields must fail before storage")

        status, payload = post_to_handler(
            UnusedStore(),
            b"use_case=first&use_case=second",
            content_type="application/x-www-form-urlencoded",
        )

        self.assertEqual(status, 400)
        self.assertEqual(
            payload,
            {"error": "form fields must not be repeated: use_case"},
        )


if __name__ == "__main__":
    unittest.main()
