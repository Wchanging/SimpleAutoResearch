# CLI Reference

[中文版本](CLI_REFERENCE_zh.md)

This page is a command lookup for SimpleAutoResearch. It intentionally focuses
on command syntax, options, outputs, and short operational notes.

- Installation and walkthroughs: [Usage And Configuration](USAGE.md)
- Workflow concepts and artifacts: [Workflows And Artifacts](WORKFLOWS.md)
- TOML schema and examples: [Configuration Reference](CONFIG_REFERENCE.md)

## Command Overview

| Command | Purpose |
| --- | --- |
| `simple-ar run` | Start a new 8-stage research pipeline run. |
| `simple-ar resume` | Continue an existing research pipeline run. |
| `simple-ar status` | Print status for a research run or code-task run. |
| `simple-ar inspect` | Build a local artifact index for a run. |
| `simple-ar search-artifacts` | Search indexed run artifacts. |
| `simple-ar clean` | Preview and remove rebuildable run caches. |
| `simple-ar code-task ...` | Work with an existing codebase in an isolated editable workspace. |

## Research Pipeline

### `simple-ar run`

**Purpose**: start a new 8-stage research pipeline run.

**Usage**:

```bash
uv run simple-ar run --topic "agent simulation" --to-stage report
uv run simple-ar run --config examples/research_report/configs/research_report.toml
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `--config PATH` | path | TOML config for a repeatable run. CLI flags override config values. |
| `--topic TEXT` | string | Research topic. Required unless `[run].topic` is set in config. |
| `--output-root DIR` | path | Directory where timestamped run directories are created. |
| `--from-stage NAME` | stage | First stage to execute. Default: `plan`. |
| `--to-stage NAME` | stage | Last stage to execute. Default: `report`. |
| `--model NAME` | string | LLM model override. |
| `--llm-workers N` | int | Parallel LLM workers for supported stages. |
| `--max-papers N` | int | Literature metadata limit. |
| `--search-query TEXT` | string | Override the generated search query. |
| `--experiment-template NAME` | string | Experiment template, such as `code_task_project`. |
| `--experiment-timeout N` | int | Subprocess timeout for experiment execution. |
| `--report-mode MODE` | enum | `auto`, `research_only`, or `experiment`. |
| `--no-llm` | flag | Use deterministic fallback text where possible. |
| `--offline-search` | flag | Skip live literature providers. |
| `--allow-fixture-fallback` | flag | Allow fixture metadata after live/cache failures. |
| `--strict-search` | flag | Fail instead of using cache/fixture fallback. |
| `--no-retrieval` | flag | Disable local artifact retrieval context. |
| `--retrieval-top-k N` | int | Number of local artifact chunks to retrieve. |
| `--quiet` | flag | Suppress progress logs. |
| `--overwrite-stage-artifacts` | flag | Disable default archive protection for rerunning `06-code` / `07-run` artifacts. Use only when old code/run outputs are disposable. |

**Code-task pipeline options**:

| Option | Type | Description |
| --- | --- | --- |
| `--code-task-config PATH` | path | Code-task TOML for `--experiment-template code_task_project`. |
| `--code-root DIR` | path | Source project prepared under the embedded code-task workspace. |
| `--task-file PATH` | path | Task file. Optional for embedded runs; if omitted, stage `05-design` generates one. |
| `--benchmark-command TEXT` | string | Benchmark command run before and after edits. |
| `--code-task-name TEXT` | string | Display name for the embedded code-task experiment. |
| `--code-task-max-file-bytes N` | int | Max copied file size for embedded copy/sparse modes. |
| `--code-task-workspace-mode MODE` | enum | `copy`, `git_worktree`, or `sparse_copy`. |
| `--code-task-workspace-reuse-source-venv` | flag | Use a detected source `.venv` Python. |
| `--code-task-workspace-setup-hook TEXT` | string | Record a setup command for future managed environments. |
| `--code-task-env-mode MODE` | enum | `current` or `external`. |
| `--code-task-python PATH` | path | Python executable for external env mode. |
| `--primary-metric NAME` | string | Primary benchmark metric for comparison. |
| `--metric-direction NAME=DIRECTION` | repeatable | Metric direction: `higher`, `lower`, `resource`, or `ignore`. |

**Outputs**:

- `runs/<run-id>/manifest.json`
- `runs/<run-id>/config_snapshot.json`
- numbered stage directories such as `01-plan/`, `02-search/`, `08-report/`

**Notes**:

Use a TOML config for real runs with many options. See the
[Configuration Reference](CONFIG_REFERENCE.md#complete-pipeline-config).

### `simple-ar resume`

**Purpose**: continue an existing research pipeline run.

**Usage**:

```bash
uv run simple-ar resume runs/<run-id>
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode research_only
uv run simple-ar resume runs/<run-id> --from-stage report --to-stage report --report-output-mode variant --report-output-label survey-v2
```

**Options**:

`resume` accepts `RUN_DIR` plus most `run` options as overrides, including
`--config`, stage range, LLM/search/report options, report output policy, and
embedded code-task options.

Common report output options:

| Option | Type | Description |
|---|---|---|
| `--report-output-mode` | choice | `overwrite`, `archive`, or `variant`. `variant` writes `08-report/variants/<label>/` without replacing the current main report. |
| `--report-output-label` | string | Optional folder label for report archive/variant outputs. |
| `--overwrite-stage-artifacts` | flag | Disable default archive protection for rerunning `06-code` / `07-run` artifacts. |

**Outputs**:

- updates the existing run directory
- appends stage execution state to `manifest.json`

**Notes**:

When `config_snapshot.json` exists, omitted values are preserved from the
original run.

### `simple-ar status`

**Purpose**: print compact status for either a research run or a code-task run.

**Usage**:

```bash
uv run simple-ar status runs/<run-id>
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Research or code-task run directory. |

