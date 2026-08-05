import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from oathcast.payment import (
    BASE_SEPOLIA_NETWORK,
    BASE_SEPOLIA_USDC,
    DuplicatePaymentError,
    HttpResult,
    PaymentChallenge,
    PaymentOutcomeUnknown,
    PaymentPreflight,
    PaymentPolicyError,
    PaymentRequiredError,
    SqlitePaymentJournal,
    TelegraphX402Client,
    ValidatedPaymentAuthorization,
)


def encoded_challenge(amount="10000"):
    payload = {
        "x402Version": 2,
        "accepts": [
            {
                "scheme": "exact",
                "network": BASE_SEPOLIA_NETWORK,
                "asset": BASE_SEPOLIA_USDC,
                "amount": amount,
                "payTo": "0xabc",
                "resource": "https://dispatcher.test/v1/18/predict?lat=1",
            }
        ],
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


class PaymentTests(unittest.TestCase):
    def signed_client(self, **kwargs):
        kwargs.setdefault("journal", SqlitePaymentJournal(":memory:"))
        # Test double for an independently verified settlement provider.  A
        # header alone is intentionally not enough in production.
        kwargs.setdefault("settlement_verifier", lambda response, authorization: True)
        return TelegraphX402Client(**kwargs)

    def test_challenge_decodes_and_detects_base_sepolia_usdc(self):
        challenge = PaymentChallenge.decode(encoded_challenge())
        self.assertTrue(challenge.supports_base_sepolia_usdc())
        self.assertEqual(challenge.accepts[0]["amount"], "10000")

    def test_client_exposes_discovery_without_payment(self):
        calls = []

        def transport(method, url, headers):
            calls.append((method, url, headers))
            return HttpResult(200, {}, b"[{\"id\":1}]")

        client = TelegraphX402Client(dispatcher_url="https://dispatcher.test", transport=transport)
        self.assertEqual(client.discover_integrations(), [{"id": 1}])
        self.assertEqual(calls[0][1], "https://dispatcher.test/integrations")
        self.assertNotIn("PAYMENT-SIGNATURE", calls[0][2])

    def test_preflight_returns_a_challenge_without_signing_or_consuming_budget(self):
        calls = []

        def transport(method, url, headers):
            calls.append((method, url, headers))
            return HttpResult(402, {"Payment-Required": encoded_challenge()}, b"")

        client = self.signed_client(
            dispatcher_url="https://dispatcher.test",
            transport=transport,
            signer=lambda authorization: "must-not-run",
            expected_pay_to="0xabc",
            allowed_miner_ids={"18"},
            allowed_endpoints={"predict"},
        )
        result = client.preflight_miner(18, "predict", {"lat": 1})
        self.assertIsInstance(result, PaymentPreflight)
        self.assertEqual(result.status, 402)
        self.assertIsNotNone(result.challenge)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("PAYMENT-SIGNATURE", calls[0][2])

    def test_client_stops_at_402_without_a_real_signer(self):
        def transport(method, url, headers):
            return HttpResult(402, {"Payment-Required": encoded_challenge()}, b"payment required")

        client = TelegraphX402Client(transport=transport)
        with self.assertRaises(PaymentRequiredError) as context:
            client.request_miner(18, "predict", {"lat": 1})
        self.assertTrue(context.exception.challenge.supports_base_sepolia_usdc())

    def test_client_retries_once_with_signer_proof_and_keeps_settlement(self):
        calls = []

        def transport(method, url, headers):
            calls.append(headers)
            if len(calls) == 1:
                return HttpResult(402, {"Payment-Required": encoded_challenge()}, b"")
            self.assertEqual(headers["PAYMENT-SIGNATURE"], "real-proof-from-signer")
            return HttpResult(200, {"x-payment-settle-response": "settled"}, b"{\"ok\":true}")

        client = self.signed_client(
            dispatcher_url="https://dispatcher.test",
            transport=transport,
            signer=lambda authorization: "real-proof-from-signer",
            expected_pay_to="0xabc",
            allowed_miner_ids={"18"},
            allowed_endpoints={"predict"},
        )
        response = client.request_miner(18, "predict", {"lat": 1})
        self.assertEqual(response.body, {"ok": True})
        self.assertEqual(response.settlement_proof, "settled")
        self.assertTrue(response.settlement_verified)
        self.assertEqual(response.settlement_verification, "verified")
        self.assertEqual(len(calls), 2)

    def test_settlement_header_without_verifier_is_not_treated_as_settled(self):
        calls = []

        def transport(method, url, headers):
            calls.append(headers)
            if len(calls) == 1:
                return HttpResult(402, {"Payment-Required": encoded_challenge()}, b"")
            return HttpResult(200, {"x-payment-settle-response": "looks-real"}, b"{}")

        client = TelegraphX402Client(
            dispatcher_url="https://dispatcher.test",
            transport=transport,
            signer=lambda authorization: "proof",
            expected_pay_to="0xabc",
            allowed_miner_ids={"18"},
            allowed_endpoints={"predict"},
            journal=SqlitePaymentJournal(":memory:"),
        )
        with self.assertRaises(PaymentOutcomeUnknown):
            client.request_miner(18, "predict", {"lat": 1})

    def test_decimal_amount_is_not_guessed_as_micro_usdc(self):
        payload = json.loads(base64.b64decode(encoded_challenge()).decode())
        payload["accepts"][0]["amount"] = "0.01"
        challenge = PaymentChallenge(encoded_header="", payload=payload)
        with self.assertRaises(PaymentPolicyError):
            challenge.validate_for_request(
                "https://dispatcher.test/v1/18/predict?lat=1",
                expected_pay_to="0xabc",
                max_amount_micro_usdc=10000,
            )

    def test_expired_challenge_deadline_is_rejected(self):
        payload = json.loads(base64.b64decode(encoded_challenge()).decode())
        payload["deadline"] = "2026-08-03T00:00:00Z"
        challenge = PaymentChallenge(encoded_header="", payload=payload)
        with self.assertRaises(PaymentPolicyError):
            challenge.validate_for_request(
                "https://dispatcher.test/v1/18/predict?lat=1",
                expected_pay_to="0xabc",
                max_amount_micro_usdc=10000,
                now=datetime(2026, 8, 4, tzinfo=timezone.utc),
            )

    def test_application_request_headers_survive_paid_retry(self):
        calls = []

        def transport(method, url, headers):
            calls.append(headers)
            if len(calls) == 1:
                self.assertEqual(headers["X-OathCast-Application-Request-ID"], "app-test")
                return HttpResult(402, {"Payment-Required": encoded_challenge()}, b"")
            self.assertEqual(headers["X-OathCast-Application-Request-ID"], "app-test")
            self.assertEqual(headers["PAYMENT-SIGNATURE"], "proof")
            return HttpResult(200, {"x-payment-settle-response": "settled"}, b"{}")

        client = self.signed_client(
            dispatcher_url="https://dispatcher.test",
            transport=transport,
            signer=lambda authorization: "proof",
            expected_pay_to="0xabc",
            allowed_miner_ids={"18"},
            allowed_endpoints={"predict"},
            settlement_verifier=lambda response, authorization: True,
        )
        client.request_miner(
            18,
            "predict",
            {"lat": 1},
            request_headers={"X-OathCast-Application-Request-ID": "app-test"},
        )
        self.assertEqual(len(calls), 2)

    def test_client_rejects_a_challenge_above_the_explicit_cap_before_signing(self):
        signed = []

        def transport(method, url, headers):
            return HttpResult(402, {"Payment-Required": encoded_challenge("10001")}, b"")

        client = self.signed_client(
            dispatcher_url="https://dispatcher.test",
            transport=transport,
            signer=lambda authorization: signed.append(True) or "proof",
            expected_pay_to="0xabc",
            allowed_miner_ids={"18"},
            allowed_endpoints={"predict"},
        )
        with self.assertRaises(PaymentPolicyError):
            client.request_miner(18, "predict", {"lat": 1})
        self.assertEqual(signed, [])

    def test_current_live_shape_without_resource_is_allowed_but_mismatched_resource_is_not(self):
        payload = json.loads(base64.b64decode(encoded_challenge()).decode())
        payload["accepts"][0].pop("resource")
        challenge = PaymentChallenge(
            encoded_header="",
            payload=payload,
        )
        option = challenge.validate_for_request(
            "https://dispatcher.test/v1/18/predict?lat=1",
            expected_pay_to="0xabc",
            max_amount_micro_usdc=10000,
        )
        self.assertEqual(option["amount"], "10000")

        payload["accepts"][0]["resource"] = "https://dispatcher.test/other"
        with self.assertRaises(PaymentPolicyError):
            PaymentChallenge(encoded_header="", payload=payload).validate_for_request(
                "https://dispatcher.test/v1/18/predict?lat=1",
                expected_pay_to="0xabc",
                max_amount_micro_usdc=10000,
            )

    def test_client_rejects_insecure_transport_before_signing(self):
        client = self.signed_client(
            dispatcher_url="http://dispatcher.test",
            transport=lambda method, url, headers: HttpResult(
                402, {"Payment-Required": encoded_challenge()}, b""
            ),
            signer=lambda authorization: "proof",
            expected_pay_to="0xabc",
            allowed_miner_ids={"18"},
            allowed_endpoints={"predict"},
        )
        with self.assertRaises(PaymentPolicyError):
            client.request_miner(18, "predict", {"lat": 1})

    def test_client_marks_missing_settlement_as_unknown_and_blocks_duplicate(self):
        calls = []

        def transport(method, url, headers):
            calls.append(headers)
            if len(calls) == 1:
                return HttpResult(402, {"Payment-Required": encoded_challenge()}, b"")
            return HttpResult(200, {}, b"{\"ok\":true}")

        client = self.signed_client(
            dispatcher_url="https://dispatcher.test",
            transport=transport,
            signer=lambda authorization: "real-proof-from-signer",
            expected_pay_to="0xabc",
            allowed_miner_ids={"18"},
            allowed_endpoints={"predict"},
        )
        with self.assertRaises(PaymentOutcomeUnknown):
            client.request_miner(18, "predict", {"lat": 1})
        with self.assertRaises(DuplicatePaymentError):
            client.request_miner(18, "predict", {"lat": 1})
        self.assertEqual(len(calls), 2)

    def test_client_rejects_unapproved_target_before_network_call(self):
        calls = []
        client = self.signed_client(
            dispatcher_url="https://dispatcher.test",
            transport=lambda method, url, headers: calls.append(url),
            signer=lambda authorization: "proof",
            expected_pay_to="0xabc",
            allowed_miner_ids={"18"},
            allowed_endpoints={"predict"},
        )
        with self.assertRaises(PaymentPolicyError):
            client.request_miner(211, "forecast", {"lat": 1})
        self.assertEqual(calls, [])

    def test_signer_receives_only_the_validated_option(self):
        payload = json.loads(base64.b64decode(encoded_challenge()).decode())
        payload["accepts"].append(
            {
                "scheme": "exact",
                "network": BASE_SEPOLIA_NETWORK,
                "asset": BASE_SEPOLIA_USDC,
                "amount": "20000",
                "payTo": "0xabc",
            }
        )
        header = base64.b64encode(json.dumps(payload).encode()).decode()
        seen = []

        def transport(method, url, headers):
            if len(seen) == 0:
                seen.append((url, headers))
                return HttpResult(402, {"Payment-Required": header}, b"")
            self.assertEqual(headers["PAYMENT-SIGNATURE"], "bound-proof")
            return HttpResult(200, {"x-payment-settle-response": "settled"}, b"{}")

        def signer(authorization: ValidatedPaymentAuthorization):
            self.assertEqual(authorization.amount_micro_usdc, 10000)
            self.assertEqual(len(authorization.challenge.accepts), 1)
            self.assertEqual(authorization.challenge.accepts[0]["amount"], "10000")
            option = authorization.option
            option["amount"] = "999999"
            self.assertEqual(authorization.option["amount"], "10000")
            seen.append((authorization.authorization_sha256, authorization.challenge_sha256))
            return "bound-proof"

        client = self.signed_client(
            dispatcher_url="https://dispatcher.test",
            transport=transport,
            signer=signer,
            expected_pay_to="0xabc",
            allowed_miner_ids={"18"},
            allowed_endpoints={"predict"},
        )
        response = client.request_miner(18, "predict", {"lat": 1})
        self.assertEqual(response.body, {})
        self.assertEqual(len(seen), 2)

    def test_signing_requires_a_durable_journal(self):
        with self.assertRaises(PaymentPolicyError):
            TelegraphX402Client(
                dispatcher_url="https://dispatcher.test",
                signer=lambda authorization: "proof",
            )

    def test_preflight_does_not_reserve_a_payment_journal_entry(self):
        journal = SqlitePaymentJournal(":memory:")
        client = TelegraphX402Client(
            dispatcher_url="https://dispatcher.test",
            transport=lambda method, url, headers: HttpResult(
                402, {"Payment-Required": encoded_challenge()}, b""
            ),
            signer=lambda authorization: "must-not-run",
            expected_pay_to="0xabc",
            allowed_miner_ids={"18"},
            allowed_endpoints={"predict"},
            journal=journal,
        )
        result = client.preflight_miner(18, "predict", {"lat": 1})
        self.assertEqual(result.status, 402)
        self.assertIsNone(journal.get(result.request_url))

    def test_payment_journal_blocks_duplicate_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payments.sqlite3"
            calls = []

            def transport(method, url, headers):
                calls.append(headers)
                if len(calls) == 1:
                    return HttpResult(402, {"Payment-Required": encoded_challenge()}, b"")
                return HttpResult(200, {"x-payment-settle-response": "settled"}, b"{}")

            first = TelegraphX402Client(
                dispatcher_url="https://dispatcher.test",
                transport=transport,
                signer=lambda authorization: "proof",
                expected_pay_to="0xabc",
                allowed_miner_ids={"18"},
                allowed_endpoints={"predict"},
                settlement_verifier=lambda response, authorization: True,
                journal=SqlitePaymentJournal(path),
            )
            first.request_miner(18, "predict", {"lat": 1})
            second_calls = []
            second = TelegraphX402Client(
                dispatcher_url="https://dispatcher.test",
                transport=lambda method, url, headers: second_calls.append(url),
                signer=lambda authorization: "must-not-run",
                expected_pay_to="0xabc",
                allowed_miner_ids={"18"},
                allowed_endpoints={"predict"},
                journal=SqlitePaymentJournal(path),
            )
            with self.assertRaises(DuplicatePaymentError):
                second.request_miner(18, "predict", {"lat": 1})
            self.assertEqual(second_calls, [])


if __name__ == "__main__":
    unittest.main()
