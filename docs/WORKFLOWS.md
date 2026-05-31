# Workflows And Artifacts

[中文版本](WORKFLOWS_zh.md)

This document explains what SimpleAutoResearch is doing internally: workflow
presets, pipeline stages, stage outputs, and run artifact layout. For concrete
commands, see [CLI Reference](CLI_REFERENCE.md); for TOML fields, see
[Configuration Reference](CONFIG_REFERENCE.md); for setup and walkthroughs, see
[Usage And Configuration](USAGE.md).

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

- `toy_spam_project`: tiny rule-based classifier, useful for patch and failure-analysis smoke tests.
- `tiny_digits_mlp_project`: lightweight NumPy MLP over scikit-learn's bundled digits dataset, useful for realistic local ML benchmark experiments without GPU or downloads.
- `medium_review_pipeline_project`: multi-module review classifier with a `main.py`
  entrypoint, JSON config, visible progress output, and a task that naturally
  touches feature extraction, model scoring, and configuration.

### 3. Research With Experiment

Use this when you want a research idea to become an executable experiment and a result-backed report.

Conceptual flow:

```text
plan -> search -> read -> synthesize -> design experiment
-> template codegen or embedded code-task -> run benchmark -> report
```

Current status:

- `06-code` normally generates a whitelisted template experiment.
- `--experiment-template code_task_project` is the generic embedded handoff into the code-task workflow. It accepts either `--code-task-config` or explicit `--code-root`, optional `--task-file`, and `--benchmark-command` flags. If no task file is supplied, `05-design` generates `generated_code_task.md` from the earlier research artifacts and a compact codebase summary.
- `simple-ar run --config ...` is the preferred way to keep multi-option research/code-task runs readable and repeatable.
- `--experiment-template llm_code_task_toy_spam` remains only as a bundled smoke-test template.
- The embedded path is end-to-end: it builds the same repo-map/context-pack,
  work-plan, and attempt/batch evidence as standalone code tasks, then
  auto-approves the patch plan inside the prepared workspace. The standalone
  code-task workflow remains the safer human-review path.
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
| `search` | `planning/`, `traces/`, `review/`, `papers.jsonl`, `search_meta.json` | Decompose the topic, retrieve candidates, deduplicate/screen metadata, check coverage, then collect literature records. |
| `read` | `paper_notes.json`, `notes.md` | Convert paper metadata into structured notes (LLM-backed when enabled). |
| `synthesize` | `synthesis.md`, `hypothesis.md` | Produce a bounded synthesis and testable hypothesis (LLM-backed when enabled). |
| `design` | `experiment_plan.json` | Select a safe experiment template and parameters. |
| `code` | `experiment.py` | Generate code from the selected template or prepare an embedded code-task harness. |
| `run` | `results.json`, `stdout.txt`, `stderr.txt` | Execute the experiment and parse numeric metrics. |
| `report` | `report.md`, `references.bib`, `manifest.json`, `report_quality.json` | Write a paper-like report with citations (LLM-backed when enabled). |

## Search And LLM Boundaries

By default, normal pipeline runs keep the search stage compact after the stage
contract is written:

```text
02-search/
  contract.json
  report.md
  papers.jsonl
  search_meta.json
```

Set `[run].debug_artifacts = true` when you want to inspect the full planning,
trace, document, local-index, review, and card layers in the run directory:

```text
02-search/
  papers.jsonl
  search_meta.json
  planning/
    research_plan.json
  traces/
    retrieval_rounds.jsonl
    screening_decisions.jsonl
  review/
    coverage_report.json
    coverage_report.md
  documents/
    documents.jsonl
    cache_manifest.json
    fulltext_manifest.json
    fulltext_extraction.json
    extracted_text/
  research_index/
    chunks.jsonl
    index_meta.json
  cards/
    paper_cards.jsonl
    claim_cards.jsonl
```

Shared accelerator stores are outside the run by default:

```text
.simple_ar_cache/
  research_index/
    sqlite_fts.db
    lancedb/
```

- `02-search/planning/research_plan.json` records scoped sub-questions, evidence
  facets, seed and expanded queries, structured title/abstract keyword hints,
  source order, local documents, retrieval mode, cache/index hints, and budget
  in one compact planning artifact. It is deterministic by default when LLM mode
  is disabled, and can be LLM-backed via `[research].planner = "auto"` or
  `"llm"`.
