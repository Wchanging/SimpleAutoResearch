from __future__ import annotations

"""Repair helpers for generated-project code-task outputs.

This module is intentionally separate from ``simple_ar.code_task.execution.repair``:
that module proposes human-reviewed patch edits for existing-project code-task
runs, while this module performs bounded automatic repair inside an already
generated project workspace. The edit application is shared and deterministic:
structured actions and whole-file content share one application path. Rejected
actions do not fall through to a second file-writing strategy.
"""

import json
import py_compile
import re
import ast
from pathlib import Path
from typing import Any, Mapping

from simple_ar.core.artifacts import write_json
from simple_ar.code_task.analysis.interfaces import (
    dependency_context,
    find_return_contract_mismatches,
    public_api,
)
from simple_ar.code_task.analysis.entrypoints import source_suppresses_entrypoint_traceback
from simple_ar.code_task.analysis.resource_static import analyze_resource_risks
from simple_ar.code_task.analysis.python_source import non_ascii_identifiers
from simple_ar.code_task.editing.actions import apply_repair_actions
from simple_ar.code_task.editing.snapshots import FileSnapshotSet, create_file_snapshot_set
from simple_ar.code_task.generation.common import safe_relative_path
from simple_ar.code_task.review_pipeline import build_review_index, compact_review_index
from simple_ar.code_task.repair_contract import atomic_patch_set_record, normalize_repair_plan
from simple_ar.integrations.llm import LLMClient, LLMError

_RUN_REPAIR_MAX_FILES = 8


