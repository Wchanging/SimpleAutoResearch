from __future__ import annotations

from dataclasses import dataclass

from simple_ar.stages import Stage


@dataclass(frozen=True)
class StageContract:
    """Defines the I/O requirements for a stage, acting as a mandatory physical constraint."""
    stage: Stage
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    description: str


CONTRACTS: dict[Stage, StageContract] = {
    Stage.PLAN: StageContract(
        stage=Stage.PLAN,
        inputs=(),
        outputs=("goal.md", "problem.md"),
        description="Scope the topic and research question.",
    ),
    Stage.SEARCH: StageContract(
        stage=Stage.SEARCH,
        inputs=("problem.md",),
        outputs=(
            "research_questions.json",
            "query_plan.json",
            "source_plan.json",
            "retrieval_rounds.jsonl",
            "screening_decisions.jsonl",
            "papers.jsonl",
            "search_meta.json",
        ),
        description="Plan research questions, retrieve candidates, screen metadata, and collect papers.",
    ),
    Stage.READ: StageContract(
        stage=Stage.READ,
        inputs=("papers.jsonl",),
        outputs=("notes.md", "paper_notes.json"),
        description="Create literature notes.",
    ),
    Stage.SYNTHESIZE: StageContract(
        stage=Stage.SYNTHESIZE,
        inputs=("notes.md", "paper_notes.json"),
        outputs=("synthesis.md", "hypothesis.md"),
        description="Synthesize themes and hypotheses.",
    ),
    Stage.DESIGN: StageContract(
        stage=Stage.DESIGN,
        inputs=("hypothesis.md",),
        outputs=("experiment_plan.json",),
        description="Design a small experiment.",
    ),
    Stage.CODE: StageContract(
        stage=Stage.CODE,
        inputs=("experiment_plan.json",),
        outputs=("experiment.py",),
        description="Generate experiment code.",
    ),
    Stage.RUN: StageContract(
        stage=Stage.RUN,
        inputs=("experiment.py",),
        outputs=("results.json", "stdout.txt", "stderr.txt"),
        description="Run the experiment and collect metrics.",
    ),
    Stage.REPORT: StageContract(
        stage=Stage.REPORT,
        inputs=("synthesis.md", "papers.jsonl"),
        outputs=("report.md", "references.bib", "manifest.json", "report_quality.json"),
        description="Write the final report.",
    ),
}


def get_contract(stage: Stage) -> StageContract:
    return CONTRACTS[stage]