- `02-search/traces/retrieval_rounds.jsonl` records executed source/query attempts.
  The first pass records the normalized query plus facet/title/abstract keyword
  intent; coverage-driven follow-up rounds reuse the same trace format.
- `02-search/traces/screening_decisions.jsonl` records deduplication and lightweight
  relevance-screening decisions before papers are written.
- `02-search/review/coverage_report.json` and `02-search/review/coverage_report.md`
  record required-facet coverage and missing questions. If round budget remains,
  the search stage can run a bounded follow-up round for uncovered facets before
  writing the final paper list.
- `02-search/documents/documents.jsonl` records selected metadata and configured
  local files as document records with extraction status. Metadata-only sources
  stay `metadata_only`; local Markdown/text can become `parsed`; unsupported or
  unavailable files are recorded as `skipped` or `failed`.
- `02-search/documents/cache_manifest.json` summarizes cache/index intent,
  extraction statuses, and source counts.
- `02-search/documents/fulltext_manifest.json` records arXiv/OpenAlex/local
  full-text hints, fetch-budget decisions, cached local resources, and remote
  fetch failures.
- `02-search/documents/fulltext_extraction.json` records best-effort parser
  results for cached/local full-text resources. Markdown/text and basic HTML
  are parsed with the standard library; PDF parsing uses lightweight `pypdf`;
  optional `unstructured` can be selected for stronger local parsing and
  degrades to a manifest-only failure when unavailable.
- `02-search/research_index/chunks.jsonl` stores portable local chunks from
  abstracts and parsed/extracted local or full-text files. This is the source
  of truth for later evidence-card extraction.
- `02-search/research_index/index_meta.json` records the selected local index
  backend, run id, portable chunk path, and shared SQLite FTS / LanceDB store
  paths. SQLite and LanceDB live under `.simple_ar_cache/research_index` by
  default so repeated runs can build toward a common research memory instead of
  duplicating databases.
- Live search uses the configured source order. By default it tries OpenAlex
  first, then Semantic Scholar, then arXiv. When `--strict-search` is not set
  and source-plan cache is enabled, cached metadata is used after live failures.
- The default source strategy is ordered fallback rather than full source union:
  a successful provider stops downstream provider calls for that query.
- `local_files` can expose user-provided Markdown/text notes as conservative
  metadata-like records. Local PDFs are best-effort parser inputs only when
  full-text intent is enabled and an optional parser is available.
- `--offline-search` skips live providers and uses fixture metadata immediately.
- `--allow-fixture-fallback` allows fixture metadata only after live and cache attempts fail.
- `--no-llm` switches plan/read/synthesize/report to deterministic fallback text.
- Report drafting defaults to `auto`: if `results.json` exists the report uses experiment sections, otherwise it becomes literature-only.

## Research Run Artifacts

A completed research run can contain these files, depending on enabled options:

