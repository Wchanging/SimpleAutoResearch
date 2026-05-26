from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


class RunConfigError(RuntimeError):
    """Raised when a top-level run config file is missing or invalid."""


def load_pipeline_run_config(config_path: str | None) -> dict[str, object]:
    """Load a TOML config for ``simple-ar run`` or ``simple-ar resume``.

    Args:
        config_path: Optional TOML config path.

    Returns:
        Flat runtime configuration values understood by ``cli.py`` and
        ``stage_handlers.py``. The function intentionally keeps CLI override
        merging outside this module so command behavior stays explicit.
    """
    if not config_path:
        return {}
    path = Path(config_path)
    data = _load_toml(path)
    result: dict[str, object] = {}

    run = _table(data, "run")
    _set_string(result, "topic", run.get("topic"))
    _set_string(result, "output_root", run.get("output_root"))
    _set_string(result, "from_stage", run.get("from_stage"))
    _set_string(result, "to_stage", run.get("to_stage"))
    _set_bool(result, "quiet", run.get("quiet"))

    llm = _table(data, "llm")
    if isinstance(llm.get("enabled"), bool):
        enabled = bool(llm["enabled"])
        result["use_llm"] = enabled
        result["mode"] = "llm" if enabled else "offline"
    _set_string(result, "model", llm.get("model"))
    _set_int(result, "llm_max_workers", llm.get("workers"))

    search = _table(data, "search")
    _set_int(result, "max_papers", search.get("max_papers"))
    _set_string(result, "search_query", search.get("query"))
    if isinstance(search.get("offline"), bool):
        result["use_arxiv"] = not bool(search["offline"])
    _set_bool(result, "allow_fixture_fallback", search.get("allow_fixture_fallback"))
    _set_bool(result, "strict_search", search.get("strict"))

    research = _table(data, "research")
    _set_string(result, "research_mode", research.get("mode"))
    _set_string(result, "research_planner", research.get("planner"))
    _set_string_list(result, "research_sources", research.get("sources"))
    _set_string_list(result, "research_queries", research.get("queries"))
    _set_bool(result, "research_auto_query_expansion", research.get("auto_query_expansion"))
    _set_int(result, "research_max_retrieval_rounds", research.get("max_retrieval_rounds"))
    _set_int(result, "research_max_queries", research.get("max_queries"))
    _set_string_list(result, "research_required_facets", research.get("required_facets"))
    _set_bool(result, "research_use_fulltext", research.get("use_fulltext"))
    _set_bool(result, "research_allow_pdf_download", research.get("allow_pdf_download"))
    _set_int(result, "research_max_fulltext_documents", research.get("max_fulltext_documents"))
    _set_int(result, "research_max_pdf_mb", research.get("max_pdf_mb"))
    _set_bool(result, "research_keep_raw_pdf", research.get("keep_raw_pdf"))
    _set_string(result, "research_parser_backend", research.get("parser_backend"))
    _set_bool(result, "research_cache", research.get("cache"))
    _set_string(result, "research_index_backend", research.get("index_backend"))
    _set_resolved_string_list(result, "research_local_documents", research.get("local_documents"), path)
    research_budget = _table(research, "budget")
    _set_int(result, "research_max_documents", research_budget.get("max_documents"))
    _set_int(result, "research_max_chunks", research_budget.get("max_chunks"))
    _set_int(result, "research_max_context_tokens", research_budget.get("max_context_tokens"))
    _set_int(result, "research_max_llm_calls", research_budget.get("max_llm_calls"))
    _set_int(result, "research_max_follow_up_queries", research_budget.get("max_follow_up_queries"))

    retrieval = _table(data, "retrieval")
    _set_bool(result, "use_retrieval", retrieval.get("enabled"))
    _set_int(result, "retrieval_top_k", retrieval.get("top_k"))

    experiment = _table(data, "experiment")
    _set_string(result, "experiment_template", experiment.get("template"))
    _set_int(result, "experiment_timeout_sec", experiment.get("timeout"))
    code_task_config = _string_value(experiment.get("code_task_config"))
    if code_task_config:
        result["code_task_config"] = str(_resolve_relative(path, code_task_config))

    report = _table(data, "report")
    _set_string(result, "report_mode", report.get("mode"))

    if "code_task_config" not in result and _contains_code_task_config(data):
        result["code_task_config"] = str(path.resolve())
    return result


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise RunConfigError(f"Run config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise RunConfigError(f"Could not parse run config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RunConfigError(f"Expected TOML table in run config: {path}")
    return data


def _table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _set_string(result: dict[str, object], key: str, value: object) -> None:
    text = _string_value(value)
    if text:
        result[key] = text


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _set_int(result: dict[str, object], key: str, value: object) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        result[key] = value


def _set_bool(result: dict[str, object], key: str, value: object) -> None:
    if isinstance(value, bool):
        result[key] = value


def _set_string_list(result: dict[str, object], key: str, value: object) -> None:
    if not isinstance(value, list):
        return
    items = [item.strip() for item in (str(item) for item in value) if item.strip()]
    if items:
        result[key] = items


def _set_resolved_string_list(
    result: dict[str, object],
    key: str,
    value: object,
    config_path: Path,
) -> None:
    if not isinstance(value, list):
        return
    paths = [
        str(_resolve_relative(config_path, item.strip()))
        for item in (str(item) for item in value)
        if item.strip()
    ]
    if paths:
        result[key] = paths


def _contains_code_task_config(data: dict[str, Any]) -> bool:
    for section in ("code_task", "benchmark", "metrics", "environment", "workspace", "safety"):
        value = data.get(section)
        if isinstance(value, dict) and value:
            return True
    return False


def _resolve_relative(config_path: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (config_path.parent / path).resolve()
