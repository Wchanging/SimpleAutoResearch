from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from simple_ar.app.state import WorkspaceState


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(read_text(path))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    write_text(path, text)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in read_text(path).splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(json.loads(stripped))
    return rows


def state_path(run_dir: Path) -> Path:
    return run_dir / "state.json"


def stage_contract_path(stage_dir: Path) -> Path:
    return stage_dir / "contract.json"


def stage_report_path(stage_dir: Path) -> Path:
    return stage_dir / "report.md"


def write_stage_contract(stage_dir: Path, data: dict[str, Any]) -> Path:
    path = stage_contract_path(stage_dir)
    write_json(path, data)
    return path


def write_stage_report(stage_dir: Path, markdown: str) -> Path:
    path = stage_report_path(stage_dir)
    write_text(path, markdown)
    return path


def load_workspace_state(run_dir: Path) -> WorkspaceState | None:
    path = state_path(run_dir)
    if not path.exists():
        return None
    return WorkspaceState.load(path)


def save_workspace_state(run_dir: Path, state: WorkspaceState) -> Path:
    path = state_path(run_dir)
    state.save(path)
    return path


def relative_to_run(run_dir: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(run_dir)).replace("\\", "/")
    except ValueError:
        return str(path)
