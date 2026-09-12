"""Use the existing CodeTask executor as a non-measuring implementation action."""

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping

from simple_ar.core.capabilities import ArtifactRef, CapabilityContext, CapabilityResult
from simple_ar.core.artifacts import read_json, write_json, write_text
from simple_ar.research.design import ResearchDesignResult
from simple_ar.research.task_handoff import research_handoff_text
from simple_ar.code_task.orchestration.execute import implement_code_task
from simple_ar.code_task.orchestration.verification import validate_repair_patch
from simple_ar.code_task.execution.repair import RepairEvidence, propose_repair_edits
from simple_ar.code_task.editing.patching import apply_patch_edits
from simple_ar.code_task.runtime.state import code_task_paths, load_code_task_manifest, save_code_task_manifest
from simple_ar.code_task.editing.scope import protected_patterns_from_manifest
from simple_ar.experiment.execution.measurement import snapshot_protocol_assets, reconcile_protocol_assets


@dataclass(frozen=True)
class ImplementationRequest:
    run_dir: Path
    cwd: Path
    approval_note: str
    llm_client: Any = field(repr=False, compare=False)
    protocol: Mapping[str, Any] | None = None
    failure_ref: ArtifactRef | None = None
    budget_profile: str | None = None
    allow_large_edits: bool = False


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
    if request.failure_ref is None:
        outcome = implement_code_task(
            request.run_dir, approval_note=request.approval_note,
            llm_client=request.llm_client, use_llm=True, allow_planning_fallback=False,
            budget_profile=request.budget_profile,
            allow_large_edits=request.allow_large_edits,
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
    finished = stop_reason == "stop_point" and integrity["status"] != "changed"
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
    artifact = context.store.write_json(
        "implementation.json", {
            "schema_version": "research_implementation.v1",
            "status": "validated" if finished else "incomplete",
            "code_task_run_dir": str(request.run_dir), "workspace_dir": str(paths.workspace_dir),
            "stop_reason": stop_reason, "next_action": next_action,
            "steps": steps,
            "failure_ref": request.failure_ref.to_dict() if request.failure_ref else None,
            "asset_integrity": integrity,
            "artifact_refs": {name: ref.to_dict() for name, ref in evidence.items()},
            "artifact_base": "attempt",
        }, kind="implementation_result", schema="research_implementation.v1", producer="research.implementation",
    )
    return CapabilityResult(
        status="completed" if finished else "blocked", artifacts=(task_ref, *evidence.values(), artifact),
        diagnostics=() if finished else (
            *(step["detail"] for step in steps if step["status"] == "blocked"),
            next_action, f"Asset integrity: {integrity['status']}",
        ),
    )


def _prepare_research_task(
    context: CapabilityContext, request: ImplementationRequest, task_dir: Path,
) -> ArtifactRef:
    """Freeze consumed research context before planning; never reuse a stale plan."""
    design_ref = next(ref for ref in context.inputs if ref.kind == "research_design")
    design = ResearchDesignResult.from_handoff_dict(context.read_input_json(design_ref))
    if design.contract is None:
        raise ValueError("Implementation requires a selected research design contract.")
    baseline_refs = [ref for ref in context.inputs if ref.kind == "experiment_result" and ref != request.failure_ref]
    baseline = context.read_input_json(baseline_refs[0]) if baseline_refs else None
    consumed = {"design": design.to_handoff_dict(), "protocol": request.protocol,
                "baseline": None if baseline is None else {
                    "source": baseline_refs[0].to_dict(), "status": baseline["status"],
                    "metrics": baseline["metrics"], "measurement": baseline["measurement"],
                }}
    if len(baseline_refs) > 1:
        consumed["paired_baselines"] = [{"source": ref.to_dict(), "status": result["status"],
            "metrics": result["metrics"], "measurement": result["measurement"]}
            for ref in baseline_refs for result in (context.read_input_json(ref),)]
    snapshot = task_dir / "research_handoff.json"
    task_path = task_dir / "task.md"
    if snapshot.exists():
        saved = read_json(snapshot)
        if saved["consumed"] != consumed:
            raise ValueError("Research inputs changed; prepare a new CodeTask run instead of reusing its plan.")
        original = saved["original_task"]
    else:
        if (task_dir / "work_plan.json").exists():
            raise ValueError("CodeTask already has a plan without this research handoff; use a fresh initialized run.")
        original = task_path.read_text(encoding="utf-8")
        write_json(snapshot, {"consumed": consumed, "original_task": original})
    task = original.rstrip() + "\n\n" + research_handoff_text(
        design.contract, execution_context="Use the configured protocol in the execution-boundary JSON below." if request.protocol else "",
    )
    task += "\n## Execution boundary and observed baseline\n\n"
    task += "The configured protocol and existing edit scope remain authoritative.\n"
    task += "Baseline values below are observations, not target values to hardcode.\n"
    task += "Implement and validate only; the research application owns benchmark execution.\n\n"
    task += "```json\n" + json.dumps(consumed, ensure_ascii=False, separators=(",", ":")) + "\n```\n"
    write_text(task_path, task)
    return context.store.write_text("inputs/research_code_task.md", task, kind="task_input",
                                    schema="markdown.v1", producer="research.implementation")
