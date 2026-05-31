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
from simple_ar.code_task.editing.attempts import (
    LoadedCodeTaskBatch,
    load_latest_code_task_batch,
    update_code_task_batch_state,
)
from simple_ar.code_task.editing.budget import EditBudget, budget_profiles_json, edit_budget_for_profile
from simple_ar.code_task.editing.scope import (
    editable_paths,
    is_protected_edit_path,
    protected_patterns_from_manifest,
)
from simple_ar.code_task.analysis.context import (
    LoadedCodeTaskContextPack,
    load_latest_code_task_context_pack,
)
from simple_ar.code_task.editing.editor import (
    ApplyEditRequest,
    ApplyEditResult,
    EditRequest,
    EditResult,
    EditorContext,
    EditorSafetyPolicy,
    editor_metadata,
)
from simple_ar.code_task.analysis.index import build_codebase_index
from simple_ar.code_task.editing.planning import select_relevant_files
from simple_ar.code_task.analysis.repo_map import build_repo_map
from simple_ar.llm import LLMClient, LLMError, LLMUsage
from simple_ar.usage import summarize_usage


CODE_TASK_EDIT_SYSTEM = (
    "You are a careful senior engineer preparing a minimal JSON edit proposal. "
    "You may propose exact old/new text replacements, but you must not apply "
    "patches yourself. Use only the supplied workspace-relative paths and source "
    "snippets. Keep the patch small and reviewable."
)

MessageCallback = Callable[[str], None]
CONTROLLED_PATCH_BACKEND = "controlled_patch"


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
    allow_large_edits: bool = False,
    budget_profile: str | None = None,
    edit_budget_overrides: dict[str, Any] | None = None,
    message_callback: MessageCallback | None = None,
) -> ProposedEditsResult:
    """Generate controlled old/new text edits through the default editor backend."""

    root = Path(run_dir)
    context = _editor_context_from_run(root)
    safety = EditorSafetyPolicy(
        protected_patterns=protected_patterns_from_manifest(context.manifest),
        allow_large_edits=allow_large_edits,
    )
    request = EditRequest(
        context=context,
        safety=safety,
        model=model,
        use_llm=use_llm,
        force=force,
        max_files=max_files,
        max_source_chars_per_file=max_source_chars_per_file,
        budget_profile=budget_profile,
        edit_budget_overrides=edit_budget_overrides,
        message_callback=message_callback,
    )
    result = ControlledPatchEditorBackend().propose(request)
    return ProposedEditsResult(
        run_dir=result.run_dir,
        proposal_path=result.proposal_path,
        mode=result.mode,
        edit_count=result.edit_count,
        selected_files=result.selected_files,
    )


def apply_patch_edits(
    run_dir: Path,
    *,
    edits_file: Path | None = None,
    allow_unapproved_plan: bool = False,
    allow_large_edits: bool = False,
) -> PatchApplyResult:
    """Apply reviewed old/new text edits through the default editor backend."""

    root = Path(run_dir)
    context = _editor_context_from_run(root)
    safety = EditorSafetyPolicy(
        protected_patterns=protected_patterns_from_manifest(context.manifest),
        allow_large_edits=allow_large_edits,
        allow_unapproved_plan=allow_unapproved_plan,
    )
    result = ControlledPatchEditorBackend().apply(
        ApplyEditRequest(
            context=context,
            safety=safety,
            proposal_path=edits_file,
        )
    )
    return PatchApplyResult(
        run_dir=result.run_dir,
        applied_edits_path=result.applied_edits_path,
        patch_diff_path=result.patch_diff_path,
        changed_files=result.changed_files,
    )


class ControlledPatchEditorBackend:
    """Editor backend that produces and applies bounded old/new replacements."""

    name = CONTROLLED_PATCH_BACKEND

    def propose(self, request: EditRequest) -> EditResult:
        result = _propose_controlled_patch_edits(
            request.context.run_dir,
            model=request.model,
            use_llm=request.use_llm,
            force=request.force,
            max_files=request.max_files,
            max_source_chars_per_file=request.max_source_chars_per_file,
            allow_large_edits=request.safety.allow_large_edits,
            budget_profile=request.budget_profile,
            edit_budget_overrides=request.edit_budget_overrides,
            message_callback=request.message_callback,
        )
        return EditResult(
            backend=self.name,
            run_dir=result.run_dir,
            proposal_path=result.proposal_path,
            mode=result.mode,
            edit_count=result.edit_count,
            selected_files=result.selected_files,
            metadata={"backend": self.name},
        )

    def apply(self, request: ApplyEditRequest) -> ApplyEditResult:
        result = _apply_controlled_patch_edits(
            request.context.run_dir,
            edits_file=request.proposal_path,
            allow_unapproved_plan=request.safety.allow_unapproved_plan,
            allow_large_edits=request.safety.allow_large_edits,
        )
        return ApplyEditResult(
            backend=self.name,
            run_dir=result.run_dir,
            applied_edits_path=result.applied_edits_path,
            patch_diff_path=result.patch_diff_path,
            changed_files=result.changed_files,
            metadata={"backend": self.name},
        )


