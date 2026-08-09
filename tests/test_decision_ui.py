from __future__ import annotations

from datetime import timezone
import http.client
import json
import threading
import unittest

from oathcast.decision_ui import (
    API_PATH,
    MAX_JSON_BODY_BYTES,
    DecisionHTTPServer,
    DecisionResult,
    MinerEvidence,
    TelegraphNotConfigured,
    ValidationError,
    parse_decision_input,
    render_decision_result,
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

        with running_server(runner) as address:
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

    def test_http_validation_happens_before_runner_readiness(self):
        invalid = dict(VALID_REQUEST)
        invalid["consent"] = False
        with running_server() as address:
            status, payload = post_json(address, json.dumps(invalid).encode())

        self.assertEqual(status, 422)
        self.assertEqual(payload["error"], "invalid_request")
        self.assertIn("consent", payload["fields"])

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

    def test_injected_runner_returns_clear_decision_and_miner_evidence(self):
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

        with running_server(runner) as address:
            status, payload = post_json(address, json.dumps(VALID_REQUEST).encode())

        self.assertEqual(status, 200)
        self.assertEqual(payload["action"], "relocate")
        self.assertEqual(payload["decision"], "relocate")
        self.assertEqual(payload["miner_evidence"][0]["miner_id"], "telegraph-miner-1")
        self.assertTrue(payload["miner_evidence"][0]["payment_verified"])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].location, VALID_REQUEST["location"])

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


def post_json(address, body: bytes):
    connection = http.client.HTTPConnection(*address, timeout=2)
    connection.request(
        "POST",
        API_PATH,
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
