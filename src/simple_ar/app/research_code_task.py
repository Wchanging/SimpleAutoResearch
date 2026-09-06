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
)
from simple_ar.research.contracts import ResearchExperimentContract
from simple_ar.research.design import (
    ResearchDesignRequest,
    ResearchDesignResult,
    run_research_design_capability,
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
    idea_id: str | None = None
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
        if self.idea_id is not None and not self.idea_id.strip():
            raise ValueError("ResearchCodeTaskSessionRequest.idea_id cannot be blank.")
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
    design: ResearchDesignResult | None = None
    design_ref: ArtifactRef | None = None

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
    registry.register("research_design", run_research_design_capability)
    registry.register("experiment", _run_code_task_capability)
    registry.register("analysis", analyze_experiment_capability)
    controller = SessionController.create(
        request.session_root,
        session_id=request.session_root.name,
        topic=request.topic,
        profile="experiment",
        registry=registry,
        budget=BudgetState(
            max_attempts=5 if next_capability is not None else 3,
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
    result_schema = request.spec.result_schema()
    design_result, _ = controller.execute(
        "research_design",
        attempt_id="design-001",
        inputs=(source_ref,),
        next_capability="experiment",
        request=ResearchDesignRequest(
            synthesis=synthesis,
            topic=request.topic,
            idea_id=request.idea_id,
            execution_schema=result_schema,
        ),
    )
    if design_result.status in {"failed", "blocked"}:
        details = "; ".join(item for item in design_result.diagnostics if item.strip())
        raise ResearchCodeTaskSessionError(
            "research_design capability returned "
            f"{design_result.status!r}"
            + (f": {details}" if details else ".")
        )
    try:
        design_ref = controller.attempt_output_ref(
            "design-001",
            kind="research_design",
            schema="research_design.v1",
        )
    except (KeyError, OSError, ValueError) as exc:
        raise ResearchCodeTaskSessionError(
            "research_design capability did not provide its typed output: "
            f"{exc}"
        ) from exc
    design_payload = controller.store.read_json(design_ref)
    if not isinstance(design_payload, Mapping):
        raise ResearchCodeTaskSessionError(
            f"Research design output is not a JSON object; inspect {request.session_root}."
        )
    design = ResearchDesignResult.from_handoff_dict(design_payload)
    if design.status != "ready" or design.contract is None:
        raise ResearchCodeTaskSessionError(
            "Research design is not executable: "
            + "; ".join(design.diagnostics or ("no diagnostic was recorded",))
        )
    try:
        experiment_result, _ = controller.execute(
            "experiment",
            attempt_id="experiment-001",
            inputs=(source_ref, design_ref),
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
        try:
            execution_ref = controller.attempt_output_ref(
                "experiment-001",
                kind="experiment_result",
                schema="canonical_results.2.5",
            )
            execution_payload = controller.store.read_json(execution_ref)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            details = "; ".join(
                item for item in experiment_result.diagnostics if item.strip()
            )
            raise ResearchCodeTaskSessionError(
                "experiment capability did not provide its typed output: "
                f"{exc}"
                + (f" Diagnostics: {details}" if details else "")
            ) from exc
        if not isinstance(execution_payload, Mapping):
            raise ResearchCodeTaskSessionError(
                f"Code-task output is not a JSON object; inspect {request.session_root}."
            )

        analysis_context = _analysis_context(
            request=request,
            synthesis=synthesis,
            result_schema=result_schema,
            contract=design.contract,
        )
        analysis_result, _ = controller.execute(
            "analysis",
            attempt_id="analysis-001",
            inputs=(execution_ref,),
            result_ref=execution_ref,
            analysis_context=analysis_context,
            next_capability=next_capability,
        )
        try:
            analysis_ref = controller.attempt_output_ref(
                "analysis-001",
                kind="analysis_result",
                schema="analysis_handoff.v1",
            )
            analysis_payload = controller.store.read_json(analysis_ref)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            details = "; ".join(
                item for item in analysis_result.diagnostics if item.strip()
            )
            raise ResearchCodeTaskSessionError(
                "analysis capability did not provide its typed output: "
                f"{exc}"
                + (f" Diagnostics: {details}" if details else "")
            ) from exc
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
        design=design,
        design_ref=design_ref,
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
        if len(experiment_attempt.inputs) not in {1, 2}:
            raise ValueError(
                "Code-task experiment must declare one synthesis input and an optional design input."
            )
        source_ref = experiment_attempt.inputs[0]
        source_payload = controller.store.read_json(source_ref)
        if not isinstance(source_payload, Mapping):
            raise ValueError("Code-task synthesis input must be a JSON object.")
        synthesis_payload = source_payload.get("synthesis")
        if not isinstance(synthesis_payload, Mapping):
            raise ValueError("Code-task synthesis input has no synthesis handoff.")
        synthesis = SynthesisResult.from_handoff_dict(synthesis_payload)

        design: ResearchDesignResult | None = None
        design_ref: ArtifactRef | None = None
        if len(experiment_attempt.inputs) == 2:
            design_ref = experiment_attempt.inputs[1]
            design_payload = controller.store.read_json(design_ref)
            if not isinstance(design_payload, Mapping):
                raise ValueError("Code-task design input must be a JSON object.")
            design = ResearchDesignResult.from_handoff_dict(design_payload)
            if design.status != "ready" or design.contract is None:
                raise ValueError("Code-task design handoff is not executable.")

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
        design=design,
        design_ref=design_ref,
    )


def _run_code_task_capability(
    *,
    context: CapabilityContext,
    request: _CodeTaskCapabilityRequest,
    backend: Any | None = None,
) -> CapabilityResult:
    """Run the existing bridge and retain a canonical result on every path."""

    # Registry handlers share the experiment adapter's optional backend slot;
    # Code-Task owns its backend selection in ``spec``.
    del backend

    if len(context.inputs) not in {1, 2}:
        raise ValueError(
            "Code-task capability expects one synthesis input and an optional design input."
        )
    source = context.read_input_json(context.inputs[0])
    synthesis = SynthesisResult.from_handoff_dict(_synthesis_payload(source))
    if len(context.inputs) == 2:
        design_payload = context.read_input_json(context.inputs[1])
        if not isinstance(design_payload, Mapping):
            raise ValueError("Research code-task input has no design handoff.")
        design = ResearchDesignResult.from_handoff_dict(design_payload)
        if design.status != "ready" or design.contract is None:
            raise ValueError("Research code-task design handoff is not executable.")
        synthesis = replace(synthesis, experiment_contract=design.contract)

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


def _synthesis_payload(source: object) -> Mapping[str, Any]:
    """Normalize the canonical synthesis input and legacy session wrapper.

    The full research session passes the synthesis capability's own
    ``synthesis_result.v1`` artifact. The standalone code-task composition
    historically stores the same handoff under an ``inputs.synthesis``
    wrapper. Accepting both at this boundary keeps one code-task backend while
    avoiding a second synthesis schema or a caller-specific copy of the
    adapter.
    """

    if not isinstance(source, Mapping):
        raise ValueError("Research code-task input must be a JSON object.")
    if str(source.get("schema_version") or "") == "synthesis_result.v1":
        return source
    nested = source.get("synthesis")
    if isinstance(nested, Mapping):
        return nested
    raise ValueError("Research code-task input has no synthesis handoff.")


def _materialize_task(
    context: CapabilityContext,
    synthesis: SynthesisResult,
    source_task_file: Path | None,
) -> ArtifactRef:
    if source_task_file is not None:
        task_text = source_task_file.read_text(encoding="utf-8")
        if synthesis.experiment_contract is not None:
            task_text = task_text.rstrip() + "\n\n" + _research_handoff_text(
                synthesis.experiment_contract
            )
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


def _research_handoff_text(contract: ResearchExperimentContract) -> str:
    """Append the selected research direction without replacing the task."""

    lines = [
        "## Research handoff",
        "",
        "The following context comes from the evidence-to-design handoff. The",
        "original task, configured benchmark, and project interfaces remain the",
        "acceptance authority.",
        "",
        "### Hypothesis",
        contract.hypothesis,
        "",
        "### Proposed change",
        contract.proposed_change or "Use the smallest evidence-supported change.",
        "",
        "### Experimental context",
        f"- Baseline: {contract.baseline}",
        f"- Dataset: {contract.dataset}",
        f"- Metrics: {', '.join(contract.metrics) or 'use configured metrics'}",
        "",
        "### Validation guidance",
        *[f"- {item}" for item in contract.validation_hints],
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
    comparison_path = root / "code_task" / "run" / "comparison.json"
    patched_report = _read_json(patched_report_path)
    baseline_report = _read_json(baseline_report_path)
    comparison = _read_json(comparison_path)
    patched_run = _run_result_from_report(root, patched_report, patched_report_path)
    baseline_run = _run_result_from_report(root, baseline_report, baseline_report_path)
    result_schema = spec.result_schema()
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
        result_schema=spec.result_schema(),
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
    contract: ResearchExperimentContract | None = None,
) -> AnalysisContext:
    experiment_contract = contract or synthesis.experiment_contract
    if experiment_contract is None:
        raise ValueError("An experiment contract is required for analysis context.")
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
            {
                "id": experiment_contract.contract_id or "hypothesis-1",
                "statement": experiment_contract.hypothesis,
            }
        ],
        expected_metrics=[
            {"name": name, "direction": normalized_directions[name]}
            for name in metric_names
        ],
        metric_directions=normalized_directions,
        task_contract={"experiment_contract": experiment_contract.to_row()},
        metadata={"synthesis_file": str(request.synthesis_file), "backend": "code_task"},
    )


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
    "ResearchCodeTaskSessionError",
    "ResearchCodeTaskSessionRequest",
    "ResearchCodeTaskSessionResult",
    "load_research_code_task_session_result",
    "run_research_code_task_session",
]
