# Workflows And Artifacts

[涓枃鐗堟湰](WORKFLOWS_zh.md)

This document explains what SimpleAutoResearch is doing internally: workflow
presets, pipeline stages, artifact ownership, and module boundaries. It avoids
duplicating the full artifact manual; for concrete commands and file trees, see
[Usage And Configuration](USAGE.md). For command flags, see
[CLI Reference](CLI_REFERENCE.md); for TOML fields, see
[Configuration Reference](CONFIG_REFERENCE.md).

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
init workspace -> index code -> map repo -> probe environment
-> run baseline -> build context pack -> work-plan -> create batch
-> plan patch -> approve -> propose edits -> apply edits
-> validate -> run patched benchmark -> compare results
-> analyze failure -> repair proposal
```

Key boundaries:

- The source project is prepared under `code_task/workspace`; default `copy`
  mode creates a guarded physical copy, while `git_worktree` creates a detached
  worktree for repo-root git projects. Experimental `sparse_copy` copies only
  configured include patterns and always excludes data/model/cache/secret-like
  paths. The original code is never modified.
- Patch application is gated by an explicit human approval step.
- Edit proposals are conservative old/new replacements, not free-form rewrites.
- The default editor backend is `controlled_patch`; the backend interface is
  now explicit so future external agents can plug in behind the same safety and
  review gates.
- Multiple ordered edits may target one file, but every `old` block must remain
  uniquely matchable; invalid proposals stop before workspace files are written.
- `code-task execute` can run the next safe steps, but it stops at plan approval
  and proposal review unless the user explicitly continues.
- Work-plan items are meant to be executable implementation batches. The
  executor skips obvious analysis-only items when choosing the first active
  batch, so an LLM-generated "inspect the project" item does not constrain the
  edit stage by accident.
- When several reviewed work-plan items form a small serial dependency chain
  that must land together, such as feature producer, model consumer, and config
  switch, the active batch may merge them. The separate plan remains visible,
  while `batch_state.json.work_item.source_work_item_ids` and `target_files`
  show the bounded execution scope used by the edit proposal.
- A benchmark-passing repair is not automatically a task success. The
  before/after verdict comes from `code_task/run/comparison.json`; if patched
  metrics remain below baseline, the system has recovered execution but has not
  achieved an improvement objective yet.
- Current execution uses workspace isolation plus an explicit interpreter
  policy. It supports `current` and `external`; managed environment creation is
  planned later. `workspace.reuse_source_venv` can point a worktree/copy/sparse
  run at an existing source `.venv` Python without installing dependencies.

Bundled examples:

- `examples/research_report/`: research-only search/read/synthesize/report
  workflow with live academic sources and report variants.
- `examples/code_task_medium_review/`: standalone code-task workflow over a
  multi-module review classifier with a `main.py` entrypoint, JSON config,
  visible progress output, and a task that naturally touches feature extraction,
  model scoring, and configuration.
- `examples/full_pipeline_tiny_mlp/`: full 8-stage pipeline over a lightweight
  NumPy MLP benchmark, useful for end-to-end local checks without GPU.

### 3. Research With Experiment

Use this when you want a research idea to become an executable experiment and a result-backed report.

Conceptual flow:

```text
plan -> search -> read -> synthesize -> design experiment
-> template codegen or embedded code-task -> run benchmark -> report
```

Current status:

- `06-code` can generate a whitelisted template experiment, prepare an embedded
  code-task workspace for existing projects, or create a bounded greenfield
  project under `06-code/generated_project` when no source project exists yet.
- `--experiment-template code_task_project` is the generic embedded handoff into the code-task workflow. It accepts either `--code-task-config` or explicit `--code-root`, optional `--task-file`, and `--benchmark-command` flags. If no task file is supplied, `05-design` generates `generated_code_task.md` from the earlier research artifacts and a compact codebase summary.
- `simple-ar run --config ...` is the preferred way to keep multi-option research/code-task runs readable and repeatable.
- `--experiment-template llm_code_task_toy_spam` remains only as a bundled smoke-test template.
- The embedded path is end-to-end: it builds the same repo-map/context-pack,
  work-plan, and attempt/batch evidence as standalone code tasks, then
  auto-approves the patch plan inside the prepared workspace. Embedded runs keep
  the active batch to the first concrete work item by default; standalone
  code-task remains the better place for large merged batches and human review.
- The final report receives the nested code-task comparison as experiment
  evidence, so before/after metrics can appear in the Code Task Evidence section
  instead of being hidden inside `06-code/`.
- Report generation is guarded: LLM drafts are accepted only when citations, metric visibility, fixture disclosure, and toy-demo boundaries pass rule-based checks.

## Default 8-Stage Pipeline

```text
01 plan        Scope the topic and research question
02 search      Retrieve paper metadata, full text, and local chunks
03 read        Screen, shortlist, and structure retrieved papers
04 synthesize  Analyze themes, gaps, and experimentable hypotheses
05 design      Create an experiment plan
06 code        Generate experiment code or prepare an embedded code task
07 run         Execute the experiment and parse metrics
08 report      Write a Markdown report with references
```

| Stage | Main outputs | Purpose |
| --- | --- | --- |
| `plan` | `goal.md`, `problem.md` | Scope the topic into a concrete research question (LLM-backed when enabled). |
| `search` | `papers.jsonl`, `search_meta.json`, `documents/`, `research_index/` | Retrieve and ingest metadata/full text, record provider provenance, and build local chunks. It may select candidates within budget but does not perform semantic review. |
| `read` | `review/`, `paper_notes.json`, `notes.md` | Screen and prioritize retrieved papers, then convert the shortlist into canonical Paper Briefs (LLM-backed when enabled). Larger LLM runs use coarse title/abstract batches before reranking the kept set. |
| `synthesize` | `synthesis_brief.json`, `synthesis.md`, `hypothesis.md` | Analyze read-stage Paper Briefs into themes, gaps, bounded ideas, and testable hypotheses (LLM-backed when enabled). |
| `design` | `experiment_plan.json`, `experiment_contract.json`, `result_schema.json`, `resource_plan.json`, `dependency_plan.json`, `domain_profile.json`, `contract_validation.json` | Select a safe experiment template and write the executable contract, metric schema, resource/dependency budget, domain profile, and pre-code validation. |
| `code` | `code_task_run/`, `experiment.py`, or generated implementation artifacts | Prepare an embedded code-task harness or generate implementation artifacts from the design contract. |
| `run` | `results.json`, `guard_report.json`, `stdout.txt`, `stderr.txt` | Execute the experiment, normalize canonical results, and guard against missing/invalid metrics before reporting. |
| `report` | `report.md`, `references.bib`, `manifest.json`, `report_quality.json`, `report_memory.json`, `report_audit.json` | Write a template-guided report with citations, bounded source backtracking, and audit artifacts (LLM-backed when enabled). |

## Search And LLM Boundaries

Search is the retrieval gate, not the whole evidence engine. It scopes research
questions, chooses source order, retrieves candidates, records provider
provenance, and builds document/full-text/index artifacts. It may rank and cap
retrieved candidates to stay within budget, but semantic screening, structured
reading, synthesis, and experiment-contract work are owned by later stages.

Normal runs keep compact artifacts by default:

```text
02-search/
  papers.jsonl / search_meta.json
  documents/       # normalized document records and full-text/cache manifests
  research_index/  # portable chunks and local-index metadata
