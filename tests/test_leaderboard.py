import copy
import json
import unittest
from urllib.error import HTTPError, URLError

import oathcast.leaderboard as module
from oathcast.leaderboard import (
    LeaderboardError,
    fetch_intent_leaderboard,
    fetch_weather_leaderboards,
    leaderboard_url,
    parse_leaderboard,
    parse_leaderboards,
    renderer_target,
)


# Captured subset of the live 2026-08-12 response.  It deliberately includes a
# non-weather Intent whose only returned entry has rank 2: entry count is not a
# trustworthy rank denominator in the current API.
CAPTURED_RESPONSE = """
{
  "epoch": 178,
  "intents": {
    "DEEPFAKE_DETECTION": [
      {
        "miner_slug": "bittensor-sn34-bitmind",
        "score": 0,
        "rank": 2,
        "activation_status": "active"
      }
    ],
    "WEATHER_CHECK": [
      {
        "miner_slug": "weatherapi",
        "score": 0.6262578,
        "rank": 1,
        "activation_status": "active"
      },
      {
        "miner_slug": "openweathermap",
        "score": 0.5959457,
        "rank": 2,
        "activation_status": "superseded"
      },
      {
        "miner_slug": "bittensor-sn18-zeus",
        "score": 0.36246166,
        "rank": 3,
        "activation_status": "active"
      }
    ],
    "WEATHER_FORECAST": [
      {
        "miner_slug": "weatherapi",
        "score": 0.593147,
        "rank": 1,
        "activation_status": "active"
      },
      {
        "miner_slug": "openweathermap",
        "score": 0.54440767,
        "rank": 2,
        "activation_status": "superseded"
      },
      {
        "miner_slug": "bittensor-sn18-zeus",
        "score": 0.43150693,
        "rank": 3,
        "activation_status": "active"
      }
    ],
    "WEATHER_RISK_ASSESSMENT": [
      {
        "miner_slug": "bittensor-sn18-zeus",
        "score": 0.32272333,
        "rank": 1,
        "activation_status": "active"
      }
    ]
  }
}
"""

CAPTURED_PAYLOAD = json.loads(CAPTURED_RESPONSE)


def current_entry(slug, score, rank, status="active"):
    return {
        "miner_slug": slug,
        "score": score,
        "rank": rank,
        "activation_status": status,
    }


def snapshot(*entries, intent="WEATHER_CHECK", epoch=178):
    return {"epoch": epoch, "intents": {intent: list(entries)}}


class RecordingFetcher:
    def __init__(self, payload=CAPTURED_PAYLOAD):
        self.payload = payload
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        return self.payload


class CapturedShapeTests(unittest.TestCase):
    def test_captured_epoch_and_intent_mapping_parse(self):
        boards = parse_leaderboards(CAPTURED_PAYLOAD)
        self.assertEqual(boards["WEATHER_CHECK"].epoch, 178)
        self.assertEqual(boards["WEATHER_CHECK"].intent, "WEATHER_CHECK")
        self.assertEqual(boards["WEATHER_CHECK"].population, 3)

    def test_entries_use_live_score_and_rank_names(self):
        entry = parse_leaderboard(
            CAPTURED_PAYLOAD, intent="WEATHER_CHECK"
        ).entries[0]
        self.assertEqual(entry.slug, "weatherapi")
        self.assertAlmostEqual(entry.score, 0.6262578)
        self.assertEqual(entry.rank, 1)
        self.assertFalse(hasattr(entry, "avg_score"))
        self.assertFalse(hasattr(entry, "position"))

    def test_rank_gap_proves_population_is_only_returned_entry_count(self):
        board = parse_leaderboard(
            CAPTURED_PAYLOAD, intent="DEEPFAKE_DETECTION"
        )
        self.assertEqual(board.population, 1)
        self.assertEqual(board.entries[0].rank, 2)

    def test_captured_superseded_entry_is_not_selected_as_active(self):
        board = parse_leaderboard(CAPTURED_PAYLOAD, intent="WEATHER_FORECAST")
        superseded = board.entries[1]
        self.assertEqual(superseded.slug, "openweathermap")
        self.assertEqual(superseded.rank, 2)
        self.assertFalse(superseded.is_active)
        self.assertEqual(board.leader().slug, "weatherapi")
        self.assertAlmostEqual(board.target_score(), 0.593147)

    def test_describe_uses_rank_and_always_includes_status(self):
        entry = parse_leaderboard(
            CAPTURED_PAYLOAD, intent="WEATHER_FORECAST"
        ).entries[1]
        rendered = entry.describe()
        self.assertIn("rank 2", rendered)
        self.assertIn("superseded", rendered)
        self.assertNotIn("position", rendered)


