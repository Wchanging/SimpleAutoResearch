from __future__ import annotations

import py_compile
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from simple_ar.core.artifacts import write_json
from simple_ar.integrations.llm import LLMClient
from simple_ar.reviewing.schema import ReviewFinding
from simple_ar.code_task.generation.common import safe_relative_path
from simple_ar.code_task.reviewing import build_review_artifact, review_prompt, run_llm_review
from simple_ar.code_task.analysis.interfaces import find_local_api_mismatches, project_api_contract
from simple_ar.code_task.review_pipeline import (
    build_review_clusters,
    build_review_index,
    compact_review_index,
    snippets_for_cluster,
)


GREENFIELD_REVIEW_CONTRACT_VERSION = 5
_STDLIB_SHADOW_MODULES = set(getattr(sys, "stdlib_module_names", ())) | {
    "types",
    "typing",
    "dataclasses",
    "pathlib",
    "json",
    "random",
    "statistics",
    "collections",
    "enum",
    "copy",
    "re",
    "sys",
}


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
        architecture_plan=architecture_plan or {},
    )
    review_index = build_review_index(
        project_dir,
        result_schema=result_schema,
        contract=contract or {},
    )
    review_clusters = build_review_clusters(
        review_index,
        deterministic_findings=deterministic,
    )
    if meta_dir is not None:
        write_json(meta_dir / "review_index.json", review_index)
        write_json(
            meta_dir / "review_clusters.json",
            {
                "schema_version": "code_task_review_clusters.v1",
                "cluster_count": len(review_clusters),
                "clusters": review_clusters,
            },
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
        deterministic_findings=deterministic,
        review_index=review_index,
        review_clusters=review_clusters,
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
            "review_mode": "layered",
            "review_index": "code_task/meta/review_index.json" if meta_dir is not None else "",
            "review_clusters": "code_task/meta/review_clusters.json" if meta_dir is not None else "",
            "review_cluster_count": len(review_clusters),
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
    architecture_plan: Mapping[str, Any],
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
        path = safe_relative_path(str(row.get("path", "")))
        if not path:
            findings.append(_finding("blocking", "unsafe_path", f"Unsafe generated path: {row.get('path', '')}"))
            continue
        target = project_dir / path
        if not target.is_file():
            findings.append(_finding("blocking", "missing_file", f"Planned file was not written: {path}"))
            continue
        if path.endswith(".py"):
            shadow = _stdlib_shadow_name(path)
            if shadow:
                findings.append(
                    _finding(
                        "blocking",
                        "stdlib_module_shadow",
                        (
                            f"`{path}` shadows Python standard-library module `{shadow}`. "
                            "Rename the generated module and update local imports before execution."
                        ),
                    )
                )
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
    findings.extend(_interface_contract_findings(project_dir, architecture_plan=architecture_plan))
    findings.extend(_semantic_scaffold_findings(project_dir, contract=contract, result_schema=result_schema))
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
    deterministic_findings: list[ReviewFinding],
    review_index: Mapping[str, Any],
    review_clusters: list[Mapping[str, Any]],
) -> list[ReviewFinding]:
    if client is None or meta_dir is None:
        return []
    compact_index = compact_review_index(review_index)
    api_contract = project_api_contract(project_dir)
    all_findings: list[ReviewFinding] = []
    for cluster in review_clusters:
        snippets = snippets_for_cluster(project_dir, cluster)
        if not snippets:
            continue
        cluster_id = str(cluster.get("cluster_id") or "cluster")
        prompt = review_prompt(
            instructions=(
                "Review this generated project cluster for runtime, scope, result-schema, resource, and metric-export risks. "
                "Use the full review index to understand the project shape, then focus on the current cluster snippets. "
                "Verify explicit task requirements and deliverables are implemented, not merely documented. "
                "Flag placeholder datasets, default-filled required metrics, missing artifact writers, schema drift between files, "
                "and cross-file API mismatches. Treat `blocking` as reserved for concrete evidence that validation, execution, "
                "or result claims should not proceed. Do not request broad rewrites."
            ),
            context={
                "result_schema": dict(result_schema),
                "resource_plan": dict(resource_plan),
                "task_contract": _compact_mapping(contract),
                "dependency_advice": _compact_mapping(dependency_advice),
                "architecture_plan": _compact_mapping(architecture_plan),
                "implementation_memory": _compact_mapping(implementation_memory),
                "review_index": compact_index,
                "review_cluster": dict(cluster),
                "deterministic_findings": [
                    finding.model_dump(mode="json") for finding in deterministic_findings[:18]
                ],
                "actual_project_api": api_contract,
            },
            snippets=snippets,
        )
        all_findings.extend(
            run_llm_review(
                meta_dir=meta_dir,
                prompt=prompt,
                label=f"greenfield-code-review-{cluster_id}",
                source=f"greenfield.llm-reviewer.{cluster_id}",
                default_category="generated_project",
                default_evidence=[
                    "code_task/meta/review_report.json",
                    "code_task/meta/code_artifacts.json",
                    "code_task/meta/review_index.json",
                ],
                use_llm=use_llm,
                client=client,
                message_callback=None,
                max_findings=6,
                allow_blocking=True,
            )
        )
    return all_findings[:24]


