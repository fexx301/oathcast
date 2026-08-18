import io
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from oathcast.service import FORECAST_PATHS
from scripts.smoke_miner import (
    CANONICAL_FORECAST_PATH,
    REGISTERED_FORECAST_PATH,
    _read_response_body,
    format_timestamp,
    release_identity_from_evidence,
    release_identity_checks,
    receipt_capacity_check,
    receipt_write_check,
    rolling_horizon,
    skipped_authenticated_checks,
    temperature_smoke_event_id,
    valid_forecast_response,
    valid_temperature_window_response,
)


ROOT = Path(__file__).resolve().parents[1]


def readyz(**capacity) -> dict:
    report = {
        "rows": 0,
        "max_rows": None,
        "used_bytes": 0,
        "max_bytes": None,
        "accepting_new_receipts": True,
    }
    report.update(capacity)
    return {"ready": True, "receipt_store": report}


class ResponseBodyLimitTests(unittest.TestCase):
    class Response(io.BytesIO):
        def __init__(self, body: bytes, headers: dict[str, str] | None = None):
            super().__init__(body)
            self.headers = headers or {}

    def test_exactly_the_response_cap_is_accepted(self):
        response = self.Response(b"abcd")
        self.assertEqual(
            _read_response_body(response, max_body_bytes=4),
            b"abcd",
        )

    def test_a_body_above_the_response_cap_is_rejected(self):
        response = self.Response(b"abcde")
        with self.assertRaisesRegex(ValueError, "4 byte cap"):
            _read_response_body(response, max_body_bytes=4)

    def test_declared_over_cap_content_length_is_rejected_before_reading(self):
        response = self.Response(b"a", {"Content-Length": "5"})
        with self.assertRaisesRegex(ValueError, "4 byte cap"):
            _read_response_body(response, max_body_bytes=4)
        self.assertEqual(response.tell(), 0)


class SmokeResultRecordTests(unittest.TestCase):
    IDENTITY = {
        "release_id": "release-1",
        "source_sha256": "source-1",
        "image_digest": "sha256:image-1",
    }

    def test_release_identity_checks_record_successes_explicitly(self):
        checks = release_identity_checks(self.IDENTITY, self.IDENTITY)
        self.assertEqual([check["name"] for check in checks], list(self.IDENTITY))
        self.assertTrue(all(check["ok"] for check in checks))
        self.assertTrue(all(check["actual"] == check["expected"] for check in checks))

    def test_release_identity_checks_record_mismatches_explicitly(self):
        actual = dict(self.IDENTITY, image_digest="sha256:different")
        checks = release_identity_checks(actual, self.IDENTITY)
        image_check = next(
            check for check in checks if check["name"] == "image_digest"
        )
        self.assertFalse(image_check["ok"])
        self.assertEqual(image_check["expected"], "sha256:image-1")
        self.assertEqual(image_check["actual"], "sha256:different")

    def test_skipped_authenticated_checks_are_explicit_and_partial(self):
        checks = skipped_authenticated_checks(require_temperature_window=True)
        self.assertEqual(
            [check["name"] for check in checks],
            [
                "authenticated_forecast",
                "canonical_path_parity",
                "authenticated_temperature_window",
                "temperature_path_parity",
            ],
        )
        self.assertTrue(all(check["ok"] for check in checks))
        self.assertTrue(all(check["skipped"] for check in checks))
        self.assertTrue(all(check["reason"] == "--skip-authenticated" for check in checks))


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

    def test_missing_capacity_fails_when_the_production_canary_requires_it(self):
        check = receipt_capacity_check(
            {"ready": True},
            min_headroom_percent=10.0,
            required=True,
        )
        self.assertFalse(check["ok"])
        self.assertTrue(check["required"])
        self.assertFalse(check["reported"])
        self.assertIn("does not report", check["error"])

    def test_an_incomplete_capacity_report_fails(self):
        check = receipt_capacity_check(
            {
                "ready": True,
                "receipt_store": {"accepting_new_receipts": True},
            },
            min_headroom_percent=10.0,
            required=True,
        )
        self.assertFalse(check["ok"])
        self.assertTrue(check["reported"])
        self.assertIn("missing", check["error"])

    def test_invalid_capacity_numbers_fail_without_raising(self):
        for invalid in (True, -1, 0.5, float("nan"), 10**10000):
            with self.subTest(invalid=invalid):
                check = receipt_capacity_check(
                    readyz(rows=invalid),
                    min_headroom_percent=10.0,
                    required=True,
                )
                self.assertFalse(check["ok"])
                self.assertIn("rows", check["error"])

    def test_a_non_dict_readyz_payload_does_not_raise(self):
        check = receipt_capacity_check("service unavailable", min_headroom_percent=10.0)
        self.assertTrue(check["ok"])
        self.assertFalse(check["reported"])


