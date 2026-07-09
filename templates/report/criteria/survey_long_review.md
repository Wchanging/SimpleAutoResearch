# Long Survey Reviewer Criteria

## Review Goal

Check whether a research-only long survey is broad, evidence-bound,
reader-oriented, and structured like an academic survey rather than a compact
technical report or a pipeline log.

## Required Checks

- The survey must not describe SimpleAutoResearch internals, artifact paths,
  stage numbers, command provenance, or prompt/tool implementation details.
- The section structure should cover foundations/taxonomy, system construction,
  applications/domains, evaluation/benchmarks, related surveys or neighboring
  fields, challenges, future directions, and conclusion.
- The report should be broad enough for a survey. Flag sections that are too
  short, omit major subareas, or rely on only one or two papers when more source
  evidence is available.
- Each major section should synthesize across papers, method families, domains,
  datasets, benchmarks, or assumptions. Flag isolated paper-summary paragraphs
  unless they introduce a milestone work.
- Every paper-specific claim must have a nearby citation from the current run.
- Taxonomy and benchmark/application sections should include compact comparison
  tables when evidence supports them.
- Figure-ready conceptual diagrams are encouraged as Mermaid or structured
  diagram specs. Flag fake Markdown image links when no real image artifact is
  referenced.
- Evaluation discussions should mention datasets, benchmarks, metrics,
  baselines, comparability, cost/resource signals, and reproducibility when the
  source set supports those points.
- Related-survey sections should explain positioning and differences, not merely
  list prior surveys.
- Challenges and future directions should be specific and testable; avoid
  generic "more research is needed" filler.
- Unsupported broad claims should be weakened, moved to open problems, or tied
  to a boundary condition.
- The report should remain readable: prefer clear subheadings, compact tables,
  and paragraphs under roughly 140 words.
- Abstract and Introduction should reflect the completed body, not generic
  background repeated from the topic prompt.
- The report must not contain prompt residue such as "Hint:", "Use this paper
  as", "Additional synthesis detail", "Paper Brief", or "Source handle".

## Output Expectations

Reviewer findings should be structured by severity and suggested action. Do not
rewrite the report directly unless the coordinator asks for a revision.