def _propose_controlled_patch_edits(
    run_dir: Path,
    *,
    model: str | None = None,
    use_llm: bool = True,
    force: bool = False,
    max_files: int = 8,
    max_source_chars_per_file: int = 4000,
    allow_large_edits: bool = False,
    budget_profile: str | None = None,
    edit_budget_overrides: dict[str, Any] | None = None,
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
        allow_large_edits: Accept proposals that exceed the selected profile
            but fit the large profile.
        budget_profile: Optional budget profile override.
        edit_budget_overrides: Optional numeric budget overrides.
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
    protected_patterns = protected_patterns_from_manifest(manifest)
    batch = load_latest_code_task_batch(root)
    batch_constraints = _batch_constraints(batch)
    allowed_edit_files = batch_constraints["target_files"]
    budget = edit_budget_for_profile(
        budget_profile or batch_constraints["budget_profile"],
        overrides=edit_budget_overrides,
    )
    batch_dir = batch.batch_state_path.parent if batch is not None else None
    loaded_context = load_latest_code_task_context_pack(root)
    context_pack_ref: dict[str, Any] | None = None
    if loaded_context is not None:
        selected_context = _context_pack_files(loaded_context, max_files=max_files)
        selected = _context_pack_editable_files(
            loaded_context,
            protected_patterns=protected_patterns,
            max_files=max_files,
        )
        read_only_context = _context_pack_read_only_files(
            loaded_context,
            protected_patterns=protected_patterns,
            max_files=max_files,
        )
        snippets = _context_pack_editable_snippets(
            loaded_context,
            selected_files=selected,
            max_chars_per_file=max_source_chars_per_file,
        )
        context_pack_ref = _context_pack_manifest_ref(root, loaded_context)
        _emit(message_callback, f"Using code-task context pack: {context_pack_ref['path']}")
        if not selected:
            _emit(message_callback, "Context pack has no editable snippets; falling back to index selection.")
            selected_context = []
            context_pack_ref = None
    else:
        selected_context = []
        selected = []
        read_only_context = []
        snippets = []

    if not selected_context:
        selected_context = _selected_context_files(
            manifest,
            index,
            task_text=task_text,
            patch_plan=patch_plan,
            max_files=max_files,
        )
        selected = _editable_context_files(
            index,
            selected_context,
            protected_patterns=protected_patterns,
            max_files=max_files,
        )
        read_only_context = [
            path
            for path in selected_context
            if is_protected_edit_path(path, protected_patterns=protected_patterns)
        ]
        snippets = _source_snippets(
            workspace_dir,
            selected,
            max_chars_per_file=max_source_chars_per_file,
        )

    if allowed_edit_files:
        selected_context = _ordered_allowed_context(
            allowed_edit_files,
            read_only_context,
            max_files=max_files,
        )
        selected = _limit_known_paths(allowed_edit_files, _known_paths(index), max_files=max_files)
        snippets = _source_snippets(
            workspace_dir,
            selected,
            max_chars_per_file=max_source_chars_per_file,
        )
    proposal_allowed_files = allowed_edit_files if allowed_edit_files else selected
    _write_batch_context(
        root,
        batch,
        selected_files=selected,
        read_only_context=read_only_context,
        budget=budget,
        context_pack=context_pack_ref,
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
                    batch_dir=batch_dir,
                    message_callback=message_callback,
                ),
            )
            proposal = _ask_llm_for_edits(
                client,
                task_text=task_text,
                patch_plan=patch_plan,
                index=index,
                snippets=snippets,
                read_only_context=read_only_context,
                protected_patterns=protected_patterns,
                budget=budget,
                allowed_edit_files=proposal_allowed_files,
                batch_work_item=batch_constraints.get("work_item", {}),
            )
            mode = "llm"
        except LLMError as exc:
            _emit(message_callback, f"LLM edit proposal failed; writing offline empty proposal. {exc}")

    if proposal is None:
        proposal = _offline_proposal(selected, read_only_context=read_only_context)

    normalized = _normalize_edit_proposal(
        proposal,
        index=index,
        mode=mode,
        protected_patterns=protected_patterns,
        workspace_dir=workspace_dir,
        budget=budget,
        allow_large_edits=allow_large_edits,
        allowed_edit_files=proposal_allowed_files,
    )
    normalized["selected_files"] = selected
    normalized["read_only_context"] = read_only_context
    normalized["context_pack"] = context_pack_ref
    normalized["batch"] = _batch_ref(root, batch)
    normalized["editor"] = editor_metadata(
        backend=CONTROLLED_PATCH_BACKEND,
        extra={
            "proposal_path": "code_task/meta/proposed_edits.json",
            "mode": mode,
            "batch": normalized["batch"],
            "context_pack": context_pack_ref,
        },
    )
    write_json(proposal_path, normalized)
    _write_batch_proposal(
        root,
        batch,
        proposal=normalized,
        proposal_path=proposal_path,
    )
    _update_manifest_after_proposal(
        manifest_path,
        manifest,
        selected_files=selected,
        read_only_context=read_only_context,
        context_pack=context_pack_ref,
        proposal=normalized,
        budget=normalized.get("budget", {}),
    )
    return ProposedEditsResult(
        run_dir=root,
        proposal_path=proposal_path,
        mode=mode,
        edit_count=len(normalized["edits"]),
        selected_files=tuple(selected),
    )


