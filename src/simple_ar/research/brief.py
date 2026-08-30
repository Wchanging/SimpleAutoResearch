"""Small composed research-brief capability.

This module composes the existing Read and Synthesis boundaries.  It is a
local, deterministic handoff for a research brief; search, LLM prose, and
artifact persistence remain policies of their callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from simple_ar.core.capabilities import CapabilityContext, CapabilityResult
from simple_ar.research.evidence.reader import ReadRequest, ReadResult, read_documents
from simple_ar.research.synthesis import (
    SynthesisRequest,
    SynthesisResult,
    synthesize_evidence,
)
from simple_ar.research.documents.ingest import DocumentBundle


ResearchBriefStatus = Literal["ready", "partial", "needs_review", "empty"]


@dataclass(frozen=True, slots=True)
class ResearchBriefRequest:
    """Inputs for one evidence-backed research brief.

    The default remains deterministic for library compatibility. A caller
    that wants model-generated synthesis must set ``use_llm`` and provide the
    shared client explicitly.
    """

    topic: str
    bundle: DocumentBundle
    document_ids: tuple[str, ...] | None = None
    paper_ids: tuple[str, ...] | None = None
    idea_limit: int = 3
    novelty_backend: str = "local"
    include_experiment_contract: bool = True
    use_llm: bool = False
    llm_client: Any | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.topic.strip():
            raise ValueError("ResearchBriefRequest.topic cannot be empty.")
        if self.idea_limit < 1:
            raise ValueError("idea_limit must be at least 1.")
        if not self.novelty_backend.strip():
            raise ValueError("novelty_backend cannot be empty.")
        if self.use_llm and self.llm_client is None:
            raise ValueError("ResearchBriefRequest.llm_client is required when use_llm is true.")


@dataclass(frozen=True, slots=True)
class ResearchBriefResult:
    """Read and synthesis outputs for one research-brief attempt."""

    status: ResearchBriefStatus
    read: ReadResult
    synthesis: SynthesisResult | None
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a compact handoff summary without copying source text."""
        return {
            "schema_version": "research_brief_result.v1",
            "status": self.status,
            "topic_document_count": len(self.read.bundle.records),
            "chunk_count": len(self.read.bundle.chunks),
            "paper_card_count": len(self.read.paper_cards),
            "idea_count": len(self.synthesis.ideas) if self.synthesis else 0,
            "has_experiment_contract": bool(
                self.synthesis and self.synthesis.experiment_contract
            ),
            "generation_mode": (
                self.synthesis.generation_mode if self.synthesis else "none"
            ),
            "diagnostics": list(self.diagnostics),
        }

    def to_handoff_dict(self, *, topic: str) -> dict[str, Any]:
        """Return structured evidence for an explicit downstream handoff.

        The compact ``to_dict()`` shape is useful for status displays.  A
        session handoff needs the cards and direction objects themselves, but
        it should not copy source chunk text into another artifact.  Source
        spans therefore retain identifiers and locations only.
        """
        read = self.read
        read_payload = read.to_handoff_dict()
        synthesis_payload: dict[str, Any] | None = None
        if self.synthesis is not None:
            synthesis_payload = self.synthesis.to_handoff_dict()
        return {
            "schema_version": "research_brief.v1",
            "topic": topic,
            "status": self.status,
            "read": read_payload,
            "synthesis": synthesis_payload,
            "diagnostics": list(self.diagnostics),
        }

    @classmethod
    def from_handoff_dict(
        cls,
        data: Mapping[str, Any],
        *,
        bundle: DocumentBundle,
    ) -> "ResearchBriefResult":
        """Restore a persisted brief while keeping source text in its bundle."""

        if str(data.get("schema_version") or "") != "research_brief.v1":
            raise ValueError("Expected a research_brief.v1 object.")
        status = str(data.get("status") or "")
        if status not in {"ready", "partial", "needs_review", "empty"}:
            raise ValueError(f"Unsupported research brief handoff status: {status!r}")
        read_payload = data.get("read")
        if not isinstance(read_payload, Mapping):
            raise ValueError("Research brief handoff is missing its read result.")
        synthesis_payload = data.get("synthesis")
        synthesis = (
            SynthesisResult.from_handoff_dict(synthesis_payload)
            if isinstance(synthesis_payload, Mapping)
            else None
        )
        return cls(
            status=status,  # type: ignore[arg-type]
            read=ReadResult.from_handoff_dict(read_payload, bundle=bundle),
            synthesis=synthesis,
            diagnostics=tuple(
                str(item) for item in data.get("diagnostics", [])
            ),
        )


