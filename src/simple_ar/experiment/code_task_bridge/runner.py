from __future__ import annotations

from pathlib import Path
from typing import Any

from simple_ar.code_task import (
    analyze_code_task_failure,
    apply_patch_edits,
    build_code_task_context_pack,
    create_code_task_batch,
    generate_code_task_work_plan,
    generate_patch_plan,
    initialize_code_task,
    probe_code_task_environment,
    propose_patch_edits,
    propose_repair_edits,
    record_provided_code_task_baseline,
    record_plan_decision,
    review_code_task_changes,
    run_code_task_baseline,
    run_code_task_benchmark,
    validate_code_task,
)
from simple_ar.code_task.editing.scope import is_protected_edit_path
from simple_ar.code_task.execution.baseline_policy import (
    load_provided_baseline_metrics,
    normalize_baseline_policy,
)
from simple_ar.code_task.runtime.state import (
    load_code_task_manifest,
    manifest_section,
    save_code_task_manifest,
    utcnow_iso,
)
from simple_ar.core.artifacts import read_json
from simple_ar.experiment.code_task_bridge.spec import (
    CodeTaskExperimentResult,
    CodeTaskExperimentSpec,
    MessageCallback,
)


def prepare_code_task_experiment(
    *,
    code_task_run_dir: Path,
    spec: CodeTaskExperimentSpec,
    model: str | None,
    use_llm: bool,
    timeout_sec: int,
    baseline_policy: str = "auto",
    baseline_metrics_file: str | Path | None = None,
    message_callback: MessageCallback | None = None,
) -> CodeTaskExperimentResult:
    """Prepare an LLM-assisted code-task experiment inside an 8-stage run."""

    if not use_llm:
        raise RuntimeError(
            f"`{spec.template}` requires LLM mode. Remove --no-llm or choose a "
            "non-code-task experiment template."
        )
    if not spec.benchmark_command:
        raise RuntimeError(
            f"`{spec.template}` requires a benchmark command. Provide "
            "--benchmark-command or set [benchmark].command in --code-task-config."
        )
    if spec.task_file is None:
        raise RuntimeError(
            f"`{spec.template}` needs a task file before code generation. Provide "
            "[code_task].task_file, pass --task-file, or run the design stage so "
            "SimpleAutoResearch can generate one from the research artifacts."
        )
    run_dir = Path(code_task_run_dir)
    if run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError(
            f"Code-task experiment run already exists: {run_dir}. "
            "Resume from the run stage or start a fresh outer run."
        )

    _emit(message_callback, "Initializing isolated code-task workspace.")
    init = initialize_code_task(
        run_dir=run_dir,
        code_root=spec.code_root,
        task_file=spec.task_file,
        benchmark_command=spec.benchmark_command,
        max_file_bytes=spec.max_file_bytes,
        workspace_mode=spec.workspace_mode,
        workspace_include=spec.workspace_include,
        workspace_exclude=spec.workspace_exclude,
        workspace_reuse_source_venv=spec.workspace_reuse_source_venv,
        workspace_setup_hook=spec.workspace_setup_hook,
        env_mode=spec.env_mode,
        python_executable=spec.python_executable,
        edit_scope_mode=spec.edit_scope_mode,
        edit_scope_allowed_patterns=spec.edit_scope_allowed_patterns,
        edit_scope_protected_patterns=spec.edit_scope_protected_patterns,
        primary_metric=spec.primary_metric,
        metric_directions=spec.metric_directions,
    )

    _emit(message_callback, "Probing code-task execution environment.")
    environment = probe_code_task_environment(
        run_dir,
        env_mode=spec.env_mode,
        python_executable=spec.python_executable,
    )

    baseline_policy = normalize_baseline_policy(baseline_policy)
    baseline_status = "skipped" if baseline_policy in {"skip", "none"} else ""
    baseline_report_path: Path | None = None
    if baseline_policy in {"skip", "none"}:
        _emit(message_callback, f"Skipping baseline benchmark because baseline_policy={baseline_policy}.")
        _record_skipped_baseline_policy(run_dir, baseline_policy)
    elif baseline_policy == "provided":
        metrics, source = load_provided_baseline_metrics(run_dir, baseline_metrics_file)
        _emit(message_callback, f"Recording provided baseline metrics from {source}.")
        baseline = record_provided_code_task_baseline(
            run_dir,
            metrics=metrics,
            source_path=source,
            env_mode=spec.env_mode,
            python_executable=spec.python_executable,
        )
        baseline_status = baseline.status
        baseline_report_path = baseline.report_path
    else:
        _emit(message_callback, "Running baseline benchmark for code-task evidence.")
        baseline = run_code_task_baseline(
            run_dir,
            timeout_sec=timeout_sec,
            env_mode=spec.env_mode,
            python_executable=spec.python_executable,
        )
        baseline_status = baseline.status
        baseline_report_path = baseline.report_path

    _emit(message_callback, "Building prompt-ready code-task context pack.")
    context_pack = build_code_task_context_pack(
        run_dir,
        max_files=8,
        max_source_chars_per_file=4000,
        max_total_chars=20_000,
    )

    _emit(message_callback, "Calling LLM for batch-oriented code-task work plan.")
    work_plan = generate_code_task_work_plan(
        run_dir,
        model=model,
        use_llm=True,
        max_files=8,
        max_source_chars_per_file=3000,
        message_callback=message_callback,
    )
    if work_plan.mode != "llm":
        raise RuntimeError(
            "Code-task work planning did not use the LLM. "
            "Check SIMPLE_AR_API_KEY, SIMPLE_AR_BASE_URL, and SIMPLE_AR_MODEL."
        )
    work_item_id = _first_executable_work_item_id(work_plan.work_plan_path)
    _emit(message_callback, f"Creating code-task attempt/batch state for {work_item_id}.")
    batch = create_code_task_batch(
        run_dir,
        work_item_id=work_item_id,
        merge_dependent_chain=False,
    )

    _emit(message_callback, "Calling LLM for code-task patch plan.")
    plan = generate_patch_plan(
        run_dir,
        model=model,
        use_llm=True,
        message_callback=message_callback,
    )
    if plan.mode != "llm":
        raise RuntimeError(
            "Code-task patch planning did not use the LLM. "
            "Check SIMPLE_AR_API_KEY, SIMPLE_AR_BASE_URL, and SIMPLE_AR_MODEL."
        )

    record_plan_decision(
        run_dir,
        decision="approve",
        note=spec.approval_note,
        reviewer="pipeline",
    )

    _emit(message_callback, "Calling LLM for code-task edit proposal.")
    proposal = propose_patch_edits(
        run_dir,
        model=model,
        use_llm=True,
        allow_large_edits=spec.allow_large_edits,
        message_callback=message_callback,
    )
    if proposal.mode != "llm" or proposal.edit_count == 0:
        raise RuntimeError(
            "Code-task edit proposal was empty or did not use the LLM. "
            "Inspect code_task_run/code_task/meta/proposed_edits.json if present."
        )

    _emit(message_callback, "Applying code-task edits to isolated workspace.")
    patch = apply_patch_edits(run_dir, allow_large_edits=spec.allow_large_edits)
    if not spec.allow_test_changes and any(is_protected_edit_path(path) for path in patch.changed_files):
        raise RuntimeError(
            "Code-task experiment rejected a patch that modified protected "
            "tests or benchmark files. Improve source behavior without changing "
            "validation targets."
        )
    _emit(message_callback, "Reviewing embedded code-task patch before validation.")
    review = review_code_task_changes(
        run_dir,
        phase="post_apply",
        model=model,
        use_llm=use_llm,
        message_callback=message_callback,
    )
    if review.status == "failed":
        raise RuntimeError(
            "Embedded code-task review blocked the applied patch. "
            f"See {review.report_path}."
        )
    validation = validate_code_task(run_dir)
    if validation.status == "failed":
        raise RuntimeError(
            "Embedded code-task validation failed after applying edits. "
            f"See {validation.report_path}."
        )
    changed_files = _verify_or_repair_patch(
        run_dir,
        spec=spec,
        model=model,
        use_llm=use_llm,
        timeout_sec=timeout_sec,
        changed_files=patch.changed_files,
        message_callback=message_callback,
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
        changed_files=changed_files,
        validation_status=validation.status,
        template=spec.template,
        baseline_status=baseline_status,
        environment_report_path=environment.report_path,
        baseline_report_path=baseline_report_path,
        repo_map_path=init.repo_map_path,
        repo_map_summary_path=init.repo_map_summary_path,
        context_pack_path=context_pack.context_pack_path,
        context_prompt_path=context_pack.prompt_context_path,
        context_snippets_path=context_pack.snippets_path,
        work_plan_path=work_plan.work_plan_path,
        work_plan_markdown_path=work_plan.work_plan_markdown_path,
        work_plan_mode=work_plan.mode,
        work_plan_item_count=work_plan.item_count,
        attempt_id=batch.attempt_id,
        attempt_state_path=batch.attempt_state_path,
        batch_id=batch.batch_id,
        batch_state_path=batch.batch_state_path,
        work_item_id=batch.work_item_id,
        summary_path=run_dir / "code_task" / "summary.md",
    )


