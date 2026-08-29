"""Standalone deterministic research-planning capability.

The capability adapts the existing question, query, and source planners to the
common session boundary. It does not call an LLM or decide what to execute
next; callers still own search, design, implementation, and execution policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from simple_ar.core.capabilities import CapabilityContext, CapabilityResult
from simple_ar.research.contracts import QueryPlan, ResearchQuestion, SourcePlan
from simple_ar.research.outputs.artifacts import build_research_plan_artifact
from simple_ar.research.sources.base import build_source_plan, primary_query

from .planner import build_query_plan, build_research_questions

if TYPE_CHECKING:
    from simple_ar.research.sources.capability import SearchRequest


@dataclass(frozen=True, slots=True)
class ResearchPlanRequest:
    """Inputs for one deterministic research-plan attempt."""

    topic: str
    problem_markdown: str = ""
    config: Mapping[str, object] = field(default_factory=dict)
    default_query: str = ""
    default_max_results: int = 10

    def __post_init__(self) -> None:
        if not self.topic.strip():
            raise ValueError("ResearchPlanRequest.topic cannot be empty.")
        if self.default_max_results < 1:
            raise ValueError("default_max_results must be positive.")
        object.__setattr__(self, "config", dict(self.config))


@dataclass(frozen=True, slots=True)
class ResearchPlanResult:
    """Structured planning handoff consumed by later research capabilities."""

    questions: tuple[ResearchQuestion, ...]
    query_plan: QueryPlan
    source_plan: SourcePlan

    def to_handoff_dict(self) -> dict[str, Any]:
        """Return the existing ``research_plan.v1`` artifact shape."""

        return build_research_plan_artifact(
            questions=list(self.questions),
            query_plan=self.query_plan,
            source_plan=self.source_plan,
        )

    @classmethod
    def from_handoff_dict(cls, data: Mapping[str, Any]) -> "ResearchPlanResult":
        """Restore a plan artifact without invoking a planner or a provider."""

        if str(data.get("schema_version") or "") != "research_plan.v1":
            raise ValueError("Expected a research_plan.v1 object.")
        questions_payload = data.get("research_questions")
        query_payload = data.get("query_plan")
        source_payload = data.get("source_plan")
        if not isinstance(questions_payload, Mapping):
            raise ValueError("Research plan is missing research questions.")
        if not isinstance(query_payload, Mapping):
            raise ValueError("Research plan is missing query plan.")
        if not isinstance(source_payload, Mapping):
            raise ValueError("Research plan is missing source plan.")

        questions: list[ResearchQuestion] = []
        raw_questions = questions_payload.get("questions")
        if not isinstance(raw_questions, list):
            raise ValueError("Research plan questions must be a list.")
        for row in raw_questions:
            if not isinstance(row, Mapping):
                continue
            question_id = str(row.get("question_id") or "").strip()
            question = str(row.get("question") or "").strip()
            if not question_id or not question:
                raise ValueError("Research plan contains an incomplete question.")
            questions.append(
                ResearchQuestion(
                    question_id=question_id,
                    question=question,
                    facet=str(row.get("facet") or "general"),
                    rationale=str(row.get("rationale") or ""),
                    required=bool(row.get("required", True)),
                    negative_scope=_string_list(row.get("negative_scope")),
                    success_criteria=_string_list(row.get("success_criteria")),
                )
            )
        if not questions:
            raise ValueError("Research plan contains no usable questions.")

        return cls(
            questions=tuple(questions),
            query_plan=_query_plan_from_row(query_payload),
            source_plan=_source_plan_from_row(source_payload),
        )


def build_research_plan(request: ResearchPlanRequest) -> ResearchPlanResult:
    """Build questions, executable queries, and a source budget deterministically."""

    config = dict(request.config)
    questions = build_research_questions(
        topic=request.topic,
        problem_markdown=request.problem_markdown,
        config=config,
    )
    query_plan = build_query_plan(
        topic=request.topic,
        problem_markdown=request.problem_markdown,
        config=config,
        default_query=request.default_query,
        questions=questions,
    )
    source_plan = build_source_plan(
        topic=request.topic,
        problem_markdown=request.problem_markdown,
        config=config,
        default_query=primary_query(query_plan) or request.topic,
        default_max_results=request.default_max_results,
    )
    return ResearchPlanResult(
        questions=tuple(questions),
        query_plan=query_plan,
        source_plan=source_plan,
    )


def run_research_plan_capability(
    *,
    context: CapabilityContext,
    request: ResearchPlanRequest,
) -> CapabilityResult:
    """Persist one deterministic planning handoff for a session attempt."""

    result = build_research_plan(request)
    output = context.store.write_json(
        "research_plan.json",
        result.to_handoff_dict(),
        kind="research_plan",
        schema="research_plan.v1",
        producer="research.planning",
    )
    return CapabilityResult(
        status="completed",
        artifacts=(output,),
        usage={
            "question_count": len(result.questions),
            "query_count": len(result.query_plan.queries),
            "source_count": len(result.source_plan.sources),
        },
        provenance={
            "capability": "plan",
            "planner": result.query_plan.planner,
            "result_schema": "research_plan.v1",
        },
    )


def search_request_from_plan(result: ResearchPlanResult) -> "SearchRequest":
    """Adapt a plan to the existing provider-neutral search request.

    This is intentionally an in-memory handoff. Search invocation, provider
    registry selection, deduplication, and retry policy remain owned by the
    caller and ``research.sources``.
    """

    from simple_ar.research.sources.capability import SearchRequest

    queries = tuple(result.query_plan.queries or result.source_plan.queries)
    providers = tuple(result.source_plan.sources)
    if not queries:
        raise ValueError("Research plan contains no executable queries.")
    if not providers:
        raise ValueError("Research plan contains no search providers.")
    return SearchRequest(
        queries=queries,
        providers=providers,
        max_results_per_query=result.source_plan.max_results_per_query,
        filters=dict(result.source_plan.filters),
    )


def _query_plan_from_row(row: Mapping[str, Any]) -> QueryPlan:
    return QueryPlan(
        topic=str(row.get("topic") or ""),
        seed_queries=_string_list(row.get("seed_queries")),
        follow_up_queries=_string_list(row.get("follow_up_queries")),
        queries=_string_list(row.get("queries")),
        query_specs=_dict_list(row.get("query_specs")),
        required_facets=_string_list(row.get("required_facets")),
        negative_terms=_string_list(row.get("negative_terms")),
        max_rounds=max(1, _int(row.get("max_rounds"), 1)),
        auto_expansion=bool(row.get("auto_expansion", True)),
        rationale=str(row.get("rationale") or ""),
        planner=str(row.get("planner") or "deterministic"),
    )


def _source_plan_from_row(row: Mapping[str, Any]) -> SourcePlan:
    index_root = row.get("index_root")
    return SourcePlan(
        queries=_string_list(row.get("queries")),
        sources=_string_list(row.get("sources")),
        max_results_per_query=max(1, _int(row.get("max_results_per_query"), 10)),
        mode=str(row.get("mode") or "standard"),  # type: ignore[arg-type]
        require_fulltext=bool(row.get("require_fulltext", False)),
        allow_pdf_download=bool(row.get("allow_pdf_download", False)),
        local_documents=_string_list(row.get("local_documents")),
        cache_enabled=bool(row.get("cache_enabled", True)),
        index_backend=str(row.get("index_backend") or "keyword"),
        index_root=str(index_root) if index_root else None,
        filters=dict(row.get("filters")) if isinstance(row.get("filters"), Mapping) else {},
        budget=dict(row.get("budget")) if isinstance(row.get("budget"), Mapping) else {},
        rationale=str(row.get("rationale") or ""),
    )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = [
    "ResearchPlanRequest",
    "ResearchPlanResult",
    "build_research_plan",
    "search_request_from_plan",
    "run_research_plan_capability",
]
