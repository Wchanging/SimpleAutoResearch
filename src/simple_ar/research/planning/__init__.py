"""Planning primitives and the session-facing deterministic adapter."""

__all__ = [
    "ResearchPlanRequest",
    "ResearchPlanResult",
    "build_research_plan",
    "search_request_from_plan",
    "run_research_plan_capability",
]


def __getattr__(name: str):
    if name in __all__:
        from simple_ar.research.planning.capability import (
            ResearchPlanRequest,
            ResearchPlanResult,
            build_research_plan,
            search_request_from_plan,
            run_research_plan_capability,
        )

        return {
            "ResearchPlanRequest": ResearchPlanRequest,
            "ResearchPlanResult": ResearchPlanResult,
            "build_research_plan": build_research_plan,
            "search_request_from_plan": search_request_from_plan,
            "run_research_plan_capability": run_research_plan_capability,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
