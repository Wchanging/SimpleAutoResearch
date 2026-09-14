"""Small persistent application entry for the P04 research path.

The application orders existing capabilities and owns session state. It does
not duplicate planning, search, reading, synthesis or design logic. Research
summary, candidate comparison, design and explicit user-command execution /
analysis, bounded preparation/repair and experimental report writing/assembly/audit
are connected. Research revision is deliberately limited to explicit, bounded
experiment retries; it does not infer a new research direction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from simple_ar.app.research_intake import normalize_assets, validate_brief, write_intake_artifacts
from simple_ar.app.research_execution import execution_pairs, execution_request, implementation_request, repair_limit
from simple_ar.research.preparation import PreparationRequest
from simple_ar.experiment.execution.backend import LocalExecutionBackend
from simple_ar.research.experiment import ExperimentRequest
from simple_ar.core import (
    ArtifactRef,
    BudgetState,
    BudgetError,
    BudgetLedger,
    CapabilityRegistry,
    SessionController,
)
from simple_ar.research.brief import evidence_pack_from_read
from simple_ar.research.assessment import IdeaAssessmentRequest
from simple_ar.research.design import ResearchDesignRequest
from simple_ar.research.documents.ingest import DocumentBundle, DocumentIngestRequest
from simple_ar.research.evidence.reader import ReadRequest, ReadResult
from simple_ar.research.planning.capability import (
    ResearchPlanRequest,
    ResearchPlanResult,
    search_request_from_plan,
)
from simple_ar.research.registry import register_research_capabilities
from simple_ar.research.sources import (
    SearchProviderRegistry,
    SearchResult,
    SearchSelectionPolicy,
    default_search_provider_registry,
)
from simple_ar.research.synthesis import SynthesisRequest, SynthesisResult, allowed_evidence_refs
from simple_ar.research.workflow_contracts import Diagnostic, ResearchAsset, ResearchBrief
from simple_ar.result_analysis.metrics import normalize_direction


class ResearchApplicationError(RuntimeError):
    """Raised when a session cannot advance without an explicit decision."""


@dataclass(frozen=True, slots=True)
class ResearchApplicationServices:
    """Concrete dependencies and bounded settings for one application run."""

    llm_client: Any | None = field(default=None, repr=False, compare=False)
    search_registry: SearchProviderRegistry | None = field(
        default=None, repr=False, compare=False
    )
    max_results: int = 10
    max_chunks: int | None = 300
    idea_limit: int = 3
    cache_dir: Path | None = None
    extraction_dir: Path | None = None
    input_base_dir: Path | None = None
    config: Mapping[str, object] = field(default_factory=dict)
    budget_limits: Mapping[str, int | float | None] = field(
        default_factory=lambda: {"llm_requests": 20, "total_tokens": 80_000}
    )
    max_attempts: int = 16
    max_no_progress: int = 3

    def __post_init__(self) -> None:
        if self.max_results < 1 or self.idea_limit < 1:
            raise ValueError("Research application numeric settings must be positive.")
        if self.max_chunks is not None and self.max_chunks < 1:
            raise ValueError("max_chunks must be positive when provided.")
        if self.max_attempts < 1 or self.max_no_progress < 1:
            raise ValueError("Research application budgets must be positive.")
        for name in ("cache_dir", "extraction_dir", "input_base_dir"):
            value = getattr(self, name)
            object.__setattr__(self, name, Path(value) if value is not None else None)
        object.__setattr__(self, "config", dict(self.config))
        object.__setattr__(self, "budget_limits", dict(self.budget_limits))


@dataclass(frozen=True, slots=True)
class ResearchApplicationView:
    """Serializable status for a CLI, UI, or service caller."""

    session_root: Path
    session_id: str
    topic: str
    status: str
    status_reason: str
    revision: int
    requested_outputs: tuple[str, ...]
    next_action: str | None
    state_refs: dict[str, ArtifactRef]
    attempts: tuple[dict[str, Any], ...]
    budget: dict[str, Any]
    diagnostics: tuple[str, ...] = ()
    work_plan: dict[str, Any] = field(default_factory=dict)
    parent_session: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "research_application_view.v1",
            "session_root": str(self.session_root),
            "session_id": self.session_id,
            "topic": self.topic,
            "status": self.status,
            "status_reason": self.status_reason,
            "revision": self.revision,
            "requested_outputs": list(self.requested_outputs),
            "next_action": self.next_action,
            "state_refs": {name: ref.to_dict() for name, ref in self.state_refs.items()},
            "attempts": [dict(item) for item in self.attempts],
            "budget": dict(self.budget),
            "diagnostics": list(self.diagnostics),
            "work_plan": dict(self.work_plan),
            "parent_session": self.parent_session,
        }


_CAPABILITY_OUTPUTS = {
    "plan": ("plan", "research_plan", "research_plan.v1"),
    "search": ("search", "search_result", "search_handoff.v1"),
    "document_ingest": ("documents", "document_bundle", "document_bundle.v1"),
    "read": ("read", "read_result", "read_result.v1"),
    "synthesize": ("synthesis", "synthesis_result", "synthesis_result.v1"),
    "assess_ideas": ("assessment", "idea_assessment", "idea_assessment.v1"),
    "research_design": ("design", "research_design", "research_design.v1"),
    "experiment": ("experiment", "experiment_result", "canonical_results.2.5"),
    "analysis": ("analysis", "analysis_result", "analysis_handoff.v1"),
    "implement": ("implementation", "implementation_result", "research_implementation.v1"),
    "prepare_execution": ("preparation", "prepared_execution", "prepared_execution.v1"),
    "report_write": ("writer", "report_writer_result", "report_agent_result.v1"),
    "report": ("report", "report", "report.v1"),
    "report_audit": ("report_audit", "report_audit", "report_audit.v1"),
}


_BASE_STEPS = (
    ("plan", "plan"),
    ("search", "search"),
    ("document_ingest", "documents"),
    ("read", "read"),
    ("synthesize", "synthesis"),
)
_DERIVED_KEYS = {name for _, name in _BASE_STEPS} | {
    "assessment", "idea_comparison", "summary", "summary_snapshot",
    "work_plan", "work_plan_markdown", "readiness", "design", "preparation", "baseline", "implementation", "experiment", "analysis", "comparison", "writer", "report", "report_audit"
}
_EXECUTION_OUTPUTS = {
    "experiment", "experiments", "code", "code_task"
}
_ASSESSMENT_OUTPUTS = {"assessment", "idea_assessment", "idea_comparison"}
_DESIGN_OUTPUTS = {"design", "research_design"}
_INPUT_REF_NAMES = {
    "brief", "brief_markdown", "assets", "runtime_config", "diagnostics"
}


class ResearchApplication:
    """Compose the canonical plan-to-synthesis capabilities."""

    def __init__(
        self,
        controller: SessionController,
        brief: ResearchBrief,
        assets: tuple[ResearchAsset, ...],
        services: ResearchApplicationServices,
        budget_ledger: BudgetLedger,
    ) -> None:
        self.controller = controller
        self.brief = brief
        self.assets = tuple(assets)
        self.services = services
        self.budget_ledger = budget_ledger

    @classmethod
    def create(
        cls,
        brief: ResearchBrief,
        *,
        root: str | Path,
        services: ResearchApplicationServices | None = None,
    ) -> "ResearchApplication":
        settings = services or ResearchApplicationServices()
        assets = _normalize_assets(brief, settings)
        diagnostics = validate_brief(brief, assets)
        _raise_on_errors(diagnostics)
        session_root = Path(root).expanduser().absolute()
        controller = SessionController.create(
            session_root,
            session_id=session_root.name or "research-session",
            topic=(brief.objective or brief.request_text).strip()[:500],
            registry=_research_registry(),
            budget=BudgetState(
                max_attempts=settings.max_attempts,
                max_no_progress=settings.max_no_progress,
            ),
        )
        ledger = BudgetLedger(
            settings.budget_limits,
            storage_path=session_root / "budget_ledger.json",
        )
        settings = _bind_budget_services(settings, ledger, controller.manifest.session_id)
        app = cls(controller, brief, assets, settings, ledger)
        with controller.mutation_scope():
            ledger.save()
            controller.manifest.budget_ledger_ref = controller.store.ref(
                "budget_ledger.json",
                kind="budget_ledger",
                schema=BudgetLedger.schema_version,
                producer="research_application",
            )
            controller.save()
            app._persist_inputs(diagnostics)
        return app

    @classmethod
    def load(
        cls,
        root: str | Path,
        *,
        services: ResearchApplicationServices | None = None,
    ) -> "ResearchApplication":
        settings = services or ResearchApplicationServices()
        controller = SessionController.load(root, registry=_research_registry())
        try:
            brief_payload = controller.store.read_json(controller.manifest.state_refs["brief"])
            assets_payload = controller.store.read_json(controller.manifest.state_refs["assets"])
        except (KeyError, FileNotFoundError, OSError, ValueError) as exc:
            raise ResearchApplicationError(f"Application inputs are incomplete: {exc}") from exc
        if not isinstance(brief_payload, Mapping) or not isinstance(assets_payload, Mapping):
            raise ResearchApplicationError("Application inputs must be JSON objects.")
        rows = assets_payload.get("assets", [])
        if not isinstance(rows, list):
            raise ResearchApplicationError("Persisted assets must be a list.")
        try:
            brief = ResearchBrief.from_dict(brief_payload)
            if any(not isinstance(row, Mapping) for row in rows):
                raise ValueError("Persisted asset entries must be JSON objects.")
            assets = tuple(ResearchAsset.from_dict(row) for row in rows)
        except (TypeError, ValueError) as exc:
            raise ResearchApplicationError(f"Could not restore application inputs: {exc}") from exc
        runtime = _read_runtime_config(controller)
        config = dict(runtime["config"])
        config.update(settings.config)
        settings = replace(
            settings,
            max_results=runtime["max_results"],
            max_chunks=runtime["max_chunks"],
            idea_limit=runtime["idea_limit"],
            budget_limits=dict(runtime.get("budget_limits", settings.budget_limits)),
            config=config,
        )
        try:
            if controller.manifest.budget_ledger_ref is None:
                ledger = BudgetLedger(
                    settings.budget_limits,
                    storage_path=controller.store.root / "budget_ledger.json",
                )
                with controller.mutation_scope():
                    ledger.save()
                    controller.manifest.budget_ledger_ref = controller.store.ref(
                        "budget_ledger.json",
                        kind="budget_ledger",
                        schema=BudgetLedger.schema_version,
                        producer="research_application",
                    )
                    controller.save()
            else:
                ledger = BudgetLedger.load(
                    controller.store.root / controller.manifest.budget_ledger_ref.path
                )
        except (BudgetError, OSError, RuntimeError, ValueError) as exc:
            raise ResearchApplicationError(f"Could not restore application budget ledger: {exc}") from exc
        settings = _bind_budget_services(settings, ledger, controller.manifest.session_id)
        return cls(controller, brief, assets, settings, ledger)

    def view(self) -> ResearchApplicationView:
        manifest = self.controller.manifest
        diagnostics = list(self._input_diagnostics())
        if manifest.status_reason.strip():
            diagnostics.append(manifest.status_reason.strip())
        return ResearchApplicationView(
            session_root=self.controller.store.root,
            session_id=manifest.session_id,
            topic=manifest.topic,
            status=manifest.status,
            status_reason=manifest.status_reason,
            revision=manifest.revision,
            requested_outputs=self.brief.requested_outputs,
            next_action=self._next_action(),
            state_refs=dict(manifest.state_refs),
            attempts=tuple(item.to_dict() for item in self.controller.list_attempts()),
            budget=manifest.budget.to_dict(),
            diagnostics=tuple(_unique(diagnostics)),
            work_plan=self._build_work_plan(),
            parent_session=manifest.parent_session,
        )

    def advance(self, *, max_actions: int = 1) -> ResearchApplicationView:
        """Run at most ``max_actions`` actions and return current state."""
        if max_actions < 1:
            raise ValueError("max_actions must be positive.")
        with self.controller.mutation_scope():
            self._reconcile_running_attempt()
            terminal = {attempt.attempt_id for attempt in self.controller.list_attempts()
                        if attempt.status in {"completed", "failed"}}
            for entry in self.budget_ledger.entries:
                if entry.status == "reserved" and entry.attempt_id in terminal:
                    self.budget_ledger.mark_unknown(
                        entry.reservation_id, retain_reservation=True,
                        reason="Attempt ended without persisted usage; retain its reservation, not zero consumption.",
                    )
            if self.controller.manifest.status in {"paused", "blocked", "completed"}:
                return self.view()
            for _ in range(max_actions):
                action = self._next_action()
                if action is None:
                    break
                if action == "summarize":
                    self._write_summary()
                    continue
                if not self._run_action(action):
                    break
            self._finish_available_work()
            return self.view()

    def continue_session(
        self,
        *,
        reason: str = "Continue the research application.",
        revised_brief: ResearchBrief | None = None,
        allow_no_progress_exhausted: bool = False,
    ) -> ResearchApplicationView:
        prepared = self._prepare_revision(revised_brief) if revised_brief else None
        if self.controller.manifest.status == "completed" and prepared is None:
            raise ResearchApplicationError("A completed session requires a revised brief.")
        if self.controller.manifest.status == "paused" and prepared is None and self._next_action() is None:
            raise ResearchApplicationError("This paused session has no enabled next action.")
        with self.controller.mutation_scope():
            self.controller.continue_with_revision(
                reason,
                allow_no_progress_exhausted=allow_no_progress_exhausted,
            )
            if prepared is not None:
                self.brief, self.assets, diagnostics = prepared
                self.controller.manifest.current_attempt = None
                dynamic = {key for key in self.controller.manifest.state_refs if key.startswith(("repair_", "experiment_repair_", "matrix_"))}
                for key in _DERIVED_KEYS | {"diagnostics"} | dynamic:
                    self.controller.manifest.state_refs.pop(key, None)
                self._persist_inputs(diagnostics)
            else:
                self._persist_application_views()
            return self.view()

    def supply_execution(self, execution: Mapping[str, object], *, task_text: str = "") -> ResearchApplicationView:
        """Attach missing execution inputs; retain research evidence and resource limits."""
        if not self._requires_execution_output():
            raise ResearchApplicationError("This session has not requested experiments.")
        if self.services.config.get("execution") is not None:
            raise ResearchApplicationError("Execution is already configured; use an explicit experiment revision instead.")
        if self.controller.manifest.status != "paused":
            raise ResearchApplicationError("Supply execution to a paused session before continuing it.")
        execution_request(execution)  # Validate argv and location without launching a process.
        with self.controller.mutation_scope():
            self.controller.continue_with_revision("User supplied the missing experiment configuration; budgets unchanged.")
            self.services = replace(self.services, config={**self.services.config, "execution": dict(execution)})
            if task_text.strip():
                self.brief = replace(self.brief, request_text=self.brief.request_text + "\n\n## Implementation task\n\n" + task_text.strip(),
                                     revision=self.brief.revision + 1, parent_revision=self.brief.revision)
            self._persist_inputs(validate_brief(self.brief, self.assets))
            return self.view()

    def request_report(
        self,
        *,
        reason: str = "Request the report deliverable from the completed research evidence.",
    ) -> ResearchApplicationView:
        """Add the report deliverable without rerunning settled research work.

        This is the explicit continuation used by ``research-report`` for a
        canonical session created with ``--no-report``. It changes the
        requested deliverable only; existing evidence and measurements remain
        immutable and are reused by the report actions.
        """

        requested = {item.strip().lower() for item in self.brief.requested_outputs}
        if requested & {"report", "paper", "full_paper"}:
            return self.view()
        if self.controller.manifest.status == "running":
            raise ResearchApplicationError(
                "The research application is already running; report is already part of its active request."
            )
        with self.controller.mutation_scope():
            self.controller.continue_with_revision(reason)
            self.brief = replace(
                self.brief,
                revision=self.brief.revision + 1,
                parent_revision=self.brief.revision,
                requested_outputs=(*self.brief.requested_outputs, "report"),
            )
            diagnostics = validate_brief(self.brief, self.assets)
            _raise_on_errors(diagnostics)
            self._persist_inputs(diagnostics)
            return self.view()

    def retry_experiment(
        self,
        *,
        command: Sequence[str],
        cwd: str | Path,
        timeout_sec: int,
        result_schema: Mapping[str, object] | None = None,
        label: str = "experiment-retry",
        parent_attempt_id: str | None = None,
        reason: str = "Retry the failed explicit experiment with a caller-supplied correction.",
    ) -> ResearchApplicationView:
        """Retry one failed explicit experiment without rebuilding research evidence.

        This is intentionally narrower than a research loop: the caller owns
        the corrected argv, project directory and scientific reason. Only a
        technical ``failed``/``timed_out`` candidate in the simple explicit
        experiment path is eligible. Paired runs, prepared datasets and
        CodeTask repairs keep their dedicated bounded paths.
        """

        argv = tuple(command)
        if not argv or any(not isinstance(item, str) or not item.strip() for item in argv):
            raise ResearchApplicationError("Experiment retry requires a non-empty argv list.")
        retry_cwd = Path(cwd).expanduser().absolute()
        if not retry_cwd.is_dir():
            raise ResearchApplicationError(
                f"Experiment retry cwd is not an existing directory: {retry_cwd}"
            )
        if type(timeout_sec) is not int or timeout_sec < 1:
            raise ResearchApplicationError("Experiment retry timeout_sec must be a positive integer.")
        if not reason.strip():
            raise ResearchApplicationError("Experiment retry reason cannot be empty.")

        execution = self.services.config.get("execution")
        if not isinstance(execution, Mapping):
            raise ResearchApplicationError(
                "Experiment retry requires an explicit execution configuration."
            )
        if any(key in execution for key in ("pairs", "dataset", "code_task")) or "preparation" in self.controller.manifest.state_refs:
            raise ResearchApplicationError(
                "This retry surface only handles a simple explicit experiment; "
                "use the paired, preparation or CodeTask boundary for this session."
            )
        previous_ref = self.controller.manifest.state_refs.get("experiment")
        if previous_ref is None or any(
            key.startswith("experiment_repair_") for key in self.controller.manifest.state_refs
        ):
            raise ResearchApplicationError(
                "Only the latest simple explicit experiment can be retried through this boundary."
            )
        previous = self.controller.store.read_json(previous_ref)
        execution_status = str(
            previous.get("execution_status") or previous.get("status") or ""
        ).strip().lower()
        if execution_status not in {"failed", "timed_out"}:
            raise ResearchApplicationError(
                "Only a technical failed or timed-out experiment may be retried; "
                "a scientific negative result is evidence, not a retry request."
            )

        attempts = self.controller.list_attempts()
        running = [item.attempt_id for item in attempts if item.status == "running"]
        if running:
            raise ResearchApplicationError(
                "Cannot retry while an attempt is running: " + ", ".join(running)
            )
        parent_id = parent_attempt_id or _attempt_id_from_ref(previous_ref)
        parent = next((item for item in attempts if item.attempt_id == parent_id), None)
        if parent is None or parent.capability != "experiment":
            raise ResearchApplicationError(f"Retry parent experiment attempt not found: {parent_id}.")

        if self.controller.manifest.status == "running":
            # A completed experiment may leave the application running while
            # deterministic analysis is the next action. Settle that checkpoint
            # before creating the explicit revision.
            if self._next_action() == "analysis":
                self.advance(max_actions=1)
            if self.controller.manifest.status == "running":
                self.controller.pause("Preparing an explicit experiment retry.")
        if self.controller.manifest.status not in {"paused", "completed"}:
            raise ResearchApplicationError(
                f"Experiment retry requires a paused or completed session, got {self.controller.manifest.status!r}."
            )

        revised_execution = dict(execution)
        revised_execution["command"] = list(argv)
        revised_execution["cwd"] = str(retry_cwd)
        revised_execution["timeout_sec"] = timeout_sec
        if label.strip():
            revised_execution["label"] = label.strip()
        if result_schema:
            revised_execution["result_schema"] = _merge_execution_result_schema(
                revised_execution.get("result_schema"), result_schema
            )
        services_config = dict(self.services.config)
        services_config["execution"] = revised_execution
        self.services = replace(self.services, config=services_config)

        with self.controller.mutation_scope():
            self.controller.continue_with_revision(reason)
            self.brief = replace(
                self.brief,
                revision=self.brief.revision + 1,
                parent_revision=self.brief.revision,
            )
            diagnostics = validate_brief(self.brief, self.assets)
            _raise_on_errors(diagnostics)
            for name in ("experiment", "analysis", "comparison", "writer", "report", "report_audit"):
                self.controller.manifest.state_refs.pop(name, None)
            self._persist_inputs(diagnostics)
            request = execution_request(revised_execution)
            inputs = self._input_refs("design", "runtime_config")
            baseline_ref = self.controller.manifest.state_refs.get("baseline")
            if baseline_ref is not None:
                inputs = (*inputs, baseline_ref)
            # The previous ref remains an explicit input after its state slot is
            # replaced, so the new attempt records its recovery lineage.
            inputs = (*inputs, previous_ref)
            self._execute(
                "experiment", "experiment",
                request, inputs,
                backend=LocalExecutionBackend(budget_ledger=self.budget_ledger),
                parent_attempt_id=parent_id,
            )
        return self.advance(max_actions=1)

    def export_session(self) -> ArtifactRef:
        view = self.view()
        lines = [
            "# Research session snapshot", "",
            f"- Session: `{view.session_id}`", f"- Topic: {view.topic}",
            f"- Status: `{view.status}`", f"- Revision: {view.revision}",
            f"- Next action: `{view.next_action or 'none'}`", "",
            "## State artifacts", "",
            *[f"- `{name}`: `{ref.path}`" for name, ref in sorted(view.state_refs.items())],
            "", "## Attempts", "",
            *[f"- `{item.get('attempt_id', '')}` {item.get('capability', '')}: `{item.get('status', '')}`" for item in view.attempts],
        ]
        if view.diagnostics:
            lines.extend(["", "## Diagnostics", "", *[f"- {item}" for item in view.diagnostics]])
        result_ref = self.latest_experiment_ref()
        if result_ref is not None:
            result = self.controller.store.read_json(result_ref)
            lines.extend(["", "## Latest candidate measurement", "",
                          f"- Artifact: `{result_ref.path}`",
                          f"- Execution status: `{result['execution_status']}`",
                          "- Earlier measurements remain in State artifacts; completed means delivery, not scientific success."])
        with self.controller.mutation_scope():
            return self.controller.store.write_text(
                "outputs/session_snapshot.md", "\n".join(lines) + "\n",
                kind="session_snapshot", schema="session_snapshot.v1",
                producer="research_application",
            )

    def report_inputs(self):
        """Project accepted research facts into the existing Writer input contract."""
        from simple_ar.report.projection import build_research_report_inputs, attach_report_read_evidence, attach_implementation_evidence
        from simple_ar.research.analysis import AnalysisHandoff
        from simple_ar.research.design import ResearchDesignResult
        from simple_ar.report.schema import SourceHandle

        refs = self.controller.manifest.state_refs
        if not set(self.brief.requested_outputs) & {"experiment", "experiments"}:
            from simple_ar.report.projection import build_literature_report_inputs
            context, memory = build_literature_report_inputs(
                topic=self.brief.objective or self.brief.request_text, brief=self._load_synthesis(),
                search=self._load_search(), documents=self._load_documents(), brief_ref=refs["synthesis"],
            )
            return attach_report_read_evidence(context, memory, documents=self._load_documents(),
                                               read=self._load_read(), read_ref=refs["read"])
        analysis = AnalysisHandoff.from_handoff_dict(self._state_payload("analysis"))
        if "matrix_results" in refs:
            from simple_ar.report.projection import attach_paired_report_measurements
            if analysis.execution_ref != refs["matrix_results"]:
                raise ResearchApplicationError("Analyze the measurement collection before writing its report.")
            collection = self._state_payload("matrix_results")
            evidence_ref = self.controller.store.ref(Path(refs["analysis"].path).parent / "paired_analysis.json",
                kind="experiment_set_analysis", schema="experiment_set_analysis.v1", producer="research.analysis")
            evidence = self.controller.store.read_json(evidence_ref)
            # Comparisons stay in results as interpreted evidence; measured ledger
            # entries below come from their own canonical artifacts, not this projection.
            context, memory = build_research_report_inputs(
                topic=self._experiment_report_topic(), brief=self._load_synthesis(),
                search=self._load_search(), documents=self._load_documents(),
                execution={"status": evidence["status"], "metrics": {}}, analysis=analysis.analysis,
                brief_ref=refs["synthesis"], execution_ref=refs["matrix_results"], analysis_ref=refs["analysis"],
                design=ResearchDesignResult.from_handoff_dict(self._state_payload("design")), design_ref=refs["design"])
            context.results = evidence
            memory.limitations.append(f"Metrics describe candidate revision {evidence.get('candidate_revision', 0)}; "
                f"{len(evidence.get('superseded_candidates', []))} superseded candidate measurements are retained separately, not pooled.")
            measurements = []
            for pair in collection["pairs"]:
                for role in ("baseline", "candidate"):
                    if pair[role] is not None:
                        ref = ArtifactRef.from_dict(pair[role])
                        measurements.append((pair["seed"], role, ref, self.controller.store.read_json(ref)))
            context, memory = attach_paired_report_measurements(context, memory, measurements,
                comparisons=evidence["comparisons"], comparison_ref=evidence_ref, summaries=evidence.get("paired_summary", []))
            context.source_handles.append(SourceHandle(handle="artifact:paired_analysis", kind="experiment_set_analysis", artifact=evidence_ref.path))
            if collection.get("implementation_ref") is not None:
                implementation_ref = ArtifactRef.from_dict(collection["implementation_ref"])
                attach_implementation_evidence(context, self.controller.store, implementation_ref)
            memory.source_handles = list(context.source_handles)
            return attach_report_read_evidence(context, memory, documents=self._load_documents(),
                                               read=self._load_read(), read_ref=refs["read"])
        if analysis.execution_ref != self.latest_experiment_ref():
            raise ResearchApplicationError("Analyze the latest measurement before creating report inputs.")
        execution = dict(self.controller.store.read_json(analysis.execution_ref))
        if "baseline" in refs:
            execution["baseline"] = dict(self._state_payload("baseline"))
        if "comparison" in refs:
            execution["comparisons"] = [dict(self._state_payload("comparison"))]
        context, memory = build_research_report_inputs(
            topic=self._experiment_report_topic(), brief=self._load_synthesis(),
            search=self._load_search(), documents=self._load_documents(), execution=execution,
            analysis=analysis.analysis, brief_ref=refs["synthesis"], execution_ref=analysis.execution_ref,
            analysis_ref=refs["analysis"], design=ResearchDesignResult.from_handoff_dict(self._state_payload("design")),
            design_ref=refs["design"],
        )
        # The legacy context embedded baseline/comparison in one execution file.
        # New application measurements are independent immutable artifacts.
        metrics = [row.model_copy(update={"artifact": refs["baseline"].path})
                   if row.label == "baseline" and "baseline" in refs else
                   row.model_copy(update={"artifact": refs["comparison"].path})
                   if row.label == "comparison_delta" and "comparison" in refs else row
                   for row in context.metric_sources]
        handles = list(context.source_handles)
        for key in ("baseline", "comparison", "preparation"):
            if key in refs:
                handles.append(SourceHandle(handle=f"artifact:{key}", kind=key, artifact=refs[key].path))
        context.metric_sources, memory.metric_sources = metrics, metrics
        context.source_handles, memory.source_handles = handles, handles
        implementation_ref = next(
            (refs[state] for action, state in reversed(self._steps())
             if (action == "implement" or action.startswith("repair:")) and state in refs),
            None,
        )
        if implementation_ref is not None:
            attach_implementation_evidence(context, self.controller.store, implementation_ref)
            memory.source_handles = list(context.source_handles)
        if "experiment_contract" in execution:
            context.experiment_plan = dict(execution["experiment_contract"])
            memory.key_decisions.append("Experiment protocol comes from the measured execution; research design records motivation, not proof of implementation.")
        return attach_report_read_evidence(context, memory, documents=self._load_documents(),
                                           read=self._load_read(), read_ref=refs["read"])

    def _run_action(self, action: str) -> bool:
        if action == "plan":
            planner_mode = str(self._effective_config().get("research_plan_mode") or "llm").strip().lower()
            return self._execute(
                "plan", "plan",
                ResearchPlanRequest(
                    topic=self.controller.manifest.topic,
                    problem_markdown=self._problem_markdown(),
                    config=self._plan_config(),
                    default_query=self.controller.manifest.topic,
                    default_max_results=self.services.max_results,
                    use_llm=self.services.llm_client is not None and planner_mode != "deterministic",
                    llm_client=self.services.llm_client,
                ), self._input_refs("brief", "assets", "runtime_config")
            )
        if action == "search":
            plan = self._load_plan()
            accepted = self._execute(
                "search", "search",
                self._search_request(plan), self._input_refs("plan"),
                registry=self._provider_registry(),
                selection_policy=SearchSelectionPolicy(
                    topic=self.controller.manifest.topic, questions=plan.questions,
                    query_plan=plan.query_plan, max_documents=self._search_limit(plan),
                ), allow_partial=True,
            )
            if accepted and not self._load_search().selected_papers:
                self.controller.pause("Search returned no usable selected papers; revise the source or brief.")
                return False
            return accepted
        if action == "document_ingest":
            plan, search = self._load_plan(), self._load_search()
            if not search.selected_papers:
                self.controller.pause("Document ingest needs at least one selected paper.")
                return False
            accepted = self._execute(
                "document_ingest", "documents",
                DocumentIngestRequest(
                    papers=search.selected_papers, source_plan=plan.source_plan,
                    cache_dir=self._cache_dir("literature"), extraction_dir=self._extraction_dir(),
                    max_chunks=self.services.max_chunks,
                ), self._input_refs("plan", "search"), allow_partial=True,
            )
            if accepted:
                documents = self._load_documents()
                if not documents.records or not documents.chunks:
                    self.controller.pause("Document ingest produced no usable text; inspect extraction diagnostics.")
                    return False
            return accepted
        if action == "read":
            plan, documents = self._load_plan(), self._load_documents()
            return self._execute(
                "read", "read",
                ReadRequest(
                    bundle=documents, topic=self.controller.manifest.topic,
                    problem_markdown=self._problem_markdown(),
                    research_plan_json=json.dumps(plan.to_handoff_dict(), ensure_ascii=False, indent=2),
                    config=self._effective_config(), use_llm=self.services.llm_client is not None,
                    llm_client=self.services.llm_client,
                ), self._input_refs("plan", "documents"), allow_partial=True,
            )
        if action == "synthesize":
            plan, search, read = self._load_plan(), self._load_search(), self._load_read()
            return self._execute(
                "synthesize", "synthesis",
                SynthesisRequest(
                    evidence_pack=evidence_pack_from_read(
                        self.controller.manifest.topic, read,
                        coverage=search.coverage_report, source_plan=plan.source_plan.to_row(),
                        execution_context=self._problem_markdown(),
                    ), idea_limit=self.services.idea_limit,
                    use_llm=self.services.llm_client is not None, llm_client=self.services.llm_client,
                ), self._input_refs("plan", "search", "read", "brief", "runtime_config"), allow_partial=True,
            )
        if action == "assess_ideas":
            synthesis, read = self._load_synthesis(), self._load_read()
            return self._execute(
                "assess_ideas", "assessment",
                IdeaAssessmentRequest(
                    candidates=synthesis.ideas,
                    novelty_checks=synthesis.novelty_checks,
                    available_evidence_refs=tuple(sorted(allowed_evidence_refs(
                        evidence_pack_from_read(self.controller.manifest.topic, read),
                    ))),
                    limit=self.services.idea_limit,
                    objective=self.brief.objective or self.brief.request_text,
                    constraints={"hard_constraints": list(self.brief.hard_constraints),
                                 "research_request": self._problem_markdown()},
                    evidence_chunks=tuple(read.bundle.chunks),
                    llm_client=self.services.llm_client,
                ), self._input_refs("synthesis", "read", "brief", "runtime_config"), allow_partial=True,
            )
        if action == "research_design":
            execution = self.services.config.get("execution")
            has_execution = isinstance(execution, Mapping)
            assessment = self.controller.store.read_json(self.controller.manifest.state_refs["assessment"])
            selected = self._effective_config().get("research_selected_idea_id")
            reason = "Explicit candidate selection from research_selected_idea_id."
            if not selected:
                # These optional fields are absent in the first v1 assessments.
                selected = assessment.get("recommended_idea_id")
                reason = assessment.get("recommendation_reason", "")
            if not selected and self.services.llm_client is not None:
                self.controller.pause("No model recommendation is available. Review idea_comparison and select a candidate before design.")
                self._persist_application_views()
                return False
            if not selected:
                reason = "Deterministic execution-readiness selection; scientific preference has not been assessed."
            return self._execute(
                "research_design", "design",
                ResearchDesignRequest(
                    synthesis=self._load_synthesis(), topic=self.brief.objective or self.brief.request_text,
                    idea_id=str(selected) if selected else None, selection_rationale=reason,
                    execution_context=self._problem_markdown() if has_execution or self.services.config.get("research_execution_context") else "",
                    execution_schema=execution.get("result_schema", {}) if has_execution else {},
                ), self._input_refs("synthesis", "assessment", "brief", "runtime_config"), allow_partial=True,
            )
        if action == "prepare_execution":
            config = dict(self._effective_config()["execution"])
            if "code_task" in config and self._state_payload("design").get("contract") is None:
                self.controller.pause("CodeTask preparation requires a selected research design contract; review the candidate assessment before running its experiment matrix.")
                self._persist_application_views()
                return False
            try:
                run = None
                if "dataset" not in config:
                    config.setdefault("cwd", config["code_task"]["code_root"])
                    run = execution_request(config).run
                repair_limit(config)
            except ValueError as exc:
                self.controller.pause(str(exc))
                return False
            return self._execute(
                "prepare_execution", "preparation",
                PreparationRequest(config, self._problem_markdown(), run),
                self._input_refs("brief", "design", "runtime_config"),
            )
        if action == "implement" or action.startswith(("repair:", "matrix_repair_")):
            try:
                request = implementation_request(self._effective_config()["execution"], self.services.llm_client)
            except ValueError as exc:
                self.controller.pause(str(exc))
                return False
            baseline = self.controller.manifest.state_refs.get("baseline")
            pairs = execution_pairs(self._effective_config()["execution"])
            matrix_baselines = tuple(self.controller.manifest.state_refs[f"matrix_baseline_{i}"] for i in range(len(pairs)))
            if any(self.controller.store.read_json(ref)["status"] != "passed" for ref in matrix_baselines):
                self.controller.pause("A paired baseline failed; resolve its diagnostics before changing the candidate code.")
                return False
            if baseline and self._state_payload("baseline")["status"] != "passed":
                self.controller.pause("Baseline is not valid; resolve its diagnostics before implementing a candidate.")
                return False
            inputs = self._input_refs("design", "runtime_config") + ((baseline,) if baseline else ())
            inputs += matrix_baselines
            state_name = "implementation"
            if action.startswith("matrix_repair_"):
                revision = int(action.rsplit("_", 1)[1])
                failure_ref = self.controller.manifest.state_refs[self._matrix_failure(revision - 1, len(pairs))]
                request = replace(request, failure_ref=failure_ref)
                inputs += (failure_ref,)
                state_name = action
            if action.startswith("repair:"):
                index = int(action.split(":")[1])
                previous = "experiment" if index == 1 else f"experiment_repair_{index - 1}"
                failure_ref = self.controller.manifest.state_refs[previous]
                request = replace(request, failure_ref=failure_ref)
                inputs += (failure_ref,)
                state_name = f"repair_{index}"
            return self._execute(
                "implement", state_name,
                request, inputs,
            )
        if action == "matrix_analysis":
            collection_ref = self.controller.manifest.state_refs["matrix_results"]
            collection = self.controller.store.read_json(collection_ref)
            children = tuple(ArtifactRef.from_dict(row[role]) for row in collection["pairs"]
                             for role in ("baseline", "candidate") if row[role] is not None)
            return self._execute("analysis", "analysis",
                None, (collection_ref, *children), allow_partial=True, result_ref=collection_ref,
                analysis_context=self._analysis_context(), use_llm=False)
        if action in {"baseline", "experiment"} or action.startswith(("retest:", "matrix_baseline_", "matrix_candidate_")):
            try:
                matrix = action.startswith("matrix_")
                condition = "baseline" if action.startswith("matrix_baseline_") else action
                request = execution_request(self._effective_config().get("execution"), condition=condition,
                                            pair_index=int(action.rsplit("_", 1)[1]) if matrix else None)
                for resource in ("process_invocations", "process_wall_seconds"):
                    if self.budget_ledger.remaining(resource) is None:
                        raise ValueError(f"Execution requires an explicit finite {resource} budget.")
            except ValueError as exc:
                self.controller.pause(str(exc))
                self._persist_application_views()
                return False
            inputs = self._input_refs("design", "runtime_config")
            if "preparation" in self.controller.manifest.state_refs:
                inputs += self._input_refs("preparation")
            if action.startswith("matrix_candidate_") and "implementation" in self.controller.manifest.state_refs:
                revision = int(action.split("_r")[1].split("_")[0]) if "_r" in action else 0
                inputs += self._input_refs(f"matrix_repair_{revision}" if revision else "implementation")
            state_name = action
            if action.startswith("retest:"):
                index = int(action.split(":")[1])
                state_name = f"experiment_repair_{index}"
                inputs += self._input_refs(f"repair_{index}")
            return self._execute(
                "experiment", state_name,
                request, inputs,
                backend=LocalExecutionBackend(budget_ledger=self.budget_ledger),
            )
        if action == "analysis":
            baseline_ref = self.controller.manifest.state_refs.get("baseline")
            result_ref = self.latest_experiment_ref()
            if result_ref is None:
                raise ResearchApplicationError("Analysis requires an existing candidate measurement.")
            return self._execute(
                "analysis", "analysis",
                None, (result_ref, baseline_ref) if baseline_ref else (result_ref,),
                allow_partial=True, baseline_ref=baseline_ref,
                result_ref=result_ref,
                analysis_context=self._analysis_context(), use_llm=False,
            )
        if action == "report_write":
            from simple_ar.report.writing import ReportWritingRequest
            from simple_ar.report.schema import ReportRuntimeConfig
            from simple_ar.report.templates import load_report_template_bundle
            report_context, memory = self.report_inputs()
            config = ReportRuntimeConfig.model_validate(self._effective_config().get("report", {}))
            sources = tuple(ref for key, ref in self.controller.manifest.state_refs.items() if key not in {"work_plan", "work_plan_markdown", "readiness"})
            resume_ref = None
            for attempt in reversed(self.controller.list_attempts()):
                if attempt.capability == "report_write":
                    checkpoint_path = Path("attempts") / attempt.attempt_id / "sections.json"
                    if self.controller.store.resolve(checkpoint_path).is_file():
                        resume_ref = self.controller.store.ref(checkpoint_path, kind="report_checkpoint", schema="report_checkpoint.v1")
                        break
            return self._execute(
                "report_write", "writer",
                ReportWritingRequest(report_context, memory, config,
                                     load_report_template_bundle(report_mode=report_context.report_mode, config=config), self.services.llm_client, resume_ref),
                sources + ((resume_ref,) if resume_ref else ()),
                allow_no_progress_exhausted=True,
            )
        if action in {"report", "report_audit"}:
            from simple_ar.report.schema import ReportContext, ReportMemory
            from simple_ar.report.capability import ReportAssemblyRequest
            from simple_ar.report.audit import ReportAuditCapabilityRequest
            from simple_ar.report.projection import _append_verified_experiment_evidence
            writer = self._state_payload("writer")
            writer_ref = self.controller.manifest.state_refs["writer"]
            snapshot_ref = self.controller.store.ref(Path(writer_ref.path).parent / writer["input_snapshot"]["path"], kind="report_snapshot")
            snapshot = self.controller.store.read_json(snapshot_ref)
            report_context = ReportContext.model_validate(snapshot["context"])
            memory = ReportMemory.model_validate(writer["memory"])
            if action == "report":
                return self._execute("report", "report",
                    ReportAssemblyRequest(title=report_context.topic,
                        sections=_append_verified_experiment_evidence(tuple(writer["sections"]), report_context),
                        config=snapshot["config"], document_plan=memory.document_plan,
                        template_name=snapshot["template"]["name"], papers=tuple(report_context.papers),
                        citation_key_map=report_context.citation_key_map,
                        paired_comparisons=tuple(report_context.results.get("comparisons", [])) if "matrix_results" in self.controller.manifest.state_refs else (),
                        paired_summaries=tuple(report_context.results.get("paired_summary", []))), (writer_ref, snapshot_ref))
            report_ref = self.controller.manifest.state_refs["report"]
            body_ref = self.controller.store.ref(Path(report_ref.path).parent / "report_body.md", kind="report_body")
            cleanup_ref = self.controller.store.ref(Path(report_ref.path).parent / "citation_cleanup.json", kind="citation_cleanup")
            # Historical assembled reports predate the cleanup trace; do not invent one.
            if not self.controller.store.exists(cleanup_ref.path):
                cleanup_ref = None
            return self._execute("report_audit", "report_audit",
                ReportAuditCapabilityRequest(report_ref=report_ref, report_body_ref=body_ref, context=report_context, memory=memory,
                                             citation_cleanup_ref=cleanup_ref),
                (report_ref, body_ref, writer_ref, snapshot_ref) + ((cleanup_ref,) if cleanup_ref else ()),
                allow_partial=True)
        raise ResearchApplicationError(f"Unsupported application action: {action}")

    def _execute(
        self, capability: str, state_name: str,
        request: Any, inputs: tuple[ArtifactRef, ...], *, allow_partial: bool = False,
        parent_attempt_id: str | None = None,
        allow_no_progress_exhausted: bool = False,
        **kwargs: Any,
    ) -> bool:
        _, artifact_kind, _ = _CAPABILITY_OUTPUTS[capability]
        attempt_id = self.controller.allocate_attempt_id(
            capability,
            allow_no_progress_exhausted=allow_no_progress_exhausted,
        )
        request = self._request_for_attempt(request, attempt_id)
        if request is not None:
            kwargs["request"] = request
        result = self.controller.execute_attempt(
            capability, attempt_id=attempt_id, inputs=inputs, parent_attempt_id=parent_attempt_id,
            trigger=f"application:{state_name}",
            allow_no_progress_exhausted=allow_no_progress_exhausted,
            **kwargs,
        )
        accepted = {"completed", "partial"} if allow_partial else {"completed"}
        # Failed measurements and their diagnostic analyses remain outputs,
        # not implicit requests to rerun. Errors without artifacts still pause.
        measured_failure = capability in {"experiment", "analysis"} and result.status == "failed" and any(
            ref.kind == artifact_kind for ref in result.artifacts
        )
        if result.status not in accepted and not measured_failure:
            detail = "; ".join(str(item) for item in result.diagnostics if str(item).strip())
            self.controller.pause(f"{capability} returned {result.status!r}" + (f": {detail}" if detail else "."))
            return False
        self._record_attempt_outputs(capability, state_name, attempt_id, result)
        self._persist_application_views()
        return True

    def _record_attempt_outputs(self, capability: str, state_name: str, attempt_id: str, result: Any) -> None:
        """Bind the same declared outputs after normal execution or recovery."""

        _, kind, schema = _CAPABILITY_OUTPUTS[capability]
        outputs = [(state_name, kind, schema)]
        if capability == "assess_ideas":
            outputs.append(("idea_comparison", "idea_comparison", "idea_comparison.v1"))
        if capability == "analysis" and any(ref.kind == "experiment_comparison" for ref in result.artifacts):
            outputs.append(("comparison", "experiment_comparison", "experiment_comparison.v1"))
        try:
            refs = {
                name: self.controller.attempt_output_ref(attempt_id, kind=kind, schema=schema)
                for name, kind, schema in outputs
            }
        except (KeyError, ValueError) as exc:
            raise ResearchApplicationError(f"{capability} has incomplete declared outputs: {exc}") from exc
        self.controller.manifest.state_refs.update(refs)

    def _request_for_attempt(self, request: Any, attempt_id: str) -> Any:
        if isinstance(request, ExperimentRequest):
            return replace(request, run=replace(
                request.run, session_id=self.controller.manifest.session_id, attempt_id=attempt_id,
            ))
        client = getattr(request, "llm_client", None)
        binder = getattr(client, "with_budget", None) if client is not None else None
        if not callable(binder):
            return request
        return replace(
            request,
            llm_client=binder(
                self.budget_ledger,
                session_id=self.controller.manifest.session_id,
                attempt_id=attempt_id,
            ),
        )

    def _write_summary(self) -> None:
        synthesis, search = self._load_synthesis(), self._load_search()
        documents, read = self._load_documents(), self._load_read()
        summary_ref = self.controller.store.write_text(
            "outputs/research_summary.md",
            _render_summary(self.brief, synthesis, search, documents, read, self.controller.manifest.state_refs),
            kind="research_summary", schema="research_summary.v1", producer="research_application",
        )
        self.controller.manifest.state_refs["summary"] = summary_ref
        snapshot_ref = self.controller.store.write_json(
            "outputs/research_summary.json",
            {
                "schema_version": "research_summary.v1", "status": synthesis.status,
                "generation_mode": synthesis.generation_mode,
                "paper_count": len(search.papers), "selected_paper_count": len(search.selected_papers),
                "document_count": len(documents.records), "chunk_count": len(documents.chunks),
                "state_refs": {name: ref.to_dict() for name, ref in self.controller.manifest.state_refs.items()},
                "diagnostics": list(synthesis.diagnostics),
            }, kind="research_summary_snapshot", schema="research_summary.v1",
            producer="research_application",
        )
        self.controller.manifest.state_refs["summary_snapshot"] = snapshot_ref
        self._persist_application_views()

    def _finish_available_work(self) -> None:
        if self._next_action() is not None or self.controller.manifest.status in {"paused", "blocked", "completed"}:
            return
        missing = [name for name in _requested_outputs(self.brief)
                   if _output_state_name(name) not in self.controller.manifest.state_refs
                   and not (name in {"experiment", "experiments"} and "matrix_results" in self.controller.manifest.state_refs)]
        if missing:
            self.controller.pause("Research artifacts are ready; requested outputs are not connected yet: " + ", ".join(missing))
        else:
            self.controller.complete("Requested research artifacts are ready.")
        self._persist_application_views()

    def _persist_inputs(self, diagnostics: tuple[Diagnostic, ...]) -> None:
        revision, store = self.brief.revision, self.controller.store
        paths = write_intake_artifacts(self.brief, self.assets, store.root / "inputs", revision=revision)
        refs = {
            "brief": store.ref(
                paths["brief"].relative_to(store.root).as_posix(),
                kind="research_brief", schema="research_brief_input.v1", producer="research_intake"
            ),
            "assets": store.ref(
                paths["assets"].relative_to(store.root).as_posix(),
                kind="research_assets", schema="research_assets.v1", producer="research_intake"
            ),
            "brief_markdown": store.ref(
                paths["markdown"].relative_to(store.root).as_posix(),
                kind="research_brief_markdown", schema="research_brief_markdown.v1", producer="research_intake"
            ),
            "runtime_config": store.write_json(
                (
                    "inputs/runtime_config.json"
                    if self.controller.manifest.revision == 0
                    else f"inputs/runtime_config-r{self.controller.manifest.revision:04d}.json"
                ),
                {
                    "schema_version": "research_application_config.v1",
                    "config": _json_safe(self._effective_config()),
                    "max_results": self.services.max_results,
                    "max_chunks": self.services.max_chunks,
                    "idea_limit": self.services.idea_limit,
                    "budget_limits": _json_safe(self.services.budget_limits),
                }, kind="runtime_config", schema="research_application_config.v1",
                producer="research_application",
            ),
        }
        if diagnostics:
            refs["diagnostics"] = store.write_json(
                f"inputs/diagnostics-r{revision:04d}.json",
                {"schema_version": "research_input_diagnostics.v1", "diagnostics": [d.to_dict() for d in diagnostics]},
                kind="research_input_diagnostics", schema="research_input_diagnostics.v1",
                producer="research_intake",
            )
        else:
            self.controller.manifest.state_refs.pop("diagnostics", None)
        self.controller.manifest.state_refs.update(refs)
        self.controller.manifest.lifecycle_owner = "research_application"
        self.controller.manifest.status_reason = _diagnostic_text(d for d in diagnostics if d.severity != "error")
        self._persist_application_views()

    def _persist_application_views(self) -> None:
        """Persist one derived progress view after a state mutation."""

        execution = self._effective_config().get("execution")
        pairs = execution_pairs(execution) if isinstance(execution, Mapping) else ()
        if pairs:
            refs = self.controller.manifest.state_refs
            revision = max((int(key.rsplit("_", 1)[1]) for key in refs if key.startswith("matrix_repair_")), default=0)
            implementation_key = f"matrix_repair_{revision}" if revision else "implementation"
            # This is a collection of canonical refs, not another result/budget store.
            refs["matrix_results"] = self.controller.store.write_json(
                "outputs/experiment_set.json", {
                    "schema_version": "experiment_set.v1", "brief_revision": self.brief.revision,
                    "candidate_revision": revision,
                    "implementation_ref": refs[implementation_key].to_dict() if implementation_key in refs else None,
                    "superseded_candidates": [{"revision": old, "seed": row["seed"],
                        "candidate_ref": refs[_matrix_candidate_key(old, i)].to_dict()}
                        for old in range(revision) for i, row in enumerate(pairs) if _matrix_candidate_key(old, i) in refs],
                    "pairs": [{"seed": row["seed"], **{
                        role: refs[key].to_dict() if key in refs else None
                        for role, key in (("baseline", f"matrix_baseline_{i}"), ("candidate", _matrix_candidate_key(revision, i)))}}
                        for i, row in enumerate(pairs)],
                }, kind="experiment_set", schema="experiment_set.v1", producer="research_application",
            )
        plan = self._build_work_plan()
        store = self.controller.store
        refs = {
            "work_plan": store.write_json(
                "planning/work_plan.json", plan,
                kind="research_work_plan", schema="research_work_plan.v1",
                producer="research_application",
            ),
            "work_plan_markdown": store.write_text(
                "planning/work_plan.md", _render_work_plan(plan),
                kind="research_work_plan_markdown", schema="research_work_plan_markdown.v1",
                producer="research_application",
            ),
        }
        self.controller.manifest.state_refs.update(refs)
        self.controller.save()

    def latest_experiment_ref(self) -> ArtifactRef | None:
        """Read the last recorded candidate without overwriting history or status."""
        for action, state in reversed(self._steps()):
            if action == "experiment" or action.startswith("retest:"):
                ref = self.controller.manifest.state_refs.get(state)
                if ref is not None:
                    return ref
        return None

    def _build_work_plan(self) -> dict[str, Any]:
        refs = {
            name: ref.to_dict()
            for name, ref in self.controller.manifest.state_refs.items()
            if name not in _INPUT_REF_NAMES
            and name not in {"work_plan", "work_plan_markdown", "readiness"}
        }
        requested = _requested_outputs(self.brief)
        deliverables: list[dict[str, Any]] = []
        gaps: list[dict[str, str]] = []
        for output in requested:
            state_name = _output_state_name(output)
            artifact = self.controller.manifest.state_refs.get(state_name)
            matrix_coverage = None
            if output in {"experiment", "experiments"}:
                artifact = self.controller.manifest.state_refs.get("matrix_results") or self.latest_experiment_ref()
                if artifact is not None and artifact.kind == "experiment_set":
                    pairs = self.controller.store.read_json(artifact)["pairs"]
                    measured = sum(row[role] is not None for row in pairs for role in ("baseline", "candidate"))
                    matrix_coverage = (measured, 2 * len(pairs))
                    if measured == 0:
                        artifact = None
            if artifact is not None:
                status, reason = "satisfied", "The requested artifact is available."
                if matrix_coverage and matrix_coverage[0] < matrix_coverage[1]:
                    status, reason = "partial", f"{matrix_coverage[0]}/{matrix_coverage[1]} planned measurements are available."
            elif output in {"experiment", "experiments"}:
                if "execution" in self._effective_config():
                    status, reason = "pending", "An explicitly configured experiment has not been measured."
                else:
                    status, reason = "blocked", "An explicit execution specification is required."
                    gaps.append({"kind": "missing", "item": output, "reason": reason})
            elif output in {"report", "paper", "full_paper"}:
                status, reason = "pending", "Report writing, assembly and audit are pending."
            elif output in _EXECUTION_OUTPUTS:
                status, reason = "blocked", (
                    "This deliverable requires the CodeTask/report application boundary "
                    "and is not connected yet."
                )
                gaps.append({"kind": "incompatible", "item": output, "reason": reason})
            elif output in {"research_summary", "summary"}:
                status, reason = "pending", "The evidence-backed summary has not been generated."
            elif output in _ASSESSMENT_OUTPUTS:
                status, reason = "pending", "The candidate assessment has not been generated."
            elif output in _DESIGN_OUTPUTS:
                status, reason = "pending", "The experiment design has not been generated."
            else:
                status, reason = "blocked", f"No application capability provides requested output {output!r}."
                gaps.append({"kind": "incompatible", "item": output, "reason": reason})
            row: dict[str, Any] = {"name": output, "status": status, "reason": reason}
            if artifact is not None:
                row["artifact"] = artifact.to_dict()
            deliverables.append(row)

        next_action = self._next_action()
        if self.controller.manifest.status == "completed":
            status = "completed"
        elif self.controller.manifest.status in {"paused", "blocked"}:
            has_available_work = "summary" in self.controller.manifest.state_refs or any(
                row["status"] in {"satisfied", "partial"} for row in deliverables
            )
            status = "partial" if has_available_work else "blocked"
        else:
            status = "ready" if next_action else "partial"
        return {
            "schema_version": "research_work_plan.v1",
            "revision": self.brief.revision,
            "brief_ref": self.controller.manifest.state_refs["brief"].to_dict(),
            "requested_outputs": deliverables,
            "accepted_refs": refs,
            "gaps": gaps,
            "next_action": next_action,
            "milestones": ["evidence-backed research summary", "explicit implementation, experiment and analysis when requested", "audited report when requested"],
            "status": status,
            "stop_reason": self.controller.manifest.status_reason if self.controller.manifest.status in {"paused", "blocked"} else "",
            "pending_user_decision": self.controller.manifest.status_reason if self.controller.manifest.status == "paused" else "",
            "decision_ref": None,
            "input_fingerprint": _input_fingerprint(self.brief, self.assets),
        }


    def _prepare_revision(self, brief: ResearchBrief | None):
        if brief is None:
            return None
        candidate = replace(
            brief, revision=self.brief.revision + 1,
            parent_revision=self.brief.revision,
        )
        assets = _normalize_assets(candidate, self.services)
        diagnostics = validate_brief(candidate, assets)
        _raise_on_errors(diagnostics)
        return candidate, assets, diagnostics

    def _reconcile_running_attempt(self) -> None:
        attempts = self.controller.list_attempts()
        running = [item for item in attempts if item.status == "running"]
        if not running:
            # A process can stop after finalizing the attempt but before the
            # application saves its state ref. Reuse that result on reload.
            next_action = self._next_action() or ""
            capability = "analysis" if next_action == "matrix_analysis" else "implement" if next_action.startswith(("repair:", "matrix_repair_")) else (
                "experiment" if next_action == "baseline" or next_action.startswith(("retest:", "matrix_baseline_", "matrix_candidate_")) else next_action
            )
            running = [item for item in attempts
                       if item.attempt_id == self.controller.manifest.current_attempt
                       and item.capability == capability
                       and (item.status == "completed" or (
                           item.status == "failed" and item.capability in {"experiment", "analysis"}
                       ))
                       and self.controller.manifest.status == "running"]
            if not running:
                return
        if len(running) != 1:
            raise ResearchApplicationError("Session has multiple interrupted attempts to inspect.")
        attempt = running[0]
        result_path = self.controller.store.root / "attempts" / attempt.attempt_id / "capability_result.json"
        if not result_path.is_file():
            raise ResearchApplicationError(
                f"Attempt {attempt.attempt_id} has no persisted result; confirm it with recover_interrupted()."
            )
        result = self.controller.reconcile_attempt(attempt.attempt_id)
        measured_failure = attempt.capability in {"experiment", "analysis"} and result.status == "failed" and any(
            ref.kind == _CAPABILITY_OUTPUTS[attempt.capability][1]
            for ref in result.artifacts
        )
        if result.status not in {"completed", "partial"} and not measured_failure:
            self.controller.pause(f"Recovered {attempt.capability} as {result.status!r}: {'; '.join(result.diagnostics)}")
            return
        state = _CAPABILITY_OUTPUTS.get(attempt.capability or "")
        if state:
            state_name = (
                attempt.trigger.removeprefix("application:")
                if attempt.trigger.startswith("application:") else state[0]
            )
            self._record_attempt_outputs(attempt.capability, state_name, attempt.attempt_id, result)
            self.controller.save()

    def _next_action(self) -> str | None:
        for action, state_name in self._steps():
            if state_name not in self.controller.manifest.state_refs:
                return action
        return None

    def _steps(self) -> tuple[tuple[str, str], ...]:
        steps = list(_BASE_STEPS)
        steps.append(("summarize", "summary"))
        if self._requires_idea_assessment():
            steps.append(("assess_ideas", "assessment"))
        if self._requires_execution_output() or set(self.brief.requested_outputs) & _DESIGN_OUTPUTS:
            steps.append(("research_design", "design"))
        if set(self.brief.requested_outputs) & {"experiment", "experiments"}:
            execution = self._effective_config().get("execution")
            if self._needs_preparation():
                steps.append(("prepare_execution", "preparation"))
            pairs = execution_pairs(execution) if isinstance(execution, Mapping) else ()
            if pairs:
                steps.extend((f"matrix_baseline_{i}", f"matrix_baseline_{i}") for i in range(len(pairs)))
                if "code_task" in execution:
                    steps.append(("implement", "implementation"))
                for revision in range(repair_limit(execution) + 1):
                    for i in range(len(pairs)):
                        key = _matrix_candidate_key(revision, i)
                        steps.append((key, key))
                        if key in self.controller.manifest.state_refs and self._state_payload(key)["execution_status"] in {"failed", "timed_out"}:
                            break
                    if self._matrix_failure(revision, len(pairs)) is None or revision == repair_limit(execution):
                        break
                    key = f"matrix_repair_{revision + 1}"
                    steps.append((key, key))
                steps.append(("matrix_analysis", "analysis"))
                if set(self.brief.requested_outputs) & {"report", "paper", "full_paper"}:
                    steps.extend((("report_write", "writer"), ("report", "report"), ("report_audit", "report_audit")))
                return tuple(steps)
            if isinstance(execution, Mapping) and "baseline" in execution:
                steps.append(("baseline", "baseline"))
            if isinstance(execution, Mapping) and "code_task" in execution:
                steps.append(("implement", "implementation"))
            steps.append(("experiment", "experiment"))
            previous = "experiment"
            for index in range(1, repair_limit(execution) + 1) if isinstance(execution, Mapping) else ():
                if previous not in self.controller.manifest.state_refs:
                    break
                if self._state_payload(previous)["execution_status"] not in {"failed", "timed_out"}:
                    break
                steps.append((f"repair:{index}", f"repair_{index}"))
                previous = f"experiment_repair_{index}"
                steps.append((f"retest:{index}", previous))
            steps.append(("analysis", "analysis"))
        if set(self.brief.requested_outputs) & {"report", "paper", "full_paper"}:
            steps.extend((("report_write", "writer"), ("report", "report"), ("report_audit", "report_audit")))
        return tuple(steps)

    def _matrix_failure(self, revision: int, count: int) -> str | None:
        for i in range(count):
            key = _matrix_candidate_key(revision, i)
            if key in self.controller.manifest.state_refs and self._state_payload(key)["execution_status"] in {"failed", "timed_out"}:
                return key
        return None

    def _requires_execution_output(self) -> bool:
        return bool({item.lower().strip() for item in self.brief.requested_outputs} & _EXECUTION_OUTPUTS)

    def _requires_idea_assessment(self) -> bool:
        requested = {item.lower().strip() for item in self.brief.requested_outputs}
        intents = {item.lower().strip() for item in self.brief.intents}
        return bool(requested & (_EXECUTION_OUTPUTS | _ASSESSMENT_OUTPUTS | _DESIGN_OUTPUTS)) or bool(
            intents & {"assess", "assessment", "evaluate_idea", "idea_assessment"}
        )

    def _input_refs(self, *names: str) -> tuple[ArtifactRef, ...]:
        try:
            return tuple(self.controller.manifest.state_refs[name] for name in names)
        except KeyError as exc:
            raise ResearchApplicationError(f"Missing application input artifact: {exc.args[0]}") from exc

    def _plan_config(self) -> dict[str, object]:
        config, local_documents = self._effective_config(), self._local_documents()
        if local_documents:
            config.setdefault("research_sources", ["local_files"])
            config["research_local_documents"] = [str(path) for path in local_documents]
            config.setdefault("research_use_fulltext", True)
            config.setdefault("research_allow_pdf_download", False)
        config.setdefault("research_max_documents", self.services.max_results)
        return config

    def _search_request(self, plan: ResearchPlanResult):
        return replace(
            search_request_from_plan(plan),
            cache_dir=self._cache_dir("literature"),
            cache_enabled=plan.source_plan.cache_enabled,
        )

    def _provider_registry(self) -> SearchProviderRegistry:
        return self.services.search_registry or default_search_provider_registry(
            local_documents=(str(path) for path in self._local_documents())
        )

    def _search_limit(self, plan: ResearchPlanResult) -> int:
        value = plan.source_plan.budget.get("max_documents")
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else self.services.max_results

    def _load_plan(self) -> ResearchPlanResult:
        return ResearchPlanResult.from_handoff_dict(self._state_payload("plan"))

    def _load_search(self) -> SearchResult:
        return SearchResult.from_handoff_dict(self._state_payload("search"))

    def _load_documents(self) -> DocumentBundle:
        return DocumentBundle.from_handoff_dict(self._state_payload("documents"))

    def _load_read(self) -> ReadResult:
        return ReadResult.from_handoff_dict(self._state_payload("read"), bundle=self._load_documents())

    def _load_synthesis(self) -> SynthesisResult:
        return SynthesisResult.from_handoff_dict(self._state_payload("synthesis"))

    def _state_payload(self, name: str) -> Mapping[str, Any]:
        try:
            payload = self.controller.store.read_json(self.controller.manifest.state_refs[name])
        except KeyError as exc:
            raise ResearchApplicationError(f"Missing application state: {name}") from exc
        if not isinstance(payload, Mapping):
            raise ResearchApplicationError(f"Application state {name} is not a JSON object.")
        return payload

    def _local_documents(self) -> tuple[Path, ...]:
        return tuple(dict.fromkeys(
            Path(asset.locator) for asset in self.assets
            if asset.availability != "missing"
            and asset.role in {"paper", "document", "reference"}
            and Path(asset.locator).is_file()
            and Path(asset.locator).suffix.lower() in {".md", ".markdown", ".txt"}
        ))

    def _cache_dir(self, name: str) -> Path:
        return (self.services.cache_dir or self.controller.store.root / "cache") / name

    def _extraction_dir(self) -> Path:
        return self.services.extraction_dir or self.controller.store.root / "documents"

    def _effective_config(self) -> dict[str, object]:
        config = dict(self.services.config)
        if "preparation" in self.controller.manifest.state_refs:
            config["execution"] = dict(self._state_payload("preparation")["execution"])
        return config

    def _needs_preparation(self) -> bool:
        execution = self.services.config.get("execution")
        if isinstance(execution, Mapping) and "dataset" in execution:
            return True
        task = execution.get("code_task") if isinstance(execution, Mapping) else None
        return isinstance(task, Mapping) and "code_root" in task

    def _problem_markdown(self) -> str:
        lines = ["# Research Problem", "", self.brief.request_text.strip()]
        if self.brief.objective.strip():
            lines += ["", "## Objective", "", self.brief.objective.strip()]
        if self.brief.hard_constraints:
            lines += ["", "## Hard constraints", "", *[f"- {x}" for x in self.brief.hard_constraints]]
        if self.brief.preferences:
            lines += ["", "## Preferences", "", *[f"- {x}" for x in self.brief.preferences]]
        execution = self.services.config.get("execution")
        if isinstance(execution, Mapping):
            lines += ["", "## Prepared execution specification", "",
                      "Use this supplied project/data and evaluation boundary. Literature motivates compatible changes; "
                      "it does not authorize a different dataset, benchmark, or protected-file edit. "
                      "These are requested conditions, not measured results.",
                      json.dumps(dict(execution), ensure_ascii=False, separators=(",", ":"))]
        context = str(self._effective_config().get("research_execution_context") or "").strip()
        if context:
            lines += ["", "## Prepared Experiment Boundary (hard)", "", context[:8000]]
        return "\n".join(lines) + "\n"

    def _analysis_context(
        self, *, result_schema: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Project the selected design into the result-analysis contract.

        Analysis owns verdict computation, while the application owns the
        handoff from the research design to an observed execution. Keeping
        this projection here avoids a second persisted hypothesis store and
        makes the same context work for single and paired measurements.
        """

        objective = (self.brief.objective or self.brief.request_text).strip()
        context: dict[str, Any] = {
            "task_id": self.controller.manifest.session_id,
            "title": objective,
            "research_question": objective,
        }
        schema = dict(result_schema or {})
        execution = self._effective_config().get("execution")
        if not schema and isinstance(execution, Mapping):
            configured = execution.get("result_schema")
            if isinstance(configured, Mapping):
                schema = dict(configured)

        contract: dict[str, Any] = {}
        design_ref = self.controller.manifest.state_refs.get("design")
        design: Mapping[str, Any] = {}
        if design_ref is not None:
            payload = self.controller.store.read_json(design_ref)
            if isinstance(payload, Mapping):
                design = payload
                candidate = payload.get("contract")
                if isinstance(candidate, Mapping):
                    contract = dict(candidate)
        if not contract and isinstance(execution, Mapping):
            configured_contract = execution.get("protocol")
            if isinstance(configured_contract, Mapping):
                contract = dict(configured_contract)

        if contract:
            selected = design.get("selected_idea")
            selected_id = (
                selected.get("idea_id")
                if isinstance(selected, Mapping) and selected.get("idea_id")
                else contract.get("contract_id") or "research-hypothesis"
            )
            hypothesis = str(contract.get("hypothesis") or "").strip()
            if hypothesis:
                context["hypotheses"] = [{
                    "id": str(selected_id),
                    "statement": hypothesis,
                    "metric_refs": list(contract.get("metrics") or []),
                    "evidence": [
                        {"source": str(ref)}
                        for ref in contract.get("motivation_refs", [])
                        if str(ref).strip()
                    ],
                    "expected_outcome": str(contract.get("expected_outcome") or ""),
                }]
            context["task_contract"] = contract

        names: list[str] = []
        primary = str(schema.get("primary_metric") or "").strip()
        if primary:
            names.append(primary)
        required = schema.get("required_metrics")
        if isinstance(required, (list, tuple)):
            names.extend(str(name).strip() for name in required if str(name).strip())
        for row in contract.get("metric_specs", []):
            if isinstance(row, Mapping) and str(row.get("name") or "").strip():
                names.append(str(row["name"]).strip())
        names.extend(str(name).strip() for name in contract.get("metrics", []) if str(name).strip())
        names = list(dict.fromkeys(names))
        raw_directions = schema.get("metric_directions")
        raw_directions = raw_directions if isinstance(raw_directions, Mapping) else {}
        if names:
            context["expected_metrics"] = [
                {
                    "name": name,
                    "direction": normalize_direction(raw_directions.get(name)),
                }
                for name in names
            ]
        directions = {
            str(name): normalize_direction(value)
            for name, value in raw_directions.items()
            if str(name).strip()
        }
        if directions:
            context["metric_directions"] = directions
        return context

    def _experiment_report_topic(self) -> str:
        """Use the selected idea as the paper title when it has one."""
        design_ref = self.controller.manifest.state_refs.get("design")
        if design_ref is not None:
            design = self.controller.store.read_json(design_ref)
            selected = design.get("selected_idea") if isinstance(design, Mapping) else None
            title = selected.get("title") if isinstance(selected, Mapping) else None
            if str(title or "").strip():
                return str(title).strip()
        return (self.brief.objective or self.brief.request_text).strip()

    def _input_diagnostics(self) -> tuple[str, ...]:
        return tuple(d.message for asset in self.assets for d in asset.diagnostics)


