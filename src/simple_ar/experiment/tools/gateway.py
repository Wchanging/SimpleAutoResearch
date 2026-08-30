from __future__ import annotations

from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from simple_ar.experiment.execution.guards import evaluate_result_guard
from simple_ar.experiment.execution.results import load_optional_json
from simple_ar.experiment.tools.registry import experiment_tool_spec_map
from simple_ar.experiment.tools.specs import ExperimentToolResult


class LocalExperimentToolGateway:
    """Read-only local tool gateway for experiment/report/repair agents."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self._specs = experiment_tool_spec_map()

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> ExperimentToolResult:
        spec = self._specs.get(name)
        if spec is None:
            return ExperimentToolResult(name=name, status="error", data={}, error="Unknown tool")
        if spec.permission != "read_only":
            return ExperimentToolResult(
                name=name,
                status="blocked",
                data={"permission": spec.permission},
                error="This gateway only executes read-only tools.",
            )
        try:
            data = self._dispatch(name, arguments or {})
        except Exception as exc:
            return ExperimentToolResult(name=name, status="error", data={}, error=str(exc))
        return ExperimentToolResult(name=name, status="ok", data=data)

    def _dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "read_experiment_contract":
            return {
                "experiment_contract": load_optional_json(self.run_dir / "05-design" / "experiment_contract.json"),
                "result_schema": load_optional_json(self.run_dir / "05-design" / "result_schema.json"),
                "resource_plan": load_optional_json(self.run_dir / "05-design" / "resource_plan.json"),
                "domain_profile": load_optional_json(self.run_dir / "05-design" / "domain_profile.json"),
            }
        if name == "list_experiment_artifacts":
            return {"artifacts": self._list_artifacts()}
        if name == "read_results_json":
            return {"results": load_optional_json(self.run_dir / "07-run" / "results.json")}
        if name == "read_experiment_diagnosis":
            return {"diagnosis": load_optional_json(self.run_dir / "07-run" / "diagnosis.json")}
        if name == "validate_results_schema":
            results = load_optional_json(self.run_dir / "07-run" / "results.json")
            schema = load_optional_json(self.run_dir / "05-design" / "result_schema.json")
            return {"guard": evaluate_result_guard(results, result_schema=schema)}
        if name == "inspect_execution_failure":
            return {
                "guard_report": load_optional_json(self.run_dir / "07-run" / "guard_report.json"),
                "diagnosis": load_optional_json(self.run_dir / "07-run" / "diagnosis.json"),
                "stdout_tail": _tail_text(self.run_dir / "07-run" / "stdout.txt"),
                "stderr_tail": _tail_text(self.run_dir / "07-run" / "stderr.txt"),
                "results": load_optional_json(self.run_dir / "07-run" / "results.json"),
            }
        if name == "list_generated_code_files":
            return {"files": self._list_generated_code_files(arguments)}
        if name == "read_generated_code_file":
            return self._read_generated_code_file(arguments)
        if name == "search_generated_code":
            return {"matches": self._search_generated_code(arguments)}
        raise RuntimeError(f"Unhandled tool: {name}")

    def _list_artifacts(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for stage in ("05-design", "06-code", "07-run"):
            stage_dir = self.run_dir / stage
            if not stage_dir.is_dir():
                continue
            for path in sorted(stage_dir.rglob("*")):
                if path.is_file() and not _is_rebuildable_noise(path):
                    rows.append(
                        {
                            "path": path.relative_to(self.run_dir).as_posix(),
                            "bytes": path.stat().st_size,
                        }
                    )
        return rows

    def _generated_project_dir(self) -> Path:
        return self.run_dir / "06-code" / "generated_project"

    def _list_generated_code_files(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        root = self._generated_project_dir()
        if not root.is_dir():
            return []
        extensions_raw = arguments.get("extensions")
        extensions = {
            str(item).lower() if str(item).startswith(".") else "." + str(item).lower()
            for item in extensions_raw
            if str(item).strip()
        } if isinstance(extensions_raw, list) else set()
        rows: list[dict[str, Any]] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or _is_rebuildable_noise(path):
                continue
            if extensions and path.suffix.lower() not in extensions:
                continue
            rel = path.relative_to(root).as_posix()
            rows.append(
                {
                    "path": rel,
                    "bytes": path.stat().st_size,
                    "line_count": _line_count(path),
                    "extension": path.suffix,
                }
            )
        return rows

    def _read_generated_code_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        rel = _safe_relative_path(str(arguments.get("path", "")))
        if not rel:
            raise ValueError("Unsafe or missing generated code path.")
        path = self._generated_project_dir() / rel
        if not path.is_file():
            raise FileNotFoundError(f"Generated code file not found: {rel}")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start_line = _bounded_int(arguments.get("start_line"), default=1, minimum=1, maximum=max(1, len(lines)))
        max_lines = _bounded_int(arguments.get("max_lines"), default=80, minimum=1, maximum=200)
        start_index = start_line - 1
        selected = lines[start_index : start_index + max_lines]
        return {
            "path": rel,
            "start_line": start_line,
            "end_line": start_line + len(selected) - 1 if selected else start_line,
            "total_lines": len(lines),
            "text": "\n".join(selected),
            "truncated": start_index + max_lines < len(lines),
        }

    def _search_generated_code(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("search_generated_code requires a non-empty query.")
        max_matches = _bounded_int(arguments.get("max_matches"), default=30, minimum=1, maximum=100)
        case_sensitive = bool(arguments.get("case_sensitive", False))
        needle = query if case_sensitive else query.lower()
        root = self._generated_project_dir()
        matches: list[dict[str, Any]] = []
        if not root.is_dir():
            return matches
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".py", ".json", ".toml", ".md", ".txt"}:
                continue
            if _is_rebuildable_noise(path):
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            rel = path.relative_to(root).as_posix()
            for number, line in enumerate(lines, start=1):
                haystack = line if case_sensitive else line.lower()
                if needle in haystack:
                    matches.append({"path": rel, "line": number, "preview": line.strip()[:300]})
                    if len(matches) >= max_matches:
                        return matches
        return matches


def _tail_text(path: Path, limit: int = 4000) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-limit:]


def _is_rebuildable_noise(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    return "__pycache__" in parts or path.suffix.lower() in {".pyc", ".pyo"}


def _safe_relative_path(value: str) -> str:
    value = value.replace("\\", "/").strip().lstrip("/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    return path.as_posix()


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0
