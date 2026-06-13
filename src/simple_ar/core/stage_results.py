from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from simple_ar.app.state import (
    CodeState,
    DesignState,
    PlanState,
    ReadState,
    ReportState,
    RunStageState,
    SearchState,
    StageRuntime,
    SynthesisState,
)
from simple_ar.core.artifacts import read_json, read_jsonl, read_text, relative_to_run
from simple_ar.literature.models import Paper
from simple_ar.research.outputs.artifacts import (
    DESIGN_DECISION_LOG,
    DESIGN_EVAL_JSON,
    DESIGN_EVAL_MD,
    DESIGN_EVIDENCE_REVIEW_MD,
    DESIGN_EXPERIMENT_CONTRACT_JSON,
    DESIGN_EXPERIMENT_CONTRACT_MD,
    DESIGN_EXTERNAL_AGENT_BACKEND,
    DESIGN_RETENTION_POLICY_JSON,
    DESIGN_RETENTION_POLICY_MD,
    DESIGN_TOOL_ADAPTER_CONTRACT_JSON,
    DESIGN_TOOL_ADAPTER_CONTRACT_MD,
    DESIGN_TOOL_CONTEXT_JSON,
    DESIGN_TOOL_CONTEXT_MD,
    DESIGN_TOOL_TRACE,
    READ_CLAIM_CARDS,
    READ_CODE_LINKS,
    READ_DATASET_CARDS,
    READ_METHOD_CARDS,
    READ_PAPER_CARDS,
    READ_READING_TABLE,
    READ_SCREENING_DECISIONS,
    READ_SHORTLIST,
    SEARCH_COVERAGE_JSON,
    SEARCH_COVERAGE_MD,
    SEARCH_DOCUMENTS,
    SEARCH_INDEX_META,
    SEARCH_META,
    SEARCH_PAPERS,
    SEARCH_RESEARCH_PLAN,
    SEARCH_RETRIEVAL_SELECTION,
    SYNTHESIS_EVIDENCE_PACK_JSON,
    SYNTHESIS_EVIDENCE_PACK_MD,
    SYNTHESIS_GAP_SUMMARY,
    SYNTHESIS_IDEA_CANDIDATES,
    SYNTHESIS_NOVELTY_CHECKS,
    SYNTHESIS_BRIEF_JSON,
)
from simple_ar.core.stages import Stage

if TYPE_CHECKING:
    from simple_ar.core.pipeline import Context


@dataclass(frozen=True)
class CollectedStageResult:
    """State and human-readable artifacts collected after one stage finishes."""

    state: StageRuntime
    contract: dict[str, Any]
    report_markdown: str


def collect_stage_result(ctx: "Context", stage: Stage) -> CollectedStageResult:
    if stage == Stage.PLAN:
        return _collect_plan(ctx)
    if stage == Stage.SEARCH:
        return _collect_search(ctx)
    if stage == Stage.READ:
        return _collect_read(ctx)
    if stage == Stage.SYNTHESIZE:
        return _collect_synthesize(ctx)
    if stage == Stage.DESIGN:
        return _collect_design(ctx)
    if stage == Stage.CODE:
        return _collect_code(ctx)
    if stage == Stage.RUN:
        return _collect_run(ctx)
    if stage == Stage.REPORT:
        return _collect_report(ctx)
    raise RuntimeError(f"Unhandled stage collector: {stage}")


def _collect_plan(ctx: "Context") -> CollectedStageResult:
    goal_path = ctx.artifact_path("goal.md")
    problem_path = ctx.artifact_path("problem.md")
    goal_markdown = read_text(goal_path)
    problem_markdown = read_text(problem_path)
    state = PlanState(
        goal_markdown=goal_markdown,
        problem_markdown=problem_markdown,
        legacy_outputs={
            "goal.md": _rel(ctx, goal_path),
            "problem.md": _rel(ctx, problem_path),
        },
    )
    contract = {
        "schema_version": "plan_contract.v1",
        "topic": ctx.topic,
        "goal_markdown": goal_markdown,
        "problem_markdown": problem_markdown,
    }
    report_markdown = "# Plan Summary\n\n" + goal_markdown.strip() + "\n\n" + problem_markdown.strip() + "\n"
    return CollectedStageResult(state=state, contract=contract, report_markdown=report_markdown)


