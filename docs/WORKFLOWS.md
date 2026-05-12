# Workflows And Artifacts

This document explains what SimpleAutoResearch is doing internally: workflow presets, pipeline stages, stage outputs, and run artifact layout. For concrete commands, see [Usage And Configuration](USAGE.md).

## Workflow Presets

The current 8-stage pipeline is one preset, not the whole architecture. SimpleAutoResearch should stay module-first so literature review, code improvement, experiment execution, and report writing can be recombined.

### 1. Research Report

Use this when the user wants a literature review, survey, or DeepResearch-like report without code execution.

Conceptual flow:

```text
plan -> search -> read -> synthesize -> report
```

Examples:

- Write a survey about agent simulation.
- Compare recent retrieval-augmented generation papers.
- Produce a no-code technical brief with verified citations.

Current status:

- Partially supported by the default research pipeline.
- A true no-code `survey` or `review` preset is planned.
- Today, `run --to-stage report` still includes design/code/run because V1 was built as a full topic-to-report-with-experiment teaching demo.

### 2. Code Task

Use this when the user already has code and wants a focused modification, optimization, repair, or benchmark improvement.

Conceptual flow:

```text
init workspace -> index code -> plan patch -> approve
-> propose edits -> apply edits -> validate -> run benchmark
-> analyze failure -> repair proposal
```

Examples:

- Improve the time complexity of a baseline algorithm while keeping its public API.
- Add an ablation to an existing benchmark repo.
- Fix an experiment script and rerun its tests.
- Reduce runtime under a resource budget.

Current status:

- Supported as a standalone workflow through `simple-ar code-task ...`.
- The source project is copied into `code_task/workspace`; original code is not modified.
- Current patch mode uses conservative old/new replacements.
- Validator, benchmark runner, failure analysis, and bounded repair proposals are available.

### 3. Research With Experiment

Use this when the user wants a research idea to become an executable experiment and a result-backed report.

Conceptual flow:

```text
plan -> search -> read -> synthesize -> design experiment
-> code task or template codegen -> run benchmark -> analyze results -> report
```

Examples:

- Form a hypothesis from papers, then test it on a small reproducible benchmark.
- Take an existing baseline repo and implement one improvement suggested by the literature.
- Generate a small controlled experiment when no existing code is provided.

Current status:

- Supported in a narrow teaching form through the default 8-stage pipeline.
- `06-code` normally generates a whitelisted template experiment.
- `--experiment-template llm_code_task_toy_spam` is an experimental embedded
  handoff into `code-task`: it copies the bundled toy spam project, asks the LLM
  for a patch plan and controlled edits, applies them inside the isolated run
  workspace, and lets `07-run` execute the benchmark harness.
- Future versions should generalize this from one bundled demo into
  user-provided code roots and config-driven experiment presets.

## Default 8-Stage Pipeline

```text
01 plan        Scope the topic and research question
02 search      Collect real paper metadata
03 read        Create literature notes from paper metadata
04 synthesize  Summarize themes and propose a testable hypothesis
05 design      Create a small experiment plan
06 code        Generate experiment code or prepare an embedded code task
07 run         Execute the experiment and parse metrics
08 report      Write a final Markdown report with references
```

| Stage | Main outputs | Purpose |
| --- | --- | --- |
| `plan` | `goal.md`, `problem.md` | Scope the topic into a concrete research question. |
| `search` | `papers.jsonl`, `search_meta.json` | Collect normalized OpenAlex/arXiv paper metadata or explicit offline fixture metadata. |
| `read` | `paper_notes.json`, `notes.md` | Convert paper metadata into structured notes. |
| `synthesize` | `synthesis.md`, `hypothesis.md` | Produce a bounded synthesis and testable hypothesis. |
| `design` | `experiment_plan.json` | Select a safe experiment template and parameters. |
| `code` | `experiment.py` | Generate code from the selected template or prepare an embedded code-task harness. |
| `run` | `results.json`, `stdout.txt`, `stderr.txt` | Execute the experiment and parse numeric metrics. |
| `report` | `report.md`, `references.bib`, `manifest.json`, `report_quality.json` | Write a paper-like report and reproducibility package. |

