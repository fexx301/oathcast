"""Read-only parsing for Telegraph's epoch-keyed Explorer leaderboard.

The current Explorer endpoint returns one snapshot containing every Intent::

    {"epoch": 178, "intents": {"WEATHER_FORECAST": [...]}}

Intent selection therefore happens locally by exact mapping key.  No query
parameter, echoed filter, or negative control is trusted.  The parser also
rejects the obsolete ``entries``/``avg_score``/``position`` shape instead of
quietly interpreting a stale capture as the live contract.

Ranks in the live response can have gaps, so ``Leaderboard.population`` is only
the number of entries returned for an Intent; it is not necessarily the rank's
denominator.  The target remains the highest score among active Miners.

These are other Miners' Telegraph scores. They are not comparable to the local
renderer proxy in ``reference_evaluator`` (an overlap/length stand-in rather
than the cosine + BM25 + length behavior described in pre-launch guidance,
which has not been verified as the current Canonical Script).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .discovery import WEATHER_INTENTS
from .protocol import USER_AGENT, outbound_headers


EXPLORER_ORIGIN = "https://explorer.telegraphprotocol.com"
LEADERBOARD_PATH = "/api/leaderboard/miners"
ACTIVE_STATUS = "active"

# Fields that identify the endpoint's obsolete per-Intent response.  Mixing
# either generation is ambiguous, so the parser fails closed instead of trying
# to guess which values should win.
LEGACY_ROOT_FIELDS = frozenset({"entries", "epoch_start", "epoch_end", "intent_id"})
LEGACY_ENTRY_FIELDS = frozenset(
    {
        "avg_score",
        "position",
        "best_rank",
        "epochs_participated",
        "total_requests_served",
    }
)

MAX_RESPONSE_BYTES = 2 * 1024 * 1024

Fetcher = Callable[[str], Any]


class LeaderboardError(RuntimeError):
    """Raised when a leaderboard read or response cannot be trusted."""


@dataclass(frozen=True)
class LeaderboardEntry:
    """One Miner's standing in a single Intent snapshot."""

    slug: str
    activation_status: str
    score: float
    rank: int

    @property
    def is_active(self) -> bool:
        return self.activation_status == ACTIVE_STATUS

    def describe(self) -> str:
        """Render the server rank together with its activation status."""

        return (
            f"{self.slug} {self.score:.4f} "
            f"(rank {self.rank}, {self.activation_status})"
        )


@dataclass(frozen=True)
class Leaderboard:
    """One Intent's entries selected from an epoch-wide response."""

    entries: tuple[LeaderboardEntry, ...]
    epoch: int
    intent: str

    @property
    def population(self) -> int:
        """Number of returned entries, not necessarily a rank denominator."""

        return len(self.entries)

    @property
    def active_entries(self) -> tuple[LeaderboardEntry, ...]:
        return tuple(entry for entry in self.entries if entry.is_active)

    def leader(self) -> LeaderboardEntry | None:
        """Highest-scoring active Miner, independent of inactive rank holders."""

        actives = self.active_entries
        if not actives:
            return None
        return max(actives, key=lambda entry: (entry.score, -entry.rank))

    def target_score(self) -> float | None:
        """The score to beat: the best active Miner's score."""

        leader = self.leader()
        return None if leader is None else leader.score


def leaderboard_url(intent: str | None = None, limit: int | None = None) -> str:
    """Return the epoch-wide endpoint and refuse obsolete query filtering.

    The optional arguments remain only as a safety rail for older callers: the
    current endpoint returns every Intent in one response, so accepting either
    would risk silently trusting an ignored server-side filter.
    """

    if intent is not None or limit is not None:
        raise ValueError(
            "the leaderboard endpoint returns all intents; fetch it without "
            "query parameters and select an exact key from 'intents' locally"
        )
    return f"{EXPLORER_ORIGIN}{LEADERBOARD_PATH}"


