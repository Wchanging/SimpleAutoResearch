from __future__ import annotations

from pathlib import Path
from typing import Any

from simple_ar.core.artifacts import read_json, write_json
from simple_ar.experiment.code_task_bridge.spec import CodeTaskExperimentResult


def write_code_task_experiment_meta(path: Path, result: CodeTaskExperimentResult) -> None:
    """Write a compact stage-level summary for the embedded code-task experiment."""

    usage_summary_path = result.code_task_run_dir / "code_task" / "meta" / "llm_usage_summary.json"
    usage_summary = read_json(usage_summary_path) if usage_summary_path.exists() else {}
    comparison_path = result.code_task_run_dir / "code_task" / "run" / "comparison.json"
    manifest = _read_optional_dict(result.code_task_run_dir / "manifest.json")
    batch_state = _read_optional_dict(result.batch_state_path)
    attempt_state = _read_optional_dict(result.attempt_state_path)
    write_json(
        path,
        {
            "schema_version": 1,
            "template": result.template,
            "code_task_run_dir": str(result.code_task_run_dir),
            "workspace": _relative_or_string(path.parent, result.workspace_dir),
            "repo_map": _optional_relative(path.parent, result.repo_map_path),
            "repo_map_summary": _optional_relative(path.parent, result.repo_map_summary_path),
            "context_pack": {
                "path": _optional_relative(path.parent, result.context_pack_path),
                "prompt_context": _optional_relative(path.parent, result.context_prompt_path),
                "selected_snippets": _optional_relative(path.parent, result.context_snippets_path),
            },
            "work_plan": _optional_relative(path.parent, result.work_plan_path),
            "work_plan_markdown": _optional_relative(path.parent, result.work_plan_markdown_path),
            "work_plan_mode": result.work_plan_mode,
            "work_plan_item_count": result.work_plan_item_count,
            "attempt": {
                "id": result.attempt_id,
                "state": attempt_state.get("state", ""),
                "state_path": _optional_relative(path.parent, result.attempt_state_path),
            },
            "batch": {
                "id": result.batch_id,
                "state": batch_state.get("state", ""),
                "state_path": _optional_relative(path.parent, result.batch_state_path),
                "work_item_id": result.work_item_id,
                "kind": batch_state.get("kind", ""),
                "artifacts": batch_state.get("artifacts", {}),
            },
            "patch_plan": _relative_or_string(path.parent, result.patch_plan_path),
            "proposed_edits": _relative_or_string(path.parent, result.proposed_edits_path),
            "patch_diff": _relative_or_string(path.parent, result.patch_diff_path),
            "validation_report": _relative_or_string(path.parent, result.validation_report_path),
            "environment_report": _optional_relative(path.parent, result.environment_report_path),
            "baseline_report": _optional_relative(path.parent, result.baseline_report_path),
            "summary": _optional_relative(path.parent, result.summary_path),
            "comparison": _optional_relative(path.parent, comparison_path),
            "plan_mode": result.plan_mode,
            "edit_mode": result.edit_mode,
            "edit_count": result.edit_count,
            "changed_files": list(result.changed_files),
            "baseline_status": result.baseline_status,
            "validation_status": result.validation_status,
            "editor_backend": _manifest_editor_backend(manifest),
            "active_attempt": _manifest_active_attempt(manifest),
            "llm_usage_summary": usage_summary,
        },
    )


def build_code_task_experiment_script(
    *,
    changed_files: tuple[str, ...],
    timeout_sec: int,
) -> str:
    """Build a harness script that runs the prepared code-task benchmark."""

    changed_files_literal = repr(list(changed_files))
    return f'''from __future__ import annotations

import json
import sys
from pathlib import Path

from simple_ar.code_task import run_code_task_benchmark


def main() -> int:
    stage_dir = Path(__file__).resolve().parent
    code_task_run = stage_dir / "code_task_run"
    changed_files = {changed_files_literal}
    result = run_code_task_benchmark(code_task_run, timeout_sec={int(timeout_sec)})
    stdout = result.stdout_path.read_text(encoding="utf-8", errors="replace")
    stderr = result.stderr_path.read_text(encoding="utf-8", errors="replace")
    if stdout.strip():
        print("=== patched benchmark stdout ===")
        print(stdout.rstrip())
    if stderr.strip():
        print("=== patched benchmark stderr ===", file=sys.stderr)
        print(stderr.rstrip(), file=sys.stderr)
    print(f"benchmark_passed: {{1.0 if result.status == 'passed' else 0.0}}")
    print(f"benchmark_returncode: {{float(result.returncode) if result.returncode is not None else -1.0}}")
    print(f"benchmark_timed_out: {{1.0 if result.timed_out else 0.0}}")
    print(f"changed_files: {{float(len(changed_files))}}")
    print(f"llm_patch_applied: {{1.0 if changed_files else 0.0}}")
    comparison_path = code_task_run / "code_task" / "run" / "comparison.json"
    if comparison_path.exists():
        comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
        verdict = str(comparison.get("verdict", "inconclusive"))
        print(f"comparison_improved: {{1.0 if verdict == 'improved' else 0.0}}")
        for row in comparison.get("metrics", []):
            if isinstance(row, dict) and row.get("is_primary"):
                delta = row.get("delta")
                if isinstance(delta, (int, float)):
                    print(f"primary_metric_delta: {{float(delta)}}")
                break
    return 0 if result.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _relative_or_string(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _optional_relative(root: Path, path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return _relative_or_string(root, path)


def _read_optional_dict(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    data = read_json(path)
    return data if isinstance(data, dict) else {}


def _manifest_editor_backend(manifest: dict[str, Any]) -> str:
    patch = manifest.get("patch")
    if not isinstance(patch, dict):
        return ""
    backend = patch.get("editor_backend")
    return backend if isinstance(backend, str) else ""


def _manifest_active_attempt(manifest: dict[str, Any]) -> dict[str, Any]:
    attempts = manifest.get("attempts")
    if not isinstance(attempts, dict):
        return {}
    return {
        "active": attempts.get("active", ""),
        "latest_attempt": attempts.get("latest_attempt", ""),
        "latest_batch": attempts.get("latest_batch", ""),
    }