def repair_generated_project_from_review(
    *,
    project_dir: Path,
    review_report: Mapping[str, Any],
    output_path: Path,
    code_artifacts: Mapping[str, Any] | None = None,
    architecture_plan: Mapping[str, Any] | None = None,
    result_schema: Mapping[str, Any] | None = None,
    contract: Mapping[str, Any] | None = None,
    dependency_advice: Mapping[str, Any] | None = None,
    previous_repair_context: str = "",
    client: LLMClient | None = None,
) -> dict[str, Any]:
    """Apply model-proposed review repairs; never invent project-specific fixes."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "schema_version": "greenfield_review_repair.v1",
        "status": "skipped",
        "strategy": "llm_review_repair",
        "review_status": str(review_report.get("status", "unknown")),
        "changed_files": [],
        "unresolved_errors": [],
        "notes": [],
    }
    if not project_dir.is_dir():
        summary["status"] = "failed"
        summary["unresolved_errors"].append(f"Missing generated project directory: {project_dir}")
        write_json(output_path, summary)
        return summary

    if client is None:
        summary["notes"].append("Model repair unavailable; original files and failed review remain unchanged.")
        write_json(output_path, summary)
        return summary

    snapshot = create_file_snapshot_set(
        workspace_dir=project_dir,
        snapshot_root=output_path.parent / "repair_snapshots",
        label="review-repair",
    )

    changed: list[str] = []
    unresolved: list[str] = []
    if _review_needs_llm_repair(review_report):
        regenerated = _regenerate_review_failed_files(
            project_dir=project_dir,
            review_report=review_report,
            code_artifacts=code_artifacts or {},
            architecture_plan=architecture_plan or {},
            result_schema=result_schema or {},
            contract=contract or {},
            dependency_advice=dependency_advice or {},
            previous_repair_context=previous_repair_context,
            client=client,
            changed=changed,
            notes=summary["notes"],
            unresolved=unresolved,
            snapshot=snapshot,
        )
        if regenerated:
            summary["regenerated_files"] = regenerated

    summary["changed_files"] = changed
    summary["unresolved_errors"] = unresolved
    if unresolved:
        summary["status"] = "failed"
    elif changed:
        summary["status"] = "patched"
        summary["notes"].append("Applied model-proposed edits; rerun review before execution.")
    else:
        summary["notes"].append("No review repairs were applied.")
    _attach_snapshot_summary(summary, snapshot)
    write_json(output_path, summary)
    return summary


def _regenerate_review_failed_files(
    *,
    project_dir: Path,
    review_report: Mapping[str, Any],
    code_artifacts: Mapping[str, Any],
    architecture_plan: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    contract: Mapping[str, Any],
    dependency_advice: Mapping[str, Any],
    previous_repair_context: str,
    client: LLMClient,
    changed: list[str],
    notes: list[str],
    unresolved: list[str],
    snapshot: FileSnapshotSet,
) -> list[dict[str, Any]]:
    target_paths = _review_repair_target_paths(review_report=review_report, code_artifacts=code_artifacts)
    if not target_paths:
        return []
    file_specs = _architecture_file_specs(architecture_plan)
    regenerated: list[dict[str, Any]] = []
    for rel_path in target_paths:
        target = project_dir / rel_path
        if rel_path in changed and target.is_file() and not _compile_error(target):
            continue
        spec = file_specs.get(rel_path, {"path": rel_path, "purpose": "Repair generated project file.", "dependencies": []})
        previous = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        try:
            response = client.ask_json(
                "You repair generated Python project files. Return only JSON with `summary` and either `actions` or whole-file `content`, not both.",
                _review_file_repair_prompt(
                    rel_path=rel_path,
                    file_spec=spec,
                    project_dir=project_dir,
                    result_schema=result_schema,
                    contract=contract,
                    dependency_advice=dependency_advice,
                    review_report=review_report,
                    previous_repair_context=previous_repair_context,
                ),
                label=f"greenfield-review-repair-{rel_path}",
            )
        except LLMError as exc:
            unresolved.append(f"{rel_path}: LLM review repair failed: {exc}")
            continue
        applied = _apply_llm_file_repair_response(
            project_dir=project_dir,
            rel_path=rel_path,
            response=response,
            previous_content=previous,
            previous_exists=target.is_file(),
            changed=changed,
            notes=notes,
            unresolved=unresolved,
            mode_prefix="llm_review_repair",
            fallback_summary="Regenerated after review failure.",
            snapshot=snapshot,
        )
        if applied is not None:
            regenerated.append(applied)
    return regenerated


def _apply_llm_file_repair_response(
    *,
    project_dir: Path,
    rel_path: str,
    response: Mapping[str, Any],
    previous_content: str,
    previous_exists: bool,
    changed: list[str],
    notes: list[str],
    unresolved: list[str],
    mode_prefix: str,
    fallback_summary: str,
    snapshot: FileSnapshotSet | None = None,
) -> dict[str, Any] | None:
    """Normalize model output once, then use the shared action application."""

    target = project_dir / rel_path
    if _response_declares_no_change(response):
        notes.append(f"Skipped {rel_path}; repair response declared no change.")
        return None
    actions = response.get("actions")
    structured = isinstance(actions, list) and bool(actions)
    if not structured:
        content = str(response.get("content", "")).strip()
        if not content:
            unresolved.append(f"{rel_path}: LLM repair returned neither actions nor content.")
            return None
        content = _strip_markdown_fence(content.rstrip() + "\n")
        actions = [{
            "action": "rewrite_file", "path": rel_path, "content": content,
            "rationale": str(response.get("summary") or fallback_summary),
        }]
    if snapshot is not None:
        snapshot.capture(rel_path)
    applied = apply_repair_actions(project_dir, actions, allowed_paths={rel_path})
    error = ""
    if applied["rejected_actions"]:
        error = "structured repair actions were rejected: " + json.dumps(
            applied["rejected_actions"], ensure_ascii=False
        )[:1000]
    elif applied["status"] != "patched":
        notes.append(f"Skipped {rel_path}; repair actions made no changes.")
        return None
    else:
        error = (
            (_compile_error(target) if target.suffix == ".py" and target.is_file() else "")
            or _post_write_static_guard(target=target, rel_path=rel_path)
            or _public_api_change_guard(action_result=applied, response=response)
        )
    if error:
        _restore_repair_target(
            target, previous_content, previous_exists,
            snapshot=snapshot, rel_path=rel_path,
        )
        unresolved.append(f"{rel_path}: repair rejected: {error}")
        return None
    if rel_path not in changed:
        changed.append(rel_path)
    notes.append(f"Applied repair actions to {rel_path}.")
    return {
        "path": rel_path,
        "mode": f"{mode_prefix}_actions" if structured else mode_prefix,
        "line_count": _file_line_count(target),
        "summary": str(response.get("summary") or fallback_summary)[:500],
        "public_api": public_api(target) if target.suffix == ".py" and target.is_file() else [],
        "edit_application": applied,
    }


def _restore_repair_target(
    target: Path,
    previous_content: str,
    previous_exists: bool,
    *,
    snapshot: FileSnapshotSet | None = None,
    rel_path: str = "",
) -> None:
    if snapshot is not None and rel_path:
        snapshot.restore([rel_path])
        return
    if previous_exists:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(previous_content, encoding="utf-8")
    elif target.exists():
        target.unlink()


def _attach_snapshot_summary(summary: dict[str, Any], snapshot: FileSnapshotSet) -> None:
    if snapshot.captured_count or snapshot.restored_count:
        snapshot.write_manifest()
        summary["snapshot"] = snapshot.artifact_record()


def _response_declares_no_change(response: Mapping[str, Any]) -> bool:
    status = str(response.get("status") or response.get("action") or "").strip().lower().replace("-", "_")
    if status in {"no_change", "skip", "skipped", "not_applicable"}:
        return True
    actions = response.get("actions")
    rows = actions if isinstance(actions, list) else []
    meaningful = [row for row in rows if isinstance(row, Mapping)]
    if meaningful and all(
        str(row.get("action") or "").strip().lower().replace("-", "_") in {"no_change", "skip"}
        for row in meaningful
    ):
        return True
    return False


def _public_api_change_guard(*, action_result: Mapping[str, Any], response: Mapping[str, Any]) -> str:
    if response.get("allow_api_breaking_change"):
        return ""
    applied = action_result.get("applied_actions")
    rows = [row for row in applied if isinstance(row, Mapping)] if isinstance(applied, list) else []
    for row in rows:
        before = row.get("before_public_api")
        after = row.get("after_public_api")
        before_api = [str(item) for item in before] if isinstance(before, list) else []
        after_api = [str(item) for item in after] if isinstance(after, list) else []
        if _public_api_contract_breaks(before_api, after_api):
            return "public_api_signature_would_change"
    return ""


def _public_api_contract_breaks(before_api: list[str], after_api: list[str]) -> bool:
    before_map = _public_api_map(before_api)
    after_map = _public_api_map(after_api)
    for name, signature in before_map.items():
        if name not in after_map:
            return True
        if after_map[name] != signature:
            return True
    return False


def _public_api_map(rows: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in rows:
        name = _api_name_from_signature(str(item))
        if name and name not in result:
            result[name] = str(item)
    return result


def _post_write_static_guard(*, target: Path, rel_path: str) -> str:
    if target.suffix != ".py" or not target.is_file():
        return ""
    try:
        source = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"could_not_read_repaired_file:{exc}"
    suppressed = source_suppresses_entrypoint_traceback(source, path=rel_path)
    if suppressed:
        return (
            f"{suppressed}; generated entrypoints must preserve the original traceback "
            "or re-raise broad exceptions so runtime repair can localize the true failing file."
        )
    identifiers = non_ascii_identifiers(source, path=rel_path)
    if identifiers:
        first = identifiers[0]
        return (
            f"non_ascii_python_identifier:{rel_path}:{first.get('line') or 'unknown'}:"
            f"{first.get('identifier')}; generated Python identifiers must be ASCII-only."
        )
    return ""


def _api_name_from_signature(value: str) -> str:
    text = value.strip()
    if text.startswith("class "):
        text = text.split(" ", 1)[1]
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", text)
    return match.group(1) if match else ""


def _file_line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        return max(1, len(path.read_text(encoding="utf-8", errors="replace").splitlines()))
    except OSError:
        return 0






def _review_needs_llm_repair(review_report: Mapping[str, Any]) -> bool:
    """Use the review findings; this boundary has not silently repaired them."""

    return any(
        (str(item.get("severity", "blocking")).strip() or "blocking") == "blocking"
        for item in _review_findings(review_report)
    )










def _review_repair_target_paths(
    *,
    review_report: Mapping[str, Any],
    code_artifacts: Mapping[str, Any],
) -> list[str]:
    targets: list[str] = []
    generated = code_artifacts.get("generated_files")
    if isinstance(generated, list):
        for row in generated:
            if not isinstance(row, Mapping):
                continue
            path = safe_relative_path(str(row.get("path", "")))
            if not path or not path.endswith(".py") or path.endswith("/__init__.py"):
                continue
            if row.get("mode") == "fallback":
                targets.append(path)
    findings = _review_findings(review_report)
    targets.extend(_paths_from_review_findings(findings))
    if not targets:
        targets.extend(sorted(_generated_python_paths(code_artifacts))[:5])
    return list(dict.fromkeys(path for path in targets if path))


def _review_signal_text(findings: list[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for item in findings:
        parts.extend(
            [
                str(item.get("summary", "")),
                str(item.get("recommendation", "")),
                str(item.get("category", "")),
                " ".join(str(row) for row in item.get("evidence", []) if isinstance(row, str))
                if isinstance(item.get("evidence"), list)
                else "",
            ]
        )
    return " ".join(parts).lower()


def _paths_from_review_findings(findings: list[Mapping[str, Any]]) -> list[str]:
    text = _review_signal_text(findings)
    return _paths_from_review_summaries(text)


def _paths_from_review_summaries(text: str) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(r"(?<![\w./-])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.py", text):
        path = safe_relative_path(match.group(0))
        if path:
            paths.append(path)
    return list(dict.fromkeys(paths))


def _architecture_file_specs(architecture_plan: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    files = architecture_plan.get("files")
    rows = [row for row in files if isinstance(row, Mapping)] if isinstance(files, list) else []
    return {
        path: row
        for row in rows
        if (path := safe_relative_path(str(row.get("path", ""))))
    }


def _review_file_repair_prompt(
    *,
    rel_path: str,
    file_spec: Mapping[str, Any],
    project_dir: Path,
    result_schema: Mapping[str, Any],
    contract: Mapping[str, Any],
    dependency_advice: Mapping[str, Any],
    review_report: Mapping[str, Any],
    previous_repair_context: str = "",
) -> str:
    return (
        "Repair exactly one generated project file. The surrounding project already exists on disk; "
        "your file must integrate with the actual dependency APIs and must not install packages.\n\n"
        "Preferred output:\n"
        "- Return `actions` as a list of structured repair actions whenever possible.\n"
        "- `replace_block` actions must include unique `old_string` and `new_string` fields.\n"
        "- `rewrite_function` actions must include `function_name` and complete `new_source` fields.\n"
        "- `rewrite_file` actions must include the complete replacement file text in `content`.\n"
        "- `add_file` actions must include the complete new file text in `content`.\n"
        "- Use `rewrite_file` or top-level `content` only when the file-level contract is structurally wrong.\n"
        "- Public API signatures are preserved by default. If the root cause truly requires an API break, set "
        "`allow_api_breaking_change: true` and explain which producer/consumer files are covered by the repair.\n"
        "- If you choose a broad rewrite, explain why a smaller repair is insufficient in `summary`.\n\n"
        "Hard rules:\n"
        "- Return JSON only; do not use markdown fences.\n"
        "- Every action must include `action`, `path`, `rationale`, and the required action fields.\n"
        "- Keep paths and behavior local; no network, shell, credentials, or hidden downloads.\n"
        "- If task-relevant installed packages are available in dependency_advice, you may use them.\n"
        "- Preserve the exact public API requested by the file spec when practical.\n"
        "- Fix the implementation path that caused the review finding; do not satisfy implementation findings by documentation-only changes.\n"
        "- Do not fill missing required metrics with 0.0, empty records, or placeholder values. Fail clearly if a metric cannot be measured.\n"
        "- Do not hide the original exception in entrypoints. If you catch broad exceptions, also call traceback.print_exc(), "
        "logging.exception/logger.exception, or re-raise so later repair can see the real file and line.\n"
        "- If artifact_scan provides an expected_path, write the required artifact to that exact workspace-relative path.\n"
        "- For generated greenfield projects, task artifact paths such as `artifacts/results.json` normally resolve under "
        "`generated_project/artifacts/results.json` because the project root is `generated_project/`.\n"
        "- Required task artifacts include `artifacts/results.json` and `artifacts/report.md` whenever requested by the task; "
        "keep writer, config, and entrypoint paths consistent.\n"
        "- The benchmark parser still needs metrics printed by main.py as `metric_name: number`.\n\n"
        f"Target path: {rel_path}\n\n"
        f"File spec:\n{json.dumps(dict(file_spec), indent=2, ensure_ascii=False)}\n\n"
        f"Actual dependency APIs:\n{json.dumps(dependency_context(project_dir, file_spec), indent=2, ensure_ascii=False)}\n\n"
        f"Existing project APIs:\n{json.dumps(_project_api_snapshot(project_dir), indent=2, ensure_ascii=False)}\n\n"
        f"Project review index:\n{json.dumps(_generated_review_index(project_dir, result_schema=result_schema, contract=contract), indent=2, ensure_ascii=False)}\n\n"
        f"Result schema:\n{json.dumps(dict(result_schema), indent=2, ensure_ascii=False)}\n\n"
        f"Task contract:\n{json.dumps(_compact_for_prompt(contract), indent=2, ensure_ascii=False)}\n\n"
        f"Dependency advice:\n{json.dumps(_compact_for_prompt(dependency_advice), indent=2, ensure_ascii=False)}\n\n"
        f"Review report:\n{json.dumps(_compact_for_prompt(review_report), indent=2, ensure_ascii=False)}\n"
        f"\nPrevious repair context:\n{previous_repair_context[:12000] or 'No previous repair context recorded.'}\n"
    )


def _project_api_snapshot(project_dir: Path) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for path in sorted(project_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(project_dir).as_posix()
        rows[rel] = public_api(path)
    return rows


def _compact_for_prompt(value: Mapping[str, Any], *, limit: int = 12000) -> dict[str, Any]:
    text = json.dumps(dict(value), ensure_ascii=False, default=str)
    if len(text) <= limit:
        return dict(value)
    return {"truncated_json": text[:limit], "truncated": True}


def _strip_markdown_fence(value: str) -> str:
    stripped = value.strip()
    if not stripped.startswith("```"):
        return value
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).rstrip() + "\n"
    return value




def _review_findings(review_report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    findings = review_report.get("findings")
    if isinstance(findings, list):
        return [item for item in findings if isinstance(item, Mapping)]
    quality = review_report.get("quality")
    if isinstance(quality, Mapping):
        nested = quality.get("findings")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, Mapping)]
    return []








def repair_generated_project_from_run_failure(
    *,
    project_dir: Path,
    failure_analysis: Mapping[str, Any],
    stderr_text: str,
    output_path: Path,
    code_artifacts: Mapping[str, Any] | None = None,
    architecture_plan: Mapping[str, Any] | None = None,
    result_schema: Mapping[str, Any] | None = None,
    contract: Mapping[str, Any] | None = None,
    dependency_advice: Mapping[str, Any] | None = None,
    previous_repair_context: str = "",
    client: LLMClient | None = None,
) -> dict[str, Any]:
    """Apply bounded model-proposed repairs using the observed runtime failure."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "schema_version": "greenfield_run_repair.v1",
        "status": "skipped",
        "strategy": "llm_runtime_repair",
        "failure_status": str(failure_analysis.get("status", "unknown")),
        "changed_files": [],
        "unresolved_errors": [],
        "notes": [],
        "ablation": {
            "use_repair_memory": bool(previous_repair_context.strip()),
        },
    }
    if not project_dir.is_dir():
        summary["status"] = "failed"
        summary["unresolved_errors"].append(f"Missing generated project directory: {project_dir}")
        write_json(output_path, summary)
        return summary

    if client is None:
        summary["notes"].append("Model repair unavailable; original files and runtime failure remain unchanged.")
        write_json(output_path, summary)
        return summary

    snapshot = create_file_snapshot_set(
        workspace_dir=project_dir,
        snapshot_root=output_path.parent / "repair_snapshots",
        label="run-repair",
    )

    changed: list[str] = []
    if client is not None:
        regenerated = _regenerate_run_failed_files(
            project_dir=project_dir,
            failure_analysis=failure_analysis,
            stderr_text=stderr_text,
            code_artifacts=code_artifacts or {},
            architecture_plan=architecture_plan or {},
            result_schema=result_schema or {},
            contract=contract or {},
            dependency_advice=dependency_advice or {},
            previous_repair_context=previous_repair_context,
            client=client,
            changed=changed,
            notes=summary["notes"],
            unresolved=summary["unresolved_errors"],
            snapshot=snapshot,
            artifact_dir=output_path.parent,
        )
        if regenerated:
            summary["regenerated_files"] = regenerated
            summary["repair_plan"] = (output_path.parent / "run_repair_plan.json").as_posix()
            summary["atomic_patch_set"] = (output_path.parent / "atomic_patch_set.json").as_posix()
            compile_errors = _compile_project(project_dir)
            if not compile_errors:
                summary["status"] = "patched"
                summary["changed_files"] = changed
                summary["notes"].append("Regenerated bounded files after benchmark runtime failure.")
                _attach_snapshot_summary(summary, snapshot)
                write_json(output_path, summary)
                return summary
            summary["unresolved_errors"].extend(compile_errors)
            snapshot.restore()
            changed.clear()

    summary["changed_files"] = changed
    summary["notes"].append("No model-proposed run-failure repair was applied.")
    _attach_snapshot_summary(summary, snapshot)
    write_json(output_path, summary)
    return summary


