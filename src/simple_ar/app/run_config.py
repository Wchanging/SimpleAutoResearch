from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class RunConfigError(RuntimeError):
    """Raised when a top-level run config file is missing or invalid."""


class _ConfigModel(BaseModel):
    """Base model for TOML sections.

    Config files are user-facing and may contain future keys while the project
    evolves. Unknown keys are ignored here and can be picked up by later
    versions without breaking older installations.
    """

    model_config = ConfigDict(extra="ignore")


class RunSection(_ConfigModel):
    topic: str | None = None
    output_root: str | None = None
    from_stage: str | None = None
    to_stage: str | None = None
    quiet: bool | None = None
    debug_artifacts: bool | None = None


class LLMSection(_ConfigModel):
    enabled: bool | None = None
    model: str | None = None
    workers: int | None = None


class SearchSection(_ConfigModel):
    offline: bool | None = None
    max_papers: int | None = None
    query: str | None = None
    allow_fixture_fallback: bool | None = None
    strict: bool | None = None


class ResearchBudgetSection(_ConfigModel):
    max_documents: int | None = None
    max_chunks: int | None = None
    max_context_tokens: int | None = None
    max_llm_calls: int | None = None
    max_follow_up_queries: int | None = None


class ResearchSection(_ConfigModel):
    mode: str | None = None
    planner: str | None = None
    sources: list[str] | None = None
    queries: list[str] | None = None
    auto_query_expansion: bool | None = None
    max_retrieval_rounds: int | None = None
    max_queries: int | None = None
    required_facets: list[str] | None = None
    use_fulltext: bool | None = None
    allow_pdf_download: bool | None = None
    max_fulltext_documents: int | None = None
    max_pdf_mb: int | None = None
    keep_raw_pdf: bool | None = None
    parser_backend: str | None = None
    cache: bool | None = None
    index_backend: str | None = None
    index_root: str | None = None
    local_documents: list[str] | None = None
    budget: ResearchBudgetSection = Field(default_factory=ResearchBudgetSection)


class RetrievalSection(_ConfigModel):
    enabled: bool | None = None
    top_k: int | None = None


class ExperimentSection(_ConfigModel):
    template: str | None = None
    timeout: int | None = None
    code_task_config: str | None = None


class ReportSection(_ConfigModel):
    mode: str | None = None


class PipelineRunConfig(_ConfigModel):
    run: RunSection = Field(default_factory=RunSection)
    llm: LLMSection = Field(default_factory=LLMSection)
    search: SearchSection = Field(default_factory=SearchSection)
    research: ResearchSection = Field(default_factory=ResearchSection)
    retrieval: RetrievalSection = Field(default_factory=RetrievalSection)
    experiment: ExperimentSection = Field(default_factory=ExperimentSection)
    report: ReportSection = Field(default_factory=ReportSection)

    def flatten(self, *, config_path: Path, raw_data: dict[str, Any]) -> dict[str, object]:
        """Convert typed TOML sections to the existing runtime config dict."""
        result: dict[str, object] = {}

        _set_string(result, "topic", self.run.topic)
        _set_string(result, "output_root", self.run.output_root)
        _set_string(result, "from_stage", self.run.from_stage)
        _set_string(result, "to_stage", self.run.to_stage)
        _set_bool(result, "quiet", self.run.quiet)
        _set_bool(result, "debug_artifacts", self.run.debug_artifacts)

        if isinstance(self.llm.enabled, bool):
            result["use_llm"] = self.llm.enabled
            result["mode"] = "llm" if self.llm.enabled else "offline"
        _set_string(result, "model", self.llm.model)
        _set_int(result, "llm_max_workers", self.llm.workers)

        _set_int(result, "max_papers", self.search.max_papers)
        _set_string(result, "search_query", self.search.query)
        if isinstance(self.search.offline, bool):
            result["use_arxiv"] = not self.search.offline
        _set_bool(result, "allow_fixture_fallback", self.search.allow_fixture_fallback)
        _set_bool(result, "strict_search", self.search.strict)

        _set_string(result, "research_mode", self.research.mode)
        _set_string(result, "research_planner", self.research.planner)
        _set_string_list(result, "research_sources", self.research.sources)
        _set_string_list(result, "research_queries", self.research.queries)
        _set_bool(result, "research_auto_query_expansion", self.research.auto_query_expansion)
        _set_int(result, "research_max_retrieval_rounds", self.research.max_retrieval_rounds)
        _set_int(result, "research_max_queries", self.research.max_queries)
        _set_string_list(result, "research_required_facets", self.research.required_facets)
        _set_bool(result, "research_use_fulltext", self.research.use_fulltext)
        _set_bool(result, "research_allow_pdf_download", self.research.allow_pdf_download)
        _set_int(result, "research_max_fulltext_documents", self.research.max_fulltext_documents)
        _set_int(result, "research_max_pdf_mb", self.research.max_pdf_mb)
        _set_bool(result, "research_keep_raw_pdf", self.research.keep_raw_pdf)
        _set_string(result, "research_parser_backend", self.research.parser_backend)
        _set_bool(result, "research_cache", self.research.cache)
        _set_string(result, "research_index_backend", self.research.index_backend)
        _set_string(result, "research_index_root", self.research.index_root)
        _set_resolved_string_list(
            result,
            "research_local_documents",
            self.research.local_documents,
            config_path,
        )

        _set_int(result, "research_max_documents", self.research.budget.max_documents)
        _set_int(result, "research_max_chunks", self.research.budget.max_chunks)
        _set_int(result, "research_max_context_tokens", self.research.budget.max_context_tokens)
        _set_int(result, "research_max_llm_calls", self.research.budget.max_llm_calls)
        _set_int(result, "research_max_follow_up_queries", self.research.budget.max_follow_up_queries)

        _set_bool(result, "use_retrieval", self.retrieval.enabled)
        _set_int(result, "retrieval_top_k", self.retrieval.top_k)

        _set_string(result, "experiment_template", self.experiment.template)
        _set_int(result, "experiment_timeout_sec", self.experiment.timeout)
        code_task_config = _string_value(self.experiment.code_task_config)
        if code_task_config:
            result["code_task_config"] = str(_resolve_relative(config_path, code_task_config))

        _set_string(result, "report_mode", self.report.mode)

        if "code_task_config" not in result and _contains_code_task_config(raw_data):
            result["code_task_config"] = str(config_path.resolve())
        return result


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
    try:
        parsed = PipelineRunConfig.model_validate(data)
    except ValidationError as exc:
        raise RunConfigError(f"Invalid run config {path}: {exc}") from exc
    return parsed.flatten(config_path=path, raw_data=data)


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