def _apply_controlled_patch_edits(
    run_dir: Path,
    *,
    edits_file: Path | None = None,
    allow_unapproved_plan: bool = False,
    allow_large_edits: bool = False,
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
    budget_info = proposal.get("budget")
    if (
        isinstance(budget_info, dict)
        and budget_info.get("requires_approval")
        and not budget_info.get("approved")
        and not allow_large_edits
    ):
        raise PermissionError(
            "Proposal exceeds the normal edit budget. Review it and rerun with "
            "`--allow-large-edits` only if the larger patch is intentional."
        )
    edits = _edit_rows(proposal)
    if not edits:
        raise PatchValidationError(f"No edits were found in {proposal_path}")

    applied_budget = _applied_budget_record(proposal, allow_large_edits=allow_large_edits)
    protected_patterns = protected_patterns_from_manifest(manifest)
    prepared = _prepare_edits(
        workspace_dir,
        edits,
        protected_patterns=protected_patterns,
    )
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
        run_dir=root,
        proposal_path=proposal_path,
        proposal=proposal,
        applied_budget=applied_budget,
        prepared=prepared,
        pre_hash_rows=pre_hash_rows,
        post_hash_rows=post_hash_rows,
    )
    write_json(applied_edits_path, applied)

    codebase_index = build_codebase_index(workspace_dir, output_path=meta_dir / "codebase_index.json")
    repo_map = build_repo_map(
        codebase_index,
        output_path=meta_dir / "repo_map.json",
        summary_path=meta_dir / "repo_map_summary.md",
        protected_patterns=protected_patterns,
    )
    _update_manifest_after_apply(
        manifest_path,
        manifest,
        changed_files=_unique_prepared_paths(prepared),
        codebase_index=codebase_index,
        repo_map=repo_map,
        proposal_path=proposal_path,
        applied_budget=applied_budget,
    )
    _update_latest_batch_after_apply(root, applied_edits_path, patch_diff_path)
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
    read_only_context: list[str],
    protected_patterns: tuple[str, ...],
    budget: EditBudget,
    allowed_edit_files: list[str],
    batch_work_item: object,
) -> dict[str, Any]:
    response = client.ask_json(
        CODE_TASK_EDIT_SYSTEM,
        _edit_user_prompt(
            task_text=task_text,
            patch_plan=patch_plan,
            index=index,
            snippets=snippets,
            read_only_context=read_only_context,
            protected_patterns=protected_patterns,
            budget=budget,
            allowed_edit_files=allowed_edit_files,
            batch_work_item=batch_work_item,
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
    read_only_context: list[str],
    protected_patterns: tuple[str, ...],
    budget: EditBudget,
    allowed_edit_files: list[str],
    batch_work_item: object,
) -> str:
    compact_files = [
        {
            "path": str(item.get("path", "")),
            "kind": item.get("kind"),
            "role_tags": item.get("role_tags", []),
            "edit_role": (
                "read_only"
                if is_protected_edit_path(
                    str(item.get("path", "")),
                    protected_patterns=protected_patterns,
                )
                else "editable"
            ),
            "summary": item.get("summary", ""),
        }
        for item in _index_files(index)
    ]
    snippet_text = "\n\n".join(
        f"### {item.get('path', '')} "
        f"({item.get('access_role', 'editable')})\n"
        f"```text\n{item.get('text', '')}\n```"
        for item in snippets
    )
    return (
        "Return JSON with fields: `summary` string, `edits` list, "
        "`validation` list of strings, `risks` list of strings, and optional "
        "`context_request` object when more files/symbols are needed.\n\n"
        "Each item in `edits` must contain exactly these string fields: "
        "`path`, `old`, `new`, and `reason`.\n\n"
        "Hard rules:\n"
        "- Do not return markdown or a unified diff.\n"
        "- Do not include diff markers such as `+`, `-`, `@@`, `---`, or "
        "`+++` inside `old` or `new`; they must contain only file text.\n"
        "- Use only workspace-relative paths from the provided file inventory.\n"
        "- Only propose edits for files whose inventory `edit_role` is `editable`.\n"
        "- Files whose `edit_role` is `read_only` are evidence only; do not "
        "modify tests, benchmarks, or validation targets.\n"
        "- Each `old` value must be an exact contiguous substring from the "
        "corresponding source snippet, including indentation and newlines.\n"
        "- Make each replacement large enough to be unique in the file.\n"
        "- Do not propose deleting whole files or directories.\n"
        "- Prefer one edit per file. If a file needs multiple nearby changes, "
        "combine them into one larger old/new replacement.\n"
        "- Keep the patch minimal and aligned with the approved patch plan.\n\n"
        "Current edit budget JSON. Stay within this budget. If the task cannot "
        "be completed within it, return a concise `context_request` or explain "
        "why a larger budget is required instead of emitting a giant patch:\n"
        f"{json.dumps(budget.to_json(), indent=2, ensure_ascii=False)}\n\n"
        "Allowed editable files for this batch:\n"
        f"{json.dumps(allowed_edit_files, indent=2, ensure_ascii=False)}\n\n"
        "Current execution work item JSON. If it contains "
        "`source_work_item_ids`, this batch intentionally combines those "
        "dependent items so the proposal should satisfy all listed done "
        "criteria together:\n"
        f"{json.dumps(batch_work_item if isinstance(batch_work_item, dict) else {}, indent=2, ensure_ascii=False)}\n\n"
        "Available budget profiles for later planning:\n"
        f"{json.dumps(budget_profiles_json(), indent=2, ensure_ascii=False)}\n\n"
        f"Task:\n{task_text}\n\n"
        f"Approved patch plan:\n{patch_plan}\n\n"
        f"Workspace file inventory JSON:\n{json.dumps(compact_files, indent=2, ensure_ascii=False)}\n\n"
        "Read-only context files omitted from editable snippets:\n"
        f"{json.dumps(read_only_context, indent=2, ensure_ascii=False)}\n\n"
        f"Selected source snippets:\n{snippet_text or 'No source snippets selected.'}"
    )


def _offline_proposal(selected_files: list[str], *, read_only_context: list[str]) -> dict[str, Any]:
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
        "read_only_context": read_only_context,
    }