```

`03-read` owns screening, reranking, and canonical Paper Briefs. In LLM mode it
first coarse-screens compact title/abstract batches, then reranks the kept set
with reading priorities, evidence roles, and synthesis hints. `04-synthesize`
owns `synthesis_brief.json`, `synthesis.md`, and `hypothesis.md`; legacy
cards/evidence-pack diagnostics are retained only when
`[run].debug_artifacts = true`. `05-design` owns the experiment contract and
optional tool handoff drafts.

When `[run].debug_artifacts = true`, search also keeps planning files, retrieval
traces, retrieval-selection rows, coverage-review reports, and section tables. Design
debug mode may keep read-only tool-context drafts, adapter notes, and governance
artifacts.

Shared accelerator stores live outside the run by default under
`.simple_ar_cache/research_index`, keyed by run/source metadata. Run-local cache
folders such as downloaded PDFs and extracted text are rebuildable and can be
previewed or cleaned with `simple-ar clean`.

LLM participation is bounded. The research planner can run in deterministic,
`auto`, or LLM mode; lightweight coverage checks and local novelty checks are
risk signals, not proof of originality. `--no-llm` keeps plan/read/synthesize/report
on deterministic fallback text.

For the full search-stage file tree and per-file descriptions, see
[Usage And Configuration](USAGE.md). For search, cache, parser, and debug-artifact
settings, see [Configuration Reference](CONFIG_REFERENCE.md).

## Artifact Ownership Summary

WORKFLOWS intentionally stays at the ownership level; the complete file tree lives
in [Usage And Configuration](USAGE.md). At a high level:

- Root run files (`state.json`, `manifest.json`, `config_snapshot.json`, usage
  logs, and optional artifact indexes) track resume state, configuration, and
  observability.
- Stage directories (`01-plan` through `08-report`) own their own contracts,
  reports, and stable handoff artifacts.
- `02-search` owns retrieval, document/full-text status, and local chunks.
- `03-read` owns reading review, shortlists, literature cards, and structured
  reading notes.
- `04-synthesize` owns the compact evidence bridge, gaps, ideas, novelty hints,
  synthesis, and hypothesis derived from read-stage artifacts.
- `05-design` owns experiment contracts, result schemas, resource/dependency
  plans, domain profiles, contract validation, and experiment plans.
- `06-code/code_task_run` embeds the same artifact shape as a standalone code
  task when the research pipeline hands off to code execution.
- `08-report` owns the final report package: report text, references, manifest,
  compact report memory, source/citation/metric audit, and quality checks.

This split keeps detailed operational files available without forcing readers to
learn every JSON/JSONL artifact before they understand the workflow. When a file
is primarily diagnostic or rebuildable, it should either be gated by
`debug_artifacts` or documented as cleanup-safe.

## Code Task Artifact Boundaries

Standalone code tasks and embedded pipeline code tasks use the same conceptual
layout. The important boundary is what each group is responsible for:

- `workspace/`: isolated editable project copy, worktree, or sparse subset.
- `meta/`: environment reports, repo maps, locate results, edit proposals,
  validation reports, applied-edit summaries, and LLM usage.
- `context_packs/`: bounded prompt context assembled from ranked editable files
  and protected read-only evidence.
- `attempts/`: durable work-plan and batch state for multi-step implementation
  and repair loops.
- `run/`: baseline/patched benchmark logs, metrics, execution reports, failure
  analysis, and before/after comparison.
- `repairs/`: bounded repair proposals grouped by repair attempt.

Tests, benchmarks, environment files, secrets, and user-configured protected paths
are indexed as read-only evidence by default and should not be edited by proposal,
repair, or apply steps. Edit-scope behavior and full artifact paths are described
in [Usage And Configuration](USAGE.md) and [Configuration Reference](CONFIG_REFERENCE.md).

## Code-Task Environment Strategy

Environment handling is intentionally separated from source-code isolation:

- Source-code isolation means user code is prepared under `code_task/workspace`
  before any patch is applied. In default `copy` mode that is a physical copy;
  in `git_worktree` mode it is a detached worktree.
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