## Research Run Artifacts

A completed research run can contain these files, depending on enabled options:

```text
runs/<run-id>/
  manifest.json
  pipeline_state.json
  config_snapshot.json
  topic.txt
  llm_usage.jsonl
  llm_usage_summary.json
  artifact_index.json
  artifact_chunks.jsonl
  artifact_search_results.json  # only after explicit search-artifacts usage
  source_plan.json
  activity_log.jsonl
  evidence_ledger.jsonl
  01-plan/
  02-search/
  03-read/
  04-synthesize/
  05-design/
  06-code/
    code_task_experiment.json
    code_task_run/
  07-run/
  08-report/
```

Root-level files:

- `manifest.json`: stage status and declared outputs.
- `pipeline_state.json`: last completed stage and next stage for resume.
- `config_snapshot.json`: selected runtime configuration.
- `llm_usage.jsonl`: one row per successful LLM request.
- `llm_usage_summary.json`: aggregate token counts and optional cost estimate.
- `artifact_index.json`: local artifact index generated by `inspect` or `search-artifacts`.
- `artifact_chunks.jsonl`: line-addressable chunks generated for local retrieval.
- `artifact_search_results.json`: last artifact search result; generated only by the artifact search command.
- `source_plan.json`: source plan describing which artifacts each stage should consult.
- `activity_log.jsonl`: structured activity log for source planning and retrieval actions.
- `evidence_ledger.jsonl`: snippets used by stages, with path and line range.
- `06-code/code_task_experiment.json`: present only for the embedded
  `llm_code_task_toy_spam` template; summarizes the nested code-task patch.

Report-stage files:

- `08-report/report.md`: final Markdown report.
- `08-report/references.bib`: BibTeX for papers cited in the report body.
- `08-report/manifest.json`: report package and reproducibility metadata.
- `08-report/report_quality.json`: rule-based checks for citations, metrics, and visible runtime limits.

## Code Task Artifacts

Code-task artifacts stay under `code_task/`:

```text
runs/<run-id>/
  manifest.json
  code_task/
    task.md
    summary.md
    patch_plan.md
    patch.diff
    workspace/
    meta/
      codebase_index.json
      hitl_decisions.jsonl
      proposed_edits.json
      applied_edits.json
      validation_report.json
      llm_usage.jsonl
      llm_usage_summary.json
    run/
      execution_report.json
      stdout.txt
      stderr.txt
      metrics.json
      failure_analysis.md
    repairs/
      repair-001/
        proposed_edits.json
```

Important directories:

- `workspace/`: editable copy of the source project.
- `meta/`: indexes, decisions, proposed edits, applied edit summaries, validation reports, and LLM usage.
- `run/`: latest benchmark stdout/stderr, execution report, parsed metrics, and failure analysis.
- `repairs/`: bounded repair proposals grouped by attempt.

Important user-facing code-task files:

- `summary.md`: compact status, task, patch, validation, benchmark, and failure-analysis summary.
- `patch_plan.md`: human-reviewable plan before edits.
- `patch.diff`: applied patch for review.
- `meta/applied_edits.json`: changed files plus before/after hashes for the files touched by the patch.

## Why This Split Matters

The split keeps the project from becoming one rigid pipeline.

- If the user wants a survey, code stages should be skipped.
- If the user wants to optimize existing code, literature stages should be optional.
- If the user wants a full automatic-research loop, modules can be composed.
- Each module can be upgraded independently.

This follows one practical lesson from AutoResearchClaw: complex behavior is easier to control when it is exposed as workflow modes and capabilities, not as one ever-growing sequence of flags.
