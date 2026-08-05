import json
from datetime import datetime, timezone
import unittest

from oathcast.forecast import CanonicalForecast, ForecastQuestion
from oathcast.render import RENDERER_VERSION, public_response, public_response_json


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.question = ForecastQuestion(
            event_id="render-1",
            location_name="Lagos",
            latitude=6.5244,
            longitude=3.3792,
            horizon_start=datetime(2026, 8, 17, 15, tzinfo=timezone.utc),
            horizon_end=datetime(2026, 8, 17, 16, tzinfo=timezone.utc),
            forecast_cutoff=datetime(2026, 8, 17, 12, tzinfo=timezone.utc),
        )
        self.forecast = CanonicalForecast(
            event_id="render-1",
            provider="fixture",
            probability=0.7055,
            horizon_start=self.question.horizon_start,
            horizon_end=self.question.horizon_end,
            threshold_mm=0.1,
            issued_at=self.question.forecast_cutoff,
            native_event_definition="fixture",
            event_equivalence="test",
            adapter_version="test",
        )

    def test_public_envelope_is_small_and_probability_matches_text(self):
        response = public_response(self.question, self.forecast)
        self.assertEqual(set(response), {"content", "probability"})
        self.assertEqual(response["probability"], 0.7055)
        self.assertIn("70.55%", response["content"])
        self.assertIn("measurable precipitation > 0.1 mm", response["content"])

    def test_public_json_is_stable(self):
        encoded = public_response_json(self.question, self.forecast)
        self.assertEqual(
            encoded,
            '{"content":"At Lagos, the probability of measurable precipitation > 0.1 mm '
            'from 2026-08-17T15:00:00Z to 2026-08-17T16:00:00Z is 70.55%.",'
            '"probability":0.7055}',
        )
        self.assertNotIn(RENDERER_VERSION, encoded)


if __name__ == "__main__":
    unittest.main()
