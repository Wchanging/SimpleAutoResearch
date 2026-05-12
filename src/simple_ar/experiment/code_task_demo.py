from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from simple_ar.artifacts import read_json, write_json
from simple_ar.code_task import (
    apply_patch_edits,
    generate_patch_plan,
    initialize_code_task,
    propose_patch_edits,
    record_plan_decision,
    validate_code_task,
)


CODE_TASK_TOY_SPAM_TEMPLATE = "llm_code_task_toy_spam"
CODE_TASK_TOY_SPAM_BENCHMARK = "python -m unittest discover -s tests"
MessageCallback = Callable[[str], None]


@dataclass(frozen=True)
class CodeTaskDemoSpec:
    """Static source configuration for the embedded code-task demo."""

    code_root: Path
    task_file: Path
    benchmark_command: str


@dataclass(frozen=True)
class CodeTaskExperimentResult:
    """Result returned after preparing an embedded code-task experiment.

    Args:
        code_task_run_dir: Nested code-task run directory.
        workspace_dir: Copied workspace where edits were applied.
        patch_plan_path: Human-reviewable plan produced by the model.
        proposed_edits_path: Model-generated controlled edit proposal.
        patch_diff_path: Unified diff for the applied patch.
        validation_report_path: Static validation report for the patched code.
        plan_mode: ``llm`` when the model generated the patch plan.
        edit_mode: ``llm`` when the model generated the edit proposal.
        edit_count: Number of edit rows in the proposal.
        changed_files: Workspace-relative files changed by the patch.
        validation_status: Validation status after applying edits.
    """

    code_task_run_dir: Path
    workspace_dir: Path
    patch_plan_path: Path
    proposed_edits_path: Path
    patch_diff_path: Path
    validation_report_path: Path
    plan_mode: str
    edit_mode: str
    edit_count: int
    changed_files: tuple[str, ...]
    validation_status: str


def is_code_task_demo_template(template: object) -> bool:
    """Return whether an experiment template requests the embedded code-task demo."""
    return str(template) == CODE_TASK_TOY_SPAM_TEMPLATE


def code_task_demo_spec(repo_root: Path) -> CodeTaskDemoSpec:
    """Resolve example files used by the embedded code-task experiment."""
    root = Path(repo_root)
    return CodeTaskDemoSpec(
        code_root=root / "examples" / "code_tasks" / "toy_spam_project",
        task_file=root / "examples" / "code_tasks" / "tasks" / "improve_toy_spam_baseline.md",
        benchmark_command=CODE_TASK_TOY_SPAM_BENCHMARK,
    )