class EndpointSelectionTests(unittest.TestCase):
    def test_url_is_the_bare_epoch_wide_endpoint(self):
        self.assertEqual(
            leaderboard_url(),
            "https://explorer.telegraphprotocol.com/api/leaderboard/miners",
        )

    def test_obsolete_server_side_intent_filter_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            leaderboard_url("WEATHER_CHECK")
        self.assertIn("select", str(caught.exception))
        self.assertIn("locally", str(caught.exception))

    def test_obsolete_limit_query_is_refused_too(self):
        with self.assertRaises(ValueError):
            leaderboard_url(limit=3)

    def test_single_intent_read_fetches_once_without_query_parameters(self):
        fetcher = RecordingFetcher()
        board = fetch_intent_leaderboard("WEATHER_CHECK", fetch=fetcher)
        self.assertEqual(board.intent, "WEATHER_CHECK")
        self.assertEqual(fetcher.urls, [leaderboard_url()])
        self.assertNotIn("?", fetcher.urls[0])

    def test_multi_intent_read_fetches_the_all_intents_response_once(self):
        fetcher = RecordingFetcher()
        boards = fetch_weather_leaderboards(
            ("WEATHER_FORECAST", "WEATHER_CHECK"), fetch=fetcher
        )
        self.assertEqual(
            tuple(boards), ("WEATHER_FORECAST", "WEATHER_CHECK")
        )
        self.assertEqual(fetcher.urls, [leaderboard_url()])

    def test_default_read_selects_all_declared_weather_intents_locally(self):
        fetcher = RecordingFetcher()
        boards = fetch_weather_leaderboards(fetch=fetcher)
        self.assertEqual(
            set(boards),
            {
                "WEATHER_CHECK",
                "WEATHER_FORECAST",
                "WEATHER_RISK_ASSESSMENT",
            },
        )
        self.assertEqual(len(fetcher.urls), 1)

    def test_selection_is_an_exact_mapping_key_not_a_fallback(self):
        fetcher = RecordingFetcher()
        with self.assertRaises(LeaderboardError) as caught:
            fetch_intent_leaderboard("weather_check", fetch=fetcher)
        self.assertIn("missing requested intent", str(caught.exception))
        self.assertEqual(fetcher.urls, [leaderboard_url()])

    def test_missing_requested_intent_reports_available_keys(self):
        with self.assertRaises(LeaderboardError) as caught:
            parse_leaderboards(CAPTURED_PAYLOAD, ("STORM_ALERT",))
        message = str(caught.exception)
        self.assertIn("STORM_ALERT", message)
        self.assertIn("WEATHER_CHECK", message)

    def test_duplicate_requested_intents_are_rejected(self):
        with self.assertRaises(LeaderboardError):
            parse_leaderboards(
                CAPTURED_PAYLOAD, ("WEATHER_CHECK", "WEATHER_CHECK")
            )


