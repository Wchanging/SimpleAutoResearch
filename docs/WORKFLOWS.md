# Workflows And Artifacts

This document explains what SimpleAutoResearch is doing internally: workflow presets, pipeline stages, stage outputs, and run artifact layout. For concrete commands, see [CLI Reference](CLI_REFERENCE.md); for setup and walkthroughs, see [Usage And Configuration](USAGE.md).

## Workflow Presets

The current 8-stage pipeline is one preset, not the whole architecture. SimpleAutoResearch stays module-first so literature review, code improvement, experiment execution, and report writing can be recombined.

### 1. Research Report (Literature-First)

Use this when you want a literature review, survey, or DeepResearch-like report without emphasizing experiments.

Conceptual flow:

```text
plan -> search -> read -> synthesize -> report
```

Reality check today:

- `run --to-stage report` still executes design/code/run stages because the default pipeline is a teaching demo.
- For a pure literature pass, stop at `synthesize`, then resume `report`; `auto`
  mode will produce a research-only report because no `results.json` exists.

### 2. Code Task (Existing Codebase)

Use this when you already have code and want a focused modification, optimization, repair, or benchmark improvement.

Conceptual flow:

```text
init workspace -> probe environment -> index code -> run baseline
-> plan patch -> approve -> propose edits -> apply edits
-> validate -> run patched benchmark -> compare results
-> analyze failure -> repair proposal
```

Key boundaries:

- The source project is copied into `code_task/workspace`; the original code is never modified.
- Patch application is gated by an explicit human approval step.
- Edit proposals are conservative old/new replacements, not free-form rewrites.
- Multiple ordered edits may target one file, but every `old` block must remain
  uniquely matchable; invalid proposals stop before workspace files are written.
- `code-task execute` can run the next safe steps, but it stops at plan approval
  and proposal review unless the user explicitly continues.
- Current execution uses workspace isolation plus an explicit interpreter policy. It supports `current` and `external`; managed environment creation is planned later.

Bundled examples:

- `toy_spam_project`: tiny rule-based classifier, useful for patch and failure-analysis smoke tests.
- `tiny_digits_mlp_project`: lightweight NumPy MLP over scikit-learn's bundled digits dataset, useful for realistic local ML benchmark experiments without GPU or downloads.

### 3. Research With Experiment

Use this when you want a research idea to become an executable experiment and a result-backed report.

Conceptual flow:

```text
plan -> search -> read -> synthesize -> design experiment
-> template codegen or embedded code-task -> run benchmark -> report
```

Current status:

- `06-code` normally generates a whitelisted template experiment.
- `--experiment-template code_task_project` is the generic embedded handoff into the code-task workflow. It accepts either `--code-task-config` or explicit `--code-root`, `--task-file`, and `--benchmark-command` flags.
- `simple-ar run --config ...` is the preferred way to keep multi-option research/code-task runs readable and repeatable.
- `--experiment-template llm_code_task_toy_spam` remains only as a bundled smoke-test template.
- The embedded path is end-to-end: it auto-approves the patch plan inside the copied workspace. The standalone code-task workflow remains the safer human-review path.
- Report generation is guarded: LLM drafts are accepted only when citations, metric visibility, fixture disclosure, and toy-demo boundaries pass rule-based checks.

## Default 8-Stage Pipeline

```text
01 plan        Scope the topic and research question
02 search      Collect paper metadata
03 read        Create literature notes
04 synthesize  Summarize themes and propose a hypothesis
05 design      Create an experiment plan
06 code        Generate experiment code or prepare an embedded code task
07 run         Execute the experiment and parse metrics
08 report      Write a Markdown report with references
```

| Stage | Main outputs | Purpose |
| --- | --- | --- |
| `plan` | `goal.md`, `problem.md` | Scope the topic into a concrete research question (LLM-backed when enabled). |
| `search` | `papers.jsonl`, `search_meta.json` | Collect OpenAlex/arXiv metadata or explicit fixture rows. |
| `read` | `paper_notes.json`, `notes.md` | Convert paper metadata into structured notes (LLM-backed when enabled). |
| `synthesize` | `synthesis.md`, `hypothesis.md` | Produce a bounded synthesis and testable hypothesis (LLM-backed when enabled). |
| `design` | `experiment_plan.json` | Select a safe experiment template and parameters. |
| `code` | `experiment.py` | Generate code from the selected template or prepare an embedded code-task harness. |
| `run` | `results.json`, `stdout.txt`, `stderr.txt` | Execute the experiment and parse numeric metrics. |
| `report` | `report.md`, `references.bib`, `manifest.json`, `report_quality.json` | Write a paper-like report with citations (LLM-backed when enabled). |

