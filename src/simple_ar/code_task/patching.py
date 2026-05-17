from __future__ import annotations

import difflib
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from simple_ar.artifacts import (
    append_jsonl,
    read_json,
    read_jsonl,
    read_text,
    write_json,
    write_text,
)
from simple_ar.code_task.index import build_codebase_index
from simple_ar.code_task.planning import select_relevant_files
from simple_ar.llm import LLMClient, LLMError, LLMUsage
from simple_ar.usage import summarize_usage


CODE_TASK_EDIT_SYSTEM = (
    "You are a careful senior engineer preparing a minimal JSON edit proposal. "
    "You may propose exact old/new text replacements, but you must not apply "
    "patches yourself. Use only the supplied workspace-relative paths and source "
    "snippets. Keep the patch small and reviewable."
)

MessageCallback = Callable[[str], None]


class PatchValidationError(RuntimeError):
    """Raised when proposed edits fail safety or consistency validation."""


@dataclass(frozen=True)
class ProposedEditsResult:
    """Result returned after generating a controlled edit proposal.

    Args:
        run_dir: Code-task run directory.
        proposal_path: JSON proposal path.
        mode: ``llm`` when model output was used, otherwise ``offline``.
        edit_count: Number of normalized edits in the proposal.
        selected_files: Workspace-relative files included in prompt context.
    """

    run_dir: Path
    proposal_path: Path
    mode: str
    edit_count: int
    selected_files: tuple[str, ...]


@dataclass(frozen=True)
class PatchApplyResult:
    """Result returned after safely applying controlled edits.

    Args:
        run_dir: Code-task run directory.
        applied_edits_path: Metadata file describing applied edits.
        patch_diff_path: Unified diff for human review.
        changed_files: Workspace-relative files changed by the patch.
    """

    run_dir: Path
    applied_edits_path: Path
    patch_diff_path: Path
    changed_files: tuple[str, ...]


@dataclass(frozen=True)
class _PreparedEdit:
    path: str
    file_path: Path
    old_text: str
    new_text: str
    updated_text: str
    reason: str


def propose_patch_edits(
    run_dir: Path,
    *,
    model: str | None = None,
    use_llm: bool = True,
    force: bool = False,
    max_files: int = 8,
    max_source_chars_per_file: int = 4000,
    message_callback: MessageCallback | None = None,
) -> ProposedEditsResult:
    """Generate controlled old/new text edits from an approved patch plan.

    Args:
        run_dir: Code-task run directory.
        model: Optional OpenAI-compatible model override.
        use_llm: Whether to call the configured LLM provider. Offline mode
            writes an empty proposal for inspection and manual replacement.
        force: Overwrite an existing proposal when true.
        max_files: Maximum number of workspace files to include in prompt
            context.
        max_source_chars_per_file: Per-file source snippet character budget.
        message_callback: Optional progress callback.

    Returns:
        Metadata for the generated edit proposal.

    Raises:
        FileExistsError: If the proposal already exists and ``force`` is false.
        FileNotFoundError: If required code-task artifacts are missing.
        RuntimeError: If the run is not a code-task workflow.
    """
    root = Path(run_dir)
    task_dir = root / "code_task"
    meta_dir = task_dir / "meta"
    workspace_dir = task_dir / "workspace"
    proposal_path = meta_dir / "proposed_edits.json"
    if proposal_path.exists() and not force:
        raise FileExistsError(f"Proposed edits already exist: {proposal_path}")

    manifest_path = root / "manifest.json"
    manifest = _load_code_task_manifest(manifest_path)
    task_text = _read_required_text(task_dir / "task.md")
    patch_plan = _read_required_text(task_dir / "patch_plan.md")
    index = _read_required_json(meta_dir / "codebase_index.json")
    selected = _selected_context_files(
        manifest,
        index,
        task_text=task_text,
        patch_plan=patch_plan,
        max_files=max_files,
    )
    snippets = _source_snippets(
        workspace_dir,
        selected,
        max_chars_per_file=max_source_chars_per_file,
    )

    mode = "offline"
    proposal: dict[str, Any] | None = None
    if use_llm:
        try:
            _emit(message_callback, "Calling LLM for controlled edit proposal.")
            client = LLMClient.from_env(
                model=model,
                usage_callback=lambda usage: _record_code_task_usage(
                    meta_dir,
                    usage,
                    stage="code_task.propose_edits",
                    message_callback=message_callback,
                ),
            )
            proposal = _ask_llm_for_edits(
                client,
                task_text=task_text,
                patch_plan=patch_plan,
                index=index,
                snippets=snippets,
            )
            mode = "llm"
        except LLMError as exc:
            _emit(message_callback, f"LLM edit proposal failed; writing offline empty proposal. {exc}")

    if proposal is None:
        proposal = _offline_proposal(selected)

    normalized = _normalize_edit_proposal(proposal, index=index, mode=mode)
    write_json(proposal_path, normalized)
    _update_manifest_after_proposal(
        manifest_path,
        manifest,
        selected_files=selected,
        proposal=normalized,
    )
    return ProposedEditsResult(
        run_dir=root,
        proposal_path=proposal_path,
        mode=mode,
        edit_count=len(normalized["edits"]),
        selected_files=tuple(selected),
    )