class LeaderAndTargetTests(unittest.TestCase):
    def test_best_active_score_wins_even_when_rank_one_is_superseded(self):
        payload = snapshot(
            current_entry("old", 0.9, 1, "superseded"),
            current_entry("active-a", 0.8, 2),
            current_entry("active-b", 0.7, 3),
        )
        board = parse_leaderboard(payload, intent="WEATHER_CHECK")
        self.assertEqual(board.leader().slug, "active-a")
        self.assertEqual(board.target_score(), 0.8)

    def test_leader_is_none_when_no_miner_is_active(self):
        payload = snapshot(current_entry("gone", 0.9, 1, "deregistered"))
        board = parse_leaderboard(payload, intent="WEATHER_CHECK")
        self.assertIsNone(board.leader())
        self.assertIsNone(board.target_score())

    def test_entries_are_presented_in_server_rank_order(self):
        payload = snapshot(
            current_entry("third", 0.3, 3),
            current_entry("first", 0.8, 1),
            current_entry("second", 0.6, 2),
        )
        board = parse_leaderboard(payload, intent="WEATHER_CHECK")
        self.assertEqual([entry.rank for entry in board.entries], [1, 2, 3])

    def test_target_is_the_hardest_declared_intent_not_an_average(self):
        boards = parse_leaderboards(CAPTURED_PAYLOAD)
        target = renderer_target(
            boards, ("WEATHER_FORECAST", "WEATHER_CHECK")
        )
        self.assertAlmostEqual(target, 0.6262578)

    def test_undeclared_low_scoring_intent_does_not_lower_target(self):
        boards = parse_leaderboards(CAPTURED_PAYLOAD)
        with_risk = renderer_target(
            boards,
            (
                "WEATHER_FORECAST",
                "WEATHER_CHECK",
                "WEATHER_RISK_ASSESSMENT",
            ),
        )
        without_risk = renderer_target(
            boards, ("WEATHER_FORECAST", "WEATHER_CHECK")
        )
        self.assertEqual(with_risk, without_risk)


class ShapeAndCompatibilityGuardTests(unittest.TestCase):
    def test_non_object_payload_is_rejected(self):
        with self.assertRaises(LeaderboardError):
            parse_leaderboards([])

    def test_legacy_entries_shape_is_explicitly_rejected(self):
        legacy = {
            "entries": [
                {
                    "miner_slug": "weatherapi",
                    "avg_score": 0.6,
                    "position": 1,
                    "activation_status": "active",
                }
            ],
            "epoch_start": 178,
            "epoch_end": 178,
            "intent_id": "WEATHER_CHECK",
        }
        with self.assertRaises(LeaderboardError) as caught:
            parse_leaderboard(legacy, intent="WEATHER_CHECK")
        self.assertIn("obsolete", str(caught.exception))

    def test_mixed_root_generations_are_rejected_as_ambiguous(self):
        payload = copy.deepcopy(CAPTURED_PAYLOAD)
        payload["entries"] = []
        with self.assertRaises(LeaderboardError) as caught:
            parse_leaderboards(payload)
        self.assertIn("ambiguously mixes", str(caught.exception))

    def test_mixed_entry_generations_are_rejected_as_ambiguous(self):
        payload = snapshot(
            {
                **current_entry("weatherapi", 0.6, 1),
                "avg_score": 0.6,
                "position": 1,
            }
        )
        with self.assertRaises(LeaderboardError) as caught:
            parse_leaderboard(payload, intent="WEATHER_CHECK")
        self.assertIn("obsolete leaderboard fields", str(caught.exception))

    def test_one_intent_parser_requires_an_explicit_exact_intent(self):
        with self.assertRaises(LeaderboardError) as caught:
            parse_leaderboard(CAPTURED_PAYLOAD)
        self.assertIn("intent is required", str(caught.exception))

    def test_missing_epoch_is_rejected(self):
        with self.assertRaises(LeaderboardError) as caught:
            parse_leaderboards({"intents": {}})
        self.assertIn("epoch", str(caught.exception))

    def test_boolean_epoch_is_not_treated_as_an_integer(self):
        with self.assertRaises(LeaderboardError):
            parse_leaderboards({"epoch": True, "intents": {}})

    def test_negative_epoch_is_rejected(self):
        with self.assertRaises(LeaderboardError):
            parse_leaderboards({"epoch": -1, "intents": {}})

    def test_intents_must_be_an_object(self):
        with self.assertRaises(LeaderboardError):
            parse_leaderboards({"epoch": 178, "intents": []})

    def test_intent_values_must_be_lists(self):
        with self.assertRaises(LeaderboardError):
            parse_leaderboards(
                {"epoch": 178, "intents": {"WEATHER_CHECK": {}}}
            )

    def test_non_string_intent_key_is_rejected(self):
        with self.assertRaises(LeaderboardError):
            parse_leaderboards({"epoch": 178, "intents": {7: []}})

    def test_malformed_unrequested_intent_still_fails_the_snapshot(self):
        payload = copy.deepcopy(CAPTURED_PAYLOAD)
        payload["intents"]["OTHER"] = [{"miner_slug": "broken"}]
        with self.assertRaises(LeaderboardError):
            parse_leaderboard(payload, intent="WEATHER_CHECK")


