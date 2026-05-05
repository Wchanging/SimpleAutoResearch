from __future__ import annotations

import json

from simple_ar.artifacts import read_json, read_jsonl, read_text, write_json, write_jsonl, write_text
from simple_ar.llm import LLMClient, LLMError
from simple_ar.pipeline import Context
from simple_ar.prompts import (
    PLAN_SYSTEM,
    READ_SYSTEM,
    SYNTHESIZE_SYSTEM,
    plan_user_prompt,
    read_user_prompt,
    synthesize_user_prompt,
)


def execute_plan(ctx: Context) -> None:
    client = _llm_client(ctx)
    if client is not None:
        try:
            response = client.ask_json(PLAN_SYSTEM, plan_user_prompt(ctx.topic))
            goal = _text_field(response, "goal_markdown")
            problem = _text_field(response, "problem_markdown")
            if goal and problem:
                write_text(ctx.artifact_path("goal.md"), _ensure_heading(goal, "Research Goal"))
                write_text(ctx.artifact_path("problem.md"), _ensure_heading(problem, "Research Problem"))
                return
        except LLMError:
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
    if client is not None:
        try:
            response = client.ask_json(
                READ_SYSTEM,
                read_user_prompt(json.dumps(papers, indent=2, ensure_ascii=False)),
            )
            notes_markdown = _text_field(response, "notes_markdown")
            paper_notes = response.get("paper_notes")
            if notes_markdown and isinstance(paper_notes, list):
                write_json(ctx.artifact_path("paper_notes.json"), paper_notes)
                write_text(ctx.artifact_path("notes.md"), _ensure_heading(notes_markdown, "Literature Notes"))
                return
        except LLMError:
            pass

    notes = [
        {
            "paper_id": paper["id"],
            "problem": "Pipeline validation",
            "method": "Placeholder metadata",
            "relevance": "Confirms artifact passing between stages.",
        }
        for paper in papers
    ]
    write_json(ctx.artifact_path("paper_notes.json"), notes)
    write_text(
        ctx.artifact_path("notes.md"),
        "# Literature Notes\n\n"
        + "\n".join(f"- `{note['paper_id']}`: {note['relevance']}" for note in notes)
        + "\n",
    )


def execute_synthesize(ctx: Context) -> None:
    notes = read_text(ctx.find_artifact("notes.md") or ctx.artifact_path("notes.md"))
    paper_notes_path = ctx.find_artifact("paper_notes.json") or ctx.artifact_path("paper_notes.json")
    paper_notes = read_text(paper_notes_path)
    client = _llm_client(ctx)
    if client is not None:
        try:
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
        except LLMError:
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
    if ctx.config.get("use_llm") is not True:
        return None
    model_value = ctx.config.get("model")
    model = str(model_value) if model_value else None
    try:
        return LLMClient.from_env(model=model)
    except LLMError:
        return None


def _text_field(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def _ensure_heading(markdown: str, heading: str) -> str:
    stripped = markdown.strip()
    if stripped.startswith("#"):
        return stripped + "\n"
    return f"# {heading}\n\n{stripped}\n"
