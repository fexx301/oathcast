from __future__ import annotations

import base64
from datetime import timezone
import hashlib
import http.client
import json
import os
import re
import threading
import unittest
from unittest.mock import patch

from oathcast.decision_ui import (
    API_PATH,
    LOGO_PATH,
    LOGO_VERSION,
    MAX_JSON_BODY_BYTES,
    DecisionHTTPServer,
    DecisionApplication,
    DecisionResult,
    MinerEvidence,
    TelegraphDecisionRunner,
    TelegraphNotConfigured,
    ValidationError,
    parse_decision_input,
    render_decision_result,
    render_page,
)


VALID_REQUEST = {
    "activity": "trail run",
    "location": "Lagos outdoor track",
    "latitude": 6.5244,
    "longitude": 3.3792,
    "local_datetime": "2026-08-17T16:00:00+01:00",
    "risk_threshold_percent": 30,
    "consent": True,
}


class DecisionUITests(unittest.TestCase):
    def test_validation_is_strict_and_requires_explicit_consent(self):
        invalid = dict(VALID_REQUEST)
        invalid["consent"] = False
        with self.assertRaises(ValidationError):
            parse_decision_input(invalid)

        invalid = dict(VALID_REQUEST)
        invalid["unexpected"] = "reject me"
        with self.assertRaises(ValidationError):
            parse_decision_input(invalid)

        invalid = dict(VALID_REQUEST)
        invalid["latitude"] = 91
        with self.assertRaises(ValidationError):
            parse_decision_input(invalid)

        parsed = parse_decision_input(
            {
                "activity": "walk",
                "location": "Lagos",
                "lat": 6.5,
                "lon": 3.3,
                "local_date_time": "2026-08-17T16:00:00+01:00",
                "risk_threshold": 25,
                "consent": True,
            }
        )
        self.assertEqual(parsed.activity, "walk")
        self.assertEqual(parsed.local_datetime.utcoffset().total_seconds(), 3600)

    def test_body_cap_returns_413_without_invoking_runner(self):
        calls = []

        def runner(request):
            calls.append(request)
            return {"action": "go", "summary": "ok", "rationale": "ok"}

        enabled_runner = TelegraphDecisionRunner(
            runner,
            routing_configured=True,
            payment_configured=True,
        )
        with running_server(enabled_runner) as address:
            body = b"{" + b"x" * MAX_JSON_BODY_BYTES + b"}"
            status, payload = post_json(address, body)

        self.assertEqual(status, 413)
        self.assertEqual(payload["error"], "body_too_large")
        self.assertEqual(calls, [])

    def test_default_service_fails_closed_with_503(self):
        with running_server() as address:
            status, payload = post_json(address, json.dumps(VALID_REQUEST).encode())

        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "decision_unavailable")
        self.assertNotIn("wallet", json.dumps(payload).lower())

    def test_bare_callable_cannot_enable_the_public_api(self):
        calls = []

        def fixture_runner(request):
            calls.append(request)
            return {"action": "go", "summary": "fixture", "rationale": "fixture"}

        with running_server(fixture_runner) as address:
            status, payload = post_json(address, json.dumps(VALID_REQUEST).encode())

        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "decision_unavailable")
        self.assertEqual(calls, [])

    def test_status_describes_read_only_fixture_mode_and_release_identity(self):
        with patch.dict(
            os.environ,
            {
                "OATHCAST_RELEASE_ID": "safe-ui-test",
                "OATHCAST_SOURCE_SHA256": "a" * 64,
                "OATHCAST_IMAGE_DIGEST": "sha256:" + "b" * 64,
            },
        ):
            payload = DecisionApplication().status_payload()

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["public_mode"], "read_only_fixture")
        self.assertEqual(payload["api_mode"], "fail_closed")
        self.assertTrue(payload["fixture_available"])
        self.assertFalse(payload["live_decision_available"])
        self.assertFalse(payload["decision_api_available"])
        self.assertEqual(payload["release"]["release_id"], "safe-ui-test")
        self.assertEqual(payload["release"]["source_sha256"], "a" * 64)

    def test_status_reports_live_decisions_when_guarded_runner_is_ready(self):
        runner = TelegraphDecisionRunner(
            lambda request: {
                "action": "go",
                "summary": "ok",
                "rationale": "ok",
            },
            routing_configured=True,
            payment_configured=True,
        )

        payload = DecisionApplication(runner).status_payload()

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["api_mode"], "live_decisions")
        self.assertTrue(payload["live_decision_available"])
        self.assertTrue(payload["decision_api_available"])

    def test_disabled_api_rejects_before_request_parsing(self):
        invalid = dict(VALID_REQUEST)
        invalid["consent"] = False
        with patch(
            "oathcast.decision_ui.decode_json_body",
            side_effect=AssertionError("disabled API must not parse a body"),
        ):
            with running_server() as address:
                status, payload = post_json(address, json.dumps(invalid).encode())

        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "decision_unavailable")

    def test_noncanonical_decision_aliases_are_not_exposed(self):
        with running_server() as address:
            for path in ("/decision", "/v1/decision"):
                status, payload = post_json(address, json.dumps(VALID_REQUEST).encode(), path=path)
                self.assertEqual(status, 404)
                self.assertEqual(payload["error"], "not_found")

    def test_safe_output_escaping(self):
        result = DecisionResult(
            action="go",
            summary='<script>alert("x")</script>',
            rationale="<img src=x onerror=alert(1)>",
            miner_evidence=(MinerEvidence("<miner>", "valid", 12.5),),
        )
        rendered = render_decision_result(result)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", rendered)
        self.assertNotIn("<script>alert", rendered)
        self.assertNotIn("<miner>", rendered)

    def test_result_redaction_preserves_noncredential_prose_after_keywords(self):
        result = DecisionResult.from_value(
            {
                "action": "go",
                "summary": "Keep the wallet ready for the trip.",
                "rationale": "The secret garden has covered seating.",
            }
        )

        self.assertEqual(result.summary, "Keep the [redacted] ready for the trip.")
        self.assertEqual(
            result.rationale,
            "The [redacted] garden has covered seating.",
        )

    def test_capability_bearing_runner_returns_clear_decision_and_miner_evidence(self):
        seen = []

        def runner(request):
            seen.append(request)
            return DecisionResult(
                action="relocate",
                summary="Move to the covered court.",
                rationale="The Miner consensus is above the requested threshold.",
                risk_percent=42.0,
                miner_evidence=(
                    MinerEvidence(
                        miner_id="telegraph-miner-1",
                        status="valid",
                        probability_percent=42.0,
                        evidence_id="ev-123",
                        routed_via_telegraph=True,
                        payment_verified=True,
                    ),
                ),
            )

        enabled_runner = TelegraphDecisionRunner(
            runner,
            routing_configured=True,
            payment_configured=True,
        )
        with running_server(enabled_runner) as address:
            status, payload = post_json(address, json.dumps(VALID_REQUEST).encode())

        self.assertEqual(status, 200)
        self.assertEqual(payload["action"], "relocate")
        self.assertEqual(payload["decision"], "relocate")
        self.assertEqual(payload["miner_evidence"][0]["miner_id"], "telegraph-miner-1")
        self.assertTrue(payload["miner_evidence"][0]["payment_verified"])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].location, VALID_REQUEST["location"])

    def test_each_incomplete_telegraph_capability_stays_fail_closed(self):
        def runner(request):
            return {"action": "go", "summary": "ok", "rationale": "ok"}

        for routing_configured, payment_configured in ((True, False), (False, True), (False, False)):
            guarded = TelegraphDecisionRunner(
                runner,
                routing_configured=routing_configured,
                payment_configured=payment_configured,
            )
            with running_server(guarded) as address:
                status, payload = post_json(address, json.dumps(VALID_REQUEST).encode())
            self.assertEqual(status, 503)
            self.assertEqual(payload["error"], "decision_unavailable")

    def test_health_reports_degraded_without_calling_the_runner(self):
        with running_server() as address:
            connection = http.client.HTTPConnection(*address, timeout=2)
            connection.request("GET", "/health")
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()

        self.assertEqual(response.status, 200)
        self.assertFalse(payload["ready"])
        self.assertFalse(payload["wallet_secrets_exposed"])

    def test_public_page_is_truthful_and_has_no_live_submit_path(self):
        rendered = render_page()

        self.assertIs(rendered, render_page())
        self.assertIn("Live decisions are not", rendered)
        self.assertIn(f'src="{LOGO_PATH}?v={LOGO_VERSION}"', rendered)
        self.assertIn('class="brand-mark"', rendered)
        self.assertNotIn(".brand::before", rendered)
        self.assertIn("registered and active", rendered)
        self.assertIn("registration ID 78", rendered)
        self.assertIn("routing ID 64173", rendered)
        self.assertIn("Development fixture", rendered)
        self.assertIn("Not Telegraph-routed", rendered)
        self.assertIn("No payment", rendered)
        self.assertIn("Not qualifying demand", rendered)
        self.assertIn("Not a safety guarantee", rendered)
        self.assertIn("Run live decision", rendered)
        self.assertIn("disabled aria-describedby", rendered)
        self.assertNotIn("fetch(", rendered)
        self.assertNotIn("XMLHttpRequest", rendered)
        self.assertNotIn("sendBeacon", rendered)
        self.assertNotIn("WebSocket", rendered)
        self.assertNotIn('id="decision-form"', rendered)
        self.assertNotIn("<form", rendered)
        self.assertIn('type="button">Update example', rendered)
        self.assertIn("Example outcome: CONTINGENCY", rendered)
        self.assertNotIn("—", rendered)
        self.assertNotIn("–", rendered)

    def test_csp_uses_exact_hashes_for_static_inline_assets(self):
        with running_server() as address:
            connection = http.client.HTTPConnection(*address, timeout=2)
            connection.request("GET", "/")
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            policy = response.getheader("Content-Security-Policy")
            connection.close()

        self.assertEqual(response.status, 200)
        self.assertNotIn("unsafe-inline", policy)
        for tag in ("style", "script"):
            match = re.search(rf"<{tag}>(.*?)</{tag}>", body, flags=re.DOTALL)
            self.assertIsNotNone(match)
            digest = base64.b64encode(
                hashlib.sha256(match.group(1).encode("utf-8")).digest()
            ).decode("ascii")
            self.assertIn(f"'sha256-{digest}'", policy)

    def test_logo_asset_is_served_as_cacheable_webp(self):
        with running_server() as address:
            connection = http.client.HTTPConnection(*address, timeout=2)
            connection.request("GET", LOGO_PATH)
            response = connection.getresponse()
            body = response.read()
            content_type = response.getheader("Content-Type")
            cache_controls = response.msg.get_all("Cache-Control")
            connection.close()

        self.assertEqual(response.status, 200)
        self.assertEqual(content_type, "image/webp")
        self.assertEqual(
            cache_controls,
            ["public, max-age=31536000, immutable"],
        )
        self.assertGreater(len(body), 1000)
        self.assertEqual(body[:4], b"RIFF")
        self.assertEqual(body[12:16], b"VP8L")


class RunnerBoundaryTests(unittest.TestCase):
    def test_unconfigured_telegraph_runner_raises_without_network_or_payment(self):
        from oathcast.decision_ui import TelegraphDecisionRunner

        with self.assertRaises(TelegraphNotConfigured):
            TelegraphDecisionRunner()(parse_decision_input(VALID_REQUEST))


class running_server:
    def __init__(self, runner=None):
        self.runner = runner
        self.server = None
        self.thread = None

    def __enter__(self):
        self.server = DecisionHTTPServer(
            ("127.0.0.1", 0),
            decision_runner=self.runner,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        return host, port

    def __exit__(self, exc_type, exc_value, traceback):
        assert self.server is not None
        self.server.shutdown()
        self.server.server_close()
        assert self.thread is not None
        self.thread.join(timeout=2)


def post_json(address, body: bytes, *, path: str = API_PATH):
    connection = http.client.HTTPConnection(*address, timeout=2)
    connection.request(
        "POST",
        path,
        body=body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        },
    )
    response = connection.getresponse()
    payload = json.loads(response.read())
    connection.close()
    return response.status, payload


if __name__ == "__main__":
    unittest.main()
