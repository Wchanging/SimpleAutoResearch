from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from simple_ar.integrations.llm import LLMClient, LLMError
from simple_ar.code_task.generation.task_contract import contract_prompt_view
from simple_ar.code_task.generation.planning_tools import build_tool_agent_architecture_plan
from simple_ar.code_task.generation.file_specs import (
    dedupe_file_rows,
    entrypoint_first,
    normalize_dependency_paths,
    normalize_plan_path,
)


GREENFIELD_TEMPLATE = "greenfield_project"


def build_architecture_plan(
    *,
    contract: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
    domain_profile: Mapping[str, Any],
    client: LLMClient | None = None,
    allow_fallback: bool = False,
    retry_attempts: int = 1,
    planning_mode: str = "tool_agent",
    planning_dir: Path | None = None,
    planning_review_rounds: int = 2,
    message_callback: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], str]:
    """Build a bounded architecture/file plan for a greenfield experiment."""

    retry_attempts = max(1, int(retry_attempts or 1))
    last_error: LLMError | None = None
    mode = _normalize_planning_mode(planning_mode)
    if client is not None:
        if mode == "tool_agent":
            try:
                raw = build_tool_agent_architecture_plan(
                    contract=contract,
                    result_schema=result_schema,
                    resource_plan=resource_plan,
                    domain_profile=domain_profile,
                    client=client,
                    retry_attempts=retry_attempts,
                    review_rounds=planning_review_rounds,
                    planning_dir=planning_dir,
                    message_callback=message_callback,
                )
            except LLMError as exc:
                last_error = exc
                _emit(message_callback, f"Greenfield tool-agent planning failed. {exc}")
            else:
                return normalize_architecture_plan(raw, contract=contract, resource_plan=resource_plan), "tool_agent"
        elif mode == "compact":
            for attempt in range(1, retry_attempts + 1):
                try:
                    prompt = greenfield_architecture_prompt(
                        contract=contract,
                        result_schema=result_schema,
                        resource_plan=resource_plan,
                        domain_profile=domain_profile,
                        retry_feedback=_retry_feedback(last_error, attempt),
                    )
                    raw = client.ask_json(
                        GREENFIELD_ARCHITECT_SYSTEM,
                        prompt,
                        label="greenfield-architecture" if attempt == 1 else f"greenfield-architecture-retry-{attempt}",
                        max_output_tokens=_architecture_output_tokens(resource_plan),
                    )
                    return normalize_architecture_plan(raw, contract=contract, resource_plan=resource_plan), "compact"
                except LLMError as exc:
                    last_error = exc
                    if attempt < retry_attempts:
                        delay = _stage_retry_delay(attempt)
                        _emit(
                            message_callback,
                            "Greenfield architecture planning failed "
                            f"(attempt {attempt}/{retry_attempts}); retrying in {delay:.1f}s. {exc}",
                        )
                        time.sleep(delay)
                    else:
                        _emit(
                            message_callback,
                            "Greenfield architecture planning failed "
                            f"(attempt {attempt}/{retry_attempts}); no retries left. {exc}",
                        )
    if client is not None and not allow_fallback:
        raise LLMError(
            "Greenfield architecture planning failed after "
            f"{retry_attempts} attempt(s); fallback is disabled. "
            "Set [execute].allow_planning_fallback = true only for demos/offline smoke tests."
        ) from last_error
    if client is None and not allow_fallback:
        raise LLMError(
            "Greenfield architecture planning requires an LLM client when fallback is disabled. "
            "Set [llm].enabled = false or [execute].allow_planning_fallback = true for deterministic fallback."
        )
    return fallback_architecture_plan(
        contract=contract,
        result_schema=result_schema,
        resource_plan=resource_plan,
        domain_profile=domain_profile,
    ), "fallback"


