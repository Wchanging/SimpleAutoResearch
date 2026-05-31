from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simple_ar.core.artifacts import read_json, write_json
from simple_ar.code_task.runtime.state import (
    code_task_paths,
    is_relative_to,
    load_code_task_manifest,
    save_code_task_manifest,
    utcnow_iso,
)


MAX_MERGED_BATCH_ITEMS = 3
MAX_MERGED_BATCH_TARGET_FILES = 4

ATTEMPT_STATES = {
    "created",
    "work_plan_ready",
    "batching",
    "completed",
    "failed",
    "discarded",
    "rolled_back",
}

BATCH_STATES = {
    "created",
    "context_ready",
    "proposing",
    "proposal_ready",
    "applying",
    "validating",
    "completed",
    "failed",
    "skipped",
}


@dataclass(frozen=True)
class CodeTaskBatchResult:
    """Result returned after creating a work-item batch.

    Args:
        run_dir: Code-task run directory.
        attempt_id: Attempt identifier such as ``attempt-001``.
        batch_id: Batch identifier such as ``batch-001``.
        attempt_state_path: JSON state file for the parent attempt.
        batch_state_path: JSON state file for the batch.
        work_item_id: Work-plan item represented by this batch.
        state: Initial batch state.
    """

    run_dir: Path
    attempt_id: str
    batch_id: str
    attempt_state_path: Path
    batch_state_path: Path
    work_item_id: str
    state: str


@dataclass(frozen=True)
class LoadedCodeTaskBatch:
    """Latest or explicit batch state loaded from a code-task run."""

    run_dir: Path
    attempt_id: str
    batch_id: str
    attempt_state_path: Path
    batch_state_path: Path
    state: dict[str, Any]


def create_code_task_batch(
    run_dir: Path,
    *,
    work_item_id: str,
    attempt_id: str | None = None,
    kind: str = "implementation",
    parent_batch_id: str = "",
    force: bool = False,
) -> CodeTaskBatchResult:
    """Create a state directory for executing one work-plan item.

    The function does not call an LLM and does not edit code. It only creates
    the durable attempt/batch bookkeeping needed by later bounded edit loops.

    Args:
        run_dir: Code-task run directory.
        work_item_id: Work-plan item id, for example ``W1``.
        attempt_id: Optional existing or new attempt id. When omitted, the
            active attempt from ``manifest.json`` is reused or ``attempt-001``
            is created.
        force: Recreate a batch for the same work item even when one already
            exists in the active attempt.

    Returns:
        Paths and identifiers for the created batch.

    Raises:
        FileNotFoundError: If no ``work_plan.json`` exists.
        ValueError: If the requested work item or attempt id is invalid.
        RuntimeError: If the run is not a code-task workflow.
    """

    root = Path(run_dir)
    paths = code_task_paths(root)
    manifest = load_code_task_manifest(root)
    work_plan = _load_work_plan(paths.task_dir / "work_plan.json")
    work_item = _find_work_item(work_plan, work_item_id)
    if work_item is None:
        available = ", ".join(_work_item_ids(work_plan)) or "none"
        raise ValueError(f"Unknown work item `{work_item_id}`. Available: {available}")
    execution_item = _execution_work_item(work_plan, work_item)

    attempt = _ensure_attempt(
        root,
        manifest,
        work_plan=work_plan,
        attempt_id=attempt_id,
    )
    batch_kind = _normalize_kind(kind)
    existing = _find_existing_batch(attempt, str(work_item["id"]), kind=batch_kind)
    if existing is not None and not force:
        batch_state_path = _safe_run_file(root, str(existing.get("state_path", "")))
        if batch_state_path is not None and batch_state_path.is_file():
            return CodeTaskBatchResult(
                run_dir=root,
                attempt_id=str(attempt["id"]),
                batch_id=str(existing["id"]),
                attempt_state_path=paths.task_dir / "attempts" / str(attempt["id"]) / "attempt_state.json",
                batch_state_path=batch_state_path,
                work_item_id=str(work_item["id"]),
                state=str(existing.get("state", "created")),
            )

    attempt_dir = paths.task_dir / "attempts" / str(attempt["id"])
    batch_id = _next_batch_id(attempt_dir / "batches")
    batch_dir = attempt_dir / "batches" / batch_id
    batch_state_path = batch_dir / "batch_state.json"
    now = utcnow_iso()
    batch_state = {
        "schema_version": 1,
        "id": batch_id,
        "attempt_id": attempt["id"],
        "work_item_id": work_item["id"],
        "state": "created",
        "kind": batch_kind,
        "parent_batch_id": parent_batch_id.strip(),
        "created_at": now,
        "updated_at": now,
        "state_history": [
            {
                "state": "created",
                "at": now,
                "reason": "Batch initialized from work_plan item.",
            }
        ],
        "work_item": execution_item,
        "artifacts": {
            "batch_state": _relative_to_run(root, batch_state_path),
            "batch_context": "",
            "context_pack": "",
            "proposed_edits": "",
            "proposal_warnings": "",
            "applied_edits": "",
            "patch_diff": "",
            "validation_report": "",
            "benchmark_run": "",
            "repair_proposal": "",
        },
    }
    write_json(batch_state_path, batch_state)

    attempt["state"] = "batching"
    attempt["updated_at"] = now
    batches = attempt.get("batches")
    if not isinstance(batches, list):
        batches = []
    batch_ref = {
        "id": batch_id,
        "work_item_id": work_item["id"],
        "kind": batch_kind,
        "state": "created",
        "state_path": _relative_to_run(root, batch_state_path),
        "created_at": now,
        "parent_batch_id": parent_batch_id.strip(),
    }
    batches.append(batch_ref)
    attempt["batches"] = batches
    write_json(attempt_dir / "attempt_state.json", attempt)
    _update_manifest_after_batch(root, manifest, attempt, batch_ref)
    return CodeTaskBatchResult(
        run_dir=root,
        attempt_id=str(attempt["id"]),
        batch_id=batch_id,
        attempt_state_path=attempt_dir / "attempt_state.json",
        batch_state_path=batch_state_path,
        work_item_id=str(work_item["id"]),
        state="created",
    )


