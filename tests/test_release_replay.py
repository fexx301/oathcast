import unittest

from scripts.compare_release_replay import compare_release_replay
from scripts.smoke_miner import json_sha256


def report(release_id: str, *, event="event-1", receipt="a" * 64, response="b" * 64):
    return {
        "release_id": release_id,
        "checks": [
            {
                "name": "authenticated_forecast",
                "ok": True,
                "event_id": event,
                "receipt_sha256": receipt,
                "public_response_sha256": response,
            }
        ],
    }


class JsonFingerprintTests(unittest.TestCase):
    def test_canonical_hash_ignores_object_key_order(self):
        self.assertEqual(json_sha256({"a": 1, "b": 2}), json_sha256({"b": 2, "a": 1}))

    def test_canonical_hash_changes_with_public_response(self):
        self.assertNotEqual(json_sha256({"probability": 0.2}), json_sha256({"probability": 0.3}))


class ReleaseReplayComparisonTests(unittest.TestCase):
    def test_identical_receipt_and_response_across_distinct_releases_pass(self):
        result = compare_release_replay(report("v5"), report("v6"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["fingerprints"]["event_id"], "event-1")

    def test_changed_response_fails(self):
        result = compare_release_replay(report("v5"), report("v6", response="c" * 64))
        self.assertFalse(result["ok"])
        self.assertIn("public_response_sha256 changed across the release", result["errors"])

    def test_same_release_does_not_claim_cross_version_proof(self):
        result = compare_release_replay(report("v6"), report("v6"))
        self.assertFalse(result["ok"])
        self.assertIn("before and after release IDs are identical", result["errors"])

    def test_missing_or_failed_forecast_is_rejected(self):
        before = {"release_id": "v5", "checks": []}
        after = report("v6")
        result = compare_release_replay(before, after)
        self.assertFalse(result["ok"])
        self.assertIn("before report has no authenticated_forecast check", result["errors"])


if __name__ == "__main__":
    unittest.main()
