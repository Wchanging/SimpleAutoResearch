from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from simple_ar.core.capabilities import (
    ArtifactRef,
    ArtifactStore,
    CapabilityRegistry,
    CapabilityResult,
)


SessionStatus = Literal["created", "running", "completed", "partial", "blocked", "failed"]
DecisionAction = Literal["accept", "revise", "repair", "block"]

_SESSION_STATUSES = {"created", "running", "completed", "partial", "blocked", "failed"}
_DECISION_ACTIONS = {"accept", "revise", "repair", "block"}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class BudgetState:
    """Small bounded budget for one research session."""

    max_attempts: int = 3
    max_no_progress: int = 2
    attempts: int = 0
    no_progress: int = 0

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.max_no_progress < 1:
            raise ValueError("Session budgets must be positive.")
        if self.attempts < 0 or self.no_progress < 0:
            raise ValueError("Session budget counters cannot be negative.")

    def record(self, progressed: bool) -> None:
        self.attempts += 1
        self.no_progress = 0 if progressed else self.no_progress + 1

    def exhausted(self) -> bool:
        return self.attempts >= self.max_attempts or self.no_progress >= self.max_no_progress

    def to_dict(self) -> dict[str, int]:
        return {
            "max_attempts": self.max_attempts,
            "max_no_progress": self.max_no_progress,
            "attempts": self.attempts,
            "no_progress": self.no_progress,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BudgetState":
        return cls(
            max_attempts=int(data.get("max_attempts", 3)),
            max_no_progress=int(data.get("max_no_progress", 2)),
            attempts=int(data.get("attempts", 0)),
            no_progress=int(data.get("no_progress", 0)),
        )


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """Auditable decision for one capability attempt."""

    capability: str
    attempt_id: str
    action: DecisionAction
    result_status: str
    reason: str
    progressed: bool
    expected_delta: str = ""
    input_paths: tuple[str, ...] = ()
    output_paths: tuple[str, ...] = ()
    created_at: str = field(default_factory=_utcnow_iso)

    def __post_init__(self) -> None:
        if not self.capability.strip() or not self.attempt_id.strip():
            raise ValueError("DecisionRecord requires capability and attempt_id.")
        if self.action not in _DECISION_ACTIONS:
            raise ValueError(f"Unsupported decision action: {self.action}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "attempt_id": self.attempt_id,
            "action": self.action,
            "result_status": self.result_status,
            "reason": self.reason,
            "progressed": self.progressed,
            "expected_delta": self.expected_delta,
            "input_paths": list(self.input_paths),
            "output_paths": list(self.output_paths),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DecisionRecord":
        return cls(
            capability=str(data["capability"]),
            attempt_id=str(data["attempt_id"]),
            action=str(data.get("action", "block")),  # type: ignore[arg-type]
            result_status=str(data.get("result_status", "failed")),
            reason=str(data.get("reason", "")),
            progressed=bool(data.get("progressed", False)),
            expected_delta=str(data.get("expected_delta", "")),
            input_paths=tuple(str(item) for item in data.get("input_paths", [])),
            output_paths=tuple(str(item) for item in data.get("output_paths", [])),
            created_at=str(data.get("created_at", _utcnow_iso())),
        )


@dataclass
class SessionManifest:
    """Persisted state for a bounded, capability-oriented research session."""

    session_id: str
    topic: str
    profile: str | None = None
    status: SessionStatus = "created"
    current_attempt: str | None = None
    budget: BudgetState = field(default_factory=BudgetState)
    decisions: list[DecisionRecord] = field(default_factory=list)
    created_at: str = field(default_factory=_utcnow_iso)
    updated_at: str = field(default_factory=_utcnow_iso)

    def __post_init__(self) -> None:
        if not self.session_id.strip() or not self.topic.strip():
            raise ValueError("SessionManifest requires session_id and topic.")
        if self.status not in _SESSION_STATUSES:
            raise ValueError(f"Unsupported session status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "session_manifest.v1",
            "session_id": self.session_id,
            "topic": self.topic,
            "profile": self.profile,
            "status": self.status,
            "current_attempt": self.current_attempt,
            "budget": self.budget.to_dict(),
            "decisions": [decision.to_dict() for decision in self.decisions],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionManifest":
        return cls(
            session_id=str(data["session_id"]),
            topic=str(data["topic"]),
            profile=str(data["profile"]) if data.get("profile") else None,
            status=str(data.get("status", "created")),  # type: ignore[arg-type]
            current_attempt=(
                str(data["current_attempt"]) if data.get("current_attempt") else None
            ),
            budget=BudgetState.from_dict(data.get("budget", {})),
            decisions=[
                DecisionRecord.from_dict(item)
                for item in data.get("decisions", [])
                if isinstance(item, dict)
            ],
            created_at=str(data.get("created_at", _utcnow_iso())),
            updated_at=str(data.get("updated_at", _utcnow_iso())),
        )


class SessionController:
    """Run bounded capability attempts without knowing stage-specific logic."""

    def __init__(self, store: ArtifactStore, registry: CapabilityRegistry, manifest: SessionManifest):
        self.store = store
        self.registry = registry
        self.manifest = manifest

    @classmethod
    def create(
        cls,
        root: str | Path,
        *,
        session_id: str,
        topic: str,
        registry: CapabilityRegistry,
        profile: str | None = None,
        budget: BudgetState | None = None,
    ) -> "SessionController":
        store = ArtifactStore(root)
        if store.exists("session_manifest.json"):
            raise FileExistsError("Session manifest already exists.")
        manifest = SessionManifest(
            session_id=session_id,
            topic=topic,
            profile=profile,
            budget=budget or BudgetState(),
        )
        controller = cls(store, registry, manifest)
        controller.save()
        return controller

    @classmethod
    def load(cls, root: str | Path, *, registry: CapabilityRegistry) -> "SessionController":
        store = ArtifactStore(root)
        payload = store.read_json("session_manifest.json")
        if not isinstance(payload, dict):
            raise ValueError("Session manifest must be a JSON object.")
        return cls(store, registry, SessionManifest.from_dict(payload))

    def save(self) -> ArtifactRef:
        self.manifest.updated_at = _utcnow_iso()
        return self.store.write_json(
            "session_manifest.json",
            self.manifest.to_dict(),
            kind="session",
            schema="session_manifest.v1",
            producer="session_controller",
        )

    def execute(
        self,
        capability: str,
        *,
        attempt_id: str,
        trigger: str = "initial",
        profile: str | None = None,
        inputs: Iterable[ArtifactRef] = (),
        expected_delta: str = "",
        progressed: bool | None = None,
        **kwargs: Any,
    ) -> tuple[CapabilityResult, DecisionRecord]:
        """Execute exactly one attempt and persist its decision.

        The controller never retries implicitly. A caller must explicitly ask
        for another attempt, so a failed capability cannot create an unseen
        loop or overwrite its parent attempt.
        """
        self._ensure_can_execute()
        input_refs = tuple(inputs)
        parent_attempt = self.manifest.current_attempt
        attempt_store, attempt = self.store.new_attempt(
            attempt_id,
            parent_attempt=parent_attempt,
            trigger=trigger,
            profile=profile or self.manifest.profile,
            inputs=input_refs,
        )
        self.manifest.status = "running"
        self.manifest.current_attempt = attempt_id

        try:
            result = self.registry.run(capability, **kwargs)
        except Exception as exc:
            result = CapabilityResult(
                status="failed",
                diagnostics=(f"{type(exc).__name__}: {exc}",),
                provenance={"capability": capability},
            )

        has_progress = (
            progressed
            if progressed is not None
            else result.status in {"completed", "partial"}
        )
        self.manifest.budget.record(has_progress)
        action = _action_for_status(result.status)
        reason = result.diagnostics[0] if result.diagnostics else f"Capability status: {result.status}."
        if action != "accept" and self.manifest.budget.exhausted():
            action = "block"
            reason = f"{reason} Session budget exhausted."

        status = "completed" if action == "accept" else "blocked" if action == "block" else "running"
        self.manifest.status = status  # type: ignore[assignment]
        decision = DecisionRecord(
            capability=capability,
            attempt_id=attempt_id,
            action=action,
            result_status=result.status,
            reason=reason,
            progressed=has_progress,
            expected_delta=expected_delta,
            input_paths=tuple(item.path for item in input_refs),
            output_paths=tuple(item.path for item in result.artifacts),
        )
        self.manifest.decisions.append(decision)
        attempt_status = "blocked" if action == "block" else "failed" if result.status == "failed" else "completed"
        attempt_store.write_attempt_manifest(
            replace(
                attempt,
                status=attempt_status,  # type: ignore[arg-type]
                outputs=result.artifacts,
                updated_at=_utcnow_iso(),
            )
        )
        self.save()
        return result, decision

    def _ensure_can_execute(self) -> None:
        if self.manifest.status in {"completed", "blocked"}:
            raise RuntimeError(f"Session is {self.manifest.status}; no further attempt is allowed.")
        if self.manifest.budget.exhausted():
            self.manifest.status = "blocked"
            self.save()
            raise RuntimeError("Session budget is exhausted.")


def _action_for_status(status: str) -> DecisionAction:
    if status == "completed":
        return "accept"
    if status == "partial":
        return "revise"
    if status == "failed":
        return "repair"
    return "block"
