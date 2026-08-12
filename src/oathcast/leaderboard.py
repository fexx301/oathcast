"""Read-only per-Intent leaderboard parsing for Telegraph's Explorer API.

This module exists because the numbers it produces were originally gathered by
hand and could not be reproduced. It is read-only: no payment, no signature, no
registration side effect.

Four properties of the live API are enforced here rather than left as prose,
because each one produces a *plausible wrong answer* rather than an error:

1. `?intent=<INTENT>` is the real filter. `?intent_type=` and `?epoch=` are
   silently ignored and return the unfiltered board — which looks like a
   successful narrow query. `assert_filter_supported` therefore requires a
   negative control (a nonsense Intent returning zero entries) before any score
   from a filtered response may be trusted.
2. `avg_score` is genuinely per-Intent, but `total_requests_served` is NOT: it
   is identical across every per-Intent view of the same Miner, because it is
   that Miner's total across all Intents. Per-Intent entries drop it rather than
   report a number that invites a per-Intent reading.
3. `position` includes non-active Miners. A `superseded` Miner can hold position
   1, so a position is never reported without its activation status, and
   "leading" is computed over active Miners only.
4. A rank without its denominator is not citable. `Leaderboard.population`
   carries the entry count so "4 of 41" can never be recorded as a bare "4".

None of these values are OathCast's own score, and none are comparable to the
local renderer proxy in `reference_evaluator` — that proxy is an overlap/length
stand-in, not Telegraph's cosine + BM25 + length composite. See
`docs/renderer-experiment.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .discovery import WEATHER_INTENTS


EXPLORER_ORIGIN = "https://explorer.telegraphprotocol.com"
LEADERBOARD_PATH = "/api/leaderboard/miners"

#: Intent name used as the negative control. It must never be a real Intent.
CONTROL_INTENT = "NOT_A_REAL_INTENT"

#: Parameters the live API accepts silently without applying them. Passing one
#: of these and reading the result as filtered is the trap this module prevents.
IGNORED_PARAMS = frozenset({"intent_type", "epoch"})

ACTIVE_STATUS = "active"

#: The Explorer rejects the default `Python-urllib/*` agent with HTTP 403, so a
#: descriptive one is required. It names the project and the read-only intent
#: rather than impersonating a browser — an operator seeing this in a log should
#: be able to tell who is calling and that nothing is being purchased.
USER_AGENT = "OathCast-Leaderboard-Reader/1.0 (read-only; +https://github.com/fexx301/oathcast)"

Fetcher = Callable[[str], Any]


class LeaderboardError(RuntimeError):
    """Raised when the leaderboard response cannot be trusted as filtered."""


@dataclass(frozen=True)
class LeaderboardEntry:
    """One Miner's standing. `requests_served` is None for per-Intent reads."""

    slug: str
    activation_status: str
    avg_score: float
    position: int
    epochs_participated: int
    best_rank: int | None = None
    requests_served: int | None = None

    @property
    def is_active(self) -> bool:
        return self.activation_status == ACTIVE_STATUS

    def describe(self) -> str:
        """Render for human copy, never a bare position number."""

        return (
            f"{self.slug} {self.avg_score:.4f} "
            f"(position {self.position}, {self.activation_status})"
        )


@dataclass(frozen=True)
class Leaderboard:
    """A leaderboard read. `intent` is None for the unfiltered board."""

    entries: tuple[LeaderboardEntry, ...]
    epoch_start: int | None
    epoch_end: int | None
    intent: str | None
    filter_verified: bool

    @property
    def population(self) -> int:
        """Entry count — the denominator any position must be quoted against."""

        return len(self.entries)

    @property
    def active_entries(self) -> tuple[LeaderboardEntry, ...]:
        return tuple(entry for entry in self.entries if entry.is_active)

    def leader(self) -> LeaderboardEntry | None:
        """Best *active* Miner. Position 1 may be superseded, so it is not used."""

        actives = self.active_entries
        if not actives:
            return None
        return max(actives, key=lambda entry: entry.avg_score)

    def target_score(self) -> float | None:
        """The score to beat: the best active Miner's average."""

        leader = self.leader()
        return None if leader is None else leader.avg_score

    def single_epoch(self) -> bool:
        """True when every Miner has one epoch — thin evidence, worth flagging."""

        return bool(self.entries) and all(
            entry.epochs_participated <= 1 for entry in self.entries
        )


def leaderboard_url(intent: str | None = None, limit: int | None = None) -> str:
    """Build a leaderboard URL, refusing parameters the API silently ignores."""

    params: dict[str, str] = {}
    if intent is not None:
        if not intent.strip():
            raise ValueError("intent must be a non-empty string or None")
        params["intent"] = intent
    if limit is not None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        params["limit"] = str(limit)
    query = f"?{urlencode(params)}" if params else ""
    return f"{EXPLORER_ORIGIN}{LEADERBOARD_PATH}{query}"


