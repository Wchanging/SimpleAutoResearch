"""Standalone evidence-to-direction synthesis boundary.

The existing evidence derivation functions remain the implementation.  This
module gives callers one small request/result boundary without taking over
LLM synthesis or persistence policy.  A caller can use it with the expanded
evidence pack already assembled by the pipeline, or with a small fixture.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, Mapping

from simple_ar.core.capabilities import CapabilityContext, CapabilityResult
from simple_ar.research.contracts import (
    IdeaCandidate,
    NoveltyCheck,
    ResearchExperimentContract,
)
from simple_ar.research.evidence.derivation import (
    build_experiment_contract,
    build_gap_summary,
    build_idea_candidates,
    build_novelty_checks,
)


SynthesisStatus = Literal["ready", "needs_review"]


@dataclass(frozen=True, slots=True)
class SynthesisRequest:
    """Input for deterministic evidence synthesis.

    ``evidence_pack`` is the in-memory, expanded form produced by
    ``build_evidence_pack``.  Persisted compact packs intentionally contain
    references rather than duplicate card rows and should be hydrated by the
    caller before invoking this boundary.
    """

    evidence_pack: Mapping[str, Any]
    idea_limit: int = 3
    novelty_backend: str = "local"
    include_experiment_contract: bool = True

    def __post_init__(self) -> None:
        if self.idea_limit < 1:
            raise ValueError("idea_limit must be at least 1")
        if not self.novelty_backend.strip():
            raise ValueError("novelty_backend must not be empty")


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    """Structured direction and experiment handoff from one evidence pack."""

    status: SynthesisStatus
    gap_summary: str
    ideas: tuple[IdeaCandidate, ...]
    novelty_checks: tuple[NoveltyCheck, ...]
    experiment_contract: ResearchExperimentContract | None = None
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a compact JSON-serializable summary."""
        return {
            "schema_version": "synthesis_result.v1",
            "status": self.status,
            "idea_count": len(self.ideas),
            "novelty_check_count": len(self.novelty_checks),
            "has_experiment_contract": self.experiment_contract is not None,
            "diagnostics": list(self.diagnostics),
        }

    def to_handoff_dict(self) -> dict[str, Any]:
        """Return the bounded direction handoff for a downstream capability."""
        return {
            "schema_version": "synthesis_result.v1",
            "status": self.status,
            "gap_summary": self.gap_summary,
            "ideas": [idea.to_row() for idea in self.ideas],
            "novelty_checks": [check.to_row() for check in self.novelty_checks],
            "experiment_contract": (
                self.experiment_contract.to_row()
                if self.experiment_contract is not None
                else None
            ),
            "diagnostics": list(self.diagnostics),
        }

    def for_idea(self, idea_id: str) -> "SynthesisResult":
        """Return a handoff whose experiment contract follows one idea.

        Synthesis may expose several grounded ideas while the historical
        contract builder keeps the first one as the default.  Candidate
        execution must make that choice explicit; it must not silently run
        every candidate against the same hypothesis and proposed change.
        """

        wanted = idea_id.strip()
        if not wanted:
            raise ValueError("idea_id cannot be empty.")
        idea = next((item for item in self.ideas if item.idea_id == wanted), None)
        if idea is None:
            raise KeyError(f"Unknown synthesis idea: {wanted}")
        contract = self.experiment_contract
        if contract is None:
            return self
        selected_contract = replace(
            contract,
            contract_id=f"{contract.contract_id}/{idea.idea_id}",
            hypothesis=idea.hypothesis or contract.hypothesis,
            motivation_refs=list(idea.motivation_refs or contract.motivation_refs),
            dataset=(idea.required_datasets[0] if idea.required_datasets else contract.dataset),
            metrics=list(idea.metrics or contract.metrics),
            proposed_change=idea.proposed_change or contract.proposed_change,
            risks=list(idea.risks or contract.risks),
        )
        return replace(self, experiment_contract=selected_contract)

    @classmethod
    def from_handoff_dict(cls, data: Mapping[str, Any]) -> "SynthesisResult":
        """Restore a ``synthesis_result.v1`` without calling an LLM."""

        if str(data.get("schema_version") or "") != "synthesis_result.v1":
            raise ValueError("Expected a synthesis_result.v1 object.")
        status = str(data.get("status") or "")
        if status not in {"ready", "needs_review"}:
            raise ValueError(f"Unsupported synthesis handoff status: {status!r}")
        contract_payload = data.get("experiment_contract")
        contract = (
            ResearchExperimentContract.from_row(contract_payload)
            if isinstance(contract_payload, Mapping)
            else None
        )
        return cls(
            status=status,  # type: ignore[arg-type]
            gap_summary=str(data.get("gap_summary") or ""),
            ideas=tuple(
                IdeaCandidate.from_row(row)
                for row in _mapping_rows(data.get("ideas"))
            ),
            novelty_checks=tuple(
                NoveltyCheck.from_row(row)
                for row in _mapping_rows(data.get("novelty_checks"))
            ),
            experiment_contract=contract,
            diagnostics=tuple(str(item) for item in data.get("diagnostics", [])),
        )


