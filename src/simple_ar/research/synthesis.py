"""Standalone evidence-to-direction synthesis boundary.

The existing evidence derivation functions remain the implementation.  This
module gives callers one small request/result boundary without taking over
LLM synthesis or persistence policy.  A caller can use it with the expanded
evidence pack already assembled by the pipeline, or with a small fixture.
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
)
from simple_ar.research.evidence.derivation import (
    build_experiment_contract,
    build_gap_summary,
    build_idea_candidates,
    build_novelty_checks,
)
from simple_ar.research.prompts import SYNTHESIZE_SYSTEM, synthesize_user_prompt


SynthesisStatus = Literal["ready", "needs_review"]


@dataclass(frozen=True, slots=True)
class SynthesisRequest:
    """Input for evidence synthesis.

    ``evidence_pack`` is the in-memory, expanded form produced by
    ``build_evidence_pack``.  Persisted compact packs intentionally contain
    references rather than duplicate card rows and should be hydrated by the
    caller before invoking this boundary. ``use_llm`` is explicit so callers
    can keep a reproducible offline path without silently mistaking it for
    model-generated research prose.
    """

    evidence_pack: Mapping[str, Any]
    idea_limit: int = 3
    novelty_backend: str = "local"
    include_experiment_contract: bool = True
    use_llm: bool = False
    llm_client: Any | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.idea_limit < 1:
            raise ValueError("idea_limit must be at least 1")
        if not self.novelty_backend.strip():
            raise ValueError("novelty_backend must not be empty")
        if self.use_llm and self.llm_client is None:
            raise ValueError("SynthesisRequest.llm_client is required when use_llm is true.")


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    """Structured direction and experiment handoff from one evidence pack."""

    status: SynthesisStatus
    gap_summary: str
    ideas: tuple[IdeaCandidate, ...]
    novelty_checks: tuple[NoveltyCheck, ...]
    experiment_contract: ResearchExperimentContract | None = None
    synthesis_markdown: str = ""
    hypothesis_markdown: str = ""
    generation_mode: Literal["deterministic", "llm"] = "deterministic"
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a compact JSON-serializable summary."""
        return {
            "schema_version": "synthesis_result.v1",
            "status": self.status,
            "idea_count": len(self.ideas),
            "novelty_check_count": len(self.novelty_checks),
            "has_experiment_contract": self.experiment_contract is not None,
            "generation_mode": self.generation_mode,
            "synthesis_character_count": len(self.synthesis_markdown),
            "hypothesis_character_count": len(self.hypothesis_markdown),
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
            "synthesis_markdown": self.synthesis_markdown,
            "hypothesis_markdown": self.hypothesis_markdown,
            "generation_mode": self.generation_mode,
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
            synthesis_markdown=str(data.get("synthesis_markdown") or ""),
            hypothesis_markdown=str(data.get("hypothesis_markdown") or ""),
            generation_mode=_generation_mode(data.get("generation_mode")),
            diagnostics=tuple(str(item) for item in data.get("diagnostics", [])),
        )


def _synthesize_deterministic_evidence(request: SynthesisRequest) -> SynthesisResult:
    """Derive bounded ideas, novelty-risk hints, and an experiment handoff.

    This helper is deliberately deterministic and does not call an LLM, write
    files, or choose a report claim.
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
        generation_mode="deterministic",
        diagnostics=tuple(diagnostics),
    )


def synthesize_evidence(request: SynthesisRequest) -> SynthesisResult:
    """Synthesize evidence using the selected deterministic or LLM mode.

    The default remains deterministic. When ``request.use_llm`` is true, the
    existing structured derivation is retained and the shared LLM client adds
    grounded synthesis and hypothesis prose; a missing or malformed model
    response is an error rather than a silent fallback.
    """

    result = _synthesize_deterministic_evidence(request)
    if not request.use_llm:
        return result
    return _add_llm_synthesis(result, request)


def run_synthesis_capability(
    *,
    context: CapabilityContext,
    request: SynthesisRequest,
) -> CapabilityResult:
    """Persist one evidence-to-direction handoff.

    The caller supplies the expanded evidence pack. LLM prose is generated
    only when the request explicitly carries a client; structured ideas and
    contracts remain produced by the existing evidence derivation functions.
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
            "mode": result.generation_mode,
            "model": str(getattr(request.llm_client, "model", ""))
            if request.use_llm
            else "",
        },
    )


