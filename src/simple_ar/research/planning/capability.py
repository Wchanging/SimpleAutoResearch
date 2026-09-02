"""Standalone research-planning capability.

The capability adapts the existing question, query, and source planners to the
common session boundary. Deterministic planning remains available for offline
use, while the session adapter can explicitly provide the existing LLM client
for model-assisted question and query planning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import TYPE_CHECKING, Any, Mapping

from simple_ar.core.capabilities import CapabilityContext, CapabilityResult
from simple_ar.integrations.llm import LLMError
from simple_ar.research.contracts import QueryPlan, ResearchQuestion, SourcePlan
from simple_ar.research.outputs.artifacts import build_research_plan_artifact
from simple_ar.research.prompts import (
    PLAN_SYSTEM,
    RESEARCH_PLANNER_SYSTEM,
    plan_user_prompt,
    research_planner_user_prompt,
)
from simple_ar.research.sources.base import build_source_plan, primary_query

from .planner import (
    build_llm_research_plan,
    build_query_plan,
    build_research_questions,
)

if TYPE_CHECKING:
    from simple_ar.research.sources.capability import SearchRequest


@dataclass(frozen=True, slots=True)
class ResearchPlanRequest:
    """Inputs for one research-plan attempt.

    ``use_llm`` is deliberately explicit. The pure ``build_research_plan``
    function stays deterministic; the capability adapter is the place where a
    caller may opt into the shared LLM transport.
    """

    topic: str
    problem_markdown: str = ""
    config: Mapping[str, object] = field(default_factory=dict)
    default_query: str = ""
    default_max_results: int = 10
    use_llm: bool = False
    llm_client: Any | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.topic.strip():
            raise ValueError("ResearchPlanRequest.topic cannot be empty.")
        if self.default_max_results < 1:
            raise ValueError("default_max_results must be positive.")
        if self.use_llm and self.llm_client is None:
            raise ValueError("ResearchPlanRequest.llm_client is required when use_llm is true.")
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


def build_research_scope(
    topic: str,
    *,
    llm_client: Any | None = None,
) -> tuple[str, str]:
    """Build the two human-readable scope artifacts used by the old pipeline.

    The eight-stage runner still writes ``goal.md`` and ``problem.md`` for
    compatibility.  Keeping their content generation here lets that adapter
    reuse the canonical planning boundary instead of maintaining a second LLM
    planning call in ``pipeline_stages``.
    """

    if not topic.strip():
        raise ValueError("Research scope topic cannot be empty.")
    if llm_client is not None:
        response = llm_client.ask_json(
            PLAN_SYSTEM,
            plan_user_prompt(topic),
            label="plan",
        )
        goal = _text_field(response, "goal_markdown")
        problem = _text_field(response, "problem_markdown")
        if not goal or not problem:
            raise LLMError(
                "Research scope response did not contain both goal_markdown and problem_markdown."
            )
        return _ensure_heading(goal, "Research Goal"), _ensure_heading(problem, "Research Problem")

    return (
        "# Research Goal\n\n"
        f"Topic: {topic}\n\n"
        "Create a small, reproducible research workflow that can be inspected "
        "stage by stage.\n",
        "# Research Problem\n\n"
        f"How can we study `{topic}` with a simple literature-backed "
        "experiment and a transparent artifact pipeline?\n",
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
    """Persist one planning handoff for a session attempt."""

    result = build_requested_research_plan(request)
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
            "mode": "llm" if request.use_llm else "deterministic",
            "model": str(getattr(request.llm_client, "model", ""))
            if request.use_llm
            else "",
        },
    )


def build_requested_research_plan(request: ResearchPlanRequest) -> ResearchPlanResult:
    """Build a deterministic or explicitly LLM-assisted plan."""

    baseline = build_research_plan(request)
    if not request.use_llm:
        return baseline
    client = request.llm_client
    if client is None:
        raise LLMError("LLM planning was requested but no client was provided.")

    config = dict(request.config)
    max_queries = _llm_query_budget(config)
    max_rounds = baseline.query_plan.max_rounds
    mode = str(config.get("research_mode") or baseline.source_plan.mode)
    prompt = research_planner_user_prompt(
        topic=request.topic,
        problem_markdown=request.problem_markdown,
        seed_queries_json=json.dumps(
            baseline.query_plan.seed_queries,
            ensure_ascii=False,
        ),
        required_facets_json=json.dumps(
            baseline.query_plan.required_facets,
            ensure_ascii=False,
        ),
        max_queries=max_queries,
        max_rounds=max_rounds,
        mode=mode,
    )
    data = client.ask_json(
        RESEARCH_PLANNER_SYSTEM,
        prompt,
        label="research-planner",
    )
    questions, query_plan = build_llm_research_plan(
        topic=request.topic,
        problem_markdown=request.problem_markdown,
        config=config,
        default_query=request.default_query or request.topic,
        data=data,
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


def _llm_query_budget(config: Mapping[str, object]) -> int:
    """Keep model planning within the same small query budget as offline mode."""

    value = config.get("research_max_queries")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return min(max(1, value), 12)
    return 6


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


def _text_field(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def _ensure_heading(markdown: str, heading: str) -> str:
    stripped = markdown.strip()
    return stripped + "\n" if stripped.startswith("#") else f"# {heading}\n\n{stripped}\n"


__all__ = [
    "ResearchPlanRequest",
    "ResearchPlanResult",
    "build_research_scope",
    "build_research_plan",
    "search_request_from_plan",
    "run_research_plan_capability",
]