def _semantic_scaffold_findings(
    project_dir: Path,
    *,
    contract: Mapping[str, Any],
    result_schema: Mapping[str, Any],
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    if not _required_metrics(result_schema):
        return findings
    task_text = _task_text(contract)
    for path, content in _project_text_files(project_dir):
        rel = path.relative_to(project_dir).as_posix()
        if _default_metric_fill_detected(content):
            findings.append(
                _finding(
                    "blocking",
                    "required_metric_default_fill",
                    (
                        f"`{rel}` appears to fill missing required metrics with `0.0` or another fixed default. "
                        "Required metrics must come from measured project outputs or the run should fail clearly."
                    ),
                )
            )
        if _placeholder_execution_detected(content.lower(), task_text=task_text):
            findings.append(
                _finding(
                    "blocking",
                    "placeholder_execution_path",
                    (
                        f"`{rel}` appears to contain placeholder/stub execution logic for a task that expects measured outputs. "
                        "Replace scaffolding with a real bounded implementation or fail clearly."
                    ),
                )
            )
        if _nested_artifact_path_risk(content):
            findings.append(
                _finding(
                    "blocking",
                    "nested_artifact_path_risk",
                    (
                        f"`{rel}` appears to join a caller-provided artifact directory with "
                        "`artifacts/results.json`, which can write `artifacts/artifacts/results.json` "
                        "instead of the required `artifacts/results.json`."
                    ),
                )
            )
        if len(findings) >= 8:
            break
    return findings


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


def _stdlib_shadow_name(path: str) -> str:
    posix = PurePosixPath(path)
    if len(posix.parts) != 1 or posix.suffix != ".py":
        return ""
    stem = posix.stem
    return stem if stem in _STDLIB_SHADOW_MODULES and stem not in {"main", "__main__"} else ""


def _nested_artifact_path_risk(content: str) -> bool:
    if "artifacts/results.json" not in content and 'Path("artifacts") / "results.json"' not in content:
        return False
    return "Path(results_dir)" in content or "base /" in content


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


def _interface_contract_findings(project_dir: Path, *, architecture_plan: Mapping[str, Any]) -> list[ReviewFinding]:
    """Check that planned public APIs exist in generated Python files.

    This is a deliberately small deterministic guard. It does not try to prove
    semantic correctness, but it catches a common greenfield failure mode where
    per-file generation invents names that differ from the plan consumed by
    downstream files. Missing APIs are warnings unless an actual local import
    mismatch already proves a runtime break; the goal is to surface contract
    drift without over-blocking exploratory projects.
    """

    files = architecture_plan.get("files")
    if not isinstance(files, list):
        return []
    actual = project_api_contract(project_dir)
    findings: list[ReviewFinding] = []
    for row in files:
        if not isinstance(row, Mapping):
            continue
        rel = safe_relative_path(str(row.get("path", "")))
        if not rel.endswith(".py"):
            continue
        expected = _api_names(row.get("public_api"))
        if not expected:
            continue
        actual_names = {_api_name_from_signature(item) for item in actual.get(rel, [])}
        actual_names.discard("")
        missing = sorted(name for name in expected if name not in actual_names)
        if missing and (project_dir / rel).is_file():
            findings.append(
                _finding(
                    "warning",
                    "planned_api_not_exported",
                    (
                        f"`{rel}` does not export planned API name(s): {', '.join(missing[:8])}. "
                        "If another file consumes these names, align the producer/consumer contract before execution."
                    ),
                )
            )
        if len(findings) >= 8:
            break
    return findings


def _api_names(value: Any) -> set[str]:
    rows = [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []
    names = {_api_name_from_signature(item) for item in rows}
    return {name for name in names if name}


def _api_name_from_signature(value: str) -> str:
    text = value.strip()
    for prefix in ("def ", "async def "):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    if text.startswith("class "):
        text = text[len("class ") :]
    match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", text)
    return match.group(1) if match else ""


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


def _project_text_files(project_dir: Path) -> list[tuple[Path, str]]:
    rows: list[tuple[Path, str]] = []
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file() or path.suffix not in {".py", ".json", ".toml", ".md", ".txt"}:
            continue
        if "__pycache__" in path.parts:
            continue
        try:
            rows.append((path, path.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
    return rows


_DEFAULT_METRIC_FILL_PATTERNS = (
    re.compile(r"(?i)(required|missing|absent).{0,80}(metric|metrics).{0,80}0\.0"),
    re.compile(r"(?i)(metric|metrics|result|results|record|records)\[[^\]]+\]\s*=\s*0\.0"),
    re.compile(r"(?i)\.setdefault\([^)]*,\s*0\.0\)"),
    re.compile(r"(?i)(metric|metrics).{0,80}(default|fallback).{0,80}0\.0"),
)


def _default_metric_fill_detected(content: str) -> bool:
    return any(pattern.search(content) for pattern in _DEFAULT_METRIC_FILL_PATTERNS)


def _placeholder_execution_detected(lowered_content: str, *, task_text: str) -> bool:
    if not any(keyword in task_text for keyword in ("metric", "evaluate", "experiment", "benchmark", "dataset", "result")):
        return False
    blocking_markers = (
        "placeholder record",
        "placeholder dataset",
        "placeholder metrics",
        "dummy record",
        "dummy dataset",
        "stub implementation",
        "not implemented",
        "todo: implement",
        "return []  #",
        "return {}  #",
    )
    for line in lowered_content.splitlines():
        if _placeholder_line_is_defensive(line):
            continue
        if any(marker in line for marker in blocking_markers):
            return True
    return False


def _placeholder_line_is_defensive(line: str) -> bool:
    """Return true for policy/error text that forbids placeholder execution.

    Generated projects often include explicit guards such as "refusing to emit
    placeholder metrics". Those are desirable, not evidence that the benchmark
    path is a stub. Keep this line-based so a genuine stub elsewhere in the
    same file is still caught.
    """

    if "placeholder" not in line and "dummy" not in line:
        return False
    defensive_markers = (
        "no placeholder",
        "not placeholder",
        "without placeholder",
        "avoid placeholder",
        "prevent placeholder",
        "reject placeholder",
        "refuse placeholder",
        "refusing to",
        "do not use placeholder",
        "must not use placeholder",
        "should not use placeholder",
        "not a placeholder",
        "no dummy",
        "not dummy",
        "without dummy",
        "avoid dummy",
    )
    return any(marker in line for marker in defensive_markers)


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
