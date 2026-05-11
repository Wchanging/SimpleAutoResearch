from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from simple_ar.artifacts import append_jsonl, read_json, read_jsonl, read_text, write_json
from simple_ar.code_task.failure import analyze_code_task_failure
from simple_ar.code_task.planning import select_relevant_files
from simple_ar.code_task.state import (
    code_task_paths,
    load_code_task_manifest,
    manifest_section,
    save_code_task_manifest,
    utcnow_iso,
    workspace_file,
)
from simple_ar.llm import LLMClient, LLMError, LLMUsage
from simple_ar.usage import summarize_usage


CODE_TASK_REPAIR_SYSTEM = (
    "You are a careful senior engineer proposing a minimal repair patch for "
    "an isolated code-task workspace. Use the failure analysis, execution "
    "report, patch plan, and supplied source snippets. Return only JSON. Do "
    "not broaden the change unless the traceback requires it."
)

MessageCallback = Callable[[str], None]


@dataclass(frozen=True)
class RepairProposalResult:
    """Result returned after proposing a bounded repair.

    Args:
        run_dir: Code-task run directory.
        repair_dir: Directory for this repair attempt.
        proposal_path: JSON edit proposal path.
        mode: ``llm`` when model output was used, otherwise ``offline``.
        edit_count: Number of normalized repair edits.
        selected_files: Workspace-relative source files included as context.
    """

    run_dir: Path
    repair_dir: Path
    proposal_path: Path
    mode: str
    edit_count: int
    selected_files: tuple[str, ...]


def propose_repair_edits(
    run_dir: Path,
    *,
    model: str | None = None,
    use_llm: bool = True,
    max_files: int = 8,
    max_source_chars_per_file: int = 4000,
    message_callback: MessageCallback | None = None,
) -> RepairProposalResult:
    """Propose a bounded repair edit set from the latest failed run.

    The function writes a repair proposal but does not apply it. Keeping
    repair application explicit preserves the same human-reviewable patch gate
    as the initial edit flow.

    Args:
        run_dir: Code-task run directory.
        model: Optional OpenAI-compatible model override.
        use_llm: Whether to call the configured LLM provider.
        max_files: Maximum source files included as repair context.
        max_source_chars_per_file: Per-file source snippet budget.
        message_callback: Optional progress callback.

    Returns:
        Repair proposal metadata.
    """
    manifest = load_code_task_manifest(run_dir)
    paths = code_task_paths(run_dir)
    analysis_path = paths.run_artifact_dir / "failure_analysis.md"
    if not analysis_path.exists():
        analysis = analyze_code_task_failure(run_dir)
        if analysis.status == "no_failure":
            raise RuntimeError("Latest benchmark passed; repair proposal is not needed.")
        manifest = load_code_task_manifest(run_dir)
    failure_analysis = read_text(analysis_path)
    execution_report = _read_required_json(paths.run_artifact_dir / "execution_report.json")
    if execution_report.get("status") == "passed":
        raise RuntimeError("Latest benchmark passed; repair proposal is not needed.")

    task_text = _read_optional_text(paths.task_dir / "task.md")
    patch_plan = _read_optional_text(paths.task_dir / "patch_plan.md")
    patch_diff = _read_optional_text(paths.task_dir / "patch.diff")
    index = _read_required_json(paths.meta_dir / "codebase_index.json")
    selected = _repair_context_files(
        manifest,
        index,
        task_text=task_text,
        failure_analysis=failure_analysis,
        max_files=max_files,
    )
    snippets = _source_snippets(
        paths.workspace_dir,
        selected,
        max_chars_per_file=max_source_chars_per_file,
    )

    repair_dir = _next_repair_dir(paths.repairs_dir)
    repair_dir.mkdir(parents=True, exist_ok=False)
    proposal_path = repair_dir / "proposed_edits.json"

    mode = "offline"
    proposal: dict[str, Any] | None = None
    if use_llm:
        try:
            _emit(message_callback, "Calling LLM for bounded repair proposal.")
            client = LLMClient.from_env(
                model=model,
                usage_callback=lambda usage: _record_repair_usage(
                    paths.meta_dir,
                    usage,
                    message_callback=message_callback,
                ),
            )
            proposal = client.ask_json(
                CODE_TASK_REPAIR_SYSTEM,
                _repair_prompt(
                    task_text=task_text,
                    patch_plan=patch_plan,
                    patch_diff=patch_diff,
                    failure_analysis=failure_analysis,
                    execution_report=execution_report,
                    snippets=snippets,
                ),
                label="code-task-repair",
            )
            mode = "llm"
        except LLMError as exc:
            _emit(message_callback, f"LLM repair proposal failed; writing offline empty proposal. {exc}")

    if proposal is None:
        proposal = _offline_repair(selected)

    normalized = _normalize_repair_proposal(proposal, index=index, mode=mode)
    write_json(proposal_path, normalized)
    _update_manifest_after_repair(
        run_dir,
        manifest,
        repair_dir=repair_dir,
        edit_count=len(normalized["edits"]),
        selected_files=selected,
    )
    return RepairProposalResult(
        run_dir=paths.run_dir,
        repair_dir=repair_dir,
        proposal_path=proposal_path,
        mode=mode,
        edit_count=len(normalized["edits"]),
        selected_files=tuple(selected),
    )


