"""Small research-design handoff between synthesis and experiment execution.

This module does not introduce a second experiment schema.  It selects and
checks the existing research-level contract so an application can make the
synthesis-to-experiment boundary explicit and inspectable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from simple_ar.core.capabilities import CapabilityContext, CapabilityResult
from simple_ar.research.contracts import (
    IdeaCandidate,
    NoveltyCheck,
    ResearchExperimentContract,
)
from simple_ar.research.synthesis import SynthesisResult


ResearchDesignStatus = Literal["ready", "needs_review", "blocked"]


@dataclass(frozen=True, slots=True)
class ResearchDesignRequest:
    """Input for one explicit synthesis-to-design handoff."""

    synthesis: SynthesisResult | Mapping[str, Any]
    idea_id: str | None = None

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
    diagnostics: tuple[str, ...] = ()

    def to_handoff_dict(self) -> dict[str, Any]:
        """Return the stable, compact design handoff."""

        return {
            "schema_version": "research_design.v1",
            "status": self.status,
            "source_synthesis_status": self.source_synthesis_status,
            "generation_mode": self.generation_mode,
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

    selected_idea, novelty_check = _select_idea(synthesis, request.idea_id)
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

    diagnostics = _contract_diagnostics(contract)
    return ResearchDesignResult(
        status="ready" if not diagnostics else "needs_review",
        contract=contract,
        selected_idea=selected_idea,
        novelty_check=novelty_check,
        evidence_refs=tuple(contract.motivation_refs),
        source_synthesis_status=synthesis.status,
        generation_mode="deterministic",
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
        selected = synthesis.ideas[0] if synthesis.ideas else None
    novelty = next(
        (
            check
            for check in synthesis.novelty_checks
            if selected is not None and check.idea_id == selected.idea_id
        ),
        None,
    )
    return selected, novelty


def _contract_diagnostics(contract: ResearchExperimentContract) -> list[str]:
    missing: list[str] = []
    for field_name, value in (
        ("hypothesis", contract.hypothesis),
        ("proposed_change", contract.proposed_change),
    ):
        if not str(value).strip():
            missing.append(field_name)
    if not missing:
        return []
    return [
        "Research design is missing required contract fields: "
        + ", ".join(missing)
        + "."
    ]


__all__ = [
    "ResearchDesignRequest",
    "ResearchDesignResult",
    "ResearchDesignStatus",
    "build_research_design",
    "run_research_design_capability",
]