def _regenerate_run_failed_files(
    *,
    project_dir: Path,
    failure_analysis: Mapping[str, Any],
    stderr_text: str,
    code_artifacts: Mapping[str, Any],
    architecture_plan: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    contract: Mapping[str, Any],
    dependency_advice: Mapping[str, Any],
    previous_repair_context: str,
    client: LLMClient,
    changed: list[str],
    notes: list[str],
    unresolved: list[str],
    snapshot: FileSnapshotSet,
    artifact_dir: Path,
) -> list[dict[str, Any]]:
    heuristic_targets = _run_repair_target_paths(
        project_dir=project_dir,
        failure_analysis=failure_analysis,
        stderr_text=stderr_text,
        code_artifacts=code_artifacts,
    )
    repair_context = _run_repair_context(
        project_dir=project_dir,
        failure_analysis=failure_analysis,
        stderr_text=stderr_text,
        code_artifacts=code_artifacts,
        heuristic_targets=heuristic_targets,
        result_schema=result_schema,
        contract=contract,
    )
    prompt_failure_analysis = dict(failure_analysis)
    raw_repair_plan = _plan_run_repair_targets(
        failure_analysis=prompt_failure_analysis,
        stderr_text=stderr_text,
        result_schema=result_schema,
        contract=contract,
        dependency_advice=dependency_advice,
        previous_repair_context=previous_repair_context,
        context=repair_context,
        client=client,
        unresolved=unresolved,
    )
    repair_plan = normalize_repair_plan(
        raw_repair_plan,
        failure_analysis=prompt_failure_analysis,
        fallback_targets=heuristic_targets,
        contract=contract,
    )
    write_json(artifact_dir / "run_repair_plan.json", repair_plan)
    target_paths = _repair_plan_targets(
        project_dir=project_dir,
        repair_plan=repair_plan,
        heuristic_targets=heuristic_targets,
    )
    if not target_paths:
        return []
    diagnosis = str(repair_plan.get("diagnosis") or repair_plan.get("root_cause") or "").strip()
    if diagnosis:
        notes.append(f"Run repair diagnosis: {diagnosis[:500]}")
    file_specs = _architecture_file_specs(architecture_plan)
    regenerated: list[dict[str, Any]] = []
    for rel_path in target_paths[:_RUN_REPAIR_MAX_FILES]:
        target = project_dir / rel_path
        if not target.is_file() or target.suffix != ".py":
            continue
        previous = target.read_text(encoding="utf-8", errors="replace")
        spec = file_specs.get(rel_path, {"path": rel_path, "purpose": "Repair generated runtime failure.", "dependencies": []})
        try:
            response = client.ask_json(
                "You repair one file in a generated Python experiment project after a benchmark runtime failure. Return only JSON with `summary` and either `actions` or whole-file `content`, not both.",
                _run_file_repair_prompt(
                    rel_path=rel_path,
                    current_content=previous,
                    file_spec=spec,
                    project_dir=project_dir,
                    failure_analysis=prompt_failure_analysis,
                    stderr_text=stderr_text,
                    repair_plan=repair_plan,
                    repair_context=repair_context,
                    previous_repair_context=previous_repair_context,
                    result_schema=result_schema,
                    contract=contract,
                    dependency_advice=dependency_advice,
                ),
                label=f"greenfield-run-repair-{rel_path}",
            )
        except LLMError as exc:
            unresolved.append(f"{rel_path}: LLM run repair failed: {exc}")
            continue
        applied = _apply_llm_file_repair_response(
            project_dir=project_dir,
            rel_path=rel_path,
            response=response,
            previous_content=previous,
            previous_exists=True,
            changed=changed,
            notes=notes,
            unresolved=unresolved,
            mode_prefix="llm_run_repair",
            fallback_summary="Regenerated after benchmark runtime failure.",
            snapshot=snapshot,
        )
        if applied is not None:
            regenerated.append(applied)
    snapshot_record = snapshot.artifact_record() if snapshot.captured_count or snapshot.restored_count else {}
    write_json(
        artifact_dir / "atomic_patch_set.json",
        atomic_patch_set_record(
            repair_plan=repair_plan,
            applied_records=regenerated,
            snapshot=snapshot_record,
        ),
    )
    return regenerated