def synthesize_evidence(request: SynthesisRequest) -> SynthesisResult:
    """Derive bounded ideas, novelty-risk hints, and an experiment handoff.

    This is deliberately deterministic and does not call an LLM, write files,
    or choose a report claim.  The existing stage-level LLM synthesis remains
    responsible for prose; this boundary supplies inspectable evidence-derived
    structure for later design and tool integrations.
    """
    pack = dict(request.evidence_pack)
    ideas = build_idea_candidates(pack, limit=request.idea_limit)
    novelty_checks = build_novelty_checks(
        ideas,
        pack,
        backend=request.novelty_backend,
    )
    experiment_contract = (
        build_experiment_contract(ideas, pack)
        if request.include_experiment_contract
        else None
    )

    diagnostics = _diagnostics(pack, ideas)
    return SynthesisResult(
        status="ready" if not diagnostics else "needs_review",
        gap_summary=build_gap_summary(pack),
        ideas=tuple(ideas),
        novelty_checks=tuple(novelty_checks),
        experiment_contract=experiment_contract,
        diagnostics=tuple(diagnostics),
    )


def run_synthesis_capability(
    *,
    context: CapabilityContext,
    request: SynthesisRequest,
) -> CapabilityResult:
    """Persist one deterministic evidence-to-direction handoff.

    The caller supplies the expanded evidence pack.  This adapter does not
    read private stage paths, call an LLM, or decide whether an experiment is
    worth running; those remain explicit policies of the caller/controller.
    """
    result = synthesize_evidence(request)
    output = context.store.write_json(
        "synthesis_result.json",
        result.to_handoff_dict(),
        kind="synthesis_result",
        schema="synthesis_result.v1",
        producer="research.synthesis",
    )
    return CapabilityResult(
        status="completed" if result.status == "ready" else "partial",
        artifacts=(output,),
        diagnostics=result.diagnostics,
        usage={
            "ideas": len(result.ideas),
            "novelty_checks": len(result.novelty_checks),
        },
        provenance={
            "capability": "synthesis",
            "result_schema": "synthesis_result.v1",
        },
    )


def _diagnostics(pack: Mapping[str, Any], ideas: list[IdeaCandidate]) -> list[str]:
    """Report evidence gaps without blocking conservative synthesis."""
    diagnostics: list[str] = []
    coverage = pack.get("coverage")
    if isinstance(coverage, Mapping):
        missing_facets = coverage.get("missing_facets")
        if isinstance(missing_facets, list) and missing_facets:
            diagnostics.append(
                "Missing evidence facets: "
                + ", ".join(str(item) for item in missing_facets)
            )
    counts = pack.get("counts")
    if isinstance(counts, Mapping):
        if int(counts.get("documents") or 0) <= 0:
            diagnostics.append("No source documents are available.")
        if int(counts.get("chunks") or 0) <= 0:
            diagnostics.append("No source chunks are available.")
    if not any(idea.motivation_refs for idea in ideas):
        diagnostics.append("No idea candidate has an evidence reference.")
    return diagnostics


def _mapping_rows(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, Mapping)]


__all__ = [
    "SynthesisRequest",
    "SynthesisResult",
    "SynthesisStatus",
    "synthesize_evidence",
    "run_synthesis_capability",
]
