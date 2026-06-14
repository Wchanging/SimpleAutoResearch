from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from simple_ar.core.artifacts import write_json
from simple_ar.core.pipeline import Context


@dataclass(frozen=True)
class StageArchive:
    """Record of preserved stage artifacts before a rerun writes new outputs."""

    archive_dir: Path
    archived_paths: tuple[str, ...]
    skipped_paths: tuple[str, ...]


def preserve_stage_outputs(
    ctx: Context,
    *,
    artifact_paths: Iterable[str],
    reason: str,
) -> StageArchive | None:
    """Copy important existing stage artifacts before a rerun overwrites them.

    The policy is intentionally conservative: reruns remain convenient, but
    reviewed artifacts are not silently lost. Users can opt out with the
    explicit ``overwrite_stage_artifacts`` runtime flag.
    """

    if _overwrite_enabled(ctx):
        return None
    stage_dir = ctx.stage_dir()
    existing = _existing_stage_paths(stage_dir, artifact_paths)
    if not existing:
        return None

    archive_dir = stage_dir / "archives" / _archive_label()
    archived: list[str] = []
    skipped: list[str] = []
    for source in existing:
        rel = source.relative_to(stage_dir)
        target = archive_dir / rel
        try:
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            archived.append(rel.as_posix())
        except OSError:
            skipped.append(rel.as_posix())

    archive = StageArchive(
        archive_dir=archive_dir,
        archived_paths=tuple(archived),
        skipped_paths=tuple(skipped),
    )
    write_json(
        stage_dir / "rerun_archive.json",
        {
            "schema_version": "stage_rerun_archive.v1",
            "stage": ctx.current_stage.name.lower(),
            "reason": reason,
            "archive_dir": _stage_relative(stage_dir, archive_dir),
            "archived_paths": list(archive.archived_paths),
            "skipped_paths": list(archive.skipped_paths),
            "overwrite_stage_artifacts": False,
        },
    )
    if archived:
        ctx.emit(
            "stage_message",
            (
                f"Archived {len(archived)} existing stage artifact(s) to "
                f"`{_stage_relative(stage_dir, archive_dir)}` before rerun."
            ),
        )
    return archive


def _overwrite_enabled(ctx: Context) -> bool:
    value = ctx.config.get("overwrite_stage_artifacts", False)
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}


def _existing_stage_paths(stage_dir: Path, artifact_paths: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for value in artifact_paths:
        rel = Path(value)
        if rel.is_absolute() or ".." in rel.parts:
            continue
        path = stage_dir / rel
        if not path.exists() or path in seen:
            continue
        paths.append(path)
        seen.add(path)
    return paths


def _archive_label() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _stage_relative(stage_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(stage_dir).as_posix()
    except ValueError:
        return str(path)