def _plan_run_repair_targets(
    *,
    failure_analysis: Mapping[str, Any],
    stderr_text: str,
    result_schema: Mapping[str, Any],
    contract: Mapping[str, Any],
    dependency_advice: Mapping[str, Any],
    previous_repair_context: str,
    context: Mapping[str, Any],
    client: LLMClient,
    unresolved: list[str],
) -> dict[str, Any]:
    try:
        response = client.ask_json(
            "You diagnose a Python project runtime failure before any code rewrite. Return only JSON.",
            _run_repair_plan_prompt(
                context=context,
                failure_analysis=failure_analysis,
                stderr_text=stderr_text,
                result_schema=result_schema,
                contract=contract,
                dependency_advice=dependency_advice,
                previous_repair_context=previous_repair_context,
            ),
            label="greenfield-run-repair-plan",
        )
    except LLMError as exc:
        unresolved.append(f"run-repair-plan: LLM diagnosis failed: {exc}")
        return {}
    return response


def _repair_plan_targets(
    *,
    project_dir: Path,
    repair_plan: Mapping[str, Any],
    heuristic_targets: list[str],
) -> list[str]:
    planned = repair_plan.get("target_files")
    rows = planned if isinstance(planned, list) else []
    selected: list[str] = []
    for row in rows:
        raw_path = row.get("path") if isinstance(row, Mapping) else row
        path = safe_relative_path(_normalize_generated_project_path(str(raw_path or "")))
        if not path or not path.endswith(".py"):
            continue
        if (project_dir / path).is_file():
            selected.append(path)
    selected = list(dict.fromkeys(selected))
    heuristic_targets = list(dict.fromkeys(heuristic_targets))
    if selected and _repair_plan_has_clear_scope(repair_plan):
        return selected[:_RUN_REPAIR_MAX_FILES]
    if selected:
        return list(dict.fromkeys([*selected, *heuristic_targets[:2]]))[:_RUN_REPAIR_MAX_FILES]
    return heuristic_targets[:_RUN_REPAIR_MAX_FILES]


