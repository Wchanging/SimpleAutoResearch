"""Pipeline stage adapters and default registry."""

from simple_ar.pipeline_stages.registry import (
    HANDLERS,
    execute_code,
    execute_design,
    execute_plan,
    execute_read,
    execute_report,
    execute_run,
    execute_search,
    execute_synthesize,
)

__all__ = [
    "HANDLERS",
    "execute_code",
    "execute_design",
    "execute_plan",
    "execute_read",
    "execute_report",
    "execute_run",
    "execute_search",
    "execute_synthesize",
]
