# Survey Reviewer Criteria

## Review Goal

Check whether a research-only survey is evidence-bound, useful, and honest
about scope.

## Required Checks

- Every paper-specific claim must have a nearby citation from the current run.
- The report must not imply that experiments were executed.
- Novelty statements must be phrased as risk hints, gaps, or hypotheses.
- Coverage and full-text limitations must be clear near consequential claims,
  with fuller qualifications consolidated rather than repeated. They are not
  pipeline internals or artifact paths.
- The report should not contain operational sections such as "Search Scope",
  "Evidence Summary", "Pipeline", "Artifact", or "Stage Outputs".
- Text after a taxonomy table should explain cross-family contrasts and
  boundary conditions, not repeat every row.
- Evaluation comparisons must explain consequential differences in conditions
  and evidence strength. Prose or a table can satisfy this requirement; a named
  evidence-quality map is not required.
- Claims about performance or usefulness should include a boundary condition,
  such as benchmark type, task scale, cross-dataset or domain transfer risk, or cost.
- Each section should be readable: avoid one very large paragraph. Prefer
  2-4 short paragraphs or concise bullets when comparing papers.
- Design-pattern sections should use subheadings or bullets when they otherwise
  become long dense paragraphs.
- Related Work should group papers by meaningful roles, not list them as a log.
- Unsupported broad claims should be weakened or explicitly left as open gaps.
- Front matter should reflect the body: Abstract and Introduction should not be
  generic background repeated from the topic prompt.
- The report must not contain prompt/planning language such as "Hint:",
  "Use this paper as", "Additional synthesis detail", or "Paper Brief".

## Default Structure

These are fallback organization suggestions; an adapted document plan owns the
actual section responsibilities. They are not additional evidence requirements.

- The body should use survey-style sections: method families, evaluation /
  benchmarks, design patterns, gaps, limitations, and conclusion.
- Method Families should contain a supported taxonomy or comparison frame,
  not a chronological or per-paper note dump.
- Comparison paragraphs can contrast works, assumptions or evaluation settings;
  an individual result may be explained on its own when that serves the question.

## Output Expectations

Reviewer findings should be structured by severity and suggested action. Do not
rewrite the report directly unless the coordinator asks for a revision.
Judge whether the user's questions are answered and important claims supported.
Formatting preferences (table presence, heading wording, paragraph count) are
optional suggestions unless explicitly requested or necessary for comprehension.
Do not require another revision solely to impose the fallback structure.