```text
runs/<run-id>/
  state.json
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
    contract.json
    report.md
    papers.jsonl
    search_meta.json
    planning/       # kept only when [run].debug_artifacts = true
      research_plan.json
    documents/      # kept only when [run].debug_artifacts = true
      documents.jsonl
      cache_manifest.json
      fulltext_manifest.json
      fulltext_extraction.json
      extracted_text/  # only when HTML/PDF-like resources are parsed to text
    research_index/ # kept only when [run].debug_artifacts = true
      chunks.jsonl
      index_meta.json
    cards/          # kept only when [run].debug_artifacts = true
      paper_cards.jsonl
      claim_cards.jsonl
    traces/         # kept only when [run].debug_artifacts = true
      retrieval_rounds.jsonl
      screening_decisions.jsonl
    review/         # kept only when [run].debug_artifacts = true
      coverage_report.json
      coverage_report.md
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

- `state.json`: typed workflow checkpoint used for resume and stage handoff.
- `manifest.json`: stage status and declared outputs.
- `pipeline_state.json`: last completed stage and next stage for resume.
- `config_snapshot.json`: selected runtime configuration.
- `llm_usage.jsonl`: one row per successful LLM request.
- `llm_usage_summary.json`: aggregate token counts and optional cost estimate.
- `artifact_index.json`: local artifact index generated by `inspect` or `search-artifacts`.
- `artifact_chunks.jsonl`: line-addressable chunks generated for local retrieval.
- `artifact_search_results.json`: last artifact search result; generated only by the artifact search command.
- `source_plan.json`: artifact-retrieval source plan describing which run
  artifacts each stage should consult; this is distinct from
  the literature `source_plan` section inside `02-search/planning/research_plan.json`.
- `activity_log.jsonl`: structured activity log for source planning and retrieval actions.
- `evidence_ledger.jsonl`: snippets used by stages, with path and line range.
- `02-search/contract.json`: compact machine-readable search summary for stage
  handoff.
- `02-search/report.md`: compact human-readable search summary.
- `02-search/planning/research_plan.json`: compact search planning artifact with
  research question decomposition, executable query plan, and literature source
  plan sections. Kept in the run directory only when `[run].debug_artifacts = true`.
- `02-search/traces/retrieval_rounds.jsonl`: executed source/query attempts with
  returned counts, errors, and compact query-intent traces. Debug artifact only.
- `02-search/traces/screening_decisions.jsonl`: keep/discard rows for retrieved
  metadata candidates. Debug artifact only.
- `02-search/review/coverage_report.json` / `coverage_report.md`: required-facet
  coverage, missing question status, and follow-up query decisions. Debug artifact
  only.
- `02-search/documents/documents.jsonl`: normalized document records for metadata
  and local files, including hash and parser status when available. Debug artifact
  only.
- `02-search/documents/cache_manifest.json`: cache, index, full-text, and
  extraction-status summary. Debug artifact only.
- `02-search/documents/fulltext_manifest.json`: full-text hints, selected
  candidates, blocked/skipped reasons, and parser/fetch budget settings. Debug
  artifact only.
- `02-search/documents/fulltext_extraction.json`: parser outcomes for cached
  local/remote full-text resources. Failed PDF or unsupported suffix parsing is
  recorded here without failing the search stage. Debug artifact only.
- `02-search/research_index/chunks.jsonl`: portable local chunk store for later
  retrieval, evidence-card extraction, and report grounding. Debug artifact only
  in the run directory; shared accelerators live under `.simple_ar_cache/`.
- `02-search/research_index/index_meta.json`: local index manifest with backend,
  run id, portable chunk path, and shared SQLite FTS / LanceDB accelerator-store
  paths. Debug artifact only.
- `02-search/cards/paper_cards.jsonl`: deterministic paper cards with evidence
  refs used by later gap, idea, and report stages. Debug artifact only.
- `02-search/cards/claim_cards.jsonl`: conservative claim cards grounded in
  chunk ids. Later report audit should still verify them before final use. Debug
  artifact only.
- `02-search/search_meta.json`: final selected source, status, counts, and
  pointers to planning/trace/review artifacts.
- `02-search/papers.jsonl`: normalized metadata rows consumed by `03-read`.
- `05-design/generated_code_task.md`: generated only when an embedded `code_task_project` run omits a task file.
- `05-design/generated_code_task_meta.json`: provenance for that generated task file.
- `06-code/code_task_experiment.json`: present for embedded code-task templates such as `code_task_project` and `llm_code_task_toy_spam`.

Nested embedded code-task files have the same shape as standalone code-task
runs, but live under `06-code/code_task_run/`:

```text
06-code/
  code_task_run/
    manifest.json
    code_task/
      summary.md
      work_plan.md
      patch_plan.md
      patch.diff
      workspace/
      meta/
        repo_map.json
        proposed_edits.json
        validation_report.json
      context_packs/
        context-001/
      attempts/
        attempt-001/
          batches/
            batch-001/
              batch_state.json
      run/
        baseline/
        patched/
        comparison.json
