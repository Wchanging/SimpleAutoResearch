from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from simple_ar.code_task.environment import probe_code_task_environment
from simple_ar.code_task.failure import analyze_code_task_failure
from simple_ar.code_task.patching import PatchValidationError, apply_patch_edits, propose_patch_edits
from simple_ar.code_task.planning import generate_patch_plan
from simple_ar.code_task.repair import propose_repair_edits
from simple_ar.code_task.runner import run_code_task_baseline, run_code_task_benchmark
from simple_ar.code_task.state import code_task_paths, load_code_task_manifest
from simple_ar.code_task.validation import validate_code_task


MessageCallback = Callable[[str], None]

EXECUTE_STEPS = (
    "probe",
    "baseline",
    "plan",
    "propose-edits",
    "apply-edits",
    "validate",
    "run",
    "analyze-failure",
    "repair",
)


@dataclass(frozen=True)
class ExecuteStepRecord:
    """One action considered by the code-task orchestrator.

    Args:
        step: Step name from ``EXECUTE_STEPS``.
        status: ``done``, ``skipped``, ``blocked``, or ``would_run``.
        detail: Short human-readable explanation.
    """

    step: str
    status: str
    detail: str


@dataclass(frozen=True)
class CodeTaskExecuteResult:
    """Result returned by the code-task execute orchestrator.

    Args:
        run_dir: Code-task run directory.
        steps: Ordered step decisions and actions.
        stop_reason: Why execution stopped.
        next_action: Suggested next command or review action.
        summary_path: Human-readable code-task summary path.
    """

    run_dir: Path
    steps: tuple[ExecuteStepRecord, ...]
    stop_reason: str
    next_action: str
    summary_path: Path