**Outputs**:

- prints stage status for pipeline runs
- prints environment, plan, patch, validation, benchmark, comparison, and repair state for code-task runs

**Notes**:

This command is read-only.

## Artifact Tools

### `simple-ar inspect`

**Purpose**: index and summarize local run artifacts.

**Usage**:

```bash
uv run simple-ar inspect runs/<run-id>
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Run directory to inspect. |

**Outputs**:

- `artifact_index.json`
- `artifact_chunks.jsonl`

**Notes**:

Operational metadata is indexed separately from user-facing artifacts.

### `simple-ar search-artifacts`

**Purpose**: search indexed run artifacts with lexical retrieval.

**Usage**:

```bash
uv run simple-ar search-artifacts runs/<run-id> "accuracy" --top-k 5
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Run directory. |
| `QUERY` | string | Search query. |
| `--top-k N` | int | Number of results. Default: `8`. |
| `--include-operational` | flag | Also search manifests, runner metadata, and other operational files. |

**Outputs**:

- prints matching artifact chunks and their source paths

**Notes**:

Run `inspect` first when the artifact index is missing or stale.

### `simple-ar clean`

**Purpose**: review and delete rebuildable caches for one run while preserving
audit artifacts.

**Usage**:

```bash
uv run simple-ar clean runs/<run-id>
uv run simple-ar clean runs/<run-id> --yes
uv run simple-ar clean runs/<run-id> --all-caches
uv run simple-ar clean --shared-index
uv run simple-ar clean --shared-cache
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Run directory to clean. Optional when `--shared-index` or `--shared-cache` is used. |
| `--yes` | flag | Delete the displayed targets without the interactive `yes` prompt. |
| `--all-caches` | flag | Delete every known rebuildable cache/index/context artifact for this run after a stronger warning. |
| `--shared-index` | flag | Strong cleanup: clear the shared research index store across runs/tests. |
| `--shared-cache` | flag | Strongest shared cleanup: clear shared research indexes and literature provider cache. |
| `--index-root PATH` | path | Shared index root for `--shared-index`/`--shared-cache`; defaults to `SIMPLE_AR_RESEARCH_INDEX_ROOT` or `.simple_ar_cache/research_index`. |
| `--literature-cache-root PATH` | path | Literature provider cache root for `--shared-cache`; defaults to `.simple_ar_cache/literature`. |
| `--allow-external-index-root` | flag | Allow shared cleanup to touch a path outside the current workspace. |

**Outputs**:

- prints a Rich tree preview before deletion
- deletes run-local rebuildable caches such as `02-search/documents/fulltext_cache/`, `02-search/documents/extracted_text/`, and `artifact_search_results.json`
- removes this run's rows from the shared SQLite research index when `index_meta.json` points to a workspace-local shared store
- with `--all-caches`, also deletes rebuildable research indexes, artifact search indexes/chunks, code-task repo maps, locate outputs, and context packs

**Notes**:

`clean` keeps reports, manifests, papers, parser audit files such as
`fulltext_extraction.json`, read-stage Paper Briefs, synthesis briefs, retained
debug coverage reports when present, and portable `research_index/chunks.jsonl`.
It does not delete the run directory itself. `--all-caches` removes
`research_index/chunks.jsonl` because it treats all indexes and retrieval
accelerators as rebuildable cache data, but it still keeps final reports,
metadata, manifests, and benchmark outputs.

`--shared-index` is stronger than `--all-caches`: it clears the shared
SQLite/LanceDB accelerator store across runs, so future runs must rebuild index
state. It keeps run-local audit artifacts because it does not touch run
directories.

`--shared-cache` is stronger again: it clears both the shared research index
and `.simple_ar_cache/literature`, so future runs may need to re-query
literature providers as well as rebuild local indexes.

## Code Task Commands

Code-task commands prepare an existing project under
`runs/<run-id>/code_task/workspace`. Later edits are applied to that isolated
workspace, not to the original project.

For normal use, start with the high-level orchestration commands. The low-level
primitive commands are mainly for debugging, learning, or fine-grained human
intervention.

### High-Level Orchestration

#### `simple-ar code-task init`

**Purpose**: create a code-task run, prepare the editable workspace, and build
the first code index.

**Usage**:

```bash
uv run simple-ar code-task init --config examples/code_task_medium_review/configs/code_task.toml
uv run simple-ar code-task init --code-root path/to/project --task-file task.md --benchmark-command "python main.py"
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `--config PATH` | path | TOML config for init settings. CLI flags override config values. |
| `--code-root DIR` | path | Source project. Required unless set in config. |
| `--task-file PATH` | path | Markdown/text task description. Required unless set in config. |
| `--output-root DIR` | path | Directory where the code-task run is created. |
| `--name TEXT` | string | Run name suffix. |
| `--benchmark-command TEXT` | string | Command run inside the workspace before and after edits. |
| `--primary-metric NAME` | string | Primary metric for before/after verdicts. |
| `--metric-direction NAME=DIRECTION` | repeatable | Metric direction: `higher`, `lower`, `resource`, or `ignore`. |
| `--env-mode MODE` | enum | `current` or `external`. |
| `--python PATH` | path | Python executable for `--env-mode external`. |
| `--workspace-mode MODE` | enum | `copy`, `git_worktree`, or `sparse_copy`. |
| `--workspace-include GLOB` | repeatable | Include pattern for `sparse_copy`. |
| `--workspace-exclude GLOB` | repeatable | Additional exclude pattern for `sparse_copy`. |
| `--workspace-reuse-source-venv` | flag | Reuse a detected source `.venv` Python as external execution policy. |
| `--workspace-setup-hook TEXT` | string | Record a setup command; it is not executed during init. |
| `--max-file-bytes N` | int | Maximum copied file size in copy/sparse modes. Use `0` to disable. |

