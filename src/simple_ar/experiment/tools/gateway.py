from __future__ import annotations

from pathlib import Path
from typing import Any

from simple_ar.core.artifacts import read_json
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
        if name == "validate_results_schema":
            results = load_optional_json(self.run_dir / "07-run" / "results.json")
            schema = load_optional_json(self.run_dir / "05-design" / "result_schema.json")
            return {"guard": evaluate_result_guard(results, result_schema=schema)}
        if name == "inspect_execution_failure":
            return {
                "guard_report": load_optional_json(self.run_dir / "07-run" / "guard_report.json"),
                "stdout_tail": _tail_text(self.run_dir / "07-run" / "stdout.txt"),
                "stderr_tail": _tail_text(self.run_dir / "07-run" / "stderr.txt"),
                "results": load_optional_json(self.run_dir / "07-run" / "results.json"),
            }
        raise RuntimeError(f"Unhandled tool: {name}")

    def _list_artifacts(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for stage in ("05-design", "06-code", "07-run"):
            stage_dir = self.run_dir / stage
            if not stage_dir.is_dir():
                continue
            for path in sorted(stage_dir.rglob("*")):
                if path.is_file():
                    rows.append(
                        {
                            "path": path.relative_to(self.run_dir).as_posix(),
                            "bytes": path.stat().st_size,
                        }
                    )
        return rows


def _tail_text(path: Path, limit: int = 4000) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-limit:]