def prepare_code_task_demo_experiment(
    *,
    code_task_run_dir: Path,
    repo_root: Path,
    model: str | None,
    use_llm: bool,
    message_callback: MessageCallback | None = None,
) -> CodeTaskExperimentResult:
    """Prepare a real LLM code-editing experiment inside an 8-stage run.

    Args:
        code_task_run_dir: Nested run directory under the code stage.
        repo_root: Project root containing the example code-task source files.
        model: Optional model override for LLM calls.
        use_llm: Whether the outer pipeline has LLM calls enabled.
        message_callback: Optional progress callback.

    Returns:
        Metadata for the prepared code-task experiment.

    Raises:
        FileExistsError: If the nested code-task run directory already exists.
        RuntimeError: If LLM mode is disabled or the model falls back to offline
            planning/edit proposal. This template exists specifically to test
            real model-assisted code modification.
    """
    if not use_llm:
        raise RuntimeError(
            f"`{CODE_TASK_TOY_SPAM_TEMPLATE}` requires LLM mode. "
            "Remove --no-llm or choose the default toy_text_classification template."
        )
    run_dir = Path(code_task_run_dir)
    if run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError(
            f"Embedded code-task run already exists: {run_dir}. "
            "Resume from the run stage or start a fresh outer run."
        )

    spec = code_task_demo_spec(repo_root)
    _emit(message_callback, "Initializing embedded code-task workspace.")
    init = initialize_code_task(
        run_dir=run_dir,
        code_root=spec.code_root,
        task_file=spec.task_file,
        benchmark_command=spec.benchmark_command,
    )

    _emit(message_callback, "Calling LLM for embedded code-task patch plan.")
    plan = generate_patch_plan(
        run_dir,
        model=model,
        use_llm=True,
        message_callback=message_callback,
    )
    if plan.mode != "llm":
        raise RuntimeError(
            "Embedded code-task patch planning did not use the LLM. "
            "Check SIMPLE_AR_API_KEY, SIMPLE_AR_BASE_URL, and SIMPLE_AR_MODEL."
        )

    record_plan_decision(
        run_dir,
        decision="approve",
        note="Auto-approved inside isolated 8-stage demo workspace.",
        reviewer="pipeline-demo",
    )

    _emit(message_callback, "Calling LLM for embedded code-task edit proposal.")
    proposal = propose_patch_edits(
        run_dir,
        model=model,
        use_llm=True,
        message_callback=message_callback,
    )
    if proposal.mode != "llm" or proposal.edit_count == 0:
        raise RuntimeError(
            "Embedded code-task edit proposal was empty or did not use the LLM. "
            "Inspect code_task_run/code_task/meta/proposed_edits.json if present."
        )

    _emit(message_callback, "Applying embedded code-task edits to copied workspace.")
    patch = apply_patch_edits(run_dir)
    if any(path.startswith("tests/") for path in patch.changed_files):
        raise RuntimeError(
            "Embedded code-task demo rejected a patch that modified tests. "
            "The demo must improve source behavior without changing benchmark expectations."
        )
    validation = validate_code_task(run_dir)
    if validation.status == "failed":
        raise RuntimeError(
            "Embedded code-task validation failed after applying edits. "
            f"See {validation.report_path}."
        )

    return CodeTaskExperimentResult(
        code_task_run_dir=run_dir,
        workspace_dir=init.workspace_dir,
        patch_plan_path=plan.patch_plan_path,
        proposed_edits_path=proposal.proposal_path,
        patch_diff_path=patch.patch_diff_path,
        validation_report_path=validation.report_path,
        plan_mode=plan.mode,
        edit_mode=proposal.mode,
        edit_count=proposal.edit_count,
        changed_files=patch.changed_files,
        validation_status=validation.status,
    )


def write_code_task_experiment_meta(path: Path, result: CodeTaskExperimentResult) -> None:
    """Write a compact stage-level summary for the embedded code-task experiment."""
    usage_summary_path = result.code_task_run_dir / "code_task" / "meta" / "llm_usage_summary.json"
    usage_summary = read_json(usage_summary_path) if usage_summary_path.exists() else {}
    write_json(
        path,
        {
            "schema_version": 1,
            "template": CODE_TASK_TOY_SPAM_TEMPLATE,
            "code_task_run_dir": str(result.code_task_run_dir),
            "workspace": _relative_or_string(path.parent, result.workspace_dir),
            "patch_plan": _relative_or_string(path.parent, result.patch_plan_path),
            "proposed_edits": _relative_or_string(path.parent, result.proposed_edits_path),
            "patch_diff": _relative_or_string(path.parent, result.patch_diff_path),
            "validation_report": _relative_or_string(path.parent, result.validation_report_path),
            "plan_mode": result.plan_mode,
            "edit_mode": result.edit_mode,
            "edit_count": result.edit_count,
            "changed_files": list(result.changed_files),
            "validation_status": result.validation_status,
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
        print("=== benchmark stdout ===")
        print(stdout.rstrip())
    if stderr.strip():
        print("=== benchmark stderr ===", file=sys.stderr)
        print(stderr.rstrip(), file=sys.stderr)
    print(f"benchmark_passed: {{1.0 if result.status == 'passed' else 0.0}}")
    print(f"benchmark_returncode: {{float(result.returncode) if result.returncode is not None else -1.0}}")
    print(f"benchmark_timed_out: {{1.0 if result.timed_out else 0.0}}")
    print(f"changed_files: {{float(len(changed_files))}}")
    print(f"llm_patch_applied: {{1.0 if changed_files else 0.0}}")
    return 0 if result.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _relative_or_string(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _emit(callback: MessageCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)
