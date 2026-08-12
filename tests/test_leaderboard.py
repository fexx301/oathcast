import json
import unittest

from oathcast.leaderboard import (
    CONTROL_INTENT,
    IGNORED_PARAMS,
    Leaderboard,
    LeaderboardError,
    assert_filter_supported,
    fetch_intent_leaderboard,
    fetch_weather_leaderboards,
    leaderboard_url,
    parse_leaderboard,
    renderer_target,
)


def entry(slug, score, position, status="active", requests=20, epochs=1):
    return {
        "miner_slug": slug,
        "activation_status": status,
        "avg_score": score,
        "best_rank": position,
        "epochs_participated": epochs,
        "total_requests_served": requests,
        "position": position,
    }


# Shapes below mirror live responses observed on 2026-08-12, epoch 178.
FORECAST_PAYLOAD = {
    "entries": [
        entry("openweathermap", 0.5444076657295227, 1, status="superseded", requests=21),
        entry("weatherapi", 0.5931469798088074, 2, requests=20),
        entry("bittensor-sn18-zeus", 0.4315069317817688, 3, requests=8),
    ],
    "epoch_start": 178,
    "epoch_end": 178,
    "intent_id": "WEATHER_FORECAST",
}

CHECK_PAYLOAD = {
    "entries": [
        entry("openweathermap", 0.5959457159042358, 1, status="superseded", requests=21),
        entry("weatherapi", 0.6262577772140503, 2, requests=20),
        entry("bittensor-sn18-zeus", 0.362461656332016, 3, requests=8),
    ],
    "epoch_start": 178,
    "epoch_end": 178,
    "intent_id": "WEATHER_CHECK",
}

RISK_PAYLOAD = {
    "entries": [entry("bittensor-sn18-zeus", 0.3227233290672302, 1, requests=8)],
    "epoch_start": 178,
    "epoch_end": 178,
    "intent_id": "WEATHER_RISK_ASSESSMENT",
}

CONTROL_PAYLOAD = {
    "entries": [],
    "epoch_start": 178,
    "epoch_end": 178,
    "intent_id": CONTROL_INTENT,
}

# The unfiltered board: no intent_id, and total_requests_served is present.
FLAT_PAYLOAD = {
    "entries": [entry(f"miner-{i}", 0.6 - i / 100, i + 1) for i in range(41)],
    "epoch_start": 178,
    "epoch_end": 178,
}


class FakeFetcher:
    """Routes by URL so a test can simulate a silently-ignored filter."""

    def __init__(self, by_intent, fallback=FLAT_PAYLOAD):
        self.by_intent = by_intent
        self.fallback = fallback
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        for name, payload in self.by_intent.items():
            if f"intent={name}" in url:
                return payload
        return self.fallback


def working_fetcher():
    return FakeFetcher(
        {
            "WEATHER_FORECAST": FORECAST_PAYLOAD,
            "WEATHER_CHECK": CHECK_PAYLOAD,
            "WEATHER_RISK_ASSESSMENT": RISK_PAYLOAD,
            CONTROL_INTENT: CONTROL_PAYLOAD,
        }
    )


class LeaderboardUrlTests(unittest.TestCase):
    def test_intent_is_the_supported_filter_parameter(self):
        self.assertEqual(
            leaderboard_url("WEATHER_CHECK"),
            "https://explorer.telegraphprotocol.com/api/leaderboard/miners?intent=WEATHER_CHECK",
        )

    def test_unfiltered_url_has_no_query(self):
        self.assertTrue(leaderboard_url().endswith("/api/leaderboard/miners"))

    def test_limit_is_honoured_because_the_api_applies_it(self):
        self.assertIn("limit=17", leaderboard_url("WEATHER_CHECK", limit=17))

    def test_silently_ignored_parameters_are_documented(self):
        self.assertEqual(IGNORED_PARAMS, {"intent_type", "epoch"})

    def test_empty_intent_is_rejected_rather_than_sent_as_a_blank_filter(self):
        with self.assertRaises(ValueError):
            leaderboard_url("   ")


