from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from simple_ar.core.capabilities import (
    ArtifactRef,
    ArtifactStore,
    AttemptManifest,
    CapabilityRegistry,
    CapabilityContext,
    CapabilityResult,
)
from simple_ar.core.transitions import (
    TransitionDecision,
    TransitionPolicy,
    TransitionRecipe,
    TransitionRequest,
)
from simple_ar.core.profiles import resolve_lifecycle_profile


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


def _default_session_budget(profile: str | None) -> BudgetState:
    """Give a named profile room for its path plus two explicit recoveries."""

    lifecycle = resolve_lifecycle_profile(profile)
    if lifecycle is None:
        return BudgetState()
    return BudgetState(max_attempts=max(3, len(lifecycle.capabilities) + 2))


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
    failure_kind: str = "none"
    next_capability: str | None = None
    budget_attempts: int = 0
    budget_no_progress: int = 0
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
            "failure_kind": self.failure_kind,
            "next_capability": self.next_capability,
            "budget_attempts": self.budget_attempts,
            "budget_no_progress": self.budget_no_progress,
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
            failure_kind=str(data.get("failure_kind", "none")),
            next_capability=(
                str(data["next_capability"])
                if data.get("next_capability")
                else None
            ),
            budget_attempts=int(data.get("budget_attempts", 0)),
            budget_no_progress=int(data.get("budget_no_progress", 0)),
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
    transition_recipe: str = "research-v1"

    def __post_init__(self) -> None:
        if not self.session_id.strip() or not self.topic.strip():
            raise ValueError("SessionManifest requires session_id and topic.")
        if not self.transition_recipe.strip():
            raise ValueError("SessionManifest.transition_recipe cannot be empty.")
        if self.status not in _SESSION_STATUSES:
            raise ValueError(f"Unsupported session status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "session_manifest.v1",
            "session_id": self.session_id,
            "topic": self.topic,
            "profile": self.profile,
            "transition_recipe": self.transition_recipe,
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
            transition_recipe=str(data.get("transition_recipe", "research-v1")),
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


def _reconcile_declared_outputs(
    result: CapabilityResult,
    store: ArtifactStore,
) -> CapabilityResult:
    """Keep available output references honest without scanning an attempt."""

    missing = tuple(
        artifact.path
        for artifact in result.artifacts
        if artifact.status == "available" and not store.exists(artifact)
    )
    if not missing:
        return result
    diagnostics = (
        *result.diagnostics,
        *(f"Declared output artifact is missing: {path}." for path in missing),
    )
    status = "partial" if result.status == "completed" else result.status
    missing_set = set(missing)
    artifacts = tuple(
        replace(artifact, status="missing") if artifact.path in missing_set else artifact
        for artifact in result.artifacts
    )
    return replace(
        result,
        status=status,  # type: ignore[arg-type]
        artifacts=artifacts,
        diagnostics=diagnostics,
    )


class SessionController:
    """Run bounded capability attempts without knowing stage-specific logic."""

    def __init__(
        self,
        store: ArtifactStore,
        registry: CapabilityRegistry,
        manifest: SessionManifest,
        *,
        recipe: TransitionRecipe | None = None,
    ):
        self.store = store
        self.registry = registry
        self.manifest = manifest
        self.transition_policy = TransitionPolicy(recipe)

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
        recipe: TransitionRecipe | None = None,
    ) -> "SessionController":
        store = ArtifactStore(root)
        if store.exists("session_manifest.json"):
            raise FileExistsError("Session manifest already exists.")
        selected_recipe = recipe or TransitionRecipe.default()
        session_budget = (
            budget if budget is not None else _default_session_budget(profile)
        )
        manifest = SessionManifest(
            session_id=session_id,
            topic=topic,
            profile=profile,
            transition_recipe=selected_recipe.name,
            budget=session_budget,
        )
        controller = cls(store, registry, manifest, recipe=selected_recipe)
        controller.save()
        return controller

    @classmethod
    def load(
        cls,
        root: str | Path,
        *,
        registry: CapabilityRegistry,
        recipe: TransitionRecipe | None = None,
    ) -> "SessionController":
        store = ArtifactStore(root)
        payload = store.read_json("session_manifest.json")
        if not isinstance(payload, dict):
            raise ValueError("Session manifest must be a JSON object.")
        manifest = SessionManifest.from_dict(payload)
        selected_recipe = recipe or TransitionRecipe.default()
        if manifest.transition_recipe != selected_recipe.name:
            raise ValueError(
                f"Session uses transition recipe {manifest.transition_recipe!r}; "
                f"load it with the matching recipe {selected_recipe.name!r}."
            )
        return cls(store, registry, manifest, recipe=selected_recipe)

    def plan_transition(
        self,
        request: TransitionRequest,
    ) -> TransitionDecision:
        """Validate one proposed transition within the optional lifecycle scope."""

        decision = self.transition_policy.decide(request)
        lifecycle = resolve_lifecycle_profile(self.manifest.profile)
        if lifecycle is None:
            return decision

        source = request.source.strip()
        if not lifecycle.allows(source):
            return TransitionDecision(
                action="block",
                failure_kind=decision.failure_kind,
                target=None,
                reason=(
                    f"Capability {source} is outside lifecycle profile "
                    f"{lifecycle.name}."
                ),
                expected_delta=decision.expected_delta,
            )
        if decision.target is not None and not lifecycle.allows(decision.target):
            return TransitionDecision(
                action="block",
                failure_kind=decision.failure_kind,
                target=None,
                reason=(
                    f"Transition {source} -> {decision.target} is outside "
                    f"lifecycle profile {lifecycle.name}."
                ),
                expected_delta=decision.expected_delta,
            )
        return decision

    def allowed_targets(self, source: str) -> tuple[str, ...]:
        """Return recipe targets visible within this session's profile."""

        targets = self.transition_policy.recipe.allowed_targets(source)
        lifecycle = resolve_lifecycle_profile(self.manifest.profile)
        if lifecycle is None:
            return targets
        if not lifecycle.allows(source):
            return ()
        return tuple(target for target in targets if lifecycle.allows(target))

    def status_snapshot(self, source: str | None = None) -> dict[str, Any]:
        """Return a compact, read-only view for status UIs and handoffs.

        The snapshot summarizes persisted attempt manifests and the current
        decision history. It deliberately excludes artifact contents and does
        not select a "best" result, because comparison rules belong to the
        capability domain rather than the session controller.
        """
        attempts = self.list_attempts()
        snapshot: dict[str, Any] = {
            "schema_version": "session_status.v1",
            "session_id": self.manifest.session_id,
            "topic": self.manifest.topic,
            "profile": self.manifest.profile,
            "status": self.manifest.status,
            "current_attempt": self.manifest.current_attempt,
            "attempt_count": len(attempts),
            "running_attempts": sum(item.status == "running" for item in attempts),
            "active_attempts": [
                {
                    "attempt_id": item.attempt_id,
                    "capability": item.capability,
                    "updated_at": item.updated_at,
                }
                for item in attempts
                if item.status == "running"
            ],
            "completed_attempts": sum(item.status == "completed" for item in attempts),
            "failed_attempts": sum(item.status == "failed" for item in attempts),
            "blocked_attempts": sum(item.status == "blocked" for item in attempts),
            "budget": self.manifest.budget.to_dict(),
            "last_decision": (
                self.manifest.decisions[-1].to_dict()
                if self.manifest.decisions
                else None
            ),
        }
        if source is not None:
            snapshot["source"] = source
            snapshot["allowed_targets"] = list(self.allowed_targets(source))
        return snapshot

    def list_attempts(self) -> tuple[AttemptManifest, ...]:
        """Return persisted attempt manifests in creation-independent order."""
        attempts_root = self.store.root / "attempts"
        if not attempts_root.is_dir():
            return ()
        manifests: list[AttemptManifest] = []
        for child in sorted(attempts_root.iterdir(), key=lambda item: item.name):
            manifest_path = child / "attempt_manifest.json"
            if manifest_path.is_file():
                manifests.append(self.store.read_attempt_manifest(manifest_path))
        return tuple(manifests)

    def attempt_lineage(self, attempt_id: str | None = None) -> tuple[AttemptManifest, ...]:
        """Return one persisted attempt lineage from root to the selected node.

        This is an inspection helper only. It does not choose a best result,
        merge artifacts, or schedule another capability.
        """

        selected_id = (attempt_id or self.manifest.current_attempt or "").strip()
        if not selected_id:
            raise ValueError("An attempt id is required when no current attempt exists.")
        attempts = {item.attempt_id: item for item in self.list_attempts()}
        current = attempts.get(selected_id)
        if current is None:
            raise KeyError(f"Unknown attempt: {selected_id}")

        lineage: list[AttemptManifest] = []
        visited: set[str] = set()
        while True:
            if current.attempt_id in visited:
                raise ValueError(
                    f"Attempt lineage contains a cycle at {current.attempt_id}."
                )
            visited.add(current.attempt_id)
            lineage.append(current)
            parent_id = (current.parent_attempt or "").strip()
            if not parent_id:
                return tuple(reversed(lineage))
            parent = attempts.get(parent_id)
            if parent is None:
                raise ValueError(
                    f"Attempt {current.attempt_id} references missing parent {parent_id}."
                )
            current = parent

    def attempt_output_refs(self, attempt_id: str | None = None) -> tuple[ArtifactRef, ...]:
        """Return session-root references for one attempt's declared outputs.

        Artifact paths in an ``AttemptManifest`` are local to that attempt.
        This helper adds the stable ``attempts/<id>/`` prefix so a caller can
        explicitly pass an earlier output to a later capability through the
        session's ``input_store``. It does not select a best result or copy
        any artifact.
        """

        selected_id = (attempt_id or self.manifest.current_attempt or "").strip()
        if not selected_id:
            raise ValueError("An attempt id is required when no current attempt exists.")
        manifest = next(
            (item for item in self.list_attempts() if item.attempt_id == selected_id),
            None,
        )
        if manifest is None:
            raise KeyError(f"Unknown attempt: {selected_id}")
        prefix = Path("attempts") / manifest.attempt_id
        return tuple(
            self.store.ref(
                prefix / artifact.path,
                kind=artifact.kind,
                schema=artifact.schema,
                producer=artifact.producer,
                status=artifact.status,
            )
            for artifact in manifest.outputs
            if artifact.kind != "capability_result"
        )

    def attempt_output_ref(
        self,
        attempt_id: str | None = None,
        *,
        kind: str,
        schema: str | None = None,
    ) -> ArtifactRef:
        """Return one uniquely identified domain output from an attempt."""

        normalized_kind = kind.strip()
        if not normalized_kind:
            raise ValueError("Artifact kind cannot be empty.")
        matches = tuple(
            ref
            for ref in self.attempt_output_refs(attempt_id)
            if ref.kind == normalized_kind
            and (schema is None or ref.schema == schema)
        )
        if not matches:
            selected_id = (attempt_id or self.manifest.current_attempt or "").strip()
            raise KeyError(
                f"Attempt {selected_id!r} has no output with kind {normalized_kind!r}."
            )
        if len(matches) > 1:
            raise ValueError(
                f"Attempt output kind {normalized_kind!r} is ambiguous; "
                "select an artifact by path instead."
            )
        return matches[0]

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
        parent_attempt_id: str | None = None,
        expected_delta: str = "",
        progressed: bool | None = None,
        failure_kind: str | None = None,
        next_capability: str | None = None,
        evidence_sufficient: bool | None = None,
        hypothesis_supported: bool | None = None,
        experiment_needed: bool | None = None,
        report_auditable: bool | None = None,
        **kwargs: Any,
    ) -> tuple[CapabilityResult, DecisionRecord]:
        """Execute exactly one attempt and persist its decision.

        The controller never retries implicitly. A caller must explicitly ask
        for another attempt, so a failed capability cannot create an unseen
        loop or overwrite its parent attempt.
        """
        capability = capability.strip()
        attempt_id = attempt_id.strip()
        if not capability:
            raise ValueError("Capability name cannot be empty.")
        if not attempt_id:
            raise ValueError("Attempt id cannot be empty.")
        self._ensure_can_execute()
        input_refs = tuple(inputs)
        attempt_profile = self._resolve_attempt_profile(profile)
        self._ensure_profile_capability(capability)
        self._ensure_transition_target(capability, next_capability)
        parent_id = self._resolve_parent_attempt_id(parent_attempt_id)
        self._ensure_capability_transition(capability, parent_attempt_id=parent_id)
        self.registry.resolve(capability)
        self._validate_input_refs(input_refs)
        if "context" in kwargs:
            raise ValueError("Capability context is managed by SessionController.")
        attempt_store, attempt = self.store.new_attempt(
            attempt_id,
            parent_attempt=parent_id,
            trigger=trigger,
            profile=attempt_profile,
            capability=capability,
            inputs=input_refs,
        )
        self.manifest.status = "running"
        self.manifest.current_attempt = attempt_id
        attempt = replace(attempt, status="running", updated_at=_utcnow_iso())
        attempt_store.write_attempt_manifest(attempt)
        self.save()

        try:
            result = self.registry.run(
                capability,
                context=CapabilityContext(
                    store=attempt_store,
                    attempt=attempt,
                    inputs=input_refs,
                    profile=attempt_profile,
                    input_store=self.store,
                ),
                **kwargs,
            )
        except Exception as exc:
            result = CapabilityResult(
                status="failed",
                diagnostics=(f"{type(exc).__name__}: {exc}",),
                provenance={"capability": capability},
            )

        result = _reconcile_declared_outputs(result, attempt_store)
        result_ref = attempt_store.write_capability_result(result)
        if all(artifact.path != result_ref.path for artifact in result.artifacts):
            result = replace(result, artifacts=(*result.artifacts, result_ref))

        has_progress = (
            progressed
            if progressed is not None
            else result.status in {"completed", "partial"}
        )
        self.manifest.budget.record(has_progress)
        transition = self.plan_transition(
            TransitionRequest(
                source=capability,
                result_status=result.status,
                failure_kind=failure_kind,
                target=next_capability,
                evidence_sufficient=evidence_sufficient,
                hypothesis_supported=hypothesis_supported,
                experiment_needed=experiment_needed,
                report_auditable=report_auditable,
                expected_delta=expected_delta,
                signals=result.diagnostics,
            )
        )
        action = transition.action
        reason = transition.reason
        if action != "accept" and self.manifest.budget.exhausted():
            action = "block"
            reason = f"{reason} Session budget exhausted."

        status = (
            "completed"
            if action == "accept" and transition.target is None
            else "blocked"
            if action == "block"
            else "running"
        )
        self.manifest.status = status  # type: ignore[assignment]
        decision = DecisionRecord(
            capability=capability,
            attempt_id=attempt_id,
            action=action,
            result_status=result.status,
            reason=reason,
            progressed=has_progress,
            expected_delta=transition.expected_delta,
            input_paths=tuple(item.path for item in input_refs),
            output_paths=tuple(item.path for item in result.artifacts),
            failure_kind=transition.failure_kind,
            next_capability=transition.target,
            budget_attempts=self.manifest.budget.attempts,
            budget_no_progress=self.manifest.budget.no_progress,
        )
        self.manifest.decisions.append(decision)
        attempt_status = (
            "blocked"
            if action == "block"
            else "failed"
            if result.status == "failed"
            else "completed"
        )
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

    def recover_interrupted(
        self,
        attempt_id: str | None = None,
        *,
        reason: str = "Process interrupted before the capability result was persisted.",
    ) -> tuple[CapabilityResult, DecisionRecord]:
        """Close one manually confirmed interrupted attempt as a failure.

        A process-level interruption can happen before ``execute`` reaches its
        normal result persistence path.  This explicit operation makes that
        state visible without retrying or selecting a replacement capability;
        callers remain responsible for deciding whether to create a new
        attempt.  Attempts with an existing result envelope are left alone so
        recovery cannot overwrite a potentially usable outcome.
        """

        if self.manifest.status in {"completed", "blocked"}:
            raise RuntimeError(
                f"Session is {self.manifest.status}; interrupted recovery is not allowed."
            )
        selected_id = (attempt_id or self.manifest.current_attempt or "").strip()
        if not selected_id:
            raise ValueError("An attempt id is required when no current attempt exists.")
        if not reason.strip():
            raise ValueError("Recovery reason cannot be empty.")
        attempt = next(
            (item for item in self.list_attempts() if item.attempt_id == selected_id),
            None,
        )
        if attempt is None:
            raise KeyError(f"Unknown attempt: {selected_id}")
        if attempt.status != "running":
            raise ValueError(
                f"Attempt {selected_id} is {attempt.status}, not running."
            )
        attempt_store = ArtifactStore(self.store.root / "attempts" / attempt.attempt_id)
        if attempt_store.exists("capability_result.json"):
            raise RuntimeError(
                f"Attempt {selected_id} already has capability_result.json; inspect it before recovery."
            )
        capability = (attempt.capability or "").strip()
        if not capability:
            raise ValueError(f"Attempt {selected_id} has no capability name.")

        result = CapabilityResult(
            status="failed",
            diagnostics=(reason.strip(),),
            provenance={
                "capability": capability,
                "recovery": "explicit_interruption",
            },
        )
        result_ref = attempt_store.write_capability_result(result)
        result = replace(result, artifacts=(result_ref,))
        self.manifest.budget.record(False)
        transition = self.plan_transition(
            TransitionRequest(
                source=capability,
                result_status=result.status,
                signals=result.diagnostics,
            )
        )
        action = transition.action
        reason_text = transition.reason
        if action != "accept" and self.manifest.budget.exhausted():
            action = "block"
            reason_text = f"{reason_text} Session budget exhausted."
        self.manifest.status = "blocked" if action == "block" else "running"
        decision = DecisionRecord(
            capability=capability,
            attempt_id=selected_id,
            action=action,
            result_status=result.status,
            reason=reason_text,
            progressed=False,
            output_paths=(result_ref.path,),
            failure_kind=transition.failure_kind,
            next_capability=transition.target,
            budget_attempts=self.manifest.budget.attempts,
            budget_no_progress=self.manifest.budget.no_progress,
        )
        self.manifest.decisions.append(decision)
        attempt_store.write_attempt_manifest(
            replace(
                attempt,
                status="blocked" if action == "block" else "failed",
                outputs=(result_ref,),
                updated_at=_utcnow_iso(),
            )
        )
        self.save()
        return result, decision

    def _ensure_can_execute(self) -> None:
        if self.manifest.status in {"completed", "blocked"}:
            raise RuntimeError(f"Session is {self.manifest.status}; no further attempt is allowed.")
        running_attempts = tuple(
            item.attempt_id for item in self.list_attempts() if item.status == "running"
        )
        if running_attempts:
            raise RuntimeError(
                "Session has an interrupted running attempt: "
                + ", ".join(running_attempts)
                + "; call recover_interrupted() before creating another attempt."
            )
        if self.manifest.budget.exhausted():
            self.manifest.status = "blocked"
            self.save()
            raise RuntimeError("Session budget is exhausted.")

    def _validate_input_refs(self, inputs: tuple[ArtifactRef, ...]) -> None:
        """Reject missing handoffs before creating an attempt or calling a handler."""

        for ref in inputs:
            if not isinstance(ref, ArtifactRef):
                raise TypeError("Session inputs must be ArtifactRef instances.")
            if ref.status in {"missing", "not_rendered"}:
                raise ValueError(
                    f"Input artifact {ref.path!r} is marked {ref.status}; "
                    "provide an available artifact instead."
                )
            try:
                self.store.require(ref)
            except FileNotFoundError as exc:
                raise FileNotFoundError(
                    f"Input artifact does not exist in the session store: {ref.path}"
                ) from exc

    def _ensure_profile_capability(self, capability: str) -> None:
        lifecycle = resolve_lifecycle_profile(self.manifest.profile)
        if lifecycle is not None and not lifecycle.allows(capability):
            raise ValueError(
                f"Capability {capability.strip()} is outside lifecycle profile "
                f"{lifecycle.name}."
            )

    def _resolve_attempt_profile(self, profile: str | None) -> str | None:
        """Keep attempt metadata inside the session's optional profile scope."""

        session_profile = self.manifest.profile
        requested_profile = profile.strip() if profile and profile.strip() else None
        if session_profile and requested_profile:
            if session_profile.strip() != requested_profile:
                raise ValueError(
                    f"Attempt profile {requested_profile} cannot override session "
                    f"profile {session_profile.strip()}."
                )
            return session_profile
        return session_profile or requested_profile

    def _ensure_transition_target(
        self,
        capability: str,
        target: str | None,
    ) -> None:
        """Reject an impossible target before invoking a capability handler."""
        if target is None:
            return
        decision = self.plan_transition(
            TransitionRequest(
                source=capability,
                result_status="completed",
                target=target,
            )
        )
        if decision.action == "block":
            raise ValueError(decision.reason)

    def _resolve_parent_attempt_id(self, parent_attempt_id: str | None) -> str | None:
        """Resolve the linear parent or an explicitly requested branch parent."""

        if parent_attempt_id is None:
            return self.manifest.current_attempt
        normalized = parent_attempt_id.strip()
        if not normalized:
            raise ValueError("parent_attempt_id cannot be blank.")
        parent = next(
            (item for item in self.list_attempts() if item.attempt_id == normalized),
            None,
        )
        if parent is None:
            raise KeyError(f"Unknown parent attempt: {normalized}")
        if parent.status not in {"completed", "failed"}:
            raise ValueError(
                f"Parent attempt {normalized} is {parent.status}; "
                "only completed or failed attempts can be branched from."
            )
        return parent.attempt_id

    def _ensure_capability_transition(
        self,
        capability: str,
        *,
        parent_attempt_id: str | None = None,
    ) -> None:
        """Reject an actual capability jump not allowed by the recipe.

        ``next_capability`` validates the route proposed by the caller for the
        current attempt. This check validates the next invocation as well, so
        a caller cannot bypass the recipe by omitting that proposal or by
        replacing it before creating the next attempt. An explicit
        ``parent_attempt_id`` deliberately validates against that earlier
        attempt, allowing a bounded branch without turning the controller into
        a graph scheduler.
        """
        previous_id = (
            parent_attempt_id
            if parent_attempt_id is not None
            else self.manifest.current_attempt
        )
        if not previous_id:
            return
        previous = next(
            (item for item in self.list_attempts() if item.attempt_id == previous_id),
            None,
        )
        previous_capability = (previous.capability or "").strip() if previous else ""
        if not previous_capability:
            return
        decision = self.plan_transition(
            TransitionRequest(
                source=previous_capability,
                result_status="completed",
                target=capability,
            )
        )
        if decision.action == "block":
            raise ValueError(decision.reason)