def execute_code_task(
    run_dir: Path,
    *,
    to_step: str = "run",
    dry_run: bool = False,
    model: str | None = None,
    use_llm: bool = True,
    timeout_sec: int = 60,
    skip_validation: bool = False,
    env_mode: str | None = None,
    python_executable: str | Path | None = None,
    strict_validation: bool = False,
    validation_max_file_bytes: int = 500_000,
    apply_proposed_edits: bool = False,
    repair_rounds: int = 0,
    max_files: int = 8,
    max_source_chars_per_file: int = 4000,
    message_callback: MessageCallback | None = None,
) -> CodeTaskExecuteResult:
    """Run a conservative state-aware code-task workflow.

    The orchestrator is intentionally thin. It calls the existing primitive
    steps, skips artifacts that are already present, and stops at review gates
    instead of silently applying model proposals.

    Args:
        run_dir: Existing code-task run directory created by ``code-task init``.
        to_step: Last step the orchestrator may attempt.
        dry_run: Preview the next executable action without writing artifacts.
        model: Optional LLM model override for planning/edit/repair steps.
        use_llm: Whether planning/edit/repair steps may call the LLM.
        timeout_sec: Benchmark timeout for baseline and patched runs.
        skip_validation: Allow benchmark execution even when validation fails.
        env_mode: Optional environment mode override for probe and runs.
        python_executable: External interpreter when ``env_mode`` is external.
        strict_validation: Treat risky validation warnings as errors.
        validation_max_file_bytes: Per-file validation scan budget.
        apply_proposed_edits: Allow execute to apply an existing or generated
            proposal after the patch plan has been approved.
        repair_rounds: Maximum repair proposals execute may create after a
            validation or benchmark failure. Proposals are never auto-applied.
        max_files: Context file budget for LLM planning/edit/repair steps.
        max_source_chars_per_file: Source snippet budget per selected file.
        message_callback: Optional progress callback.

    Returns:
        Ordered execute decisions and suggested next action.
    """
    if to_step not in EXECUTE_STEPS:
        raise ValueError("to_step must be one of: " + ", ".join(EXECUTE_STEPS))
    if timeout_sec < 1:
        raise ValueError("timeout_sec must be at least 1")
    if repair_rounds < 0:
        raise ValueError("repair_rounds must be non-negative")

    root = Path(run_dir)
    paths = code_task_paths(root)
    load_code_task_manifest(root)
    steps: list[ExecuteStepRecord] = []

    if _should_run("probe", to_step):
        manifest = load_code_task_manifest(root)
        if _environment_report_exists(paths):
            _record(steps, "probe", "skipped", "environment_report.json already exists")
        elif dry_run:
            return _dry_result(paths, steps, "probe", "record environment signals")
        else:
            _emit(message_callback, "Running environment probe.")
            result = probe_code_task_environment(
                root,
                env_mode=env_mode,
                python_executable=python_executable,
            )
            _record(steps, "probe", "done", f"wrote {result.report_path}")
    if _stop_after("probe", to_step):
        return _result(paths, steps, "stop_point", "Stopped after probe as requested.")

    if _should_run("baseline", to_step):
        manifest = load_code_task_manifest(root)
        baseline = _run_record(manifest, "baseline")
        if baseline:
            _record(steps, "baseline", "skipped", f"baseline status is {baseline.get('status', 'unknown')}")
        elif dry_run:
            return _dry_result(paths, steps, "baseline", "run unchanged benchmark")
        else:
            _emit(message_callback, "Running baseline benchmark.")
            result = run_code_task_baseline(
                root,
                timeout_sec=timeout_sec,
                skip_validation=skip_validation,
                env_mode=env_mode,
                python_executable=python_executable,
            )
            _record(steps, "baseline", "done", f"status {result.status}")
            if result.status != "passed":
                return _result(
                    paths,
                    steps,
                    "baseline_failed",
                    "Review code_task/run/baseline/ before asking for edits.",
                )
        baseline = _run_record(load_code_task_manifest(root), "baseline")
        if baseline and baseline.get("status") != "passed":
            return _result(
                paths,
                steps,
                "baseline_failed",
                "Review code_task/run/baseline/ before asking for edits.",
            )
    if _stop_after("baseline", to_step):
        return _result(paths, steps, "stop_point", "Stopped after baseline as requested.")

    if _should_run("plan", to_step):
        manifest = load_code_task_manifest(root)
        plan_status = _plan_status(manifest)
        if plan_status in {"approved", "rejected", "revision_requested", "pending_approval"}:
            _record(steps, "plan", "skipped", f"plan status is {plan_status}")
        elif dry_run:
            return _dry_result(paths, steps, "plan", "generate patch_plan.md")
        else:
            _emit(message_callback, "Generating patch plan.")
            result = generate_patch_plan(
                root,
                model=model,
                use_llm=use_llm,
                max_files=max_files,
                max_source_chars_per_file=max_source_chars_per_file,
                message_callback=message_callback,
            )
            _record(steps, "plan", "done", f"mode {result.mode}; pending approval")
            plan_status = "pending_approval"
        if plan_status == "pending_approval":
            return _result(
                paths,
                steps,
                "approval_required",
                "Review code_task/patch_plan.md, then run code-task decide-plan --decision approve.",
            )
        if plan_status == "revision_requested":
            return _result(paths, steps, "plan_revision_requested", "Revise the task or regenerate the plan.")
        if plan_status == "rejected":
            return _result(paths, steps, "plan_rejected", "Stop this run or create a new plan.")

    if _stop_after("plan", to_step):
        return _result(paths, steps, "stop_point", "Stopped after plan as requested.")

    if _should_run("propose-edits", to_step):
        manifest = load_code_task_manifest(root)
        patch_status = _patch_status(manifest)
        proposal_exists = _proposal_exists(paths)
        if patch_status == "applied":
            _record(steps, "propose-edits", "skipped", "patch already applied")
        elif proposal_exists:
            _record(steps, "propose-edits", "skipped", "proposed_edits.json already exists")
        elif _plan_status(manifest) != "approved":
            return _result(paths, steps, "approval_required", "Approve the patch plan before proposing edits.")
        elif dry_run:
            return _dry_result(paths, steps, "propose-edits", "generate controlled edit proposal")
        else:
            _emit(message_callback, "Generating controlled edit proposal.")
            result = propose_patch_edits(
                root,
                model=model,
                use_llm=use_llm,
                max_files=max_files,
                max_source_chars_per_file=max_source_chars_per_file,
                message_callback=message_callback,
            )
            _record(steps, "propose-edits", "done", f"mode {result.mode}; edits {result.edit_count}")
            if result.edit_count == 0:
                return _result(
                    paths,
                    steps,
                    "no_edits_proposed",
                    "Review code_task/meta/proposed_edits.json or rerun with LLM enabled.",
                )
            return _result(
                paths,
                steps,
                "proposal_review_required",
                "Review code_task/meta/proposed_edits.json, then rerun execute with --apply-proposed-edits.",
            )

    if _stop_after("propose-edits", to_step):
        return _result(paths, steps, "stop_point", "Stopped after propose-edits as requested.")

    if _should_run("apply-edits", to_step):
        manifest = load_code_task_manifest(root)
        if _patch_status(manifest) == "applied":
            _record(steps, "apply-edits", "skipped", "patch already applied")
        elif not _proposal_exists(paths):
            return _result(paths, steps, "missing_proposal", "Generate or provide proposed_edits.json first.")
        elif not apply_proposed_edits:
            return _result(
                paths,
                steps,
                "proposal_review_required",
                "Review code_task/meta/proposed_edits.json, then rerun execute with --apply-proposed-edits.",
            )
        elif dry_run:
            return _dry_result(paths, steps, "apply-edits", "apply reviewed proposed edits")
        else:
            _emit(message_callback, "Applying reviewed edit proposal.")
            try:
                result = apply_patch_edits(root)
            except PatchValidationError as exc:
                _record(steps, "apply-edits", "blocked", _first_error_line(str(exc)))
                return _result(
                    paths,
                    steps,
                    "patch_apply_failed",
                    "Review code_task/meta/proposed_edits.json; patch validation failed before workspace files were changed.",
                )
            _record(steps, "apply-edits", "done", f"changed {len(result.changed_files)} file(s)")

    if _stop_after("apply-edits", to_step):
        return _result(paths, steps, "stop_point", "Stopped after apply-edits as requested.")

    if _should_run("validate", to_step):
        if _patch_status(load_code_task_manifest(root)) != "applied":
            return _result(paths, steps, "patch_not_applied", "Apply reviewed edits before validation.")
        if dry_run:
            return _dry_result(paths, steps, "validate", "run static validation")
        _emit(message_callback, "Running static validation.")
        validation = validate_code_task(
            root,
            strict=strict_validation,
            max_file_bytes=validation_max_file_bytes,
        )
        _record(steps, "validate", "done", f"status {validation.status}")
        if validation.status == "failed":
            if not _should_run("analyze-failure", to_step):
                return _result(
                    paths,
                    steps,
                    "validation_failed",
                    "Review code_task/meta/validation_report.json or run execute --to-step analyze-failure.",
                )
            return _handle_failure(
                root,
                paths,
                steps,
                dry_run=dry_run,
                allow_repair=_should_run("repair", to_step),
                repair_rounds=repair_rounds,
                model=model,
                use_llm=use_llm,
                max_files=max_files,
                max_source_chars_per_file=max_source_chars_per_file,
                message_callback=message_callback,
            )

    if _stop_after("validate", to_step):
        return _result(paths, steps, "stop_point", "Stopped after validate as requested.")

    if _should_run("run", to_step):
        if _patch_status(load_code_task_manifest(root)) != "applied":
            return _result(paths, steps, "patch_not_applied", "Apply reviewed edits before running benchmark.")
        if dry_run:
            return _dry_result(paths, steps, "run", "run patched benchmark")
        _emit(message_callback, "Running patched benchmark.")
        result = run_code_task_benchmark(
            root,
            timeout_sec=timeout_sec,
            skip_validation=skip_validation,
            env_mode=env_mode,
            python_executable=python_executable,
        )
        _record(steps, "run", "done", f"status {result.status}")
        if result.status != "passed":
            if not _should_run("analyze-failure", to_step):
                return _result(
                    paths,
                    steps,
                    "benchmark_failed",
                    "Review code_task/run/patched/ or run execute --to-step analyze-failure.",
                )
            return _handle_failure(
                root,
                paths,
                steps,
                dry_run=dry_run,
                allow_repair=_should_run("repair", to_step),
                repair_rounds=repair_rounds,
                model=model,
                use_llm=use_llm,
                max_files=max_files,
                max_source_chars_per_file=max_source_chars_per_file,
                message_callback=message_callback,
            )

    if _stop_after("run", to_step):
        return _result(paths, steps, "completed", "Review code_task/summary.md and patch.diff.")

    return _result(paths, steps, "completed", "Review code_task/summary.md.")


