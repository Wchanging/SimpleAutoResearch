# Survey Reviewer Criteria

## Review Goal

Check whether a research-only survey is evidence-bound, useful, and honest
about scope.

## Required Checks

- Every paper-specific claim must have a nearby citation from the current run.
- The report must not imply that experiments were executed.
- Novelty statements must be phrased as risk hints, gaps, or hypotheses.
- Coverage and full-text limitations should be visible only in the Limitations
  section, not as pipeline internals or artifact paths.
- The report should not contain operational sections such as "Search Scope",
  "Evidence Summary", "Pipeline", "Artifact", or "Stage Outputs".
- The body should use survey-style sections: method families, evaluation /
  benchmarks, design patterns, gaps, limitations, and conclusion.
- Method Families must contain a real taxonomy or comparison frame, not a
  chronological or per-paper note dump.
- Text after a taxonomy table should explain cross-family contrasts and
  boundary conditions, not repeat every row.
- Each major technical paragraph should compare at least two works, method
  families, assumptions, or evaluation settings. Flag isolated "paper brief"
  paragraphs unless they introduce a milestone work.
- Evaluation / benchmark sections should include a compact evidence-quality map
  when the available sources support it.
- Claims about performance or usefulness should include a boundary condition,
  such as benchmark type, task scale, repository-level transfer risk, or cost.
- Each section should be readable: avoid one very large paragraph. Prefer
  2-4 short paragraphs or concise bullets when comparing papers.
- Design-pattern sections should use subheadings or bullets when they otherwise
  become long dense paragraphs.
- Related Work should group papers by meaningful roles, not list them as a log.
- Unsupported broad claims should be weakened or moved to Open Gaps.
- Front matter should reflect the body: Abstract and Introduction should not be
  generic background repeated from the topic prompt.
- The report must not contain prompt/planning language such as "Hint:",
  "Use this paper as", "Additional synthesis detail", or "Paper Brief".

## Output Expectations

Reviewer findings should be structured by severity and suggested action. Do not
rewrite the report directly unless the coordinator asks for a revision.
