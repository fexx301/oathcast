from contextlib import redirect_stdout
import io
import os
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oathcast.backtest import load_chronological_cases
from oathcast.ground_truth import FileObservationSource

from scripts.collect_provider_pairs import (
    CollectionError,
    _attempt,
    _scrub,
    build_question,
    collect_once,
    load_locations,
    main as collect_main,
    merge_cases,
    resolve_cases,
    write_dataset,
)

LOCATION = {
    "slug": "lagos",
    "location_name": "Lagos",
    "latitude": 6.5244,
    "longitude": 3.3792,
    "climatology_probability": 0.2305,
    "climatology_source": "ERA5 August hours 2015-2024",
}
ISSUED = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


class StubAdapter:
    """Minimal stand-in so these tests never touch a provider network."""

    adapter_version = "stub_v1"

    def __init__(self, probability=0.4, *, error=None, url="https://stub/?key={key}"):
        self.probability = probability
        self.error = error
        self.url_template = url

    def build_url(self, question, api_key=None):
        return self.url_template.format(key=api_key)

    def parse(self, payload, question, *, issued_at, retrieved_at):
        if self.error is not None:
            raise self.error

        class Forecast:
            probability = self.probability
            adapter_version = "stub_v1"
            event_equivalence = "unverified"

        return Forecast()


class LocationLoadingTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.path = Path(self._directory.name) / "locations.json"

    def _write(self, payload):
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        return self.path

    def test_a_sourced_location_loads(self):
        locations = load_locations(self._write([LOCATION]))
        self.assertEqual(len(locations), 1)
        self.assertEqual(locations[0]["climatology_probability"], 0.2305)

    def test_an_unsourced_climatology_is_refused(self):
        # Brier skill is measured against this baseline. A placeholder would
        # score every provider against a number nobody derived.
        unsourced = {**LOCATION, "climatology_source": "UNSET"}
        with self.assertRaises(CollectionError) as caught:
            load_locations(self._write([unsourced]))
        self.assertIn("no sourced climatology", str(caught.exception))

    def test_an_out_of_range_climatology_is_refused(self):
        with self.assertRaises(CollectionError):
            load_locations(self._write([{**LOCATION, "climatology_probability": 1.4}]))

    def test_duplicate_slugs_are_refused(self):
        with self.assertRaises(CollectionError) as caught:
            load_locations(self._write([LOCATION, LOCATION]))
        self.assertIn("duplicate", str(caught.exception))

    def test_a_missing_field_names_itself(self):
        partial = {key: value for key, value in LOCATION.items() if key != "latitude"}
        with self.assertRaises(CollectionError) as caught:
            load_locations(self._write([partial]))
        self.assertIn("latitude", str(caught.exception))

    def test_an_empty_list_is_refused(self):
        with self.assertRaises(CollectionError):
            load_locations(self._write([]))


class SecretScrubbingTests(unittest.TestCase):
    def test_a_url_bearing_error_is_scrubbed(self):
        # urllib copies the request URL into connection-failure messages, and
        # the key is a query parameter, so an unscrubbed error writes the key
        # into a scheduled job's log. The failure is raised from fetch_json so
        # the message carries the built URL, which is the real leak path.
        import scripts.collect_provider_pairs as module

        key = "abcdef0123456789abcdef0123456789"

        def leaking_fetch(url, *args, **kwargs):
            raise OSError(f"connection failed for {url}")

        original = module.fetch_json
        module.fetch_json = leaking_fetch
        self.addCleanup(lambda: setattr(module, "fetch_json", original))

        result = _attempt(
            StubAdapter(url="https://stub/?key={key}"),
            build_question(LOCATION, ISSUED, 3),
            ISSUED,
            key,
        )
        self.assertEqual(result["status"], "missing")
        self.assertNotIn(key, result["error"])
        self.assertIn("<redacted>", result["error"])

    def test_scrub_replaces_every_occurrence(self):
        key = "s3cr3t-key-value"
        message = f"failed key={key} retry key={key}"
        scrubbed = _scrub(message, key)
        self.assertNotIn(key, scrubbed)
        self.assertEqual(scrubbed.count("<redacted>"), 2)

    def test_scrub_tolerates_no_secret(self):
        self.assertEqual(_scrub("plain message", None), "plain message")


