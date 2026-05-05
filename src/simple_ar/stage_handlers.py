from __future__ import annotations

import json
from typing import Any

from simple_ar.artifacts import read_json, read_jsonl, read_text, write_json, write_jsonl, write_text
from simple_ar.llm import LLMClient, LLMError, LLMRequest
from simple_ar.pipeline import Context
from simple_ar.prompts import (
    PLAN_SYSTEM,
    READ_SYSTEM,
    SYNTHESIZE_SYSTEM,
    paper_note_user_prompt,
    plan_user_prompt,
    synthesize_user_prompt,
)


def execute_plan(ctx: Context) -> None:
    client = _llm_client(ctx)
    if client is not None:
        try:
            ctx.emit("stage_message", "Calling LLM for research planning.")
            response = client.ask_json(PLAN_SYSTEM, plan_user_prompt(ctx.topic))
            goal = _text_field(response, "goal_markdown")
            problem = _text_field(response, "problem_markdown")
            if goal and problem:
                write_text(ctx.artifact_path("goal.md"), _ensure_heading(goal, "Research Goal"))
                write_text(ctx.artifact_path("problem.md"), _ensure_heading(problem, "Research Problem"))
                return
        except LLMError as exc:
            ctx.emit("stage_message", f"LLM planning failed; using offline fallback. {exc}")
            pass

    write_text(
        ctx.artifact_path("goal.md"),
        (
            "# Research Goal\n\n"
            f"Topic: {ctx.topic}\n\n"
            "Create a small, reproducible research workflow that can be inspected "
            "stage by stage.\n"
        ),
    )
    write_text(
        ctx.artifact_path("problem.md"),
        (
            "# Research Problem\n\n"
            f"How can we study `{ctx.topic}` with a simple literature-backed "
            "experiment and a transparent artifact pipeline?\n"
        ),
    )


def execute_search(ctx: Context) -> None:
    problem = read_text(ctx.find_artifact("problem.md") or ctx.artifact_path("problem.md"))
    rows = [
        {
            "id": "stub-001",
            "title": "Placeholder Paper for Pipeline Testing",
            "authors": ["SimpleAutoResearch"],
            "abstract": "This placeholder record lets the pipeline validate JSONL artifacts.",
            "url": "https://example.com/stub-001",
            "source": "stub",
            "problem_excerpt": problem[:160],
        }
    ]
    write_jsonl(ctx.artifact_path("papers.jsonl"), rows)


def execute_read(ctx: Context) -> None:
    papers = read_jsonl(ctx.find_artifact("papers.jsonl") or ctx.artifact_path("papers.jsonl"))
    client = _llm_client(ctx)
    if client is not None and papers:
        try:
            notes = _read_paper_notes_with_llm(ctx, client, papers)
            write_json(ctx.artifact_path("paper_notes.json"), notes)
            write_text(ctx.artifact_path("notes.md"), _notes_markdown(notes))
            return
        except LLMError as exc:
            ctx.emit("stage_message", f"LLM reading failed; using offline fallback. {exc}")
            pass

    notes = [
        {
            "paper_id": paper["id"],
            "title": paper.get("title", ""),
            "problem": "Pipeline validation",
            "method": "Placeholder metadata",
            "limitation": "No real literature search has been implemented yet.",
            "relevance": "Confirms artifact passing between stages.",
        }
        for paper in papers
    ]
    write_json(ctx.artifact_path("paper_notes.json"), notes)
    write_text(ctx.artifact_path("notes.md"), _notes_markdown(notes))


