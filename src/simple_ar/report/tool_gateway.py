from __future__ import annotations

import re
from typing import Any

from simple_ar.report.retrieval import ReportSourceResolver, rank_source_chunks, source_query_terms
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.report.schema import ReportContext, ReportToolCall, ReportToolResult, ReportToolSpec
from simple_ar.report.tools import (
    GetCodeTaskResultArgs,
    GetMetricSourceArgs,
    GetNeighborChunksArgs,
    GetPaperBriefArgs,
    GetSynthesisBriefArgs,
    SearchSourceChunksArgs,
    report_tool_specs,
)


class ReportToolGateway:
    """Local report tool executor with OpenAI-style schema export."""

    def __init__(self, context: ReportContext, *, documents: DocumentBundle | None = None) -> None:
        self.context = context
        self.resolver = ReportSourceResolver(context)
        self.specs = {spec.name: spec for spec in report_tool_specs()}
        self.call_counts = {name: 0 for name in self.specs}
        self.documents = documents

    def list_specs(self) -> list[ReportToolSpec]:
        """Return tool specs."""
        return list(self.specs.values())

    def openai_tools(self) -> list[dict[str, Any]]:
        """Export tool specs in OpenAI-compatible function-tool shape."""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.input_schema,
                },
            }
            for spec in self.list_specs()
        ]

    def call(self, call: ReportToolCall) -> ReportToolResult:
        """Execute a bounded local report tool call."""
        spec = self.specs.get(call.tool_name)
        if spec is None:
            return ReportToolResult(tool_name=call.tool_name, status="blocked", summary="Unknown report tool.")
        self.call_counts[call.tool_name] += 1
        if self.call_counts[call.tool_name] > spec.max_calls:
            return ReportToolResult(
                tool_name=call.tool_name,
                status="blocked",
                summary=f"Tool call budget exceeded for {call.tool_name}.",
            )
        try:
            return self._dispatch(call)
        except Exception as exc:  # pragma: no cover - defensive boundary
            return ReportToolResult(tool_name=call.tool_name, status="error", summary=str(exc))

    def _dispatch(self, call: ReportToolCall) -> ReportToolResult:
        name = call.tool_name
        if name == "search_source_chunks":
            args = SearchSourceChunksArgs.model_validate(call.arguments)
            handle = self.resolver.get(args.handle)
            if handle is None:
                return ReportToolResult(tool_name=name, status="not_found", summary="Registered source handle not found.")
            if self.documents is None:
                return ReportToolResult(tool_name=name, status="not_found",
                    summary="Persisted text unavailable; no source search was performed.",
                    content={"search_performed": False}, source_handles=[handle.handle])
            document_id = str(handle.metadata.get("document_id") or handle.paper_id)
            chunks = [row for row in self.documents.chunks if row.document_id == document_id]
            selected = rank_source_chunks(chunks, args.query, limit=args.limit)
            return ReportToolResult(tool_name=name, status="ok" if selected else "not_found",
                summary="Lexical matches in retained text; reread neighboring passages before judging support." if selected else
                    "No lexical match in retained text. This is not proof that the original source omits the claim.",
                content={"search_performed": True, "search_scope": "persisted_extracted_text",
                    "query": args.query, "document_chunk_count": len(chunks),
                    "semantic_verification": "not_performed",
                    "chunks": _chunk_views(selected, query=args.query)}, source_handles=[handle.handle])
        if name == "get_paper_brief":
            args = GetPaperBriefArgs.model_validate(call.arguments)
            if args.handle:
                handle = self.resolver.get(args.handle)
                handles = self.resolver.find_by_paper(handle.paper_id) if handle and handle.paper_id else ([handle] if handle else [])
            elif args.citation_key:
                handles = self.resolver.find_by_citation_key(args.citation_key)
            else:
                handles = self.resolver.find_by_paper(args.paper_id)
            if not handles:
                return ReportToolResult(tool_name=name, status="not_found", summary="No paper handle found.")
            source_label = args.handle or args.citation_key or args.paper_id
            return ReportToolResult(
                tool_name=name,
                summary=f"Found {len(handles)} handle(s) for paper {source_label}.",
                content={"handles": [_tool_handle_view(handle) for handle in handles]},
                source_handles=[handle.handle for handle in handles],
            )
        if name == "get_neighbor_chunks":
            args = GetNeighborChunksArgs.model_validate(call.arguments)
            handle = self.resolver.get(args.handle)
            if handle is None:
                return ReportToolResult(tool_name=name, status="not_found", summary="Chunk handle not found.")
            if self.documents is not None:
                document_id = str(handle.metadata.get("document_id") or handle.paper_id)
                chunks = [item for item in self.documents.chunks if item.document_id == document_id]
                anchor = args.chunk_id or handle.chunk_id
                if not anchor:
                    passages = handle.metadata.get("evidence_passages", [])
                    anchor = next((item.get("chunk_id", "") for item in passages if isinstance(item, dict)), "")
                index = next((i for i, item in enumerate(chunks) if item.chunk_id == anchor), None)
                if index is None:
                    return ReportToolResult(tool_name=name, status="not_found",
                        summary="No persisted anchor in this source. Choose a chunk id; no unrelated passage was substituted.",
                        content={"available_chunk_ids": [item.chunk_id for item in chunks[:12]],
                                 "chunk_ids_truncated": len(chunks) > 12}, source_handles=[handle.handle])
                # A fixed output window, centered on the actual cited passage.
                # Keep ingest order; never cross into another paper or open an arbitrary path.
                selected = chunks[max(0, index - args.before):index + args.after + 1]
                rows = _chunk_views(selected)
                return ReportToolResult(tool_name=name, summary=f"Read {len(rows)} persisted passages around {anchor}.",
                    content={"source_kind": "persisted_extracted_text", "anchor_chunk_id": anchor,
                             "chunks": rows, "document_chunk_count": len(chunks)}, source_handles=[handle.handle])
            related = self.resolver.find_by_paper(handle.paper_id) if handle.paper_id else [handle]
            index = next((i for i, item in enumerate(related) if item.handle == handle.handle), 0)
            selected = related[max(0, index - args.before):index + args.after + 1]
            return ReportToolResult(
                tool_name=name,
                summary="Persisted original text unavailable; returned metadata/previous excerpts, not a fresh source read.",
                content={"source_kind": "metadata_or_cached_excerpt", "handles": [_tool_handle_view(item) for item in selected]},
                source_handles=[item.handle for item in selected],
            )
        if name == "get_metric_source":
            args = GetMetricSourceArgs.model_validate(call.arguments)
            metric = next((item for item in self.context.metric_sources if item.metric_id == args.metric_id), None)
            if metric is None:
                return ReportToolResult(tool_name=name, status="not_found", summary="Metric source not found.")
            return ReportToolResult(
                tool_name=name,
                summary=f"Metric {metric.name}={metric.value} from {metric.artifact}.",
                content=metric.model_dump(mode="json"),
            )
        if name == "get_synthesis_brief":
            args = GetSynthesisBriefArgs.model_validate(call.arguments)
            text = self.context.synthesis_markdown or self.context.hypothesis_markdown or self.context.evidence_summary
            if args.query:
                hits = self.resolver.search(args.query, limit=5)
            else:
                hits = []
            return ReportToolResult(
                tool_name=name,
                summary="Returned compact synthesis context.",
                content={
                    "text": text[:2400],
                    "truncated": len(text) > 2400,
                    "total_characters": len(text),
                    "matching_handles": [_tool_handle_view(handle) for handle in hits],
                },
                source_handles=[handle.handle for handle in hits],
            )
        if name == "get_code_task_result":
            GetCodeTaskResultArgs.model_validate(call.arguments)
            return ReportToolResult(
                tool_name=name,
                summary="Returned experiment result artifact context.",
                content={
                    "results": self.context.results,
                    "metric_sources": [metric.model_dump(mode="json") for metric in self.context.metric_sources],
                },
            )
        return ReportToolResult(tool_name=name, status="blocked", summary="Unhandled report tool.")


