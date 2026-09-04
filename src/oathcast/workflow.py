"""Thin Application workflow that connects routing to durable evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from oathcast.application import ApplicationDecision, CrossMinerRouter
from oathcast.cases import CaseStateError, SqliteCaseStore
from oathcast.forecast import ForecastQuestion
from oathcast.ground_truth import (
    GroundTruthResult,
    ObservationSource,
    resolve_precipitation,
)


UTC = timezone.utc


class ApplicationWorkflow:
    """Run the live decision path and the later resolution path separately."""

    def __init__(
        self,
        router: CrossMinerRouter,
        case_store: SqliteCaseStore,
        observation_source: ObservationSource,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.router = router
        self.case_store = case_store
        self.observation_source = observation_source
        self.clock = clock or (lambda: datetime.now(tz=UTC))

    def decide(
        self,
        question: ForecastQuestion,
        *,
        disable_owned: bool = False,
        application_request_id: str | None = None,
        decision_threshold: float = 0.5,
    ) -> ApplicationDecision:
        """Create and freeze one case using only current Miner responses."""

        self.case_store.create(question, created_at=self.clock())
        existing = self.case_store.get(question.event_id)
        if existing is not None and existing.get("decision") is not None:
            raise CaseStateError("case decision is already sealed; use its stored evidence")
        decision = self.router.decide(
            question,
            disable_owned=disable_owned,
            application_request_id=application_request_id,
            decision_threshold=decision_threshold,
        )
        self.case_store.seal_decision(
            question.event_id,
            decision,
            sealed_at=decision.decided_at,
        )
        return decision

    def resolve(self, question: ForecastQuestion) -> GroundTruthResult:
        """Resolve a frozen case later through the configured observation source."""

        observation = self.observation_source.observe(question)
        result = resolve_precipitation(
            question,
            observation,
            resolved_at=self.clock(),
        )
        self.case_store.resolve(
            question.event_id,
            result,
            observation=observation,
        )
        return result
