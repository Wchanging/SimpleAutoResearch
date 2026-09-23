"""Small research-design handoff between synthesis and experiment execution.

This module does not introduce a second experiment schema.  It selects and
checks the existing research-level contract so an application can make the
synthesis-to-experiment boundary explicit and inspectable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from typing import Any, Literal, Mapping

from simple_ar.core.capabilities import CapabilityContext, CapabilityResult
from simple_ar.integrations.llm import LLMError
from simple_ar.research.contracts import (
    IdeaCandidate,
    NoveltyCheck,
    ResearchExperimentContract,
    rank_idea_candidates,
)
from simple_ar.research.prompts import (
    RESEARCH_DESIGN_SYSTEM,
    research_design_user_prompt,
)
from simple_ar.research.synthesis import SynthesisResult


ResearchDesignStatus = Literal["ready", "needs_review", "blocked"]


@dataclass(frozen=True, slots=True)
class ResearchDesignRequest:
    """Input for one explicit synthesis-to-design handoff."""

    synthesis: SynthesisResult | Mapping[str, Any]
    topic: str = ""
    idea_id: str | None = None
    execution_schema: Mapping[str, Any] = field(default_factory=dict)
    execution_boundary: Mapping[str, Any] = field(default_factory=dict)
    entry_facts: Mapping[str, Any] = field(default_factory=dict)
    execution_context: str = ""
    use_llm: bool = False
    llm_client: Any | None = None
    selection_rationale: str = ""

    def __post_init__(self) -> None:
        if self.use_llm and self.llm_client is None:
            raise ValueError(
                "ResearchDesignRequest.llm_client is required when use_llm is true."
            )
        object.__setattr__(self, "execution_schema", dict(self.execution_schema))
        object.__setattr__(self, "execution_boundary", dict(self.execution_boundary))
        object.__setattr__(self, "entry_facts", dict(self.entry_facts))
        object.__setattr__(self, "execution_context", self.execution_context.strip())

    def normalized_synthesis(self) -> SynthesisResult:
        """Restore the typed synthesis boundary without invoking an LLM."""

        if isinstance(self.synthesis, SynthesisResult):
            return self.synthesis
        if isinstance(self.synthesis, Mapping):
            return SynthesisResult.from_handoff_dict(self.synthesis)
        raise TypeError("synthesis must be a SynthesisResult or handoff mapping")


@dataclass(frozen=True, slots=True)
class ResearchDesignResult:
    """Selected contract and diagnostics for one executable direction."""

    status: ResearchDesignStatus
    contract: ResearchExperimentContract | None
    selected_idea: IdeaCandidate | None = None
    novelty_check: NoveltyCheck | None = None
    evidence_refs: tuple[str, ...] = ()
    source_synthesis_status: str = ""
    generation_mode: str = "deterministic"
    selection_rationale: str = ""
    execution_protocol: dict[str, Any] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()

    def to_handoff_dict(self) -> dict[str, Any]:
        """Return the stable, compact design handoff."""

        return {
            "schema_version": "research_design.v1",
            "status": self.status,
            "source_synthesis_status": self.source_synthesis_status,
            "generation_mode": self.generation_mode,
            "selection_rationale": self.selection_rationale,
            "execution_protocol": dict(self.execution_protocol),
            "selected_idea": (
                self.selected_idea.to_row() if self.selected_idea is not None else None
            ),
            "novelty_check": (
                self.novelty_check.to_row() if self.novelty_check is not None else None
            ),
            "contract": self.contract.to_row() if self.contract is not None else None,
            "evidence_refs": list(self.evidence_refs),
            "diagnostics": list(self.diagnostics),
        }

    @classmethod
    def from_handoff_dict(cls, data: Mapping[str, Any]) -> "ResearchDesignResult":
        """Restore a design handoff without re-running synthesis or design."""

        if str(data.get("schema_version") or "") != "research_design.v1":
            raise ValueError("Expected a research_design.v1 object.")
        status = str(data.get("status") or "")
        if status not in {"ready", "needs_review", "blocked"}:
            raise ValueError(f"Unsupported research design status: {status!r}")
        selected_payload = data.get("selected_idea")
        novelty_payload = data.get("novelty_check")
        contract_payload = data.get("contract")
        return cls(
            status=status,  # type: ignore[arg-type]
            source_synthesis_status=str(data.get("source_synthesis_status") or ""),
            generation_mode=str(data.get("generation_mode") or "deterministic"),
            selection_rationale=str(data.get("selection_rationale") or ""),
            execution_protocol=(
                dict(data.get("execution_protocol"))
                if isinstance(data.get("execution_protocol"), Mapping)
                else {}
            ),
            selected_idea=(
                IdeaCandidate.from_row(selected_payload)
                if isinstance(selected_payload, Mapping)
                else None
            ),
            novelty_check=(
                NoveltyCheck.from_row(novelty_payload)
                if isinstance(novelty_payload, Mapping)
                else None
            ),
            contract=(
                ResearchExperimentContract.from_row(contract_payload)
                if isinstance(contract_payload, Mapping)
                else None
            ),
            evidence_refs=tuple(
                str(item) for item in data.get("evidence_refs", [])
            ),
            diagnostics=tuple(str(item) for item in data.get("diagnostics", [])),
        )


def build_research_design(request: ResearchDesignRequest) -> ResearchDesignResult:
    """Select one grounded idea and check the existing research contract.

    Missing information is surfaced as ``needs_review``.  The function never
    invents a command, baseline result, metric value, or implementation plan.
    """

    synthesis = request.normalized_synthesis()
    selected_for_validation = synthesis.status == "needs_review" and request.idea_id is not None
    if synthesis.status != "ready" and not selected_for_validation:
        return ResearchDesignResult(
            status="needs_review",
            contract=None,
            source_synthesis_status=synthesis.status,
            generation_mode="deterministic",
            diagnostics=(
                f"Synthesis handoff is {synthesis.status!r}; review it before design.",
                *synthesis.diagnostics,
            ),
        )

    contract = synthesis.experiment_contract
    if contract is None:
        return ResearchDesignResult(
            status="blocked",
            contract=None,
            source_synthesis_status=synthesis.status,
            generation_mode="deterministic",
            diagnostics=("Synthesis handoff has no experiment contract.",),
        )

    selection_rationale = request.selection_rationale
    generation_mode = "deterministic"
    proposed_protocol: Mapping[str, Any] | None = None
    if request.use_llm and synthesis.ideas:
        selected_idea, selection_rationale, proposed_protocol = _select_idea_with_llm(
            synthesis, request,
        )
        novelty_check = next(
            (
                check
                for check in synthesis.novelty_checks
                if check.idea_id == selected_idea.idea_id
            ),
            None,
        )
        generation_mode = "llm"
    else:
        selected_idea, novelty_check = _select_idea(synthesis, request.idea_id)
    if request.idea_id and selected_idea is not None and selected_idea.idea_id != request.idea_id:
        raise LLMError(
            f"LLM research design changed the explicitly selected idea: {request.idea_id!r}."
        )
    if selected_idea is not None:
        contract = synthesis.for_idea(selected_idea.idea_id).experiment_contract
        if contract is None:
            return ResearchDesignResult(
                status="blocked",
                contract=None,
                selected_idea=selected_idea,
                novelty_check=novelty_check,
                source_synthesis_status=synthesis.status,
                generation_mode="deterministic",
                diagnostics=("Selected idea has no experiment contract.",),
            )

    contract = _apply_execution_boundary(
        contract,
        execution_schema=request.execution_schema,
        execution_context=request.execution_context,
        prepared_execution=isinstance(request.execution_boundary.get("code_task"), Mapping),
    )

    execution_protocol = _resolve_execution_protocol(
        request,
        contract,
        proposed_protocol,
    )

    diagnostics = _contract_diagnostics(
        contract,
        execution_schema=request.execution_schema,
    )
    if selected_for_validation:
        diagnostics = [*synthesis.diagnostics,
                       "Selected for bounded validation; source synthesis still needs review, not scientific approval.",
                       *diagnostics]
    return ResearchDesignResult(
        status="ready" if not diagnostics else "needs_review",
        contract=contract,
        selected_idea=selected_idea,
        novelty_check=novelty_check,
        evidence_refs=tuple(contract.motivation_refs),
        source_synthesis_status=synthesis.status,
        generation_mode=generation_mode,
        selection_rationale=selection_rationale,
        execution_protocol=execution_protocol,
        diagnostics=tuple(diagnostics),
    )


def _apply_execution_boundary(
    contract: ResearchExperimentContract,
    *,
    execution_schema: Mapping[str, Any],
    execution_context: str,
    prepared_execution: bool,
) -> ResearchExperimentContract:
    """Make a prepared project the execution contract's source of truth.

    Literature-derived baseline and dataset hints are useful during synthesis,
    but they are not executable configuration once a caller supplies a
    prepared project boundary.  Keep the research hypothesis and provenance;
    replace only the fields that otherwise invite a downstream agent to
    substitute the prepared task.  The detailed boundary remains in the
    synthesis handoff for prompts and audit.
    """

    if not execution_context.strip():
        return contract
    raw_metrics = execution_schema.get("required_metrics")
    metrics = (
        [str(item).strip() for item in raw_metrics if str(item).strip()]
        if isinstance(raw_metrics, list)
        else []
    )
    metric_directions = execution_schema.get("metric_directions")
    metric_directions = metric_directions if isinstance(metric_directions, Mapping) else {}
    metric_specs = [dict(item) for item in contract.metric_specs]
    known_metrics = {
        str(item.get("name") or "").strip()
        for item in metric_specs
        if str(item.get("name") or "").strip()
    }
    for name in dict.fromkeys(metrics or contract.metrics):
        name = str(name).strip()
        if not name or name in known_metrics:
            continue
        spec: dict[str, Any] = {"name": name}
        direction = str(metric_directions.get(name) or "").strip()
        if direction:
            spec["direction"] = direction
        metric_specs.append(spec)
        known_metrics.add(name)
    boundary_fields: dict[str, Any] = {}
    if prepared_execution:
        # A prepared CodeTask supplies an executable project boundary, not
        # dataset, split, or evaluator facts.  Preserve only metrics that are
        # actually declared by the execution schema; leave other protocol
        # fields empty so measurement identity remains incomplete/unknown.
        boundary_fields = {"dataset": "unknown", "metric_specs": metric_specs}
    return replace(
        contract,
        metrics=list(dict.fromkeys(metrics or contract.metrics)),
        **boundary_fields,
    )


def run_research_design_capability(
    *,
    context: CapabilityContext,
    request: ResearchDesignRequest,
) -> CapabilityResult:
    """Persist one design handoff through the common capability envelope."""

    result = build_research_design(request)
    output = context.store.write_json(
        "research_design.json",
        result.to_handoff_dict(),
        kind="research_design",
        schema="research_design.v1",
        producer="research.design",
    )
    capability_status = {
        "ready": "completed",
        "needs_review": "partial",
        "blocked": "blocked",
    }[result.status]
    return CapabilityResult(
        status=capability_status,  # type: ignore[arg-type]
        artifacts=(output,),
        diagnostics=result.diagnostics,
        usage={"evidence_refs": len(result.evidence_refs)},
        provenance={
            "capability": "research_design",
            "result_schema": "research_design.v1",
            "generation_mode": result.generation_mode,
            "model": str(getattr(request.llm_client, "model", ""))
            if request.use_llm
            else "",
        },
    )


def _select_idea(
    synthesis: SynthesisResult,
    idea_id: str | None,
) -> tuple[IdeaCandidate | None, NoveltyCheck | None]:
    wanted = idea_id.strip() if idea_id is not None else ""
    if wanted:
        selected = next((idea for idea in synthesis.ideas if idea.idea_id == wanted), None)
        if selected is None:
            raise KeyError(f"Unknown synthesis idea: {wanted}")
    else:
        selected = rank_idea_candidates(synthesis.ideas)[0] if synthesis.ideas else None
    novelty = next(
        (
            check
            for check in synthesis.novelty_checks
            if selected is not None and check.idea_id == selected.idea_id
        ),
        None,
    )
    return selected, novelty


def _select_idea_with_llm(
    synthesis: SynthesisResult,
    request: ResearchDesignRequest,
) -> tuple[IdeaCandidate, str, dict[str, Any]]:
    """Select a persisted idea and propose a bounded execution protocol."""

    client = request.llm_client
    if client is None:
        raise LLMError("LLM research design was requested but no client was provided.")
    response = client.ask_json(
        RESEARCH_DESIGN_SYSTEM,
        research_design_user_prompt(
            research_context=_synthesis_context(synthesis, request.topic),
            ideas_json=json.dumps(
                [idea.to_row() for idea in synthesis.ideas],
                ensure_ascii=False,
            ),
            novelty_checks_json=json.dumps(
                [check.to_row() for check in synthesis.novelty_checks],
                ensure_ascii=False,
            ),
            contract_json=json.dumps(
                synthesis.experiment_contract.to_row()
                if synthesis.experiment_contract is not None
                else {},
                ensure_ascii=False,
            ),
            execution_context=request.execution_context,
            execution_boundary_json=json.dumps(
                dict(request.execution_boundary), ensure_ascii=False, default=str,
            ),
            entry_facts_json=json.dumps(
                dict(request.entry_facts), ensure_ascii=False, default=str,
            ),
            requested_idea_id=request.idea_id or "",
        ),
        label="research-design",
    )
    if not isinstance(response, Mapping):
        raise LLMError("LLM research design response must be a JSON object.")
    selected_id = response.get("selected_idea_id")
    rationale = response.get("rationale")
    if not isinstance(selected_id, str) or not selected_id.strip():
        raise LLMError("LLM research design response is missing selected_idea_id.")
    if not isinstance(rationale, str) or not rationale.strip():
        raise LLMError("LLM research design response is missing rationale.")
    selected = next(
        (idea for idea in synthesis.ideas if idea.idea_id == selected_id.strip()),
        None,
    )
    if selected is None:
        raise LLMError(
            f"LLM research design selected unknown idea: {selected_id.strip()!r}."
        )
    protocol = response.get("execution_protocol", {})
    if protocol is None:
        protocol = {}
    if not isinstance(protocol, Mapping):
        raise LLMError("LLM research design execution_protocol must be a JSON object.")
    return selected, rationale.strip(), dict(protocol)


def _resolve_execution_protocol(
    request: ResearchDesignRequest,
    contract: ResearchExperimentContract,
    proposed: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Ground a design proposal in inspected entry facts and explicit config."""

    boundary = dict(request.execution_boundary)
    facts = dict(request.entry_facts)
    if not boundary and not facts and not proposed:
        return {}

    protocol: dict[str, Any] = {}
    configured_command = boundary.get("command")
    if isinstance(configured_command, (list, tuple)) and configured_command:
        protocol["command"] = list(configured_command)
    else:
        benchmark = facts.get("benchmark_argv")
        if isinstance(benchmark, (list, tuple)) and benchmark:
            protocol["command"] = list(benchmark)

    configured_baseline = boundary.get("baseline")
    if isinstance(configured_baseline, Mapping) and isinstance(
        configured_baseline.get("command"), (list, tuple),
    ):
        protocol["baseline_command"] = list(configured_baseline["command"])

    for key in ("pairs", "seeds", "seed_count", "seed_flag", "baseline_policy", "baseline_ref"):
        if key in boundary:
            protocol[key] = boundary[key]
    if isinstance(boundary.get("result_schema"), Mapping):
        protocol["result_schema"] = dict(boundary["result_schema"])
    elif request.execution_schema:
        protocol["result_schema"] = dict(request.execution_schema)

    comparison_required = bool(
        protocol.get("pairs")
        or protocol.get("baseline_command")
    )
    if proposed:
        _validate_proposed_protocol(proposed, facts)
        for key in (
            "command", "baseline_command", "pairs", "seeds", "seed_count", "seed_flag",
            "baseline_policy", "result_schema", "comparison_required", "decision_reason",
            "stopping_criteria", "input_refs",
        ):
            if key in proposed:
                protocol[key] = proposed[key]
        if "comparison_required" in proposed:
            comparison_required = proposed["comparison_required"]

    # Explicit configuration is authoritative over the model proposal.
    for key in (
        "command", "pairs", "seeds", "seed_count", "seed_flag", "baseline_policy",
        "baseline_ref", "result_schema",
    ):
        if key in boundary:
            protocol[key] = boundary[key]
    if "result_schema" not in boundary and request.execution_schema:
        protocol["result_schema"] = dict(request.execution_schema)
    if isinstance(configured_baseline, Mapping):
        protocol["baseline_command"] = list(configured_baseline.get("command", ()))

    # Do not persist two competing ways of expressing the same accepted
    # condition.  A caller-supplied pairs/compact form wins; a model response
    # that conflicts with itself is rejected before this point.
    if "pairs" in boundary:
        for key in ("seeds", "seed_count", "seed_flag"):
            protocol.pop(key, None)
    elif any(key in boundary for key in ("seeds", "seed_count", "seed_flag")):
        protocol.pop("pairs", None)

    explicit_policy = str(boundary.get("baseline_policy") or "").strip().lower()
    explicit_comparison = bool(boundary.get("pairs") or isinstance(configured_baseline, Mapping))
    if explicit_policy == "skip":
        comparison_required = False
    elif explicit_policy in {"run", "reuse"} or explicit_comparison:
        comparison_required = True
    elif not isinstance(comparison_required, bool):
        raise LLMError("Research design comparison_required must be a boolean.")

    policy = str(protocol.get("baseline_policy") or "").strip().lower()
    if policy not in {"", "run", "skip", "reuse"}:
        raise LLMError("Research design proposed unsupported baseline_policy; expected run, skip or reuse.")
    if not explicit_policy:
        if explicit_comparison and policy == "skip":
            # A supplied pair/baseline is an explicit comparison request;
            # model output cannot silently disable it.
            policy = "run"
            protocol["baseline_policy"] = policy
        elif not explicit_comparison and policy == "skip":
            comparison_required = False
        elif policy in {"run", "reuse"}:
            comparison_required = True
    if not comparison_required:
        protocol["baseline_policy"] = "skip"
        default_reason = "The task and selected design do not require a comparison baseline."
    elif not policy:
        protocol["baseline_policy"] = "run"
        default_reason = "The selected design requires a comparison and no reusable result was supplied."
    else:
        default_reason = "The baseline policy was explicitly configured or proposed within the accepted boundary."
    protocol["comparison_required"] = comparison_required
    protocol["decision_reason"] = str(protocol.get("decision_reason") or default_reason).strip()
    if not protocol.get("input_refs"):
        protocol["input_refs"] = list(facts.get("input_refs") or contract.motivation_refs)
    if "stopping_criteria" not in protocol:
        protocol["stopping_criteria"] = []
    return protocol


