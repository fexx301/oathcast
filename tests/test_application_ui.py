from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import re
import threading
import unittest

from oathcast.application_ui import DEFAULT_GATEWAY_URL, LoopbackApplicationRunner
from oathcast.decision_ui import (
    DecisionApplication,
    DecisionHTTPServer,
    DemoDecisionRunner,
    TelegraphDecisionRunner,
    parse_decision_input,
    render_page,
)
from oathcast.decision_ui import _content_security_policy


REQUEST = {
    "activity": "market setup",
    "location": "Lagos",
    "latitude": 6.5244,
    "longitude": 3.3792,
    "local_datetime": "2030-01-01T15:00:00+00:00",
    "risk_threshold_percent": 30,
    "consent": True,
}


class ApplicationUITests(unittest.TestCase):
    def test_demo_runner_opens_only_an_explicit_demo_surface(self):
        app = DecisionApplication(DemoDecisionRunner())
        self.assertEqual(app.public_mode, "demo")
        status = app.status_payload()
        self.assertTrue(status["decision_api_available"])
        self.assertTrue(status["interactive_demo_available"])
        self.assertFalse(status["live_decision_available"])
        self.assertFalse(status["telegraph_routing_and_payment_configured"])

        page = render_page(application=app)
        self.assertIn("Interactive demo", page)
        self.assertIn('id="decision-form"', page)
        self.assertIn("never calls Telegraph", page)
        self.assertIn("Date (UTC)", page)

    def test_demo_request_returns_a_stable_non_telegraph_result(self):
        server = DecisionHTTPServer(("127.0.0.1", 0), decision_runner=DemoDecisionRunner())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            body = json.dumps(REQUEST).encode()
            connection = http.client.HTTPConnection(host, port, timeout=2)
            connection.request(
                "POST",
                "/api/decision",
                body=body,
                headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(response.status, 200)
        self.assertIn(payload["action"], {"go", "contingency"})
        self.assertEqual(payload["miner_evidence"][0]["miner_id"], "local-demo")
        self.assertFalse(payload["miner_evidence"][0]["routed_via_telegraph"])

    def test_loopback_runner_requires_the_fixed_gateway_and_probes_readiness(self):
        captured: dict[str, str] = {}

        class GatewayHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "ready": True,
                            "public_ui_enabled": False,
                            "payment_boundary": "private_unix_socket",
                        }
                    ).encode()
                )

            def do_POST(self):
                captured.update(
                    {
                        "authorization": self.headers.get("Authorization", ""),
                        "principal": self.headers.get("X-OathCast-Principal", ""),
                        "idempotency": self.headers.get("Idempotency-Key", ""),
                    }
                )
                length = int(self.headers["Content-Length"])
                json.loads(self.rfile.read(length))
                body = json.dumps(
                    {
                        "ok": True,
                        "action": "go",
                        "summary": "Gateway result",
                        "rationale": "The configured route is below the threshold.",
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        gateway = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
        gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        gateway_thread.start()
        try:
            url = f"http://127.0.0.1:{gateway.server_address[1]}/v1/application/forecast"
            runner = LoopbackApplicationRunner(url, "t" * 32)
            self.assertTrue(runner.configured)
            result = runner(parse_decision_input(REQUEST))
            self.assertEqual(result["action"], "go")
            self.assertEqual(captured["authorization"], "Bearer " + "t" * 32)
            self.assertEqual(captured["principal"], "judge-public")
            self.assertTrue(captured["idempotency"].startswith("public-"))

            live = DecisionApplication(
                TelegraphDecisionRunner(runner, routing_configured=True, payment_configured=True)
            )
            page = render_page(application=live)
            self.assertIn("Live Telegraph route", page)
            self.assertIn("private Application boundary", page)
            match = re.search(r"<style>(.*?)</style>", page, flags=re.DOTALL)
            script = re.search(r"<script>(.*?)</script>", page, flags=re.DOTALL)
            self.assertIsNotNone(match)
            self.assertIsNotNone(script)
            self.assertIn(
                "'sha256-" + base64.b64encode(hashlib.sha256(match.group(1).encode()).digest()).decode() + "'",
                _content_security_policy(page),
            )
        finally:
            gateway.shutdown()
            gateway.server_close()
            gateway_thread.join(timeout=2)

    def test_loopback_runner_rejects_public_gateway_urls(self):
        with self.assertRaises(ValueError):
            LoopbackApplicationRunner("https://example.com/v1/application/forecast", "t" * 32)
        with self.assertRaises(ValueError):
            LoopbackApplicationRunner(DEFAULT_GATEWAY_URL.replace("127.0.0.1", "example.com"), "t" * 32)


if __name__ == "__main__":
    unittest.main()
