from __future__ import annotations

from datetime import datetime, timezone
from http.client import HTTPResponse
from pathlib import Path
import json
import socket
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from oathcast.application_gateway import (
    APPLICATION_PATH,
    LiveApplicationService,
    make_application_gateway,
)
from oathcast.application_payment import (
    ApplicationPaymentBoundary,
    PaymentBoundaryUnavailable,
    PaymentConsentRequired,
    PaymentOutcomeUnknown,
    PaymentPolicyConflict,
    UnixSocketPaymentClient,
    _paid_response_from_mapping,
    canonical_request_fingerprint,
)
from oathcast.cases import CaseConflict, SqliteCaseStore
from oathcast.demand import DemandLedger
from oathcast.discovery import MinerCapability


UTC = timezone.utc


class FakeSidecarClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def request(self, payload):
        self.calls.append(dict(payload))
        return {
            "version": 1,
            "ok": True,
            "operation_id": "app-operation-1",
            "payment_attempt_id": "app-operation-1",
            "status": 200,
            "body": {"probability": 0.7},
            "body_sha256": "a" * 64,
            "challenge_sha256": "b" * 64,
            "target_sha256": "c" * 64,
            "settlement_artifact_sha256": "d" * 64,
            "transaction_signature": "fixture-signature",
            "received_at": "2026-09-05T14:59:00.000Z",
            "verification": {"confirmed_transaction": True},
            "evidence": {
                "evidence_version": "oathcast.payment-canary.v1",
                "ok": True,
                "mode": "execute",
                "operation_id": "app-operation-1",
                "target": {"request_url_sha256": "c" * 64},
                "preflight": {
                    "challenge_sha256": "b" * 64,
                    "challenge_validated": True,
                    "payment_attempted": True,
                },
                "paid_response_status": 200,
                "paid_response_body_sha256": "a" * 64,
                "settlement": {
                    "header_sha256": "d" * 64,
                    "transaction_signature": "fixture-signature",
                    "success": True,
                    "network": "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1",
                },
                "verification": {
                    "confirmed_transaction": True,
                    "transaction_error": False,
                    "transaction_signature_matches": True,
                    "fee_payer_verified": True,
                    "token_movement": {"status": "verified"},
                },
            },
        }


def make_service(directory: str) -> tuple[LiveApplicationService, FakeSidecarClient]:
    client = FakeSidecarClient()
    boundary = ApplicationPaymentBoundary(
        client,
        allowed_miner_ids={"999"},
        allowed_endpoints={"predict"},
    )
    capability = MinerCapability(
        "999",
        "external-weather",
        "External Weather",
        "https://dispatcher.example",
        frozenset({"WEATHER_FORECAST"}),
        endpoint_name="predict",
    )
    return (
        LiveApplicationService(
            capabilities=(capability,),
            payment_boundary=boundary,
            case_store=SqliteCaseStore(Path(directory) / "cases.sqlite3"),
            demand_ledger=DemandLedger(Path(directory) / "demand.sqlite3"),
        ),
        client,
    )


