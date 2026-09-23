"""Use the existing CodeTask executor as a non-measuring implementation action."""

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import shlex
import subprocess
from typing import Any, Callable, Mapping

from simple_ar.core.capabilities import ArtifactRef, CapabilityContext, CapabilityResult
from simple_ar.core.artifacts import read_json, write_json, write_text
from simple_ar.research.design import ResearchDesignResult
from simple_ar.research.task_handoff import research_handoff_text
from simple_ar.code_task.orchestration.execute import implement_code_task
from simple_ar.code_task.orchestration.verification import validate_repair_patch
from simple_ar.code_task.execution.runner import run_code_task_benchmark
from simple_ar.code_task.execution.repair import RepairEvidence, propose_repair_edits
from simple_ar.code_task.editing.patching import apply_patch_edits
from simple_ar.code_task.runtime.state import code_task_paths, load_code_task_manifest, save_code_task_manifest
from simple_ar.code_task.editing.scope import protected_patterns_from_manifest
from simple_ar.experiment.execution.measurement import snapshot_protocol_assets, reconcile_protocol_assets


IMPLEMENTATION_CONTEXT_MAX_FILES = 6
IMPLEMENTATION_CONTEXT_MAX_SOURCE_CHARS = 12_000


@dataclass(frozen=True)
class ImplementationRequest:
    run_dir: Path
    cwd: Path
    approval_note: str
    llm_client: Any = field(repr=False, compare=False)
    protocol: Mapping[str, Any] | None = None
    failure_ref: ArtifactRef | None = None
    revision_instruction: str = ""
    validation_command: tuple[str, ...] | None = None
    validation_timeout_sec: int | None = None
    budget_ledger: Any | None = field(default=None, repr=False, compare=False)
    session_id: str = ""
    attempt_id: str = ""
    budget_profile: str | None = None
    allow_large_edits: bool = False
    message_callback: Callable[[str], None] | None = field(default=None, repr=False, compare=False)


