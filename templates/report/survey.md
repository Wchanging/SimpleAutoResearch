# Survey Report Template

## Intended Use

Use this template for research-only runs that do not execute experiments. The
report should read like a compact academic survey: synthesize a research area,
compare methods, identify gaps, and keep novelty claims conservative.

Do not write this as a pipeline run log. Search provenance, artifact paths,
tool internals, and extraction/debug details belong in `manifest.json`,
`report_audit.json`, or `report_memory.json`, not in the report body.

## Writing Workflow

Draft evidence-heavy sections before front matter. A good default order is:
Method Families, Evaluation And Benchmarks, Design Patterns And Failure Modes,
Research Gaps And Opportunities, Limitations, Conclusion, Introduction And
Scope, then Abstract / Executive Summary. The final report should still render
in the section order below.

Draft order: Method Families -> Evaluation And Benchmarks -> Design Patterns And Failure Modes -> Research Gaps And Opportunities -> Limitations -> Conclusion -> Introduction And Scope -> Abstract / Executive Summary

When the source budget allows it, consider the full selected source set before
writing synthesis-heavy sections. If the source set is large, group papers by
method family and evidence role instead of expanding report length linearly.

## Abstract / Executive Summary

Write this after the body is drafted. Keep it compact, ideally one paragraph
of roughly 120-180 words. Open with the core research problem and the main
evidence gap, then summarize the taxonomy and practical implication. Do not
imply that an experiment was run. Do not describe the report generation
process.

## Introduction And Scope

Introduce the topic as a research problem. Explain the intellectual boundary
of the survey in prose, not as a command log.

## Method Families

Build a taxonomy rather than a paper-by-paper list. Start with a compact table
or bullet taxonomy that maps papers onto 2-3 comparison dimensions, such as
learning or inference strategy, assumptions, required data or resources, and
evaluation protocol. Choose dimensions supported by this topic's evidence,
not a fixed taxonomy from another research field. After the table, focus prose on cross-family contrasts,
assumptions, and boundary conditions; do not simply restate each table row. Do
not write paragraphs of the form "Paper A does X, Paper B does Y" unless the
paper is a clear milestone.

## Evaluation And Benchmarks

Compare how the papers evaluate their systems. Discuss benchmark type, task
scale, metrics, and evidence strength. Distinguish the actual evaluation settings,
data splits, baselines, and generalization boundaries reported by the sources. Avoid
fabricating results. Include a compact evidence-quality map when enough
information is available, for example columns such as method family, benchmark
scale, baseline comparison, cost/budget control, and evidence strength.

## Design Patterns And Failure Modes

Synthesize recurring design patterns, trade-offs, and failure modes. Make clear
when evidence is suggestive rather than conclusive. Every strong claim should
include a boundary statement that explains where the claim may not transfer.
Use short subheadings grounded in the topic's recurring methodological choices,
resource trade-offs, and observed failure modes.

## Research Gaps And Opportunities

State open questions and practical research opportunities. Frame candidate
ideas as hypotheses or future work, not established findings. Avoid copying the
hypothesis artifact verbatim; integrate it into a broader research agenda.

## Limitations

State survey limitations in reader-facing terms: partial coverage, metadata or
full-text gaps, possible missed papers, and lack of new experiment results.
Frame forward-looking ideas as testable directions rather than established
facts, without over-apologizing for normal survey-level inference.

## Conclusion

Close with the main takeaway and the most actionable next step.