def _repair_prompt(
    *,
    task_text: str,
    patch_plan: str,
    patch_diff: str,
    failure_analysis: str,
    execution_report: dict[str, Any],
    snippets: list[dict[str, str]],
) -> str:
    snippet_text = "\n\n".join(
        f"### {item['path']}\n```text\n{item['text']}\n```"
        for item in snippets
    )
    return (
        "Return JSON with fields: `summary` string, `edits` list, "
        "`validation` list of strings, and `risks` list of strings.\n\n"
        "Each edit must contain `path`, `old`, `new`, and `reason` string fields.\n\n"
        "Rules:\n"
        "- Use exact old/new text replacements only.\n"
        "- Use only workspace-relative paths from the supplied snippets.\n"
        "- Prefer repairing implicated or recently changed files.\n"
        "- Do not change tests unless the failure analysis clearly shows the test is wrong.\n"
        "- Keep the repair minimal and runnable.\n"
        "- Do not return markdown or a unified diff.\n\n"
        f"Task:\n{task_text or 'No task text found.'}\n\n"
        f"Patch plan:\n{patch_plan or 'No patch plan found.'}\n\n"
        f"Current patch diff:\n```diff\n{patch_diff or 'No patch diff found.'}\n```\n\n"
        f"Execution report JSON:\n{json.dumps(execution_report, indent=2, ensure_ascii=False)}\n\n"
        f"Failure analysis:\n{failure_analysis}\n\n"
        f"Selected source snippets:\n{snippet_text or 'No source snippets selected.'}"
    )


def _repair_context_files(
    manifest: dict[str, Any],
    index: dict[str, Any],
    *,
    task_text: str,
    failure_analysis: str,
    max_files: int,
) -> list[str]:
    known_paths = {str(item.get("path", "")) for item in _index_files(index)}
    selected: list[str] = []
    failure = manifest.get("failure_analysis", {})
    if isinstance(failure, dict):
        for path in failure.get("implicated_files", []):
            if isinstance(path, str) and path in known_paths and path not in selected:
                selected.append(path)
    patch = manifest.get("patch", {})
    if isinstance(patch, dict):
        for path in patch.get("changed_files", []):
            if isinstance(path, str) and path in known_paths and path not in selected:
                selected.append(path)
    for path in select_relevant_files(index, task_text + "\n" + failure_analysis, max_files=max_files):
        if path not in selected:
            selected.append(path)
    return selected[: max(1, max_files)]


def _source_snippets(
    workspace_dir: Path,
    selected_files: list[str],
    *,
    max_chars_per_file: int,
) -> list[dict[str, str]]:
    snippets: list[dict[str, str]] = []
    for rel_path in selected_files:
        path = workspace_file(workspace_dir, rel_path)
        if path is None or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        snippets.append(
            {
                "path": rel_path,
                "text": _clip(text, max_chars=max(200, max_chars_per_file)),
            }
        )
    return snippets


def _offline_repair(selected_files: list[str]) -> dict[str, Any]:
    return {
        "summary": "Offline mode does not invent repair edits. Review the failure analysis and provide an edits JSON file.",
        "edits": [],
        "validation": [
            "No repair edits were generated because --no-llm was used.",
        ],
        "risks": [
            "Manual repair edits should still be applied through code-task apply-edits and re-run.",
        ],
        "selected_files": selected_files,
    }


