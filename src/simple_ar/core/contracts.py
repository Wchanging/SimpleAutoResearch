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
        outputs=("contract.json", "report.md"),
        description="Scope the topic into a concrete research goal and problem statement.",
    ),
    Stage.SEARCH: StageContract(
        stage=Stage.SEARCH,
        requires=(Stage.PLAN,),
        outputs=("contract.json", "report.md"),
        description="Plan queries, retrieve evidence, and record searchable document state.",
    ),
    Stage.READ: StageContract(
        stage=Stage.READ,
        requires=(Stage.SEARCH,),
        outputs=("contract.json", "report.md"),
        description="Read selected literature and produce structured notes.",
    ),
    Stage.SYNTHESIZE: StageContract(
        stage=Stage.SYNTHESIZE,
        requires=(Stage.READ,),
        outputs=("contract.json", "report.md"),
        description="Synthesize themes and derive a working hypothesis.",
    ),
    Stage.DESIGN: StageContract(
        stage=Stage.DESIGN,
        requires=(Stage.SYNTHESIZE,),
        outputs=("contract.json", "report.md"),
        description="Translate the hypothesis into an executable experiment plan.",
    ),
    Stage.CODE: StageContract(
        stage=Stage.CODE,
        requires=(Stage.DESIGN,),
        outputs=("contract.json", "report.md"),
        description="Generate or prepare experiment code in the run workspace.",
    ),
    Stage.RUN: StageContract(
        stage=Stage.RUN,
        requires=(Stage.CODE,),
        outputs=("contract.json", "report.md"),
        description="Execute the experiment and collect benchmark results.",
    ),
    Stage.REPORT: StageContract(
        stage=Stage.REPORT,
        requires=(Stage.SYNTHESIZE,),
        outputs=("contract.json", "report.md", "references.bib", "report_quality.json"),
        description="Assemble the final report and citation audit artifacts.",
    ),
}


def get_contract(stage: Stage) -> StageContract:
    return CONTRACTS[stage]