def execute_synthesize(ctx: Context) -> None:
    notes = read_text(ctx.find_artifact("notes.md") or ctx.artifact_path("notes.md"))
    paper_notes_path = ctx.find_artifact("paper_notes.json") or ctx.artifact_path("paper_notes.json")
    paper_notes = read_text(paper_notes_path)
    client = _llm_client(ctx)
    if client is not None:
        try:
            ctx.emit("stage_message", "Calling LLM for synthesis.")
            response = client.ask_json(
                SYNTHESIZE_SYSTEM,
                synthesize_user_prompt(notes, paper_notes),
            )
            synthesis = _text_field(response, "synthesis_markdown")
            hypothesis = _text_field(response, "hypothesis_markdown")
            if synthesis and hypothesis:
                write_text(ctx.artifact_path("synthesis.md"), _ensure_heading(synthesis, "Synthesis"))
                write_text(ctx.artifact_path("hypothesis.md"), _ensure_heading(hypothesis, "Hypothesis"))
                return
        except LLMError as exc:
            ctx.emit("stage_message", f"LLM synthesis failed; using offline fallback. {exc}")
            pass

    write_text(
        ctx.artifact_path("synthesis.md"),
        "# Synthesis\n\n"
        "The current skeleton confirms that stage outputs can become later inputs.\n\n"
        f"Notes excerpt:\n\n{notes[:500]}\n",
    )
    write_text(
        ctx.artifact_path("hypothesis.md"),
        "# Hypothesis\n\n"
        "A file-first staged pipeline makes auto-research behavior easier to inspect "
        "and resume than a hidden monolithic agent loop.\n",
    )


def execute_design(ctx: Context) -> None:
    hypothesis = read_text(ctx.find_artifact("hypothesis.md") or ctx.artifact_path("hypothesis.md"))
    write_json(
        ctx.artifact_path("experiment_plan.json"),
        {
            "name": "pipeline_stub",
            "hypothesis": hypothesis.strip(),
            "dataset": "built_in_stub",
            "baseline": "manual_artifact_check",
            "method": "stage_contract_runner",
            "metrics": ["completed_stages"],
        },
    )


