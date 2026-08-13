"""The outbound agent string, and proof that it reaches the wire.

`leaderboard.py` learned on 2026-08-12 that the Explorer answers urllib's
default `Python-urllib/*` agent with HTTP 403.  The fix there was local; this
module holds the generalisation, so the same lesson does not have to be
relearned per call site.

Two things are tested, and the second is the one that earns its keep:

* `outbound_headers` behaves as documented; and
* every module that calls a host we do not own actually puts the agent on the
  request.

The second matters because the existing suites all substitute the seam *above*
the transport — `tests/test_payment.py` injects a fake `transport`, so
`urllib_transport` is never driven, and that is precisely the function where the
agent is attached on the payment path.  A call site missed during the migration
would leave every one of those tests green.  That is the same shape as the
original leaderboard bug: `HTTPError` escaped for as long as it did because
every test passed a `Fetcher` and nothing exercised `urllib_fetch`.

The payment case has a sharper edge than the rest.  `urllib_transport`
deliberately *returns* non-2xx responses rather than raising, because the 402
challenge lives in the error response — so a bot-filter 403 would arrive as an
ordinary `HttpResult` and be parsed as a failed challenge rather than surfacing
as a blocked request.
"""

import json
import unittest
import unittest.mock

from oathcast.protocol import USER_AGENT, outbound_headers


BLOCKED_PREFIX = "Python-urllib/"


class _FakeResponse:
    """Minimal response covering the shapes the four call sites read."""

    def __init__(self, body: bytes = b'{"ok":true}', headers: dict | None = None):
        self._body = body
        self.headers = headers if headers is not None else {}
        self.status = 200

    def read(self, amount: int | None = None) -> bytes:
        if amount is None:
            return self._body
        return self._body[:amount]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _captured_agent(module_path: str, call) -> str | None:
    """Run `call` with `module_path`'s `urlopen` patched; return the sent agent.

    The agent is read back off the `Request` object rather than from the headers
    dict the caller built, so this asserts what would go out on the wire even if
    a call site later constructs its request differently.
    """

    seen = {}

    def fake_urlopen(request, timeout=None):
        # `Request.get_header` title-cases the name it stores, so query it the
        # same way urllib does internally rather than by exact string match.
        seen["agent"] = request.get_header("User-agent")
        return _FakeResponse()

    with unittest.mock.patch(f"{module_path}.urlopen", fake_urlopen):
        call()
    return seen.get("agent")


class OutboundHeaderTests(unittest.TestCase):
    def test_agent_and_accept_are_set_by_default(self):
        headers = outbound_headers()
        self.assertEqual(headers["User-Agent"], USER_AGENT)
        self.assertEqual(headers["Accept"], "application/json")

    def test_none_behaves_like_no_argument(self):
        self.assertEqual(outbound_headers(None), outbound_headers())

    def test_extra_headers_pass_through(self):
        headers = outbound_headers({"X-Request-ID": "abc"})
        self.assertEqual(headers["X-Request-ID"], "abc")
        self.assertEqual(headers["User-Agent"], USER_AGENT)

    def test_caller_can_override_the_agent_explicitly(self):
        """Documented behaviour: a different identity must be deliberate.

        The failure being designed against is presenting the default agent by
        *omission*, which is how urllib's default reached the Explorer. An
        explicit override is a decision and stays possible.
        """

        headers = outbound_headers({"User-Agent": "Something-Else/2.0"})
        self.assertEqual(headers["User-Agent"], "Something-Else/2.0")

    def test_caller_can_override_accept(self):
        headers = outbound_headers({"Accept": "text/plain"})
        self.assertEqual(headers["Accept"], "text/plain")

    def test_the_callers_dict_is_not_mutated(self):
        """`HttpMinerClient` adds request-ID headers to the returned dict.

        It passes `self.headers`, which is reused across every request the
        client makes, so a returned alias would accumulate one request's IDs
        into the next request's headers.
        """

        original = {"Authorization": "Bearer x"}
        returned = outbound_headers(original)
        returned["X-Request-ID"] = "req-1"
        self.assertEqual(original, {"Authorization": "Bearer x"})

    def test_each_call_returns_a_fresh_dict(self):
        first = outbound_headers()
        first["X-Request-ID"] = "req-1"
        self.assertNotIn("X-Request-ID", outbound_headers())


class AgentIdentityTests(unittest.TestCase):
    def test_agent_is_not_the_blocked_default_prefix(self):
        """Guards a rename. The live filter is anchored on this literal prefix."""

        self.assertFalse(USER_AGENT.startswith(BLOCKED_PREFIX))

    def test_agent_does_not_impersonate_a_browser(self):
        """The goal is being identifiable, not evading a bot filter.

        Sending a browser agent would very likely pass the Explorer's filter,
        which is exactly why the prohibition is asserted rather than left as a
        comment.
        """

        lowered = USER_AGENT.lower()
        for token in ("mozilla", "chrome", "safari", "applewebkit", "gecko"):
            self.assertNotIn(token, lowered)

    def test_agent_names_the_project_and_links_the_source(self):
        self.assertIn("OathCast", USER_AGENT)
        self.assertIn("github.com/fexx301/oathcast", USER_AGENT)