def _coerce_float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LeaderboardError(f"avg_score must be numeric, got {value!r}")
    return float(value)


def _coerce_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LeaderboardError(f"{field} must be an integer, got {value!r}")
    return value


def parse_leaderboard(
    payload: Any,
    *,
    intent: str | None = None,
    filter_verified: bool = False,
) -> Leaderboard:
    """Parse an Explorer leaderboard payload.

    Drops `total_requests_served` on per-Intent reads: the live API reports the
    Miner's cross-Intent total there, so retaining it would invite a per-Intent
    reading that contradicts the question-feed census.
    """

    if not isinstance(payload, dict):
        raise LeaderboardError("leaderboard payload must be a JSON object")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise LeaderboardError("leaderboard payload must contain an 'entries' list")

    echoed = payload.get("intent_id")
    if intent is not None and echoed is not None and echoed != intent:
        raise LeaderboardError(
            f"requested intent {intent!r} but payload echoed {echoed!r}"
        )

    entries: list[LeaderboardEntry] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise LeaderboardError("each leaderboard entry must be an object")
        slug = raw.get("miner_slug")
        if not isinstance(slug, str) or not slug:
            raise LeaderboardError("entry is missing miner_slug")
        status = raw.get("activation_status")
        if not isinstance(status, str) or not status:
            raise LeaderboardError(f"{slug} is missing activation_status")
        best_rank = raw.get("best_rank")
        entries.append(
            LeaderboardEntry(
                slug=slug,
                activation_status=status,
                avg_score=_coerce_float(raw.get("avg_score")),
                position=_coerce_int(raw.get("position"), "position"),
                epochs_participated=_coerce_int(
                    raw.get("epochs_participated", 0), "epochs_participated"
                ),
                best_rank=best_rank if isinstance(best_rank, int) else None,
                requests_served=(
                    None
                    if intent is not None
                    else raw.get("total_requests_served")
                    if isinstance(raw.get("total_requests_served"), int)
                    else None
                ),
            )
        )

    return Leaderboard(
        entries=tuple(entries),
        epoch_start=payload.get("epoch_start"),
        epoch_end=payload.get("epoch_end"),
        intent=intent,
        filter_verified=filter_verified,
    )


def urllib_fetch(url: str) -> Any:
    """Minimal read-only GET returning parsed JSON."""

    request = Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    with urlopen(request, timeout=25) as response:  # noqa: S310 - pinned origin
        return json.loads(response.read().decode("utf-8"))


def assert_filter_supported(fetch: Fetcher = urllib_fetch) -> None:
    """Verify `?intent=` filters before any filtered score is trusted.

    A silently-ignored filter returns the full board, so a filtered read that is
    never controlled cannot be distinguished from an unfiltered one. This sends
    a nonsense Intent and requires zero entries back.
    """

    control = parse_leaderboard(
        fetch(leaderboard_url(CONTROL_INTENT)), intent=CONTROL_INTENT
    )
    if control.population != 0:
        raise LeaderboardError(
            f"negative control returned {control.population} entries for "
            f"{CONTROL_INTENT!r}; the intent filter is not being applied, so no "
            "per-Intent score from this endpoint can be trusted"
        )


def fetch_intent_leaderboard(
    intent: str,
    *,
    fetch: Fetcher = urllib_fetch,
    verify_filter: bool = True,
) -> Leaderboard:
    """Read one Intent's leaderboard, running the negative control first."""

    if verify_filter:
        assert_filter_supported(fetch)
    return parse_leaderboard(
        fetch(leaderboard_url(intent)), intent=intent, filter_verified=verify_filter
    )


def fetch_weather_leaderboards(
    intents: tuple[str, ...] | None = None,
    *,
    fetch: Fetcher = urllib_fetch,
) -> dict[str, Leaderboard]:
    """Read every weather Intent, running the negative control exactly once."""

    assert_filter_supported(fetch)
    names = intents if intents is not None else tuple(sorted(WEATHER_INTENTS))
    return {
        name: fetch_intent_leaderboard(name, fetch=fetch, verify_filter=False)
        for name in names
    }


def renderer_target(boards: dict[str, Leaderboard], declared: tuple[str, ...]) -> float | None:
    """Highest active score across our declared Intents.

    Clearing the hardest declared Intent clears the others, so the maximum is
    the target. A mean would sit between the real bars and be wrong in both
    directions at once, which is how a flat cross-Intent average misled us.
    """

    scores = [
        board.target_score()
        for name, board in boards.items()
        if name in declared and board.target_score() is not None
    ]
    return max(scores) if scores else None