def _validate_proposed_protocol(
    proposed: Mapping[str, Any],
    entry_facts: Mapping[str, Any],
) -> None:
    """Reject model authority outside the inspected command boundary."""

    allowed = {
        "command", "baseline_command", "pairs", "seeds", "seed_count", "seed_flag",
        "baseline_policy", "result_schema", "comparison_required", "decision_reason",
        "stopping_criteria", "input_refs",
    }
    unknown = set(proposed) - allowed
    if unknown:
        raise LLMError(
            "LLM research design proposed unsupported execution fields: "
            + ", ".join(sorted(unknown))
        )
    prefixes = [
        list(row) for row in entry_facts.get("authorized_argv_prefixes", [])
        if isinstance(row, (list, tuple)) and row
    ]
    for key in ("command", "baseline_command"):
        if key not in proposed:
            continue
        _validate_argv_within_boundary(proposed[key], key, prefixes)
    if "baseline_policy" in proposed and str(proposed["baseline_policy"]).strip().lower() not in {"run", "skip", "reuse"}:
        raise LLMError("LLM research design baseline_policy must be run, skip or reuse.")
    if "comparison_required" in proposed and type(proposed["comparison_required"]) is not bool:
        raise LLMError("LLM research design comparison_required must be a boolean.")
    if "decision_reason" in proposed and not isinstance(proposed["decision_reason"], str):
        raise LLMError("LLM research design decision_reason must be a string.")
    if "stopping_criteria" in proposed:
        criteria = proposed["stopping_criteria"]
        if not isinstance(criteria, list) or any(
            not isinstance(item, str) or not item.strip() for item in criteria
        ):
            raise LLMError("LLM research design stopping_criteria must be a list of non-empty strings.")
    if "result_schema" in proposed and not isinstance(proposed["result_schema"], Mapping):
        raise LLMError("LLM research design result_schema must be an object.")
    if "input_refs" in proposed:
        _validate_input_refs(proposed["input_refs"], entry_facts)
    if "pairs" in proposed and any(key in proposed for key in ("seeds", "seed_count", "seed_flag")):
        raise LLMError("LLM research design must choose pairs or compact seed settings, not both.")
    if "pairs" in proposed:
        pairs = proposed["pairs"]
        if not isinstance(pairs, list) or not pairs:
            raise LLMError("LLM research design pairs must be a non-empty list.")
        seen: set[int] = set()
        for row in pairs:
            if not isinstance(row, Mapping) or set(row) != {"seed", "baseline_command", "candidate_command"}:
                raise LLMError(
                    "LLM research design pairs require seed, baseline_command and candidate_command."
                )
            seed = row["seed"]
            if type(seed) is not int or seed in seen:
                raise LLMError("LLM research design pair seeds must be unique integers.")
            seen.add(seed)
            for key in ("baseline_command", "candidate_command"):
                _validate_argv_within_boundary(row[key], key, prefixes)
    for key in ("seeds",):
        if key in proposed:
            values = proposed[key]
            if not isinstance(values, list) or not values or any(type(item) is not int for item in values) or len(set(values)) != len(values):
                raise LLMError(f"LLM research design {key} must be a non-empty list of unique integers.")
    if "seed_count" in proposed and (type(proposed["seed_count"]) is not int or proposed["seed_count"] < 1):
        raise LLMError("LLM research design seed_count must be a positive integer.")
    if "seed_flag" in proposed and (not isinstance(proposed["seed_flag"], str) or not proposed["seed_flag"].strip()):
        raise LLMError("LLM research design seed_flag must be a non-empty string.")