class EntryValidationTests(unittest.TestCase):
    def test_each_entry_must_be_an_object(self):
        with self.assertRaises(LeaderboardError):
            parse_leaderboard(snapshot("not an object"), intent="WEATHER_CHECK")

    def test_missing_or_whitespace_slug_is_rejected(self):
        for slug in (None, "", " weatherapi"):
            with self.subTest(slug=slug), self.assertRaises(LeaderboardError):
                parse_leaderboard(
                    snapshot(current_entry(slug, 0.6, 1)),
                    intent="WEATHER_CHECK",
                )

    def test_missing_activation_status_is_rejected(self):
        entry = current_entry("weatherapi", 0.6, 1)
        del entry["activation_status"]
        with self.assertRaises(LeaderboardError) as caught:
            parse_leaderboard(snapshot(entry), intent="WEATHER_CHECK")
        self.assertIn("activation_status", str(caught.exception))

    def test_non_numeric_boolean_and_non_finite_scores_are_rejected(self):
        for score in ("0.6", True, float("nan"), float("inf")):
            with self.subTest(score=score), self.assertRaises(LeaderboardError):
                parse_leaderboard(
                    snapshot(current_entry("weatherapi", score, 1)),
                    intent="WEATHER_CHECK",
                )

    def test_scores_outside_protocol_range_are_rejected(self):
        for score in (-0.01, 1.01):
            with self.subTest(score=score), self.assertRaises(LeaderboardError):
                parse_leaderboard(
                    snapshot(current_entry("weatherapi", score, 1)),
                    intent="WEATHER_CHECK",
                )

    def test_rank_must_be_a_positive_integer(self):
        for rank in (None, True, 0, -1, 1.0):
            with self.subTest(rank=rank), self.assertRaises(LeaderboardError):
                parse_leaderboard(
                    snapshot(current_entry("weatherapi", 0.6, rank)),
                    intent="WEATHER_CHECK",
                )

    def test_duplicate_slug_is_rejected(self):
        payload = snapshot(
            current_entry("same", 0.6, 1),
            current_entry("same", 0.5, 2),
        )
        with self.assertRaises(LeaderboardError) as caught:
            parse_leaderboard(payload, intent="WEATHER_CHECK")
        self.assertIn("duplicate miner_slug", str(caught.exception))

    def test_tied_ranks_are_preserved(self):
        payload = snapshot(
            current_entry("one", 0.6, 1),
            current_entry("two", 0.5, 1),
        )
        board = parse_leaderboard(payload, intent="WEATHER_CHECK")
        self.assertEqual([entry.rank for entry in board.entries], [1, 1])


