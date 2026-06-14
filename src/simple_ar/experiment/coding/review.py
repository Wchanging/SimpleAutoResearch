from __future__ import annotations

import py_compile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from simple_ar.integrations.llm import LLMClient, LLMError


def review_generated_project(
    *,
    project_dir: Path,
    code_artifacts: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
    client: LLMClient | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    files = code_artifacts.get("generated_files")
    generated = [row for row in files if isinstance(row, Mapping)] if isinstance(files, list) else []
    max_files = _int(resource_plan.get("max_files"), 12)
    max_lines = _int(resource_plan.get("max_generated_lines"), 1200)
    if len(generated) > max_files:
        findings.append(_finding("error", "too_many_files", f"Generated {len(generated)} files; budget is {max_files}."))
    total_lines = sum(_int(row.get("line_count"), 0) for row in generated)
    if total_lines > max_lines:
        findings.append(_finding("error", "too_many_lines", f"Generated {total_lines} lines; budget is {max_lines}."))
    if not (project_dir / "main.py").is_file():
        findings.append(_finding("error", "missing_entrypoint", "`main.py` entrypoint is missing."))
    for row in generated:
        path = _safe_path(str(row.get("path", "")))
        if not path:
            findings.append(_finding("error", "unsafe_path", f"Unsafe generated path: {row.get('path', '')}"))
            continue
        target = project_dir / path
        if not target.is_file():
            findings.append(_finding("error", "missing_file", f"Planned file was not written: {path}"))
            continue
        if path.endswith(".py"):
            try:
                py_compile.compile(str(target), doraise=True)
            except py_compile.PyCompileError as exc:
                findings.append(_finding("error", "python_compile_failed", f"{path}: {exc.msg}"))
    for metric in _required_metrics(result_schema):
        if not _metric_name_visible(project_dir, metric):
            findings.append(
                _finding(
                    "warning",
                    "metric_not_visible",
                    f"Required metric `{metric}` is not visibly printed or returned in generated files.",
                )
            )
    agent_findings = _agent_review(project_dir=project_dir, result_schema=result_schema, client=client)
    findings.extend(agent_findings)
    return {
        "schema_version": "code_review.v1",
        "status": _status(findings),
        "findings": findings,
        "summary": {
            "error_count": sum(1 for item in findings if item.get("severity") == "error"),
            "warning_count": sum(1 for item in findings if item.get("severity") == "warning"),
            "generated_file_count": len(generated),
            "generated_line_count": total_lines,
        },
    }


def _agent_review(
    *,
    project_dir: Path,
    result_schema: Mapping[str, Any],
    client: LLMClient | None,
) -> list[dict[str, str]]:
    if client is None:
        return []
    snippets = []
    for path in sorted(project_dir.rglob("*.py"))[:6]:
        try:
            rel = path.relative_to(project_dir).as_posix()
            text = _review_snippet(path)
        except OSError:
            continue
        snippets.append(f"### {rel}\n```python\n{text}\n```")
    if not snippets:
        return []
    try:
        response = client.ask_json(
            "You are a strict but practical code reviewer for generated experiment projects.",
            (
                "Review this generated project for runtime, scope, and metric-export risks. "
                "Return JSON with `findings`, a list of objects containing severity "
                "(error|warning|info), code, and message. Do not request broad rewrites.\n\n"
                f"Result schema:\n{dict(result_schema)}\n\n"
                + "\n\n".join(snippets)
            ),
            label="greenfield-code-review",
        )
    except LLMError:
        return []
    rows = response.get("findings")
    if not isinstance(rows, list):
        return []
    findings: list[dict[str, str]] = []
    for row in rows[:12]:
        if isinstance(row, Mapping):
            severity = str(row.get("severity", "warning")).lower()
            if severity not in {"error", "warning", "info"}:
                severity = "warning"
            if severity == "error":
                # The LLM reviewer receives snippets, not a full executable view.
                # Keep its feedback visible but leave hard failures to deterministic
                # checks such as path safety, file budgets, py_compile, and run guards.
                severity = "warning"
            findings.append(
                _finding(
                    severity,
                    str(row.get("code", "agent_review")),
                    str(row.get("message", "")).strip()[:500] or "Agent reviewer finding.",
                )
            )
    return findings


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
    return (
        text[:half].rstrip()
        + "\n\n# ... middle omitted for reviewer prompt ...\n\n"
        + text[-half:].lstrip()
    )


def _safe_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/").strip().lstrip("/"))
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    return path.as_posix()


def _finding(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _status(findings: list[Mapping[str, str]]) -> str:
    if any(item.get("severity") == "error" for item in findings):
        return "failed"
    if any(item.get("severity") == "warning" for item in findings):
        return "warning"
    return "passed"


def _int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