def _coerce_score(value: Any, *, intent: str, slug: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LeaderboardError(
            f"{intent}/{slug} score must be numeric, got {value!r}"
        )
    score = float(value)
    if not math.isfinite(score):
        raise LeaderboardError(
            f"{intent}/{slug} score must be finite, got {value!r}"
        )
    if not 0 <= score <= 1:
        raise LeaderboardError(
            f"{intent}/{slug} score must be in [0, 1], got {score!r}"
        )
    return score


def _coerce_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LeaderboardError(f"{field} must be an integer, got {value!r}")
    return value


def _parse_entries(intent: str, raw_entries: Any) -> tuple[LeaderboardEntry, ...]:
    if not isinstance(raw_entries, list):
        raise LeaderboardError(f"intents[{intent!r}] must be a list")

    entries: list[LeaderboardEntry] = []
    seen_slugs: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise LeaderboardError(f"each {intent} leaderboard entry must be an object")

        legacy = LEGACY_ENTRY_FIELDS.intersection(raw)
        if legacy:
            fields = ", ".join(sorted(legacy))
            raise LeaderboardError(
                f"{intent} entry mixes in obsolete leaderboard fields: {fields}"
            )

        slug = raw.get("miner_slug")
        if not isinstance(slug, str) or not slug or slug.strip() != slug:
            raise LeaderboardError(f"{intent} entry has an invalid miner_slug")
        if slug in seen_slugs:
            raise LeaderboardError(f"{intent} contains duplicate miner_slug {slug!r}")

        status = raw.get("activation_status")
        if not isinstance(status, str) or not status or status.strip() != status:
            raise LeaderboardError(f"{intent}/{slug} has an invalid activation_status")

        rank = _coerce_int(raw.get("rank"), f"{intent}/{slug} rank")
        if rank <= 0:
            raise LeaderboardError(f"{intent}/{slug} rank must be positive, got {rank}")
        entries.append(
            LeaderboardEntry(
                slug=slug,
                activation_status=status,
                score=_coerce_score(raw.get("score"), intent=intent, slug=slug),
                rank=rank,
            )
        )
        seen_slugs.add(slug)

    return tuple(sorted(entries, key=lambda entry: entry.rank))


def parse_leaderboards(
    payload: Any,
    intents: Iterable[str] | None = None,
) -> dict[str, Leaderboard]:
    """Parse an epoch-wide response, optionally selecting exact Intent keys.

    The complete response is validated before selection.  This makes malformed
    or mixed-generation payloads fail closed even when their bad entry belongs
    to an Intent the caller did not request.
    """

    if not isinstance(payload, dict):
        raise LeaderboardError("leaderboard payload must be a JSON object")

    legacy = LEGACY_ROOT_FIELDS.intersection(payload)
    if legacy:
        fields = ", ".join(sorted(legacy))
        if "intents" in payload:
            raise LeaderboardError(
                f"leaderboard payload ambiguously mixes current 'intents' with "
                f"obsolete fields: {fields}"
            )
        raise LeaderboardError(
            f"obsolete leaderboard payload shape detected ({fields}); expected "
            "the epoch/intents response"
        )

    epoch = _coerce_int(payload.get("epoch"), "epoch")
    if epoch < 0:
        raise LeaderboardError(f"epoch must be non-negative, got {epoch}")

    raw_intents = payload.get("intents")
    if not isinstance(raw_intents, dict):
        raise LeaderboardError("leaderboard payload must contain an 'intents' object")

    parsed: dict[str, Leaderboard] = {}
    for intent, raw_entries in raw_intents.items():
        if not isinstance(intent, str) or not intent or intent.strip() != intent:
            raise LeaderboardError("every leaderboard intent key must be a non-empty string")
        parsed[intent] = Leaderboard(
            entries=_parse_entries(intent, raw_entries),
            epoch=epoch,
            intent=intent,
        )

    if intents is None:
        return parsed

    names = tuple(intents)
    if any(not isinstance(name, str) or not name or name.strip() != name for name in names):
        raise LeaderboardError("requested intents must be non-empty strings")
    if len(set(names)) != len(names):
        raise LeaderboardError("requested intents must not contain duplicates")

    missing = [name for name in names if name not in parsed]
    if missing:
        available = ", ".join(sorted(parsed)) or "none"
        raise LeaderboardError(
            f"leaderboard response is missing requested intent(s) "
            f"{', '.join(repr(name) for name in missing)}; available: {available}"
        )
    return {name: parsed[name] for name in names}


def parse_leaderboard(payload: Any, *, intent: str | None = None) -> Leaderboard:
    """Parse one exact Intent from the epoch-wide Explorer response."""

    if intent is None:
        raise LeaderboardError(
            "intent is required because the leaderboard response contains multiple intents"
        )
    return parse_leaderboards(payload, (intent,))[intent]


def urllib_fetch(url: str) -> Any:
    """Read-only GET returning parsed JSON with structured failure translation."""

    request = Request(
        url,
        method="GET",
        headers=outbound_headers(),
    )
    try:
        with urlopen(request, timeout=25) as response:  # noqa: S310 - pinned origin
            encoded_body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        if error.code == 403:
            raise LeaderboardError(
                f"Explorer refused the request with HTTP 403 for {url}. The agent "
                f"string is most likely being filtered: the default "
                f"'Python-urllib/*' agent is blocked, which is why USER_AGENT is "
                f"set explicitly in this module. Verify with curl -A "
                f"'{USER_AGENT}' before assuming the endpoint moved, and do not "
                f"work around it by impersonating a browser."
            ) from error
        raise LeaderboardError(
            f"Explorer returned HTTP {error.code} for {url}: {error.reason}"
        ) from error
    except URLError as error:
        raise LeaderboardError(
            f"could not reach the Explorer at {url}: {error.reason}"
        ) from error
    except TimeoutError as error:
        raise LeaderboardError(
            f"timed out reading the Explorer response from {url} after 25s"
        ) from error

    try:
        if not isinstance(encoded_body, (bytes, bytearray)):
            raise LeaderboardError(
                f"Explorer returned a response body of type "
                f"{type(encoded_body).__name__}, not bytes, for {url}"
            )
        if len(encoded_body) > MAX_RESPONSE_BYTES:
            raise LeaderboardError(
                f"Explorer response exceeded the {MAX_RESPONSE_BYTES}-byte cap for {url}"
            )
        body = encoded_body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LeaderboardError(
            f"Explorer returned {len(encoded_body)} bytes that were not UTF-8 for {url}"
        ) from error

    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise LeaderboardError(
            f"Explorer returned {len(body)} bytes of non-JSON for {url}; a web-app "
            f"HTML fallback here means the path is not an API route"
        ) from error


def _read(fetch: Fetcher, url: str) -> Any:
    """Call a replaceable fetcher without leaking foreign exception types."""

    try:
        return fetch(url)
    except LeaderboardError:
        raise
    except Exception as error:  # noqa: BLE001 - public fetcher seam
        raise LeaderboardError(
            f"leaderboard read for {url} failed with "
            f"{type(error).__name__}: {error}"
        ) from error


def fetch_intent_leaderboard(
    intent: str,
    *,
    fetch: Fetcher = urllib_fetch,
) -> Leaderboard:
    """Read the epoch-wide response once and select one exact Intent locally."""

    payload = _read(fetch, leaderboard_url())
    return parse_leaderboard(payload, intent=intent)


def fetch_weather_leaderboards(
    intents: tuple[str, ...] | None = None,
    *,
    fetch: Fetcher = urllib_fetch,
) -> dict[str, Leaderboard]:
    """Read once and select the requested weather Intents by exact mapping key."""

    names = intents if intents is not None else tuple(sorted(WEATHER_INTENTS))
    payload = _read(fetch, leaderboard_url())
    return parse_leaderboards(payload, names)


def renderer_target(
    boards: dict[str, Leaderboard], declared: tuple[str, ...]
) -> float | None:
    """Highest active score across our declared Intents."""

    scores: list[float] = []
    for name, board in boards.items():
        if name not in declared:
            continue
        score = board.target_score()
        if score is not None:
            scores.append(score)
    return max(scores) if scores else None