def _collect_search(ctx: "Context") -> CollectedStageResult:
    research_plan_path = ctx.artifact_path(SEARCH_RESEARCH_PLAN)
    search_meta_path = ctx.artifact_path(SEARCH_META)
    papers_path = ctx.artifact_path(SEARCH_PAPERS)
    documents_path = ctx.artifact_path(SEARCH_DOCUMENTS)
    coverage_md_path = ctx.artifact_path(SEARCH_COVERAGE_MD)
    coverage_json_path = ctx.artifact_path(SEARCH_COVERAGE_JSON)
    index_meta_path = ctx.artifact_path(SEARCH_INDEX_META)
    retrieval_selection_path = ctx.artifact_path(SEARCH_RETRIEVAL_SELECTION)

    research_plan = read_json(research_plan_path)
    search_meta = read_json(search_meta_path)
    papers = read_jsonl(papers_path)
    documents = read_jsonl(documents_path)
    coverage_report = read_json(coverage_json_path)
    index_meta = read_json(index_meta_path)

    selected_paper_ids = [str(row.get("id", "")) for row in papers if row.get("id")]
    selected_document_ids = [str(row.get("document_id", "")) for row in documents if row.get("document_id")]
    source_plan = research_plan.get("source_plan", {})
    query_plan = research_plan.get("query_plan", {})
    research_questions = research_plan.get("research_questions", {}).get("questions", [])
    coverage_markdown = read_text(coverage_md_path)

    state = SearchState(
        query=str(search_meta.get("query", "")),
        queries=[str(item) for item in search_meta.get("queries", []) if str(item).strip()],
        sources=[str(item) for item in search_meta.get("sources", []) if str(item).strip()],
        planner=str(search_meta.get("research_planner", "")),
        research_questions=[
            str(item.get("question", ""))
            for item in research_questions
            if isinstance(item, dict) and str(item.get("question", "")).strip()
        ],
        research_plan_path=_rel(ctx, research_plan_path),
        search_meta_path=_rel(ctx, search_meta_path),
        papers_path=_rel(ctx, papers_path),
        documents_path=_rel(ctx, documents_path),
        coverage_path=_rel(ctx, coverage_md_path),
        coverage_json_path=_rel(ctx, coverage_json_path),
        selected_paper_ids=selected_paper_ids,
        selected_document_ids=selected_document_ids,
        document_count=len(documents),
        chunk_count=int(index_meta.get("chunk_count", 0) or 0),
        store_paths={
            "index_meta": _rel(ctx, index_meta_path),
            "index_backend": str(index_meta.get("backend", "")),
            "index_root": str(source_plan.get("index_root", "") or ""),
        },
        legacy_outputs={
            "planning/research_plan.json": _rel(ctx, research_plan_path),
            "search_meta.json": _rel(ctx, search_meta_path),
            "papers.jsonl": _rel(ctx, papers_path),
            SEARCH_DOCUMENTS: _rel(ctx, documents_path),
            **(
                {SEARCH_RETRIEVAL_SELECTION: _rel(ctx, retrieval_selection_path)}
                if retrieval_selection_path.exists()
                else {}
            ),
            SEARCH_COVERAGE_MD: _rel(ctx, coverage_md_path),
            SEARCH_COVERAGE_JSON: _rel(ctx, coverage_json_path),
            SEARCH_INDEX_META: _rel(ctx, index_meta_path),
        },
    )
    contract = {
        "schema_version": "search_contract.v1",
        "query": search_meta.get("query", ""),
        "queries": search_meta.get("queries", []),
        "sources": search_meta.get("sources", []),
        "planner": query_plan.get("planner", search_meta.get("research_planner", "")),
        "selected_paper_ids": selected_paper_ids,
        "selected_document_ids": selected_document_ids,
        "counts": {
            "papers": len(papers),
            "documents": len(documents),
            "chunks": int(index_meta.get("chunk_count", 0) or 0),
        },
        "store": state.store_paths,
        "coverage": coverage_report,
    }
    return CollectedStageResult(state=state, contract=contract, report_markdown=coverage_markdown)


