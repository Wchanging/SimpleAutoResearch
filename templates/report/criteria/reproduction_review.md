# Reproduction Reviewer Criteria

## Review Goal

Check whether a reproduction or ablation report separates source-paper claims
from local evidence and records environment limits.

## Required Checks

- Source-paper results and local reproduced results must be clearly separated.
- Local setup must explain material method and comparison conditions, environment,
  execution limits and seed policy from available evidence. Full commands and
  absolute paths may remain in the existing reproduction attachments when the
  setup identifies them; do not require their duplication in the body unless
  the user explicitly asks.
- Attribute producer-reported software or hardware separately from executor
  observations. An unread or truncated output preview does not establish that
  runtime details were not recorded; consult the registered attachment before
  making a missing-evidence finding.
- Failed runs must be reported as failures or partial reproductions, not hidden.
- Ablation claims must map to recorded metric rows or comparison artifacts.
- Missing dependencies, data, checkpoints, or GPU resources must be disclosed.
- Speculative explanations should be labelled as hypotheses.

## Output Expectations

Reviewer findings should focus on reproducibility, metric provenance, and
overclaim control.

