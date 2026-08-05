from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from oathcast.application import CrossMinerRouter
from oathcast.cases import CaseStateError, SqliteCaseStore
from oathcast.discovery import MinerCapability
from oathcast.forecast import ForecastQuestion
from oathcast.ground_truth import MappingObservationSource, PrecipitationObservation
from oathcast.workflow import ApplicationWorkflow


UTC = timezone.utc


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.question = ForecastQuestion(
            event_id="workflow-1",
            location_name="Lagos",
            latitude=6.5244,
            longitude=3.3792,
            horizon_start=datetime(2026, 8, 17, 15, tzinfo=UTC),
            horizon_end=datetime(2026, 8, 17, 16, tzinfo=UTC),
            forecast_cutoff=datetime(2026, 8, 17, 12, tzinfo=UTC),
        )

    def test_decide_freezes_current_cross_miner_evidence_then_resolve_later(self):
        capabilities = [
            MinerCapability(
                "own",
                "oathcast-weather",
                "OathCast",
                "https://own.example",
                frozenset({"WEATHER_FORECAST"}),
                historical_reliability=0.5,
            ),
            MinerCapability(
                "external",
                "external-weather",
                "External",
                "https://external.example",
                frozenset({"WEATHER_FORECAST"}),
                historical_reliability=0.8,
            ),
        ]
        router = CrossMinerRouter(
            capabilities,
            clients={
                "oathcast-weather": lambda question: {"probability": 0.1},
                "external-weather": lambda question: {"probability": 0.9},
            },
            own_slugs={"oathcast-weather"},
            clock=lambda: datetime(2026, 8, 17, 11, 59, tzinfo=UTC),
        )
        observation = PrecipitationObservation(
            event_id=self.question.event_id,
            latitude=self.question.latitude,
            longitude=self.question.longitude,
            window_start=self.question.horizon_start,
            window_end=self.question.horizon_end,
            precipitation_mm=0.101,
            source="station-fixture",
            observation_id="workflow-observation-1",
            observed_at=datetime(2026, 8, 17, 16, 5, tzinfo=UTC),
        )

        with tempfile.TemporaryDirectory() as directory:
            store = SqliteCaseStore(Path(directory) / "cases.sqlite3")
            workflow = ApplicationWorkflow(
                router,
                store,
                MappingObservationSource({self.question.event_id: observation}),
                clock=lambda: datetime(2026, 8, 17, 17, tzinfo=UTC),
            )
            decision = workflow.decide(self.question)
            self.assertTrue(decision.used_external_miner)
            self.assertTrue(decision.external_influence)
            result = workflow.resolve(self.question)
            self.assertEqual(result.outcome, 1)
            record = store.get(self.question.event_id)
            self.assertIsNotNone(record)
            self.assertIsNotNone(record["decision"])
            self.assertEqual(record["ground_truth"]["outcome"], 1)

            with self.assertRaises(CaseStateError):
                workflow.decide(self.question)


if __name__ == "__main__":
    unittest.main()