def _validate_argv_within_boundary(
    argv: object,
    name: str,
    prefixes: list[list[str]],
) -> None:
    if not isinstance(argv, (list, tuple)) or not argv or any(
        not isinstance(arg, str) or not arg.strip() or any(token in arg for token in ("\x00", "\n", "\r"))
        for arg in argv
    ):
        raise LLMError(f"LLM research design {name} must be a literal argv list.")
    if not prefixes or not any(list(argv[:len(prefix)]) == prefix for prefix in prefixes):
        raise LLMError(
            f"LLM research design {name} is outside the inspected authorized entrypoint boundary."
        )


def _validate_input_refs(value: object, entry_facts: Mapping[str, Any]) -> None:
    if not isinstance(value, list) or not value:
        raise LLMError("LLM research design input_refs must be a non-empty list.")
    known = entry_facts.get("input_refs")
    if not isinstance(known, list) or any(item not in known for item in value):
        raise LLMError("LLM research design input_refs must refer to inspected entry inputs.")


def _synthesis_context(synthesis: SynthesisResult, topic: str = "") -> str:
    """Keep the original topic alongside the compact synthesis context."""

    topic_text = topic.strip()
    gap_text = synthesis.gap_summary.strip()
    if topic_text and gap_text:
        return f"Topic: {topic_text}\nEvidence gap summary: {gap_text}"
    return topic_text or gap_text or "the supplied research topic"