def _matrix_candidate_key(revision: int, index: int) -> str:
    return f"matrix_candidate_r{revision}_{index}" if revision else f"matrix_candidate_{index}"


def _attempt_id_from_ref(ref: ArtifactRef) -> str:
    parts = Path(ref.path).parts
    try:
        index = parts.index("attempts")
        return parts[index + 1]
    except (ValueError, IndexError) as exc:
        raise ResearchApplicationError(
            f"Cannot determine the parent attempt from artifact path: {ref.path}"
        ) from exc


def _merge_execution_result_schema(
    original: object, override: Mapping[str, object]
) -> dict[str, object]:
    merged = dict(original) if isinstance(original, Mapping) else {}
    for key, value in override.items():
        if key == "required_metrics" and not value:
            continue
        if key == "metric_directions" and not value:
            continue
        merged[str(key)] = value
    return merged


def create_session(brief: ResearchBrief, *, root: str | Path, services: ResearchApplicationServices | None = None) -> ResearchApplication:
    return ResearchApplication.create(brief, root=root, services=services)


def load_session(root: str | Path, *, services: ResearchApplicationServices | None = None) -> ResearchApplication:
    return ResearchApplication.load(root, services=services)


def advance_session(root: str | Path, *, services: ResearchApplicationServices | None = None, max_actions: int = 1) -> ResearchApplicationView:
    return load_session(root, services=services).advance(max_actions=max_actions)


