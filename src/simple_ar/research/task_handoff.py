"""Render the selected research direction for existing CodeTask consumers."""

from simple_ar.research.contracts import ResearchExperimentContract


def research_handoff_text(
    contract: ResearchExperimentContract,
    *,
    execution_context: str = "",
) -> str:
    """Append the selected research direction without replacing the task."""

    lines = [
        "## Research handoff",
        "",
        "The following context comes from the evidence-to-design handoff. The",
        "original task, configured benchmark, and project interfaces remain the",
        "acceptance authority.",
        "",
        "### Hypothesis",
        contract.hypothesis,
        "",
        "### Proposed change",
        contract.proposed_change or "Use the smallest evidence-supported change.",
        "",
    ]
    if execution_context.strip():
        lines.extend(
            [
                "### Prepared experiment boundary (authoritative)",
                execution_context.strip(),
                "",
                "### Literature motivation (not executable configuration)",
                f"- Motivation references: {', '.join(contract.motivation_refs) or 'not specified'}",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "### Experimental context",
                f"- Baseline: {contract.baseline}",
                f"- Dataset: {contract.dataset}",
                f"- Metrics: {', '.join(contract.metrics) or 'use configured metrics'}",
                "",
            ]
        )
    lines.extend(
        [
            "### Validation guidance",
            *[f"- {item}" for item in contract.validation_hints],
            "",
        ]
    )
    return "\n".join(lines)