def _first_executable_work_item_id(work_plan_path: Path) -> str:
    data = read_json(work_plan_path)
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected JSON object in {work_plan_path}")
    items = [item for item in data.get("items", []) if isinstance(item, dict)]
    for item in items:
        if _work_item_target_files(item):
            item_id = str(item.get("id") or "").strip()
            if item_id:
                return item_id
    raise RuntimeError(f"Work plan has no executable items: {work_plan_path}")


def _record_skipped_baseline_policy(run_dir: Path, policy: str) -> None:
    manifest = load_code_task_manifest(run_dir)
    benchmark = manifest_section(manifest, "benchmark")
    benchmark["baseline_policy"] = {
        "policy": policy,
        "status": "skipped",
        "source": "config",
        "updated_at": utcnow_iso(),
    }
    save_code_task_manifest(run_dir, manifest)


def _work_item_target_files(item: dict[str, Any]) -> list[str]:
    value = item.get("target_files")
    if not isinstance(value, list):
        return []
    return [path for path in value if isinstance(path, str) and path.strip()]


def _verify_or_repair_patch(
    run_dir: Path,
    *,
    spec: CodeTaskExperimentSpec,
    model: str | None,
    use_llm: bool,
    timeout_sec: int,
    changed_files: tuple[str, ...],
    message_callback: MessageCallback | None,
) -> tuple[str, ...]:
    _emit(message_callback, "Running patched benchmark for embedded code-task verification.")
    first = run_code_task_benchmark(
        run_dir,
        timeout_sec=timeout_sec,
        env_mode=spec.env_mode,
        python_executable=spec.python_executable,
    )
    if first.status == "passed":
        return changed_files
    if not use_llm:
        raise RuntimeError(
            "Embedded code-task patched benchmark failed and LLM repair is disabled. "
            f"See {first.report_path}."
        )

    _emit(message_callback, "Embedded code-task benchmark failed; analyzing failure.")
    analysis = analyze_code_task_failure(run_dir)
    if analysis.status == "no_failure":
        raise RuntimeError(
            "Embedded code-task benchmark failed, but no actionable failure analysis was produced. "
            f"See {analysis.analysis_path}."
        )
    _emit(message_callback, "Calling LLM for one bounded embedded code-task repair.")
    repair = propose_repair_edits(
        run_dir,
        model=model,
        use_llm=True,
        max_files=8,
        max_source_chars_per_file=4000,
        message_callback=message_callback,
    )
    if repair.edit_count == 0:
        raise RuntimeError(
            "Embedded code-task repair produced no edits after a failed benchmark. "
            f"See {repair.proposal_path}."
        )
    _emit(message_callback, "Applying embedded code-task repair proposal.")
    repaired_patch = apply_patch_edits(run_dir, edits_file=repair.proposal_path)
    if not spec.allow_test_changes and any(
        is_protected_edit_path(path) for path in repaired_patch.changed_files
    ):
        raise RuntimeError(
            "Embedded code-task repair modified protected tests or benchmark files. "
            "Improve source behavior without changing validation targets."
        )
    _emit(message_callback, "Reviewing embedded code-task repair before validation.")
    repaired_review = review_code_task_changes(
        run_dir,
        phase="post_repair",
        model=model,
        use_llm=use_llm,
        message_callback=message_callback,
    )
    if repaired_review.status == "failed":
        raise RuntimeError(
            "Embedded code-task review blocked the repair patch. "
            f"See {repaired_review.report_path}."
        )
    repaired_validation = validate_code_task(run_dir)
    if repaired_validation.status == "failed":
        raise RuntimeError(
            "Embedded code-task validation failed after repair. "
            f"See {repaired_validation.report_path}."
        )
    repaired = run_code_task_benchmark(
        run_dir,
        timeout_sec=timeout_sec,
        env_mode=spec.env_mode,
        python_executable=spec.python_executable,
    )
    if repaired.status != "passed":
        raise RuntimeError(
            "Embedded code-task patched benchmark still failed after one bounded repair. "
            f"See {repaired.report_path}."
        )
    return _merge_paths(changed_files, repaired_patch.changed_files)


def _merge_paths(first: tuple[str, ...], second: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    merged: list[str] = []
    for path in [*first, *second]:
        if path not in seen:
            seen.add(path)
            merged.append(path)
    return tuple(merged)


def _emit(callback: MessageCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)