def _chunk_views(chunks, *, query: str = "") -> list[dict[str, Any]]:
    """Bound output and expose exact offsets within each persisted chunk."""
    remaining = 4800
    rows = []
    terms = source_query_terms(query)
    for chunk in chunks:
        limit = remaining // (len(chunks) - len(rows))
        exact = re.search(re.escape(query.strip()), chunk.text, re.I) if query.strip() else None
        matches = [(len(hits), -len(term), hits[0].start()) for term in sorted(terms)
                   if (hits := list(re.finditer(re.escape(term), chunk.text, re.I)))]
        anchor = exact.start() if exact else (min(matches)[2] if matches else 0)
        start = min(max(0, anchor - limit // 3), max(0, len(chunk.text) - limit))
        text = chunk.text[start:start + limit]
        remaining -= len(text)
        rows.append({"chunk_id": chunk.chunk_id, "document_id": chunk.document_id,
            "text": text, "character_start": start, "character_end": start + len(text),
            "total_characters": len(chunk.text), "truncated": len(text) < len(chunk.text),
            "source_path": chunk.source_path, "page": chunk.page, "line_start": chunk.line_start,
            "line_end": chunk.line_end, "extraction_status": chunk.metadata.get("extraction_status", "unknown")})
    return rows


def _tool_handle_view(handle: Any) -> dict[str, Any]:
    """Return a model-facing source handle view with short citation guidance."""
    data = handle.model_dump(mode="json")
    citation_key = data.get("citation_key") or ""
    if citation_key:
        data["cite_as"] = f"[@{citation_key}]"
        data["paper_id_for_display"] = citation_key
        data.pop("paper_id", None)
        data["tool_args"] = {"citation_key": citation_key}
    return data