class NegativeControlTests(unittest.TestCase):
    def test_control_passes_when_filter_applies(self):
        assert_filter_supported(working_fetcher())  # must not raise

    def test_ignored_filter_is_caught_instead_of_returning_a_full_board(self):
        """The trap: a filter that does nothing returns 41 entries and looks fine."""

        broken = FakeFetcher({}, fallback=FLAT_PAYLOAD)
        with self.assertRaises(LeaderboardError) as caught:
            assert_filter_supported(broken)
        self.assertIn("41 entries", str(caught.exception))
        self.assertIn("not being applied", str(caught.exception))

    def test_control_runs_before_any_score_is_read(self):
        broken = FakeFetcher({}, fallback=FLAT_PAYLOAD)
        with self.assertRaises(LeaderboardError):
            fetch_intent_leaderboard("WEATHER_CHECK", fetch=broken)
        self.assertTrue(all(CONTROL_INTENT in url for url in broken.urls))

    def test_control_runs_once_for_a_multi_intent_read(self):
        fetcher = working_fetcher()
        fetch_weather_leaderboards(fetch=fetcher)
        controls = [url for url in fetcher.urls if CONTROL_INTENT in url]
        self.assertEqual(len(controls), 1)


class RequestsServedTests(unittest.TestCase):
    def test_per_intent_reads_drop_total_requests_served(self):
        """It is the Miner's cross-Intent total, not this Intent's volume."""

        board = parse_leaderboard(FORECAST_PAYLOAD, intent="WEATHER_FORECAST")
        self.assertTrue(all(e.requests_served is None for e in board.entries))

    def test_unfiltered_read_keeps_it_because_there_it_means_what_it_says(self):
        board = parse_leaderboard(FLAT_PAYLOAD)
        self.assertEqual(board.entries[0].requests_served, 20)

    def test_the_live_trap_is_reproduced_identical_totals_across_intents(self):
        forecast = parse_leaderboard(FORECAST_PAYLOAD, intent="WEATHER_FORECAST")
        check = parse_leaderboard(CHECK_PAYLOAD, intent="WEATHER_CHECK")
        raw_forecast = {e["miner_slug"]: e["total_requests_served"] for e in FORECAST_PAYLOAD["entries"]}
        raw_check = {e["miner_slug"]: e["total_requests_served"] for e in CHECK_PAYLOAD["entries"]}
        # Identical in the raw payload even though avg_score differs...
        self.assertEqual(raw_forecast["weatherapi"], raw_check["weatherapi"])
        self.assertNotEqual(
            forecast.leader().avg_score, check.leader().avg_score
        )
        # ...so the parsed entries must not expose it at all.
        self.assertIsNone(forecast.entries[0].requests_served)
        self.assertIsNone(check.entries[0].requests_served)


class SupersededPositionTests(unittest.TestCase):
    def test_position_one_can_be_superseded(self):
        board = parse_leaderboard(FORECAST_PAYLOAD, intent="WEATHER_FORECAST")
        top = board.entries[0]
        self.assertEqual(top.position, 1)
        self.assertFalse(top.is_active)

    def test_leader_is_the_best_active_miner_not_position_one(self):
        board = parse_leaderboard(FORECAST_PAYLOAD, intent="WEATHER_FORECAST")
        self.assertEqual(board.leader().slug, "weatherapi")
        self.assertAlmostEqual(board.target_score(), 0.5931469798088074)

    def test_describe_never_emits_a_bare_position(self):
        board = parse_leaderboard(FORECAST_PAYLOAD, intent="WEATHER_FORECAST")
        rendered = board.entries[0].describe()
        self.assertIn("superseded", rendered)
        self.assertIn("position 1", rendered)

    def test_leader_is_none_when_no_miner_is_active(self):
        payload = {
            "entries": [entry("gone", 0.9, 1, status="deregistered")],
            "intent_id": "WEATHER_CHECK",
        }
        board = parse_leaderboard(payload, intent="WEATHER_CHECK")
        self.assertIsNone(board.leader())
        self.assertIsNone(board.target_score())


class PopulationAndEpochTests(unittest.TestCase):
    def test_population_is_available_as_a_rank_denominator(self):
        self.assertEqual(parse_leaderboard(FLAT_PAYLOAD).population, 41)
        self.assertEqual(
            parse_leaderboard(FORECAST_PAYLOAD, intent="WEATHER_FORECAST").population, 3
        )

    def test_single_epoch_evidence_is_flagged(self):
        self.assertTrue(
            parse_leaderboard(CHECK_PAYLOAD, intent="WEATHER_CHECK").single_epoch()
        )

    def test_multi_epoch_board_is_not_flagged(self):
        payload = {
            "entries": [entry("veteran", 0.7, 1, epochs=9)],
            "intent_id": "WEATHER_CHECK",
        }
        self.assertFalse(parse_leaderboard(payload, intent="WEATHER_CHECK").single_epoch())


