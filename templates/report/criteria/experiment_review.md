# Experiment Reviewer Criteria

## Review Goal

Check whether an experiment or code-task report connects claims to recorded
metrics, source artifacts, and citations.

## Required Checks

- Reported numbers must match recorded metrics, comparison, or result artifacts.
- Baseline, patched, delta, passed, failed, and inconclusive outcomes must not
  be confused.
- Toy or local benchmarks must not be generalized beyond their scope.
- Code-task claims must describe recorded patch and validation evidence only.
- Literature citations should support motivation or related work, not local
  benchmark outcomes unless the paper actually reports that outcome.
- The conclusion must not introduce new results.
- Limitations should include runtime, data, hardware, and search/evidence
  boundaries when available.

## Default Structure

These headings are a fallback, not an additional plan or permission to invent
missing experimental evidence. An adapted plan owns the section responsibilities.

- The report should contain a recognizable Abstract, Introduction, Method,
  Experimental Setup, Results, Discussion, Limitations, and Conclusion.

## Output Expectations

Reviewer findings should identify unsupported claims, missing metric
provenance, citation misuse, and missing limitations.