def _handle_failure(
    run_dir: Path,
    paths: object,
    steps: list[ExecuteStepRecord],
    *,
    dry_run: bool,
    allow_repair: bool,
    repair_rounds: int,
    model: str | None,
    use_llm: bool,
    max_files: int,
    max_source_chars_per_file: int,
    message_callback: MessageCallback | None,
) -> CodeTaskExecuteResult:
    if dry_run:
        return _dry_result(paths, steps, "analyze-failure", "summarize latest failure")
    _emit(message_callback, "Analyzing latest failure.")
    analysis = analyze_code_task_failure(run_dir)
    _record(steps, "analyze-failure", "done", f"source {analysis.source}; status {analysis.status}")
    if not allow_repair or repair_rounds <= _repair_count(load_code_task_manifest(run_dir)):
        return _result(
            paths,
            steps,
            "failure_analyzed",
            "Review failure_analysis.md. Use --to-step repair and --repair-rounds to request a bounded repair proposal.",
        )
    _emit(message_callback, "Generating bounded repair proposal.")
    repair = propose_repair_edits(
        run_dir,
        model=model,
        use_llm=use_llm,
        max_files=max_files,
        max_source_chars_per_file=max_source_chars_per_file,
        message_callback=message_callback,
    )
    _record(steps, "repair", "done", f"mode {repair.mode}; edits {repair.edit_count}")
    return _result(
        paths,
        steps,
        "repair_review_required",
        "Review the repair proposal, then apply it explicitly with code-task apply-edits --edits-file.",
    )