def continue_session(
    root: str | Path, *, services: ResearchApplicationServices | None = None,
    reason: str = "Continue the research application.", revised_brief: ResearchBrief | None = None,
) -> ResearchApplicationView:
    return load_session(root, services=services).continue_session(reason=reason, revised_brief=revised_brief)


def retry_experiment(
    root: str | Path, *, command: Sequence[str], cwd: str | Path, timeout_sec: int,
    result_schema: Mapping[str, object] | None = None, label: str = "experiment-retry",
    parent_attempt_id: str | None = None,
    reason: str = "Retry the failed explicit experiment with a caller-supplied correction.",
    services: ResearchApplicationServices | None = None,
) -> ResearchApplicationView:
    return load_session(root, services=services).retry_experiment(
        command=command, cwd=cwd, timeout_sec=timeout_sec, result_schema=result_schema,
        label=label, parent_attempt_id=parent_attempt_id, reason=reason,
    )


def export_session(root: str | Path, *, services: ResearchApplicationServices | None = None) -> ArtifactRef:
    return load_session(root, services=services).export_session()


def _research_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    register_research_capabilities(
        registry,
        names=tuple(_CAPABILITY_OUTPUTS),
    )
    return registry


def _normalize_assets(brief: ResearchBrief, services: ResearchApplicationServices) -> tuple[ResearchAsset, ...]:
    base = services.input_base_dir
    if base is None and brief.source_path:
        source = Path(brief.source_path)
        base = source.parent if source.suffix else source
    return normalize_assets(brief, input_base_dir=base or Path.cwd())