## Search And LLM Boundaries

- Live search uses OpenAlex first, then arXiv. When `--strict-search` is not set, cached metadata is used after live failures.
- `--offline-search` skips live providers and uses fixture metadata immediately.
- `--allow-fixture-fallback` allows fixture metadata only after live and cache attempts fail.
- `--no-llm` switches plan/read/synthesize/report to deterministic fallback text.
- Report drafting defaults to `auto`: if `results.json` exists the report uses experiment sections, otherwise it becomes literature-only.

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
- `06-code/code_task_experiment.json`: present for embedded code-task templates such as `code_task_project` and `llm_code_task_toy_spam`.

Nested embedded code-task files:

- `06-code/code_task_run/code_task/summary.md`: consolidated nested code-task outcome.
- `06-code/code_task_run/code_task/patch_plan.md`: LLM patch plan auto-approved by the pipeline.
- `06-code/code_task_run/code_task/meta/proposed_edits.json`: controlled old/new edit proposal.
- `06-code/code_task_run/code_task/patch.diff`: applied patch inside the copied workspace.
- `06-code/code_task_run/code_task/run/baseline/`: pre-patch benchmark artifacts.
- `06-code/code_task_run/code_task/run/patched/`: patched benchmark artifacts.
- `06-code/code_task_run/code_task/run/comparison.json`: before/after comparison when both runs exist.

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
      environment_report.json
      codebase_index.json
      hitl_decisions.jsonl
      proposed_edits.json
      applied_edits.json
      validation_report.json
      failure_analysis.md        # validation-only failure diagnosis
      llm_usage.jsonl
      llm_usage_summary.json
    run/
      comparison.json
      baseline/
        execution_report.json
        stdout.txt
        stderr.txt
        metrics.json
      patched/
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
- `meta/`: environment reports, indexes, decisions, proposed edits, applied edit summaries, validation reports, validation-only failure analysis, and LLM usage.
- `run/`: labelled benchmark stdout/stderr, execution reports, parsed metrics, before/after comparison, and benchmark failure analysis.
- `repairs/`: bounded repair proposals grouped by attempt. Each proposal records the source analysis path and selected repair context.

Important user-facing code-task files:

- `summary.md`: compact outcome, next-step guidance, task, patch, validation, benchmark, comparison, and failure-analysis summary.
- Changed test or benchmark files are highlighted as review-sensitive in
  `summary.md` and embedded report evidence; benchmark improvements should be
  trusted only after inspecting the diff.
- `meta/environment_report.json`: observational OS/Python/tool/GPU/project probe for planning and debugging.
- `run/baseline/execution_report.json`: pre-patch benchmark result.
- `run/patched/execution_report.json`: post-patch benchmark result.
- `run/comparison.json`: before/after metric deltas and conservative verdict when both baseline and patched runs exist. Explicit `primary_metric` and `metric_directions` from the manifest are used before heuristic metric-name rules.
- `patch_plan.md`: human-reviewable plan before edits, including recorded environment, validation, and baseline context when available.
- `patch.diff`: applied patch for review.
- `meta/applied_edits.json`: changed files plus before/after hashes for the files touched by the patch.

## Code-Task Environment Strategy

Environment handling is intentionally separated from source-code isolation:

- Source-code isolation means user code is copied to `code_task/workspace` before any patch is applied.
- Execution isolation means benchmarks run with a selected Python/runtime environment.

Today, code-task has the first kind of isolation and records environment signals
with `meta/environment_report.json`. It can select either the active
SimpleAutoResearch Python environment or a user-provided external interpreter.
It does not yet create virtual environments or install dependencies automatically.

The planned environment modes are:

- `current`: use the active SimpleAutoResearch Python environment. Supported now.
- `external`: use a user-provided Python or Conda interpreter. Supported now.
- `project-venv`: create a per-run environment inside the run directory. Planned.
- `shared-env-cache`: reuse environments keyed by dependency-file and platform hashes. Planned.
- `docker`: run in a container when stronger isolation is needed. Planned.

The default should remain conservative: dependency installation must be explicit
and reviewable, and user project packages should not be silently installed into
SimpleAutoResearch's own environment.

## Why This Split Matters

The split keeps the project from becoming one rigid pipeline.

- If the user wants a survey, code stages should be skipped.
- If the user wants to optimize existing code, literature stages should be optional.
- If the user wants a full automatic-research loop, modules can be composed.
- Each module can be upgraded independently.

This follows one practical lesson from AutoResearchClaw: complex behavior is easier to control when it is exposed as workflow modes and capabilities, not as one ever-growing sequence of flags.