def apply_patch_edits(
    run_dir: Path,
    *,
    edits_file: Path | None = None,
    allow_unapproved_plan: bool = False,
) -> PatchApplyResult:
    """Safely apply controlled old/new text edits to the copied workspace.

    Args:
        run_dir: Code-task run directory.
        edits_file: Optional JSON file containing edits. Defaults to
            ``code_task/meta/proposed_edits.json``.
        allow_unapproved_plan: Bypass the human approval gate. This should be
            used only for tests or explicit local experiments.

    Returns:
        Paths to patch metadata and the changed workspace files.

    Raises:
        PermissionError: If the patch plan has not been approved.
        PatchValidationError: If any edit is unsafe, ambiguous, or inconsistent.
    """
    root = Path(run_dir)
    task_dir = root / "code_task"
    meta_dir = task_dir / "meta"
    workspace_dir = task_dir / "workspace"
    manifest_path = root / "manifest.json"
    manifest = _load_code_task_manifest(manifest_path)
    if not allow_unapproved_plan and _plan_status(manifest) != "approved":
        raise PermissionError(
            "Patch plan is not approved. Run `simple-ar code-task decide-plan "
            "<run-dir> --decision approve` before applying edits."
        )

    proposal_path = Path(edits_file) if edits_file is not None else meta_dir / "proposed_edits.json"
    proposal = _read_required_json(proposal_path)
    edits = _edit_rows(proposal)
    if not edits:
        raise PatchValidationError(f"No edits were found in {proposal_path}")

    prepared = _prepare_edits(workspace_dir, edits)
    pre_hash_rows = _hash_rows_for_prepared(workspace_dir, prepared)
    old_text_by_path = {item.file_path: read_text(item.file_path) for item in prepared}
    new_text_by_path = {item.file_path: item.updated_text for item in prepared}
    diff_text = _unified_diff(prepared, old_text_by_path, new_text_by_path)

    written: list[Path] = []
    try:
        for path, new_text in new_text_by_path.items():
            _write_text_atomically(path, new_text)
            written.append(path)
    except Exception:
        for path in written:
            _write_text_atomically(path, old_text_by_path[path])
        raise

    post_hash_rows = _hash_rows_for_prepared(workspace_dir, prepared)
    patch_diff_path = task_dir / "patch.diff"
    applied_edits_path = meta_dir / "applied_edits.json"

    write_text(patch_diff_path, diff_text)
    applied = _applied_edits_record(
        proposal_path=proposal_path,
        prepared=prepared,
        pre_hash_rows=pre_hash_rows,
        post_hash_rows=post_hash_rows,
    )
    write_json(applied_edits_path, applied)

    codebase_index = build_codebase_index(workspace_dir, output_path=meta_dir / "codebase_index.json")
    _update_manifest_after_apply(
        manifest_path,
        manifest,
        changed_files=_unique_prepared_paths(prepared),
        codebase_index=codebase_index,
    )
    return PatchApplyResult(
        run_dir=root,
        applied_edits_path=applied_edits_path,
        patch_diff_path=patch_diff_path,
        changed_files=tuple(_unique_prepared_paths(prepared)),
    )