def _collect_read(ctx: "Context") -> CollectedStageResult:
    notes_path = ctx.artifact_path("notes.md")
    paper_notes_path = ctx.artifact_path("paper_notes.json")
    screening_path = ctx.artifact_path(READ_SCREENING_DECISIONS)
    shortlist_path = ctx.artifact_path(READ_SHORTLIST)
    reading_table_path = ctx.artifact_path(READ_READING_TABLE)
    notes_markdown = read_text(notes_path)
    paper_notes = read_json(paper_notes_path)
    shortlist = read_jsonl(shortlist_path) if shortlist_path.exists() else []
    legacy_outputs = {
        "notes.md": _rel(ctx, notes_path),
        "paper_notes.json": _rel(ctx, paper_notes_path),
    }
    debug_card_paths: dict[str, str] = {}
    for artifact in (
        READ_SCREENING_DECISIONS,
        READ_SHORTLIST,
        READ_READING_TABLE,
        READ_PAPER_CARDS,
        READ_CLAIM_CARDS,
        READ_METHOD_CARDS,
        READ_DATASET_CARDS,
        READ_CODE_LINKS,
    ):
        path = ctx.artifact_path(artifact)
        if path.exists():
            legacy_outputs[artifact] = _rel(ctx, path)
            if artifact.startswith("cards/"):
                debug_card_paths[artifact] = _rel(ctx, path)
    state = ReadState(
        notes_path=_rel(ctx, notes_path),
        paper_notes_path=_rel(ctx, paper_notes_path),
        screening_decisions_path=_rel(ctx, screening_path) if screening_path.exists() else None,
        shortlist_path=_rel(ctx, shortlist_path) if shortlist_path.exists() else None,
        reading_table_path=_rel(ctx, reading_table_path) if reading_table_path.exists() else None,
        shortlist_count=len(shortlist),
        paper_note_count=len(paper_notes) if isinstance(paper_notes, list) else 0,
        debug_card_paths=debug_card_paths,
        legacy_outputs=legacy_outputs,
    )
    contract = {
        "schema_version": "read_contract.v1",
        "shortlist_count": state.shortlist_count,
        "paper_note_count": state.paper_note_count,
        "paper_ids": [
            item.get("paper_id")
            for item in paper_notes
            if isinstance(item, dict) and item.get("paper_id")
        ],
    }
    return CollectedStageResult(state=state, contract=contract, report_markdown=notes_markdown)


def _collect_synthesize(ctx: "Context") -> CollectedStageResult:
    synthesis_path = ctx.artifact_path("synthesis.md")
    hypothesis_path = ctx.artifact_path("hypothesis.md")
    synthesis_brief_path = ctx.artifact_path(SYNTHESIS_BRIEF_JSON)
    evidence_pack_path = ctx.artifact_path(SYNTHESIS_EVIDENCE_PACK_JSON)
    idea_candidates_path = ctx.artifact_path(SYNTHESIS_IDEA_CANDIDATES)
    synthesis_markdown = read_text(synthesis_path)
    hypothesis_markdown = read_text(hypothesis_path)
    synthesis_brief = read_json(synthesis_brief_path) if synthesis_brief_path.exists() else {}
    evidence_pack = read_json(evidence_pack_path) if evidence_pack_path.exists() else {}
    idea_candidates = (
        synthesis_brief.get("idea_candidates", [])
        if isinstance(synthesis_brief.get("idea_candidates"), list)
        else read_jsonl(idea_candidates_path) if idea_candidates_path.exists() else []
    )
    legacy_outputs = {
        "synthesis.md": _rel(ctx, synthesis_path),
        "hypothesis.md": _rel(ctx, hypothesis_path),
    }
    if synthesis_brief_path.exists():
        legacy_outputs[SYNTHESIS_BRIEF_JSON] = _rel(ctx, synthesis_brief_path)
    for artifact in (
        SYNTHESIS_EVIDENCE_PACK_JSON,
        SYNTHESIS_EVIDENCE_PACK_MD,
        SYNTHESIS_GAP_SUMMARY,
        SYNTHESIS_IDEA_CANDIDATES,
        SYNTHESIS_NOVELTY_CHECKS,
    ):
        path = ctx.artifact_path(artifact)
        if path.exists():
            legacy_outputs[artifact] = _rel(ctx, path)
    state = SynthesisState(
        synthesis_markdown=synthesis_markdown,
        hypothesis_markdown=hypothesis_markdown,
        synthesis_path=_rel(ctx, synthesis_path),
        hypothesis_path=_rel(ctx, hypothesis_path),
        synthesis_brief_path=_rel(ctx, synthesis_brief_path) if synthesis_brief_path.exists() else None,
        evidence_pack_path=_rel(ctx, evidence_pack_path) if evidence_pack_path.exists() else None,
        gap_summary_path=_rel(ctx, ctx.artifact_path(SYNTHESIS_GAP_SUMMARY))
        if ctx.artifact_path(SYNTHESIS_GAP_SUMMARY).exists()
        else None,
        idea_candidates_path=_rel(ctx, idea_candidates_path) if idea_candidates_path.exists() else None,
        novelty_checks_path=_rel(ctx, ctx.artifact_path(SYNTHESIS_NOVELTY_CHECKS))
        if ctx.artifact_path(SYNTHESIS_NOVELTY_CHECKS).exists()
        else None,
        idea_candidate_count=len(idea_candidates),
        legacy_outputs=legacy_outputs,
    )
    contract = {
        "schema_version": "synthesis_contract.v1",
        "synthesis_markdown": synthesis_markdown,
        "hypothesis_markdown": hypothesis_markdown,
        "synthesis_brief": {
            "schema_version": synthesis_brief.get("schema_version", ""),
            "path": _rel(ctx, synthesis_brief_path) if synthesis_brief_path.exists() else "",
            "limitations": synthesis_brief.get("limitations", []),
        },
        "debug_evidence_pack": {
            "schema_version": evidence_pack.get("schema_version", ""),
            "path": _rel(ctx, evidence_pack_path) if evidence_pack_path.exists() else "",
        },
        "idea_candidate_count": len(idea_candidates),
    }
    report_markdown = synthesis_markdown.strip() + "\n\n" + hypothesis_markdown.strip() + "\n"
    return CollectedStageResult(state=state, contract=contract, report_markdown=report_markdown)