def build_research_brief(request: ResearchBriefRequest) -> ResearchBriefResult:
    """Compose reading and evidence-to-direction synthesis.

    The function does not search or write files. Synthesis is deterministic by
    default; callers can explicitly set `use_llm` and provide the shared
    client to add grounded model prose. It never chooses a final research
    claim or a next workflow stage.
    """
    read = read_documents(
        ReadRequest(
            bundle=request.bundle,
            document_ids=request.document_ids,
            paper_ids=request.paper_ids,
        )
    )
    if read.status == "empty":
        return ResearchBriefResult(
            status="empty",
            read=read,
            synthesis=None,
            diagnostics=read.diagnostics,
        )

    synthesis = synthesize_evidence(
        SynthesisRequest(
            evidence_pack=evidence_pack_from_read(request.topic, read),
            idea_limit=request.idea_limit,
            novelty_backend=request.novelty_backend,
            include_experiment_contract=request.include_experiment_contract,
            use_llm=request.use_llm,
            llm_client=request.llm_client,
        )
    )
    diagnostics = tuple((*read.diagnostics, *synthesis.diagnostics))
    if synthesis.status == "needs_review":
        status: ResearchBriefStatus = "needs_review"
    elif read.status == "partial":
        status = "partial"
    else:
        status = "ready"
    return ResearchBriefResult(
        status=status,
        read=read,
        synthesis=synthesis,
        diagnostics=diagnostics,
    )


def run_research_brief_capability(
    *,
    context: CapabilityContext,
    request: ResearchBriefRequest,
) -> CapabilityResult:
    """Run the brief composition as an explicit session capability.

    Registration stays caller-owned, for example
    ``registry.register("research_brief", run_research_brief_capability)``.
    This avoids silently changing the built-in lifecycle profiles while still
    giving the composed domain operation a standard session result and a
    declared output artifact.
    """
    result = build_research_brief(request)
    output = context.store.write_json(
        "research_brief.json",
        result.to_handoff_dict(topic=request.topic),
        kind="research_brief",
        schema="research_brief.v1",
        producer="research.brief",
    )
    capability_status = {
        "ready": "completed",
        "partial": "partial",
        "needs_review": "partial",
        "empty": "blocked",
    }[result.status]
    return CapabilityResult(
        status=capability_status,  # type: ignore[arg-type]
        artifacts=(output,),
        diagnostics=result.diagnostics,
        usage={
            "documents": len(result.read.bundle.records),
            "chunks": len(result.read.bundle.chunks),
            "paper_cards": len(result.read.paper_cards),
            "idea_candidates": len(result.synthesis.ideas) if result.synthesis else 0,
        },
        provenance={
            "capability": "research_brief",
            "result_schema": "research_brief.v1",
            "planner": "llm" if request.use_llm else "deterministic",
            "generation_mode": (
                result.synthesis.generation_mode
                if result.synthesis is not None
                else "none"
            ),
            "model": str(getattr(request.llm_client, "model", ""))
            if request.use_llm
            else "",
        },
    )


def evidence_pack_from_read(topic: str, result: ReadResult) -> dict[str, Any]:
    """Adapt a typed Read result to the minimal synthesis input shape.

    This is a domain handoff, not a replacement for the full evidence-pack
    builder used by the legacy research stage.  It keeps one small adapter for
    callers that explicitly compose the standalone Read and Synthesis
    capabilities without copying source chunk text.
    """
    return {
        "schema_version": "evidence_pack.v1",
        "topic": topic,
        "counts": {
            "documents": len(result.bundle.records),
            "chunks": len(result.bundle.chunks),
            "paper_cards": len(result.paper_cards),
            "claim_cards": len(result.claim_cards),
            "method_cards": len(result.method_cards),
            "dataset_cards": len(result.dataset_cards),
        },
        "coverage": {"status": "unknown"},
        "paper_cards": [card.to_row() for card in result.paper_cards],
        "claim_cards": [card.to_row() for card in result.claim_cards],
        "method_cards": [card.to_row() for card in result.method_cards],
        "dataset_cards": [card.to_row() for card in result.dataset_cards],
        "limitations": list(result.diagnostics),
    }


__all__ = [
    "ResearchBriefRequest",
    "ResearchBriefResult",
    "ResearchBriefStatus",
    "build_research_brief",
    "evidence_pack_from_read",
    "run_research_brief_capability",
]
