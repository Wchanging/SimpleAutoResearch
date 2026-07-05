from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from simple_ar.code_task.editing.scope import (
    allowed_patterns_from_manifest,
    is_edit_allowed_path,
    protected_patterns_from_manifest,
)
from simple_ar.code_task.generation.task_contract import load_task_contract
from simple_ar.code_task.memory import record_review_finding, task_memory_context
from simple_ar.code_task.analysis.interfaces import find_local_api_mismatches, project_api_contract
from simple_ar.code_task.review_pipeline import (
    build_review_clusters,
    build_review_index,
    compact_review_index,
    snippets_for_cluster,
)
from simple_ar.code_task.runtime.state import code_task_paths, load_code_task_manifest
from simple_ar.core.artifacts import read_json, read_text, write_json
from simple_ar.reviewing.schema import ReviewFinding
from simple_ar.code_task.reviewing import build_review_artifact, review_prompt, run_llm_review


MessageCallback = Callable[[str], None]

@dataclass(frozen=True, slots=True)
class CodeTaskReviewResult:
    run_dir: Path
    report_path: Path
    status: str
    blocking_count: int
    warning_count: int


def review_code_task_changes(
    run_dir: Path,
    *,
    phase: str = "post_apply",
    model: str | None = None,
    use_llm: bool = True,
    max_source_chars_per_file: int = 3000,
    message_callback: MessageCallback | None = None,
) -> CodeTaskReviewResult:
    """Review the current code-task patch and write structured findings.

    The review is advisory except for deterministic scope violations. Findings
    are recorded into task memory so later planning and repair prompts retain
    the reviewer context.
    """

    root = Path(run_dir)
    paths = code_task_paths(root)
    manifest = load_code_task_manifest(root)
    changed_files = _changed_files(manifest, paths)
    deterministic = _deterministic_findings(root, manifest, changed_files)
    contract = _contract_from_run(paths)
    review_index = build_review_index(
        paths.workspace_dir,
        result_schema=_result_schema_from_manifest(manifest),
        contract=contract,
    )
    review_clusters = build_review_clusters(
        review_index,
        deterministic_findings=deterministic,
        max_clusters=4,
        max_files_per_cluster=5,
    )
    write_json(_review_index_path(paths.meta_dir, phase), review_index)
    write_json(
        _review_clusters_path(paths.meta_dir, phase),
        {
            "schema_version": "code_task_review_clusters.v1",
            "phase": phase,
            "cluster_count": len(review_clusters),
            "clusters": review_clusters,
        },
    )
    llm_findings = _layered_llm_findings(
        run_dir=root,
        manifest=manifest,
        phase=phase,
        changed_files=changed_files,
        review_index=review_index,
        review_clusters=review_clusters,
        model=model,
        use_llm=use_llm,
        max_source_chars_per_file=max_source_chars_per_file,
        message_callback=message_callback,
    )

    report = build_review_artifact(
        reviewer="code-task-reviewer",
        subject=phase,
        findings=[*deterministic, *llm_findings],
        metadata={
            "phase": phase,
            "changed_files": changed_files,
            "patch_diff": "code_task/patch.diff" if (paths.task_dir / "patch.diff").is_file() else "",
            "review_mode": "layered",
            "review_index": _relative_meta_path(phase, "review_index"),
            "review_clusters": _relative_meta_path(phase, "review_clusters"),
            "review_cluster_count": len(review_clusters),
        },
    )
    report_path = paths.meta_dir / ("review_report.json" if phase == "post_apply" else f"review_report_{phase}.json")
    write_json(report_path, report)
    findings = report.get("findings", [])
    for row in findings:
        if not isinstance(row, dict):
            continue
        record_review_finding(
            root,
            {
                "key": row.get("key") or f"{phase}:{row.get('category', '')}:{str(row.get('summary', ''))[:40]}",
                "severity": row.get("severity", "info"),
                "category": row.get("category", "general"),
                "summary": row.get("summary", ""),
                "evidence": row.get("evidence", []),
                "recommendation": row.get("recommendation", ""),
                "source": row.get("source", "reviewer"),
            },
        )
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    return CodeTaskReviewResult(
        run_dir=root,
        report_path=report_path,
        status=str(report.get("status", "unknown")),
        blocking_count=int(summary.get("blocking_count", 0) or 0),
        warning_count=int(summary.get("warning_count", 0) or 0),
    )