def _ask_llm_for_edits(
    client: LLMClient,
    *,
    task_text: str,
    patch_plan: str,
    index: dict[str, Any],
    snippets: list[dict[str, str]],
) -> dict[str, Any]:
    response = client.ask_json(
        CODE_TASK_EDIT_SYSTEM,
        _edit_user_prompt(
            task_text=task_text,
            patch_plan=patch_plan,
            index=index,
            snippets=snippets,
        ),
        label="code-task-propose-edits",
    )
    return response


def _edit_user_prompt(
    *,
    task_text: str,
    patch_plan: str,
    index: dict[str, Any],
    snippets: list[dict[str, str]],
) -> str:
    compact_files = [
        {
            "path": item.get("path"),
            "kind": item.get("kind"),
            "role_tags": item.get("role_tags", []),
            "summary": item.get("summary", ""),
        }
        for item in _index_files(index)
    ]
    snippet_text = "\n\n".join(
        f"### {item['path']}\n```text\n{item['text']}\n```"
        for item in snippets
    )
    return (
        "Return JSON with fields: `summary` string, `edits` list, "
        "`validation` list of strings, and `risks` list of strings.\n\n"
        "Each item in `edits` must contain exactly these string fields: "
        "`path`, `old`, `new`, and `reason`.\n\n"
        "Hard rules:\n"
        "- Do not return markdown or a unified diff.\n"
        "- Use only workspace-relative paths from the provided file inventory.\n"
        "- Each `old` value must be an exact contiguous substring from the "
        "corresponding source snippet, including indentation and newlines.\n"
        "- Make each replacement large enough to be unique in the file.\n"
        "- Do not propose deleting whole files or directories.\n"
        "- Prefer one edit per file. If a file needs multiple nearby changes, "
        "combine them into one larger old/new replacement.\n"
        "- Keep the patch minimal and aligned with the approved patch plan.\n\n"
        f"Task:\n{task_text}\n\n"
        f"Approved patch plan:\n{patch_plan}\n\n"
        f"Workspace file inventory JSON:\n{json.dumps(compact_files, indent=2, ensure_ascii=False)}\n\n"
        f"Selected source snippets:\n{snippet_text or 'No source snippets selected.'}"
    )


def _offline_proposal(selected_files: list[str]) -> dict[str, Any]:
    return {
        "summary": "Offline mode does not invent edits. Provide an edits JSON file or rerun with LLM enabled.",
        "edits": [],
        "validation": [
            "No edits were generated because --no-llm was used.",
        ],
        "risks": [
            "Manual edits should still be validated by apply-edits before touching the workspace.",
        ],
        "selected_files": selected_files,
    }


def _normalize_edit_proposal(
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
        "generated_at": _utcnow_iso(),
        "mode": mode,
        "summary": _string(proposal.get("summary")) or "No summary provided.",
        "edits": edits,
        "validation": _string_list(proposal.get("validation")),
        "risks": _string_list(proposal.get("risks")),
        "warnings": warnings,
    }


def _prepare_edits(workspace_dir: Path, edits: list[dict[str, Any]]) -> list[_PreparedEdit]:
    workspace = workspace_dir.resolve()
    current_text_by_path: dict[Path, str] = {}
    prepared: list[_PreparedEdit] = []
    errors: list[str] = []
    for index, edit in enumerate(edits, start=1):
        path = _string(edit.get("path"))
        old_text = edit.get("old")
        new_text = edit.get("new")
        reason = _string(edit.get("reason"))
        if not path:
            errors.append(f"edit {index}: missing path")
            continue
        if not isinstance(old_text, str) or not old_text:
            errors.append(f"edit {index} `{path}`: old text must be a non-empty string")
            continue
        if not isinstance(new_text, str) or not new_text:
            errors.append(f"edit {index} `{path}`: new text must be a non-empty string")
            continue
        file_path = _workspace_file(workspace, path)
        if file_path is None:
            errors.append(f"edit {index} `{path}`: path escapes the workspace")
            continue
        if not file_path.exists() or not file_path.is_file():
            errors.append(f"edit {index} `{path}`: target file does not exist")
            continue
        text = current_text_by_path.get(file_path)
        if text is None:
            text = read_text(file_path)
        occurrences = text.count(old_text)
        if occurrences == 0:
            errors.append(f"edit {index} `{path}`: old text was not found")
            continue
        if occurrences > 1:
            errors.append(f"edit {index} `{path}`: old text matched {occurrences} times")
            continue
        updated_text = text.replace(old_text, new_text, 1)
        current_text_by_path[file_path] = updated_text
        prepared.append(
            _PreparedEdit(
                path=path,
                file_path=file_path,
                old_text=old_text,
                new_text=new_text,
                updated_text=updated_text,
                reason=reason or "No reason provided.",
            )
        )
    if errors:
        raise PatchValidationError("Patch validation failed:\n" + "\n".join(f"- {error}" for error in errors))
    if not prepared:
        raise PatchValidationError("Patch validation failed: no valid edits to apply")
    return prepared