class ReleaseEvidenceIdentityTests(unittest.TestCase):
    EVIDENCE = (
        ROOT
        / "artifacts"
        / "release-evidence"
        / "oathcast-2026-08-17-temperature-v8-runtime-evidence.json"
    )

    def test_v8_identity_is_loaded_from_consistent_release_evidence(self):
        identity = release_identity_from_evidence(self.EVIDENCE)
        self.assertEqual(identity["release_id"], "2026-08-17-temperature-v8")
        self.assertEqual(
            identity["source_sha256"],
            "edeeaacf470b2207f6bbd8439e0720eff0459d9ca5fe214bc3a09d48ae0c639c",
        )
        self.assertEqual(
            identity["image_digest"],
            "sha256:ae1fff9db3317cd0f6a9d23772df62d93195bd814359e9a3c8d9b21aa0850672",
        )

    def test_internal_identity_drift_is_rejected(self):
        evidence = json.loads(self.EVIDENCE.read_text(encoding="utf-8"))
        evidence["image"]["labels"]["org.opencontainers.image.version"] = "drifted"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / self.EVIDENCE.name
            path.write_text(json.dumps(evidence), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "image.version"):
                release_identity_from_evidence(path)

    def test_linked_evidence_cannot_escape_the_repository(self):
        evidence = json.loads(self.EVIDENCE.read_text(encoding="utf-8"))
        evidence["source_verification"]["manifest_path"] = "/etc/passwd"
        evidence["evidence"]["manifest"] = "/etc/passwd"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / self.EVIDENCE.name
            path.write_text(json.dumps(evidence), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "escapes the repository"):
                release_identity_from_evidence(path)