def _normalize_edit_proposal(
    proposal: dict[str, Any],
    *,
    index: dict[str, Any],
    mode: str,
    protected_patterns: tuple[str, ...],
    workspace_dir: Path,
    budget: EditBudget,
    allow_large_edits: bool,
    allowed_edit_files: list[str],
) -> dict[str, Any]:
    known_paths = {str(item.get("path", "")) for item in _index_files(index)}
    allowed_paths = set(allowed_edit_files)
    warnings: list[str] = []
    edits: list[dict[str, str]] = []
    rejected_edits: list[dict[str, Any]] = []
    raw_edits = proposal.get("edits", [])
    if not isinstance(raw_edits, list):
        warnings.append("Dropped edits because `edits` was not a list.")
        raw_edits = []
    for item in raw_edits:
        if not isinstance(item, dict):
            warnings.append("Dropped non-object edit.")
            continue
        path = _string(item.get("path"))
        old = item.get("old")
        new = item.get("new")
        if path not in known_paths:
            warnings.append(f"Dropped edit for unknown path: {path or '<empty>'}")
            continue
        if is_protected_edit_path(path, protected_patterns=protected_patterns):
            warnings.append(f"Dropped edit for protected read-only path: {path}")
            continue
        if allowed_paths and path not in allowed_paths:
            warnings.append(f"Dropped edit outside current batch target files: {path}")
            rejected_edits.append({"path": path, "reason": "outside_batch_target_files"})
            continue
        if not isinstance(old, str) or not isinstance(new, str):
            warnings.append(f"Dropped edit for {path}: old/new must be strings.")
            continue
        if _looks_like_diff_fragment(old) or _looks_like_diff_fragment(new):
            warnings.append(
                f"Dropped edit for {path}: old/new must be exact text, not a diff fragment."
            )
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
    budget_result = _evaluate_budget(
        edits,
        proposal=proposal,
        workspace_dir=workspace_dir,
        budget=budget,
        allow_large_edits=allow_large_edits,
    )
    warnings.extend(budget_result["warnings"])
    context_request = proposal.get("context_request")
    if not isinstance(context_request, dict):
        context_request = {}
    if budget_result["drop_edits"]:
        rejected_edits.extend({"path": edit["path"], "reason": budget_result["status"]} for edit in edits)
        edits = []
    return {
        "schema_version": 1,
        "generated_at": _utcnow_iso(),
        "mode": mode,
        "summary": _string(proposal.get("summary")) or "No summary provided.",
        "edits": edits,
        "validation": _string_list(proposal.get("validation")),
        "risks": _string_list(proposal.get("risks")),
        "warnings": warnings,
        "rejected_edits": rejected_edits,
        "context_request": _normalize_context_request(context_request, known_paths),
        "budget": budget_result["budget"],
    }


def _prepare_edits(
    workspace_dir: Path,
    edits: list[dict[str, Any]],
    *,
    protected_patterns: tuple[str, ...],
) -> list[_PreparedEdit]:
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
        if is_protected_edit_path(path, protected_patterns=protected_patterns):
            errors.append(
                f"edit {index} `{path}`: path is protected by the edit scope"
            )
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


def _looks_like_diff_fragment(text: str) -> bool:
    """Detect accidental unified-diff content in structured old/new edits."""
    lines = [line for line in text.splitlines() if line.strip()]
    if any(line.startswith(("@@", "--- ", "+++ ")) for line in lines):
        return True
    removed = any(line.startswith("-") for line in lines)
    added = any(line.startswith("+") for line in lines)
    return removed and added


