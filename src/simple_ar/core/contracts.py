from __future__ import annotations

from dataclasses import dataclass

from simple_ar.core.stages import Stage


@dataclass(frozen=True)
class StageContract:
    """State-driven contract for one pipeline stage."""

    stage: Stage
    requires: tuple[Stage, ...]
    outputs: tuple[str, ...]
    description: str


CONTRACTS: dict[Stage, StageContract] = {
    Stage.PLAN: StageContract(
        stage=Stage.PLAN,
        requires=(),
        outputs=("goal.md", "problem.md"),
        description="Scope the topic into a concrete research goal and problem statement.",
    ),
    Stage.SEARCH: StageContract(
        stage=Stage.SEARCH,
        requires=(Stage.PLAN,),
        outputs=("papers.jsonl", "search_meta.json", "documents/documents.jsonl", "research_index/index_meta.json"),
        description="Plan and execute source search; retain metadata, full-text extraction state, and search index chunks.",
    ),
    Stage.READ: StageContract(
        stage=Stage.READ,
        requires=(Stage.SEARCH,),
        outputs=("notes.md", "paper_notes.json"),
        description="Screen selected records and produce canonical Paper Briefs for synthesis.",
    ),
    Stage.SYNTHESIZE: StageContract(
        stage=Stage.SYNTHESIZE,
        requires=(Stage.READ,),
        outputs=("synthesis.md", "hypothesis.md", "synthesis_brief.json"),
        description="Synthesize Paper Briefs into themes, gaps, bounded ideas, and a working hypothesis.",
    ),
    Stage.DESIGN: StageContract(
        stage=Stage.DESIGN,
        requires=(Stage.SYNTHESIZE,),
        outputs=("experiment_plan.json", "experiment_contract.json", "result_schema.json", "resource_plan.json"),
        description="Translate the synthesized hypothesis into an experiment plan and research-to-code handoff contract.",
    ),
    Stage.CODE: StageContract(
        stage=Stage.CODE,
        requires=(Stage.DESIGN,),
        outputs=("experiment.py",),
        description="Generate or prepare experiment code in the run workspace.",
    ),
    Stage.RUN: StageContract(
        stage=Stage.RUN,
        requires=(Stage.CODE,),
        outputs=("results.json", "guard_report.json", "diagnosis.json", "stdout.txt", "stderr.txt"),
        description="Execute the experiment and collect benchmark results.",
    ),
    Stage.REPORT: StageContract(
        stage=Stage.REPORT,
        requires=(Stage.SYNTHESIZE,),
        outputs=(
            "report.md",
            "references.bib",
            "report_quality.json",
            "manifest.json",
        ),
        description="Assemble the final report and citation audit artifacts.",
    ),
}


def get_contract(stage: Stage) -> StageContract:
    return CONTRACTS[stage]
