"""Small research-design handoff between synthesis and experiment execution.

This module does not introduce a second experiment schema.  It selects and
checks the existing research-level contract so an application can make the
synthesis-to-experiment boundary explicit and inspectable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    execution_context: str = ""
    use_llm: bool = False
    llm_client: Any | None = None

    def __post_init__(self) -> None:
        if self.use_llm and self.llm_client is None:
            raise ValueError(
                "ResearchDesignRequest.llm_client is required when use_llm is true."
            )
        object.__setattr__(self, "execution_schema", dict(self.execution_schema))
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
    diagnostics: tuple[str, ...] = ()

    def to_handoff_dict(self) -> dict[str, Any]:
        """Return the stable, compact design handoff."""

        return {
            "schema_version": "research_design.v1",
            "status": self.status,
            "source_synthesis_status": self.source_synthesis_status,
            "generation_mode": self.generation_mode,
            "selection_rationale": self.selection_rationale,
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
    if synthesis.status != "ready":
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

    selection_rationale = ""
    generation_mode = "deterministic"
    if request.idea_id is not None or not request.use_llm or not synthesis.ideas:
        selected_idea, novelty_check = _select_idea(synthesis, request.idea_id)
    else:
        selected_idea, selection_rationale = _select_idea_with_llm(synthesis, request)
        novelty_check = next(
            (
                check
                for check in synthesis.novelty_checks
                if check.idea_id == selected_idea.idea_id
            ),
            None,
        )
        generation_mode = "llm"
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

    diagnostics = _contract_diagnostics(
        contract,
        execution_schema=request.execution_schema,
    )
    return ResearchDesignResult(
        status="ready" if not diagnostics else "needs_review",
        contract=contract,
        selected_idea=selected_idea,
        novelty_check=novelty_check,
        evidence_refs=tuple(contract.motivation_refs),
        source_synthesis_status=synthesis.status,
        generation_mode=generation_mode,
        selection_rationale=selection_rationale,
        diagnostics=tuple(diagnostics),
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
) -> tuple[IdeaCandidate, str]:
    """Select only from persisted candidates; never let the model create one."""

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
    return selected, rationale.strip()


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