```

Important nested files:

- `06-code/code_task_run/code_task/summary.md`: consolidated nested code-task outcome.
- `06-code/code_task_run/code_task/meta/repo_map.json`: layered repo map for the prepared workspace.
- `06-code/code_task_run/code_task/context_packs/context-001/`: prompt-ready context pack used by planning/editing.
- `06-code/code_task_run/code_task/work_plan.md`: batch-oriented implementation plan.
- `06-code/code_task_run/code_task/attempts/attempt-001/batches/batch-001/batch_state.json`: active embedded batch state.
- `06-code/code_task_run/code_task/patch_plan.md`: LLM patch plan auto-approved by the pipeline.
- `06-code/code_task_run/code_task/meta/proposed_edits.json`: controlled old/new edit proposal.
- `06-code/code_task_run/code_task/patch.diff`: applied patch inside the prepared workspace.
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
    work_plan.md
    patch_plan.md
    patch.diff
    workspace/
    meta/
      environment_report.json
      codebase_index.json
      repo_map.json
      repo_map_summary.md
      locate_results.json
      locate_results.md
      hitl_decisions.jsonl
      proposed_edits.json
      applied_edits.json
      validation_report.json
      failure_analysis.md        # validation-only failure diagnosis
      llm_usage.jsonl
      llm_usage_summary.json
    context_packs/
      context-001/
        context_pack.json
        prompt_context.md
        selected_snippets.jsonl
    attempts/
      attempt-001/
        attempt_state.json
        batches/
          batch-001/
            batch_state.json
            batch_context.json
            proposed_edits.json
            proposal_warnings.json
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
- `meta/`: environment reports, indexes, locate results, decisions, proposed edits, applied edit summaries, validation reports, validation-only failure analysis, and LLM usage.
- `context_packs/`: bounded prompt context packs derived from locate results and workspace snippets.
- `attempts/`: durable work-plan attempt and batch state for bounded implementation or repair loops.
- `run/`: labelled benchmark stdout/stderr, execution reports, parsed metrics, before/after comparison, and benchmark failure analysis.
- `repairs/`: bounded repair proposals grouped by attempt. Each proposal records the source analysis path and selected repair context.

Important user-facing code-task files:

- `summary.md`: compact outcome, next-step guidance, task, patch, validation, benchmark, comparison, and failure-analysis summary.
- Tests and benchmark files are protected by the default edit scope. They may
  be indexed as read-only evidence, but `propose-edits`, `repair`, and
  `apply-edits` should not modify them.
- `meta/environment_report.json`: observational OS/Python/tool/GPU/project probe for planning and debugging.
- `meta/repo_map.json`: layered project/directory/file/symbol/entrypoint/test/benchmark/config map derived from `codebase_index.json`.
- `meta/repo_map_summary.md`: compact human-readable repo-map summary and prompt-budget note.
- `meta/locate_results.json`: deterministic ranking of likely editable targets and protected read-only evidence.
- `meta/locate_results.md`: human-readable locate summary for review before building prompt context.
- `context_packs/context-NNN/context_pack.json`: selected files, budgets, source references, and omitted-file accounting.
- `context_packs/context-NNN/prompt_context.md`: prompt-ready Markdown grouped into editable targets and read-only evidence.
- `context_packs/context-NNN/selected_snippets.jsonl`: clipped source snippets, one selected file per row.
- `work_plan.md`: batch-oriented implementation plan above the narrower patch plan.
- `attempts/attempt-NNN/attempt_state.json`: attempt lifecycle state derived from work-plan and batch outcomes.
- `attempts/attempt-NNN/batches/batch-NNN/batch_state.json`: active work item, allowed target files, batch artifacts, and final batch state.
- `run/baseline/execution_report.json`: pre-patch benchmark result.
- `run/patched/execution_report.json`: post-patch benchmark result.
- `run/comparison.json`: before/after metric deltas and conservative verdict when both baseline and patched runs exist. Explicit `primary_metric` and `metric_directions` from the manifest are used before heuristic metric-name rules.
- `manifest.json.objective`: current task-objective verdict derived from
  comparison when patched benchmark artifacts exist. This separates "the code
  ran" from "the measured goal improved."
- `patch_plan.md`: human-reviewable plan before edits, including recorded environment, validation, and baseline context when available.
- When a latest context pack exists, `patch_plan.md` records its path and uses
  its selected snippets instead of the older index-only file selector.
- `patch.diff`: applied patch for review.
- `meta/proposed_edits.json`: reviewable edit proposal plus editor backend metadata.
- `meta/applied_edits.json`: changed files plus before/after hashes for the files touched by the patch, including the proposal path and editor backend actually applied. For repair proposals this is the `code_task/repairs/repair-NNN/proposed_edits.json` path.

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