class TargetSelectionTests(unittest.TestCase):
    def test_target_is_the_hardest_declared_intent_not_an_average(self):
        boards = {
            "WEATHER_FORECAST": parse_leaderboard(FORECAST_PAYLOAD, intent="WEATHER_FORECAST"),
            "WEATHER_CHECK": parse_leaderboard(CHECK_PAYLOAD, intent="WEATHER_CHECK"),
            "WEATHER_RISK_ASSESSMENT": parse_leaderboard(RISK_PAYLOAD, intent="WEATHER_RISK_ASSESSMENT"),
        }
        declared = ("WEATHER_FORECAST", "WEATHER_CHECK")
        target = renderer_target(boards, declared)
        self.assertAlmostEqual(target, 0.6262577772140503)

    def test_undeclared_intents_do_not_lower_the_target(self):
        """WEATHER_RISK_ASSESSMENT scores 0.3227; including it would mislead."""

        boards = {
            "WEATHER_FORECAST": parse_leaderboard(FORECAST_PAYLOAD, intent="WEATHER_FORECAST"),
            "WEATHER_CHECK": parse_leaderboard(CHECK_PAYLOAD, intent="WEATHER_CHECK"),
            "WEATHER_RISK_ASSESSMENT": parse_leaderboard(RISK_PAYLOAD, intent="WEATHER_RISK_ASSESSMENT"),
        }
        with_risk = renderer_target(boards, ("WEATHER_FORECAST", "WEATHER_CHECK", "WEATHER_RISK_ASSESSMENT"))
        without = renderer_target(boards, ("WEATHER_FORECAST", "WEATHER_CHECK"))
        self.assertEqual(with_risk, without)

    def test_flat_average_would_have_been_wrong_in_both_directions(self):
        """0.6097 sat between the two real bars — the regression this guards."""

        forecast = 0.5931469798088074
        check = 0.6262577772140503
        flat = 0.6097023785114288
        self.assertGreater(flat, forecast)
        self.assertLess(flat, check)


class ParsingGuardTests(unittest.TestCase):
    def test_missing_entries_list_is_rejected(self):
        with self.assertRaises(LeaderboardError):
            parse_leaderboard({"epoch_start": 178})

    def test_non_object_payload_is_rejected(self):
        with self.assertRaises(LeaderboardError):
            parse_leaderboard([entry("a", 0.5, 1)])

    def test_echoed_intent_mismatch_is_rejected(self):
        with self.assertRaises(LeaderboardError) as caught:
            parse_leaderboard(CHECK_PAYLOAD, intent="WEATHER_FORECAST")
        self.assertIn("echoed", str(caught.exception))

    def test_non_numeric_score_is_rejected_rather_than_coerced(self):
        payload = {"entries": [{**entry("a", 0.5, 1), "avg_score": "0.5"}]}
        with self.assertRaises(LeaderboardError):
            parse_leaderboard(payload)

    def test_boolean_score_is_not_treated_as_a_number(self):
        payload = {"entries": [{**entry("a", 0.5, 1), "avg_score": True}]}
        with self.assertRaises(LeaderboardError):
            parse_leaderboard(payload)

    def test_missing_activation_status_is_rejected(self):
        payload = {"entries": [{k: v for k, v in entry("a", 0.5, 1).items() if k != "activation_status"}]}
        with self.assertRaises(LeaderboardError) as caught:
            parse_leaderboard(payload)
        self.assertIn("activation_status", str(caught.exception))


class FilterProvenanceTests(unittest.TestCase):
    def test_verified_reads_record_that_the_control_ran(self):
        board = fetch_intent_leaderboard("WEATHER_CHECK", fetch=working_fetcher())
        self.assertTrue(board.filter_verified)

    def test_unverified_reads_are_marked_as_such(self):
        board = parse_leaderboard(CHECK_PAYLOAD, intent="WEATHER_CHECK")
        self.assertFalse(board.filter_verified)


if __name__ == "__main__":
    unittest.main()