def _contract_diagnostics(
    contract: ResearchExperimentContract,
    *,
    execution_schema: Mapping[str, Any] | None = None,
) -> list[str]:
    missing: list[str] = []
    for field_name, value in (
        ("hypothesis", contract.hypothesis),
        ("proposed_change", contract.proposed_change),
    ):
        if not str(value).strip():
            missing.append(field_name)
    diagnostics: list[str] = []
    if missing:
        diagnostics.append(
            "Research design is missing required contract fields: "
            + ", ".join(missing)
            + "."
        )

    configured = dict(execution_schema or {})
    configured_metrics = _metric_names(configured)
    contract_metrics = {
        str(metric).strip()
        for metric in contract.metrics
        if str(metric).strip()
    }
    if contract_metrics and configured_metrics and not _metrics_overlap(
        contract_metrics,
        configured_metrics,
    ):
        diagnostics.append(
            "Research contract metrics do not overlap the configured execution metrics: "
            f"contract={sorted(contract_metrics)}, configured={sorted(configured_metrics)}."
        )
    return diagnostics


def _metric_names(schema: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    primary = str(schema.get("primary_metric") or "").strip()
    if primary:
        names.add(primary)
    required = schema.get("required_metrics")
    if isinstance(required, (list, tuple)):
        names.update(str(item).strip() for item in required if str(item).strip())
    directions = schema.get("metric_directions")
    if isinstance(directions, Mapping):
        names.update(str(name).strip() for name in directions if str(name).strip())
    return names


def _metrics_overlap(left: set[str], right: set[str]) -> bool:
    """Match metric labels conservatively across extracted prose and config."""

    normalized_left = {_metric_key(item) for item in left if _metric_key(item)}
    normalized_right = {_metric_key(item) for item in right if _metric_key(item)}
    return any(
        item == other or item in other or other in item
        for item in normalized_left
        for other in normalized_right
    )


def _metric_key(value: str) -> str:
    return "".join(char for char in value.lower() if char.isalnum())


__all__ = [
    "ResearchDesignRequest",
    "ResearchDesignResult",
    "ResearchDesignStatus",
    "build_research_design",
    "run_research_design_capability",
]