def load_latest_code_task_batch(run_dir: Path) -> LoadedCodeTaskBatch | None:
    """Load the latest batch referenced by ``manifest.json`` if it exists."""

    root = Path(run_dir)
    manifest = load_code_task_manifest(root)
    attempts = manifest.get("attempts")
    if not isinstance(attempts, dict):
        return None
    latest = attempts.get("latest_batch")
    if not isinstance(latest, str) or not latest.strip():
        return None
    batch_state_path = _safe_run_file(root, latest)
    if batch_state_path is None or not batch_state_path.is_file():
        return None
    state = read_json(batch_state_path)
    if not isinstance(state, dict):
        raise RuntimeError(f"Expected JSON object in {batch_state_path}")
    attempt_id = str(state.get("attempt_id") or batch_state_path.parents[1].name)
    batch_id = str(state.get("id") or batch_state_path.parent.name)
    return LoadedCodeTaskBatch(
        run_dir=root,
        attempt_id=attempt_id,
        batch_id=batch_id,
        attempt_state_path=batch_state_path.parents[2] / "attempt_state.json",
        batch_state_path=batch_state_path,
        state=state,
    )


def update_code_task_batch_state(
    run_dir: Path,
    batch_state_path: Path,
    *,
    state: str | None = None,
    artifacts: dict[str, str] | None = None,
    detail: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update a batch state file and mirror the status into attempt metadata."""

    root = Path(run_dir)
    if not _is_path_inside(root, batch_state_path):
        raise RuntimeError(f"Batch state is outside run directory: {batch_state_path}")
    data = read_json(batch_state_path)
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected JSON object in {batch_state_path}")
    now = utcnow_iso()
    if state is not None:
        if state not in BATCH_STATES:
            raise ValueError("Unsupported batch state: " + state)
        data["state"] = state
        history = data.get("state_history")
        if not isinstance(history, list):
            history = []
        if not history or history[-1].get("state") != state or history[-1].get("reason") != detail:
            history.append({"state": state, "at": now, "reason": detail})
        data["state_history"] = history
    data["updated_at"] = now
    if artifacts:
        current_artifacts = data.get("artifacts")
        if not isinstance(current_artifacts, dict):
            current_artifacts = {}
        current_artifacts.update({key: value for key, value in artifacts.items() if value})
        data["artifacts"] = current_artifacts
    if extra:
        data.update(extra)
    write_json(batch_state_path, data)
    _sync_batch_ref(root, data, batch_state_path)
    return data


def _ensure_attempt(
    run_dir: Path,
    manifest: dict[str, Any],
    *,
    work_plan: dict[str, Any],
    attempt_id: str | None,
) -> dict[str, Any]:
    paths = code_task_paths(run_dir)
    attempts_root = paths.task_dir / "attempts"
    attempts_root.mkdir(parents=True, exist_ok=True)
    if attempt_id is None:
        attempts = manifest.get("attempts")
        if isinstance(attempts, dict) and isinstance(attempts.get("active"), str):
            attempt_id = str(attempts["active"])
        else:
            attempt_id = _next_attempt_id(attempts_root)
    attempt_id = _normalize_attempt_id(attempt_id)
    attempt_dir = attempts_root / attempt_id
    attempt_state_path = attempt_dir / "attempt_state.json"
    if attempt_state_path.exists():
        attempt = read_json(attempt_state_path)
        if not isinstance(attempt, dict):
            raise RuntimeError(f"Expected JSON object in {attempt_state_path}")
        if attempt.get("state") not in ATTEMPT_STATES:
            attempt["state"] = "created"
        return attempt

    now = utcnow_iso()
    attempt = {
        "schema_version": 1,
        "id": attempt_id,
        "state": "work_plan_ready",
        "created_at": now,
        "updated_at": now,
        "work_plan": "code_task/work_plan.json",
        "work_plan_generated_at": work_plan.get("generated_at"),
        "state_history": [
            {
                "state": "work_plan_ready",
                "at": now,
                "reason": "Attempt initialized from current work_plan.json.",
            }
        ],
        "batches": [],
        "budget_profiles": work_plan.get("budget_profiles", {}),
    }
    write_json(attempt_state_path, attempt)
    _update_manifest_after_attempt(run_dir, manifest, attempt)
    return attempt


def _load_work_plan(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing work plan: {path}. Run `simple-ar code-task work-plan <run_dir>` first."
        )
    data = read_json(path)
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected JSON object in {path}")
    return data


def _find_work_item(work_plan: dict[str, Any], work_item_id: str) -> dict[str, Any] | None:
    wanted = _normalize_work_item_id(work_item_id)
    for item in _object_list(work_plan.get("items")):
        if str(item.get("id", "")).upper() == wanted:
            return item
    return None


def _work_item_ids(work_plan: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in _object_list(work_plan.get("items")):
        value = item.get("id")
        if isinstance(value, str) and value:
            ids.append(value)
    return ids


def _find_existing_batch(
    attempt: dict[str, Any],
    work_item_id: str,
    *,
    kind: str,
) -> dict[str, Any] | None:
    for item in _object_list(attempt.get("batches")):
        if item.get("work_item_id") == work_item_id and item.get("kind", "implementation") == kind:
            return item
    return None


def _execution_work_item(work_plan: dict[str, Any], work_item: dict[str, Any]) -> dict[str, Any]:
    chain = _dependent_work_item_chain(work_plan, work_item)
    if len(chain) <= 1:
        result = dict(work_item)
        result.setdefault("source_work_item_ids", [str(work_item.get("id", ""))])
        return result
    return _merge_work_items(chain)


def _dependent_work_item_chain(work_plan: dict[str, Any], first: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a small serial dependency chain that should execute together.

    Work plans are still reviewed as separate items, but tightly coupled source
    changes are often not useful unless they land in the same patch. This helper
    merges only a narrow chain: one direct downstream item at a time, no
    parallel branches, and a strict file budget.
    """

    items = _object_list(work_plan.get("items"))
    by_id = {str(item.get("id", "")): item for item in items if item.get("id")}
    source_ids = [str(first.get("id", ""))]
    chain = [first]
    target_files = _ordered_unique(_string_list(first.get("target_files")))
    while len(chain) < MAX_MERGED_BATCH_ITEMS:
        last_id = source_ids[-1]
        candidates = [
            item for item in items
            if _is_merge_candidate(item, last_id=last_id, source_ids=set(source_ids))
        ]
        if len(candidates) != 1:
            break
        candidate = candidates[0]
        candidate_id = str(candidate.get("id", ""))
        if not candidate_id or candidate_id not in by_id:
            break
        next_targets = _ordered_unique([*target_files, *_string_list(candidate.get("target_files"))])
        if len(next_targets) > MAX_MERGED_BATCH_TARGET_FILES:
            break
        chain.append(candidate)
        source_ids.append(candidate_id)
        target_files = next_targets
    return chain


def _is_merge_candidate(item: dict[str, Any], *, last_id: str, source_ids: set[str]) -> bool:
    item_id = str(item.get("id", ""))
    if not item_id or item_id in source_ids:
        return False
    if item.get("parallelizable") is True:
        return False
    status = str(item.get("status", "pending")).lower()
    if status not in {"", "pending", "ready"}:
        return False
    target_files = _string_list(item.get("target_files"))
    if not target_files:
        return False
    depends_on = _string_list(item.get("depends_on"))
    if last_id not in depends_on:
        return False
    return set(depends_on).issubset(source_ids)


def _merge_work_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    first = items[0]
    source_ids = [str(item.get("id", "")) for item in items if item.get("id")]
    target_files = _ordered_unique(
        path
        for item in items
        for path in _string_list(item.get("target_files"))
    )
    evidence = _ordered_unique(
        path
        for item in items
        for path in _string_list(item.get("read_only_evidence"))
    )
    validation = _ordered_unique(
        value
        for item in items
        for value in _string_list(item.get("validation"))
    )
    done_criteria = _ordered_unique(
        f"{str(item.get('id', ''))}: {criterion}"
        for item in items
        for criterion in _string_list(item.get("done_criteria"))
    )
    objectives = [
        f"{str(item.get('id', ''))}: {str(item.get('objective', '')).strip()}"
        for item in items
        if str(item.get("objective", "")).strip()
    ]
    budget_profile = _merged_budget_profile(items, target_files)
    context_files = _ordered_unique(
        path
        for item in items
        for path in _context_request_files(item.get("context_request"))
    )
    symbols = _ordered_unique(
        symbol
        for item in items
        for symbol in _context_request_symbols(item.get("context_request"))
    )
    merged = dict(first)
    merged.update(
        {
            "id": first.get("id"),
            "source_work_item_ids": source_ids,
            "execution_scope": "merged_dependent_chain",
            "objective": "Execute tightly coupled work items together: " + " / ".join(objectives),
            "target_files": target_files,
            "read_only_evidence": evidence,
            "depends_on": _string_list(first.get("depends_on")),
            "validation": validation or ["Run the recorded benchmark after applying this merged batch."],
            "done_criteria": done_criteria,
            "risk": _merged_risk(items),
            "parallelizable": False,
            "budget_profile": budget_profile,
            "requires_budget_override": (
                budget_profile != "normal"
                or any(bool(item.get("requires_budget_override")) for item in items)
            ),
            "suggested_budget_override": _merged_budget_note(items, budget_profile),
            "context_request": {
                "query": "Inspect the coupled definition, caller, and configuration files before editing.",
                "files": context_files or _ordered_unique([*target_files, *evidence]),
                "symbols": symbols,
            },
        }
    )
    return merged


def _merged_budget_profile(items: list[dict[str, Any]], target_files: list[str]) -> str:
    profiles = [_string(item.get("budget_profile")).lower() for item in items]
    if "absolute" in profiles:
        return "absolute"
    if "large" in profiles or len(target_files) > 2 or len(items) > 1:
        return "large"
    return "normal"


def _merged_risk(items: list[dict[str, Any]]) -> str:
    risks = [
        f"{str(item.get('id', ''))}: {str(item.get('risk', '')).strip()}"
        for item in items
        if str(item.get("risk", "")).strip()
    ]
    return "Merged dependent batch; validate all coupled files together. " + " ".join(risks)


def _merged_budget_note(items: list[dict[str, Any]], budget_profile: str) -> str:
    notes = _ordered_unique(
        _string(item.get("suggested_budget_override"))
        for item in items
        if _string(item.get("suggested_budget_override"))
    )
    if notes:
        return " ".join(notes)
    if budget_profile == "large":
        return "Merged dependent work items require a larger review gate but should remain a cohesive small patch."
    if budget_profile == "absolute":
        return "Merged batch inherited an absolute budget profile; review carefully before applying."
    return ""


def _context_request_files(value: object) -> list[str]:
    data = value if isinstance(value, dict) else {}
    return _string_list(data.get("files"))


def _context_request_symbols(value: object) -> list[str]:
    data = value if isinstance(value, dict) else {}
    return _string_list(data.get("symbols"))


def _ordered_unique(values: object) -> list[str]:
    if isinstance(values, str):
        iterable = [values]
    else:
        try:
            iterable = list(values)  # type: ignore[arg-type]
        except TypeError:
            iterable = []
    result: list[str] = []
    for value in iterable:
        text = _string(value)
        if text and text not in result:
            result.append(text)
    return result


def _sync_batch_ref(run_dir: Path, batch_state: dict[str, Any], batch_state_path: Path) -> None:
    manifest = load_code_task_manifest(run_dir)
    attempt_id = str(batch_state.get("attempt_id", ""))
    if not attempt_id:
        return
    attempt_state_path = batch_state_path.parents[2] / "attempt_state.json"
    if not attempt_state_path.is_file():
        return
    attempt = read_json(attempt_state_path)
    if not isinstance(attempt, dict):
        return
    batches = attempt.get("batches")
    if not isinstance(batches, list):
        batches = []
    rel_state_path = _relative_to_run(run_dir, batch_state_path)
    updated = False
    for item in batches:
        if isinstance(item, dict) and item.get("id") == batch_state.get("id"):
            item["state"] = batch_state.get("state", item.get("state"))
            item["state_path"] = rel_state_path
            item["updated_at"] = batch_state.get("updated_at")
            updated = True
    if not updated:
        batches.append(
            {
                "id": batch_state.get("id"),
                "work_item_id": batch_state.get("work_item_id"),
                "kind": batch_state.get("kind", "implementation"),
                "state": batch_state.get("state"),
                "state_path": rel_state_path,
                "updated_at": batch_state.get("updated_at"),
            }
        )
    attempt["batches"] = batches
    attempt["updated_at"] = batch_state.get("updated_at")
    next_attempt_state = _attempt_state_from_batches(batches, latest_state=str(batch_state.get("state", "")))
    if attempt.get("state") != next_attempt_state:
        attempt["state"] = next_attempt_state
        history = attempt.get("state_history")
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "state": next_attempt_state,
                "at": batch_state.get("updated_at"),
                "reason": "Attempt state synchronized from batch states.",
            }
        )
        attempt["state_history"] = history
    write_json(attempt_state_path, attempt)
    _update_manifest_after_batch(
        run_dir,
        manifest,
        attempt,
        {
            "id": batch_state.get("id"),
            "state": batch_state.get("state"),
            "state_path": rel_state_path,
            "updated_at": batch_state.get("updated_at"),
        },
    )


