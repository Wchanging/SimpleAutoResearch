from __future__ import annotations

from typing import Any

from simple_ar.report.retrieval import ReportSourceResolver
from simple_ar.report.schema import ReportContext, ReportToolCall, ReportToolResult, ReportToolSpec
from simple_ar.report.tools import (
    GetCodeTaskResultArgs,
    GetMetricSourceArgs,
    GetNeighborChunksArgs,
    GetPaperBriefArgs,
    GetSynthesisBriefArgs,
    report_tool_specs,
)


class ReportToolGateway:
    """Local report tool executor with OpenAI-style schema export."""

    def __init__(self, context: ReportContext) -> None:
        self.context = context
        self.resolver = ReportSourceResolver(context)
        self.specs = {spec.name: spec for spec in report_tool_specs()}
        self.call_counts = {name: 0 for name in self.specs}

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
            related = self.resolver.find_by_paper(handle.paper_id) if handle.paper_id else [handle]
            return ReportToolResult(
                tool_name=name,
                summary=f"Returned bounded context for {args.handle}.",
                content={"handles": [_tool_handle_view(item) for item in related[: 1 + args.before + args.after]]},
                source_handles=[item.handle for item in related[: 1 + args.before + args.after]],
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
            text = self.context.evidence_summary or self.context.synthesis_markdown or self.context.hypothesis_markdown
            if args.query:
                hits = self.resolver.search(args.query, limit=5)
            else:
                hits = []
            return ReportToolResult(
                tool_name=name,
                summary="Returned compact synthesis context.",
                content={
                    "text": text[:2400],
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