class CanaryWorkflowIntegrityTests(unittest.TestCase):
    @staticmethod
    def _git(repository: Path, *args: str, input_text: str | None = None):
        return subprocess.run(
            ["git", *args],
            cwd=repository,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )

    def _new_repository(self, directory: str, content: str) -> tuple[Path, str]:
        repository = Path(directory)
        self.assertEqual(self._git(repository, "init", "-q").returncode, 0)
        self.assertEqual(
            self._git(repository, "config", "user.email", "canary@example.invalid").returncode,
            0,
        )
        self.assertEqual(
            self._git(repository, "config", "user.name", "Canary Test").returncode,
            0,
        )
        (repository / "sample.txt").write_text(content, encoding="utf-8")
        self.assertEqual(self._git(repository, "add", "sample.txt").returncode, 0)
        self.assertEqual(
            self._git(repository, "commit", "-q", "-m", "fixture").returncode,
            0,
        )
        head = self._git(repository, "rev-parse", "HEAD")
        self.assertEqual(head.returncode, 0, head.stderr)
        return repository, head.stdout.strip()

    def test_production_canary_uses_evidence_identity_and_requires_capacity(self):
        workflow = (ROOT / ".github" / "workflows" / "oathcast-canary.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "--release-evidence artifacts/release-evidence/"
            "oathcast-2026-08-17-temperature-v8-runtime-evidence.json",
            workflow,
        )
        self.assertIn("--require-receipt-capacity", workflow)
        self.assertNotIn("--expected-release-id", workflow)
        self.assertNotIn("--expected-source-sha256", workflow)
        self.assertNotIn("--expected-image-digest", workflow)

    def test_ci_checks_committed_whitespace_across_event_ranges(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("github.event.pull_request.base.sha", workflow)
        self.assertIn("github.event.pull_request.head.sha", workflow)
        self.assertIn("github.event.before", workflow)
        self.assertIn("git hash-object -t tree /dev/null", workflow)
        self.assertNotIn("run: git diff --check\n", workflow)

    def test_commit_range_check_rejects_committed_trailing_whitespace(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, base = self._new_repository(directory, "clean\n")
            (repository / "sample.txt").write_text("trailing space \n", encoding="utf-8")
            self.assertEqual(self._git(repository, "add", "sample.txt").returncode, 0)
            self.assertEqual(
                self._git(repository, "commit", "-q", "-m", "bad whitespace").returncode,
                0,
            )
            head = self._git(repository, "rev-parse", "HEAD").stdout.strip()
            check = self._git(repository, "diff", "--check", f"{base}...{head}")
            self.assertNotEqual(check.returncode, 0)
            self.assertIn("trailing whitespace", check.stdout)

    def test_empty_tree_check_rejects_whitespace_in_an_initial_push(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, head = self._new_repository(directory, "trailing space \n")
            empty_tree = self._git(
                repository,
                "hash-object",
                "-t",
                "tree",
                "--stdin",
                input_text="",
            )
            self.assertEqual(empty_tree.returncode, 0, empty_tree.stderr)
            check = self._git(
                repository,
                "diff",
                "--check",
                empty_tree.stdout.strip(),
                head,
            )
            self.assertNotEqual(check.returncode, 0)
            self.assertIn("trailing whitespace", check.stdout)


class ReceiptWriteCheckTests(unittest.TestCase):
    def test_verified_transactional_probe_passes(self):
        check = receipt_write_check(
            {
                "ready": True,
                "receipt_store_write": {
                    "ready": True,
                    "probe": "sqlite_transactional_write",
                    "rolled_back": True,
                    "cached": False,
                },
            }
        )
        self.assertTrue(check["ok"])
        self.assertTrue(check["reported"])

    def test_missing_probe_is_visible_but_legacy_canary_compatible(self):
        check = receipt_write_check({"ready": True})
        self.assertTrue(check["ok"])
        self.assertFalse(check["required"])
        self.assertFalse(check["reported"])

    def test_missing_probe_fails_the_v6_release_smoke(self):
        check = receipt_write_check({"ready": True}, required=True)
        self.assertFalse(check["ok"])
        self.assertFalse(check["reported"])
        self.assertIn("does not report", check["error"])

    def test_ready_without_verified_rollback_fails(self):
        check = receipt_write_check(
            {
                "receipt_store_write": {
                    "ready": True,
                    "probe": "sqlite_transactional_write",
                    "rolled_back": False,
                }
            },
            required=True,
        )
        self.assertFalse(check["ok"])
        self.assertTrue(check["reported"])

    def test_reported_failed_probe_fails_even_in_legacy_canary_mode(self):
        check = receipt_write_check(
            {
                "receipt_store_write": {
                    "ready": False,
                    "probe": "sqlite_transactional_write",
                    "rolled_back": True,
                    "error": "write_unavailable",
                }
            }
        )
        self.assertFalse(check["ok"])
        self.assertFalse(check["required"])
        self.assertTrue(check["reported"])


class ForecastResponseCheckTests(unittest.TestCase):
    def test_nonempty_content_and_probability_pass(self):
        self.assertTrue(
            valid_forecast_response(
                {"content": "Rain is unlikely. Probability: 20%.", "probability": 0.2}
            )
        )

    def test_empty_or_invalid_answers_fail(self):
        for response in (
            {"content": "", "probability": 0.2},
            {"content": "   ", "probability": 0.2},
            {"content": "Rain is unlikely."},
            {"content": "Rain is unlikely.", "probability": True},
            {"content": "Rain is unlikely.", "probability": float("nan")},
            {"content": "Rain is unlikely.", "probability": 10**10000},
            {"content": "Rain is unlikely.", "probability": 1.1},
        ):
            with self.subTest(response=response):
                self.assertFalse(valid_forecast_response(response))


class TemperatureWindowResponseCheckTests(unittest.TestCase):
    def _response(self, *, hours=24):
        reference = "2026-08-17T11:00:00Z"
        times = [
            (datetime(2026, 8, 17, 12, tzinfo=timezone.utc) + timedelta(hours=index)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            for index in range(hours)
        ]
        return {
            "content": "Hourly 2 metre temperatures.",
            "reference_time": reference,
            "hourly": {"time": times, "2t": [298.15] * hours},
            "hourly_units": {"time": "iso8601", "2t": "K"},
        }

    def test_valid_twenty_four_hour_temperature_response_passes(self):
        self.assertTrue(valid_temperature_window_response(self._response()))

    def test_temperature_response_rejects_wrong_shape_units_and_spacing(self):
        response = self._response()
        for mutate in (
            lambda value: value["hourly"].pop("2t"),
            lambda value: value["hourly_units"].update({"2t": "C"}),
            lambda value: value["hourly"]["time"].__setitem__(1, value["hourly"]["time"][0]),
            lambda value: value["hourly"]["2t"].__setitem__(0, 0),
        ):
            candidate = json.loads(json.dumps(response))
            mutate(candidate)
            with self.subTest(candidate=candidate):
                self.assertFalse(valid_temperature_window_response(candidate))

    def test_temperature_response_requires_exact_count(self):
        self.assertFalse(valid_temperature_window_response(self._response(hours=23)))

    def test_temperature_response_can_be_pinned_to_the_request_hour(self):
        response = self._response()
        expected = datetime(2026, 8, 17, 11, tzinfo=timezone.utc)
        self.assertTrue(
            valid_temperature_window_response(
                response,
                expected_reference_times={expected},
            )
        )
        self.assertFalse(
            valid_temperature_window_response(
                response,
                expected_reference_times={expected - timedelta(hours=1)},
            )
        )

    def test_temperature_smoke_id_is_stable_only_within_one_utc_hour(self):
        first = datetime(2026, 8, 17, 11, 1, tzinfo=timezone.utc)
        same_hour = first + timedelta(minutes=58)
        next_hour = first + timedelta(hours=1)

        self.assertEqual(
            temperature_smoke_event_id(first),
            "smoke-temperature-20260817T11z",
        )
        self.assertEqual(
            temperature_smoke_event_id(first),
            temperature_smoke_event_id(same_hour),
        )
        self.assertNotEqual(
            temperature_smoke_event_id(first),
            temperature_smoke_event_id(next_hour),
        )


class RollingHorizonTests(unittest.TestCase):
    """The recurring canary must always pick a requestable horizon.

    A fixed date is squeezed between two independent failure modes: past the
    provider's rolling 7-day window (Open-Meteo -> provider_unavailable -> 502)
    and past its own forecast_cutoff (service.py:435 rejects when now >= cutoff).
    The rolling horizon must stay between both bounds at every clock time, and
    must stay stable within a UTC day so the 96 daily runs replay one receipt
    instead of writing 96.
    """

    def test_horizon_lands_at_noon_utc_tomorrow(self):
        now = datetime(2026, 8, 10, 18, 30, tzinfo=timezone.utc)
        start, end, cutoff = rolling_horizon(now)
        self.assertEqual(format_timestamp(start), "2026-08-11T12:00:00Z")
        self.assertEqual(format_timestamp(end), "2026-08-11T13:00:00Z")
        self.assertEqual(format_timestamp(cutoff), "2026-08-11T11:00:00Z")

    def test_smoke_timestamp_formatting_rejects_naive_datetimes(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            format_timestamp(datetime(2026, 8, 11, 12, 0))

    def test_cutoff_stays_in_the_future_even_at_235959(self):
        # service.py:435 rejects a request issued at or after forecast_cutoff,
        # so the tightest time is the last instant of the UTC day.
        now = datetime(2026, 8, 10, 23, 59, 59, tzinfo=timezone.utc)
        _, _, cutoff = rolling_horizon(now)
        self.assertGreater(cutoff, now)
        self.assertLessEqual((cutoff - now).total_seconds(), 12 * 3600)

    def test_lead_stays_inside_the_provider_window_at_midnight(self):
        # The loosest time (just after UTC midnight) gives the largest lead.
        # Open-Meteo publishes a rolling 7 days; select_exact_point refuses to
        # substitute a neighbouring hour, so the horizon must stay inside it.
        now = datetime(2026, 8, 11, 0, 1, tzinfo=timezone.utc)
        start, end, cutoff = rolling_horizon(now)
        lead_hours = (start - now).total_seconds() / 3600
        self.assertLess(lead_hours, 7 * 24)
        self.assertGreater(lead_hours, 12)
        self.assertEqual(end - start, timedelta(hours=1))
        self.assertEqual(start - cutoff, timedelta(hours=1))

    def test_horizon_rolls_at_utc_midnight(self):
        late = rolling_horizon(datetime(2026, 8, 10, 23, 59, tzinfo=timezone.utc))
        early = rolling_horizon(datetime(2026, 8, 11, 0, 1, tzinfo=timezone.utc))
        self.assertEqual(format_timestamp(late[0]), "2026-08-11T12:00:00Z")
        self.assertEqual(format_timestamp(early[0]), "2026-08-12T12:00:00Z")

    def test_stable_within_a_utc_day_replays_one_receipt(self):
        # The receipt hash derives from the canonical question (event_id is
        # excluded, service.py:603), so an identical horizon within a day must
        # not multiply rows in the receipt store.
        first = rolling_horizon(datetime(2026, 8, 10, 0, 5, tzinfo=timezone.utc))
        last = rolling_horizon(datetime(2026, 8, 10, 23, 55, tzinfo=timezone.utc))
        self.assertEqual(first, last)


class RegisteredRouteConfigTests(unittest.TestCase):
    def test_smoke_paths_match_the_service_aliases(self):
        self.assertEqual(REGISTERED_FORECAST_PATH, "/predict")
        self.assertEqual(CANONICAL_FORECAST_PATH, "/v1/forecast/point")
        self.assertIn(REGISTERED_FORECAST_PATH, FORECAST_PATHS)
        self.assertIn(CANONICAL_FORECAST_PATH, FORECAST_PATHS)
        self.assertNotIn("/v1/forecast/window", FORECAST_PATHS)

    def test_caddy_routes_the_registered_path_to_the_miner(self):
        matcher = next(
            line.strip()
            for line in (ROOT / "Caddyfile").read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("@miner path ")
        )
        self.assertIn(" /predict ", f" {matcher} ")

    def test_caddy_caps_bodies_and_sets_https_security_headers(self):
        caddyfile = (ROOT / "Caddyfile").read_text(encoding="utf-8")
        self.assertEqual(caddyfile.count("max_size 64KB"), 2)
        self.assertIn("Strict-Transport-Security", caddyfile)
        self.assertIn("X-Content-Type-Options", caddyfile)
        self.assertIn("Referrer-Policy", caddyfile)


if __name__ == "__main__":
    unittest.main()