def _raise_on_errors(diagnostics: tuple[Diagnostic, ...]) -> None:
    errors = [item.message for item in diagnostics if item.severity == "error"]
    if errors:
        raise ResearchApplicationError("; ".join(errors))


def _read_runtime_config(controller: SessionController) -> Mapping[str, Any]:
    try:
        ref = controller.manifest.state_refs["runtime_config"]
        payload = controller.store.read_json(ref)
    except (KeyError, OSError, ValueError) as exc:
        raise ResearchApplicationError(f"Could not restore runtime configuration: {exc}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != "research_application_config.v1":
        raise ResearchApplicationError("Expected research_application_config.v1 configuration.")
    return payload


def _bind_budget_services(
    services: ResearchApplicationServices,
    ledger: BudgetLedger,
    session_id: str,
) -> ResearchApplicationServices:
    client = services.llm_client
    binder = getattr(client, "with_budget", None) if client is not None else None
    if callable(binder):
        client = binder(ledger, session_id=session_id)
        services = replace(services, llm_client=client)
    return services


def _requested_outputs(brief: ResearchBrief) -> tuple[str, ...]:
    outputs = _unique(brief.requested_outputs)
    return outputs or ("research_summary",)


def _output_state_name(output: str) -> str:
    normalized = output.strip().lower()
    if normalized in {"summary", "research_summary"}:
        return "summary"
    if normalized in {"assessment", "idea_assessment"}:
        return "assessment"
    if normalized == "idea_comparison":
        return "idea_comparison"
    if normalized in _DESIGN_OUTPUTS:
        return "design"
    if normalized == "experiments":
        return "experiment"
    if normalized in {"paper", "full_paper"}:
        return "report"
    return normalized


def _input_fingerprint(brief: ResearchBrief, assets: tuple[ResearchAsset, ...]) -> str:
    payload = {
        "brief": brief.to_dict(),
        "assets": [asset.to_dict() for asset in assets],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _render_work_plan(plan: Mapping[str, Any]) -> str:
    lines = [
        "# Research work plan",
        "",
        f"- Status: `{plan.get('status', 'unknown')}`",
        f"- Revision: `{plan.get('revision', '')}`",
        f"- Next action: `{plan.get('next_action') or 'none'}`",
        "",
        "## Requested outputs",
        "",
    ]
    outputs = plan.get("requested_outputs", [])
    lines.extend(
        f"- `{row.get('name', '')}`: **{row.get('status', 'unknown')}** — {row.get('reason', '')}"
        for row in outputs if isinstance(row, Mapping)
    )
    if not outputs:
        lines.append("- None declared.")
    gaps = plan.get("gaps", [])
    if gaps:
        lines.extend(["", "## Gaps", ""])
        lines.extend(
            f"- `{row.get('kind', 'unknown')}` `{row.get('item', '')}`: {row.get('reason', '')}"
            for row in gaps if isinstance(row, Mapping)
        )
    stop_reason = str(plan.get("stop_reason") or "").strip()
    if stop_reason:
        lines.extend(["", "## Stop reason", "", stop_reason])
    return "\n".join(lines) + "\n"


def _render_summary(
    brief: ResearchBrief, synthesis: SynthesisResult, search: SearchResult,
    documents: DocumentBundle, read: ReadResult, state_refs: Mapping[str, ArtifactRef],
) -> str:
    limitations = _unique(list(search.diagnostics) + list(read.diagnostics) + list(synthesis.diagnostics))
    lines = [
        "# Research Summary", "", "## Abstract", "", brief.objective or brief.request_text.strip(),
        "", "## Research question", "", brief.request_text.strip(), "", "## Evidence collection", "",
        f"- Raw papers returned: {len(search.papers)}",
        f"- Selected papers: {len(search.selected_papers)}",
        f"- Ingested documents: {len(documents.records)}",
        f"- Text chunks: {len(documents.chunks)}",
        f"- Evidence cards: {len(read.paper_cards)} paper, {len(read.claim_cards)} claim, {len(read.method_cards)} method, {len(read.dataset_cards)} dataset",
        "", "## Synthesis", "", synthesis.synthesis_markdown.strip() or "No synthesis prose was produced.",
        "", "## Proposed direction", "", synthesis.hypothesis_markdown.strip() or "No explicit hypothesis was produced.",
        "", "## Limitations", "", *[f"- {item}" for item in limitations or ("No capability limitation was reported.",)],
        "", "## Artifact trail", "",
        *[f"- `{name}`: `{ref.path}`" for name, ref in sorted(state_refs.items())],
    ]
    return "\n".join(lines) + "\n"


def _diagnostic_text(items: Any) -> str:
    return "; ".join(
        item.message if isinstance(item, Diagnostic) else str(item)
        for item in items
        if (item.message if isinstance(item, Diagnostic) else str(item)).strip()
    )


def _unique(items: Any) -> tuple[str, ...]:
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
    return value


__all__ = [
    "ResearchApplication", "ResearchApplicationError", "ResearchApplicationServices",
    "ResearchApplicationView", "advance_session", "continue_session", "create_session",
    "retry_experiment",
    "export_session", "load_session",
]