def execute_code(ctx: Context) -> None:
    plan = read_json(ctx.find_artifact("experiment_plan.json") or ctx.artifact_path("experiment_plan.json"))
    code = (
        '"""Generated placeholder experiment."""\n\n'
        "def main() -> None:\n"
        f"    print('experiment: {plan['name']}')\n"
        "    print('completed_stages: 8')\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    write_text(ctx.artifact_path("experiment.py"), code)


def execute_run(ctx: Context) -> None:
    experiment_path = ctx.find_artifact("experiment.py")
    write_text(ctx.artifact_path("stdout.txt"), f"validated: {experiment_path}\ncompleted_stages: 8\n")
    write_text(ctx.artifact_path("stderr.txt"), "No subprocess execution in skeleton mode.\n")
    write_json(
        ctx.artifact_path("results.json"),
        {
            "returncode": 0,
            "timed_out": False,
            "metrics": {"completed_stages": 8.0},
            "mode": "stub",
        },
    )


def execute_report(ctx: Context) -> None:
    synthesis = read_text(ctx.find_artifact("synthesis.md") or ctx.artifact_path("synthesis.md"))
    results = read_json(ctx.find_artifact("results.json") or ctx.artifact_path("results.json"))
    papers = read_jsonl(ctx.find_artifact("papers.jsonl") or ctx.artifact_path("papers.jsonl"))
    results_json = json.dumps(results, indent=2, ensure_ascii=False)
    write_text(
        ctx.artifact_path("report.md"),
        (
            f"# SimpleAutoResearch Stub Report\n\n"
            f"## Topic\n\n{ctx.topic}\n\n"
            f"## Synthesis\n\n{synthesis}\n\n"
            f"## Results\n\n```json\n{results_json}\n```\n\n"
            "## Limitations\n\n"
            "This is a skeleton report. Literature search, experiment execution, "
            "and citation verification will be implemented in later days.\n"
        ),
    )
    bib_entries = []
    for paper in papers:
        bib_entries.append(
            "@misc{"
            + str(paper["id"]).replace("-", "_")
            + ",\n"
            + f"  title = {{{paper['title']}}},\n"
            + f"  author = {{{' and '.join(paper['authors'])}}},\n"
            + f"  url = {{{paper['url']}}}\n"
            + "}"
        )
    write_text(ctx.artifact_path("references.bib"), "\n\n".join(bib_entries) + "\n")


HANDLERS = {
    1: execute_plan,
    2: execute_search,
    3: execute_read,
    4: execute_synthesize,
    5: execute_design,
    6: execute_code,
    7: execute_run,
    8: execute_report,
}


def _llm_client(ctx: Context) -> LLMClient | None:
    """Create an LLM client for a stage when LLM mode is enabled.

    Args:
        ctx: Current pipeline context containing runtime configuration.

    Returns:
        Configured client, or ``None`` when offline fallback should be used.
    """
    if ctx.config.get("use_llm") is not True:
        return None
    model_value = ctx.config.get("model")
    model = str(model_value) if model_value else None
    try:
        return LLMClient.from_env(model=model)
    except LLMError as exc:
        ctx.emit("stage_message", f"LLM unavailable; using offline fallback. {exc}")
        return None


def _read_paper_notes_with_llm(
    ctx: Context,
    client: LLMClient,
    papers: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Create one structured note per paper using concurrent LLM requests.

    Args:
        ctx: Current pipeline context.
        client: Configured LLM client.
        papers: Paper metadata loaded from ``papers.jsonl``.

    Returns:
        Normalized paper notes suitable for ``paper_notes.json``.
    """
    requests = [
        LLMRequest(
            system=READ_SYSTEM,
            user=paper_note_user_prompt(json.dumps(paper, indent=2, ensure_ascii=False)),
            label=_paper_id(paper, index),
        )
        for index, paper in enumerate(papers, start=1)
    ]
    workers = min(_llm_max_workers(ctx), len(requests))
    ctx.emit(
        "stage_message",
        f"Calling LLM for {len(requests)} paper note(s) with {workers} worker(s).",
    )
    responses = client.ask_json_many(requests, max_workers=workers)
    return [
        _normalize_paper_note(paper, response, index)
        for index, (paper, response) in enumerate(zip(papers, responses), start=1)
    ]


def _normalize_paper_note(
    paper: dict[str, Any],
    response: dict[str, Any],
    index: int,
) -> dict[str, str]:
    """Merge model output with source metadata into a stable note schema."""
    return {
        "paper_id": _text_field(response, "paper_id") or _paper_id(paper, index),
        "title": str(paper.get("title", "")),
        "problem": _text_field(response, "problem") or "Not specified.",
        "method": _text_field(response, "method") or "Not specified.",
        "limitation": _text_field(response, "limitation") or "Not specified.",
        "relevance": _text_field(response, "relevance") or "Not specified.",
    }


def _notes_markdown(notes: list[dict[str, str]]) -> str:
    """Render structured paper notes as inspectable Markdown."""
    lines = ["# Literature Notes", ""]
    for note in notes:
        lines.append(f"## {note['paper_id']}")
        if note.get("title"):
            lines.append(f"Title: {note['title']}")
        lines.extend(
            [
                f"- Problem: {note['problem']}",
                f"- Method: {note['method']}",
                f"- Limitation: {note['limitation']}",
                f"- Relevance: {note['relevance']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _paper_id(paper: dict[str, Any], index: int) -> str:
    """Return a stable paper identifier for prompts and generated notes."""
    value = paper.get("id")
    return str(value) if value else f"paper-{index:03d}"


def _llm_max_workers(ctx: Context) -> int:
    """Read the configured LLM worker limit, falling back to a safe default."""
    value = ctx.config.get("llm_max_workers", 4)
    try:
        workers = int(value)
    except (TypeError, ValueError):
        workers = 4
    return max(1, workers)


def _text_field(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def _ensure_heading(markdown: str, heading: str) -> str:
    stripped = markdown.strip()
    if stripped.startswith("#"):
        return stripped + "\n"
    return f"# {heading}\n\n{stripped}\n"