def _normalize_planning_mode(value: object) -> str:
    text = str(value or "tool_agent").strip().lower().replace("-", "_")
    aliases = {
        "tools": "tool_agent",
        "tool": "tool_agent",
        "tool_agent": "tool_agent",
        "agent_tools": "tool_agent",
        "compact": "compact",
        "single": "compact",
        "single_call": "compact",
        "legacy": "compact",
    }
    if text not in aliases:
        raise ValueError("[execute].planning_mode must be `tool_agent` or `compact`")
    return aliases[text]


def normalize_architecture_plan(
    value: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
) -> dict[str, Any]:
    max_files = _positive_int(resource_plan.get("max_files"), 8)
    raw_files = value.get("files")
    files = [_normalize_file(row) for row in raw_files if isinstance(row, Mapping)] if isinstance(raw_files, list) else []
    files = dedupe_file_rows(
        [row for row in files if row],
        dependency_limit=12,
        public_api_limit=30,
        acceptance_limit=12,
    )[:max_files]
    if not any(row.get("path") == "main.py" for row in files):
        files.insert(0, _main_file_spec())
    files = entrypoint_first(files)
    if not files:
        files = fallback_architecture_plan(
            contract=contract,
            result_schema={},
            resource_plan=resource_plan,
            domain_profile={},
        )["files"]
    return {
        "schema_version": "greenfield_architecture.v1",
        "mode": "greenfield_project",
        "objective": _text(value.get("objective")) or _text(contract.get("objective")),
        "architecture_summary": _text(value.get("architecture_summary"))
        or "Small, reviewable experiment project with a single command-line entrypoint.",
        "data_flow": _list(value.get("data_flow"))[:8],
        "interfaces": _list(value.get("interfaces"))[:8],
        "test_strategy": _list(value.get("test_strategy"))[:8],
        "risks": _list(value.get("risks"))[:8],
        "files": files,
    }


def fallback_architecture_plan(
    *,
    contract: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
    domain_profile: Mapping[str, Any],
) -> dict[str, Any]:
    primary_metric = str(result_schema.get("primary_metric") or "score")
    required = result_schema.get("required_metrics")
    required_metrics = [str(item) for item in required if str(item).strip()] if isinstance(required, list) else []
    if primary_metric and primary_metric not in required_metrics:
        required_metrics.insert(0, primary_metric)
    objective = _text(contract.get("objective")) or "Run a bounded local experiment."
    expected_entrypoints = domain_profile.get("expected_entrypoints")
    entrypoint_hint = ""
    if isinstance(expected_entrypoints, list) and expected_entrypoints:
        entrypoint_hint = f" Domain entrypoint hint: {expected_entrypoints[0]}."
    return {
        "schema_version": "greenfield_architecture.v1",
        "mode": "greenfield_project",
        "objective": objective,
        "architecture_summary": (
            "A compact generated project with a Python entrypoint, a small "
            "experiment module, a JSON config, and machine-readable metric output."
            + entrypoint_hint
        ),
        "data_flow": [
            "Load lightweight configuration from config.json.",
            "Run a deterministic local experiment/evaluation function.",
            "Print every required metric as `name: value` for the execution parser.",
        ],
        "interfaces": [
            "`python main.py` is the execution command.",
            "`generated_experiment.runner.run_experiment()` returns metric dict.",
        ],
        "test_strategy": [
            "Run the configured command under the local ExecutionBackend.",
            "Validate required metrics through result_schema and guard_report.",
        ],
        "risks": [
            "Fallback generation is a conservative runnable scaffold, not a domain-specific breakthrough.",
            "LLM-generated files still require review and run guard validation.",
        ],
        "files": _ensure_public_api(
            _fallback_files(
                required_metrics,
                _positive_int(resource_plan.get("max_files"), 8),
                contract=contract,
                domain_profile=domain_profile,
            )
        ),
    }


