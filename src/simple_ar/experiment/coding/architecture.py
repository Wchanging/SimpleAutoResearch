from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any, Mapping

from simple_ar.integrations.llm import LLMClient, LLMError


GREENFIELD_TEMPLATE = "greenfield_project"


def build_architecture_plan(
    *,
    contract: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
    domain_profile: Mapping[str, Any],
    client: LLMClient | None = None,
) -> tuple[dict[str, Any], str]:
    """Build a bounded architecture/file plan for a greenfield experiment."""

    if client is not None:
        try:
            raw = client.ask_json(
                GREENFIELD_ARCHITECT_SYSTEM,
                greenfield_architecture_prompt(
                    contract=contract,
                    result_schema=result_schema,
                    resource_plan=resource_plan,
                    domain_profile=domain_profile,
                ),
                label="greenfield-architecture",
            )
            return normalize_architecture_plan(raw, contract=contract, resource_plan=resource_plan), "llm"
        except LLMError:
            pass
    return fallback_architecture_plan(
        contract=contract,
        result_schema=result_schema,
        resource_plan=resource_plan,
        domain_profile=domain_profile,
    ), "fallback"


def normalize_architecture_plan(
    value: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
) -> dict[str, Any]:
    max_files = _positive_int(resource_plan.get("max_files"), 8)
    raw_files = value.get("files")
    files = [_normalize_file(row) for row in raw_files if isinstance(row, Mapping)] if isinstance(raw_files, list) else []
    files = [row for row in files if row][:max_files]
    if not any(row.get("path") == "main.py" for row in files):
        files.insert(0, _main_file_spec())
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
        "files": _fallback_files(required_metrics),
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
) -> str:
    max_files = _positive_int(resource_plan.get("max_files"), 8)
    max_lines = _positive_int(resource_plan.get("max_generated_lines"), 1200)
    if max_files >= 12 or max_lines >= 2000:
        size_guidance = (
            "- This is a medium local experiment: use 8-15 cohesive files when helpful, "
            "with clear modules for data/task generation, scoring, orchestration, reporting, "
            "configuration, and self-checks.\n"
            "- Keep modules purposeful; do not create filler files just to hit the budget.\n"
        )
    else:
        size_guidance = (
            "- Prefer 3-5 files for ordinary compact local experiments; do not add test files "
            "unless they use only the standard library.\n"
            "- Prefer a compact runnable project over a broad framework.\n"
        )
    return (
        "Design a small greenfield experiment project from this contract. "
        "Return JSON with fields: objective, architecture_summary, data_flow, "
        "interfaces, test_strategy, risks, and files. Each file must include "
        "path, purpose, dependencies, acceptance_criteria, and entrypoint boolean.\n\n"
        "Hard rules:\n"
        "- Keep paths relative, POSIX-style, and inside the generated project.\n"
        "- Include `main.py` as the command-line entrypoint.\n"
        "- Keep file count within resource_plan.max_files.\n"
        f"{size_guidance}"
        "- The entrypoint must print all required metrics as `metric_name: number`.\n"
        "- Avoid heavyweight dependencies, network access, and GPU use unless explicitly allowed.\n\n"
        f"Experiment contract JSON:\n{json.dumps(dict(contract), indent=2, ensure_ascii=False)}\n\n"
        f"Result schema JSON:\n{json.dumps(dict(result_schema), indent=2, ensure_ascii=False)}\n\n"
        f"Resource plan JSON:\n{json.dumps(dict(resource_plan), indent=2, ensure_ascii=False)}\n\n"
        f"Domain profile JSON:\n{json.dumps(dict(domain_profile), indent=2, ensure_ascii=False)}\n"
    )


GREENFIELD_ARCHITECT_SYSTEM = (
    "You are a cautious experiment software architect. Design bounded, "
    "runnable Python projects that satisfy explicit result schemas and resource budgets."
)


def _fallback_files(required_metrics: list[str]) -> list[dict[str, Any]]:
    metric_note = ", ".join(required_metrics) if required_metrics else "configured metrics"
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


def _main_file_spec() -> dict[str, Any]:
    return {
        "path": "main.py",
        "purpose": "Command-line entrypoint.",
        "dependencies": [],
        "acceptance_criteria": ["Runs with `python main.py`."],
        "entrypoint": True,
    }


def _normalize_file(row: Mapping[str, Any]) -> dict[str, Any]:
    path = _safe_path(_text(row.get("path")))
    if not path:
        return {}
    return {
        "path": path,
        "purpose": _text(row.get("purpose"))[:500] or "Generated project file.",
        "dependencies": _list(row.get("dependencies"))[:12],
        "acceptance_criteria": _list(row.get("acceptance_criteria"))[:12],
        "entrypoint": bool(row.get("entrypoint")),
    }


def _safe_path(value: str) -> str:
    value = value.replace("\\", "/").strip().lstrip("/")
    if not value or value.startswith("../") or "/../" in value or value == "..":
        return ""
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    return path.as_posix()


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
