"""Task-scoped accepted plans for the shared research application.

This module only describes and validates the small sequential plan consumed by
the application.  Attempts, budgets, and artifact storage remain owned by the
existing planning capability and :class:`SessionController`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import re
from typing import Any, Mapping

from simple_ar.integrations.llm import LLMError


SCHEMA_VERSION = "research_task_plan.v1"

_ACTION_CAPABILITIES = {
    "search": "search",
    "document_ingest": "document_ingest",
    "read": "read",
    "synthesize": "synthesize",
    "summarize": "summary",
    "assess_ideas": "assess_ideas",
    "research_design": "research_design",
    "prepare_execution": "prepare_execution",
    "implement": "implement",
    "baseline": "experiment",
    "experiment": "experiment",
    "analysis": "analysis",
    "matrix_analysis": "analysis",
    "report_write": "report_write",
    "report": "report",
    "report_audit": "report_audit",
}
_STEP_TEXT = {
    "search": ("Collect candidate sources from the accepted source plan.", "Selected papers and source diagnostics."),
    "document_ingest": ("Make selected source text available to the evidence reader.", "Records, chunks, and extraction limitations."),
    "read": ("Extract claims, methods, and limitations from available source text.", "Evidence cards and unresolved source claims."),
    "synthesize": ("Combine the evidence into an evidence-backed direction or summary.", "Synthesis status, candidate direction, and limitations."),
    "summarize": ("Persist the requested evidence-backed research summary.", "Summary artifact and its immutable source refs."),
    "assess_ideas": ("Compare candidate directions against evidence and constraints.", "Candidate assessment and unresolved evidence gaps."),
    "research_design": ("Turn the selected direction into the existing execution contract.", "Design contract and selection rationale."),
    "prepare_execution": ("Create the isolated CodeTask workspace from the supplied project.", "Prepared workspace, copy report, and edit scope."),
    "implement": ("Locate, scope, patch, review, and validate the requested code change.", "Patch diff, validation, and implementation lineage."),
    "baseline": ("Measure the explicitly configured baseline before a candidate.", "Canonical baseline measurement and diagnostics."),
    "experiment": ("Run the explicitly configured experiment or candidate measurement.", "Canonical measurement and execution status."),
    "analysis": ("Interpret the completed measurement using its paired artifacts.", "Analysis handoff, metrics, and limitations."),
    "matrix_analysis": ("Compare the accepted paired measurements.", "Paired comparison and measurement coverage."),
    "report_write": ("Write the report from the accepted evidence and measurements.", "Report sections and source lineage."),
    "report": ("Assemble the report without changing the underlying evidence.", "Assembled report and citation trace."),
    "report_audit": ("Audit the assembled report against its sources and protocol.", "Audit findings and semantic limitations."),
    "supplement_baseline": ("Measure the unchanged baseline for an evidence-driven supplement.", "Supplement baseline measurement and diagnostics."),
    "supplement_candidate": ("Measure the current candidate under the newly accepted condition.", "Supplement candidate measurement and diagnostics."),
    "reanalysis": ("Re-analyze the supplement together with its explicit paired evidence.", "Updated analysis, decision basis, and limitations."),
    "prepare_candidate": ("Create a fresh isolated workspace from the recorded original project for a candidate revision.", "Revision workspace lineage and copy report."),
    "revise_candidate": ("Apply and validate the analysis-directed candidate revision in the isolated workspace.", "Candidate revision patch, validation, and lineage."),
    "research_candidate": ("Measure the analysis-directed candidate revision under the accepted comparison condition.", "Candidate revision measurement and diagnostics."),
}
_ACTION_RE = re.compile(
    r"^(?:repair:\d+|retest:\d+|matrix_repair_\d+|matrix_baseline_\d+|"
    r"matrix_candidate(?:_r\d+)?_\d+|supplement_baseline:\d+|"
    r"supplement_candidate:\d+|reanalysis:\d+|prepare_candidate:\d+|"
    r"revise_candidate:\d+|research_candidate:\d+(?:_\d+)?)$"
)


@dataclass(frozen=True, slots=True)
class TaskPlanStep:
    """One logical step in the accepted sequential task plan."""

    step_id: str
    action: str
    capability: str
    state_name: str
    problem_solved: str
    observation: str
    condition: str = ""

    def to_dict(self) -> dict[str, Any]:
        row = {
            "step_id": self.step_id,
            "action": self.action,
            "capability": self.capability,
            "state_name": self.state_name,
            "problem_solved": self.problem_solved,
            "observation": self.observation,
        }
        if self.condition:
            row["condition"] = self.condition
        return row


@dataclass(frozen=True, slots=True)
class TaskPlanRequest:
    """Task context used to create one accepted plan."""

    task_kind: str
    goal: str
    request_text: str
    objective: str = ""
    hard_constraints: tuple[str, ...] = ()
    preferences: tuple[str, ...] = ()
    requested_outputs: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    assets: tuple[Mapping[str, Any], ...] = ()
    config: Mapping[str, object] = field(default_factory=dict)
    execution: Mapping[str, object] | None = None
    execution_protocol_accepted: bool = False
    use_llm: bool = False
    llm_client: Any | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.task_kind not in {"survey", "bug_fix", "research"}:
            raise ValueError(f"Unsupported task kind: {self.task_kind!r}")
        if not self.goal.strip() or not self.request_text.strip():
            raise ValueError("Task plan goal and request_text cannot be empty.")
        if self.use_llm and self.llm_client is None:
            raise ValueError("TaskPlanRequest.llm_client is required when use_llm is true.")
        object.__setattr__(self, "config", dict(self.config))
        object.__setattr__(self, "assets", tuple(dict(asset) for asset in self.assets))
        if self.execution is not None:
            object.__setattr__(self, "execution", dict(self.execution))


@dataclass(frozen=True, slots=True)
class TaskPlanResult:
    """Persisted plan and its producer metadata."""

    task_kind: str
    goal: str
    steps: tuple[TaskPlanStep, ...]
    mode: str
    model: str = ""
    assumptions: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    status: str = "accepted"

    def to_handoff_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "task_kind": self.task_kind,
            "goal": self.goal,
            "mode": self.mode,
            "model": self.model,
            "assumptions": list(self.assumptions),
            "diagnostics": list(self.diagnostics),
            "steps": [step.to_dict() for step in self.steps],
        }

    @classmethod
    def from_handoff_dict(cls, data: Mapping[str, Any]) -> "TaskPlanResult":
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Expected a {SCHEMA_VERSION} object.")
        rows = data.get("steps")
        if not isinstance(rows, list):
            raise ValueError("Accepted task plan steps must be a list.")
        steps = _normalize_steps(rows)
        if str(data.get("status") or "accepted") != "accepted":
            raise ValueError("Only accepted task plans can be dispatched.")
        task_kind = str(data.get("task_kind") or "research")
        goal = str(data.get("goal") or "").strip()
        if task_kind not in {"survey", "bug_fix", "research"} or not goal:
            raise ValueError("Accepted task plan has an invalid task kind or goal.")
        return cls(
            task_kind=task_kind,
            goal=goal,
            steps=steps,
            mode=str(data.get("mode") or "deterministic"),
            model=str(data.get("model") or ""),
            assumptions=_strings(data.get("assumptions")),
            diagnostics=_strings(data.get("diagnostics")),
        )


def build_task_plan(request: TaskPlanRequest) -> TaskPlanResult:
    """Build, validate, and return the plan that the application will consume."""

    defaults = default_task_steps(request)
    mode = "deterministic"
    model = ""
    rows: list[Mapping[str, Any]] = defaults
    if request.use_llm:
        client = request.llm_client
        if client is None:
            raise LLMError("Task planning was requested but no client was provided.")
        prompt = _llm_prompt(request, defaults)
        response = client.ask_json(
            """You are the task planner for a bounded research application.