def _repair_plan_has_clear_scope(repair_plan: Mapping[str, Any]) -> bool:
    scope = str(repair_plan.get("repair_scope", "")).strip().lower()
    if scope in {"block", "function", "file"}:
        return True
    failure_kind = str(repair_plan.get("failure_kind", "")).strip().lower()
    if any(token in failure_kind for token in ("artifact", "watchdog", "warning_flood", "timeout")):
        return True
    affected = repair_plan.get("affected_contracts")
    if isinstance(affected, list):
        lowered = {str(item).strip().lower() for item in affected}
        if lowered & {"artifact", "resource", "runtime"}:
            return True
    return False


def _failure_graph_candidate_paths(
    failure_analysis: Mapping[str, Any],
    *,
    project_dir: Path,
) -> list[str]:
    graph = failure_analysis.get("failure_graph_data")
    if not isinstance(graph, Mapping):
        return []
    values: list[str] = []
    for key in ("traceback_files", "candidate_files", "signal_matched_files"):
        rows = graph.get(key)
        for raw in rows if isinstance(rows, list) else []:
            path = _normalize_generated_project_path(str(raw))
            if path and path.endswith(".py") and (project_dir / path).is_file():
                values.append(path)
    return list(dict.fromkeys(values))


def _run_repair_context(
    *,
    project_dir: Path,
    failure_analysis: Mapping[str, Any],
    stderr_text: str,
    code_artifacts: Mapping[str, Any],
    heuristic_targets: list[str],
    result_schema: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    all_paths = _generated_python_paths(code_artifacts, project_dir=project_dir)
    candidate_paths = list(dict.fromkeys([
        *heuristic_targets, *sorted(all_paths),
    ]))[:10]
    context = {
        "schema_version": "code_task_runtime_repair_context.v1",
        "heuristic_targets": heuristic_targets,
        "review_index": _generated_review_index(project_dir, result_schema=result_schema, contract=contract),
        "return_contract_mismatches": find_return_contract_mismatches(project_dir),
        "candidate_files": [
            _candidate_file_context(project_dir, path)
            for path in candidate_paths
            if (project_dir / path).is_file()
        ],
        "project_api": _project_api_snapshot(project_dir),
        "resource_static": analyze_resource_risks(project_dir),
    }
    context["failure_graph"] = _compact_failure_graph_for_repair(failure_analysis)
    context["runtime_contracts"] = _runtime_contract_context(failure_analysis)
    return context




def _compact_failure_graph_for_repair(failure_analysis: Mapping[str, Any]) -> dict[str, Any]:
    graph = failure_analysis.get("failure_graph_data")
    if not isinstance(graph, Mapping):
        return {}
    result: dict[str, Any] = {
        "schema_version": graph.get("schema_version", "code_task_failure_graph.v1"),
        "primary_signal": graph.get("primary_signal", ""),
    }
    for key, limit in (
        ("runtime_signals", 8),
        ("traceback_files", 8),
        ("candidate_files", 10),
        ("signal_terms", 16),
    ):
        value = graph.get(key)
        result[key] = value[:limit] if isinstance(value, list) else []
    for key in ("artifact_scan", "runtime_watchdog"):
        value = graph.get(key)
        if isinstance(value, Mapping):
            result[key] = dict(value)
    return result


def _runtime_contract_context(failure_analysis: Mapping[str, Any]) -> dict[str, Any]:
    graph = failure_analysis.get("failure_graph_data")
    if not isinstance(graph, Mapping):
        return {}
    context: dict[str, Any] = {}
    artifact_scan = graph.get("artifact_scan")
    if isinstance(artifact_scan, Mapping):
        context["artifact_scan"] = dict(artifact_scan)
    watchdog = graph.get("runtime_watchdog")
    if isinstance(watchdog, Mapping):
        context["runtime_watchdog"] = dict(watchdog)
    return context


def _generated_review_index(
    project_dir: Path,
    *,
    result_schema: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    return compact_review_index(
        build_review_index(project_dir, result_schema=result_schema, contract=contract),
        max_files=80,
    )


def _candidate_file_context(project_dir: Path, rel_path: str) -> dict[str, Any]:
    target = project_dir / rel_path
    try:
        source = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        source = ""
    return {
        "path": rel_path,
        "public_api": public_api(target) if target.suffix == ".py" else [],
        "source_excerpt": _head_tail_excerpt(source, limit=3600),
    }


def _source_signal_matches(project_dir: Path, paths: list[str], signal_text: str) -> list[str]:
    terms = _failure_terms(signal_text)
    if not terms:
        return []
    matches: list[str] = []
    for path in paths:
        target = project_dir / path
        if not target.is_file():
            continue
        try:
            source = target.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if any(term in source for term in terms):
            matches.append(path)
    return matches


def _failure_terms(text: str) -> list[str]:
    terms = []
    for quoted in re.findall(r"'([^']{2,80})'|\"([^\"]{2,80})\"", text):
        value = next((part for part in quoted if part), "")
        if value:
            terms.append(value.lower())
    terms.extend(
        token.lower()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)
        if token.lower() not in {"the", "and", "for", "with", "object", "failed", "error", "cannot", "proceed"}
    )
    return list(dict.fromkeys(terms))[:24]


def _head_tail_excerpt(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = max(1000, limit // 2)
    return text[:half].rstrip() + "\n\n# ... middle omitted for repair context ...\n\n" + text[-half:].lstrip()


def _run_repair_plan_prompt(
    *,
    context: Mapping[str, Any],
    failure_analysis: Mapping[str, Any],
    stderr_text: str,
    result_schema: Mapping[str, Any],
    contract: Mapping[str, Any],
    dependency_advice: Mapping[str, Any],
    previous_repair_context: str,
) -> str:
    return (
        "Diagnose the runtime failure before editing files. Choose a small ordered list of existing Python files "
        "that most likely own the root cause.\n\n"
        "Rules:\n"
        "- Prefer producer/consumer contract fixes over entrypoint-only changes.\n"
        "- If the error mentions a missing dataset/source/field, inspect data loading, preprocessing, config, and orchestrator files.\n"
        "- If the error mentions an attribute/type mismatch, inspect the object producer, object consumer, and the call site.\n"
        "- Build a dependency trace from failing metric/field -> aggregate/report consumer -> record object -> producer function -> call site before choosing files.\n"
        "- If a required metric is missing or invalid, inspect whether the raw evidence required for that metric was lost earlier in the data flow.\n"
        "- If the failure is a timeout, repeated warning flood, or apparent hang, use resource_static to identify nested fit/search loops and propose a bounded algorithmic repair.\n"
        "- If artifact_scan reports artifact_path_mismatch, fix the output path contract first: expected path, actual candidate path, writer, config, and entrypoint must agree.\n"
        "- If runtime_watchdog reports warning_flood or repeated_output_flood, repair the root loop/convergence/resource cause; do not merely suppress warnings.\n"
        "- If stderr is generic because an entrypoint caught the real exception, choose the entrypoint only to restore traceback visibility; do not treat the generic wrapper as the root cause.\n"
        "- Use Previous repair context to avoid repeating the same failed localization or patch strategy.\n"
        "- If the same error survived a prior repair, explicitly explain why the previous fix was insufficient before selecting target files.\n"
        "- Do not choose files only because they appear in validation warnings if benchmark stderr contains a clearer runtime failure.\n"
        "- Return JSON with fields: failure_kind, diagnosis, root_cause, observed_error, repeated_failure, previous_attempt_summary, affected_files, producer_files, consumer_files, evidence_gaps, repair_scope, why_not_smaller_scope, why_not_larger_scope, dependency_trace, target_files, repair_strategy, risks.\n"
        "- repair_scope must be one of: block, function, file, multi_file, regenerate_plan.\n"
        "- dependency_trace should be a short ordered list of producer/consumer/aggregate facts, not prose filler.\n"
        "- target_files must use only paths from candidate_files.\n\n"
        f"Benchmark stderr:\n{stderr_text[:6000]}\n\n"
        f"Failure analysis:\n{json.dumps(_compact_for_prompt(failure_analysis), indent=2, ensure_ascii=False)}\n\n"
        f"Candidate context:\n{json.dumps(_compact_for_prompt(context, limit=36000), indent=2, ensure_ascii=False)}\n\n"
        f"Previous repair context:\n{previous_repair_context[:12000] or 'No previous repair context recorded.'}\n\n"
        f"Result schema:\n{json.dumps(dict(result_schema), indent=2, ensure_ascii=False)}\n\n"
        f"Task contract:\n{json.dumps(_compact_for_prompt(contract), indent=2, ensure_ascii=False)}\n\n"
        f"Dependency advice:\n{json.dumps(_compact_for_prompt(dependency_advice), indent=2, ensure_ascii=False)}\n"
    )


def _run_repair_target_paths(
    *,
    project_dir: Path,
    failure_analysis: Mapping[str, Any],
    stderr_text: str,
    code_artifacts: Mapping[str, Any],
) -> list[str]:
    """Prioritize observed locations and source matches, not filename roles."""

    known = _generated_python_paths(code_artifacts, project_dir=project_dir)
    text = stderr_text + "\n" + json.dumps(dict(failure_analysis), ensure_ascii=False, default=str)
    candidates = _failure_graph_candidate_paths(failure_analysis, project_dir=project_dir)
    implicated = failure_analysis.get("implicated_files")
    if isinstance(implicated, list):
        candidates.extend(_normalize_generated_project_path(str(path)) for path in implicated)
    candidates.extend(_paths_from_review_summaries(text))
    candidates.extend(_source_signal_matches(project_dir, known, text))
    valid = [
        rel for path in candidates
        if (rel := safe_relative_path(path)) and rel.endswith(".py")
        and (project_dir / rel).is_file()
    ]
    return list(dict.fromkeys(valid)) or sorted(known)














def _generated_python_paths(
    code_artifacts: Mapping[str, Any],
    *,
    project_dir: Path | None = None,
) -> list[str]:
    generated = code_artifacts.get("generated_files")
    rows = [row for row in generated if isinstance(row, Mapping)] if isinstance(generated, list) else []
    paths = [
        path
        for row in rows
        if isinstance(row.get("path", ""), str)
        if (path := safe_relative_path(str(row.get("path", "")))) and path.endswith(".py")
    ]
    if project_dir is not None and project_dir.is_dir():
        paths.extend(
            path.relative_to(project_dir).as_posix()
            for path in project_dir.rglob("*.py")
            if "__pycache__" not in path.parts
        )
    return list(dict.fromkeys(path for path in paths if not path.endswith("/__init__.py")))






















def _normalize_generated_project_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    marker = "generated_project/"
    if marker in normalized:
        normalized = normalized.split(marker, 1)[1]
    return normalized.lstrip("/")


def _run_file_repair_prompt(
    *,
    rel_path: str,
    current_content: str,
    file_spec: Mapping[str, Any],
    project_dir: Path,
    failure_analysis: Mapping[str, Any],
    stderr_text: str,
    repair_plan: Mapping[str, Any],
    repair_context: Mapping[str, Any],
    previous_repair_context: str,
    result_schema: Mapping[str, Any],
    contract: Mapping[str, Any],
    dependency_advice: Mapping[str, Any],
) -> str:
    return (
        "Repair exactly one generated project file after a benchmark runtime failure. "
        "The full project already exists on disk, and this file must integrate with the existing public APIs.\n\n"
        "Preferred output:\n"
        "- Return `actions` when a local repair is enough.\n"
        "- Use `replace_block` with unique `old_string`/`new_string` for call-site, field, import, or return-shape fixes.\n"
        "- Use `rewrite_function` with `function_name` and `new_source` for one-function repairs.\n"
        "- Use `rewrite_file` or top-level `content` only when the file's whole responsibility or public API must change.\n"
        "- Public API signatures are preserved by default. If changing one is truly required, set "
        "`allow_api_breaking_change: true` and make the repair plan include producer and consumer files; otherwise preserve it.\n\n"
        "Hard rules:\n"
        "- Return JSON only; do not use markdown fences.\n"
        "- Every action must include `action`, `path`, `rationale`, and the required action fields.\n"
        "- Preserve the file's public API unless the failure proves that API is wrong.\n"
        "- Keep behavior local and deterministic; no network, shell, credentials, or hidden downloads.\n"
        "- Do not fake metrics. Fix the runtime path so the benchmark can produce measured outputs.\n"
        "- Do not convert unresolved runtime errors into a successful all-zero run.\n"
        "- Do not replace a concrete traceback with a generic entrypoint-only error. Preserve traceback.print_exc(), "
        "logging.exception/logger.exception, or re-raise broad exceptions.\n"
        "- Do not use self-check, empty datasets, or placeholder records as substitutes for full benchmark mode.\n"
        "- Use Previous repair context to avoid reapplying a patch that already failed to change the observed error.\n"
        "- If the same error survived a previous patch, explain in code comments only where useful and fix the producer/consumer contract, not just the visible traceback line.\n"
        "- Before changing this file's API, check Existing project APIs and update consumers/producers through the repair plan; avoid creating a new unmatched interface.\n"
        "- If this file writes metrics or reports, preserve raw evidence needed by the task evidence_plan before aggregating.\n"
        "- If the experiment cannot produce condition-level evidence, the entrypoint must fail clearly instead of exiting 0.\n"
        "- Required metrics must remain parseable by main.py as `metric_name: number`.\n\n"
        f"Target path: {rel_path}\n\n"
        f"Current file content:\n```python\n{current_content[:16000]}\n```\n\n"
        f"File spec:\n{json.dumps(dict(file_spec), indent=2, ensure_ascii=False)}\n\n"
        f"Benchmark stderr:\n{stderr_text[:6000]}\n\n"
        f"Failure analysis:\n{json.dumps(_compact_for_prompt(failure_analysis), indent=2, ensure_ascii=False)}\n\n"
        f"Runtime repair plan:\n{json.dumps(_compact_for_prompt(repair_plan), indent=2, ensure_ascii=False)}\n\n"
        f"Relevant project context for this repair:\n{json.dumps(_compact_for_prompt(repair_context, limit=24000), indent=2, ensure_ascii=False)}\n\n"
        f"Previous repair context:\n{previous_repair_context[:12000] or 'No previous repair context recorded.'}\n\n"
        f"Actual dependency APIs:\n{json.dumps(dependency_context(project_dir, file_spec, max_source_chars=5000), indent=2, ensure_ascii=False)}\n\n"
        f"Existing project APIs:\n{json.dumps(_project_api_snapshot(project_dir), indent=2, ensure_ascii=False)}\n\n"
        f"Result schema:\n{json.dumps(dict(result_schema), indent=2, ensure_ascii=False)}\n\n"
        f"Task contract:\n{json.dumps(_compact_for_prompt(contract), indent=2, ensure_ascii=False)}\n\n"
        f"Dependency advice:\n{json.dumps(_compact_for_prompt(dependency_advice), indent=2, ensure_ascii=False)}\n"
    )




def _compile_error(path: Path) -> str:
    try:
        py_compile.compile(str(path), doraise=True)
    except py_compile.PyCompileError as exc:
        return exc.msg
    return ""


def _compile_project(project_dir: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(project_dir.rglob("*.py")):
        error = _compile_error(path)
        if error:
            errors.append(f"{path.relative_to(project_dir).as_posix()}: {error}")
    return errors
