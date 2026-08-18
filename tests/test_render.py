import json
from datetime import datetime, timezone
import unittest

from oathcast.forecast import CanonicalForecast, ForecastQuestion
from oathcast.render import (
    PREVIOUS_RENDERER_VERSION,
    RENDERER_VERSION,
    _measurement,
    calibrated_phrase,
    public_response,
    public_response_json,
    render_forecast_content,
    render_forecast_content_v1,
)
from oathcast.script_benchmark import evaluate_robust_reference


def _question(event_id="render-1", location="Lagos"):
    return ForecastQuestion(
        event_id=event_id,
        location_name=location,
        latitude=6.5244,
        longitude=3.3792,
        horizon_start=datetime(2026, 8, 17, 15, tzinfo=timezone.utc),
        horizon_end=datetime(2026, 8, 17, 16, tzinfo=timezone.utc),
        forecast_cutoff=datetime(2026, 8, 17, 12, tzinfo=timezone.utc),
    )


def _forecast(question, probability=0.7055):
    return CanonicalForecast(
        event_id=question.event_id,
        provider="fixture",
        probability=probability,
        horizon_start=question.horizon_start,
        horizon_end=question.horizon_end,
        threshold_mm=0.1,
        issued_at=question.forecast_cutoff,
        native_event_definition="fixture",
        event_equivalence="test",
        adapter_version="test",
    )


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.question = _question()
        self.forecast = _forecast(self.question)

    def test_public_envelope_is_small_and_probability_matches_text(self):
        response = public_response(self.question, self.forecast)
        self.assertEqual(set(response), {"content", "probability"})
        self.assertEqual(response["probability"], 0.7055)
        self.assertIn("70.55%", response["content"])
        self.assertIn("measurable precipitation > 0.1 mm", response["content"].lower())

    def test_public_json_is_stable(self):
        encoded = public_response_json(self.question, self.forecast)
        self.assertEqual(
            encoded,
            '{"content":"Measurable precipitation > 0.1 mm is likely to occur in Lagos '
            "in the hour from 15:00 to 16:00 UTC on 17 August 2026. "
            'Probability: 70.55%.","probability":0.7055}',
        )
        self.assertNotIn(RENDERER_VERSION, encoded)

    def test_v1_is_retained_verbatim_for_regression(self):
        self.assertEqual(PREVIOUS_RENDERER_VERSION, "semantic_text_v1")
        self.assertEqual(
            render_forecast_content_v1(self.question, self.forecast),
            "At Lagos, the probability of measurable precipitation > 0.1 mm "
            "from 2026-08-17T15:00:00Z to 2026-08-17T16:00:00Z is 70.55%.",
        )

    def test_v2_replaces_iso_stamps_with_readable_clock_time(self):
        content = render_forecast_content(self.question, self.forecast)
        self.assertIn("15:00", content)
        self.assertIn("16:00", content)
        self.assertIn("17 August 2026", content)
        self.assertNotIn("2026-08-17T15:00:00Z", content)

    def test_event_id_mismatch_is_rejected_by_both_renderers(self):
        other = _forecast(_question(event_id="different"))
        for renderer in (render_forecast_content, render_forecast_content_v1):
            with self.subTest(renderer=renderer.__name__):
                with self.assertRaises(ValueError):
                    renderer(self.question, other)

    def test_renderer_never_asserts_a_resolved_outcome(self):
        """A forecast states likelihood; it must not claim the event happened."""

        for probability in (0.0, 0.02, 0.5, 0.98, 1.0):
            with self.subTest(probability=probability):
                content = render_forecast_content(
                    self.question, _forecast(self.question, probability)
                )
                lowered = content.lower()
                self.assertFalse(lowered.startswith("yes"))
                self.assertFalse(lowered.startswith("no"))
                self.assertNotIn("occurred", lowered)
                self.assertNotIn("did not occur", lowered)

    def test_measurement_does_not_render_negative_zero(self):
        self.assertEqual(_measurement(-0.004), "0")
        self.assertEqual(_measurement(-0.01), "-0.01")


class CalibratedPhraseTests(unittest.TestCase):
    def test_below_half_never_contains_the_bare_word_likely(self):
        """Guards the contradiction that a word-boundary polarity check flags.

        A phrase such as "slightly less likely than not" matches ``\\blikely\\b``
        and so reads as a positive claim, while the number says negative. That
        scores zero on the anti-gaming benchmark and is ambiguous to a reader.
        """

        probability = 0.0
        for step in range(0, 500):
            probability = step / 1000
            phrase = calibrated_phrase(probability)
            with self.subTest(probability=probability):
                self.assertNotIn(" likely", f" {phrase}".replace("unlikely", ""))

    def test_ladder_is_monotonic_and_covers_the_full_range(self):
        seen = []
        for step in range(0, 1001):
            phrase = calibrated_phrase(step / 1000)
            if not seen or seen[-1] != phrase:
                seen.append(phrase)
        self.assertEqual(
            seen,
            [
                "very unlikely to occur",
                "unlikely to occur",
                "somewhat unlikely to occur",
                "as likely as not to occur",
                "more likely than not to occur",
                "likely to occur",
                "very likely to occur",
            ],
        )

    def test_exact_boundaries(self):
        self.assertEqual(calibrated_phrase(0.50), "as likely as not to occur")
        self.assertEqual(calibrated_phrase(0.66), "likely to occur")
        self.assertEqual(calibrated_phrase(0.90), "very likely to occur")

    def test_as_likely_as_not_never_applies_below_half(self):
        """That phrase contains a bare "likely"; below 0.5 it would contradict."""

        for step in range(0, 500):
            probability = step / 1000
            with self.subTest(probability=probability):
                self.assertNotIn("as likely as not", calibrated_phrase(probability))


class RendererIntegrityTests(unittest.TestCase):
    """The renderer must never trip the anti-gaming benchmark.

    A wording change that raised a similarity score by contradicting itself or
    stuffing keywords would be worthless and dishonest. This pins that.

    Note what is deliberately *not* in `FATAL`: `polarity_mismatch`. That issue
    compares the response against resolved ground truth, so it fires whenever a
    forecast that said "likely" meets an event that did not happen. That is
    being wrong, which is a normal and unavoidable outcome for any probabilistic
    forecaster — not dishonesty. The only way to never trip it would be to stop
    stating a direction at all, which would make the forecast useless. Integrity
    issues are the ones where the response contradicts *itself* or games the
    scorer, and those are listed below.
    """

    FATAL = frozenset(
        {
            "wrong_time_window",
            "keyword_stuffing",
            "contradictory_polarity",
            "contradictory_probability",
            "response_too_long",
            "malformed_json_like_response",
            "empty_response",
        }
    )

    def test_no_disqualifying_issue_at_any_probability(self):
        question = _question()
        prompt = (
            "Will measurable precipitation > 0.1 mm occur in Lagos "
            "from 15:00 to 16:00 UTC?"
        )
        truths = {
            True: (
                "Yes. Measurable precipitation occurred in Lagos "
                "during the requested UTC hour."
            ),
            False: (
                "No. Measurable precipitation did not occur in Lagos "
                "during the requested UTC hour."
            ),
        }

        probability = 0.0
        for step in range(0, 1001):
            probability = step / 1000
            content = render_forecast_content(question, _forecast(question, probability))
            for occurred, truth in truths.items():
                evaluation = evaluate_robust_reference(prompt, truth, content)
                issues = self.FATAL.intersection(evaluation.issues)
                with self.subTest(probability=probability, occurred=occurred):
                    self.assertEqual(issues, set(), f"{probability}: {content}")


if __name__ == "__main__":
    unittest.main()