class UrllibFetchTranslationTests(unittest.TestCase):
    """The real network seam must not leak urllib or decoding exceptions."""

    def _fetch_with_urlopen_raising(self, error):
        closer = getattr(error, "close", None)
        if callable(closer):
            self.addCleanup(closer)

        def fake_urlopen(request, timeout=None):
            raise error

        original = module.urlopen
        module.urlopen = fake_urlopen
        self.addCleanup(setattr, module, "urlopen", original)
        return module.urllib_fetch

    def _fetch_with_body(self, body):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, amount=None):
                return body if amount is None else body[:amount]

        original = module.urlopen
        module.urlopen = lambda request, timeout=None: FakeResponse()
        self.addCleanup(setattr, module, "urlopen", original)
        return module.urllib_fetch

    def test_403_is_translated_with_the_agent_string_as_the_likely_cause(self):
        fetch = self._fetch_with_urlopen_raising(
            HTTPError("https://example.invalid", 403, "Forbidden", {}, None)
        )
        with self.assertRaises(LeaderboardError) as caught:
            fetch("https://example.invalid")
        message = str(caught.exception)
        self.assertIn("403", message)
        self.assertIn("Python-urllib", message)
        self.assertIn(module.USER_AGENT, message)

    def test_403_message_forbids_impersonating_a_browser(self):
        fetch = self._fetch_with_urlopen_raising(
            HTTPError("https://example.invalid", 403, "Forbidden", {}, None)
        )
        with self.assertRaises(LeaderboardError) as caught:
            fetch("https://example.invalid")
        self.assertIn("do not", str(caught.exception).lower())
        self.assertIn("impersonating a browser", str(caught.exception))

    def test_other_http_errors_are_translated_too(self):
        fetch = self._fetch_with_urlopen_raising(
            HTTPError("https://example.invalid", 500, "Server Error", {}, None)
        )
        with self.assertRaises(LeaderboardError) as caught:
            fetch("https://example.invalid")
        self.assertIn("500", str(caught.exception))

    def test_unreachable_host_is_translated(self):
        fetch = self._fetch_with_urlopen_raising(URLError("no route"))
        with self.assertRaises(LeaderboardError) as caught:
            fetch("https://example.invalid")
        self.assertIn("could not reach", str(caught.exception))

    def test_read_timeout_is_translated_even_though_it_is_not_a_urlerror(self):
        self.assertFalse(issubclass(TimeoutError, URLError))
        fetch = self._fetch_with_urlopen_raising(TimeoutError("timed out"))
        with self.assertRaises(LeaderboardError) as caught:
            fetch("https://example.invalid")
        self.assertIn("timed out", str(caught.exception))

    def test_html_fallback_is_reported_as_a_wrong_path_not_a_parse_bug(self):
        fetch = self._fetch_with_body(b"<!doctype html><title>404</title>")
        with self.assertRaises(LeaderboardError) as caught:
            fetch("https://example.invalid")
        self.assertIn("non-JSON", str(caught.exception))
        self.assertIn("not an API route", str(caught.exception))

    def test_non_utf8_response_is_translated(self):
        fetch = self._fetch_with_body(b"\xff\xfe")
        with self.assertRaises(LeaderboardError) as caught:
            fetch("https://example.invalid")
        self.assertIn("not UTF-8", str(caught.exception))

    def test_oversized_response_is_rejected_before_json_parsing(self):
        fetch = self._fetch_with_body(b" " * (module.MAX_RESPONSE_BYTES + 1))
        with self.assertRaises(LeaderboardError) as caught:
            fetch("https://example.invalid")
        self.assertIn("byte cap", str(caught.exception))

    def test_non_bytes_response_body_is_translated(self):
        fetch = self._fetch_with_body("not bytes")
        with self.assertRaises(LeaderboardError) as caught:
            fetch("https://example.invalid")
        self.assertIn("not bytes", str(caught.exception))

    def test_the_agent_is_sent_on_the_request(self):
        sent = {}

        def fake_urlopen(request, timeout=None):
            sent["agent"] = request.get_header("User-agent")
            raise URLError("stop here")

        original = module.urlopen
        module.urlopen = fake_urlopen
        self.addCleanup(setattr, module, "urlopen", original)
        with self.assertRaises(LeaderboardError):
            module.urllib_fetch("https://example.invalid")
        self.assertEqual(sent["agent"], module.USER_AGENT)

    def test_default_agent_is_not_the_blocked_prefix(self):
        self.assertFalse(module.USER_AGENT.startswith("Python-urllib/"))
        self.assertIn("read-only", module.USER_AGENT)


class SubstitutedFetcherTests(unittest.TestCase):
    def test_foreign_exception_is_translated(self):
        class SomeOtherLibraryError(Exception):
            pass

        def fetch(url):
            raise SomeOtherLibraryError("boom")

        with self.assertRaises(LeaderboardError) as caught:
            fetch_weather_leaderboards(fetch=fetch)
        message = str(caught.exception)
        self.assertIn("SomeOtherLibraryError", message)
        self.assertIn("boom", message)

    def test_leaderboard_error_is_not_double_wrapped(self):
        original = LeaderboardError("already actionable")

        def fetch(url):
            raise original

        with self.assertRaises(LeaderboardError) as caught:
            fetch_weather_leaderboards(fetch=fetch)
        self.assertIs(caught.exception, original)

    def test_original_exception_stays_chained_for_debugging(self):
        root = ValueError("root cause")

        def fetch(url):
            raise root

        with self.assertRaises(LeaderboardError) as caught:
            fetch_intent_leaderboard("WEATHER_CHECK", fetch=fetch)
        self.assertIs(caught.exception.__cause__, root)


if __name__ == "__main__":
    unittest.main()