**Outputs**:

- `code_task/manifest.json`
- `code_task/task.md`
- `code_task/workspace/`
- `code_task/meta/codebase_index.json`
- `code_task/meta/repo_map.json`
- `code_task/meta/repo_map_summary.md`

**Notes**:

For reusable settings, prefer TOML. See
[Configuration Reference](CONFIG_REFERENCE.md#standalone-code-task-config).

#### `simple-ar code-task execute`

**Purpose**: advance a code-task run to the next safe stop based on current
artifacts.

**Usage**:

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml
uv run simple-ar code-task execute runs/<run-id> --to-step propose-edits
uv run simple-ar code-task execute runs/<run-id> --apply-proposed-edits --timeout 60
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Code-task run directory. |
| `--config PATH` | path | Optional TOML for model routing, budget, and runtime settings. |
| `--to-step STEP` | enum | Stop no later than `probe`, `baseline`, `work-plan`, `batch`, `plan`, `propose-edits`, `apply-edits`, `validate`, `run`, `analyze-failure`, or `repair`. |
| `--dry-run` | flag | Print the next action without writing artifacts. |
| `--model NAME` | string | Model override for LLM-backed steps. |
| `--no-llm` | flag | Use deterministic fallbacks where possible. |
| `--timeout N` | int | Benchmark timeout. |
| `--yes` | flag | Auto-approve inline review gates in normal execute mode; with `--interactive`, auto-continue primitive prompts. Use only after you are comfortable approving the reviewed plan/proposal. |
| `--interactive` | flag | Debug mode: confirm each primitive step instead of running continuously to the next review gate. |
| `--no-review-inline` | flag | Disable inline review prompts and stop at review gates instead. |
| `--skip-validation` | flag | Run benchmark even when static validation has not passed. |
| `--strict-validation` | flag | Treat higher-risk validation warnings as errors. |
| `--validation-max-file-bytes N` | int | Max file size scanned by static validation. |
| `--apply-proposed-edits` | flag | Apply reviewed `proposed_edits.json` after plan approval. |
| `--allow-large-edits` | flag | Allow a reviewed proposal that exceeds the normal edit budget. |
| `--allow-planning-fallback` | flag | Allow deterministic offline work/patch plans after LLM planning retries fail. |
| `--llm-retry-attempts N` | int | LLM work-plan and patch-plan attempts before stopping or explicitly falling back. |
| `--repair-rounds N` | int | Number of bounded repair proposals after failure. |
| `--max-files N` | int | Context file budget for LLM steps. |
| `--max-source-chars-per-file N` | int | Per-file source context budget. |
| `--env-mode MODE` | enum | `current` or `external`. |
| `--python PATH` | path | Python executable for external env mode. |

**Outputs**:

- `code_task/work_plan.md` and `work_plan.json`
- `code_task/attempts/attempt-*/batches/batch-*/batch_state.json`
- `code_task/patch_plan.md`
- `code_task/meta/proposed_edits.json`
- `code_task/meta/applied_edits.json`
- `code_task/meta/validation_report.json`
- `code_task/run/baseline/`, `code_task/run/patched/`, `code_task/run/comparison.json`
- `code_task/summary.md`

**Notes**:

`execute` preserves review gates without forcing a separate command for every
gate. In an interactive terminal, it renders a yellow Rich review panel for
`patch_plan.md`, `proposed_edits.json`, or large-edit approval and asks whether
to continue. In non-interactive shells it stops cleanly at the gate unless
`--yes` is supplied. Completed steps are shown as skipped on resume. Use
`--interactive` only when debugging primitive steps; combine it with `--yes` to
auto-continue those primitive prompts. Use `--yes` in normal execute mode only
when automated approval is intentional, and `--no-review-inline` when you want
the older stop-and-rerun behavior. Full workflow walkthroughs live in
[Usage And Configuration](USAGE.md#recommended-path-toml--execute).

When LLM work planning or patch planning returns malformed JSON, execute stops
with `llm_planning_failed` and does not write an offline fallback plan. Rerun
the same `execute` command to retry the LLM step. Use `--no-llm` for a fully
deterministic plan, or `--allow-planning-fallback` only when a fallback plan is
acceptable for the current task.

#### `simple-ar code-task decide-plan`

**Purpose**: record a human decision for the current patch plan.

**Usage**:

```bash
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Code-task run directory. |
| `--decision VALUE` | enum | `approve`, `reject`, or `revise`. Required. |
| `--note TEXT` | string | Optional review note. |
| `--reviewer TEXT` | string | Reviewer label. Default: `user`. |

**Outputs**:

- updates plan decision state in `manifest.json`

**Notes**:

Use `revise` or `reject` when the patch plan should not proceed to edit
proposal generation.

### Low-Level Primitives

The following commands are usually called by `execute`. Use them when you want
manual control or need to debug a specific stage.

#### `simple-ar code-task map`

**Purpose**: rebuild repo-map artifacts from the editable workspace.

**Usage**:

```bash
uv run simple-ar code-task map runs/<run-id> --show-summary
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Code-task run directory. |
| `--no-refresh-index` | flag | Reuse the existing `codebase_index.json`. |
| `--show-summary` | flag | Print `repo_map_summary.md`. |

**Outputs**:

- `code_task/meta/codebase_index.json`
- `code_task/meta/repo_map.json`
- `code_task/meta/repo_map_summary.md`

**Notes**:

This command is deterministic and does not call the LLM.

#### `simple-ar code-task locate`

**Purpose**: rank likely editable files and read-only evidence from the repo map.

**Usage**:

```bash
uv run simple-ar code-task locate runs/<run-id> --query "improve classifier"
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Code-task run directory. |
| `--query TEXT` | string | Locate query. Defaults to `code_task/task.md`. |
| `--top-k N` | int | Candidate count per group. Default: `8`. |
| `--refresh-map` | flag | Rebuild index and repo map before ranking. |
| `--no-read-only` | flag | Omit protected read-only evidence files. |
| `--show-summary` | flag | Print `locate_results.md`. |

**Outputs**:

- `code_task/meta/locate_results.json`
- `code_task/meta/locate_results.md`

**Notes**:

Protected evidence such as tests and benchmarks can inform planning but is not
editable by default.

#### `simple-ar code-task context`

**Purpose**: build a bounded prompt-ready context pack.

**Usage**:

```bash
uv run simple-ar code-task context runs/<run-id> --max-files 8 --max-total-chars 20000
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Code-task run directory. |
| `--query TEXT` | string | Locate query. Defaults to `task.md`. |
| `--top-k N` | int | Locate candidate budget. |
| `--max-files N` | int | Maximum snippets included. |
| `--max-source-chars-per-file N` | int | Per-file snippet budget. |
| `--max-total-chars N` | int | Total snippet budget. |
| `--refresh-map` | flag | Rebuild map before packing context. |
| `--show-prompt` | flag | Print `prompt_context.md`. |

**Outputs**:

- `code_task/context_packs/context-NNN/context_pack.json`
- `code_task/context_packs/context-NNN/prompt_context.md`
- `code_task/context_packs/context-NNN/selected_snippets.jsonl`

**Notes**:

This command does not modify files.

#### `simple-ar code-task probe`

**Purpose**: inspect runtime and project environment signals.

**Usage**:

```bash
uv run simple-ar code-task probe runs/<run-id>
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Code-task run directory. |
| `--env-mode MODE` | enum | `current` or `external`. |
| `--python PATH` | path | Python executable for external env mode. |

**Outputs**:

- `code_task/meta/environment_report.json`

**Notes**:

`probe` does not install dependencies or run project benchmark code.

#### `simple-ar code-task baseline`

**Purpose**: run the recorded benchmark before applying edits.

**Usage**:

```bash
uv run simple-ar code-task baseline runs/<run-id> --timeout 60
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Code-task run directory. |
| `--command TEXT` | string | Override the benchmark command for this run. |
| `--timeout N` | int | Benchmark timeout. |
| `--skip-validation` | flag | Run even when static validation has not passed. |
| `--env-mode MODE` | enum | `current` or `external`. |
| `--python PATH` | path | Python executable for external env mode. |

**Outputs**:

- `code_task/run/baseline/execution_report.json`
- `code_task/run/baseline/stdout.txt`
- `code_task/run/baseline/stderr.txt`
- `code_task/run/baseline/metrics.json`

**Notes**:

The benchmark command runs inside `code_task/workspace`.

#### `simple-ar code-task work-plan`

**Purpose**: generate a batch-oriented implementation work plan.

**Usage**:

```bash
uv run simple-ar code-task work-plan runs/<run-id>
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Code-task run directory. |
| `--model NAME` | string | Model override. |
| `--no-llm` | flag | Use fallback planning. |
| `--force` | flag | Regenerate existing work-plan artifacts. |
| `--allow-planning-fallback` | flag | Allow deterministic fallback if LLM work planning fails. |
| `--llm-retry-attempts N` | int | LLM work planning attempts before failing or falling back. |
| `--max-files N` | int | Planning context file budget. |
| `--max-source-chars-per-file N` | int | Per-file source context budget. |

**Outputs**:

- `code_task/work_plan.json`
- `code_task/work_plan.md`

**Notes**:

Work-plan target files become part of the later edit-scope check.

#### `simple-ar code-task batch`

**Purpose**: create an attempt/batch state directory for one work-plan item.

**Usage**:

```bash
uv run simple-ar code-task batch runs/<run-id> --work-item W1
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Code-task run directory. |
| `--work-item ID` | string | Work-plan item id, such as `W1`. Required. |
| `--attempt-id ID` | string | Optional attempt id, such as `attempt-001`. |
| `--force` | flag | Create a new batch even when one already exists. |

**Outputs**:

- `code_task/attempts/attempt-NNN/attempt_state.json`
- `code_task/attempts/attempt-NNN/batches/batch-NNN/batch_state.json`

**Notes**:

`batch` does not call the LLM or edit files.

#### `simple-ar code-task plan`

**Purpose**: generate a human-reviewable patch plan for the active batch.

**Usage**:

```bash
uv run simple-ar code-task plan runs/<run-id>
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Code-task run directory. |
| `--model NAME` | string | Model override. |
| `--no-llm` | flag | Use fallback plan. |
| `--force` | flag | Regenerate existing plan. |
| `--allow-planning-fallback` | flag | Allow deterministic fallback if LLM patch planning fails. |
| `--llm-retry-attempts N` | int | LLM patch planning attempts before failing or falling back. |
| `--max-files N` | int | Context file budget. |
| `--max-source-chars-per-file N` | int | Per-file source context budget. |

**Outputs**:

- `code_task/patch_plan.md`

**Notes**:

Run `decide-plan` before generating edit proposals.

#### `simple-ar code-task propose-edits`

**Purpose**: ask the model to produce controlled old/new text edits.

**Usage**:

```bash
uv run simple-ar code-task propose-edits runs/<run-id>
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Code-task run directory. |
| `--model NAME` | string | Model override. |
| `--no-llm` | flag | Write deterministic empty proposal. |
| `--force` | flag | Regenerate existing proposal. |
| `--max-files N` | int | Editable context file budget. |
| `--max-source-chars-per-file N` | int | Per-file source context budget. |
| `--allow-large-edits` | flag | Accept a large but bounded proposal after review. |

**Outputs**:

- `code_task/meta/proposed_edits.json`
- `code_task/meta/proposal_warnings.json`
- per-batch proposal artifacts when an active batch exists

**Notes**:

The proposal format is structured JSON, not a unified diff.

#### `simple-ar code-task apply-edits`

**Purpose**: safely apply controlled old/new text edits inside the workspace.

**Usage**:

```bash
uv run simple-ar code-task apply-edits runs/<run-id>
uv run simple-ar code-task apply-edits runs/<run-id> --edits-file runs/<run-id>/code_task/repairs/repair-001/proposed_edits.json
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Code-task run directory. |
| `--edits-file PATH` | path | Specific proposal file to apply. |
| `--allow-unapproved-plan` | flag | Bypass plan approval for local tests/demos. |
| `--allow-large-edits` | flag | Apply a reviewed proposal that requires large-edit approval. |

**Outputs**:

- modifies files under `code_task/workspace`
- `code_task/meta/applied_edits.json`
- `code_task/patch.diff`

**Notes**:

Path, edit-scope, old-text, and large-edit checks run before file writes.

#### `simple-ar code-task validate`

**Purpose**: run lightweight static validation over the workspace.

**Usage**:

```bash
uv run simple-ar code-task validate runs/<run-id> --strict
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Code-task run directory. |
| `--strict` | flag | Treat higher-risk warnings as errors. |
| `--max-file-bytes N` | int | Maximum file size scanned. |

**Outputs**:

- `code_task/meta/validation_report.json`

**Notes**:

Validation is static and intentionally conservative.

#### `simple-ar code-task run`

**Purpose**: run the benchmark after edits.

**Usage**:

```bash
uv run simple-ar code-task run runs/<run-id> --timeout 60
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Code-task run directory. |
| `--command TEXT` | string | Override recorded benchmark command. |
| `--timeout N` | int | Benchmark timeout. |
| `--skip-validation` | flag | Run even when static validation has not passed. |
| `--env-mode MODE` | enum | `current` or `external`. |
| `--python PATH` | path | Python executable for external env mode. |

**Outputs**:

- `code_task/run/patched/execution_report.json`
- `code_task/run/patched/stdout.txt`
- `code_task/run/patched/stderr.txt`
- `code_task/run/patched/metrics.json`
- `code_task/run/comparison.json` when baseline metrics also exist

**Notes**:

Metric comparison distinguishes "benchmark passed" from "objective improved."

#### `simple-ar code-task analyze-failure`

**Purpose**: summarize the latest failed validation or benchmark result.

**Usage**:

```bash
uv run simple-ar code-task analyze-failure runs/<run-id>
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Code-task run directory. |

**Outputs**:

- `code_task/run/patched/failure_analysis.md` or `code_task/meta/failure_analysis.md`

**Notes**:

This command is deterministic and does not modify source files.

#### `simple-ar code-task repair`

**Purpose**: propose bounded repair edits from the latest failure context.

**Usage**:

```bash
uv run simple-ar code-task repair runs/<run-id>
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Code-task run directory. |
| `--model NAME` | string | Model override. |
| `--no-llm` | flag | Write deterministic empty repair proposal. |
| `--max-files N` | int | Repair context file budget. |
| `--max-source-chars-per-file N` | int | Per-file source context budget. |

**Outputs**:

- `code_task/repairs/repair-NNN/proposed_edits.json`
- updates `code_task/summary.md`

**Notes**:

Repair proposals are not applied automatically. Review and apply them with
`apply-edits --edits-file ...`.