Return JSON only. Propose a short sequential plan from the supplied
task, assets, constraints, and boundary preset. Do not invent
capabilities, processes, datasets, files, or parallel branches.
The application will validate and execute the plan.""",
            prompt,
            label="task-plan",
            max_output_tokens=_planning_output_tokens(request.config),
        )
        raw = response.get("steps") if isinstance(response, Mapping) else None
        if not isinstance(raw, list) or not raw:
            raise LLMError("Task-plan response did not contain a non-empty steps list.")
        rows = raw
        mode = "llm"
        model = str(getattr(client, "model", ""))

    steps = _normalize_steps(rows)
    try:
        _validate_sequence(request, steps)
    except ValueError as exc:
        if not request.use_llm:
            raise
        # A model may omit a required evidence handoff even when every
        # proposed action is otherwise valid.  Keep the authorization checks
        # strict, then use the small deterministic plan as the recovery path
        # instead of pausing a user session for a repairable omission.
        _validate_authorized_boundaries(request, steps)
        fallback_rows = default_task_steps(request)
        fallback_steps = _normalize_steps(fallback_rows)
        _validate_sequence(request, fallback_steps)
        steps = fallback_steps
        mode = "deterministic_fallback"
        diagnostics = (
            f"LLM task plan rejected ({exc}); used the validated deterministic plan.",
        )
    else:
        diagnostics = ()
    return TaskPlanResult(
        task_kind=request.task_kind,
        goal=request.goal.strip(),
        steps=steps,
        mode=mode,
        model=model,
        assumptions=tuple(str(item).strip() for item in request.hard_constraints if str(item).strip()),
        diagnostics=diagnostics,
    )


def default_task_steps(request: TaskPlanRequest) -> list[dict[str, Any]]:
    """Return explicit offline defaults; these are a planning seed, not dispatch."""

    if request.task_kind == "bug_fix":
        execution = request.execution or {}
        steps: list[dict[str, Any]] = []
        if not execution or _needs_preparation(execution):
            steps.append(_row("prepare_execution"))
        steps.append(_row("implement"))
        return steps

    if _provided_materials_only(request):
        # Supplied papers are an input boundary, not the output of a fake
        # search attempt.  The downstream reader and report contracts still
        # consume the canonical document bundle.
        steps = [
            _row("document_ingest"),
            _row("read"),
            _row("synthesize"),
            _row("summarize"),
        ]
    else:
        steps = [
            _row("search"),
            _row("document_ingest"),
            _row("read"),
            _row("synthesize"),
            _row("summarize"),
        ]
    requested = {str(item).strip().lower() for item in request.requested_outputs}
    intents = {str(item).strip().lower() for item in request.intents}
    needs_execution = bool(requested & {"experiment", "experiments", "code", "code_task"})
    needs_assessment = needs_execution or bool(requested & {"assessment", "idea_assessment", "idea_comparison", "design", "research_design"}) or bool(
        intents & {"assess", "assessment", "evaluate_idea", "idea_assessment"}
    )
    if needs_assessment:
        steps.append(_row("assess_ideas"))
    execution = request.execution or {}
    if needs_execution or requested & {"design", "research_design"}:
        steps.append(_row("research_design"))
    if needs_execution and not request.execution_protocol_accepted:
        # The first plan is intentionally a short fact/design checkpoint. The
        # same accepted-plan capability is invoked again after design supplies
        # the executable protocol.
        return steps
    if requested & {"experiment", "experiments"}:
        steps.extend(_execution_steps(execution))
    report_condition = "" if requested & {"report", "paper", "full_paper"} else "on_request:report"
    steps.extend([
        _row("report_write", condition=report_condition),
        _row("report", condition=report_condition),
        _row("report_audit", condition=report_condition),
    ])
    return steps


def append_research_followup(
    plan: TaskPlanResult,
    iteration: int,
    *,
    action: str = "supplement",
    pair_count: int = 0,
) -> TaskPlanResult:
    """Extend one accepted plan with one analysis-authorized research action.

    This reuses the accepted-plan artifact and existing experiment/analysis
    capabilities. It never creates a second lifecycle or a technical repair
    alias; the application calls it only after a persisted research decision.
    """

    if type(iteration) is not int or iteration < 1:
        raise ValueError("Research follow-up iteration must be a positive integer.")
    if action not in {"supplement", "revise_candidate"}:
        raise ValueError("Research follow-up action must be supplement or revise_candidate.")
    if type(pair_count) is not int or pair_count < 0:
        raise ValueError("Research follow-up pair_count must be a non-negative integer.")
    marker = f"reanalysis:{iteration}"
    if any(step.action == marker for step in plan.steps):
        return plan
    rows = [step.to_dict() for step in plan.steps]
    if action == "supplement":
        followup_rows = (
            _row(f"supplement_baseline:{iteration}", condition="on_decision:supplement"),
            _row(
                f"supplement_candidate:{iteration}",
                condition=f"after_success:baseline_supplement_{iteration}",
            ),
            _row(
                marker,
                condition=f"after_success:experiment_supplement_{iteration}",
            ),
        )
        message = f"Accepted one analysis-directed supplement round {iteration}."
    else:
        preparation = f"prepare_candidate:{iteration}"
        revision = f"revise_candidate:{iteration}"
        candidate_actions = (
            tuple(f"research_candidate:{iteration}_{index}" for index in range(pair_count))
            if pair_count else (f"research_candidate:{iteration}",)
        )
        candidate_rows = []
        previous = _state_name(revision)
        for candidate_action in candidate_actions:
            candidate_rows.append(_row(candidate_action, condition=f"after_success:{previous}"))
            previous = _state_name(candidate_action)
        followup_rows = (
            _row(preparation, condition="on_decision:revise_candidate"),
            _row(revision, condition=f"after_success:{_state_name(preparation)}"),
            *candidate_rows,
            _row(marker, condition=f"after_success:{previous}"),
        )
        message = f"Accepted one analysis-directed candidate revision round {iteration}."
    insertion = next(
        (index for index, row in enumerate(rows)
         if str(row.get("action") or "") in {"report_write", "report", "report_audit"}),
        len(rows),
    )
    rows[insertion:insertion] = followup_rows
    return replace(
        plan,
        steps=_normalize_steps(rows),
        diagnostics=(*plan.diagnostics, message),
    )


def _execution_steps(execution: Mapping[str, object]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    if _needs_preparation(execution):
        steps.append(_row("prepare_execution"))
    pairs = execution.get("pairs", ())
    if isinstance(pairs, (list, tuple)) and pairs:
        count = len(pairs)
        baseline_policy = str(execution.get("baseline_policy") or "run").strip().lower()
        if baseline_policy not in {"skip", "reuse"}:
            steps.extend(_row(f"matrix_baseline_{i}") for i in range(count))
        if isinstance(execution.get("code_task"), Mapping):
            steps.append(_row("implement"))
        limit = _repair_limit(execution)
        for revision in range(limit + 1):
            for index in range(count):
                action = _matrix_candidate_key(revision, index)
                condition = "" if revision == 0 and index == 0 else (
                    f"after_success:{_matrix_candidate_key(revision, index - 1)}"
                    if index else f"after_success:matrix_repair_{revision}"
                )
                steps.append(_row(action, condition=condition))
            if revision < limit:
                steps.append(_row(
                    f"matrix_repair_{revision + 1}",
                    condition=f"on_failure_prefix:{_matrix_candidate_key(revision, 0).rsplit('_', 1)[0]}",
                ))
        steps.append(_row("matrix_analysis"))
        return steps

    baseline_policy = str(execution.get("baseline_policy") or "").strip().lower()
    baseline_requested = baseline_policy == "run" or (
        "baseline" in execution and baseline_policy not in {"skip", "reuse"}
    )
    if baseline_requested:
        steps.append(_row("baseline"))
    if isinstance(execution.get("code_task"), Mapping):
        steps.append(_row("implement"))
    steps.append(_row("experiment"))
    previous = "experiment"
    for index in range(1, _repair_limit(execution) + 1):
        steps.append(_row(f"repair:{index}", condition=f"on_failure:{previous}"))
        current = f"experiment_repair_{index}"
        steps.append(_row(f"retest:{index}", condition=f"after_success:repair_{index}"))
        previous = current
    steps.append(_row("analysis"))
    return steps


def _row(action: str, *, condition: str = "") -> dict[str, Any]:
    problem, observation = _STEP_TEXT.get(action, ("Advance the accepted task plan.", "Inspect the declared capability result."))
    return {
        "step_id": action,
        "action": action,
        "capability": _capability(action),
        "state_name": _state_name(action),
        "problem_solved": problem,
        "observation": observation,
        "condition": condition,
    }


def _normalize_steps(rows: list[Any]) -> tuple[TaskPlanStep, ...]:
    steps: list[TaskPlanStep] = []
    seen: set[str] = set()
    state_seen: set[str] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ValueError(f"Task plan step {index + 1} must be an object.")
        action = str(raw.get("action") or "").strip()
        # The summary capability and its action have different public names.
        # Canonicalize that unambiguous spelling before validating the boundary.
        if action == "summary":
            action = "summarize"
        if action in {"plan", "task_plan"} or not (_is_known_action(action)):
            raise ValueError(f"Unsupported task plan action: {action!r}")
        step_id = str(raw.get("step_id") or action).strip()
        if not step_id or step_id in seen:
            raise ValueError(f"Task plan step ids must be unique: {step_id!r}")
        seen.add(step_id)
        expected_capability, expected_state = _capability(action), _state_name(action)
        capability = str(raw.get("capability") or expected_capability).strip()
        state_name = str(raw.get("state_name") or expected_state).strip()
        if capability != expected_capability or state_name != expected_state:
            raise ValueError(f"Task plan boundary mismatch for {action!r}.")
        if state_name in state_seen:
            raise ValueError(f"Task plan state names must be unique: {state_name!r}")
        state_seen.add(state_name)
        condition = str(raw.get("condition") or "").strip()
        if condition and not _valid_condition(condition):
            raise ValueError(f"Unsupported task plan condition: {condition!r}")
        problem, observation = _STEP_TEXT.get(action, ("Advance the accepted task plan.", "Inspect the declared capability result."))
        steps.append(TaskPlanStep(
            step_id=step_id,
            action=action,
            capability=capability,
            state_name=state_name,
            problem_solved=str(raw.get("problem_solved") or problem).strip(),
            observation=str(raw.get("observation") or observation).strip(),
            condition=condition,
        ))
    if not steps:
        raise ValueError("Accepted task plan cannot be empty.")
    return tuple(steps)


def _validate_sequence(request: TaskPlanRequest, steps: tuple[TaskPlanStep, ...]) -> None:
    actions = [step.action for step in steps]
    _validate_authorized_boundaries(request, steps)
    if request.task_kind == "bug_fix":
        if any(step.capability not in {"prepare_execution", "implement"} for step in steps) or "implement" not in actions:
            raise ValueError("Bug-fix plans may contain only preparation and implementation.")
        if any(action not in {"prepare_execution", "implement"} for action in actions):
            raise ValueError("Bug-fix plans may use only the preparation and implementation actions.")
        if "prepare_execution" in actions and actions.index("implement") < actions.index("prepare_execution"):
            raise ValueError("Bug-fix implementation must follow preparation.")
        return
    provided_only = "search" not in actions
    if provided_only and not request.config.get("research_local_documents"):
        raise ValueError("Omitting search requires supplied local documents.")
    required = {"document_ingest", "read", "synthesize"} if provided_only else {
        "search", "document_ingest", "read", "synthesize"
    }
    if not required <= set(actions):
        boundary = "provided-materials" if provided_only else "search-to-evidence"
        raise ValueError(f"Research plans must preserve the {boundary} boundary.")
    requested = {str(item).strip().lower() for item in request.requested_outputs}
    if (not requested or requested & {"summarize", "summary", "research_summary"}) and "summarize" not in actions:
        raise ValueError("The requested research summary is missing from the accepted plan.")
    dependencies = {
        "document_ingest": () if provided_only else ("search",),
        "read": ("document_ingest",), "synthesize": ("read",),
        "summarize": ("synthesize",), "assess_ideas": ("synthesize",), "research_design": ("synthesize", "assess_ideas"),
        "prepare_execution": ("research_design",), "baseline": ("research_design",), "experiment": ("research_design",),
        "analysis": ("experiment",), "matrix_analysis": ("matrix_candidate",), "report_write": ("synthesize",),
        "report": ("report_write",), "report_audit": ("report",),
    }
    for index, step in enumerate(steps):
        for dependency in dependencies.get(step.action, ()):
            if not any(_action_matches(item.action, dependency) for item in steps[:index]):
                raise ValueError(f"Task plan action {step.action!r} is missing prerequisite {dependency!r}.")
    if requested & {"experiment", "experiments"} and not request.execution_protocol_accepted and "research_design" in actions:
        return
    if requested & {"experiment", "experiments"} and not ("experiment" in actions or any(action.startswith("matrix_candidate") for action in actions)):
        raise ValueError("The requested experiment is missing from the accepted plan.")
    if requested & {"report", "paper", "full_paper"} and not request.execution_protocol_accepted and "research_design" in actions:
        return
    if requested & {"report", "paper", "full_paper"} and "report_audit" not in actions:
        raise ValueError("The requested report is missing its assembly/audit steps.")


def _provided_materials_only(request: TaskPlanRequest) -> bool:
    """Return whether the caller explicitly selected supplied-materials mode."""

    return bool(request.config.get("research_materials_only"))


def _validate_authorized_boundaries(
    request: TaskPlanRequest, steps: tuple[TaskPlanStep, ...],
) -> None:
    """Bind process actions to the configured protocol, not model discretion."""
    process_capabilities = {"prepare_execution", "implement", "experiment"}
    if request.task_kind == "survey" and any(step.capability in process_capabilities for step in steps):
        raise ValueError("Process action exceeds the authorized protocol for a survey.")
    permitted = {
        row["action"]: row.get("condition", "")
        for row in default_task_steps(request)
        if row["capability"] in process_capabilities
    }
    for step in steps:
        if step.capability in process_capabilities:
            if step.action not in permitted or step.condition != permitted[step.action]:
                raise ValueError(f"Process action exceeds the authorized protocol: {step.action}")
        if _provided_materials_only(request) and step.action == "search":
            raise ValueError("Provided-materials plans cannot authorize search.")
        if step.capability not in process_capabilities and step.condition:
            if step.action not in {"report_write", "report", "report_audit"} or step.condition != "on_request:report":
                raise ValueError(f"Unsupported delivery condition for {step.action}.")


def _action_matches(action: str, expected: str) -> bool:
    return action == expected or (expected == "experiment" and (action.startswith(("retest:", "matrix_candidate", "matrix_baseline_")))) or (expected == "matrix_candidate" and action.startswith("matrix_candidate"))


def _is_known_action(action: str) -> bool:
    return action in _ACTION_CAPABILITIES or bool(_ACTION_RE.match(action))


def _capability(action: str) -> str:
    if action in _ACTION_CAPABILITIES:
        return _ACTION_CAPABILITIES[action]
    if action.startswith(("repair:", "matrix_repair_")):
        return "implement"
    if action.startswith(("retest:", "matrix_baseline_", "matrix_candidate")):
        return "experiment"
    if action.startswith(("supplement_baseline:", "supplement_candidate:")):
        return "experiment"
    if action.startswith("prepare_candidate:"):
        return "prepare_execution"
    if action.startswith("revise_candidate:"):
        return "implement"
    if action.startswith("research_candidate:"):
        return "experiment"
    if action.startswith("reanalysis:"):
        return "analysis"
    raise ValueError(f"Unsupported task plan action: {action!r}")


def _state_name(action: str) -> str:
    aliases = {
        "document_ingest": "documents",
        "synthesize": "synthesis",
        "summarize": "summary",
        "assess_ideas": "assessment",
        "research_design": "design",
        "prepare_execution": "preparation",
        "implement": "implementation",
        "report_write": "writer",
        "matrix_analysis": "analysis",
    }
    if action in aliases:
        return aliases[action]
    if action.startswith("repair:"):
        return f"repair_{action.split(':', 1)[1]}"
    if action.startswith("retest:"):
        return f"experiment_repair_{action.split(':', 1)[1]}"
    if action.startswith("supplement_baseline:"):
        return f"baseline_supplement_{action.split(':', 1)[1]}"
    if action.startswith("supplement_candidate:"):
        return f"experiment_supplement_{action.split(':', 1)[1]}"
    if action.startswith("prepare_candidate:"):
        return f"preparation_r{action.split(':', 1)[1]}"
    if action.startswith("revise_candidate:"):
        return f"implementation_r{action.split(':', 1)[1]}"
    if action.startswith("research_candidate:"):
        suffix = action.split(":", 1)[1]
        return f"experiment_revision_{suffix}"
    if action.startswith("reanalysis:"):
        return f"analysis_r{action.split(':', 1)[1]}"
    return action


def _valid_condition(condition: str) -> bool:
    return condition.startswith(("on_failure:", "on_failure_prefix:", "after_success:", "on_request:", "on_decision:")) and bool(condition.split(":", 1)[1].strip())


def _needs_preparation(execution: Mapping[str, object]) -> bool:
    if "dataset" in execution:
        return True
    task = execution.get("code_task")
    return isinstance(task, Mapping) and "code_root" in task


def _repair_limit(execution: Mapping[str, object]) -> int:
    task = execution.get("code_task")
    value = task.get("max_repairs", 0) if isinstance(task, Mapping) else 0
    return value if type(value) is int and value >= 0 else 0


def _matrix_candidate_key(revision: int, index: int) -> str:
    return f"matrix_candidate_r{revision}_{index}" if revision else f"matrix_candidate_{index}"


def _planning_output_tokens(config: Mapping[str, object]) -> int | None:
    if config.get("research_task_planning_max_output_tokens") is None:
        return None
    try:
        value = int(config.get("research_task_planning_max_output_tokens", 1200))
    except (TypeError, ValueError):
        return 1200
    return value if value > 0 else 1200


def _llm_prompt(request: TaskPlanRequest, defaults: list[dict[str, Any]]) -> str:
    boundary = {
        "task_kind": request.task_kind,
        "goal": request.goal,
        "request": request.request_text,
        "objective": request.objective,
        "hard_constraints": list(request.hard_constraints),
        "preferences": list(request.preferences),
        "requested_outputs": list(request.requested_outputs),
        "intents": list(request.intents),
        "assets": [dict(asset) for asset in request.assets],
        "local_documents": request.config.get("research_local_documents", []),
        "execution_boundary": {
            "configured": request.execution is not None,
            "has_code_task": isinstance((request.execution or {}).get("code_task"), Mapping),
            "has_pairs": bool((request.execution or {}).get("pairs")),
            "has_dataset": "dataset" in (request.execution or {}),
            "repair_limit": _repair_limit(request.execution or {}),
        },
        "material_boundary": {
            "provided_materials_only": _provided_materials_only(request),
            "search_allowed": not _provided_materials_only(request),
            "supplied_asset_count": sum(
                1 for asset in request.assets
                if str(asset.get("role") or "").strip().lower() in {"paper", "document", "reference"}
            ),
        },
        "default_steps": defaults,
    }
    return (
        "Interpret this task into one short sequential accepted plan. The returned JSON must have "
        "a `steps` array. The defaults are a seed: choose the necessary sequential stages for "
        "the task, assets, and constraints. Use only actions present in the seed or its explicit "
        "bounded execution boundary; preserve each chosen action's capability and state_name. "
        "For a summary, use action `summarize` with capability/state_name `summary`. "
        "Do not invent dynamic indices, repair rounds, capabilities, processes, or parallel work. "
        "For provided_materials_only, begin with document_ingest over the supplied assets and do "
        "not add search. With supplied local documents you may omit search when the task calls "
        "for analysing those materials. Inputs are bound by capability adapters; do not invent "
        "input fields. Do not use `plan` or `task_plan` as a step: this planning attempt is "
        "already producing the accepted plan.\n\n"
        + json.dumps(boundary, ensure_ascii=False, indent=2, default=str)
    )


def _strings(value: object) -> tuple[str, ...]:
    return tuple(str(item).strip() for item in value if str(item).strip()) if isinstance(value, list) else ()


__all__ = ["SCHEMA_VERSION", "TaskPlanRequest", "TaskPlanResult", "TaskPlanStep", "append_research_followup", "build_task_plan", "default_task_steps"]
