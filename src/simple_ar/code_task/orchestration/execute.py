from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from simple_ar.artifacts import read_json
from simple_ar.code_task.editing.attempts import (
    LoadedCodeTaskBatch,
    create_code_task_batch,
    load_latest_code_task_batch,
    update_code_task_batch_state,
)
from simple_ar.code_task.execution.environment import probe_code_task_environment
from simple_ar.code_task.execution.failure import analyze_code_task_failure
from simple_ar.code_task.editing.patching import PatchValidationError, apply_patch_edits, propose_patch_edits
from simple_ar.code_task.editing.planning import generate_patch_plan
from simple_ar.code_task.execution.repair import propose_repair_edits
from simple_ar.code_task.execution.runner import run_code_task_baseline, run_code_task_benchmark
from simple_ar.code_task.runtime.state import code_task_paths, load_code_task_manifest
from simple_ar.code_task.execution.validation import validate_code_task
from simple_ar.code_task.editing.work_plan import generate_code_task_work_plan


MessageCallback = Callable[[str], None]

EXECUTE_STEPS = (
    "probe",
    "baseline",
    "work-plan",
    "batch",
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
    planner_model: str | None = None,
    editor_model: str | None = None,
    repair_model: str | None = None,
    use_llm: bool = True,
    timeout_sec: int = 60,
    skip_validation: bool = False,
    env_mode: str | None = None,
    python_executable: str | Path | None = None,
    strict_validation: bool = False,
    validation_max_file_bytes: int = 500_000,
    stream_benchmark_output: bool | str = False,
    apply_proposed_edits: bool = False,
    allow_large_edits: bool = False,
    repair_rounds: int = 0,
    budget_profile: str | None = None,
    edit_budget_overrides: dict[str, Any] | None = None,
    max_batches: int | None = None,
    cost_cap_usd: float | None = None,
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
        model: Optional fallback LLM model override for planning/edit/repair
            steps.
        planner_model: Optional model override for work-plan and patch-plan
            steps.
        editor_model: Optional model override for controlled edit proposals.
        repair_model: Optional model override for repair proposals.
        use_llm: Whether planning/edit/repair steps may call the LLM.
        timeout_sec: Benchmark timeout for baseline and patched runs.
        skip_validation: Allow benchmark execution even when validation fails.
        env_mode: Optional environment mode override for probe and runs.
        python_executable: External interpreter when ``env_mode`` is external.
        strict_validation: Treat risky validation warnings as errors.
        validation_max_file_bytes: Per-file validation scan budget.
        stream_benchmark_output: Relay benchmark stdout/stderr while baseline
            or patched runs are executing. ``True`` uses ``auto`` mode, which
            understands carriage-return progress output such as tqdm.
        apply_proposed_edits: Allow execute to apply an existing or generated
            proposal after the patch plan has been approved.
        allow_large_edits: Allow proposals that exceed the normal edit budget
            but fit the large profile.
        repair_rounds: Maximum repair proposals execute may create after a
            validation or benchmark failure. Proposals are never auto-applied.
        budget_profile: Optional edit budget profile passed to edit proposal
            normalization.
        edit_budget_overrides: Optional numeric budget overrides from config.
        max_batches: Optional hard cap on attempt/batch creation.
        cost_cap_usd: Optional LLM cost cap. When no cost estimate is
            available from the provider, this guard is informational only.
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
    if max_batches is not None and max_batches < 1:
        raise ValueError("max_batches must be at least 1 when provided")
    if cost_cap_usd is not None and cost_cap_usd < 0:
        raise ValueError("cost_cap_usd must be non-negative when provided")

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
                stream_output=stream_benchmark_output,
                output_callback=_benchmark_output_callback(message_callback),
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

    if _should_run("work-plan", to_step):
        if _work_plan_exists(paths):
            _record(steps, "work-plan", "skipped", "work_plan.json already exists")
        elif dry_run:
            return _dry_result(paths, steps, "work-plan", "generate batch-oriented work plan")
        elif _cost_cap_exceeded(paths.meta_dir, cost_cap_usd):
            return _result(paths, steps, "cost_cap_exceeded", "LLM cost cap reached before work planning.")
        else:
            _emit(message_callback, "Generating batch-oriented work plan.")
            result = generate_code_task_work_plan(
                root,
                model=planner_model or model,
                use_llm=use_llm,
                max_files=max_files,
                max_source_chars_per_file=max_source_chars_per_file,
                message_callback=message_callback,
            )
            _record(steps, "work-plan", "done", f"mode {result.mode}; items {result.item_count}")
    if _stop_after("work-plan", to_step):
        return _result(paths, steps, "stop_point", "Stopped after work-plan as requested.")

    if _should_run("batch", to_step):
        manifest = load_code_task_manifest(root)
        latest_batch = _latest_batch(manifest)
        if latest_batch:
            _record(steps, "batch", "skipped", f"latest batch is {latest_batch}")
        elif dry_run:
            return _dry_result(paths, steps, "batch", "create attempt/batch state for first work item")
        elif _batch_cap_exceeded(root, max_batches):
            return _result(paths, steps, "batch_budget_exceeded", "Configured batch cap was reached.")
        else:
            work_item = _first_work_item_id(paths)
            _emit(message_callback, f"Creating batch state for {work_item}.")
            result = create_code_task_batch(root, work_item_id=work_item)
            _record(steps, "batch", "done", f"{result.attempt_id}/{result.batch_id} for {result.work_item_id}")
    if _stop_after("batch", to_step):
        return _result(paths, steps, "stop_point", "Stopped after batch as requested.")

    if _should_run("plan", to_step):
        manifest = load_code_task_manifest(root)
        plan_status = _plan_status(manifest)
        if plan_status in {"approved", "rejected", "revision_requested", "pending_approval"}:
            _record(steps, "plan", "skipped", f"plan status is {plan_status}")
        elif dry_run:
            return _dry_result(paths, steps, "plan", "generate patch_plan.md")
        elif _cost_cap_exceeded(paths.meta_dir, cost_cap_usd):
            return _result(paths, steps, "cost_cap_exceeded", "LLM cost cap reached before patch planning.")
        else:
            _emit(message_callback, "Generating patch plan.")
            result = generate_patch_plan(
                root,
                model=planner_model or model,
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
        elif _cost_cap_exceeded(paths.meta_dir, cost_cap_usd):
            return _result(paths, steps, "cost_cap_exceeded", "LLM cost cap reached before edit proposal.")
        else:
            _emit(message_callback, "Generating controlled edit proposal.")
            result = propose_patch_edits(
                root,
                model=editor_model or model,
                use_llm=use_llm,
                max_files=max_files,
                max_source_chars_per_file=max_source_chars_per_file,
                allow_large_edits=allow_large_edits,
                budget_profile=budget_profile,
                edit_budget_overrides=edit_budget_overrides,
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
                result = apply_patch_edits(root, allow_large_edits=allow_large_edits)
            except PatchValidationError as exc:
                _record(steps, "apply-edits", "blocked", _first_error_line(str(exc)))
                return _result(
                    paths,
                    steps,
                    "patch_apply_failed",
                    "Review code_task/meta/proposed_edits.json; patch validation failed before workspace files were changed.",
                )
            except PermissionError as exc:
                _record(steps, "apply-edits", "blocked", _first_error_line(str(exc)))
                return _result(
                    paths,
                    steps,
                    "large_edit_approval_required",
                    "Review the larger proposal, then rerun with --allow-large-edits if it is intentional.",
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
                max_batches=max_batches,
                cost_cap_usd=cost_cap_usd,
                model=model,
                repair_model=repair_model,
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
            # The execute path runs static validation immediately before this
            # benchmark step, so avoid recording the same validation twice.
            skip_validation=True,
            env_mode=env_mode,
            python_executable=python_executable,
            stream_output=stream_benchmark_output,
            output_callback=_benchmark_output_callback(message_callback),
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
                max_batches=max_batches,
                cost_cap_usd=cost_cap_usd,
                model=model,
                repair_model=repair_model,
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
    max_batches: int | None,
    cost_cap_usd: float | None,
    model: str | None,
    repair_model: str | None,
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
    if _batch_cap_exceeded(run_dir, max_batches):
        return _result(
            paths,
            steps,
            "batch_budget_exceeded",
            "Configured batch cap was reached before creating a repair batch.",
        )
    if _cost_cap_exceeded(paths.meta_dir, cost_cap_usd):
        return _result(
            paths,
            steps,
            "cost_cap_exceeded",
            "LLM cost cap reached before repair proposal.",
        )
    repair_batch = _create_repair_batch(run_dir)
    _emit(message_callback, "Generating bounded repair proposal.")
    repair = propose_repair_edits(
        run_dir,
        model=repair_model or model,
        use_llm=use_llm,
        max_files=max_files,
        max_source_chars_per_file=max_source_chars_per_file,
        message_callback=message_callback,
    )
    if repair_batch is not None:
        update_code_task_batch_state(
            run_dir,
            repair_batch.batch_state_path,
            state="proposal_ready" if repair.edit_count else "failed",
            artifacts={"repair_proposal": _relative_to_run(run_dir, repair.proposal_path)},
            detail="Repair proposal generated for failed validation or benchmark evidence.",
            extra={"repair_edit_count": repair.edit_count},
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


def _work_plan_exists(paths: object) -> bool:
    return (paths.task_dir / "work_plan.json").is_file()


def _batch_cap_exceeded(run_dir: Path, max_batches: int | None) -> bool:
    if max_batches is None:
        return False
    return _batch_count(run_dir) >= max_batches


def _batch_count(run_dir: Path) -> int:
    root = Path(run_dir) / "code_task" / "attempts"
    if not root.is_dir():
        return 0
    return sum(1 for path in root.glob("attempt-*/batches/batch-*/batch_state.json") if path.is_file())


def _cost_cap_exceeded(meta_dir: Path, cost_cap_usd: float | None) -> bool:
    if cost_cap_usd is None:
        return False
    summary_path = meta_dir / "llm_usage_summary.json"
    if not summary_path.is_file():
        return False
    summary = read_json(summary_path)
    if not isinstance(summary, dict):
        return False
    cost = summary.get("estimated_cost_usd")
    return isinstance(cost, (int, float)) and float(cost) >= cost_cap_usd


def _create_repair_batch(run_dir: Path) -> LoadedCodeTaskBatch | None:
    parent = load_latest_code_task_batch(run_dir)
    if parent is None:
        return None
    work_item_id = str(parent.state.get("work_item_id") or "").strip()
    if not work_item_id:
        return None
    result = create_code_task_batch(
        run_dir,
        work_item_id=work_item_id,
        attempt_id=parent.attempt_id,
        kind="repair",
        parent_batch_id=parent.batch_id,
        force=True,
    )
    return load_latest_code_task_batch(result.run_dir)


def _latest_batch(manifest: dict[str, object]) -> str:
    attempts = manifest.get("attempts", {})
    if not isinstance(attempts, dict):
        return ""
    value = attempts.get("latest_batch")
    return value if isinstance(value, str) else ""


def _first_work_item_id(paths: object) -> str:
    work_plan_path = paths.task_dir / "work_plan.json"
    if not work_plan_path.is_file():
        raise FileNotFoundError(f"Missing work plan: {work_plan_path}")
    work_plan = read_json(work_plan_path)
    if not isinstance(work_plan, dict):
        raise RuntimeError(f"Expected JSON object in {work_plan_path}")
    items = [item for item in work_plan.get("items", []) if isinstance(item, dict)]
    for item in items:
        if _is_executable_work_item(item):
            item_id = str(item.get("id") or "").strip()
            if item_id:
                return item_id
    for item in items:
        item_id = str(item.get("id") or "").strip()
        if item_id:
            return item_id
    raise RuntimeError(f"Work plan has no executable items: {work_plan_path}")


def _is_executable_work_item(item: dict[str, object]) -> bool:
    objective = str(item.get("objective") or "").lower()
    done = " ".join(str(value).lower() for value in item.get("done_criteria", []) if isinstance(value, str))
    text = objective + "\n" + done
    target_files = item.get("target_files")
    if not isinstance(target_files, list) or not any(isinstance(path, str) and path for path in target_files):
        return False
    edit_patterns = (
        r"\badd(?:s|ed|ing)?\b",
        r"\bchang(?:e|es|ed|ing)\b",
        r"\bfix(?:es|ed|ing)?\b",
        r"\bimplement(?:s|ed|ing)?\b",
        r"\bimprov(?:e|es|ed|ing)\b",
        r"\bmodif(?:y|ies|ied|ying)\b",
        r"\boptimi[sz](?:e|es|ed|ing)\b",
        r"\brefactor(?:s|ed|ing)?\b",
        r"\brepair(?:s|ed|ing)?\b",
        r"\btun(?:e|es|ed|ing)\b",
        r"\bupdat(?:e|es|ed|ing)\b",
    )
    analysis_patterns = (
        r"\banaly[sz](?:e|es|ed|ing)\b",
        r"\bcaptur(?:e|es|ed|ing)\b",
        r"\bdocument(?:s|ed|ing)?\b",
        r"\bfinali[sz](?:e|es|ed|ing)\b",
        r"\bidentif(?:y|ies|ied|ying)\b",
        r"\binspect(?:s|ed|ing)?\b",
        r"\bmeasur(?:e|es|ed|ing)\b",
        r"\breview(?:s|ed|ing)?\b",
        r"\bunderstand(?:s|ing)?\b",
    )
    if any(re.search(pattern, text) for pattern in edit_patterns):
        return True
    return not any(re.search(pattern, objective) for pattern in analysis_patterns)


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


def _benchmark_output_callback(callback: MessageCallback | None) -> Callable[[str, str], None] | None:
    if callback is None:
        return None

    def _relay(stream: str, line: str) -> None:
        text = line.rstrip()
        if text:
            callback(f"benchmark {stream}: {text}")

    return _relay


def _first_error_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and stripped != "Patch validation failed:":
            return stripped.removeprefix("- ").strip()
    return "patch validation failed"


def _relative_to_run(run_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        return str(path)
