from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from simple_ar.app.usage import summarize_usage
from simple_ar.code_task.editing.scope import (
    allowed_patterns_from_manifest,
    is_edit_allowed_path,
    protected_patterns_from_manifest,
)
from simple_ar.code_task.memory import record_review_finding, task_memory_context
from simple_ar.code_task.runtime.state import code_task_paths, load_code_task_manifest
from simple_ar.core.artifacts import append_jsonl, read_json, read_jsonl, read_text, write_json
from simple_ar.integrations.llm import LLMClient, LLMError, LLMUsage
from simple_ar.reviewing.schema import ReviewFinding, normalize_review_findings, review_report


MessageCallback = Callable[[str], None]

CODE_TASK_REVIEW_SYSTEM = (
    "You are a strict but practical senior code reviewer for an isolated code-task workspace. "
    "You cannot edit files. Review the applied patch for scope, interface, logic, tests, benchmark integrity, "
    "and repair risk. Return only JSON."
)


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
    llm_findings: list[ReviewFinding] = []
    if use_llm:
        try:
            _emit(message_callback, f"Calling LLM reviewer for code-task {phase}.")
            client = LLMClient.from_env(
                model=model,
                usage_callback=lambda usage: _record_review_usage(
                    paths.meta_dir,
                    usage,
                    message_callback=message_callback,
                ),
            )
            response = client.ask_json(
                CODE_TASK_REVIEW_SYSTEM,
                _review_prompt(
                    root,
                    manifest=manifest,
                    phase=phase,
                    changed_files=changed_files,
                    max_source_chars_per_file=max_source_chars_per_file,
                ),
                label=f"code-task-review-{phase}",
            )
            llm_findings = normalize_review_findings(
                response.get("findings"),
                source="code-task.llm-reviewer",
                default_category="llm_review",
                default_evidence=["code_task/patch.diff"],
                max_findings=16,
            )
        except LLMError as exc:
            _emit(message_callback, f"LLM reviewer unavailable; keeping deterministic review only. {exc}")

    findings = _dedupe_findings([*deterministic, *llm_findings])
    report = review_report(
        reviewer="code-task-reviewer",
        subject=phase,
        findings=findings,
        metadata={
            "phase": phase,
            "changed_files": changed_files,
            "patch_diff": "code_task/patch.diff" if (paths.task_dir / "patch.diff").is_file() else "",
        },
    )
    report_path = paths.meta_dir / ("review_report.json" if phase == "post_apply" else f"review_report_{phase}.json")
    write_json(report_path, report.model_dump(mode="json"))
    for finding in findings:
        record_review_finding(
            root,
            {
                "key": finding.key or f"{phase}:{finding.category}:{finding.summary[:40]}",
                "severity": finding.severity,
                "category": finding.category,
                "summary": finding.summary,
                "evidence": finding.evidence,
                "recommendation": finding.recommendation,
                "source": finding.source,
            },
        )
    return CodeTaskReviewResult(
        run_dir=root,
        report_path=report_path,
        status=report.status,
        blocking_count=report.summary.get("blocking_count", 0),
        warning_count=report.summary.get("warning_count", 0),
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
    return (
        "Return JSON with `findings`: a list of objects with fields "
        "`severity` (blocking|warning|info), `category`, `summary`, `evidence`, and `recommendation`.\n"
        "Use `blocking` only when the evidence clearly shows the patch should not proceed.\n"
        "Prefer concrete, bounded findings over style feedback.\n\n"
        f"Phase: {phase}\n\n"
        f"Task:\n{_read_optional_text(paths.task_dir / 'task.md')}\n\n"
        f"Task memory:\n{task_memory_context(run_dir)}\n\n"
        f"Manifest patch section:\n{json.dumps(manifest.get('patch', {}), indent=2, ensure_ascii=False)}\n\n"
        f"Validation report:\n{json.dumps(_read_optional_json(paths.meta_dir / 'validation_report.json'), indent=2, ensure_ascii=False)}\n\n"
        f"Patched run record:\n{json.dumps(_run_record(manifest, 'patched'), indent=2, ensure_ascii=False)}\n\n"
        f"Patch diff:\n```diff\n{_clip(_read_optional_text(paths.task_dir / 'patch.diff'), 9000)}\n```\n\n"
        f"Changed file snippets:\n{snippets or 'No changed file snippets available.'}"
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


def _record_review_usage(
    meta_dir: Path,
    usage: LLMUsage,
    *,
    message_callback: MessageCallback | None,
) -> None:
    usage_path = meta_dir / "llm_usage.jsonl"
    row = usage.to_row()
    row["stage"] = "code_task.review"
    append_jsonl(usage_path, row)
    write_json(meta_dir / "llm_usage_summary.json", summarize_usage(read_jsonl(usage_path)))
    _emit(
        message_callback,
        f"LLM usage {row.get('label', '')}: "
        f"{row['prompt_tokens']} input + {row['completion_tokens']} output = "
        f"{row['total_tokens']} tokens ({row['source']}).",
    )


def _dedupe_findings(rows: list[ReviewFinding]) -> list[ReviewFinding]:
    found: dict[str, ReviewFinding] = {}
    for row in rows:
        key = row.key or f"{row.severity}:{row.category}:{row.summary}"
        if key not in found:
            found[key] = row.model_copy(update={"key": key})
    return list(found.values())


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


def _emit(callback: MessageCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)
