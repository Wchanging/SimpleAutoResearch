from __future__ import annotations

from simple_ar.artifacts import read_json, read_jsonl, read_text, write_json, write_jsonl, write_text
from simple_ar.pipeline import Context


def execute_plan(ctx: Context) -> None:
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
            "abstract": "This placeholder record lets Day 2 validate JSONL artifacts.",
            "url": "https://example.com/stub-001",
            "source": "stub",
            "problem_excerpt": problem[:160],
        }
    ]
    write_jsonl(ctx.artifact_path("papers.jsonl"), rows)


def execute_read(ctx: Context) -> None:
    papers = read_jsonl(ctx.find_artifact("papers.jsonl") or ctx.artifact_path("papers.jsonl"))
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
    write_text(
        ctx.artifact_path("synthesis.md"),
        "# Synthesis\n\n"
        "The current Day 2 stub confirms that stage outputs can become later inputs.\n\n"
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
            "name": "day2_pipeline_stub",
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
        '"""Generated Day 2 placeholder experiment."""\n\n'
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
    write_text(ctx.artifact_path("stderr.txt"), "No subprocess execution in Day 2 stub.\n")
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
    write_text(
        ctx.artifact_path("report.md"),
        (
            f"# SimpleAutoResearch Stub Report\n\n"
            f"## Topic\n\n{ctx.topic}\n\n"
            f"## Synthesis\n\n{synthesis}\n\n"
            f"## Results\n\n```json\n{results}\n```\n\n"
            "## Limitations\n\n"
            "This is a Day 2 skeleton report. Literature, LLM, experiment execution, "
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