def _add_llm_synthesis(
    result: SynthesisResult,
    request: SynthesisRequest,
) -> SynthesisResult:
    """Add grounded model prose to an already-derived structured result."""

    client = request.llm_client
    if client is None:
        raise LLMError("LLM synthesis was requested but no client was provided.")
    if not _has_evidence(request.evidence_pack):
        raise LLMError(
            "LLM synthesis requires at least one evidence card or source chunk; "
            "refusing deterministic fallback."
        )

    pack = dict(request.evidence_pack)
    response = client.ask_json(
        SYNTHESIZE_SYSTEM,
        synthesize_user_prompt(
            _evidence_notes_markdown(pack),
            _bounded_pack_json(pack),
            str(pack.get("evidence_snippets") or ""),
            json.dumps(
                {
                    "topic": pack.get("topic", ""),
                    "coverage": pack.get("coverage", {}),
                    "counts": pack.get("counts", {}),
                    "deterministic_idea_count": len(result.ideas),
                },
                ensure_ascii=False,
            ),
        ),
        label="research-synthesis",
    )
    synthesis_markdown = _required_text(response, "synthesis_markdown")
    hypothesis_markdown = _required_text(response, "hypothesis_markdown")
    return replace(
        result,
        synthesis_markdown=synthesis_markdown,
        hypothesis_markdown=hypothesis_markdown,
        generation_mode="llm",
    )


def _has_evidence(pack: Mapping[str, Any]) -> bool:
    counts = pack.get("counts")
    if isinstance(counts, Mapping):
        return any(
            int(counts.get(key) or 0) > 0
            for key in ("documents", "chunks", "paper_cards", "claim_cards")
        )
    return any(pack.get(key) for key in ("papers", "paper_cards", "claim_cards"))


def _bounded_pack_json(pack: Mapping[str, Any]) -> str:
    """Serialize compact card evidence without sending full source text."""

    selected: dict[str, Any] = {
        "topic": pack.get("topic", ""),
        "coverage": pack.get("coverage", {}),
        "counts": pack.get("counts", {}),
    }
    for key in ("paper_cards", "claim_cards", "method_cards", "dataset_cards"):
        value = pack.get(key)
        if isinstance(value, list):
            selected[key] = value[:24]
    return json.dumps(selected, ensure_ascii=False, default=str)


def _evidence_notes_markdown(pack: Mapping[str, Any]) -> str:
    """Create a small readable evidence view for the synthesis prompt."""

    lines = [f"# Evidence Notes\n\nTopic: {pack.get('topic', '')}"]
    for key, heading, fields in (
        ("paper_cards", "Papers", ("paper_id", "title", "method_summary")),
        ("claim_cards", "Claims", ("claim_id", "paper_id", "claim")),
        ("method_cards", "Methods", ("method_id", "paper_id", "name")),
        ("dataset_cards", "Datasets", ("dataset_id", "paper_id", "name")),
    ):
        rows = pack.get(key)
        if not isinstance(rows, list) or not rows:
            continue
        lines.append(f"\n## {heading}")
        for row in rows[:24]:
            if not isinstance(row, Mapping):
                continue
            values = [str(row.get(field) or "").strip() for field in fields]
            values = [value[:360] for value in values if value]
            if values:
                lines.append("- " + " | ".join(values))
    return "\n".join(lines)


def _required_text(response: Mapping[str, Any], key: str) -> str:
    value = response.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LLMError(f"LLM synthesis response is missing non-empty {key}.")
    return value.strip()


def _generation_mode(value: object) -> Literal["deterministic", "llm"]:
    return "llm" if str(value or "").strip().lower() == "llm" else "deterministic"


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