def _normalize_repair_proposal(
    proposal: dict[str, Any],
    *,
    index: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    known_paths = {str(item.get("path", "")) for item in _index_files(index)}
    warnings: list[str] = []
    edits: list[dict[str, str]] = []
    for item in proposal.get("edits", []):
        if not isinstance(item, dict):
            warnings.append("Dropped non-object edit.")
            continue
        path = _string(item.get("path"))
        old = item.get("old")
        new = item.get("new")
        if path not in known_paths:
            warnings.append(f"Dropped edit for unknown path: {path or '<empty>'}")
            continue
        if not isinstance(old, str) or not isinstance(new, str):
            warnings.append(f"Dropped edit for {path}: old/new must be strings.")
            continue
        if old == new:
            warnings.append(f"Dropped edit for {path}: old and new are identical.")
            continue
        edits.append(
            {
                "path": path,
                "old": old,
                "new": new,
                "reason": _string(item.get("reason")) or "No reason provided.",
            }
        )
    return {
        "schema_version": 1,
        "generated_at": utcnow_iso(),
        "kind": "repair_proposal",
        "mode": mode,
        "summary": _string(proposal.get("summary")) or "No summary provided.",
        "edits": edits,
        "validation": _string_list(proposal.get("validation")),
        "risks": _string_list(proposal.get("risks")),
        "warnings": warnings,
    }


def _next_repair_dir(repairs_dir: Path) -> Path:
    repairs_dir.mkdir(parents=True, exist_ok=True)
    existing = [
        path
        for path in repairs_dir.iterdir()
        if path.is_dir() and path.name.startswith("repair-")
    ]
    numbers: list[int] = []
    for path in existing:
        try:
            numbers.append(int(path.name.removeprefix("repair-")))
        except ValueError:
            continue
    return repairs_dir / f"repair-{(max(numbers) + 1 if numbers else 1):03d}"


def _update_manifest_after_repair(
    run_dir: Path,
    manifest: dict[str, Any],
    *,
    repair_dir: Path,
    edit_count: int,
    selected_files: list[str],
) -> None:
    root = code_task_paths(run_dir).run_dir
    rel_repair_dir = repair_dir.relative_to(root).as_posix()
    layout = manifest_section(manifest, "layout")
    layout["repairs"] = "code_task/repairs"
    repair = manifest_section(manifest, "repair")
    previous_count = int(repair.get("repair_count", 0) or 0)
    repair.update(
        {
            "status": "repair_proposed",
            "generated_at": utcnow_iso(),
            "repair_count": previous_count + 1,
            "latest_repair_dir": rel_repair_dir,
            "latest_proposed_edits": f"{rel_repair_dir}/proposed_edits.json",
            "latest_edit_count": edit_count,
            "selected_files": selected_files,
        }
    )
    manifest["layout"] = layout
    manifest["repair"] = repair
    manifest["status"] = "repair_proposed"
    save_code_task_manifest(run_dir, manifest)


def _record_repair_usage(
    meta_dir: Path,
    usage: LLMUsage,
    *,
    message_callback: MessageCallback | None,
) -> None:
    usage_path = meta_dir / "llm_usage.jsonl"
    row = usage.to_row()
    row["stage"] = "code_task.repair"
    append_jsonl(usage_path, row)
    write_json(meta_dir / "llm_usage_summary.json", summarize_usage(read_jsonl(usage_path)))
    _emit(
        message_callback,
        f"LLM usage {row.get('label', '')}: "
        f"{row['prompt_tokens']} input + {row['completion_tokens']} output = "
        f"{row['total_tokens']} tokens ({row['source']}).",
    )


def _read_required_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing required artifact: {path}")
    value = read_json(path)
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object in {path}")
    return value


def _read_optional_text(path: Path) -> str:
    return read_text(path) if path.exists() else ""


def _index_files(index: dict[str, Any]) -> list[dict[str, Any]]:
    files = index.get("files", [])
    if not isinstance(files, list):
        return []
    return [item for item in files if isinstance(item, dict)]


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_string(item) for item in value if _string(item)]


def _clip(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n... [truncated]"


def _emit(callback: MessageCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)