def _evaluate_budget(
    edits: list[dict[str, str]],
    *,
    proposal: dict[str, Any],
    workspace_dir: Path,
    budget: EditBudget,
    allow_large_edits: bool,
) -> dict[str, Any]:
    stats = _proposal_stats(edits, proposal=proposal, workspace_dir=workspace_dir)
    warnings: list[str] = []
    normal = edit_budget_for_profile("normal")
    large = edit_budget_for_profile("large")
    absolute = edit_budget_for_profile("absolute")
    if _stats_exceed(stats, absolute) or stats["whole_file_rewrite_suspicions"]:
        if stats["whole_file_rewrite_suspicions"]:
            warnings.extend(
                f"Rejected suspected whole-file rewrite for {path}."
                for path in stats["whole_file_rewrite_suspicions"]
            )
        if _stats_exceed(stats, absolute):
            warnings.append("Rejected proposal because it exceeds the absolute edit budget.")
        return {
            "drop_edits": True,
            "status": "rejected_absolute",
            "warnings": warnings,
            "budget": _budget_record(budget, stats, status="rejected_absolute", approved=False),
        }

    if _stats_exceed(stats, budget):
        if not _stats_exceed(stats, large):
            warnings.append(
                "Proposal exceeds the selected edit budget but fits the large budget; "
                "explicit approval is required before apply."
            )
            approved = allow_large_edits
            return {
                "drop_edits": not approved,
                "status": "large_approved" if approved else "large_requires_approval",
                "warnings": warnings,
                "budget": _budget_record(
                    budget,
                    stats,
                    status="large_approved" if approved else "large_requires_approval",
                    approved=approved,
                    requires_approval=True,
                ),
            }
        warnings.append("Rejected proposal because it exceeds the large edit budget.")
        return {
            "drop_edits": True,
            "status": "rejected_large",
            "warnings": warnings,
            "budget": _budget_record(budget, stats, status="rejected_large", approved=False),
        }

    return {
        "drop_edits": False,
        "status": "accepted",
        "warnings": warnings,
        "budget": _budget_record(
            budget,
            stats,
            status="accepted",
            approved=not budget.requires_approval or allow_large_edits,
            requires_approval=budget.requires_approval and not allow_large_edits,
        ),
    }


def _proposal_stats(
    edits: list[dict[str, str]],
    *,
    proposal: dict[str, Any],
    workspace_dir: Path,
) -> dict[str, Any]:
    paths = sorted({edit["path"] for edit in edits})
    total_edit_chars = sum(len(edit["old"]) + len(edit["new"]) for edit in edits)
    max_old_chars = max((len(edit["old"]) for edit in edits), default=0)
    max_new_chars = max((len(edit["new"]) for edit in edits), default=0)
    proposal_chars = len(json.dumps(proposal, ensure_ascii=False))
    whole_file = _whole_file_rewrite_suspicions(workspace_dir, edits)
    return {
        "file_count": len(paths),
        "edit_count": len(edits),
        "max_old_chars": max_old_chars,
        "max_new_chars": max_new_chars,
        "total_edit_chars": total_edit_chars,
        "proposal_chars": proposal_chars,
        "whole_file_rewrite_suspicions": whole_file,
    }


def _stats_exceed(stats: dict[str, Any], budget: EditBudget) -> bool:
    return (
        int(stats.get("file_count", 0)) > budget.max_files
        or int(stats.get("edit_count", 0)) > budget.max_edits
        or int(stats.get("max_old_chars", 0)) > budget.max_old_chars
        or int(stats.get("max_new_chars", 0)) > budget.max_new_chars
        or int(stats.get("total_edit_chars", 0)) > budget.max_total_edit_chars
        or int(stats.get("proposal_chars", 0)) > budget.max_proposal_chars
    )


def _budget_record(
    budget: EditBudget,
    stats: dict[str, Any],
    *,
    status: str,
    approved: bool,
    requires_approval: bool | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "profile": budget.profile,
        "limits": budget.to_json(),
        "stats": stats,
        "requires_approval": budget.requires_approval if requires_approval is None else requires_approval,
        "approved": approved,
    }


def _whole_file_rewrite_suspicions(workspace_dir: Path, edits: list[dict[str, str]]) -> list[str]:
    workspace = workspace_dir.resolve()
    suspicious: list[str] = []
    for edit in edits:
        path = _workspace_file(workspace, edit["path"])
        if path is None or not path.is_file():
            continue
        try:
            source = read_text(path)
        except OSError:
            continue
        source_len = len(source)
        if source_len < 4000:
            continue
        old_ratio = len(edit["old"]) / max(1, source_len)
        new_ratio = len(edit["new"]) / max(1, source_len)
        if old_ratio > 0.85 or new_ratio > 0.95:
            suspicious.append(edit["path"])
    return suspicious


def _normalize_context_request(value: dict[str, Any], known_paths: set[str]) -> dict[str, Any]:
    files = [path for path in _string_list(value.get("files")) if path in known_paths]
    return {
        "query": _string(value.get("query")),
        "files": files,
        "symbols": _string_list(value.get("symbols"))[:20],
        "reason": _string(value.get("reason")),
    }


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
    run_dir: Path,
    proposal_path: Path,
    proposal: dict[str, Any],
    applied_budget: dict[str, Any],
    prepared: list[_PreparedEdit],
    pre_hash_rows: dict[str, dict[str, Any]],
    post_hash_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "applied_at": _utcnow_iso(),
        "proposal": _relative_to_run(run_dir, proposal_path),
        "editor": _proposal_editor_metadata(proposal),
        "budget": applied_budget,
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