def run_implementation_capability(*, context: CapabilityContext, request: ImplementationRequest) -> CapabilityResult:
    if request.llm_client is None:
        raise ValueError("Implementation requires a shared LLM client; no offline patch is invented.")
    if not request.approval_note.strip():
        raise ValueError("Implementation requires explicit isolated-workspace approval.")
    manifest = load_code_task_manifest(request.run_dir)
    paths = code_task_paths(request.run_dir)
    if paths.workspace_dir.resolve() != request.cwd.resolve():
        raise ValueError("Candidate cwd must be the initialized CodeTask workspace.")
    task_ref = _prepare_research_task(context, request, paths.task_dir)
    before = snapshot_protocol_assets(request.protocol, request.cwd)
    protected = list(protected_patterns_from_manifest(manifest))
    for snapshot in before.values():
        try:
            relative = Path(snapshot["path"]).relative_to(request.cwd).as_posix()
        except ValueError:
            continue  # Shared data outside the editable workspace is audited too.
        if relative not in protected:
            protected.append(relative)
    scope = dict(manifest.get("edit_scope", {}))
    scope["protected_patterns"] = protected
    manifest["edit_scope"] = scope
    save_code_task_manifest(request.run_dir, manifest)
    repair_paths = {}
    steps = []
    validation = None
    if request.failure_ref is None:
        outcome = implement_code_task(
            request.run_dir, approval_note=request.approval_note,
            llm_client=request.llm_client, use_llm=True, allow_planning_fallback=False,
            budget_profile=request.budget_profile,
            allow_large_edits=request.allow_large_edits,
            max_files=IMPLEMENTATION_CONTEXT_MAX_FILES,
            max_source_chars_per_file=IMPLEMENTATION_CONTEXT_MAX_SOURCE_CHARS,
            message_callback=request.message_callback,
        )
        stop_reason, next_action = outcome.stop_reason, outcome.next_action
        steps = [asdict(step) for step in outcome.steps]
    else:
        failed = context.read_input_json(request.failure_ref)
        failure_dir = context.resolve_input(request.failure_ref).parent
        # Logs are attempt-relative artifacts; resolve through the same store
        # boundary rather than trusting a path supplied in the result JSON.
        store = context.input_store or context.store
        stderr_path = failure_dir / failed["artifacts"]["stderr"]
        details = store.read_text(store.ref(stderr_path))
        evidence = RepairEvidence(request.failure_ref.path, failed, details or json.dumps(failed["diagnosis"]))
        repair = propose_repair_edits(request.run_dir, llm_client=request.llm_client, failure_evidence=evidence)
        repair_paths = {"repair_proposal": repair.proposal_path,
                        "failure_evidence": repair.repair_dir / "failure_evidence.json"}
        if repair.mode != "llm" or not repair.edit_count:
            stop_reason, next_action = "empty_repair", "Inspect the failed measurement and repair proposal."
        else:
            apply_patch_edits(request.run_dir, edits_file=repair.proposal_path)
            validate_repair_patch(request.run_dir, llm_client=request.llm_client)
            stop_reason, next_action = "stop_point", "Measure the repaired candidate separately."
    integrity = reconcile_protocol_assets(before)
    if (
        request.validation_command
        and stop_reason == "stop_point"
        and integrity["status"] != "changed"
    ):
        command = (
            subprocess.list2cmdline(request.validation_command)
            if os.name == "nt"
            else shlex.join(request.validation_command)
        )
        validation = run_code_task_benchmark(
            request.run_dir,
            command=command,
            timeout_sec=request.validation_timeout_sec or 60,
            skip_validation=True,
            run_label="patched",
            budget_ledger=request.budget_ledger,
            session_id=request.session_id,
            attempt_id=request.attempt_id,
        )
        stop_reason = "stop_point" if validation.status == "passed" else "validation_failed"
        next_action = (
            "Review the recorded bug validation artifacts before accepting the patch."
            if validation.status != "passed"
            else "Review the patch and the passed bug validation artifacts."
        )
    finished = (
        stop_reason == "stop_point"
        and integrity["status"] != "changed"
        and (validation is None or validation.status == "passed")
    )
    # Keep the small implementation evidence with its attempt, not at mutable
    # absolute paths in an independently managed CodeTask run. Do not copy data,
    # environments, checkpoints or the whole workspace into every attempt.
    evidence = {}
    for name, path in {
        "patch": paths.task_dir / "patch.diff",
        "validation": paths.meta_dir / "validation_report.json",
        "review": paths.meta_dir / ("review_report_post_repair.json" if request.failure_ref else "review_report.json"),
        "work_plan": paths.task_dir / "work_plan.json",
        "patch_plan": paths.task_dir / "patch_plan.md",
        "research_handoff": paths.task_dir / "research_handoff.json",
        **repair_paths,
    }.items():
        if path.is_file():
            evidence[name] = context.store.write_text(
                f"code_task/{path.name}", path.read_text(encoding="utf-8"),
                kind=f"implementation_{name}", producer="research.implementation",
            )
    if validation is not None:
        for name, path in {
            "validation_report": validation.report_path,
            "validation_stdout": validation.stdout_path,
            "validation_stderr": validation.stderr_path,
            "validation_metrics": validation.metrics_path,
        }.items():
            if path.is_file():
                evidence[name] = context.store.write_text(
                    f"code_task/{path.name}", path.read_text(encoding="utf-8"),
                    kind=f"implementation_{name}", producer="research.implementation",
                )
    validation_row = None if validation is None else {
        "status": validation.status,
        "returncode": validation.returncode,
        "timed_out": validation.timed_out,
        "metrics": dict(validation.metrics),
    }
    artifact = context.store.write_json(
        "implementation.json", {
            "schema_version": "research_implementation.v1",
            "status": "validated" if finished else "incomplete",
            "code_task_run_dir": str(request.run_dir), "workspace_dir": str(paths.workspace_dir),
            "stop_reason": stop_reason, "next_action": next_action,
            "steps": steps,
            "failure_ref": request.failure_ref.to_dict() if request.failure_ref else None,
            "asset_integrity": integrity,
            "validation": validation_row,
            "artifact_refs": {name: ref.to_dict() for name, ref in evidence.items()},
            "artifact_base": "attempt",
        }, kind="implementation_result", schema="research_implementation.v1", producer="research.implementation",
    )
    capability_status = "completed" if finished else "partial" if validation is not None else "blocked"
    return CapabilityResult(
        status=capability_status, artifacts=(task_ref, *evidence.values(), artifact),
        diagnostics=() if finished else (
            *(step["detail"] for step in steps if step["status"] == "blocked"),
            next_action, f"Asset integrity: {integrity['status']}",
        ),
    )