def _workspace_file(workspace: Path, relative_path: str) -> Path | None:
    rel = Path(relative_path)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    path = (workspace / rel).resolve()
    if not _is_relative_to(path, workspace):
        return None
    return path


def _unified_diff(
    prepared: list[_PreparedEdit],
    old_text_by_path: dict[Path, str],
    new_text_by_path: dict[Path, str],
) -> str:
    chunks: list[str] = []
    for item in prepared:
        if item.file_path not in old_text_by_path:
            continue
        old_text = old_text_by_path.pop(item.file_path)
        new_lines = new_text_by_path[item.file_path].splitlines(keepends=True)
        chunks.extend(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_lines,
                fromfile=f"a/{item.path}",
                tofile=f"b/{item.path}",
            )
        )
        if chunks and not chunks[-1].endswith("\n"):
            chunks[-1] += "\n"
    return "".join(chunks)


def _unique_prepared_paths(prepared: list[_PreparedEdit]) -> list[str]:
    paths: list[str] = []
    for item in prepared:
        if item.path not in paths:
            paths.append(item.path)
    return paths


def _write_text_atomically(path: Path, text: str) -> None:
    temp_path = path.with_name(path.name + ".simple_ar_tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def _applied_edits_record(
    *,
    proposal_path: Path,
    prepared: list[_PreparedEdit],
    pre_hash_rows: dict[str, dict[str, Any]],
    post_hash_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "applied_at": _utcnow_iso(),
        "proposal": str(proposal_path),
        "edit_count": len(prepared),
        "changed_files": _unique_prepared_paths(prepared),
        "edits": [
            {
                "path": item.path,
                "reason": item.reason,
                "old_sha256": pre_hash_rows.get(item.path, {}).get("sha256"),
                "new_sha256": post_hash_rows.get(item.path, {}).get("sha256"),
                "old_bytes": pre_hash_rows.get(item.path, {}).get("bytes"),
                "new_bytes": post_hash_rows.get(item.path, {}).get("bytes"),
            }
            for item in prepared
        ],
    }


def _selected_context_files(
    manifest: dict[str, Any],
    index: dict[str, Any],
    *,
    task_text: str,
    patch_plan: str,
    max_files: int,
) -> list[str]:
    known_paths = {str(item.get("path", "")) for item in _index_files(index)}
    plan = manifest.get("plan", {})
    selected: list[str] = []
    if isinstance(plan, dict):
        for path in plan.get("selected_files", []):
            if isinstance(path, str) and path in known_paths and path not in selected:
                selected.append(path)
    for path in _paths_from_patch_plan(patch_plan, known_paths):
        if path not in selected:
            selected.append(path)
    if not selected:
        selected = select_relevant_files(index, task_text, max_files=max_files)
    return selected[: max(1, max_files)]


def _paths_from_patch_plan(patch_plan: str, known_paths: set[str]) -> list[str]:
    found: list[str] = []
    for candidate in re.findall(r"`([^`]+)`", patch_plan):
        if candidate in known_paths and candidate not in found:
            found.append(candidate)
    return found


def _source_snippets(
    workspace_dir: Path,
    selected_files: list[str],
    *,
    max_chars_per_file: int,
) -> list[dict[str, str]]:
    snippets: list[dict[str, str]] = []
    workspace = workspace_dir.resolve()
    for rel_path in selected_files:
        path = _workspace_file(workspace, rel_path)
        if path is None or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        snippets.append(
            {
                "path": rel_path,
                "text": _clip_text(text, max_chars=max(200, max_chars_per_file)),
            }
        )
    return snippets


