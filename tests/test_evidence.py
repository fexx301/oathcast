from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from oathcast.application import ApplicationDecision, MinerReply
from oathcast.cases import CaseConflict, CaseStateError, SqliteCaseStore
from oathcast.forecast import ForecastQuestion
from oathcast.ground_truth import (
    FileObservationSource,
    GroundTruthResult,
    PrecipitationObservation,
    resolve_precipitation,
)


UTC = timezone.utc
FIXED_RESOLUTION = datetime(2026, 8, 17, 17, tzinfo=UTC)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.question = ForecastQuestion(
            event_id="evidence-1",
            location_name="Lagos",
            latitude=6.5244,
            longitude=3.3792,
            horizon_start=datetime(2026, 8, 17, 15, tzinfo=UTC),
            horizon_end=datetime(2026, 8, 17, 16, tzinfo=UTC),
            forecast_cutoff=datetime(2026, 8, 17, 12, tzinfo=UTC),
        )
        self.decision = ApplicationDecision(
            question=self.question,
            aggregate_probability=0.7,
            event_likely=True,
            recommended_action="plan_for_event",
            used_external_miner=True,
            external_influence=True,
            replies=(
                MinerReply(
                    miner_id="external-1",
                    slug="external-weather",
                    owned=False,
                    raw_response={"probability": 0.7, "content": "70%"},
                    probability=0.7,
                    content="70%",
                    latency_ms=12.5,
                    transport="fixture",
                    received_at=datetime(2026, 8, 17, 11, 58, tzinfo=UTC),
                ),
            ),
            decided_at=datetime(2026, 8, 17, 11, 59, tzinfo=UTC),
        )

    def observation(
        self,
        precipitation_mm: float,
        *,
        observed_at=FIXED_RESOLUTION,
        observation_id="obs-1",
    ):
        return PrecipitationObservation(
            event_id=self.question.event_id,
            latitude=self.question.latitude,
            longitude=self.question.longitude,
            window_start=self.question.horizon_start,
            window_end=self.question.horizon_end,
            precipitation_mm=precipitation_mm,
            source="independent-station-fixture",
            observation_id=observation_id,
            observed_at=observed_at,
        )

    def test_resolution_uses_strict_provider_native_threshold(self):
        exactly_threshold = resolve_precipitation(
            self.question,
            self.observation(0.1),
            resolved_at=FIXED_RESOLUTION,
        )
        above_threshold = resolve_precipitation(
            self.question,
            self.observation(0.101),
            resolved_at=FIXED_RESOLUTION,
        )
        self.assertEqual(exactly_threshold.outcome, 0)
        self.assertEqual(above_threshold.outcome, 1)
        self.assertEqual(above_threshold.status, "resolved")

    def test_missing_and_temporally_invalid_observations_are_explicit(self):
        missing = resolve_precipitation(
            self.question,
            None,
            resolved_at=FIXED_RESOLUTION,
        )
        self.assertEqual(missing.to_dict()["status"], "missing")
        self.assertEqual(missing.issue, "observation_missing")

        invalid = resolve_precipitation(
            self.question,
            self.observation(
                0.5,
                observed_at=datetime(2026, 8, 17, 15, 59, tzinfo=UTC),
            ),
            resolved_at=FIXED_RESOLUTION,
        )
        self.assertEqual(invalid.status, "invalid")
        self.assertEqual(invalid.issue, "observation_predates_event_end")
        self.assertIsNone(invalid.outcome)

    def test_file_observation_source_hashes_and_rejects_duplicates(self):
        observation = self.observation(0.4)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.json"
            path.write_text(
                json.dumps({"observations": [observation.to_dict()]}),
                encoding="utf-8",
            )
            source = FileObservationSource(path)
            self.assertEqual(source.observation_count, 1)
            self.assertEqual(len(source.source_sha256), 64)
            self.assertEqual(
                source.observe(self.question).observation_id,
                observation.observation_id,
            )
            self.assertEqual(
                source.manifest()["independence_status"],
                "must_be_verified_by_operator",
            )

            path.write_text(
                json.dumps([observation.to_dict(), observation.to_dict()]),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                FileObservationSource(path)

    def test_case_lifecycle_is_idempotent_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.sqlite3"
            store = SqliteCaseStore(path)
            created = store.create(
                self.question,
                created_at=datetime(2026, 8, 17, 11, 0, tzinfo=UTC),
            )
            self.assertEqual(created["event_id"], self.question.event_id)
            sealed = store.seal_decision(
                self.question.event_id,
                self.decision,
                sealed_at=self.decision.decided_at,
            )
            self.assertIsNotNone(sealed["decision"])
            self.assertEqual(len(sealed["miner_replies"]), 1)
            self.assertEqual(len(sealed["decisions"]), 1)
            self.assertEqual(len(sealed["decisions"][0]["reply_ids"]), 1)
            truth = resolve_precipitation(
                self.question,
                self.observation(0.4),
                resolved_at=FIXED_RESOLUTION,
            )
            resolved = store.resolve(
                self.question.event_id,
                truth,
                observation=self.observation(0.4),
            )
            self.assertEqual(resolved["ground_truth"]["outcome"], 1)

            restarted = SqliteCaseStore(path)
            again = restarted.create(self.question)
            self.assertEqual(again, resolved)
            self.assertEqual(restarted.get(self.question.event_id), resolved)

            revised_truth = resolve_precipitation(
                self.question,
                self.observation(0.0, observation_id="obs-2"),
                resolved_at=datetime(2026, 8, 18, 17, tzinfo=UTC),
            )
            revised = restarted.resolve(
                self.question.event_id,
                revised_truth,
                observation=self.observation(0.0, observation_id="obs-2"),
            )
            self.assertEqual(revised["ground_truth"]["outcome"], 1)
            self.assertEqual(len(revised["observations"]), 2)
            self.assertEqual(len(revised["resolutions"]), 2)

    def test_file_case_store_enforces_foreign_keys_on_fresh_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteCaseStore(Path(directory) / "cases.sqlite3")
            connection = store._connection()
            try:
                self.assertEqual(
                    connection.execute("PRAGMA foreign_keys").fetchone()[0],
                    1,
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO miner_replies (
                            reply_id, event_id, miner_id, slug, owned,
                            raw_response_json, raw_response_sha256,
                            parser_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "orphan-reply",
                            "missing-case",
                            "external-1",
                            "external-weather",
                            0,
                            "{}",
                            "0" * 64,
                            "probability_extractor_v1",
                        ),
                    )
            finally:
                connection.rollback()
                connection.close()

    def test_case_conflicts_and_ordering_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteCaseStore(Path(directory) / "cases.sqlite3")
            with self.assertRaises(CaseStateError):
                store.resolve(
                    self.question.event_id,
                    GroundTruthResult(
                        event_id=self.question.event_id,
                        status="missing",
                        outcome=None,
                        source=None,
                        observation_id=None,
                        precipitation_mm=None,
                        resolved_at=FIXED_RESOLUTION,
                        issue="observation_missing",
                    ),
                )
            store.create(self.question)
            changed = ForecastQuestion(
                event_id=self.question.event_id,
                location_name="Ibadan",
                latitude=self.question.latitude,
                longitude=self.question.longitude,
                horizon_start=self.question.horizon_start,
                horizon_end=self.question.horizon_end,
                forecast_cutoff=self.question.forecast_cutoff,
            )
            with self.assertRaises(CaseConflict):
                store.create(changed)


if __name__ == "__main__":
    unittest.main()
