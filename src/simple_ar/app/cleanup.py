from __future__ import annotations

import sqlite3
import shutil
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from rich.console import Console
from rich.panel import Panel
from rich.tree import Tree

from simple_ar.core.artifacts import read_json, write_json
from simple_ar.core.console import make_console
from simple_ar.literature.cache import DEFAULT_CACHE_DIR as DEFAULT_LITERATURE_CACHE_DIR
from simple_ar.research.store.index import DEFAULT_SHARED_INDEX_ROOT


CleanTargetKind = Literal["file", "directory", "sqlite_rows", "lancedb_rows"]


class CleanError(RuntimeError):
    """Raised when a run cannot be cleaned safely."""


@dataclass(frozen=True)
class CleanTarget:
    """One cache or accelerator target selected for cleanup."""

    kind: CleanTargetKind
    label: str
    reason: str
    path: Path | None = None
    bytes_count: int = 0
    sqlite_run_id: str | None = None
    sqlite_rows: int = 0


@dataclass(frozen=True)
class CleanPlan:
    """Preview of what the clean command will delete and preserve."""

    run_dir: Path
    all_caches: bool = False
    shared_index: bool = False
    shared_cache: bool = False
    targets: list[CleanTarget] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CleanResult:
    """Summary of a completed cleanup."""

    deleted_targets: int
    deleted_bytes: int
    deleted_sqlite_rows: int
    deleted_lancedb_rows: int


def build_clean_plan(run_dir: Path, *, all_caches: bool = False) -> CleanPlan:
    """Build a conservative cleanup plan for one run directory.

    The default policy removes rebuildable or bulky cache files while keeping
    audit artifacts such as papers, parser manifests, Paper Briefs, synthesis
    briefs, reports, manifests, and portable text chunks. ``all_caches``
    removes every known run-local cache and accelerator artifact while still
    preserving user-facing reports, manifests, and source evidence records.
    """

    root = run_dir.resolve()
    if not root.exists():
        raise CleanError(f"Run directory does not exist: {run_dir}")
    if not root.is_dir():
        raise CleanError(f"Run path is not a directory: {run_dir}")

    targets: list[CleanTarget] = []
    skipped: list[str] = []
    kept = _existing_relative_paths(
        root,
        [
            "manifest.json",
            "state.json",
            "02-search/papers.jsonl",
            "02-search/search_meta.json",
            "02-search/documents/documents.jsonl",
            "02-search/documents/cache_manifest.json",
            "02-search/documents/fulltext_manifest.json",
            "02-search/documents/fulltext_extraction.json",
            "02-search/review",
            "02-search/research_index/chunks.jsonl",
            "02-search/research_index/index_meta.json",
            "03-read/review",
            "03-read/paper_notes.json",
            "03-read/notes.md",
            "03-read/cards",
            "04-synthesize/synthesis_brief.json",
            "04-synthesize/synthesis.md",
            "04-synthesize/hypothesis.md",
            "04-synthesize/evidence",
            "05-design/evidence",
            "08-report",
            "code_task/summary.md",
        ],
    )

    selected_targets = _ALL_CACHE_TARGETS if all_caches else _RUN_LOCAL_CLEAN_TARGETS
    seen: set[Path] = set()
    for relative, reason in selected_targets:
        _append_path_target(root, targets, seen, relative, reason)

    sqlite_target, sqlite_skipped = _sqlite_clean_target(root)
    if sqlite_target is not None:
        targets.append(sqlite_target)
    skipped.extend(sqlite_skipped)
    if all_caches:
        lancedb_target, lancedb_skipped = _lancedb_clean_target(root)
        if lancedb_target is not None:
            targets.append(lancedb_target)
        skipped.extend(lancedb_skipped)

    return CleanPlan(run_dir=root, all_caches=all_caches, targets=targets, kept=kept, skipped=skipped)


