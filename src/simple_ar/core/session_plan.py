"""Explicit, bounded execution of a caller-owned capability sequence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from simple_ar.core.capabilities import ArtifactRef, CapabilityResult
from simple_ar.core.session import DecisionRecord, SessionController
from simple_ar.core.transitions import TransitionRequest


_CONTROLLER_OWNED_KWARGS = {
    "attempt_id",
    "context",
    "evidence_sufficient",
    "expected_delta",
    "failure_kind",
    "inputs",
    "hypothesis_supported",
    "next_capability",
    "profile",
    "progressed",
    "experiment_needed",
    "report_auditable",
    "trigger",
}


@dataclass(frozen=True, slots=True)
class SessionStep:
    """One explicit capability invocation in a session plan.

    The sequence owner supplies the order and handler arguments. The core
    supplies attempt lineage, transition validation, and bounded stopping.
    ``handler_kwargs`` is intentionally separate from controller arguments so
    a capability cannot accidentally override session state.
    """

    capability: str
    attempt_id: str
    inputs: tuple[ArtifactRef, ...] = ()
    trigger: str = "sequence"
    profile: str | None = None
    expected_delta: str = ""
    progressed: bool | None = None
    failure_kind: str | None = None
    evidence_sufficient: bool | None = None
    hypothesis_supported: bool | None = None
    experiment_needed: bool | None = None
    report_auditable: bool | None = None
    handler_kwargs: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.capability.strip():
            raise ValueError("SessionStep.capability cannot be empty.")
        if not self.attempt_id.strip():
            raise ValueError("SessionStep.attempt_id cannot be empty.")
        if not self.trigger.strip():
            raise ValueError("SessionStep.trigger cannot be empty.")
        reserved = _CONTROLLER_OWNED_KWARGS.intersection(self.handler_kwargs)
        if reserved:
            names = ", ".join(sorted(reserved))
            raise ValueError(f"SessionStep handler_kwargs are controller-owned: {names}")
        object.__setattr__(self, "inputs", tuple(self.inputs))
        object.__setattr__(self, "handler_kwargs", dict(self.handler_kwargs))


def run_session_plan(
    controller: SessionController,
    steps: Iterable[SessionStep],
) -> tuple[tuple[CapabilityResult, DecisionRecord], ...]:
    """Run one explicit sequence until completion or the first non-accept.

    All route and handler names are checked before the first attempt starts.
    The function never retries, changes step order, or invents a target. A
    caller can inspect the returned decision and explicitly invoke another
    attempt when a repair or backtrack is appropriate.
    """

    plan = tuple(steps)
    _preflight_plan(controller, plan)
    outcomes: list[tuple[CapabilityResult, DecisionRecord]] = []
    for index, step in enumerate(plan):
        next_capability = (
            plan[index + 1].capability if index + 1 < len(plan) else None
        )
        result, decision = controller.execute(
            step.capability,
            attempt_id=step.attempt_id,
            trigger=step.trigger,
            profile=step.profile,
            inputs=step.inputs,
            expected_delta=step.expected_delta,
            progressed=step.progressed,
            failure_kind=step.failure_kind,
            evidence_sufficient=step.evidence_sufficient,
            hypothesis_supported=step.hypothesis_supported,
            experiment_needed=step.experiment_needed,
            report_auditable=step.report_auditable,
            next_capability=next_capability,
            **dict(step.handler_kwargs),
        )
        outcomes.append((result, decision))
        if decision.action != "accept":
            break
    return tuple(outcomes)


def _preflight_plan(
    controller: SessionController,
    plan: tuple[SessionStep, ...],
) -> None:
    if not plan:
        return

    attempt_ids = [step.attempt_id.strip() for step in plan]
    if len(set(attempt_ids)) != len(attempt_ids):
        raise ValueError("Session plan attempt IDs must be unique.")
    existing_ids = {attempt.attempt_id for attempt in controller.list_attempts()}
    conflicts = sorted(set(attempt_ids) & existing_ids)
    if conflicts:
        raise ValueError(
            "Session plan attempt IDs already exist: " + ", ".join(conflicts)
        )

    current_attempt_id = controller.manifest.current_attempt
    if current_attempt_id:
        current_attempt = next(
            (
                item
                for item in controller.list_attempts()
                if item.attempt_id == current_attempt_id
            ),
            None,
        )
        current_capability = (
            (current_attempt.capability or "").strip()
            if current_attempt is not None
            else ""
        )
        if current_capability:
            decision = controller.plan_transition(
                TransitionRequest(
                    source=current_capability,
                    result_status="completed",
                    target=plan[0].capability,
                )
            )
            if decision.action == "block":
                raise ValueError(decision.reason)

    for index, step in enumerate(plan):
        controller.registry.resolve(step.capability)
        next_capability = (
            plan[index + 1].capability if index + 1 < len(plan) else None
        )
        decision = controller.plan_transition(
            TransitionRequest(
                source=step.capability,
                result_status="completed",
                target=next_capability,
                expected_delta=step.expected_delta,
            )
        )
        if decision.action == "block":
            raise ValueError(decision.reason)


__all__ = ["SessionStep", "run_session_plan"]