def _update_manifest_after_attempt(
    run_dir: Path,
    manifest: dict[str, Any],
    attempt: dict[str, Any],
) -> None:
    layout = manifest.get("layout")
    if not isinstance(layout, dict):
        layout = {}
    layout["attempts"] = "code_task/attempts"
    manifest["layout"] = layout
    attempts = manifest.get("attempts")
    if not isinstance(attempts, dict):
        attempts = {}
    attempts.update(
        {
            "active": attempt["id"],
            "latest_attempt": f"code_task/attempts/{attempt['id']}/attempt_state.json",
        }
    )
    manifest["attempts"] = attempts
    save_code_task_manifest(run_dir, manifest)


def _update_manifest_after_batch(
    run_dir: Path,
    manifest: dict[str, Any],
    attempt: dict[str, Any],
    batch_ref: dict[str, Any],
) -> None:
    layout = manifest.get("layout")
    if not isinstance(layout, dict):
        layout = {}
    layout["attempts"] = "code_task/attempts"
    manifest["layout"] = layout

    attempts = manifest.get("attempts")
    if not isinstance(attempts, dict):
        attempts = {}
    attempt_refs = attempts.get("items")
    if not isinstance(attempt_refs, list):
        attempt_refs = []
    attempt_path = f"code_task/attempts/{attempt['id']}/attempt_state.json"
    attempt_refs = [item for item in attempt_refs if not (
        isinstance(item, dict) and item.get("id") == attempt["id"]
    )]
    attempt_refs.append(
        {
            "id": attempt["id"],
            "state": attempt.get("state", "batching"),
            "state_path": attempt_path,
            "batch_count": len(_object_list(attempt.get("batches"))),
            "updated_at": attempt.get("updated_at"),
        }
    )
    attempts.update(
        {
            "active": attempt["id"],
            "latest_attempt": attempt_path,
            "latest_batch": batch_ref.get("state_path", ""),
            "items": attempt_refs,
        }
    )
    manifest["attempts"] = attempts
    work_plan = manifest.get("work_plan")
    if isinstance(work_plan, dict):
        batch_states = _batch_state_values(attempt.get("batches"))
        if batch_ref.get("state") == "completed":
            work_plan["status"] = "completed"
        elif batch_states and all(state in {"failed", "skipped"} for state in batch_states):
            work_plan["status"] = "failed"
        elif batch_states:
            work_plan["status"] = "batching"
        manifest["work_plan"] = work_plan
    if manifest.get("status") in {None, "", "initialized", "work_planned"}:
        manifest["status"] = "batch_created"
    save_code_task_manifest(run_dir, manifest)