def _applied_budget_record(proposal: dict[str, Any], *, allow_large_edits: bool) -> dict[str, Any]:
    budget = proposal.get("budget")
    if not isinstance(budget, dict):
        return {}
    result = dict(budget)
    requires_approval = bool(result.get("requires_approval"))
    was_approved = bool(result.get("approved"))
    if requires_approval and allow_large_edits and not was_approved:
        result["approved"] = True
        result["approval_source"] = "apply_edits_allow_large_edits"
        result["approval_note"] = "Large edit was explicitly allowed during apply."
    return result


def _proposal_editor_metadata(proposal: dict[str, Any]) -> dict[str, Any]:
    editor = proposal.get("editor")
    if isinstance(editor, dict) and editor.get("backend"):
        return editor
    return editor_metadata(
        backend=CONTROLLED_PATCH_BACKEND,
        extra={"source": "legacy_or_manual_proposal"},
    )


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


def _context_pack_files(
    loaded: LoadedCodeTaskContextPack,
    *,
    max_files: int,
) -> list[str]:
    selected: list[str] = []
    for path in loaded.selected_files:
        if path not in selected:
            selected.append(path)
        if len(selected) >= max(1, max_files):
            break
    return selected


def _context_pack_editable_files(
    loaded: LoadedCodeTaskContextPack,
    *,
    protected_patterns: tuple[str, ...],
    max_files: int,
) -> list[str]:
    selected: list[str] = []
    for row in loaded.snippets:
        path = _string(row.get("path"))
        if not path or path in selected:
            continue
        role = str(row.get("access_role", "editable"))
        if role != "editable" or is_protected_edit_path(path, protected_patterns=protected_patterns):
            continue
        selected.append(path)
        if len(selected) >= max(1, max_files):
            break
    return selected


def _context_pack_read_only_files(
    loaded: LoadedCodeTaskContextPack,
    *,
    protected_patterns: tuple[str, ...],
    max_files: int,
) -> list[str]:
    selected: list[str] = []
    for row in loaded.snippets:
        path = _string(row.get("path"))
        if not path or path in selected:
            continue
        role = str(row.get("access_role", "editable"))
        if role == "editable" and not is_protected_edit_path(path, protected_patterns=protected_patterns):
            continue
        selected.append(path)
        if len(selected) >= max(1, max_files):
            break
    return selected


def _context_pack_editable_snippets(
    loaded: LoadedCodeTaskContextPack,
    *,
    selected_files: list[str],
    max_chars_per_file: int,
) -> list[dict[str, str]]:
    selected_set = set(selected_files)
    snippets: list[dict[str, str]] = []
    for row in loaded.snippets:
        path = _string(row.get("path"))
        text = row.get("text")
        if path not in selected_set or not isinstance(text, str):
            continue
        snippets.append(
            {
                "path": path,
                "access_role": "editable",
                "text": _clip_text(text, max_chars=max(200, max_chars_per_file)),
            }
        )
    return snippets


def _context_pack_manifest_ref(
    run_dir: Path,
    loaded: LoadedCodeTaskContextPack,
) -> dict[str, Any]:
    budget = loaded.context_pack.get("budget")
    if not isinstance(budget, dict):
        budget = {}
    return {
        "path": _relative_to_run(run_dir, loaded.context_pack_path),
        "prompt_context": _relative_to_run(run_dir, loaded.prompt_context_path),
        "snippets": _relative_to_run(run_dir, loaded.snippets_path),
        "selected_files": list(loaded.selected_files),
        "budget": budget,
    }


def _batch_constraints(batch: LoadedCodeTaskBatch | None) -> dict[str, Any]:
    if batch is None:
        return {
            "target_files": [],
            "read_only_evidence": [],
            "budget_profile": "normal",
            "work_item": {},
        }
    work_item = batch.state.get("work_item")
    if not isinstance(work_item, dict):
        work_item = {}
    return {
        "target_files": _string_list(work_item.get("target_files")),
        "read_only_evidence": _string_list(work_item.get("read_only_evidence")),
        "budget_profile": _string(work_item.get("budget_profile")) or "normal",
        "validation": _string_list(work_item.get("validation")),
        "work_item": work_item,
    }


def _ordered_allowed_context(
    editable_files: list[str],
    read_only_context: list[str],
    *,
    max_files: int,
) -> list[str]:
    result: list[str] = []
    for path in [*editable_files, *read_only_context]:
        if path and path not in result:
            result.append(path)
        if len(result) >= max(1, max_files):
            break
    return result


def _known_paths(index: dict[str, Any]) -> set[str]:
    return {str(item.get("path", "")) for item in _index_files(index) if item.get("path")}


