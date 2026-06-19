from __future__ import annotations

import py_compile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from simple_ar.integrations.llm import LLMClient
from simple_ar.reviewing.schema import ReviewFinding
from simple_ar.code_task.reviewing import build_review_artifact, review_prompt, run_llm_review
from simple_ar.code_task.analysis.interfaces import find_local_api_mismatches, project_api_contract


GREENFIELD_REVIEW_CONTRACT_VERSION = 2


def review_generated_project(
    *,
    project_dir: Path,
    code_artifacts: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
    implementation_memory: Mapping[str, Any] | None = None,
    architecture_plan: Mapping[str, Any] | None = None,
    client: LLMClient | None = None,
    meta_dir: Path | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Review a generated greenfield project with the shared code-task format."""

    generated = _generated_file_rows(code_artifacts)
    deterministic = _deterministic_findings(
        project_dir=project_dir,
        generated=generated,
        result_schema=result_schema,
        resource_plan=resource_plan,
    )
    llm_findings = _llm_findings(
        project_dir=project_dir,
        result_schema=result_schema,
        resource_plan=resource_plan,
        implementation_memory=implementation_memory or {},
        architecture_plan=architecture_plan or {},
        client=client,
        meta_dir=meta_dir,
        use_llm=use_llm,
    )
    total_lines = sum(_int(row.get("line_count"), 0) for row in generated)
    return build_review_artifact(
        reviewer="greenfield-code-reviewer",
        subject="generated_project",
        findings=[*deterministic, *llm_findings],
        metadata={
            "project_dir": str(project_dir),
            "required_metrics": _required_metrics(result_schema),
            "max_files": _int(resource_plan.get("max_files"), 12),
            "max_lines": _int(resource_plan.get("max_generated_lines"), 1200),
            "generated_file_count": len(generated),
            "generated_line_count": total_lines,
            "review_contract_version": GREENFIELD_REVIEW_CONTRACT_VERSION,
        },
    )


def is_current_greenfield_review(report: Mapping[str, Any]) -> bool:
    metadata = report.get("metadata")
    if not isinstance(metadata, Mapping):
        return False
    return _int(metadata.get("review_contract_version"), 0) == GREENFIELD_REVIEW_CONTRACT_VERSION


def _deterministic_findings(
    *,
    project_dir: Path,
    generated: list[Mapping[str, Any]],
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    max_files = _int(resource_plan.get("max_files"), 12)
    max_lines = _int(resource_plan.get("max_generated_lines"), 1200)
    if len(generated) > max_files:
        findings.append(_finding("blocking", "too_many_files", f"Generated {len(generated)} files; budget is {max_files}."))
    total_lines = sum(_int(row.get("line_count"), 0) for row in generated)
    if total_lines > max_lines:
        findings.append(_finding("blocking", "too_many_lines", f"Generated {total_lines} lines; budget is {max_lines}."))
    if not (project_dir / "main.py").is_file():
        findings.append(_finding("blocking", "missing_entrypoint", "`main.py` entrypoint is missing."))
    has_llm_files = any(str(row.get("mode", "")).startswith("llm") for row in generated)
    if has_llm_files:
        for row in generated:
            path = str(row.get("path", ""))
            if row.get("mode") == "fallback" and path.endswith(".py") and not path.endswith("/__init__.py"):
                findings.append(
                    _finding(
                        "blocking",
                        "mixed_generation_fallback",
                        f"Core file `{path}` fell back while related files were LLM-generated; cross-file contracts are unsafe.",
                    )
                )
    for row in generated:
        path = _safe_path(str(row.get("path", "")))
        if not path:
            findings.append(_finding("blocking", "unsafe_path", f"Unsafe generated path: {row.get('path', '')}"))
            continue
        target = project_dir / path
        if not target.is_file():
            findings.append(_finding("blocking", "missing_file", f"Planned file was not written: {path}"))
            continue
        if path.endswith(".py"):
            try:
                py_compile.compile(str(target), doraise=True)
            except py_compile.PyCompileError as exc:
                findings.append(_finding("blocking", "python_compile_failed", f"{path}: {exc.msg}"))
    for metric in _required_metrics(result_schema):
        if not _metric_name_visible(project_dir, metric):
            findings.append(
                _finding(
                    "warning",
                    "metric_not_visible",
                    f"Required metric `{metric}` is not visibly printed or returned in generated files.",
                )
            )
    for mismatch in find_local_api_mismatches(project_dir):
        available = ", ".join(mismatch.get("available_symbols", [])) or "none"
        findings.append(
            _finding(
                "blocking",
                "missing_local_api",
                (
                    f"{mismatch.get('caller')}:{mismatch.get('line')} references "
                    f"`{mismatch.get('target_module')}.{mismatch.get('missing_symbol')}`, "
                    f"but the generated module does not export it. Available: {available}."
                ),
            )
        )
    return findings


def _llm_findings(
    *,
    project_dir: Path,
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
    implementation_memory: Mapping[str, Any],
    architecture_plan: Mapping[str, Any],
    client: LLMClient | None,
    meta_dir: Path | None,
    use_llm: bool,
) -> list[ReviewFinding]:
    if client is None or meta_dir is None:
        return []
    snippets = []
    for path in _review_paths(project_dir)[:8]:
        try:
            rel = path.relative_to(project_dir).as_posix()
            text = _review_snippet(path)
        except OSError:
            continue
        snippets.append(f"### {rel}\n```python\n{text}\n```")
    prompt = review_prompt(
        instructions=(
            "Review this generated project for runtime, scope, result-schema, resource, and metric-export risks. "
            "Use the resource plan, architecture plan, and implementation memory as context. Do not request broad rewrites."
        ),
        context={
            "result_schema": dict(result_schema),
            "resource_plan": dict(resource_plan),
            "architecture_plan": _compact_mapping(architecture_plan),
            "implementation_memory": _compact_mapping(implementation_memory),
            "actual_project_api": project_api_contract(project_dir),
        },
        snippets=snippets,
    )
    return run_llm_review(
        meta_dir=meta_dir,
        prompt=prompt,
        label="greenfield-code-review",
        source="greenfield.llm-reviewer",
        default_category="generated_project",
        default_evidence=["code_task/meta/review_report.json", "code_task/meta/code_artifacts.json"],
        use_llm=use_llm,
        client=client,
        message_callback=None,
        max_findings=12,
    )


def _generated_file_rows(code_artifacts: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    files = code_artifacts.get("generated_files")
    return [row for row in files if isinstance(row, Mapping)] if isinstance(files, list) else []


def _required_metrics(schema: Mapping[str, Any]) -> list[str]:
    value = schema.get("required_metrics")
    metrics = [str(item) for item in value if str(item).strip()] if isinstance(value, list) else []
    primary = str(schema.get("primary_metric") or "").strip()
    if primary and primary not in metrics:
        metrics.insert(0, primary)
    return metrics


def _metric_name_visible(project_dir: Path, metric: str) -> bool:
    needle = metric.strip()
    if not needle:
        return True
    for path in project_dir.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".json", ".toml", ".md"}:
            continue
        try:
            if needle in path.read_text(encoding="utf-8", errors="ignore"):
                return True
        except OSError:
            continue
    return False


def _review_snippet(path: Path, *, limit: int = 5000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) <= limit:
        return text
    half = max(1000, limit // 2)
    return text[:half].rstrip() + "\n\n# ... middle omitted for reviewer prompt ...\n\n" + text[-half:].lstrip()


def _review_paths(project_dir: Path) -> list[Path]:
    """Prioritize orchestration and dependency-boundary files for review."""

    paths = sorted(project_dir.rglob("*.py"))
    priorities = {
        "main.py": 0,
        "generated_experiment/runner.py": 1,
        "generated_experiment/data.py": 2,
        "generated_experiment/models.py": 3,
        "generated_experiment/metrics.py": 4,
        "generated_experiment/evaluation.py": 5,
        "generated_experiment/reporting.py": 6,
    }
    return sorted(
        paths,
        key=lambda path: (
            priorities.get(path.relative_to(project_dir).as_posix(), 100),
            path.relative_to(project_dir).as_posix(),
        ),
    )


def _safe_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/").strip().lstrip("/"))
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    return path.as_posix()


def _finding(severity: str, category: str, summary: str) -> ReviewFinding:
    return ReviewFinding(
        key=f"greenfield:{category}:{summary[:80]}",
        severity=severity,  # type: ignore[arg-type]
        category=category,
        summary=summary,
        evidence=["code_task/meta/review_report.json", "code_task/meta/code_artifacts.json"],
        recommendation="Repair the generated project before validation or execution.",
        source="greenfield.rule-review",
    )


def _compact_mapping(value: Mapping[str, Any], *, limit: int = 2200) -> str:
    text = str(dict(value))
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
