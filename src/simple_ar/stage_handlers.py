from __future__ import annotations

from typing import Any

from simple_ar.legacy import stage_handlers as _legacy
from simple_ar.stages import Stage


_PATCHABLE_NAMES = (
    "ArxivSearchClient",
    "OpenAlexSearchClient",
    "SemanticScholarSearchClient",
    "get_cached",
    "put_cache",
    "_llm_client",
    "prepare_code_task_experiment",
)

for _name in _PATCHABLE_NAMES:
    if hasattr(_legacy, _name):
        globals()[_name] = getattr(_legacy, _name)


def _sync_patchable_globals() -> None:
    for name in _PATCHABLE_NAMES:
        if name in globals():
            setattr(_legacy, name, globals()[name])


def execute_plan(ctx: Any) -> None:
    _sync_patchable_globals()
    return _legacy.execute_plan(ctx)


def execute_search(ctx: Any) -> None:
    _sync_patchable_globals()
    return _legacy.execute_search(ctx)


def execute_read(ctx: Any) -> None:
    _sync_patchable_globals()
    return _legacy.execute_read(ctx)


def execute_synthesize(ctx: Any) -> None:
    _sync_patchable_globals()
    return _legacy.execute_synthesize(ctx)


def execute_design(ctx: Any) -> None:
    _sync_patchable_globals()
    return _legacy.execute_design(ctx)


def execute_code(ctx: Any) -> None:
    _sync_patchable_globals()
    return _legacy.execute_code(ctx)


def execute_run(ctx: Any) -> None:
    _sync_patchable_globals()
    return _legacy.execute_run(ctx)


def execute_report(ctx: Any) -> None:
    _sync_patchable_globals()
    return _legacy.execute_report(ctx)


HANDLERS = {
    Stage.PLAN: execute_plan,
    Stage.SEARCH: execute_search,
    Stage.READ: execute_read,
    Stage.SYNTHESIZE: execute_synthesize,
    Stage.DESIGN: execute_design,
    Stage.CODE: execute_code,
    Stage.RUN: execute_run,
    Stage.REPORT: execute_report,
}


def __getattr__(name: str) -> Any:
    return getattr(_legacy, name)