def _collect_design(ctx: "Context") -> CollectedStageResult:
    plan_path = ctx.artifact_path("experiment_plan.json")
    experiment_contract_path = ctx.artifact_path(DESIGN_EXPERIMENT_CONTRACT_JSON)
    plan = read_json(plan_path)
    experiment_name = str(plan.get("name", ""))
    experiment_template = str(plan.get("template", ""))
    experiment_mode = str(plan.get("mode", "standard"))
    state = DesignState(
        experiment_plan_path=_rel(ctx, plan_path),
        experiment_contract_path=_rel(ctx, experiment_contract_path) if experiment_contract_path.exists() else None,
        experiment_name=experiment_name,
        experiment_template=experiment_template,
        experiment_mode=experiment_mode,
        legacy_outputs={
            "experiment_plan.json": _rel(ctx, plan_path),
            **(
                {
                    DESIGN_EXPERIMENT_CONTRACT_JSON: _rel(ctx, experiment_contract_path),
                    DESIGN_EXPERIMENT_CONTRACT_MD: _rel(ctx, ctx.artifact_path(DESIGN_EXPERIMENT_CONTRACT_MD)),
                    DESIGN_TOOL_CONTEXT_JSON: _rel(ctx, ctx.artifact_path(DESIGN_TOOL_CONTEXT_JSON)),
                    DESIGN_TOOL_CONTEXT_MD: _rel(ctx, ctx.artifact_path(DESIGN_TOOL_CONTEXT_MD)),
                    DESIGN_EVIDENCE_REVIEW_MD: _rel(ctx, ctx.artifact_path(DESIGN_EVIDENCE_REVIEW_MD)),
                    DESIGN_DECISION_LOG: _rel(ctx, ctx.artifact_path(DESIGN_DECISION_LOG)),
                    DESIGN_EVAL_JSON: _rel(ctx, ctx.artifact_path(DESIGN_EVAL_JSON)),
                    DESIGN_EVAL_MD: _rel(ctx, ctx.artifact_path(DESIGN_EVAL_MD)),
                    DESIGN_TOOL_ADAPTER_CONTRACT_JSON: _rel(ctx, ctx.artifact_path(DESIGN_TOOL_ADAPTER_CONTRACT_JSON)),
                    DESIGN_TOOL_ADAPTER_CONTRACT_MD: _rel(ctx, ctx.artifact_path(DESIGN_TOOL_ADAPTER_CONTRACT_MD)),
                    DESIGN_TOOL_TRACE: _rel(ctx, ctx.artifact_path(DESIGN_TOOL_TRACE)),
                    DESIGN_EXTERNAL_AGENT_BACKEND: _rel(ctx, ctx.artifact_path(DESIGN_EXTERNAL_AGENT_BACKEND)),
                    DESIGN_RETENTION_POLICY_JSON: _rel(ctx, ctx.artifact_path(DESIGN_RETENTION_POLICY_JSON)),
                    DESIGN_RETENTION_POLICY_MD: _rel(ctx, ctx.artifact_path(DESIGN_RETENTION_POLICY_MD)),
                }
                if experiment_contract_path.exists()
                else {}
            ),
        },
    )
    contract = {
        "schema_version": "design_contract.v1",
        "name": experiment_name,
        "template": experiment_template,
        "mode": experiment_mode,
        "metrics": plan.get("metrics", []),
        "timeout_sec": plan.get("timeout_sec"),
    }
    report_markdown = (
        "# Experiment Design\n\n"
        f"- Name: {experiment_name or 'unnamed'}\n"
        f"- Template: {experiment_template or 'unknown'}\n"
        f"- Mode: {experiment_mode or 'standard'}\n"
    )
    return CollectedStageResult(state=state, contract=contract, report_markdown=report_markdown)


