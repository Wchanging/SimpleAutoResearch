# ARC-Bench Adapter Contract

This directory is a local benchmark adapter, not part of the public
SimpleAutoResearch package surface.

The adapter exists to translate between ARC-Bench's benchmark contract and
SimpleAutoResearch's code-task contract. It should stay replaceable: another
benchmark suite should be able to add a sibling adapter without changing this
one or adding suite-specific branches to the core pipeline.

## Inputs

The adapter reads ARC-Bench assets from a configurable root:

- topic manifests, usually under `config/<domain>/manifests/`;
- rubrics, usually under `config/<domain>/rubrics/`;
- optional external judge commands supplied by the user or server environment.

The adapter must not assume that `AutoResearchClaw/` lives inside this
repository. Use `--arc-root`, `[arc].arc_root`, or `ARC_BENCH_ROOT`.

## Prepared Package

`prepare` and `prepare-ml` produce a SimpleAutoResearch code-task package:

```text
prepared/<topic>/
  task.md
  code_task.toml
  manifest.json
  rubric.json
  arc_meta.json
  commands.md
```

The generated TOML should remain small. Topic-specific scientific details
belong in `task.md`; runtime settings belong in `code_task.toml`.

## SimpleAutoResearch Run

SimpleAutoResearch is responsible for:

- creating the workspace;
- generating or modifying code;
- validating and running the benchmark command;
- repairing failures;
- recording memory, review, logs, and run artifacts.

The ARC adapter must not bypass these gates.

## Finalized Submission

`finalize` projects a completed run into:

```text
submission/
  code/
  results/metrics.json
  README.md
  claims.json
  reproduce.sh
stage-14/
  experiment_summary.json
arc_adapter_meta.json
```

`finalize` must not overwrite a non-empty output directory unless the user
passes `--force`.

`finalize` also writes a generic `result_analysis/` folder. This folder is
produced through SimpleAutoResearch's benchmark-agnostic result-analysis layer:

```text
result_analysis/
  analysis_context.json
  metric_summary.json
  claims.json
  analysis_report.md
  analysis_audit.json
```

When `--analyze` is passed, the adapter may call the configured LLM through the
generic result-analysis layer and use the analyzed README/claims in the
submission. Without `--analyze`, the analysis remains deterministic and does
not spend model tokens.

## Judge Wrapper

`judge` runs a user-supplied external command. It only records:

- resolved command;
- return code;
- stdout/stderr;
- timeout status;
- output paths.

It does not interpret judge semantics. Any deeper analysis should go through a
generic result-analysis layer rather than ARC-specific core code.

## Core Boundary

Keep these ARC-specific concepts out of `src/simple_ar`:

- ARC task IDs such as `ML02`;
- ARC rubric leaf IDs and category names;
- ARC submission directory names;
- ARC judge CLI details;
- task-specific fallback files, metrics, or dataset names.

Only promote a feature into core when it is benchmark-agnostic, such as metric
normalization, claim grounding, result analysis, run failure diagnosis, or
artifact references. ARC-specific projection stays in this adapter.