def build_shared_index_clean_plan(
    *,
    index_root: str | Path | None = None,
    allow_external_index_root: bool = False,
) -> CleanPlan:
    """Build a destructive cleanup plan for the shared research index store.

    This clears accelerator state across runs, including SQLite FTS databases
    and optional LanceDB tables/directories. It never removes run-local audit
    files such as ``papers.jsonl`` or ``research_index/chunks.jsonl``.
    """

    root = _resolve_shared_index_root(index_root)
    if not _is_inside_workspace(root) and not allow_external_index_root:
        raise CleanError(
            "Shared index root is outside the current workspace. "
            "Pass --allow-external-index-root only if you intentionally want to clean it: "
            f"{root}"
        )
    targets: list[CleanTarget] = []
    skipped: list[str] = []
    kept: list[str] = []
    if not root.exists():
        skipped.append(f"shared index root does not exist: {root}")
        return CleanPlan(run_dir=root, shared_index=True, targets=targets, kept=kept, skipped=skipped)
    if not root.is_dir():
        raise CleanError(f"Shared index root is not a directory: {root}")
    kept.append(f"{root} (directory itself is kept)")
    for child in sorted(root.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        targets.append(
            CleanTarget(
                kind="directory" if child.is_dir() else "file",
                label=child.name + ("/" if child.is_dir() else ""),
                reason="shared research index store used across runs",
                path=child,
                bytes_count=_path_size(child),
            )
        )
    return CleanPlan(run_dir=root, shared_index=True, targets=targets, kept=kept, skipped=skipped)


def build_shared_cache_clean_plan(
    *,
    index_root: str | Path | None = None,
    literature_cache_root: str | Path | None = None,
    allow_external_index_root: bool = False,
) -> CleanPlan:
    """Build a destructive cleanup plan for all shared cache stores.

    This is stronger than ``build_shared_index_clean_plan``. It clears the
    shared research index store and the shared literature-provider cache under
    ``.simple_ar_cache`` by default. Run-local audit artifacts remain untouched.
    """

    roots = [
        (
            _resolve_shared_index_root(index_root),
            "shared research index store used across runs",
        ),
        (
            _resolve_literature_cache_root(literature_cache_root),
            "shared literature provider cache used across runs",
        ),
    ]
    for root, _reason in roots:
        if not _is_inside_workspace(root) and not allow_external_index_root:
            raise CleanError(
                "Shared cache root is outside the current workspace. "
                "Pass --allow-external-index-root only if you intentionally want to clean it: "
                f"{root}"
            )
    plan_root = _common_root([root for root, _reason in roots])
    targets: list[CleanTarget] = []
    kept: list[str] = []
    skipped: list[str] = []
    seen: set[Path] = set()
    for root, reason in roots:
        if not root.exists():
            skipped.append(f"shared cache root does not exist: {root}")
            continue
        if not root.is_dir():
            raise CleanError(f"Shared cache root is not a directory: {root}")
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        targets.append(
            CleanTarget(
                kind="directory",
                label=_relative_label(plan_root, root) + "/",
                reason=reason,
                path=root,
                bytes_count=_path_size(root),
            )
        )
    kept.append(f"{plan_root} (parent directory is kept)")
    return CleanPlan(run_dir=plan_root, shared_cache=True, targets=targets, kept=kept, skipped=skipped)


def render_clean_plan(plan: CleanPlan, *, console: Console | None = None) -> None:
    """Print a Rich tree preview of the cleanup plan."""

    console = console or make_console()
    if plan.shared_index:
        console.print(
            Panel(
                (
                    "[bold red]Shared-index cleanup is enabled.[/bold red]\n"
                    "This clears accelerator indexes across different runs/tests, "
                    "including SQLite FTS and optional LanceDB data under the shared "
                    "research index root. Future runs can rebuild these indexes from "
                    "their retained run-local artifacts, but cross-run search cache hits "
                    "and index acceleration will be lost."
                ),
                title="[bold red]Strong Cleanup Warning[/bold red]",
                border_style="red",
            )
        )
    if plan.shared_cache:
        console.print(
            Panel(
                (
                    "[bold red]Shared-cache cleanup is enabled.[/bold red]\n"
                    "This clears cross-run cache stores, including the shared "
                    "research index and the literature provider cache. Future "
                    "runs can rebuild these artifacts, but they may need to "
                    "re-query providers, re-download metadata, and rebuild local "
                    "search acceleration."
                ),
                title="[bold red]Strong Cleanup Warning[/bold red]",
                border_style="red",
            )
        )
    if plan.all_caches:
        console.print(
            Panel(
                (
                    "[bold red]All-cache cleanup is enabled.[/bold red]\n"
                    "This removes rebuildable indexes, downloaded/parsed full-text caches, "
                    "artifact search caches, and code-task context caches for this run. "
                    "Reports, manifests, papers, and benchmark results are kept."
                ),
                title="[bold red]Warning[/bold red]",
                border_style="red",
            )
        )
    tree = Tree(f"[green]{plan.run_dir}[/green]")

    delete_branch = tree.add("[bold red]Will delete[/bold red]")
    if plan.targets:
        for target in plan.targets:
            _add_target_node(delete_branch, plan.run_dir, target)
    else:
        delete_branch.add("[dim]No matching cache targets found.[/dim]")

    keep_branch = tree.add("[bold green]Will keep[/bold green]")
    if plan.kept:
        for label in plan.kept:
            keep_branch.add(f"[green]{label}[/green]")
    else:
        keep_branch.add("[dim]No standard audit artifacts found.[/dim]")

    if plan.skipped:
        skipped_branch = tree.add("[bold yellow]Skipped[/bold yellow]")
        for label in plan.skipped:
            skipped_branch.add(f"[yellow]{label}[/yellow]")

    console.print(tree)


def confirm_clean_plan(plan: CleanPlan, *, console: Console | None = None, assume_yes: bool = False) -> bool:
    """Ask for a human confirmation unless ``assume_yes`` is set."""

    if not plan.targets:
        return False
    if assume_yes:
        return True
    console = console or make_console()
    if plan.shared_cache:
        prompt = "Type yes to clear ALL shared caches across runs, or no to cancel: "
    elif plan.shared_index:
        prompt = "Type yes to clear this SHARED index store across runs, or no to cancel: "
    elif plan.all_caches:
        prompt = "Type yes to delete ALL listed caches, or no to cancel: "
    else:
        prompt = "Type yes to delete these items, or no to cancel: "
    answer = console.input(f"[bold]{prompt}[/bold]").strip().lower()
    return answer in {"yes", "y"}


def apply_clean_plan(plan: CleanPlan) -> CleanResult:
    """Execute a previously reviewed cleanup plan."""

    deleted_targets = 0
    deleted_bytes = 0
    deleted_sqlite_rows = 0
    deleted_lancedb_rows = 0
    for target in plan.targets:
        if target.kind in {"file", "directory"}:
            if target.path is None:
                continue
            _assert_inside(plan.run_dir, target.path)
            if not target.path.exists():
                continue
            deleted_bytes += target.bytes_count
            if target.kind == "directory":
                shutil.rmtree(target.path)
            else:
                target.path.unlink()
            deleted_targets += 1
            continue
        if target.kind == "sqlite_rows":
            deleted_sqlite_rows += _delete_sqlite_rows(target)
            _mark_index_meta_cleaned(plan.run_dir, target)
            deleted_targets += 1
            continue
        if target.kind == "lancedb_rows":
            deleted_lancedb_rows += _delete_lancedb_rows(target)
            deleted_targets += 1
    return CleanResult(
        deleted_targets=deleted_targets,
        deleted_bytes=deleted_bytes,
        deleted_sqlite_rows=deleted_sqlite_rows,
        deleted_lancedb_rows=deleted_lancedb_rows,
    )


_RUN_LOCAL_CLEAN_TARGETS: tuple[tuple[str, str], ...] = (
    ("02-search/documents/fulltext_cache", "downloaded full-text cache"),
    ("02-search/documents/extracted_text", "parsed full-text cache"),
    ("artifact_search_results.json", "last artifact search output"),
)

_ALL_CACHE_TARGETS: tuple[tuple[str, str], ...] = (
    ("02-search/documents/fulltext_cache", "downloaded full-text cache"),
    ("02-search/documents/extracted_text", "parsed full-text cache"),
    ("02-search/research_index", "run-local portable and accelerator research index"),
    ("artifact_index.json", "rebuildable run artifact index"),
    ("artifact_chunks.jsonl", "rebuildable artifact retrieval chunks"),
    ("artifact_search_results.json", "last artifact search output"),
    ("code_task/meta/codebase_index.json", "rebuildable codebase file index"),
    ("code_task/meta/repo_map.json", "rebuildable code-task repo map"),
    ("code_task/meta/repo_map_summary.md", "rebuildable repo-map summary"),
    ("code_task/meta/locate_results.json", "rebuildable code-task locate results"),
    ("code_task/meta/locate_results.md", "rebuildable code-task locate summary"),
    ("code_task/context_packs", "rebuildable code-task prompt context packs"),
)


def _append_path_target(
    root: Path,
    targets: list[CleanTarget],
    seen: set[Path],
    relative: str,
    reason: str,
) -> None:
    path = root / relative
    if not path.exists():
        return
    _assert_inside(root, path)
    resolved = path.resolve()
    if resolved in seen:
        return
    seen.add(resolved)
    kind: CleanTargetKind = "directory" if path.is_dir() else "file"
    targets.append(
        CleanTarget(
            kind=kind,
            label=relative,
            reason=reason,
            path=path,
            bytes_count=_path_size(path),
        )
    )


def _existing_relative_paths(root: Path, candidates: list[str]) -> list[str]:
    values: list[str] = []
    for relative in candidates:
        if (root / relative).exists():
            values.append(relative)
    return values


def _sqlite_clean_target(run_dir: Path) -> tuple[CleanTarget | None, list[str]]:
    meta_path = run_dir / "02-search" / "research_index" / "index_meta.json"
    if not meta_path.exists():
        return None, []
    try:
        meta = read_json(meta_path)
    except Exception as exc:
        return None, [f"02-search/research_index/index_meta.json could not be read: {exc}"]
    if not isinstance(meta, dict):
        return None, ["02-search/research_index/index_meta.json is not an object"]
    store = meta.get("store") if isinstance(meta.get("store"), dict) else {}
    sqlite_meta = meta.get("sqlite_fts") if isinstance(meta.get("sqlite_fts"), dict) else {}
    run_id = str(store.get("run_id") or run_dir.name)
    sqlite_path_value = sqlite_meta.get("path")
    if not sqlite_path_value:
        return None, []
    sqlite_path = Path(str(sqlite_path_value)).resolve()
    if not sqlite_path.exists():
        return None, [f"shared SQLite index missing: {sqlite_path}"]
    cwd = Path.cwd().resolve()
    try:
        sqlite_path.relative_to(cwd)
    except ValueError:
        return None, [f"shared SQLite index is outside this workspace and was not touched: {sqlite_path}"]
    row_count = _count_sqlite_rows(sqlite_path, run_id)
    if row_count <= 0:
        return None, []
    return (
        CleanTarget(
            kind="sqlite_rows",
            label=f"{sqlite_path} rows for run_id={run_id}",
            reason="shared research index accelerator rows",
            path=sqlite_path,
            sqlite_run_id=run_id,
            sqlite_rows=row_count,
        ),
        [],
    )


def _lancedb_clean_target(run_dir: Path) -> tuple[CleanTarget | None, list[str]]:
    meta_path = run_dir / "02-search" / "research_index" / "index_meta.json"
    if not meta_path.exists():
        return None, []
    try:
        meta = read_json(meta_path)
    except Exception as exc:
        return None, [f"02-search/research_index/index_meta.json could not be read for LanceDB cleanup: {exc}"]
    if not isinstance(meta, dict):
        return None, []
    store = meta.get("store") if isinstance(meta.get("store"), dict) else {}
    lancedb_meta = meta.get("lancedb") if isinstance(meta.get("lancedb"), dict) else {}
    run_id = str(store.get("run_id") or run_dir.name)
    path_value = lancedb_meta.get("path")
    if not path_value:
        return None, []
    lancedb_path = Path(str(path_value)).resolve()
    if not lancedb_path.exists():
        return None, [f"shared LanceDB index missing: {lancedb_path}"]
    cwd = Path.cwd().resolve()
    try:
        lancedb_path.relative_to(cwd)
    except ValueError:
        return None, [f"shared LanceDB index is outside this workspace and was not touched: {lancedb_path}"]
    rows = _count_lancedb_rows(lancedb_path, run_id)
    if rows is None:
        return None, [f"LanceDB rows for run_id={run_id} could not be counted; install lancedb to clean shared LanceDB rows."]
    if rows <= 0:
        return None, []
    return (
        CleanTarget(
            kind="lancedb_rows",
            label=f"{lancedb_path} LanceDB rows for run_id={run_id}",
            reason="shared LanceDB accelerator rows",
            path=lancedb_path,
            sqlite_run_id=run_id,
            sqlite_rows=rows,
        ),
        [],
    )


def _count_sqlite_rows(path: Path, run_id: str) -> int:
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(path)
        row = conn.execute("SELECT count(*) FROM chunks WHERE run_id = ?", (run_id,)).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0
    finally:
        if conn is not None:
            conn.close()


def _delete_sqlite_rows(target: CleanTarget) -> int:
    if target.path is None or target.sqlite_run_id is None:
        return 0
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(target.path)
        row = conn.execute("SELECT count(*) FROM chunks WHERE run_id = ?", (target.sqlite_run_id,)).fetchone()
        before = int(row[0]) if row else 0
        conn.execute("DELETE FROM chunks WHERE run_id = ?", (target.sqlite_run_id,))
        conn.commit()
        return before
    except sqlite3.Error:
        return 0
    finally:
        if conn is not None:
            conn.close()


def _count_lancedb_rows(path: Path, run_id: str) -> int | None:
    try:
        import lancedb  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return None
    try:
        table = lancedb.connect(str(path)).open_table("chunks")
        return int(table.count_rows(f"run_id = '{_lancedb_literal(run_id)}'"))
    except Exception:
        return None


def _delete_lancedb_rows(target: CleanTarget) -> int:
    if target.path is None or target.sqlite_run_id is None:
        return 0
    try:
        import lancedb  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return 0
    try:
        table = lancedb.connect(str(target.path)).open_table("chunks")
        before = int(table.count_rows(f"run_id = '{_lancedb_literal(target.sqlite_run_id)}'"))
        table.delete(f"run_id = '{_lancedb_literal(target.sqlite_run_id)}'")
        return before
    except Exception:
        return 0


def _lancedb_literal(value: str) -> str:
    return value.replace("'", "''")


def _mark_index_meta_cleaned(run_dir: Path, target: CleanTarget) -> None:
    meta_path = run_dir / "02-search" / "research_index" / "index_meta.json"
    if not meta_path.exists():
        return
    try:
        meta = read_json(meta_path)
    except Exception:
        return
    if not isinstance(meta, dict):
        return
    sqlite_meta = meta.get("sqlite_fts")
    if not isinstance(sqlite_meta, dict):
        return
    sqlite_meta["status"] = "cleaned"
    sqlite_meta["cleaned_rows"] = target.sqlite_rows
    sqlite_meta["notes"] = "Shared accelerator rows were removed by `simple-ar clean`; chunks.jsonl remains the portable source of truth."
    write_json(meta_path, meta)


def _add_target_node(branch: Tree, run_dir: Path, target: CleanTarget) -> None:
    if target.kind in {"sqlite_rows", "lancedb_rows"}:
        branch.add(
            f"[red]{target.label}[/red] "
            f"[dim]({target.sqlite_rows} row(s), {target.reason})[/dim]"
        )
        return
    size = _format_bytes(target.bytes_count)
    node = branch.add(f"[red]{target.label}[/red] [dim]({size}, {target.reason})[/dim]")
    if target.path is None or not target.path.is_dir():
        return
    for child in _preview_children(target.path, run_dir):
        node.add(f"[red]{child}[/red]")


def _preview_children(path: Path, run_dir: Path, *, limit: int = 12) -> list[str]:
    children = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    labels: list[str] = []
    for child in children[:limit]:
        suffix = "/" if child.is_dir() else ""
        labels.append(_relative_label(run_dir, child) + suffix)
    remaining = len(children) - limit
    if remaining > 0:
        labels.append(f"... {remaining} more item(s)")
    return labels


def _path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


def _assert_inside(root: Path, path: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise CleanError(f"Refusing to clean path outside run directory: {path}") from exc


def _relative_label(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value / (1024 * 1024):.1f} MiB"


def _resolve_shared_index_root(value: str | Path | None) -> Path:
    if value is not None and str(value).strip():
        return Path(str(value)).resolve()
    env_value = os.environ.get("SIMPLE_AR_RESEARCH_INDEX_ROOT", "").strip()
    return Path(env_value or DEFAULT_SHARED_INDEX_ROOT).resolve()


def _resolve_literature_cache_root(value: str | Path | None) -> Path:
    if value is not None and str(value).strip():
        return Path(str(value)).resolve()
    return DEFAULT_LITERATURE_CACHE_DIR.resolve()


def _common_root(paths: list[Path]) -> Path:
    if not paths:
        return Path.cwd().resolve()
    try:
        return Path(os.path.commonpath([str(path.resolve()) for path in paths])).resolve()
    except ValueError as exc:
        raise CleanError("Shared cache roots must be on the same drive to clean together.") from exc


def _is_inside_workspace(path: Path) -> bool:
    try:
        path.resolve().relative_to(Path.cwd().resolve())
        return True
    except ValueError:
        return False
