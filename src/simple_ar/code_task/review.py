from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from simple_ar.code_task.editing.scope import (
    allowed_patterns_from_manifest,
    is_edit_allowed_path,
    protected_patterns_from_manifest,
)
from simple_ar.code_task.memory import record_review_finding, task_memory_context
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
    llm_findings = run_llm_review(
        meta_dir=paths.meta_dir,
        prompt=_review_prompt(
            root,
            manifest=manifest,
            phase=phase,
            changed_files=changed_files,
            max_source_chars_per_file=max_source_chars_per_file,
        ),
        label=f"code-task-review-{phase}",
        source="code-task.llm-reviewer",
        default_category="llm_review",
        default_evidence=["code_task/patch.diff"],
        model=model,
        use_llm=use_llm,
        message_callback=message_callback,
        max_findings=16,
    )

    report = build_review_artifact(
        reviewer="code-task-reviewer",
        subject=phase,
        findings=[*deterministic, *llm_findings],
        metadata={
            "phase": phase,
            "changed_files": changed_files,
            "patch_diff": "code_task/patch.diff" if (paths.task_dir / "patch.diff").is_file() else "",
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


def _review_prompt(
    run_dir: Path,
    *,
    manifest: dict[str, Any],
    phase: str,
    changed_files: list[str],
    max_source_chars_per_file: int,
) -> str:
    paths = code_task_paths(run_dir)
    snippets = "\n\n".join(
        f"### {path}\n```text\n{_source_snippet(paths.workspace_dir / path, max_source_chars_per_file)}\n```"
        for path in changed_files[:8]
    )
    return review_prompt(
        instructions=(
            "Review the applied patch for scope, interface compatibility, logic, tests, benchmark integrity, "
            "and repair risk."
        ),
        context={
            "phase": phase,
            "task": _read_optional_text(paths.task_dir / "task.md"),
            "task_memory": task_memory_context(run_dir),
            "manifest_patch": manifest.get("patch", {}),
            "validation_report": _read_optional_json(paths.meta_dir / "validation_report.json"),
            "patched_run_record": _run_record(manifest, "patched"),
            "patch_diff": _clip(_read_optional_text(paths.task_dir / "patch.diff"), 9000),
        },
        snippets=[snippets] if snippets else [],
    )


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


def _source_snippet(path: Path, limit: int) -> str:
    text = _read_optional_text(path)
    if len(text) <= limit:
        return text
    half = max(800, limit // 2)
    return text[:half].rstrip() + "\n\n# ... middle omitted ...\n\n" + text[-half:].lstrip()


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