def _next_attempt_id(root: Path) -> str:
    return f"attempt-{_next_number(root, r'attempt-(\d{3})'):03d}"


def _next_batch_id(root: Path) -> str:
    return f"batch-{_next_number(root, r'batch-(\d{3})'):03d}"


def _attempt_state_from_batches(batches: object, *, latest_state: str) -> str:
    """Return the parent attempt state implied by current batch progress."""

    states = _batch_state_values(batches)
    if latest_state == "completed":
        return "completed"
    if states and all(state in {"failed", "skipped"} for state in states):
        return "failed"
    if states:
        return "batching"
    return "work_plan_ready"


def _batch_state_values(batches: object) -> list[str]:
    return [
        str(item.get("state", ""))
        for item in _object_list(batches)
        if item.get("state")
    ]


def _next_number(root: Path, pattern: str) -> int:
    root.mkdir(parents=True, exist_ok=True)
    max_id = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        match = re.fullmatch(pattern, child.name)
        if match:
            max_id = max(max_id, int(match.group(1)))
    return max_id + 1


def _normalize_attempt_id(value: str) -> str:
    text = value.strip().lower()
    if not re.fullmatch(r"attempt-[0-9]{3}", text):
        raise ValueError("attempt_id must look like attempt-001")
    return text


def _normalize_work_item_id(value: str) -> str:
    text = value.strip().upper()
    if not re.fullmatch(r"W[0-9]{1,3}", text):
        raise ValueError("work_item_id must look like W1")
    return text


def _normalize_kind(value: str) -> str:
    text = value.strip().lower() if isinstance(value, str) else ""
    if text in {"implementation", "repair"}:
        return text
    return "implementation"


def _is_path_inside(run_dir: Path, path: Path) -> bool:
    return is_relative_to(path.resolve(), run_dir.resolve())


def _safe_run_file(run_dir: Path, relative_path: str) -> Path | None:
    rel = Path(relative_path)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    root = run_dir.resolve()
    path = (root / rel).resolve()
    return path if is_relative_to(path, root) else None


def _relative_to_run(run_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        return str(path)


def _object_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_string(item) for item in value if _string(item)]