def _collect_code(ctx: "Context") -> CollectedStageResult:
    experiment_path = ctx.artifact_path("experiment.py")
    code_task_meta_path = ctx.artifact_path("code_task_experiment.json")
    code_artifacts_path = ctx.artifact_path("code_artifacts.json")
    code_review_path = ctx.artifact_path("code_review.json")
    code_backend_path = ctx.artifact_path("code_backend.json")
    architecture_plan_path = ctx.artifact_path("architecture_plan.json")
    file_plan_path = ctx.artifact_path("file_plan.json")
    memory_path = ctx.artifact_path("implementation_memory.json")
    changed_files: list[str] = []
    if code_task_meta_path.exists():
        meta = read_json(code_task_meta_path)
        changed_files = [str(item) for item in meta.get("changed_files", []) if str(item).strip()]
    generated_files: list[str] = []
    if code_artifacts_path.exists():
        artifacts = read_json(code_artifacts_path)
        rows = artifacts.get("generated_files", []) if isinstance(artifacts, dict) else []
        generated_files = [
            str(item.get("path", ""))
            for item in rows
            if isinstance(item, dict) and str(item.get("path", "")).strip()
        ]
    state = CodeState(
        experiment_path=_rel(ctx, experiment_path),
        code_task_meta_path=_rel(ctx, code_task_meta_path) if code_task_meta_path.exists() else None,
        changed_files=changed_files,
        legacy_outputs={
            "experiment.py": _rel(ctx, experiment_path),
            **(
                {"code_task_experiment.json": _rel(ctx, code_task_meta_path)}
                if code_task_meta_path.exists()
                else {}
            ),
            **(
                {"code_artifacts.json": _rel(ctx, code_artifacts_path)}
                if code_artifacts_path.exists()
                else {}
            ),
            **(
                {"code_review.json": _rel(ctx, code_review_path)}
                if code_review_path.exists()
                else {}
            ),
            **(
                {"code_backend.json": _rel(ctx, code_backend_path)}
                if code_backend_path.exists()
                else {}
            ),
            **(
                {"architecture_plan.json": _rel(ctx, architecture_plan_path)}
                if architecture_plan_path.exists()
                else {}
            ),
            **({"file_plan.json": _rel(ctx, file_plan_path)} if file_plan_path.exists() else {}),
            **(
                {"implementation_memory.json": _rel(ctx, memory_path)}
                if memory_path.exists()
                else {}
            ),
        },
    )
    contract = {
        "schema_version": "code_contract.v1",
        "experiment_path": state.experiment_path,
        "code_task_meta_path": state.code_task_meta_path,
        "changed_files": changed_files,
        "generated_files": generated_files,
    }
    report_markdown = (
        "# Code Stage Summary\n\n"
        f"- Experiment script: `{state.experiment_path}`\n"
        f"- Changed files recorded: {len(changed_files)}\n"
        f"- Generated files recorded: {len(generated_files)}\n"
    )
    return CollectedStageResult(state=state, contract=contract, report_markdown=report_markdown)