def _prepare_research_task(
    context: CapabilityContext, request: ImplementationRequest, task_dir: Path,
) -> ArtifactRef:
    """Freeze consumed research context before planning; never reuse a stale plan."""
    design_ref = next((ref for ref in context.inputs if ref.kind == "research_design"), None)
    design = ResearchDesignResult.from_handoff_dict(context.read_input_json(design_ref)) if design_ref else None
    if design is not None and design.contract is None:
        raise ValueError("Implementation requires a selected research design contract.")
    brief_ref = next((ref for ref in context.inputs if ref.kind == "research_brief"), None)
    if design is None and brief_ref is None:
        raise ValueError("Implementation requires a research design or a bug-task brief.")
    baseline_refs = [ref for ref in context.inputs if ref.kind == "experiment_result" and ref != request.failure_ref]
    baseline = context.read_input_json(baseline_refs[0]) if baseline_refs else None
    consumed = {
        "task_kind": "research" if design is not None else "bug_fix",
        "brief": None if brief_ref is None else context.read_input_json(brief_ref),
        "design": None if design is None else design.to_handoff_dict(),
        "protocol": request.protocol,
        "revision_instruction": request.revision_instruction.strip(),
        "baseline": None if baseline is None else {
            "source": baseline_refs[0].to_dict(), "status": baseline["status"],
            "metrics": baseline["metrics"], "measurement": baseline["measurement"],
        },
    }
    if len(baseline_refs) > 1:
        consumed["paired_baselines"] = [{"source": ref.to_dict(), "status": result["status"],
            "metrics": result["metrics"], "measurement": result["measurement"]}
            for ref in baseline_refs for result in (context.read_input_json(ref),)]
    snapshot = task_dir / "research_handoff.json"
    task_path = task_dir / "task.md"
    if snapshot.exists():
        saved = read_json(snapshot)
        saved_consumed = dict(saved["consumed"])
        saved_consumed.setdefault("revision_instruction", "")
        if saved_consumed != consumed:
            raise ValueError("Research inputs changed; prepare a new CodeTask run instead of reusing its plan.")
        original = saved["original_task"]
    else:
        if (task_dir / "work_plan.json").exists():
            raise ValueError("CodeTask already has a plan without this research handoff; use a fresh initialized run.")
        original = task_path.read_text(encoding="utf-8")
        write_json(snapshot, {"consumed": consumed, "original_task": original})
    if design is not None:
        task = (
            "# Implementation-only CodeTask\n\n"
            "This action only proposes and validates the selected code change in the "
            "isolated workspace. The outer ResearchApplication owns literature review, "
            "experiment execution, result analysis, and academic report writing. Do "
            "not create a paper, report, citations, or documentation as part of this "
            "CodeTask action.\n\n"
            "Use the selected research design and the configured execution boundary "
            "below as the implementation requirements. Keep the change within the "
            "existing CodeTask edit scope and preserve all protected assets.\n\n"
            + research_handoff_text(
                design.contract,
                execution_context=(
                    "Use the configured protocol in the execution-boundary JSON below."
                    if request.protocol else ""
                ),
            )
        )
        brief = consumed.get("brief")
        if isinstance(brief, Mapping):
            objective = str(brief.get("objective") or brief.get("request_text") or "").strip()
            if objective:
                task += "\n## User research objective\n\n" + objective + "\n"
            constraints = brief.get("hard_constraints")
            if isinstance(constraints, list) and constraints:
                task += "\n## User hard constraints\n\n"
                task += "\n".join(f"- {item}" for item in constraints if str(item).strip()) + "\n"
            preferences = brief.get("preferences")
            if isinstance(preferences, list) and preferences:
                task += "\n## User preferences\n\n"
                task += "\n".join(f"- {item}" for item in preferences if str(item).strip()) + "\n"
            task += (
                "\nResolve implementation details the user objective explicitly delegates by inspecting "
                "the project and selecting a concrete option within the accepted design and CodeTask "
                "scope. Do not expand the approved change or alter the execution protocol.\n"
            )
        if request.revision_instruction.strip():
            task += (
                "\n\n## Evidence-directed revision\n\n"
                "Apply this proposed research revision only after inspecting the current workspace and "
                "existing patch. Preserve the configured edit scope and protected assets.\n\n"
                + request.revision_instruction.strip() + "\n"
            )
    else:
        brief = consumed["brief"]
        if not isinstance(brief, Mapping):
            raise ValueError("Bug-task implementation requires the persisted brief payload.")
        constraints = brief.get("hard_constraints", [])
        task = (
            "# Bug-fix CodeTask\n\n"
            "This action locates and repairs one scoped defect in the existing project. "
            "Use the isolated CodeTask workspace, preserve protected files, and make "
            "the smallest behaviorally justified patch. Do not search literature, "
            "train a model, run a research baseline, or write an academic report. "
            "The configured command is a short bug-validation command, not a research "
            "measurement.\n\n"
            f"## User request\n\n{str(brief.get('request_text') or '').strip()}\n"
            f"\n## Objective\n\n{str(brief.get('objective') or '').strip()}\n"
            + ("\n## Hard constraints\n\n" + "\n".join(f"- {item}" for item in constraints) + "\n"
               if isinstance(constraints, list) and constraints else "")
        )
    task += "\n## Execution boundary\n\n"
    task += "The configured protocol and existing edit scope remain authoritative.\n"
    if design is not None:
        task += "Execution boundary and observed baseline are supplied facts, not model-selected targets.\n"
        task += "Baseline values below are observations, not target values to hardcode.\n"
    task += "Implement and validate only; the research application owns benchmark execution.\n\n"
    task += "```json\n" + json.dumps(consumed, ensure_ascii=False, separators=(",", ":")) + "\n```\n"
    write_text(task_path, task)
    return context.store.write_text("inputs/research_code_task.md", task, kind="task_input",
                                    schema="markdown.v1", producer="research.implementation")