def _deterministic_findings(root: Path, manifest: dict[str, Any], changed_files: list[str]) -> list[ReviewFinding]:
    paths = code_task_paths(root)
    findings: list[ReviewFinding] = []
    allowed = allowed_patterns_from_manifest(manifest)
    protected = protected_patterns_from_manifest(manifest)
    for path in changed_files:
        if not is_edit_allowed_path(path, allowed_patterns=allowed, protected_patterns=protected):
            findings.append(
                ReviewFinding(
                    key=f"scope:{path}",
                    severity="blocking",
                    category="scope",
                    summary=f"Changed file `{path}` is outside the configured editable scope.",
                    evidence=["code_task/patch.diff", "manifest.json"],
                    recommendation="Regenerate the proposal inside edit_scope or update the config deliberately.",
                    source="code-task.rule-review",
                )
            )
    for mismatch in find_local_api_mismatches(paths.workspace_dir, relevant_paths=changed_files):
        caller = str(mismatch.get("caller", ""))
        target_path = str(mismatch.get("target_path", ""))
        target_module = str(mismatch.get("target_module", ""))
        missing_symbol = str(mismatch.get("missing_symbol", ""))
        available = ", ".join(mismatch.get("available_symbols", [])) or "none"
        findings.append(
            ReviewFinding(
                key=f"interface:{caller}:{mismatch.get('line', 0)}:{target_module}:{missing_symbol}",
                severity="blocking",
                category="interface_compatibility",
                summary=(
                    f"`{caller}` references missing local API `{target_module}.{missing_symbol}`; "
                    f"available symbols: {available}."
                ),
                evidence=[caller, target_path, "code_task/patch.diff"],
                recommendation="Align the caller with the actual local module API before running the benchmark.",
                source="code-task.rule-review",
            )
        )
    if not changed_files and _patch_status(manifest) == "applied":
        findings.append(
            ReviewFinding(
                key="patch:no-changed-files",
                severity="warning",
                category="patch",
                summary="Patch status is applied, but no changed files are recorded.",
                evidence=["manifest.json"],
                recommendation="Inspect applied_edits.json and patch.diff before trusting the run.",
                source="code-task.rule-review",
            )
        )
    validation = _read_optional_json(paths.meta_dir / "validation_report.json")
    if validation.get("status") == "failed":
        findings.append(
            ReviewFinding(
                key="validation:failed",
                severity="blocking",
                category="validation",
                summary="Static validation failed after applying the patch.",
                evidence=["code_task/meta/validation_report.json"],
                recommendation="Fix validation errors before treating benchmark results as meaningful.",
                source="code-task.rule-review",
            )
        )
    run_record = _run_record(manifest, "patched")
    if run_record and run_record.get("status") not in {"passed", "skipped"}:
        findings.append(
            ReviewFinding(
                key="benchmark:patched-failed",
                severity="warning",
                category="benchmark",
                summary=f"Patched benchmark status is `{run_record.get('status', 'unknown')}`.",
                evidence=["code_task/run/patched/report.md"],
                recommendation="Use failure analysis and repair memory before another proposal.",
                source="code-task.rule-review",
            )
        )
    if (paths.task_dir / "patch.diff").is_file() and (paths.task_dir / "patch.diff").stat().st_size == 0:
        findings.append(
            ReviewFinding(
                key="patch:empty-diff",
                severity="warning",
                category="patch",
                summary="patch.diff is empty even though review was requested.",
                evidence=["code_task/patch.diff"],
                recommendation="Verify whether the proposal was already applied or produced no effect.",
                source="code-task.rule-review",
            )
        )
    return findings


def _layered_llm_findings(
    *,
    run_dir: Path,
    manifest: dict[str, Any],
    phase: str,
    changed_files: list[str],
    review_index: dict[str, Any],
    review_clusters: list[dict[str, Any]],
    model: str | None,
    use_llm: bool,
    max_source_chars_per_file: int,
    message_callback: MessageCallback | None,
) -> list[ReviewFinding]:
    paths = code_task_paths(run_dir)
    compact_index = compact_review_index(review_index)
    interface_mismatches = find_local_api_mismatches(paths.workspace_dir, relevant_paths=changed_files)
    interface_paths = _dedupe(
        [*changed_files, *(str(row.get("target_path", "")) for row in interface_mismatches)]
    )[:20]
    findings: list[ReviewFinding] = []
    for cluster in review_clusters:
        snippets = snippets_for_cluster(
            paths.workspace_dir,
            cluster,
            chars_per_file=max_source_chars_per_file,
        )
        if not snippets:
            continue
        cluster_id = str(cluster.get("cluster_id") or "cluster")
        prompt = _review_prompt(
            run_dir,
            manifest=manifest,
            phase=phase,
            changed_files=changed_files,
            review_index=compact_index,
            review_cluster=cluster,
            interface_paths=interface_paths,
            interface_mismatches=interface_mismatches,
            snippets=snippets,
        )
        findings.extend(
            run_llm_review(
                meta_dir=paths.meta_dir,
                prompt=prompt,
                label=f"code-task-review-{phase}-{cluster_id}",
                source=f"code-task.llm-reviewer.{cluster_id}",
                default_category="llm_review",
                default_evidence=["code_task/patch.diff", _relative_meta_path(phase, "review_index")],
                model=model,
                use_llm=use_llm,
                message_callback=message_callback,
                max_findings=5,
                allow_blocking=False,
            )
        )
    return findings[:16]


