"""Capability-backed materialization of the evidence summary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simple_ar.core import ArtifactRef, CapabilityContext, CapabilityResult
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.research.evidence.reader import ReadResult
from simple_ar.research.sources.capability import SearchResult, provided_materials_result
from simple_ar.research.synthesis import SynthesisResult
from simple_ar.research.workflow_contracts import ResearchBrief


@dataclass(frozen=True, slots=True)
class SummaryRequest:
    """Explicit inputs for one summary materialization attempt."""

    brief_ref: ArtifactRef
    search_ref: ArtifactRef | None
    documents_ref: ArtifactRef
    read_ref: ArtifactRef
    synthesis_ref: ArtifactRef
    state_refs: tuple[tuple[str, ArtifactRef], ...] = ()


def run_summary_capability(
    *, context: CapabilityContext, request: SummaryRequest,
) -> CapabilityResult:
    """Render the summary from the accepted evidence handoffs."""

    brief = ResearchBrief.from_dict(context.read_input_json(request.brief_ref))
    documents = DocumentBundle.from_handoff_dict(
        context.read_input_json(request.documents_ref)
    )
    search = (
        SearchResult.from_handoff_dict(context.read_input_json(request.search_ref))
        if request.search_ref is not None
        else provided_materials_result(documents.records)
    )
    read = ReadResult.from_handoff_dict(
        context.read_input_json(request.read_ref), bundle=documents
    )
    synthesis = SynthesisResult.from_handoff_dict(
        context.read_input_json(request.synthesis_ref)
    )
    state_refs = dict(request.state_refs)
    text = render_summary(brief, synthesis, search, documents, read, state_refs)
    output = context.store.write_text(
        "research_summary.md", text,
        kind="research_summary", schema="research_summary.v1",
        producer="research.summary",
    )
    snapshot = context.store.write_json(
        "research_summary.json",
        {
            "schema_version": "research_summary.v1",
            "status": synthesis.status,
            "generation_mode": synthesis.generation_mode,
            "paper_count": len(search.papers),
            "selected_paper_count": len(search.selected_papers),
            "document_count": len(documents.records),
            "chunk_count": len(documents.chunks),
            "state_refs": {name: ref.to_dict() for name, ref in state_refs.items()},
            "diagnostics": list(synthesis.diagnostics),
        },
        kind="research_summary_snapshot", schema="research_summary.v1",
        producer="research.summary",
    )
    return CapabilityResult(
        status="completed",
        artifacts=(output, snapshot),
        usage={
            "paper_count": len(search.papers),
            "document_count": len(documents.records),
            "chunk_count": len(documents.chunks),
        },
        provenance={"capability": "summary", "result_schema": "research_summary.v1"},
    )


def render_summary(
    brief: ResearchBrief,
    synthesis: SynthesisResult,
    search: SearchResult,
    documents: DocumentBundle,
    read: ReadResult,
    state_refs: dict[str, ArtifactRef] | None = None,
) -> str:
    """Render the existing evidence summary without adding a new store."""

    limitations = _unique(
        [*search.diagnostics, *read.diagnostics, *synthesis.diagnostics]
    )
    lines = [
        "# Research Summary", "", "## Research question", "",
        brief.objective or brief.request_text.strip(),
        "", "## Evidence collection", "",
        f"- Available source records: {len(search.papers)}",
        f"- Sources used: {len(search.selected_papers)}",
        f"- Ingested documents: {len(documents.records)}",
        f"- Text chunks: {len(documents.chunks)}",
        f"- Evidence cards: {len(read.paper_cards)} paper, "
        f"{len(read.claim_cards)} claim, {len(read.method_cards)} method, "
        f"{len(read.dataset_cards)} dataset",
        "", "## Synthesis", "",
        synthesis.synthesis_markdown.strip() or "No synthesis prose was produced.",
        "", "## Proposed direction", "",
        synthesis.hypothesis_markdown.strip() or "No explicit hypothesis was produced.",
        "", "## Limitations", "",
        *[f"- {item}" for item in limitations or ("No capability limitation was reported.",)],
        "", "## Artifact trail", "",
        *[
            f"- `{name}`: `{ref.path}`"
            for name, ref in sorted((state_refs or {}).items())
        ],
    ]
    return "\n".join(lines) + "\n"


def _unique(items: Any) -> tuple[str, ...]:
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


__all__ = ["SummaryRequest", "render_summary", "run_summary_capability"]