def _record(steps: list[ExecuteStepRecord], step: str, status: str, detail: str) -> None:
    steps.append(ExecuteStepRecord(step=step, status=status, detail=detail))


def _dry_result(paths: object, steps: list[ExecuteStepRecord], step: str, detail: str) -> CodeTaskExecuteResult:
    _record(steps, step, "would_run", detail)
    return _result(paths, steps, "dry_run", "Dry run only; no artifacts were written.")


def _result(
    paths: object,
    steps: list[ExecuteStepRecord],
    stop_reason: str,
    next_action: str,
) -> CodeTaskExecuteResult:
    return CodeTaskExecuteResult(
        run_dir=paths.run_dir,
        steps=tuple(steps),
        stop_reason=stop_reason,
        next_action=next_action,
        summary_path=paths.task_dir / "summary.md",
    )


def _should_run(step: str, to_step: str) -> bool:
    return EXECUTE_STEPS.index(step) <= EXECUTE_STEPS.index(to_step)


def _stop_after(step: str, to_step: str) -> bool:
    return EXECUTE_STEPS.index(step) >= EXECUTE_STEPS.index(to_step)


def _environment_report_exists(paths: object) -> bool:
    return (paths.meta_dir / "environment_report.json").is_file()


def _proposal_exists(paths: object) -> bool:
    return (paths.meta_dir / "proposed_edits.json").is_file()


def _run_record(manifest: dict[str, object], label: str) -> dict[str, object]:
    benchmark = manifest.get("benchmark", {})
    if not isinstance(benchmark, dict):
        return {}
    runs = benchmark.get("runs", {})
    if not isinstance(runs, dict):
        return {}
    record = runs.get(label, {})
    return record if isinstance(record, dict) else {}


def _plan_status(manifest: dict[str, object]) -> str:
    plan = manifest.get("plan", {})
    if not isinstance(plan, dict):
        return "not_started"
    return str(plan.get("status") or "not_started")


def _patch_status(manifest: dict[str, object]) -> str:
    patch = manifest.get("patch", {})
    if not isinstance(patch, dict):
        return "not_started"
    return str(patch.get("status") or "not_started")


def _repair_count(manifest: dict[str, object]) -> int:
    repair = manifest.get("repair", {})
    if not isinstance(repair, dict):
        return 0
    try:
        return int(repair.get("repair_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _emit(callback: MessageCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _first_error_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and stripped != "Patch validation failed:":
            return stripped.removeprefix("- ").strip()
    return "patch validation failed"
