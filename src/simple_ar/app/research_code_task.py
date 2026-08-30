"""Compose a research direction with the existing code-task backend.

This module is intentionally an adapter, not a second code-generation path.
It turns a persisted synthesis handoff into a task file, delegates generation
and validation to the established code-task bridge, and exposes its final
execution through the existing analysis capability.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import traceback
from typing import Any, Mapping

from simple_ar.core import (
    ArtifactRef,
    AttemptManifest,
    BudgetState,
    CapabilityContext,
    CapabilityRegistry,
    CapabilityResult,
    DecisionRecord,
    SessionController,
)
from simple_ar.experiment.code_task_bridge import (
    CodeTaskExperimentResult,
    CodeTaskExperimentSpec,
    prepare_code_task_experiment,
)
from simple_ar.experiment.execution import RunResult, build_canonical_results
from simple_ar.research.analysis import (
    AnalysisHandoff,
    analyze_experiment_capability,
    compare_experiment_results,
)
from simple_ar.research.synthesis import SynthesisResult
from simple_ar.result_analysis.metrics import normalize_direction
from simple_ar.result_analysis.schema import AnalysisContext, AnalysisResult


@dataclass(frozen=True, slots=True)
class ResearchCodeTaskSessionRequest:
    """Inputs for one bounded synthesis-to-code-task session.

    ``spec`` is the existing code-task backend configuration. The adapter does
    not reinterpret its workspace, edit-scope, or safety settings.
    """

    topic: str
    session_root: Path
    synthesis_file: Path
    spec: CodeTaskExperimentSpec
    model: str | None = None
    use_llm: bool = True
    timeout_sec: int = 300
    baseline_policy: str = "auto"
    baseline_metrics_file: Path | None = None
    label: str = "research-code-task"

    def __post_init__(self) -> None:
        if not self.topic.strip():
            raise ValueError("ResearchCodeTaskSessionRequest.topic cannot be empty.")
        if not str(self.session_root).strip():
            raise ValueError("ResearchCodeTaskSessionRequest.session_root is required.")
        if self.timeout_sec < 1:
            raise ValueError("ResearchCodeTaskSessionRequest.timeout_sec must be positive.")
        if not self.label.strip():
            raise ValueError("ResearchCodeTaskSessionRequest.label cannot be empty.")
        object.__setattr__(self, "session_root", Path(self.session_root))
        object.__setattr__(self, "synthesis_file", Path(self.synthesis_file))
        if self.baseline_metrics_file is not None:
            object.__setattr__(
                self,
                "baseline_metrics_file",
                Path(self.baseline_metrics_file),
            )


@dataclass(frozen=True, slots=True)
class ResearchCodeTaskSessionResult:
    """Persisted code-task execution and result-analysis outputs."""

    topic: str
    session_root: Path
    synthesis: SynthesisResult
    execution: Mapping[str, Any]
    analysis: AnalysisResult
    source_ref: ArtifactRef
    execution_ref: ArtifactRef
    analysis_ref: ArtifactRef
    attempts: tuple[AttemptManifest, ...]
    decisions: tuple[DecisionRecord, ...]

    @property
    def status(self) -> str:
        execution_status = str(self.execution.get("status") or "unknown")
        if execution_status == "passed" and self.analysis.status == "passed":
            return "completed"
        if self.analysis.status == "passed":
            return "partial"
        return self.analysis.status

    @property
    def execution_path(self) -> Path:
        return self.session_root / self.execution_ref.path

    @property
    def analysis_path(self) -> Path:
        return self.session_root / self.analysis_ref.path


class ResearchCodeTaskSessionError(RuntimeError):
    """Raised when a synthesis handoff cannot enter the code-task backend."""


@dataclass(frozen=True, slots=True)
class ResearchCodeTaskCandidateResult:
    """Outcome of one isolated research idea execution."""

    candidate_id: str
    idea_id: str
    title: str
    session: ResearchCodeTaskSessionResult | None
    comparison: Mapping[str, Any]
    accepted: bool
    error: str = ""

    def to_summary_dict(self, *, root: Path | None = None) -> dict[str, Any]:
        """Return references and decisions without duplicating inner results."""

        def summary_path(path: Path) -> str:
            if root is not None:
                try:
                    return path.relative_to(root).as_posix()
                except ValueError:
                    pass
            return str(path)

        execution_status = (
            str(self.session.execution.get("status") or "unknown")
            if self.session is not None
            else "missing"
        )
        return {
            "candidate_id": self.candidate_id,
            "idea_id": self.idea_id,
            "title": self.title,
            "status": self.session.status if self.session is not None else "failed",
            "execution_status": execution_status,
            "comparison_verdict": str(self.comparison.get("verdict") or "inconclusive"),
            "accepted": self.accepted,
            "session_root": (
                summary_path(self.session.session_root) if self.session is not None else ""
            ),
            "execution_path": (
                summary_path(self.session.execution_path) if self.session is not None else ""
            ),
            "analysis_path": (
                summary_path(self.session.analysis_path) if self.session is not None else ""
            ),
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class ResearchCodeTaskCandidatesResult:
    """Bounded candidate search over isolated Code-Task sessions."""

    session_root: Path
    synthesis: SynthesisResult
    candidates: tuple[ResearchCodeTaskCandidateResult, ...]
    selected_candidate_id: str | None
    attempts: tuple[AttemptManifest, ...]
    decisions: tuple[DecisionRecord, ...]
    summary_path: Path

    @property
    def status(self) -> str:
        if self.selected_candidate_id:
            return "completed"
        if self.decisions and self.decisions[-1].action == "block":
            return "blocked"
        return "partial"

    @property
    def selected(self) -> ResearchCodeTaskCandidateResult | None:
        return next(
            (
                item
                for item in self.candidates
                if item.candidate_id == self.selected_candidate_id
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class _CandidateCapabilityRequest:
    base_request: ResearchCodeTaskSessionRequest
    synthesis: SynthesisResult
    candidate_id: str
    idea_id: str


@dataclass(frozen=True, slots=True)
class _CodeTaskCapabilityRequest:
    """Attempt-local request passed through the generic capability registry."""

    spec: CodeTaskExperimentSpec
    model: str | None
    use_llm: bool
    timeout_sec: int
    baseline_policy: str
    baseline_metrics_file: Path | None


def run_research_code_task_session(
    request: ResearchCodeTaskSessionRequest,
    *,
    next_capability: str | None = None,
) -> ResearchCodeTaskSessionResult:
    """Run synthesis, existing Code-Task generation, execution, and analysis.

    The composition is explicit and bounded: one code-task attempt followed by
    one result-analysis attempt. ``next_capability`` can leave an explicitly
    named continuation open for a caller-owned report or audit attempt; the
    default preserves the historical closed session behavior.
    """

    synthesis = _require_ready_synthesis(_load_synthesis(request.synthesis_file))
    if not request.use_llm:
        raise ResearchCodeTaskSessionError(
            "Research code-task composition requires LLM-backed code generation."
        )

    request.session_root.mkdir(parents=True, exist_ok=True)
    registry = CapabilityRegistry()
    registry.register("experiment", _run_code_task_capability)
    registry.register("analysis", analyze_experiment_capability)
    controller = SessionController.create(
        request.session_root,
        session_id=request.session_root.name,
        topic=request.topic,
        profile="experiment",
        registry=registry,
        budget=BudgetState(
            max_attempts=4 if next_capability is not None else 3,
            max_no_progress=2,
        ),
    )

    source_ref = controller.store.write_json(
        "inputs/synthesis.json",
        {
            "schema_version": "research_code_task_input.v1",
            "topic": request.topic,
            "source_file": str(request.synthesis_file),
            "synthesis": synthesis.to_handoff_dict(),
        },
        kind="synthesis_input",
        schema="research_code_task_input.v1",
        producer="research.code_task.session",
    )
    result_schema = _result_schema(request.spec)
    try:
        controller.execute(
            "experiment",
            attempt_id="experiment-001",
            inputs=(source_ref,),
            next_capability="analysis",
            request=_CodeTaskCapabilityRequest(
                spec=request.spec,
                model=request.model,
                use_llm=request.use_llm,
                timeout_sec=request.timeout_sec,
                baseline_policy=request.baseline_policy,
                baseline_metrics_file=request.baseline_metrics_file,
            ),
        )
        execution_ref = controller.attempt_output_ref(
            "experiment-001",
            kind="experiment_result",
            schema="canonical_results.2.5",
        )
        execution_payload = controller.store.read_json(execution_ref)
        if not isinstance(execution_payload, Mapping):
            raise ResearchCodeTaskSessionError(
                f"Code-task output is not a JSON object; inspect {request.session_root}."
            )

        analysis_context = _analysis_context(
            request=request,
            synthesis=synthesis,
            result_schema=result_schema,
        )
        controller.execute(
            "analysis",
            attempt_id="analysis-001",
            inputs=(execution_ref,),
            result_ref=execution_ref,
            analysis_context=analysis_context,
            next_capability=next_capability,
        )
        analysis_ref = controller.attempt_output_ref(
            "analysis-001",
            kind="analysis_result",
            schema="analysis_handoff.v1",
        )
        analysis_payload = controller.store.read_json(analysis_ref)
        if not isinstance(analysis_payload, Mapping):
            raise ResearchCodeTaskSessionError(
                f"Analysis output is not a JSON object; inspect {request.session_root}."
            )
    except ResearchCodeTaskSessionError:
        raise
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ResearchCodeTaskSessionError(
            f"Could not complete code-task session {request.session_root}: {exc}"
        ) from exc

    return ResearchCodeTaskSessionResult(
        topic=request.topic,
        session_root=request.session_root,
        synthesis=synthesis,
        execution=dict(execution_payload),
        analysis=AnalysisHandoff.from_handoff_dict(analysis_payload).analysis,
        source_ref=source_ref,
        execution_ref=execution_ref,
        analysis_ref=analysis_ref,
        attempts=controller.list_attempts(),
        decisions=tuple(controller.manifest.decisions),
    )


def load_research_code_task_session_result(
    session_root: str | Path,
) -> ResearchCodeTaskSessionResult:
    """Restore one persisted Code-Task session without executing it.

    Restoration accepts only the session's declared synthesis input and the
    typed execution/analysis handoffs. It does not scan for an alternative
    result, infer a missing stage, or register executable handlers.
    """

    root = Path(session_root)
    try:
        controller = SessionController.load(
            root,
            registry=CapabilityRegistry(),
        )
        experiment_attempt = _attempt_by_id(controller, "experiment-001")
        if len(experiment_attempt.inputs) != 1:
            raise ValueError(
                "Code-task experiment must declare exactly one synthesis input."
            )
        source_ref = experiment_attempt.inputs[0]
        source_payload = controller.store.read_json(source_ref)
        if not isinstance(source_payload, Mapping):
            raise ValueError("Code-task synthesis input must be a JSON object.")
        synthesis_payload = source_payload.get("synthesis")
        if not isinstance(synthesis_payload, Mapping):
            raise ValueError("Code-task synthesis input has no synthesis handoff.")
        synthesis = SynthesisResult.from_handoff_dict(synthesis_payload)

        execution_ref = controller.attempt_output_ref(
            "experiment-001",
            kind="experiment_result",
            schema="canonical_results.2.5",
        )
        execution_payload = controller.store.read_json(execution_ref)
        if not isinstance(execution_payload, Mapping):
            raise ValueError("Code-task execution result must be a JSON object.")

        analysis_ref = controller.attempt_output_ref(
            "analysis-001",
            kind="analysis_result",
            schema="analysis_handoff.v1",
        )
        analysis_payload = controller.store.read_json(analysis_ref)
        if not isinstance(analysis_payload, Mapping):
            raise ValueError("Code-task analysis handoff must be a JSON object.")
        analysis_handoff = AnalysisHandoff.from_handoff_dict(analysis_payload)
        if analysis_handoff.execution_ref != execution_ref:
            raise ValueError(
                "Code-task analysis handoff does not reference experiment-001 output."
            )
    except (FileNotFoundError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ResearchCodeTaskSessionError(
            f"Could not restore code-task session {root}: {exc}"
        ) from exc

    return ResearchCodeTaskSessionResult(
        topic=controller.manifest.topic,
        session_root=root,
        synthesis=synthesis,
        execution=dict(execution_payload),
        analysis=analysis_handoff.analysis,
        source_ref=source_ref,
        execution_ref=execution_ref,
        analysis_ref=analysis_ref,
        attempts=controller.list_attempts(),
        decisions=tuple(controller.manifest.decisions),
    )


def run_research_code_task_candidates(
    request: ResearchCodeTaskSessionRequest,
    *,
    max_candidates: int = 3,
) -> ResearchCodeTaskCandidatesResult:
    """Try grounded ideas in separate Code-Task sessions until one improves.

    Candidate execution is deliberately a caller-facing policy layer.  The
    existing single-session bridge remains unchanged; this wrapper only adds
    deterministic candidate ordering, independent workspaces, comparison and
    a bounded stop rule.  A failed candidate is evidence and does not mutate
    another candidate's workspace.
    """

    if max_candidates < 1:
        raise ValueError("max_candidates must be at least 1.")
    synthesis = _require_ready_synthesis(_load_synthesis(request.synthesis_file))
    candidates = _candidate_ideas(synthesis, max_candidates)
    request.session_root.mkdir(parents=True, exist_ok=True)
    captured: dict[str, ResearchCodeTaskSessionResult] = {}
    registry = CapabilityRegistry()
    registry.register(
        "experiment",
        lambda *, context, request: _run_candidate_capability(
            context=context,
            request=request,
            captured=captured,
        ),
    )
    controller = SessionController.create(
        request.session_root,
        session_id=request.session_root.name,
        topic=request.topic,
        profile=None,
        registry=registry,
        budget=BudgetState(
            max_attempts=len(candidates),
            max_no_progress=len(candidates),
        ),
    )
    source_ref = controller.store.write_json(
        "inputs/synthesis.json",
        {
            "schema_version": "research_code_task_candidates_input.v1",
            "topic": request.topic,
            "source_file": str(request.synthesis_file),
            "synthesis": synthesis.to_handoff_dict(),
            "max_candidates": max_candidates,
        },
        kind="synthesis_input",
        schema="research_code_task_candidates_input.v1",
        producer="research.code_task.candidates",
    )

    outcomes: list[ResearchCodeTaskCandidateResult] = []
    selected_candidate_id: str | None = None
    for candidate_id, idea_id, title, selected_synthesis in candidates:
        try:
            controller.execute(
                "experiment",
                attempt_id=candidate_id,
                trigger="candidate",
                inputs=(source_ref,),
                request=_CandidateCapabilityRequest(
                    base_request=request,
                    synthesis=selected_synthesis,
                    candidate_id=candidate_id,
                    idea_id=idea_id,
                ),
                expected_delta=(
                    "Accept only a passed candidate with an improved primary metric; "
                    "otherwise evaluate the next bounded idea."
                ),
            )
        except RuntimeError as exc:
            # The controller may block before invoking a new attempt. Preserve
            # the already-recorded candidates and stop at the explicit budget.
            outcomes.append(
                ResearchCodeTaskCandidateResult(
                    candidate_id=candidate_id,
                    idea_id=idea_id,
                    title=title,
                    session=None,
                    comparison=_inconclusive_comparison(str(exc)),
                    accepted=False,
                    error=str(exc),
                )
            )
            break

        session = captured.get(candidate_id)
        comparison = (
            _candidate_comparison(session)
            if session is not None
            else _inconclusive_comparison("Candidate session did not return a result.")
        )
        accepted = _candidate_is_accepted(session, comparison)
        if accepted:
            selected_candidate_id = candidate_id
        outcomes.append(
            ResearchCodeTaskCandidateResult(
                candidate_id=candidate_id,
                idea_id=idea_id,
                title=title,
                session=session,
                comparison=comparison,
                accepted=accepted,
                error="" if session is not None else "Candidate session failed.",
            )
        )
        if accepted or controller.manifest.status == "blocked":
            break

    summary_status = (
        "completed"
        if selected_candidate_id
        else "blocked"
        if controller.manifest.status == "blocked"
        else "partial"
    )
    summary = {
        "schema_version": "research_code_task_candidates.v1",
        "status": summary_status,
        "topic": request.topic,
        "max_candidates": max_candidates,
        "selected_candidate_id": selected_candidate_id,
        "candidates": [
            item.to_summary_dict(root=request.session_root) for item in outcomes
        ],
        "attempts": [item.to_dict() for item in controller.list_attempts()],
        "decisions": [item.to_dict() for item in controller.manifest.decisions],
    }
    summary_ref = controller.store.write_json(
        "candidate_summary.json",
        summary,
        kind="candidate_summary",
        schema="research_code_task_candidates.v1",
        producer="research.code_task.candidates",
    )
    summary_path = controller.store.resolve(summary_ref)
    return ResearchCodeTaskCandidatesResult(
        session_root=request.session_root,
        synthesis=synthesis,
        candidates=tuple(outcomes),
        selected_candidate_id=selected_candidate_id,
        attempts=controller.list_attempts(),
        decisions=tuple(controller.manifest.decisions),
        summary_path=summary_path,
    )


def _run_candidate_capability(
    *,
    context: CapabilityContext,
    request: _CandidateCapabilityRequest,
    captured: dict[str, ResearchCodeTaskSessionResult],
) -> CapabilityResult:
    """Run one candidate below an outer, auditable candidate attempt."""

    synthesis_ref = context.store.write_json(
        "inputs/candidate_synthesis.json",
        request.synthesis.to_handoff_dict(),
        kind="synthesis_input",
        schema="synthesis_result.v1",
        producer="research.code_task.candidates",
    )
    candidate_task_ref: ArtifactRef | None = None
    try:
        candidate_task_ref = _materialize_candidate_task(context, request)
        candidate_spec = request.base_request.spec
        if candidate_task_ref is not None:
            candidate_spec = replace(
                candidate_spec,
                task_file=context.store.resolve(candidate_task_ref),
            )
        inner_request = replace(
            request.base_request,
            session_root=context.store.root / "candidate_session",
            synthesis_file=context.store.resolve(synthesis_ref),
            spec=candidate_spec,
            label=f"{request.base_request.label}-{request.candidate_id}",
        )
        result = run_research_code_task_session(inner_request)
        captured[request.candidate_id] = result
        comparison = _candidate_comparison(result)
        accepted = _candidate_is_accepted(result, comparison)
        summary_ref = context.store.write_json(
            "candidate_result.json",
            {
                "schema_version": "research_code_task_candidate_result.v1",
                "candidate_id": request.candidate_id,
                "idea_id": request.idea_id,
                "status": result.status,
                "accepted": accepted,
                "comparison": comparison,
                "inner_session": "candidate_session",
            },
            kind="candidate_result",
            schema="research_code_task_candidate_result.v1",
            producer="research.code_task.candidates",
        )
        artifacts = [summary_ref, synthesis_ref]
        if candidate_task_ref is not None:
            artifacts.append(candidate_task_ref)
        if context.store.exists("candidate_session"):
            artifacts.append(
                context.store.ref(
                    "candidate_session",
                    kind="candidate_session",
                    schema="session_manifest.v1",
                    producer="research.code_task.candidates",
                )
            )
        if accepted:
            status = "completed"
            diagnostics: tuple[str, ...] = ()
        elif result.status == "completed":
            status = "failed"
            diagnostics = (
                "Candidate completed without an improved primary metric; trying the next bounded idea.",
            )
        else:
            status = "failed"
            diagnostics = (
                f"Candidate session status was {result.status!r}; candidate was not accepted.",
            )
        return CapabilityResult(
            status=status,
            artifacts=tuple(_unique_refs(artifacts)),
            diagnostics=diagnostics,
            usage={
                "candidate_id": request.candidate_id,
                "idea_id": request.idea_id,
            },
            provenance={
                "capability": "experiment",
                "backend": "bounded_candidate",
                "candidate_id": request.candidate_id,
                "accepted": str(accepted).lower(),
            },
        )
    except Exception as exc:
        error_ref = context.store.write_json(
            "candidate_error.json",
            {
                "schema_version": "research_code_task_candidate_error.v1",
                "candidate_id": request.candidate_id,
                "idea_id": request.idea_id,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
            kind="diagnosis",
            schema="research_code_task_candidate_error.v1",
            producer="research.code_task.candidates",
        )
        error_artifacts = [synthesis_ref, error_ref]
        if candidate_task_ref is not None:
            error_artifacts.append(candidate_task_ref)
        return CapabilityResult(
            status="failed",
            artifacts=tuple(_unique_refs(error_artifacts)),
            diagnostics=(f"{type(exc).__name__}: {exc}",),
            provenance={
                "capability": "experiment",
                "backend": "bounded_candidate",
                "candidate_id": request.candidate_id,
                "failure_artifact": error_ref.path,
            },
        )


def _materialize_candidate_task(
    context: CapabilityContext,
    request: _CandidateCapabilityRequest,
) -> ArtifactRef | None:
    """Preserve a supplied task while adding the selected idea explicitly."""

    source = request.base_request.spec.task_file
    if source is None:
        return None
    text = source.read_text(encoding="utf-8").rstrip()
    return context.store.write_text(
        "inputs/candidate_task.md",
        text
        + "\n\n## Selected research candidate\n\n"
        + _generated_task_text(request.synthesis),
        kind="task_input",
        schema="markdown.v1",
        producer="research.code_task.candidates",
    )


def _candidate_ideas(
    synthesis: SynthesisResult,
    limit: int,
) -> tuple[tuple[str, str, str, SynthesisResult], ...]:
    """Return stable candidate ids and contracts without ranking by score."""

    if not synthesis.ideas:
        return (
            (
                "candidate-001",
                "contract-001",
                "Default experiment contract",
                synthesis,
            ),
        )
    return tuple(
        (
            f"candidate-{index:03d}",
            idea.idea_id,
            idea.title or idea.idea_id,
            synthesis.for_idea(idea.idea_id),
        )
        for index, idea in enumerate(synthesis.ideas[:limit], start=1)
    )


def _candidate_comparison(
    result: ResearchCodeTaskSessionResult,
) -> dict[str, Any]:
    execution = result.execution
    existing = execution.get("comparisons")
    if isinstance(existing, list):
        for item in existing:
            if isinstance(item, Mapping) and str(item.get("verdict") or "").strip():
                return dict(item)
    baseline = execution.get("baseline")
    if isinstance(baseline, Mapping):
        baseline_result = dict(baseline)
        baseline_result["result_schema"] = execution.get("result_schema", {})
        return compare_experiment_results(
            baseline_result,
            execution,
            primary_metric=str(execution.get("primary_metric") or ""),
        )
    return _inconclusive_comparison("Candidate did not expose baseline evidence.")


def _candidate_is_accepted(
    result: ResearchCodeTaskSessionResult | None,
    comparison: Mapping[str, Any],
) -> bool:
    return bool(
        result is not None
        and result.status == "completed"
        and str(comparison.get("verdict") or "").strip().lower() == "improved"
    )


def _inconclusive_comparison(reason: str) -> dict[str, Any]:
    return {
        "schema_version": "experiment_comparison.v1",
        "status": "incomplete",
        "verdict": "inconclusive",
        "reasons": [reason],
    }


def _run_code_task_capability(
    *,
    context: CapabilityContext,
    request: _CodeTaskCapabilityRequest,
) -> CapabilityResult:
    """Run the existing bridge and retain a canonical result on every path."""

    if len(context.inputs) != 1:
        raise ValueError("Code-task capability expects exactly one synthesis input.")
    source = context.read_input_json(context.inputs[0])
    if not isinstance(source, Mapping) or not isinstance(source.get("synthesis"), Mapping):
        raise ValueError("Research code-task input has no synthesis handoff.")
    synthesis = SynthesisResult.from_handoff_dict(source["synthesis"])

    task_ref: ArtifactRef | None = None
    code_task_run_dir = context.store.root / "code_task_run"
    try:
        task_ref = _materialize_task(context, synthesis, request.spec.task_file)
        spec = replace(request.spec, task_file=context.store.resolve(task_ref))
        result = prepare_code_task_experiment(
            code_task_run_dir=code_task_run_dir,
            spec=spec,
            model=request.model,
            use_llm=request.use_llm,
            timeout_sec=request.timeout_sec,
            baseline_policy=request.baseline_policy,
            baseline_metrics_file=request.baseline_metrics_file,
        )
        canonical = _canonical_code_task_result(
            result,
            spec=spec,
            store=context.store,
            synthesis=synthesis,
        )
        result_ref = context.store.write_json(
            "results.json",
            canonical,
            kind="experiment_result",
            schema="canonical_results.2.5",
            producer="research.code_task",
        )
        artifacts = _result_artifacts(
            context.store,
            result,
            result_ref=result_ref,
            task_ref=task_ref,
        )
        status = str(canonical.get("status") or "failed")
        return CapabilityResult(
            status="completed" if status == "passed" else "failed",
            artifacts=artifacts,
            diagnostics=()
            if status == "passed"
            else (f"Code-task execution status: {status}.",),
            usage={
                "changed_file_count": len(result.changed_files),
                "edit_count": result.edit_count,
                "work_item_count": result.work_plan_item_count,
            },
            provenance={
                "capability": "experiment",
                "backend": "code_task",
                "template": result.template,
            },
        )
    except Exception as exc:
        error_ref = context.store.write_json(
            "code_task_error.json",
            {
                "schema_version": "research_code_task_error.v1",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
                "code_task_run": _relative_path(context.store, code_task_run_dir),
            },
            kind="diagnosis",
            schema="research_code_task_error.v1",
            producer="research.code_task",
        )
        canonical = _failed_canonical_result(
            spec=request.spec,
            store=context.store,
            synthesis=synthesis,
            error=exc,
            code_task_run_dir=code_task_run_dir,
        )
        result_ref = context.store.write_json(
            "results.json",
            canonical,
            kind="experiment_result",
            schema="canonical_results.2.5",
            producer="research.code_task",
        )
        artifacts = [result_ref, error_ref]
        if task_ref is not None:
            artifacts.append(task_ref)
        if code_task_run_dir.exists():
            artifacts.append(
                context.store.ref(
                    "code_task_run",
                    kind="code_task_run",
                    schema="code_task_run.v1",
                    producer="research.code_task",
                )
            )
        return CapabilityResult(
            status="failed",
            artifacts=tuple(_unique_refs(artifacts)),
            diagnostics=(f"{type(exc).__name__}: {exc}",),
            provenance={
                "capability": "experiment",
                "backend": "code_task",
                "failure_artifact": error_ref.path,
            },
        )


def _materialize_task(
    context: CapabilityContext,
    synthesis: SynthesisResult,
    source_task_file: Path | None,
) -> ArtifactRef:
    if source_task_file is not None:
        task_text = source_task_file.read_text(encoding="utf-8")
    else:
        if synthesis.experiment_contract is None:
            raise ValueError("Synthesis handoff has no experiment contract.")
        task_text = _generated_task_text(synthesis)
    return context.store.write_text(
        "inputs/research_code_task.md",
        task_text,
        kind="task_input",
        schema="markdown.v1",
        producer="research.code_task.session",
    )


def _generated_task_text(synthesis: SynthesisResult) -> str:
    contract = synthesis.experiment_contract
    assert contract is not None
    lines = [
        "# Research experiment",
        "",
        "Implement and evaluate the proposed research change in the provided project.",
        "The research handoff is context for the task; the recorded benchmark and",
        "project interfaces remain the acceptance authority.",
        "",
        "## Hypothesis",
        contract.hypothesis,
        "",
        "## Research context",
        f"- Baseline: {contract.baseline}",
        f"- Dataset: {contract.dataset}",
        f"- Metrics: {', '.join(contract.metrics) or 'use the configured benchmark metrics'}",
        f"- Motivation: {', '.join(contract.motivation_refs) or 'not specified'}",
        "",
        "## Proposed change",
        contract.proposed_change or "Make the smallest evidence-supported improvement.",
        "",
        "## Scope and validation",
        *[f"- {item}" for item in contract.implementation_scope],
        *[f"- Validate: {item}" for item in contract.validation_hints],
        "- Do not modify tests or benchmark scoring code.",
        "- Preserve the existing public interfaces and produce the configured metrics.",
        "",
    ]
    return "\n".join(lines)


def _canonical_code_task_result(
    result: CodeTaskExperimentResult,
    *,
    spec: CodeTaskExperimentSpec,
    store: Any,
    synthesis: SynthesisResult,
) -> dict[str, Any]:
    root = result.code_task_run_dir
    baseline_report_path = root / "code_task" / "run" / "baseline" / "execution_report.json"
    patched_report_path = root / "code_task" / "run" / "patched" / "execution_report.json"
    comparison_path = root / "code_task" / "comparison.json"
    patched_report = _read_json(patched_report_path)
    baseline_report = _read_json(baseline_report_path)
    comparison = _read_json(comparison_path)
    patched_run = _run_result_from_report(root, patched_report, patched_report_path)
    baseline_run = _run_result_from_report(root, baseline_report, baseline_report_path)
    result_schema = _result_schema(spec)
    artifacts = _code_task_artifact_paths(store, result, baseline_report_path, patched_report_path, comparison_path)
    canonical = build_canonical_results(
        patched_run,
        result_schema=result_schema,
        experiment_contract=(
            synthesis.experiment_contract.to_row()
            if synthesis.experiment_contract is not None
            else None
        ),
        artifacts=artifacts,
        comparisons=[comparison] if comparison else [],
    )
    canonical["status"] = str(patched_report.get("status") or patched_run.status)
    canonical["baseline"] = _run_summary(baseline_run, baseline_report_path, store)
    canonical["code_task"] = _code_task_metadata(result, store)
    canonical["execution"]["code_task_report"] = _relative_path(store, patched_report_path)
    return canonical


def _failed_canonical_result(
    *,
    spec: CodeTaskExperimentSpec,
    store: Any,
    synthesis: SynthesisResult,
    error: Exception,
    code_task_run_dir: Path,
) -> dict[str, Any]:
    run = RunResult(
        returncode=None,
        timed_out=False,
        stdout="",
        stderr=str(error),
        metrics={},
        command=[],
        cwd=str(code_task_run_dir),
        duration_sec=0.0,
        backend="code_task",
        label="patched",
    )
    canonical = build_canonical_results(
        run,
        result_schema=_result_schema(spec),
        experiment_contract=(
            synthesis.experiment_contract.to_row()
            if synthesis.experiment_contract is not None
            else None
        ),
        artifacts={"code_task_run": _relative_path(store, code_task_run_dir)},
    )
    canonical["status"] = "failed"
    canonical["error"] = {"type": type(error).__name__, "message": str(error)}
    return canonical


def _run_result_from_report(
    code_task_root: Path,
    report: Mapping[str, Any],
    report_path: Path,
) -> RunResult:
    metric_values = report.get("metric_values")
    if not isinstance(metric_values, Mapping):
        metric_values = _read_json(_resolve_report_path(code_task_root, report.get("metrics")))
    metrics = {
        str(name): float(value)
        for name, value in dict(metric_values).items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    return RunResult(
        returncode=_optional_int(report.get("returncode")),
        timed_out=bool(report.get("timed_out", False)),
        stdout="",
        stderr="",
        metrics=metrics,
        command=[str(item) for item in report.get("command", []) if str(item).strip()]
        if isinstance(report.get("command"), list)
        else [],
        cwd=str(report.get("cwd") or ""),
        duration_sec=float(report.get("duration_sec") or 0.0),
        backend="code_task",
        label=str(report.get("label") or report_path.parent.name),
    )


def _analysis_context(
    *,
    request: ResearchCodeTaskSessionRequest,
    synthesis: SynthesisResult,
    result_schema: Mapping[str, Any],
) -> AnalysisContext:
    contract = synthesis.experiment_contract
    assert contract is not None
    metric_names = list(result_schema.get("required_metrics") or [])
    directions = result_schema.get("metric_directions")
    directions = directions if isinstance(directions, Mapping) else {}
    normalized_directions = {
        name: normalize_direction(directions.get(name))
        for name in metric_names
    }
    return AnalysisContext(
        task_id=request.session_root.name,
        title=request.topic,
        hypotheses=[
            {"id": contract.contract_id or "hypothesis-1", "statement": contract.hypothesis}
        ],
        expected_metrics=[
            {"name": name, "direction": normalized_directions[name]}
            for name in metric_names
        ],
        metric_directions=normalized_directions,
        task_contract={"experiment_contract": contract.to_row()},
        metadata={"synthesis_file": str(request.synthesis_file), "backend": "code_task"},
    )


def _result_schema(spec: CodeTaskExperimentSpec) -> dict[str, Any]:
    primary = str(spec.primary_metric or "").strip()
    directions = {
        str(name): str(direction)
        for name, direction in spec.metric_directions.items()
        if str(name).strip()
    }
    required = list(dict.fromkeys(([primary] if primary else []) + list(directions)))
    schema: dict[str, Any] = {"required_metrics": required}
    if primary:
        schema["primary_metric"] = primary
    if directions:
        schema["metric_directions"] = directions
    return schema


def _code_task_artifact_paths(
    store: Any,
    result: CodeTaskExperimentResult,
    baseline_report: Path,
    patched_report: Path,
    comparison: Path,
) -> dict[str, str]:
    paths = {
        "code_task_run": _relative_path(store, result.code_task_run_dir),
        "baseline_execution_report": _relative_path(store, baseline_report),
        "patched_execution_report": _relative_path(store, patched_report),
        "comparison": _relative_path(store, comparison),
    }
    return {name: path for name, path in paths.items() if path}


def _result_artifacts(
    store: Any,
    result: CodeTaskExperimentResult,
    *,
    result_ref: ArtifactRef,
    task_ref: ArtifactRef | None,
) -> tuple[ArtifactRef, ...]:
    refs = [result_ref]
    if task_ref is not None:
        refs.append(task_ref)
    refs.append(
        store.ref(
            "code_task_run",
            kind="code_task_run",
            schema="code_task_run.v1",
            producer="research.code_task",
        )
    )
    return tuple(_unique_refs(refs))


def _code_task_metadata(result: CodeTaskExperimentResult, store: Any) -> dict[str, Any]:
    data = asdict(result)
    return _jsonable_paths(data, store)


def _run_summary(run: RunResult, report: Path, store: Any) -> dict[str, Any]:
    return {
        "status": run.status,
        "returncode": run.returncode,
        "timed_out": run.timed_out,
        "duration_sec": run.duration_sec,
        "metrics": dict(run.metrics),
        "execution_report": _relative_path(store, report),
    }


def _resolve_report_path(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        return Path()
    candidate = root / value
    if candidate.is_file():
        return candidate
    return root / "code_task" / "run" / Path(value).name


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _load_synthesis(path: Path) -> SynthesisResult:
    if not path.is_file():
        raise ResearchCodeTaskSessionError(f"Synthesis handoff not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResearchCodeTaskSessionError(f"Could not read synthesis handoff {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ResearchCodeTaskSessionError("Synthesis handoff must be a JSON object.")
    if payload.get("schema_version") == "research_brief.v1":
        payload = payload.get("synthesis")
    if not isinstance(payload, Mapping):
        raise ResearchCodeTaskSessionError("Research brief handoff has no synthesis object.")
    try:
        return SynthesisResult.from_handoff_dict(payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise ResearchCodeTaskSessionError(f"Invalid synthesis handoff {path}: {exc}") from exc


def _require_ready_synthesis(synthesis: SynthesisResult) -> SynthesisResult:
    """Keep the downstream code boundary closed over incomplete synthesis."""

    if synthesis.status != "ready":
        raise ResearchCodeTaskSessionError(
            f"Synthesis handoff status is {synthesis.status!r}; review it before execution."
        )
    if synthesis.experiment_contract is None:
        raise ResearchCodeTaskSessionError(
            "Synthesis handoff has no experiment contract; inspect the source handoff."
        )
    return synthesis


def _relative_path(store: Any, path: Path) -> str:
    try:
        return path.relative_to(store.root).as_posix()
    except ValueError:
        return str(path)


def _jsonable_paths(value: Any, store: Any) -> Any:
    if isinstance(value, Path):
        return _relative_path(store, value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable_paths(item, store) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable_paths(item, store) for item in value]
    return value


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _unique_refs(refs: list[ArtifactRef]) -> list[ArtifactRef]:
    seen: set[str] = set()
    unique: list[ArtifactRef] = []
    for ref in refs:
        if ref.path in seen:
            continue
        seen.add(ref.path)
        unique.append(ref)
    return unique


def _attempt_by_id(controller: SessionController, attempt_id: str) -> AttemptManifest:
    """Return one declared attempt without searching for substitutes."""

    wanted = attempt_id.strip()
    attempt = next(
        (item for item in controller.list_attempts() if item.attempt_id == wanted),
        None,
    )
    if attempt is None:
        raise KeyError(f"Unknown attempt: {wanted}")
    return attempt


__all__ = [
    "ResearchCodeTaskCandidateResult",
    "ResearchCodeTaskCandidatesResult",
    "ResearchCodeTaskSessionError",
    "ResearchCodeTaskSessionRequest",
    "ResearchCodeTaskSessionResult",
    "load_research_code_task_session_result",
    "run_research_code_task_candidates",
    "run_research_code_task_session",
]