def _limit_known_paths(paths: list[str], known_paths: set[str], *, max_files: int) -> list[str]:
    result: list[str] = []
    for path in paths:
        if path in known_paths and path not in result:
            result.append(path)
        if len(result) >= max(1, max_files):
            break
    return result


def _write_batch_context(
    run_dir: Path,
    batch: LoadedCodeTaskBatch | None,
    *,
    selected_files: list[str],
    read_only_context: list[str],
    budget: EditBudget,
    context_pack: dict[str, Any] | None,
) -> None:
    if batch is None:
        return
    path = batch.batch_state_path.parent / "batch_context.json"
    data = {
        "schema_version": 1,
        "generated_at": _utcnow_iso(),
        "batch_id": batch.batch_id,
        "work_item_id": batch.state.get("work_item_id"),
        "selected_files": selected_files,
        "read_only_context": read_only_context,
        "budget": budget.to_json(),
        "context_pack": context_pack,
    }
    write_json(path, data)
    update_code_task_batch_state(
        run_dir,
        batch.batch_state_path,
        state="context_ready",
        artifacts={"batch_context": _relative_to_run(run_dir, path)},
        detail="Batch context prepared for controlled edit proposal.",
    )


def _write_batch_proposal(
    run_dir: Path,
    batch: LoadedCodeTaskBatch | None,
    *,
    proposal: dict[str, Any],
    proposal_path: Path,
) -> None:
    if batch is None:
        return
    batch_dir = batch.batch_state_path.parent
    batch_proposal_path = batch_dir / "proposed_edits.json"
    warnings_path = batch_dir / "proposal_warnings.json"
    write_json(batch_proposal_path, proposal)
    write_json(
        warnings_path,
        {
            "schema_version": 1,
            "generated_at": _utcnow_iso(),
            "warnings": proposal.get("warnings", []),
            "rejected_edits": proposal.get("rejected_edits", []),
            "budget": proposal.get("budget", {}),
            "context_request": proposal.get("context_request", {}),
            "editor": proposal.get("editor", {}),
            "top_level_proposal": _relative_to_run(run_dir, proposal_path),
        },
    )
    state = "proposal_ready" if proposal.get("edits") else "failed"
    update_code_task_batch_state(
        run_dir,
        batch.batch_state_path,
        state=state,
        artifacts={
            "proposed_edits": _relative_to_run(run_dir, batch_proposal_path),
            "proposal_warnings": _relative_to_run(run_dir, warnings_path),
        },
        detail="Controlled edit proposal generated for this batch.",
        extra={
            "proposal_budget": proposal.get("budget", {}),
            "editor": proposal.get("editor", {}),
        },
    )


def _batch_ref(run_dir: Path, batch: LoadedCodeTaskBatch | None) -> dict[str, Any] | None:
    if batch is None:
        return None
    return {
        "attempt_id": batch.attempt_id,
        "batch_id": batch.batch_id,
        "state_path": _relative_to_run(run_dir, batch.batch_state_path),
    }


def _update_latest_batch_after_apply(
    run_dir: Path,
    applied_edits_path: Path,
    patch_diff_path: Path,
) -> None:
    batch = load_latest_code_task_batch(run_dir)
    if batch is None:
        return
    update_code_task_batch_state(
        run_dir,
        batch.batch_state_path,
        state="applying",
        artifacts={
            "applied_edits": _relative_to_run(run_dir, applied_edits_path),
            "patch_diff": _relative_to_run(run_dir, patch_diff_path),
        },
        detail="Reviewed proposal applied to workspace.",
    )


def _relative_to_run(run_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        return str(path)


def _editable_context_files(
    index: dict[str, Any],
    selected_files: list[str],
    *,
    protected_patterns: tuple[str, ...],
    max_files: int,
) -> list[str]:
    selected = editable_paths(selected_files, protected_patterns=protected_patterns)
    if selected:
        return selected[: max(1, max_files)]
    fallback: list[str] = []
    for item in _index_files(index):
        path = _string(item.get("path"))
        if not path:
            continue
        if is_protected_edit_path(path, protected_patterns=protected_patterns):
            continue
        kind = _string(item.get("kind"))
        role_tags = [str(tag) for tag in item.get("role_tags", []) if isinstance(tag, str)]
        if kind == "python" or "source" in role_tags:
            fallback.append(path)
        if len(fallback) >= max(1, max_files):
            break
    return fallback


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
                "access_role": "editable",
                "text": _clip_text(text, max_chars=max(200, max_chars_per_file)),
            }
        )
    return snippets


