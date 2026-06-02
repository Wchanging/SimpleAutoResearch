from __future__ import annotations

from pathlib import Path
from typing import Any

from simple_ar.core.artifacts import read_json, read_jsonl, read_text
from simple_ar.core.pipeline import Context
from simple_ar.core.stages import Stage


def load_problem_markdown(ctx: Context) -> str:
    if ctx.state is not None and ctx.state.plan.problem_markdown:
        return ctx.state.plan.problem_markdown
    return read_text(ctx.artifact_path("problem.md", Stage.PLAN))


def load_search_paper_rows(ctx: Context) -> list[dict[str, Any]]:
    path = None
    if ctx.state is not None and ctx.state.search.papers_path:
        path = ctx.resolve_artifact(ctx.state.search.papers_path)
    if path is None:
        path = ctx.artifact_path("papers.jsonl", Stage.SEARCH)
    return read_jsonl(path)


def load_notes_markdown(ctx: Context) -> str:
    if ctx.state is not None and ctx.state.read.notes_path:
        path = ctx.resolve_artifact(ctx.state.read.notes_path)
        if path is not None:
            return read_text(path)
    return read_text(ctx.artifact_path("notes.md", Stage.READ))


def load_paper_notes_json(ctx: Context) -> list[dict[str, Any]]:
    path = None
    if ctx.state is not None and ctx.state.read.paper_notes_path:
        path = ctx.resolve_artifact(ctx.state.read.paper_notes_path)
    if path is None:
        path = ctx.artifact_path("paper_notes.json", Stage.READ)
    data = read_json(path)
    return data if isinstance(data, list) else []


def load_hypothesis_markdown(ctx: Context) -> str:
    if ctx.state is not None and ctx.state.synthesize.hypothesis_markdown:
        return ctx.state.synthesize.hypothesis_markdown
    if ctx.state is not None and ctx.state.synthesize.hypothesis_path:
        path = ctx.resolve_artifact(ctx.state.synthesize.hypothesis_path)
        if path is not None:
            return read_text(path)
    return read_text(ctx.artifact_path("hypothesis.md", Stage.SYNTHESIZE))


def safe_read_artifact(ctx: Context, filename: str) -> str:
    path = _state_or_known_artifact(ctx, filename)
    return read_text(path) if path is not None else ""


def safe_read_json_artifact(ctx: Context, filename: str) -> dict[str, Any]:
    path = _state_or_known_artifact(ctx, filename)
    if path is None:
        return {}
    data = read_json(path)
    return data if isinstance(data, dict) else {}


def resolve_relative_run_path(ctx: Context, relative_path: str | None) -> Path | None:
    return ctx.resolve_artifact(relative_path) if relative_path else None


def _state_or_known_artifact(ctx: Context, filename: str) -> Path | None:
    if ctx.state is not None:
        known = ctx.state.resolve_artifact(filename)
        if known:
            path = ctx.resolve_artifact(known)
            if path is not None and path.exists():
                return path
    known_stage = _KNOWN_ARTIFACT_STAGES.get(filename)
    if known_stage is None:
        return None
    path = ctx.artifact_path(filename, known_stage)
    return path if path.exists() else None


_KNOWN_ARTIFACT_STAGES = {
    "goal.md": Stage.PLAN,
    "problem.md": Stage.PLAN,
    "papers.jsonl": Stage.SEARCH,
    "search_meta.json": Stage.SEARCH,
    "planning/research_plan.json": Stage.SEARCH,
    "documents/documents.jsonl": Stage.SEARCH,
    "documents/fulltext_manifest.json": Stage.SEARCH,
    "documents/fulltext_extraction.json": Stage.SEARCH,
    "documents/sections.jsonl": Stage.SEARCH,
    "research_index/chunks.jsonl": Stage.SEARCH,
    "research_index/index_meta.json": Stage.SEARCH,
    "cards/paper_cards.jsonl": Stage.SEARCH,
    "cards/claim_cards.jsonl": Stage.SEARCH,
    "cards/method_cards.jsonl": Stage.SEARCH,
    "cards/dataset_cards.jsonl": Stage.SEARCH,
    "cards/code_links.jsonl": Stage.SEARCH,
    "evidence/evidence_pack.json": Stage.SEARCH,
    "evidence/evidence_pack.md": Stage.SEARCH,
    "evidence/gap_summary.md": Stage.SEARCH,
    "evidence/idea_candidates.jsonl": Stage.SEARCH,
    "evidence/novelty_checks.jsonl": Stage.SEARCH,
    "evidence/experiment_contract.json": Stage.SEARCH,
    "evidence/experiment_contract.md": Stage.SEARCH,
    "evidence/tool_context.json": Stage.SEARCH,
    "evidence/tool_context.md": Stage.SEARCH,
    "evidence/evidence_review.md": Stage.SEARCH,
    "evidence/decision_log.jsonl": Stage.SEARCH,
    "evidence/eval_report.json": Stage.SEARCH,
    "evidence/eval_report.md": Stage.SEARCH,
    "tools/tool_adapter_contract.json": Stage.SEARCH,
    "tools/tool_adapter_contract.md": Stage.SEARCH,
    "tools/tool_trace.jsonl": Stage.SEARCH,
    "tools/external_agent_backend.md": Stage.SEARCH,
    "governance/artifact_retention_policy.json": Stage.SEARCH,
    "governance/artifact_retention_policy.md": Stage.SEARCH,
    "notes.md": Stage.READ,
    "paper_notes.json": Stage.READ,
    "synthesis.md": Stage.SYNTHESIZE,
    "hypothesis.md": Stage.SYNTHESIZE,
    "experiment_plan.json": Stage.DESIGN,
    "results.json": Stage.RUN,
}
