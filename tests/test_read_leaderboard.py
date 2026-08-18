"""Intent selection for the Explorer leaderboard reader.

These tests exist because of a real failure: the reader defaulted to every
Intent in ``WEATHER_INTENTS``, and ``parse_leaderboards`` fails closed on any
requested Intent that is absent. When the live endpoint stopped serving
``WEATHER_RISK_ASSESSMENT`` the whole read aborted with exit 2, so the tool
could not report our own score at all. The fix keeps the fail-closed behaviour
where it carries a signal about *our* registration and drops it where the
response is only describing somebody else's Intent list.
"""

from pathlib import Path
import unittest

from oathcast.discovery import WEATHER_INTENTS
from oathcast.leaderboard import LeaderboardError
from scripts.read_leaderboard import DECLARED_INTENTS, select_boards


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_MINER = ROOT / "miners" / "oathcast-weather.yaml"


class DeclaredIntentTests(unittest.TestCase):
    def test_declared_intents_match_the_registered_yaml(self):
        """The reader's notion of "ours" must track the registered contract.

        ``DECLARED_INTENTS`` carries a keep-in-sync comment, and the registered
        YAML is pinned on-chain, so a divergence here would silently change
        which Intent the reader treats as a hard requirement.
        """

        text = CANONICAL_MINER.read_text(encoding="utf-8")
        marker = "supported_intents:"
        self.assertIn(marker, text)
        tail = text[text.index(marker) + len(marker) :]
        declared = []
        for line in tail.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                declared.append(stripped[2:].strip())
            elif stripped:
                break
        self.assertEqual(tuple(declared), DECLARED_INTENTS)

    def test_declared_intents_are_weather_intents(self):
        self.assertTrue(set(DECLARED_INTENTS).issubset(WEATHER_INTENTS))


class SelectBoardsTests(unittest.TestCase):
    def test_absent_context_intent_is_skipped_not_fatal(self):
        """The regression: a weather Intent we do not declare may be missing."""

        available = {"WEATHER_FORECAST": "wf", "WEATHER_CHECK": "wc"}
        boards, omitted = select_boards(available, None)
        self.assertEqual(sorted(boards), ["WEATHER_CHECK", "WEATHER_FORECAST"])
        self.assertIn("WEATHER_RISK_ASSESSMENT", omitted)

    def test_declared_intent_first_then_context_in_sorted_order(self):
        available = {name: name for name in sorted(WEATHER_INTENTS)}
        boards, omitted = select_boards(available, None)
        self.assertEqual(tuple(boards)[: len(DECLARED_INTENTS)], DECLARED_INTENTS)
        self.assertEqual(omitted, ())
        self.assertEqual(set(boards), set(WEATHER_INTENTS))

    def test_missing_declared_intent_fails_closed(self):
        """Our own Intent disappearing is a signal, not noise."""

        with self.assertRaises(LeaderboardError) as caught:
            select_boards({"WEATHER_CHECK": "wc"}, None)
        self.assertIn("declared intent", str(caught.exception))
        self.assertIn("WEATHER_FORECAST", str(caught.exception))

    def test_explicitly_requested_absent_intent_fails_closed(self):
        with self.assertRaises(LeaderboardError) as caught:
            select_boards({"WEATHER_FORECAST": "wf"}, ("WEATHER_RISK_ASSESSMENT",))
        self.assertIn("requested intent", str(caught.exception))

    def test_explicit_request_is_honoured_exactly(self):
        """An explicit request adds no context Intents of its own accord."""

        available = {"WEATHER_FORECAST": "wf", "WEATHER_CHECK": "wc"}
        boards, omitted = select_boards(available, ("WEATHER_FORECAST",))
        self.assertEqual(list(boards), ["WEATHER_FORECAST"])
        self.assertEqual(omitted, ())

    def test_empty_response_reports_what_was_available(self):
        with self.assertRaises(LeaderboardError) as caught:
            select_boards({}, None)
        self.assertIn("none", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