def _update_manifest_after_proposal(
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    selected_files: list[str],
    read_only_context: list[str],
    context_pack: dict[str, Any] | None,
    proposal: dict[str, Any],
    budget: dict[str, Any],
) -> None:
    edits = proposal.get("edits", [])
    patch = _dict_value(manifest, "patch")
    patch.update(
        {
            "status": "edits_proposed",
            "proposed_at": _utcnow_iso(),
            "proposed_edits": "code_task/meta/proposed_edits.json",
            "proposed_edit_count": len(edits) if isinstance(edits, list) else 0,
            "editor": proposal.get("editor", {}),
            "editor_backend": CONTROLLED_PATCH_BACKEND,
            "selected_files": selected_files,
            "read_only_context_files": read_only_context,
            "context_pack": context_pack,
            "budget": budget,
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
    repo_map: dict[str, Any],
    proposal_path: Path,
    applied_budget: dict[str, Any],
) -> None:
    run_dir = manifest_path.parent
    proposal_ref = _relative_to_run(run_dir, proposal_path)
    patch = _dict_value(manifest, "patch")
    patch.update(
        {
            "status": "applied",
            "applied_at": _utcnow_iso(),
            "patch_diff": "code_task/patch.diff",
            "applied_edits": "code_task/meta/applied_edits.json",
            "latest_applied_proposal": proposal_ref,
            "editor_backend": CONTROLLED_PATCH_BACKEND,
            "changed_files": changed_files,
        }
    )
    if applied_budget:
        patch["budget"] = applied_budget
    editor = patch.get("editor")
    if not isinstance(editor, dict):
        editor = editor_metadata(backend=CONTROLLED_PATCH_BACKEND)
    editor.update(
        {
            "backend": CONTROLLED_PATCH_BACKEND,
            "latest_applied_proposal": proposal_ref,
            "applied_edits": "code_task/meta/applied_edits.json",
            "patch_diff": "code_task/patch.diff",
        }
    )
    patch["editor"] = editor
    layout = _dict_value(manifest, "layout")
    layout.update(
        {
            "patch_diff": "code_task/patch.diff",
            "applied_edits": "code_task/meta/applied_edits.json",
            "repo_map": "code_task/meta/repo_map.json",
            "repo_map_summary": "code_task/meta/repo_map_summary.md",
        }
    )
    project = codebase_index.get("project", {})
    repo_project = repo_map.get("project", {})
    manifest["layout"] = layout
    manifest["patch"] = patch
    manifest["status"] = "patched"
    _update_repair_after_apply(manifest, proposal_ref)
    manifest["codebase"] = {
        "file_count": project.get("file_count", 0),
        "python_file_count": project.get("python_file_count", 0),
        "test_file_count": project.get("test_file_count", 0),
        "entrypoint_candidates": project.get("entrypoint_candidates", []),
        "repo_map": {
            "schema_version": repo_map.get("schema_version"),
            "path": "code_task/meta/repo_map.json",
            "summary": "code_task/meta/repo_map_summary.md",
            "directory_count": repo_project.get("directory_count", 0),
            "symbol_count": repo_project.get("symbol_count", 0),
            "benchmark_file_count": repo_project.get("benchmark_file_count", 0),
            "config_file_count": repo_project.get("config_file_count", 0),
        },
    }
    write_json(manifest_path, manifest)


def _update_repair_after_apply(manifest: dict[str, Any], proposal_ref: str) -> None:
    repair = manifest.get("repair")
    if not isinstance(repair, dict) or not repair:
        return
    latest = str(repair.get("latest_proposed_edits") or "")
    if latest and latest != proposal_ref:
        return
    repair["status"] = "repair_applied"
    repair["latest_applied_proposal"] = proposal_ref
    repair["latest_applied_edits"] = "code_task/meta/applied_edits.json"
    repair["applied_at"] = _utcnow_iso()
    manifest["repair"] = repair


def _record_code_task_usage(
    meta_dir: Path,
    usage: LLMUsage,
    *,
    stage: str,
    batch_dir: Path | None = None,
    message_callback: MessageCallback | None,
) -> None:
    usage_path = meta_dir / "llm_usage.jsonl"
    row = usage.to_row()
    row["stage"] = stage
    append_jsonl(usage_path, row)
    write_json(meta_dir / "llm_usage_summary.json", summarize_usage(read_jsonl(usage_path)))
    if batch_dir is not None:
        batch_usage_path = batch_dir / "usage.jsonl"
        append_jsonl(batch_usage_path, row)
        write_json(batch_dir / "usage_summary.json", summarize_usage(read_jsonl(batch_usage_path)))
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


def _editor_context_from_run(run_dir: Path) -> EditorContext:
    root = Path(run_dir)
    task_dir = root / "code_task"
    meta_dir = task_dir / "meta"
    workspace_dir = task_dir / "workspace"
    manifest = _load_code_task_manifest(root / "manifest.json")
    batch = load_latest_code_task_batch(root)
    loaded_context = load_latest_code_task_context_pack(root)
    context_pack_ref = (
        _context_pack_manifest_ref(root, loaded_context)
        if loaded_context is not None
        else None
    )
    return EditorContext(
        run_dir=root,
        task_dir=task_dir,
        workspace_dir=workspace_dir,
        meta_dir=meta_dir,
        manifest=manifest,
        task_text=_read_optional_text(task_dir / "task.md"),
        patch_plan=_read_optional_text(task_dir / "patch_plan.md"),
        codebase_index=_read_optional_json(meta_dir / "codebase_index.json"),
        batch=_batch_ref(root, batch),
        context_pack=context_pack_ref,
    )


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = read_json(path)
    return data if isinstance(data, dict) else {}


def _read_optional_text(path: Path) -> str:
    return read_text(path) if path.exists() else ""


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