def render_architecture_markdown(plan: Mapping[str, Any]) -> str:
    lines = [
        "# Architecture Plan",
        "",
        f"- Mode: `{plan.get('mode', '')}`",
        f"- Objective: {plan.get('objective', '')}",
        "",
        "## Summary",
        "",
        str(plan.get("architecture_summary", "")).strip() or "(not specified)",
    ]
    for heading, key in (
        ("Data Flow", "data_flow"),
        ("Interfaces", "interfaces"),
        ("Test Strategy", "test_strategy"),
        ("Risks", "risks"),
    ):
        items = _list(plan.get(key))
        if items:
            lines.extend(["", f"## {heading}", ""])
            lines.extend(f"- {item}" for item in items)
    lines.extend(["", "## File Plan", ""])
    for row in plan.get("files", []):
        if isinstance(row, Mapping):
            lines.append(f"- `{row.get('path', '')}`: {row.get('purpose', '')}")
            for api in _list(row.get("public_api")):
                lines.append(f"  - API: `{api}`")
    return "\n".join(lines).rstrip() + "\n"


def file_plan_from_architecture(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "greenfield_file_plan.v1",
        "files": [dict(row) for row in plan.get("files", []) if isinstance(row, Mapping)],
    }


def greenfield_architecture_prompt(
    *,
    contract: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
    domain_profile: Mapping[str, Any],
    retry_feedback: str = "",
) -> str:
    max_files = _positive_int(resource_plan.get("max_files"), 8)
    max_lines = _positive_int(resource_plan.get("max_generated_lines"), 1200)
    if max_files >= 16 or max_lines >= 4000:
        size_guidance = (
            "- This is a medium-scale greenfield project: use 10-18 cohesive files when the task warrants it, "
            "with clear modules for configuration, input loading/adaptation, processing or analysis, reusable "
            "domain logic, execution orchestration, evaluation, reporting, CLI, diagnostics, and smoke tests.\n"
            "- Prefer a maintainable package layout over one very large file, but do not create filler files just "
            "to hit the budget.\n"
            "- Preserve the task file's requested capabilities and acceptance criteria unless they conflict with "
            "explicit resource limits.\n"
        )
    elif max_files >= 8 or max_lines >= 1400:
        size_guidance = (
            "- This is a medium-light local experiment: use 6-10 cohesive files when helpful, "
            "with clear modules for inputs, processing or analysis, domain logic, metrics/checks, "
            "execution orchestration, reporting/formatting, configuration, and self-checks.\n"
            "- Keep modules purposeful; do not create filler files just to hit the budget.\n"
            "- Preserve the task file's requested module boundaries unless they conflict with the resource budget.\n"
        )
    else:
        size_guidance = (
            "- Prefer 3-5 files for ordinary compact local experiments; do not add test files "
            "unless they use only the standard library.\n"
            "- Prefer a compact runnable project over a broad framework.\n"
        )
    return (
        "Design a bounded greenfield project from this contract. "
        "Return JSON with fields: objective, architecture_summary, data_flow, "
        "interfaces, test_strategy, risks, and files. Each file must include "
        "path, purpose, dependencies, public_api, acceptance_criteria, and entrypoint boolean. "
        "public_api must list exact exported class/function names and concise signatures used by dependent files.\n\n"
        "Hard rules:\n"
        "- Keep paths relative, POSIX-style, and inside the generated project.\n"
        "- Keep paths relative to the generated project root. If the run command names a directory such as "
        "`generated_project/main.py`, treat that directory as the generated project root and use `main.py` in the plan.\n"
        "- Include `main.py` as the command-line entrypoint relative to that root.\n"
        "- Keep file count within resource_plan.max_files.\n"
        f"{size_guidance}"
        "- The entrypoint must print all required metrics as `metric_name: number`.\n"
        "- Define exactly one authoritative experiment orchestrator. Helper modules "
        "must not each reimplement their own full dataset/model/metric pipeline.\n"
        "- You may use task-relevant installed packages listed in domain_profile.available_task_relevant_packages. "
        "Do not require package installation during execution; provide fallbacks for optional packages when practical.\n"
        "- Design dependency interfaces before implementation. Every cross-file call must use an exact name "
        "declared in the dependency file's public_api; do not use vague prose as an interface contract.\n"
        "- Convert explicit task requirements into concrete module responsibilities, data structures, and "
        "artifact flows. Do not rely on later files to rediscover global requirements from prose.\n"
        "- For multi-step or experimental tasks, define the shared record/result schema that runner, analysis, "
        "reporting, and validation will use. This schema must come from the task contract, not from a benchmark-specific template.\n"
        "- Make `main.py` a thin CLI wrapper when possible; put reusable logic in "
        "purpose-specific modules and call them from the orchestrator.\n"
        "- Avoid heavyweight dependencies, network access, and GPU use unless explicitly allowed.\n\n"
        f"Experiment contract JSON:\n{json.dumps(_architecture_contract_view(contract), indent=2, ensure_ascii=False)}\n\n"
        f"Result schema JSON:\n{json.dumps(dict(result_schema), indent=2, ensure_ascii=False)}\n\n"
        f"Resource plan JSON:\n{json.dumps(dict(resource_plan), indent=2, ensure_ascii=False)}\n\n"
        f"Domain profile JSON:\n{json.dumps(dict(domain_profile), indent=2, ensure_ascii=False)}\n"
        + (f"\nRetry feedback:\n{retry_feedback}\n" if retry_feedback else "")
    )


