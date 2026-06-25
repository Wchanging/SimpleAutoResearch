from __future__ import annotations

import py_compile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from simple_ar.integrations.llm import LLMClient
from simple_ar.reviewing.schema import ReviewFinding
from simple_ar.code_task.reviewing import build_review_artifact, review_prompt, run_llm_review
from simple_ar.code_task.analysis.interfaces import find_local_api_mismatches, project_api_contract


GREENFIELD_REVIEW_CONTRACT_VERSION = 3


def review_generated_project(
    *,
    project_dir: Path,
    code_artifacts: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
    contract: Mapping[str, Any] | None = None,
    dependency_advice: Mapping[str, Any] | None = None,
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
        contract=contract or {},
        dependency_advice=dependency_advice or {},
    )
    llm_findings = _llm_findings(
        project_dir=project_dir,
        result_schema=result_schema,
        resource_plan=resource_plan,
        contract=contract or {},
        dependency_advice=dependency_advice or {},
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
    contract: Mapping[str, Any],
    dependency_advice: Mapping[str, Any],
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
    findings.extend(_task_acceptance_findings(project_dir, contract=contract, dependency_advice=dependency_advice))
    return findings


def _llm_findings(
    *,
    project_dir: Path,
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
    contract: Mapping[str, Any],
    dependency_advice: Mapping[str, Any],
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
            "task_contract": _compact_mapping(contract),
            "dependency_advice": _compact_mapping(dependency_advice),
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


def _task_acceptance_findings(
    project_dir: Path,
    *,
    contract: Mapping[str, Any],
    dependency_advice: Mapping[str, Any],
) -> list[ReviewFinding]:
    """Check explicit task-level acceptance requirements.

    These checks intentionally trigger only when the task text asks for the
    capability. They are a guard against "benchmark-only" green lights where a
    project prints the right metrics but skips the requested project surface.
    """

    text = _task_text(contract)
    findings: list[ReviewFinding] = []
    if not text:
        return findings

    if "readme" in text and not _nonempty_file(project_dir / "README.md"):
        findings.append(
            _finding(
                "blocking",
                "missing_required_artifact",
                "Task requests a README, but generated_project/README.md is missing or empty.",
            )
        )
    expected_artifacts = [
        ("artifacts/results.json", "results.json"),
        ("artifacts/report.md", "report.md"),
        ("artifacts/condition_results.jsonl", "condition_results.jsonl"),
    ]
    for phrase, filename in expected_artifacts:
        if phrase in text and not _source_mentions(project_dir, filename):
            findings.append(
                _finding(
                    "blocking",
                    "missing_artifact_writer",
                    f"Task requests `{phrase}`, but generated code does not visibly write or reference `{filename}`.",
                )
            )
    for mode in ("self-check", "list-datasets", "list-models", "report"):
        if _explicit_cli_mode_requested(text, mode) and not _source_mentions(project_dir, mode):
            findings.append(
                _finding(
                    "blocking",
                    "missing_cli_mode",
                    f"Task requests CLI mode `{mode}`, but generated code does not visibly support it.",
                )
            )

    for requirement in _explicit_installed_dependency_requirements(text, dependency_advice):
        markers = requirement["markers"]
        if not any(_source_mentions(project_dir, marker) for marker in markers):
            findings.append(
                _finding(
                    "blocking",
                    "missing_requested_dependency_path",
                    (
                        f"Task explicitly asks to use installed dependency `{requirement['package']}` "
                        "when available, but generated code does not visibly import or reference it."
                    ),
                )
            )
    if _requires_multiple_tasks(text) and any(
        _source_mentions(project_dir, marker)
        for marker in ("task_count = 1", "'task_count': 1", '"task_count": 1')
    ):
        findings.append(
            _finding(
                "blocking",
                "insufficient_task_count",
                "Task asks for multiple tasks, but generated code visibly hard-codes task_count to 1.",
            )
        )
    if "single authoritative" in text and _source_mentions(project_dir, "class ExperimentSummary") and _source_mentions(
        project_dir, "final_metrics_from_evaluation"
    ):
        runner = project_dir / "generated_experiment" / "runner.py"
        reporting_used = False
        if runner.is_file():
            content = runner.read_text(encoding="utf-8", errors="ignore")
            reporting_used = "final_metrics_from_evaluation" in content or "write_run_artifacts" in content
        if not reporting_used:
            findings.append(
                _finding(
                    "warning",
                    "duplicated_or_unused_reporting_path",
                    "Reporting helpers exist but the runner appears to reimplement final metric aggregation instead of using them.",
                )
            )
    return findings


def _generated_file_rows(code_artifacts: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    files = code_artifacts.get("generated_files")
    if not isinstance(files, list):
        return []
    return [row for row in files if isinstance(row, Mapping) and _is_reviewable_generated_path(str(row.get("path", "")))]


def _is_reviewable_generated_path(value: str) -> bool:
    path = PurePosixPath(value.replace("\\", "/").strip().lstrip("/"))
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    lowered = path.as_posix().lower()
    if "__pycache__" in path.parts or any(part.startswith(".") and part != ".env.example" for part in path.parts):
        return False
    if lowered.endswith((".pyc", ".pyo", ".log", ".tmp")):
        return False
    if path.name in {"agent_result.json", "ingestion.json", "review.md"}:
        return False
    return True


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
    return sorted(
        paths,
        key=lambda path: (
            _review_path_priority(path.relative_to(project_dir).as_posix()),
            path.relative_to(project_dir).as_posix(),
        ),
    )


def _review_path_priority(path: str) -> int:
    lowered = path.lower()
    name = PurePosixPath(path).name.lower()
    stem = PurePosixPath(path).stem.lower()
    if name in {"main.py", "__main__.py", "cli.py", "app.py"} or stem in {"main", "cli", "app"}:
        return 0
    if _contains_any(lowered, ("runner", "run_", "execute", "executor", "orchestr", "workflow", "pipeline", "experiment", "train", "eval")):
        return 1
    if _contains_any(lowered, ("input", "data", "dataset", "loader", "source", "ingest", "feature", "label")):
        return 2
    if _contains_any(lowered, ("process", "preprocess", "transform", "prepare", "clean", "split")):
        return 3
    if _contains_any(lowered, ("core", "model", "algorithm", "logic", "method", "estimator", "classif", "regress")):
        return 4
    if _contains_any(lowered, ("analysis", "metric", "score", "report", "artifact", "output", "result", "summary", "writer")):
        return 5
    return 100


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def _safe_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/").strip().lstrip("/"))
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    return path.as_posix()


def _task_text(contract: Mapping[str, Any]) -> str:
    parts = [
        str(contract.get("objective") or ""),
        str(contract.get("task") or ""),
    ]
    criteria = contract.get("success_criteria")
    if isinstance(criteria, list):
        parts.extend(str(item) for item in criteria)
    return "\n".join(parts).lower()


def _nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and bool(path.read_text(encoding="utf-8", errors="ignore").strip())
    except OSError:
        return False


def _source_mentions(project_dir: Path, needle: str) -> bool:
    target = needle.lower()
    for path in project_dir.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".json", ".toml", ".md", ".txt"}:
            continue
        if "__pycache__" in path.parts:
            continue
        try:
            if target in path.read_text(encoding="utf-8", errors="ignore").lower():
                return True
        except OSError:
            continue
    return False


def _explicit_installed_dependency_requirements(
    text: str,
    dependency_advice: Mapping[str, Any],
) -> list[dict[str, Any]]:
    packages = dependency_advice.get("packages")
    if not isinstance(packages, list):
        return []
    requirements: list[dict[str, Any]] = []
    for row in packages:
        if not isinstance(row, Mapping):
            continue
        package = str(row.get("package") or "").lower()
        import_name = str(row.get("import_name") or "").lower()
        status = str(row.get("status") or "").lower()
        if status != "installed" or not package:
            continue
        aliases = {package, package.replace("-", "_")}
        if import_name:
            aliases.add(import_name)
        aliases = {alias for alias in aliases if alias}
        if not any(alias in text for alias in aliases):
            continue
        if _dependency_mention_is_negated(text, aliases):
            continue
        if not _dependency_use_is_requested(text):
            continue
        markers = [import_name or package.replace("-", "_"), package, *sorted(aliases)]
        requirements.append({"package": package, "markers": list(dict.fromkeys(markers))})
    return requirements


def _dependency_use_is_requested(text: str) -> bool:
    signals = (
        "prefer",
        "when available",
        "if installed",
        "if available",
        "use installed",
        "use packaged",
        "required",
        "must use",
        "should use",
        "may use",
    )
    return any(signal in text for signal in signals)


def _dependency_mention_is_negated(text: str, aliases: set[str]) -> bool:
    for alias in aliases:
        if not alias:
            continue
        negations = (
            f"do not use {alias}",
            f"don't use {alias}",
            f"avoid {alias}",
            f"without {alias}",
            f"no {alias}",
        )
        if any(negation in text for negation in negations):
            return True
    return False


def _explicit_cli_mode_requested(text: str, mode: str) -> bool:
    """Return true only for explicit CLI mode/subcommand requirements."""

    mode = mode.lower()
    if mode in {"self-check", "list-datasets", "list-models"}:
        signals = (
            mode,
            f"--mode {mode}",
            f"mode `{mode}`",
            f"mode '{mode}'",
            f"mode \"{mode}\"",
            f"cli mode {mode}",
            f"subcommand {mode}",
        )
        return any(signal in text for signal in signals)
    signals = (
        "--mode report",
        "mode `report`",
        "mode 'report'",
        'mode "report"',
        "report mode",
        "cli mode report",
        "subcommand report",
        "`report` mode",
        "`report` subcommand",
    )
    return any(signal in text for signal in signals)


def _requires_multiple_tasks(text: str) -> bool:
    signals = (
        "at least two tasks",
        "multiple tasks",
        "two or more tasks",
        "task matrix",
        "condition matrix",
        "at least two classification tasks",
        "one tabular/numeric task",
        "one additional task",
        "dataset x model x feature",
        "multiple conditions",
    )
    return any(signal in text for signal in signals)


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