class ApplicationPaymentTests(unittest.TestCase):
    def test_socket_timeout_after_send_is_an_unknown_payment_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            socket_path = str(Path(directory) / "payment.sock")
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(socket_path)
                listener.listen(1)
            except Exception:
                listener.close()
                raise

            def hold_response():
                connection, _address = listener.accept()
                try:
                    connection.recv(4096)
                    time.sleep(0.2)
                finally:
                    connection.close()

            thread = threading.Thread(target=hold_response, daemon=True)
            thread.start()
            try:
                client = UnixSocketPaymentClient(
                    socket_path,
                    "x" * 32,
                    timeout_seconds=0.05,
                )
                with self.assertRaises(PaymentOutcomeUnknown):
                    client.request({"kind": "paid_miner_request"})
            finally:
                listener.close()
                thread.join(timeout=1)

    def test_boundary_requires_consent_and_canonical_request_binding(self):
        client = FakeSidecarClient()
        boundary = ApplicationPaymentBoundary(
            client,
            allowed_miner_ids={"999"},
            allowed_endpoints={"predict"},
        )
        params = {"lat": "6.524400", "lon": "3.379200"}
        fingerprint = canonical_request_fingerprint(
            principal_id="user-1",
            idempotency_key="request-1",
            miner_id="999",
            endpoint="predict",
            params=params,
        )
        with self.assertRaises(PaymentConsentRequired):
            boundary.request_miner(
                principal_id="user-1",
                idempotency_key="request-1",
                request_fingerprint=fingerprint,
                miner_id="999",
                endpoint="predict",
                params=params,
                consent=False,
            )
        with self.assertRaises(PaymentPolicyConflict):
            boundary.request_miner(
                principal_id="user-1",
                idempotency_key="request-1",
                request_fingerprint="0" * 64,
                miner_id="999",
                endpoint="predict",
                params=params,
                consent=True,
            )
        self.assertEqual(client.calls, [])

    def test_boundary_requires_exact_chain_verification_flags(self):
        payload = FakeSidecarClient().request({})
        payload["evidence"]["verification"]["fee_payer_verified"] = None
        with self.assertRaises(PaymentBoundaryUnavailable):
            _paid_response_from_mapping(payload)

    def test_live_service_routes_external_miner_and_replays_sealed_case(self):
        with tempfile.TemporaryDirectory() as directory:
            service, client = make_service(directory)
            request = {
                "activity": "Outdoor event",
                "location": "Lagos",
                "latitude": 6.5244,
                "longitude": 3.3792,
                "local_datetime": "2026-09-05T15:00:00Z",
                "risk_threshold_percent": 50,
                "consent": True,
            }
            from oathcast.decision_ui import parse_decision_input

            decision_input = parse_decision_input(request)
            first = service.decide(
                decision_input,
                principal_id="user-1",
                idempotency_key="request-1",
            )
            second = service.decide(
                decision_input,
                principal_id="user-1",
                idempotency_key="request-1",
            )
            self.assertEqual(first.action, "contingency")
            self.assertEqual(first.risk_percent, 70.0)
            self.assertEqual(first.request_id, second.request_id)
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(service.demand_ledger.summary()["local_candidate_events"], 1)
            service.case_store.close()
            service.demand_ledger.close()

    def test_application_idempotency_key_is_bound_to_the_full_request(self):
        with tempfile.TemporaryDirectory() as directory:
            service, _client = make_service(directory)
            from oathcast.decision_ui import parse_decision_input

            first = parse_decision_input(
                {
                    "activity": "Outdoor event",
                    "location": "Lagos",
                    "latitude": 6.5244,
                    "longitude": 3.3792,
                    "local_datetime": "2026-09-05T15:00:00Z",
                    "risk_threshold_percent": 50,
                    "consent": True,
                }
            )
            service.decide(first, principal_id="user-1", idempotency_key="request-1")
            changed = parse_decision_input(
                {
                    "activity": "Outdoor event",
                    "location": "Tokyo",
                    "latitude": 35.6762,
                    "longitude": 139.6503,
                    "local_datetime": "2026-09-05T15:00:00Z",
                    "risk_threshold_percent": 50,
                    "consent": True,
                }
            )
            with self.assertRaises(CaseConflict):
                service.decide(changed, principal_id="user-1", idempotency_key="request-1")
            service.case_store.close()
            service.demand_ledger.close()

    def test_private_gateway_rejects_missing_auth_and_serves_authenticated_result(self):
        with tempfile.TemporaryDirectory() as directory:
            service, _client = make_service(directory)
            server = make_application_gateway(
                service,
                app_token="application-token-" + "x" * 20,
                port=0,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                payload = json.dumps(
                    {
                        "activity": "Outdoor event",
                        "location": "Lagos",
                        "latitude": 6.5244,
                        "longitude": 3.3792,
                        "local_datetime": "2026-09-05T15:00:00Z",
                        "risk_threshold_percent": 50,
                        "consent": True,
                    }
                ).encode()
                base = f"http://{host}:{port}{APPLICATION_PATH}"
                with self.assertRaises(HTTPError) as missing:
                    urlopen(
                        Request(
                            base,
                            data=payload,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        ),
                        timeout=2,
                    )
                self.assertEqual(missing.exception.code, 401)
                response = urlopen(
                    Request(
                        base,
                        data=payload,
                        headers={
                            "Authorization": "Bearer " + "application-token-" + "x" * 20,
                            "X-OathCast-Principal": "user-1",
                            "Idempotency-Key": "request-1",
                            "Content-Type": "application/json",
                        },
                        method="POST",
                    ),
                    timeout=2,
                )
                result = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(result["decision"], "contingency")
                self.assertTrue(result["miner_evidence"][0]["payment_verified"])

                changed_request = json.loads(payload)
                changed_request.update(
                    {"location": "Tokyo", "latitude": 35.6762, "longitude": 139.6503}
                )
                with self.assertRaises(HTTPError) as conflict:
                    urlopen(
                        Request(
                            base,
                            data=json.dumps(changed_request).encode(),
                            headers={
                                "Authorization": "Bearer " + "application-token-" + "x" * 20,
                                "X-OathCast-Principal": "user-1",
                                "Idempotency-Key": "request-1",
                                "Content-Type": "application/json",
                            },
                            method="POST",
                        ),
                        timeout=2,
                    )
                self.assertEqual(conflict.exception.code, 409)
                self.assertEqual(json.loads(conflict.exception.read())["error"], "idempotency_conflict")
            finally:
                server.shutdown()
                server.server_close()
                service.case_store.close()
                service.demand_ledger.close()


if __name__ == "__main__":
    unittest.main()