def _collect_run(ctx: "Context") -> CollectedStageResult:
    results_path = ctx.artifact_path("results.json")
    stdout_path = ctx.artifact_path("stdout.txt")
    stderr_path = ctx.artifact_path("stderr.txt")
    results = read_json(results_path)
    metrics = results.get("metrics", {}) if isinstance(results, dict) else {}
    state = RunStageState(
        results_path=_rel(ctx, results_path),
        stdout_path=_rel(ctx, stdout_path),
        stderr_path=_rel(ctx, stderr_path),
        metrics=dict(metrics) if isinstance(metrics, dict) else {},
        legacy_outputs={
            "results.json": _rel(ctx, results_path),
            "stdout.txt": _rel(ctx, stdout_path),
            "stderr.txt": _rel(ctx, stderr_path),
        },
    )
    contract = {
        "schema_version": "run_contract.v1",
        "metrics": state.metrics,
        "returncode": results.get("returncode") if isinstance(results, dict) else None,
        "timed_out": bool(results.get("timed_out")) if isinstance(results, dict) else False,
    }
    report_lines = ["# Run Results", ""]
    if state.metrics:
        for key, value in state.metrics.items():
            report_lines.append(f"- {key}: {value}")
    else:
        report_lines.append("- No metrics were recorded.")
    report_lines.append("")
    return CollectedStageResult(
        state=state,
        contract=contract,
        report_markdown="\n".join(report_lines),
    )


def _collect_report(ctx: "Context") -> CollectedStageResult:
    report_dir = _report_artifact_dir(ctx)
    report_path = report_dir / "report.md"
    references_path = report_dir / "references.bib"
    citation_map_path = report_dir / "citation_map.json"
    quality_path = report_dir / "report_quality.json"
    memory_path = report_dir / "report_memory.json"
    audit_path = report_dir / "report_audit.json"
    manifest_path = report_dir / "manifest.json"
    report_text = read_text(report_path)
    quality = read_json(quality_path)
    audit = read_json(audit_path) if audit_path.exists() else {}
    manifest = read_json(manifest_path)
    papers = manifest.get("cited_papers", []) if isinstance(manifest, dict) else []
    cited_paper_ids = [
        str(item.get("id", ""))
        for item in papers
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    ]
    state = ReportState(
        report_path=_rel(ctx, report_path),
        references_path=_rel(ctx, references_path),
        quality_path=_rel(ctx, quality_path),
        memory_path=_rel(ctx, memory_path) if memory_path.exists() else None,
        audit_path=_rel(ctx, audit_path) if audit_path.exists() else None,
        manifest_path=_rel(ctx, manifest_path),
        report_mode=str(manifest.get("report_mode", "")) if isinstance(manifest, dict) else "",
        template_name=str(
            (manifest.get("report_template", {}) or {}).get("name", "")
        )
        if isinstance(manifest, dict)
        else "",
        audit_status=str(audit.get("status", "")) if isinstance(audit, dict) else "",
        cited_paper_ids=cited_paper_ids,
        legacy_outputs={
            "report.md": _rel(ctx, report_path),
            "references.bib": _rel(ctx, references_path),
            **({"citation_map.json": _rel(ctx, citation_map_path)} if citation_map_path.exists() else {}),
            "report_quality.json": _rel(ctx, quality_path),
            **({"report_memory.json": _rel(ctx, memory_path)} if memory_path.exists() else {}),
            **({"report_audit.json": _rel(ctx, audit_path)} if audit_path.exists() else {}),
            "manifest.json": _rel(ctx, manifest_path),
        },
    )
    contract = {
        "schema_version": "report_contract.v1",
        "report_mode": state.report_mode,
        "template_name": state.template_name,
        "quality_status": quality.get("status") if isinstance(quality, dict) else None,
        "audit_status": state.audit_status,
        "cited_paper_ids": cited_paper_ids,
    }
    return CollectedStageResult(state=state, contract=contract, report_markdown=report_text)


def _report_artifact_dir(ctx: "Context") -> Path:
    """Return the report package directory for normal or variant output."""
    stage_dir = ctx.stage_dir()
    if str(ctx.config.get("report_output_mode") or "").strip().lower() == "variant":
        marker_path = stage_dir / "latest_variant.json"
        if marker_path.exists():
            marker = read_json(marker_path)
            if isinstance(marker, dict):
                variant_dir = ctx.resolve_artifact(str(marker.get("output_dir") or ""))
                if variant_dir is not None and (variant_dir / "report.md").exists():
                    return variant_dir
    return stage_dir


def _rel(ctx: "Context", path: Path) -> str:
    return relative_to_run(ctx.run_dir, path) or str(path)
