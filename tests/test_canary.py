import unittest

from scripts.smoke_miner import receipt_capacity_check


def readyz(**capacity) -> dict:
    return {"ready": True, "receipt_store": dict(capacity)}


class ReceiptCapacityCheckTests(unittest.TestCase):
    """The canary must warn while there is still room to act."""

    def test_a_healthy_store_passes_and_reports_headroom(self):
        check = receipt_capacity_check(
            readyz(
                rows=10,
                max_rows=1000,
                used_bytes=1_000,
                max_bytes=1_000_000,
                accepting_new_receipts=True,
            ),
            min_headroom_percent=10.0,
        )
        self.assertTrue(check["ok"])
        self.assertTrue(check["reported"])
        self.assertAlmostEqual(check["rows_headroom_percent"], 99.0)
        self.assertAlmostEqual(check["bytes_headroom_percent"], 99.9)

    def test_low_headroom_fails_before_the_store_is_actually_full(self):
        # The point of the check: alert while there is still time to act,
        # not once every forecast is already returning 507.
        check = receipt_capacity_check(
            readyz(rows=995, max_rows=1000, accepting_new_receipts=True),
            min_headroom_percent=10.0,
        )
        self.assertFalse(check["ok"])
        self.assertIn("headroom", check["error"])

    def test_a_full_store_fails_with_an_explicit_error(self):
        check = receipt_capacity_check(
            readyz(rows=1000, max_rows=1000, accepting_new_receipts=False),
            min_headroom_percent=10.0,
        )
        self.assertFalse(check["ok"])
        self.assertIn("507", check["error"])

    def test_the_tightest_configured_cap_decides(self):
        # Plenty of rows left, but the byte cap is nearly exhausted.
        check = receipt_capacity_check(
            readyz(
                rows=1,
                max_rows=1000,
                used_bytes=999_000,
                max_bytes=1_000_000,
                accepting_new_receipts=True,
            ),
            min_headroom_percent=10.0,
        )
        self.assertFalse(check["ok"])

    def test_an_uncapped_store_passes(self):
        check = receipt_capacity_check(
            readyz(rows=10_000, max_rows=None, used_bytes=5, max_bytes=None,
                   accepting_new_receipts=True),
            min_headroom_percent=10.0,
        )
        self.assertTrue(check["ok"])
        self.assertTrue(check["reported"])
        self.assertNotIn("rows_headroom_percent", check)

    def test_a_release_that_does_not_report_capacity_is_visible_not_silent(self):
        # A live host can legitimately lag the repo between merge and redeploy.
        # That must not fail the canary, but it must also not read as healthy.
        check = receipt_capacity_check({"ready": True}, min_headroom_percent=10.0)
        self.assertTrue(check["ok"])
        self.assertFalse(check["reported"])
        self.assertNotIn("rows_headroom_percent", check)

    def test_a_non_dict_readyz_payload_does_not_raise(self):
        check = receipt_capacity_check("service unavailable", min_headroom_percent=10.0)
        self.assertTrue(check["ok"])
        self.assertFalse(check["reported"])


if __name__ == "__main__":
    unittest.main()