def _review_prompt(
    run_dir: Path,
    *,
    manifest: dict[str, Any],
    phase: str,
    changed_files: list[str],
    review_index: dict[str, Any],
    review_cluster: dict[str, Any],
    interface_paths: list[str],
    interface_mismatches: list[dict[str, Any]],
    snippets: list[str],
) -> str:
    paths = code_task_paths(run_dir)
    return review_prompt(
        instructions=(
            "Review the applied patch for scope, interface compatibility, logic, tests, benchmark integrity, "
            "and repair risk. Use the full review index to understand project shape, but focus findings on "
            "the current review cluster and the supplied patch evidence. Do not request broad rewrites."
        ),
        context={
            "phase": phase,
            "task": _read_optional_text(paths.task_dir / "task.md"),
            "task_memory": task_memory_context(run_dir),
            "changed_files": changed_files,
            "review_index": review_index,
            "review_cluster": review_cluster,
            "manifest_patch": manifest.get("patch", {}),
            "validation_report": _read_optional_json(paths.meta_dir / "validation_report.json"),
            "patched_run_record": _run_record(manifest, "patched"),
            "patch_diff": _clip(_read_optional_text(paths.task_dir / "patch.diff"), 9000),
            "local_api_contract": project_api_contract(
                paths.workspace_dir,
                relevant_paths=interface_paths,
            ),
            "local_api_mismatches": interface_mismatches,
        },
        snippets=snippets,
    )


def _review_index_path(meta_dir: Path, phase: str) -> Path:
    suffix = "" if phase == "post_apply" else f"_{phase}"
    return meta_dir / f"review_index{suffix}.json"


def _review_clusters_path(meta_dir: Path, phase: str) -> Path:
    suffix = "" if phase == "post_apply" else f"_{phase}"
    return meta_dir / f"review_clusters{suffix}.json"


def _relative_meta_path(phase: str, kind: str) -> str:
    suffix = "" if phase == "post_apply" else f"_{phase}"
    return f"code_task/meta/{kind}{suffix}.json"


def _result_schema_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    benchmark = manifest.get("benchmark")
    benchmark = benchmark if isinstance(benchmark, dict) else {}
    primary = str(benchmark.get("primary_metric") or "score").strip() or "score"
    directions = benchmark.get("metric_directions")
    directions = directions if isinstance(directions, dict) else {}
    required = [primary]
    required.extend(str(key) for key in directions if str(key).strip() and str(key) not in required)
    return {
        "primary_metric": primary,
        "required_metrics": required,
        "metric_directions": {str(key): str(value) for key, value in directions.items()},
    }


def _contract_from_run(paths: Any) -> dict[str, Any]:
    contract = load_task_contract(paths.meta_dir)
    if contract:
        return contract
    task_text = _read_optional_text(paths.task_dir / "task.md")
    return {
        "objective": task_text[:2000],
        "task": task_text[:4000],
        "success_criteria": [],
    }


def _changed_files(manifest: dict[str, Any], paths: Any) -> list[str]:
    patch = manifest.get("patch")
    if isinstance(patch, dict):
        rows = [str(item) for item in patch.get("changed_files", []) if str(item).strip()]
        if rows:
            return _dedupe(rows)
    applied = _read_optional_json(paths.meta_dir / "applied_edits.json")
    return _dedupe(str(item) for item in applied.get("changed_files", []) if str(item).strip())


def _run_record(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    benchmark = manifest.get("benchmark")
    if not isinstance(benchmark, dict):
        return {}
    record = benchmark.get(name)
    return record if isinstance(record, dict) else {}


def _patch_status(manifest: dict[str, Any]) -> str:
    patch = manifest.get("patch")
    if isinstance(patch, dict):
        return str(patch.get("status", ""))
    return ""


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = read_json(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_optional_text(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return read_text(path)
    except OSError:
        return ""


def _dedupe(values: Any) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).replace("\\", "/").strip()
        if text and text not in seen:
            seen.add(text)
            rows.append(text)
    return rows


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
