from __future__ import annotations

from pydantic import BaseModel, Field

from simple_ar.report.schema import ReportToolSpec


class GetPaperBriefArgs(BaseModel):
    handle: str = Field(default="", description="Optional source handle such as paper:<id> from the current run.")
    paper_id: str = Field(default="", description="Paper id from the current run.")
    citation_key: str = Field(default="", description="Short citation key such as P1 from the current report prompt.")


class GetNeighborChunksArgs(BaseModel):
    handle: str = Field(description="Chunk source handle from the current run.")
    chunk_id: str = Field(default="", description="Optional persisted chunk id from evidence_passages; anchors the neighborhood.")
    before: int = Field(default=1, ge=0, le=3)
    after: int = Field(default=1, ge=0, le=3)


class GetMetricSourceArgs(BaseModel):
    metric_id: str = Field(description="Metric id such as metric:accuracy.")


class SearchSourceChunksArgs(BaseModel):
    handle: str = Field(description="Registered paper/material handle; never a filesystem path.")
    query: str = Field(min_length=1, max_length=500, description="Specific phrase, result, condition or contradiction to locate in retained source text.")
    limit: int = Field(default=3, ge=1, le=5)


class GetSynthesisBriefArgs(BaseModel):
    query: str = Field(default="", description="Optional theme, claim, or gap query.")


class GetCodeTaskResultArgs(BaseModel):
    run_ref: str = Field(default="", description="Optional experiment/code-task or run reference.")


class ToolOutput(BaseModel):
    summary: str
    content: dict = Field(default_factory=dict)
    source_handles: list[str] = Field(default_factory=list)


def report_tool_specs() -> list[ReportToolSpec]:
    """Return the shared read-only report tool contracts."""
    return [
        _spec(
            "get_paper_brief",
            "Return current-run paper metadata and paper-brief handles for a paper id.",
            GetPaperBriefArgs,
        ),
        _spec(
            "get_neighbor_chunks",
            "Read persisted source passages around a cited chunk. Without stored text, return explicitly labeled metadata only.",
            GetNeighborChunksArgs,
        ),
        _spec(
            "search_source_chunks",
            "Locate query-matching passages in one registered source's persisted text, including passages absent from the initial summary. Lexical matches are not semantic support; no match is not proof of absence from the original.",
            SearchSourceChunksArgs,
        ),
        _spec(
            "get_metric_source",
            "Return a metric value and artifact provenance from the current run.",
            GetMetricSourceArgs,
        ),
        _spec(
            "get_synthesis_brief",
            "Return compact synthesis evidence related to a query.",
            GetSynthesisBriefArgs,
        ),
        _spec(
            "get_code_task_result",
            "Return canonical experiment/code-task result, guard, review, and metric provenance when available.",
            GetCodeTaskResultArgs,
        ),
    ]


def _spec(name: str, description: str, args_model: type[BaseModel]) -> ReportToolSpec:
    return ReportToolSpec(
        name=name,
        description=description,
        input_schema=args_model.model_json_schema(),
        output_schema=ToolOutput.model_json_schema(),
        permissions=["read"],
        max_calls=8,
        max_output_tokens=1200,
    )