def _update_manifest_after_proposal(
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    selected_files: list[str],
    proposal: dict[str, Any],
) -> None:
    edits = proposal.get("edits", [])
    patch = _dict_value(manifest, "patch")
    patch.update(
        {
            "status": "edits_proposed",
            "proposed_at": _utcnow_iso(),
            "proposed_edits": "code_task/meta/proposed_edits.json",
            "proposed_edit_count": len(edits) if isinstance(edits, list) else 0,
            "selected_files": selected_files,
        }
    )
    layout = _dict_value(manifest, "layout")
    layout["proposed_edits"] = "code_task/meta/proposed_edits.json"
    manifest["layout"] = layout
    manifest["patch"] = patch
    manifest["status"] = "edits_proposed"
    write_json(manifest_path, manifest)


def _update_manifest_after_apply(
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    changed_files: list[str],
    codebase_index: dict[str, Any],
) -> None:
    patch = _dict_value(manifest, "patch")
    patch.update(
        {
            "status": "applied",
            "applied_at": _utcnow_iso(),
            "patch_diff": "code_task/patch.diff",
            "applied_edits": "code_task/meta/applied_edits.json",
            "changed_files": changed_files,
        }
    )
    layout = _dict_value(manifest, "layout")
    layout.update(
        {
            "patch_diff": "code_task/patch.diff",
            "applied_edits": "code_task/meta/applied_edits.json",
        }
    )
    project = codebase_index.get("project", {})
    manifest["layout"] = layout
    manifest["patch"] = patch
    manifest["status"] = "patched"
    manifest["codebase"] = {
        "file_count": project.get("file_count", 0),
        "python_file_count": project.get("python_file_count", 0),
        "test_file_count": project.get("test_file_count", 0),
        "entrypoint_candidates": project.get("entrypoint_candidates", []),
    }
    write_json(manifest_path, manifest)


def _record_code_task_usage(
    meta_dir: Path,
    usage: LLMUsage,
    *,
    stage: str,
    message_callback: MessageCallback | None,
) -> None:
    usage_path = meta_dir / "llm_usage.jsonl"
    row = usage.to_row()
    row["stage"] = stage
    append_jsonl(usage_path, row)
    write_json(meta_dir / "llm_usage_summary.json", summarize_usage(read_jsonl(usage_path)))
    cost = row.get("estimated_cost_usd")
    cost_text = f", est cost ${cost:.6f}" if isinstance(cost, (int, float)) else ""
    _emit(
        message_callback,
        f"LLM usage {row.get('label', '')}: "
        f"{row['prompt_tokens']} input + {row['completion_tokens']} output = "
        f"{row['total_tokens']} tokens ({row['source']}{cost_text}).",
    )


def _load_code_task_manifest(path: Path) -> dict[str, Any]:
    data = _read_required_json(path)
    if data.get("workflow") != "code_task":
        raise RuntimeError(f"Run is not a code-task workflow: {path.parent}")
    return data


def _read_required_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing required artifact: {path}")
    data = read_json(path)
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected JSON object in {path}")
    return data


def _read_required_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing required artifact: {path}")
    return read_text(path)


def _edit_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    edits = data.get("edits", [])
    return [item for item in edits if isinstance(item, dict)] if isinstance(edits, list) else []


def _index_files(index: dict[str, Any]) -> list[dict[str, Any]]:
    files = index.get("files", [])
    if not isinstance(files, list):
        return []
    return [item for item in files if isinstance(item, dict)]


def _hash_row(root: Path, path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": stat.st_size,
        "sha256": _sha256(path),
    }


def _hash_rows_for_prepared(
    workspace_dir: Path,
    prepared: list[_PreparedEdit],
) -> dict[str, dict[str, Any]]:
    workspace = workspace_dir.resolve()
    return {
        item.path: _hash_row(workspace, item.file_path)
        for item in prepared
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _plan_status(manifest: dict[str, Any]) -> str:
    plan = manifest.get("plan", {})
    if isinstance(plan, dict):
        return str(plan.get("status", ""))
    return ""


def _dict_value(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_string(item) for item in value if _string(item)]


def _clip_text(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n... [truncated]"


def _emit(callback: MessageCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