GREENFIELD_ARCHITECT_SYSTEM = (
    "You are a cautious experiment software architect. Design bounded, "
    "runnable Python projects that satisfy explicit result schemas and resource budgets. "
    "Scale architecture to the requested task and budget without adding filler complexity."
)


def _fallback_files(
    required_metrics: list[str],
    max_files: int = 4,
    *,
    contract: Mapping[str, Any] | None = None,
    domain_profile: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    metric_note = ", ".join(required_metrics) if required_metrics else "configured metrics"
    task_text = " ".join(
        [
            _text((contract or {}).get("objective")),
            _text((contract or {}).get("task")),
            _text((domain_profile or {}).get("task_excerpt")),
        ]
    ).lower()
    if max_files >= 8:
        return _fallback_capability_files(metric_note, max_files, task_text)
    return [
        {
            "path": "main.py",
            "purpose": "Command-line entrypoint that calls the generated experiment runner.",
            "dependencies": ["generated_experiment/runner.py", "config.json"],
            "acceptance_criteria": [f"Prints {metric_note} as parseable metric lines."],
            "entrypoint": True,
        },
        {
            "path": "generated_experiment/__init__.py",
            "purpose": "Package marker for generated experiment code.",
            "dependencies": [],
            "acceptance_criteria": ["Importing generated_experiment succeeds."],
            "entrypoint": False,
        },
        {
            "path": "generated_experiment/runner.py",
            "purpose": "Implements the bounded local experiment and returns metrics.",
            "dependencies": ["config.json"],
            "acceptance_criteria": [f"Returns numeric values for {metric_note}."],
            "entrypoint": False,
        },
        {
            "path": "config.json",
            "purpose": "Small, auditable experiment configuration.",
            "dependencies": [],
            "acceptance_criteria": ["Contains metric defaults and task metadata."],
            "entrypoint": False,
        },
    ]


def _fallback_capability_files(metric_note: str, max_files: int, task_text: str) -> list[dict[str, Any]]:
    """Build a generic fallback plan from requested capabilities.

    The LLM planner remains the preferred path. This fallback only prevents a
    planning failure from collapsing a medium task into one tiny file. It
    deliberately avoids domain-specific templates; modules are selected by
    broad capability words present in the task, then filled to a sensible
    minimum when the resource budget is large.
    """

    selected = {"main", "package", "config_json", "runner", "core", "metrics"}
    if _mentions_any(task_text, ("config", "preset", "setting", "schema", "option", "parameter")):
        selected.add("config")
    if _mentions_any(task_text, ("input", "data", "dataset", "file", "csv", "jsonl", "source", "ingest", "load")):
        selected.add("inputs")
    if _mentions_any(task_text, ("parse", "preprocess", "feature", "transform", "normalize", "token", "extract")):
        selected.add("processing")
    if _mentions_any(task_text, ("analy", "inspect", "audit", "rank", "screen", "compare", "evaluate", "experiment")):
        selected.add("analysis")
    if _mentions_any(task_text, ("report", "artifact", "result", "table", "markdown", "jsonl", "rich", "export")):
        selected.add("reporting")
    if _mentions_any(task_text, ("self-check", "self check", "test", "validate", "quality", "guard", "review")):
        selected.add("validation")
    if _mentions_any(task_text, ("resource", "gpu", "cuda", "memory", "hardware", "profile", "timeout")):
        selected.add("resources")
    if _mentions_any(task_text, ("readme", "documentation", "docs", "usage guide", "open-source", "open source")):
        selected.add("readme")

    if max_files >= 8:
        selected.update(("config", "inputs", "analysis", "reporting"))
    if max_files >= 12:
        selected.update(("processing", "validation", "resources", "readme"))

    catalog = _generic_file_catalog(metric_note)
    order = [
        "main",
        "readme",
        "config_json",
        "package",
        "runner",
        "config",
        "inputs",
        "processing",
        "core",
        "metrics",
        "analysis",
        "reporting",
        "validation",
        "resources",
    ]
    files = [catalog[key] for key in order if key in selected][: max(1, max_files)]
    return _prune_file_dependencies(files)


def _generic_file_catalog(metric_note: str) -> dict[str, dict[str, Any]]:
    return {
        "main": {
            "path": "main.py",
            "purpose": "Thin command-line entrypoint that resolves arguments, calls the orchestrator, and prints metrics.",
            "dependencies": [
                "generated_experiment/runner.py",
                "generated_experiment/validation.py",
                "generated_experiment/reporting.py",
                "config.json",
            ],
            "acceptance_criteria": [f"Prints {metric_note} as parseable metric lines."],
            "entrypoint": True,
            "public_api": ["main(argv=None)"],
        },
        "readme": {
            "path": "README.md",
            "purpose": "Generated project usage guide and extension notes.",
            "dependencies": [],
            "acceptance_criteria": ["Documents the run command, extension points, and fallback behavior."],
            "entrypoint": False,
            "public_api": [],
        },
        "config_json": {
            "path": "config.json",
            "purpose": "Auditable default configuration with bounded presets or settings.",
            "dependencies": [],
            "acceptance_criteria": ["Declares default runtime settings and required metric names."],
            "entrypoint": False,
            "public_api": [],
        },
        "package": {
            "path": "generated_experiment/__init__.py",
            "purpose": "Package marker for generated project code.",
            "dependencies": [],
            "acceptance_criteria": ["Importing generated_experiment succeeds."],
            "entrypoint": False,
            "public_api": [],
        },
        "config": {
            "path": "generated_experiment/config.py",
            "purpose": "Configuration loading, normalization, and preset resolution helpers.",
            "dependencies": ["config.json"],
            "acceptance_criteria": ["Resolves user settings into a bounded runtime configuration."],
            "entrypoint": False,
            "public_api": ["load_config(config_path=None) -> RuntimeConfig", "resolve_runtime_options(config, overrides=None) -> RuntimeOptions"],
        },
        "inputs": {
            "path": "generated_experiment/inputs.py",
            "purpose": "Input/source loading and provenance tracking for the generated task.",
            "dependencies": ["generated_experiment/config.py"],
            "acceptance_criteria": ["Loads task inputs deterministically and records provenance/fallback status."],
            "entrypoint": False,
            "public_api": ["list_sources() -> list[SourceSpec]", "load_input_bundle(config, source='auto') -> InputBundle"],
        },
        "processing": {
            "path": "generated_experiment/processing.py",
            "purpose": "Deterministic preprocessing or transformation pipeline shared by the runner.",
            "dependencies": ["generated_experiment/inputs.py"],
            "acceptance_criteria": ["Transforms loaded inputs without hidden global state or network access."],
            "entrypoint": False,
            "public_api": ["build_processor(config) -> Processor", "process_bundle(bundle, processor) -> ProcessedBundle"],
        },
        "core": {
            "path": "generated_experiment/core.py",
            "purpose": "Reusable domain logic for the requested project.",
            "dependencies": ["generated_experiment/processing.py"],
            "acceptance_criteria": ["Exposes cohesive functions/classes used by the orchestrator instead of embedding all logic in main.py."],
            "entrypoint": False,
            "public_api": ["build_conditions(config, processed=None) -> list[Condition]", "run_condition(condition, processed, config) -> ConditionResult"],
        },
        "metrics": {
            "path": "generated_experiment/metrics.py",
            "purpose": "Metric, score, and resource accounting helpers.",
            "dependencies": [],
            "acceptance_criteria": ["Returns numeric values for required metric calculations."],
            "entrypoint": False,
            "public_api": ["compute_metrics(records, config=None) -> dict[str, float]", "safe_float(value, default=0.0) -> float"],
        },
        "analysis": {
            "path": "generated_experiment/analysis.py",
            "purpose": "Aggregate condition/task outputs into comparisons, diagnostics, or evaluation summaries.",
            "dependencies": ["generated_experiment/core.py", "generated_experiment/metrics.py"],
            "acceptance_criteria": ["Produces auditable records and final summary values without duplicating orchestration."],
            "entrypoint": False,
            "public_api": ["analyze_records(records, config=None) -> AnalysisSummary"],
        },
        "reporting": {
            "path": "generated_experiment/reporting.py",
            "purpose": "Write human-readable and machine-readable run artifacts.",
            "dependencies": ["generated_experiment/analysis.py"],
            "acceptance_criteria": ["Keeps stdout parseable while preserving detailed outputs in artifact files."],
            "entrypoint": False,
            "public_api": ["final_metrics_from_summary(summary, context=None) -> dict[str, float]", "write_run_artifacts(summary, metrics, output_dir) -> dict[str, str]"],
        },
        "validation": {
            "path": "generated_experiment/validation.py",
            "purpose": "Offline self-checks and validation gates for generated behavior.",
            "dependencies": ["generated_experiment/config.py", "generated_experiment/metrics.py"],
            "acceptance_criteria": ["Self-checks run without network and return numeric status metrics."],
            "entrypoint": False,
            "public_api": ["run_self_check(config=None) -> dict[str, float]"],
        },
        "resources": {
            "path": "generated_experiment/resources.py",
            "purpose": "Runtime resource detection and bounded profile selection.",
            "dependencies": [],
            "acceptance_criteria": ["Detects CPU/GPU/resource hints without assuming a specific machine."],
            "entrypoint": False,
            "public_api": ["detect_resources() -> ResourceInfo", "select_profile(resources, config=None) -> str"],
        },
        "runner": {
            "path": "generated_experiment/runner.py",
            "purpose": "Single authoritative orchestrator for the requested task.",
            "dependencies": [
                "generated_experiment/config.py",
                "generated_experiment/inputs.py",
                "generated_experiment/processing.py",
                "generated_experiment/core.py",
                "generated_experiment/analysis.py",
                "generated_experiment/reporting.py",
                "generated_experiment/validation.py",
                "generated_experiment/resources.py",
            ],
            "acceptance_criteria": [f"Returns numeric values for {metric_note} and writes required artifacts when requested."],
            "entrypoint": False,
            "public_api": ["run_experiment(preset='smoke', data_source='auto', mode='run', config=None) -> dict[str, float]"],
        },
    }


def _mentions_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _prune_file_dependencies(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected_paths = {str(row.get("path", "")) for row in files}
    pruned: list[dict[str, Any]] = []
    for row in files:
        copy = dict(row)
        dependencies = copy.get("dependencies")
        if isinstance(dependencies, list):
            copy["dependencies"] = [str(dep) for dep in dependencies if str(dep) in selected_paths]
        pruned.append(copy)
    return pruned


def _main_file_spec() -> dict[str, Any]:
    return {
        "path": "main.py",
        "purpose": "Command-line entrypoint.",
        "dependencies": [],
        "acceptance_criteria": ["Runs with `python main.py`."],
        "entrypoint": True,
    }


def _normalize_file(row: Mapping[str, Any]) -> dict[str, Any]:
    path = normalize_plan_path(row.get("path"))
    if not path:
        return {}
    return {
        "path": path,
        "purpose": _text(row.get("purpose"))[:500] or "Generated project file.",
        "dependencies": normalize_dependency_paths(row.get("dependencies"), limit=12),
        "public_api": _list(row.get("public_api"))[:30] or _default_public_api(path),
        "acceptance_criteria": _list(row.get("acceptance_criteria"))[:12],
        "entrypoint": bool(row.get("entrypoint")),
    }


def _retry_feedback(error: LLMError | None, attempt: int) -> str:
    if error is None:
        return ""
    return (
        f"The previous architecture attempt failed validation before attempt {attempt}: {error}. "
        "Return one strict JSON object only, with a concrete task-specific file plan and no Markdown fences."
    )


def _stage_retry_delay(attempt: int) -> float:
    return min(30.0, 2.0 * (2 ** max(0, attempt - 1)))


def _architecture_output_tokens(resource_plan: Mapping[str, Any]) -> int:
    max_files = _positive_int(resource_plan.get("max_files"), 8)
    if max_files >= 24:
        return 2200
    if max_files >= 12:
        return 1800
    return 1400


def _architecture_contract_view(contract: Mapping[str, Any]) -> dict[str, Any]:
    return contract_prompt_view(
        contract,
        max_task_chars=1400,
        max_requirements=32,
        max_success_criteria=20,
    )


def _emit(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _ensure_public_api(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in files:
        path = str(row.get("path", ""))
        row.setdefault("public_api", _default_public_api(path))
    return files


def _default_public_api(path: str) -> list[str]:
    contracts = {
        "main.py": ["main(argv=None)"],
        "generated_experiment/config.py": [
            "load_config(config_path=None) -> RuntimeConfig",
            "resolve_runtime_options(config, overrides=None) -> RuntimeOptions",
        ],
        "generated_experiment/inputs.py": [
            "list_sources() -> list[SourceSpec]",
            "load_input_bundle(config, source='auto') -> InputBundle",
        ],
        "generated_experiment/processing.py": [
            "build_processor(config) -> Processor",
            "process_bundle(bundle, processor) -> ProcessedBundle",
        ],
        "generated_experiment/core.py": [
            "build_conditions(config, processed=None) -> list[Condition]",
            "run_condition(condition, processed, config) -> ConditionResult",
        ],
        "generated_experiment/metrics.py": [
            "compute_metrics(records, config=None) -> dict[str, float]",
            "safe_float(value, default=0.0) -> float",
        ],
        "generated_experiment/analysis.py": ["analyze_records(records, config=None) -> AnalysisSummary"],
        "generated_experiment/runner.py": [
            "run_experiment(preset='smoke', data_source='auto', mode='run', config=None) -> dict[str, float]",
        ],
        "generated_experiment/reporting.py": [
            "final_metrics_from_summary(summary, context=None) -> dict[str, float]",
            "write_run_artifacts(summary, metrics, output_dir) -> dict[str, str]",
        ],
        "generated_experiment/validation.py": ["run_self_check(config=None) -> dict[str, float]"],
        "generated_experiment/resources.py": [
            "detect_resources() -> ResourceInfo",
            "select_profile(resources, config=None) -> str",
        ],
    }
    return contracts.get(path, [])


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