class MergeTests(unittest.TestCase):
    def _case(self, case_id, issued_at, *, lead_hours=3):
        start = issued_at + timedelta(hours=lead_hours)
        return {
            "case_id": case_id,
            "issued_at": issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "forecast_cutoff": issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "horizon_start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "horizon_end": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "outcome": None,
            "resolved_at": None,
            "climatology_probability": 0.2305,
            "forecasts": {"open_meteo": {"probability": 0.4, "status": "valid"}},
        }

    def test_a_repeated_run_adds_nothing(self):
        first = [self._case("lagos-a", ISSUED)]
        merged, added = merge_cases(first, [self._case("lagos-a", ISSUED)])
        self.assertEqual(added, [])
        self.assertEqual(len(merged), 1)

    def test_new_cases_stay_chronologically_ordered(self):
        existing = [self._case("lagos-a", ISSUED)]
        later = self._case("lagos-b", ISSUED + timedelta(hours=1))
        merged, added = merge_cases(existing, [later])
        self.assertEqual(added, ["lagos-b"])
        self.assertEqual([case["case_id"] for case in merged], ["lagos-a", "lagos-b"])

    def test_a_changed_lead_time_is_refused(self):
        # Comparing providers at different lead times measures lead time.
        existing = [self._case("lagos-a", ISSUED, lead_hours=3)]
        changed = self._case("lagos-b", ISSUED + timedelta(hours=1), lead_hours=6)
        with self.assertRaises(CollectionError) as caught:
            merge_cases(existing, [changed])
        self.assertIn("lead time changed", str(caught.exception))

    def test_a_changed_lead_time_is_allowed_explicitly(self):
        existing = [self._case("lagos-a", ISSUED, lead_hours=3)]
        changed = self._case("lagos-b", ISSUED + timedelta(hours=1), lead_hours=6)
        merged, added = merge_cases(existing, [changed], allow_lead_change=True)
        self.assertEqual(added, ["lagos-b"])


class CollectAndWriteTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.dataset = Path(self._directory.name) / "paired.json"

    def test_a_failed_provider_is_recorded_not_dropped(self):
        # Dropping the case would bias the comparison toward whichever
        # provider happens to be more available.
        import scripts.collect_provider_pairs as module

        original = module.fetch_json
        module.fetch_json = lambda url, *args, **kwargs: {}
        self.addCleanup(lambda: setattr(module, "fetch_json", original))

        cases = collect_once(
            [LOCATION], issued_at=ISSUED, lead_hours=3, weatherapi_key=None
        )
        self.assertEqual(len(cases), 1)
        statuses = {
            name: forecast["status"] for name, forecast in cases[0]["forecasts"].items()
        }
        self.assertEqual(set(statuses), {"open_meteo", "weatherapi"})
        # No key was supplied, so weatherapi cannot produce a forecast, but the
        # attempt is still part of the record.
        self.assertEqual(statuses["weatherapi"], "missing")

    def test_a_written_dataset_loads_through_the_backtest_loader(self):
        case = MergeTests()._case("lagos-a", ISSUED)
        write_dataset(self.dataset, [case])
        cases, digest = load_chronological_cases(str(self.dataset))
        self.assertEqual(len(cases), 1)
        self.assertEqual(len(digest), 64)

    def test_an_invalid_dataset_is_never_installed(self):
        # A scheduled job that writes an unloadable file is worse than one
        # that writes nothing, because the failure surfaces at analysis time.
        broken = MergeTests()._case("lagos-a", ISSUED)
        broken["horizon_end"] = broken["horizon_start"]
        with self.assertRaises(CollectionError):
            write_dataset(self.dataset, [broken])
        self.assertFalse(self.dataset.exists())
        self.assertFalse(self.dataset.with_name(self.dataset.name + ".tmp").exists())

    def test_a_write_replaces_the_previous_dataset_atomically(self):
        first = MergeTests()._case("lagos-a", ISSUED)
        write_dataset(self.dataset, [first])
        second = MergeTests()._case("lagos-b", ISSUED + timedelta(hours=1))
        write_dataset(self.dataset, [first, second])
        loaded, _ = load_chronological_cases(str(self.dataset))
        self.assertEqual(len(loaded), 2)

    def test_dataset_temp_file_is_unique_fsynced_and_cleaned_up(self):
        case = MergeTests()._case("lagos-a", ISSUED)
        with patch(
            "oathcast.artifacts.os.fsync",
            wraps=os.fsync,
        ) as fsync:
            write_dataset(self.dataset, [case])

        self.assertGreaterEqual(fsync.call_count, 1)
        self.assertEqual(
            list(self.dataset.parent.glob(f".{self.dataset.name}.*.tmp")),
            [],
        )


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)

    def _case(self, case_id="lagos-a", issued_at=ISSUED):
        case = MergeTests()._case(case_id, issued_at)
        case["location"] = {
            "slug": "lagos",
            "location_name": "Lagos",
            "latitude": 6.5244,
            "longitude": 3.3792,
            "climatology_source": "ERA5",
        }
        return case

    def _observations(self, case, precipitation_mm):
        payload = [
            {
                "event_id": case["case_id"],
                "latitude": 6.5244,
                "longitude": 3.3792,
                "window_start": case["horizon_start"],
                "window_end": case["horizon_end"],
                "precipitation_mm": precipitation_mm,
                "source": "test-export",
                "observation_id": f"obs-{case['case_id']}",
                "observed_at": case["horizon_end"],
            }
        ]
        path = Path(self._directory.name) / f"obs-{case['case_id']}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return FileObservationSource(path)

    def test_a_closed_window_resolves_above_the_threshold(self):
        case = self._case()
        now = datetime(2026, 8, 11, 0, 0, tzinfo=timezone.utc)
        updated, counts = resolve_cases([case], self._observations(case, 0.8), now=now)
        self.assertEqual(counts["resolved"], 1)
        self.assertEqual(updated[0]["outcome"], 1)
        self.assertEqual(updated[0]["observation"]["precipitation_mm"], 0.8)

    def test_a_closed_window_resolves_below_the_threshold(self):
        case = self._case()
        now = datetime(2026, 8, 11, 0, 0, tzinfo=timezone.utc)
        updated, counts = resolve_cases([case], self._observations(case, 0.05), now=now)
        self.assertEqual(counts["resolved"], 1)
        self.assertEqual(updated[0]["outcome"], 0)

    def test_an_open_window_is_left_alone(self):
        # Resolving before the window closes would be leakage.
        case = self._case()
        now = datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)
        updated, counts = resolve_cases([case], self._observations(case, 0.8), now=now)
        self.assertEqual(counts["window_open"], 1)
        self.assertIsNone(updated[0]["outcome"])

    def test_a_missing_observation_records_the_issue(self):
        case = self._case()
        other = self._case(case_id="lagos-other")
        now = datetime(2026, 8, 11, 0, 0, tzinfo=timezone.utc)
        updated, counts = resolve_cases([case], self._observations(other, 0.8), now=now)
        self.assertEqual(counts["unresolved"], 1)
        self.assertIsNone(updated[0]["outcome"])
        self.assertEqual(updated[0]["resolution_issue"], "observation_missing")

    def test_an_already_resolved_case_is_not_rewritten(self):
        case = self._case()
        case["outcome"] = 1
        case["resolved_at"] = "2026-08-10T17:00:00Z"
        now = datetime(2026, 8, 11, 0, 0, tzinfo=timezone.utc)
        updated, counts = resolve_cases([case], self._observations(case, 0.05), now=now)
        self.assertEqual(counts["already_resolved"], 1)
        self.assertEqual(updated[0]["outcome"], 1)
        self.assertEqual(updated[0]["resolved_at"], "2026-08-10T17:00:00Z")

    def test_resolution_is_idempotent(self):
        case = self._case()
        now = datetime(2026, 8, 11, 0, 0, tzinfo=timezone.utc)
        source = self._observations(case, 0.8)
        once, _ = resolve_cases([case], source, now=now)
        twice, counts = resolve_cases(once, source, now=now)
        self.assertEqual(counts["already_resolved"], 1)
        self.assertEqual(once[0]["outcome"], twice[0]["outcome"])

    def test_resolve_mode_reports_when_it_is_a_dry_run(self):
        case = self._case()
        dataset = Path(self._directory.name) / "paired.json"
        write_dataset(dataset, [case])
        self._observations(case, 0.8)
        observations = Path(self._directory.name) / f"obs-{case['case_id']}.json"
        output = io.StringIO()

        with redirect_stdout(output):
            status = collect_main(
                [
                    "--mode",
                    "resolve",
                    "--dataset",
                    str(dataset),
                    "--observations",
                    str(observations),
                    "--dry-run",
                ]
            )

        self.assertEqual(status, 0)
        self.assertTrue(json.loads(output.getvalue())["dry_run"])
        cases, _ = load_chronological_cases(str(dataset))
        self.assertIsNone(cases[0].outcome)


if __name__ == "__main__":
    unittest.main()