class AgentReachesTheWireTests(unittest.TestCase):
    """One test per migrated call site. A missed migration fails here."""

    def test_payment_transport_sends_the_agent(self):
        """The money path, and the one whose own suite cannot catch this.

        `tests/test_payment.py` injects a fake `transport`, so nothing there
        drives `urllib_transport`.
        """

        from oathcast.payment import urllib_transport

        agent = _captured_agent(
            "oathcast.payment",
            lambda: urllib_transport(
                "GET", "https://dispatcher.example/x", {"Accept": "application/json"}
            ),
        )
        self.assertEqual(agent, USER_AGENT)

    def test_payment_transport_keeps_caller_headers_alongside_the_agent(self):
        """The x402 handshake replays `PAYMENT-SIGNATURE` through this transport."""

        from oathcast.payment import urllib_transport

        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["agent"] = request.get_header("User-agent")
            seen["signature"] = request.get_header("Payment-signature")
            return _FakeResponse()

        with unittest.mock.patch("oathcast.payment.urlopen", fake_urlopen):
            urllib_transport(
                "GET",
                "https://dispatcher.example/x",
                {"Accept": "application/json", "PAYMENT-SIGNATURE": "proof"},
            )
        self.assertEqual(seen["agent"], USER_AGENT)
        self.assertEqual(seen["signature"], "proof")

    def test_provider_fetch_sends_the_agent(self):
        from oathcast.service import fetch_json

        agent = _captured_agent(
            "oathcast.service",
            lambda: fetch_json("https://provider.example/x"),
        )
        self.assertEqual(agent, USER_AGENT)

    def test_service_no_longer_sends_the_old_hardcoded_agent(self):
        """`service.fetch_json` shipped `OathCast/0.1` before this pass.

        Asserted so the two strings cannot silently drift apart again.
        """

        from oathcast.service import fetch_json

        agent = _captured_agent(
            "oathcast.service",
            lambda: fetch_json("https://provider.example/x"),
        )
        self.assertNotEqual(agent, "OathCast/0.1")

    def test_smoke_miner_sends_the_agent(self):
        from scripts.smoke_miner import request_json

        agent = _captured_agent(
            "scripts.smoke_miner",
            lambda: request_json("https://miner.example/forecast"),
        )
        self.assertEqual(agent, USER_AGENT)


class ApplicationClientAgentTests(unittest.TestCase):
    """The Application's direct-HTTP client, which also adds request-ID headers."""

    def _client(self):
        from oathcast.application import HttpMinerClient
        from oathcast.discovery import MinerCapability

        capability = MinerCapability(
            "212",
            "external-alpha",
            "External Alpha",
            "https://miner.example",
            frozenset({"WEATHER_FORECAST"}),
            endpoint_path="/forecast",
        )
        return HttpMinerClient(capability)

    def _question(self):
        from pathlib import Path
        from oathcast.forecast import ForecastQuestion

        root = Path(__file__).resolve().parents[1]
        with open(root / "fixtures" / "question.json", encoding="utf-8") as handle:
            return ForecastQuestion.from_dict(json.load(handle))

    def test_direct_miner_request_sends_the_agent(self):
        client = self._client()
        agent = _captured_agent(
            "oathcast.application",
            lambda: client.request_with_id(self._question(), request_id=None),
        )
        self.assertEqual(agent, USER_AGENT)

    def test_request_id_headers_do_not_displace_the_agent(self):
        client = self._client()
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["agent"] = request.get_header("User-agent")
            seen["request_id"] = request.get_header("X-request-id")
            return _FakeResponse()

        with unittest.mock.patch("oathcast.application.urlopen", fake_urlopen):
            client.request_with_id(self._question(), request_id="req-1")
        self.assertEqual(seen["agent"], USER_AGENT)
        self.assertEqual(seen["request_id"], "req-1")

    def test_repeated_requests_do_not_accumulate_request_ids(self):
        """Guards the aliasing case `test_the_callers_dict_is_not_mutated` describes."""

        client = self._client()
        seen = []

        def fake_urlopen(request, timeout=None):
            seen.append(request.get_header("X-request-id"))
            return _FakeResponse()

        with unittest.mock.patch("oathcast.application.urlopen", fake_urlopen):
            client.request_with_id(self._question(), request_id="req-1")
            client.request_with_id(self._question(), request_id=None)
        self.assertEqual(seen, ["req-1", None])


if __name__ == "__main__":
    unittest.main()
