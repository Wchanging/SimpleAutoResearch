# CLI Reference

[中文版本](CLI_REFERENCE_zh.md)

This page is a command lookup for SimpleAutoResearch. It intentionally focuses
on command syntax, options, outputs, and short operational notes.

For ordinary V2.8 use, `simple-ar research-session` is the only formal user
entrypoint for the bounded research-to-report flow. `research-report` can
continue a canonical session created with `--no-report`; the current
`research-session-continue` uses the canonical retry boundary for v2 sessions
only. Legacy v1 sessions remain read-only; use `research-session-migrate` to
import evidence into a new canonical successor rather than execute the old workflow.
`research-brief` is a segmented development or diagnostic interface.
The old `research-experiment`, `research-code-task` and `run/resume` execution commands are retired;
`status` and artifact tools still read historical outputs. Old stage options are
not silently translated to the canonical application.

- Installation and walkthroughs: [Usage And Configuration](USAGE.md)
- Workflow concepts and artifacts: [Workflows And Artifacts](WORKFLOWS.md)
- TOML schema and examples: [Configuration Reference](CONFIG_REFERENCE.md)

## Command Overview

| Command | Purpose |
| --- | --- |
| `simple-ar research-session` | Canonical entry for a bounded task-driven session; the accepted plan selects applicable research and execution steps. |
| `simple-ar research-session-continue` | Retry one failed canonical explicit experiment. |
| `simple-ar research-session-migrate` | Create a canonical successor from a read-only `session_manifest.v1`. |
| `simple-ar research-report` | Generate and audit a report from a completed research session. |
| `simple-ar research-brief` | Segmented/development interface for an evidence-backed research brief. |
| `simple-ar status` | Print status for a research run or code-task run. |
| `simple-ar tools ...` | Export tool schemas, call run-local tools, or serve read-only tools over MCP stdio. |
| `simple-ar inspect` | Build a local artifact index for a run. |
| `simple-ar search-artifacts` | Search indexed run artifacts. |
| `simple-ar clean` | Preview and remove rebuildable run caches. |
| `simple-ar code-task ...` | Work with an existing codebase or a greenfield code task in an isolated editable workspace. |

## Research Pipeline


### `simple-ar research-brief` (segmented/development interface)

**Purpose**: build a small evidence-backed brief from a topic or local
Markdown/text documents.

**Usage**:

```bash
uv run simple-ar research-brief \
  --topic "reliable agents" \
  --local-document examples/research_brief/fixtures/reliable_agents.md \
  --output-root runs/research-brief
```

The command creates a timestamped session with separate plan, search, document
ingest, read, and synthesize attempts. It does not silently retry or overwrite an attempt;
`--query`, `--provider`, `--max-results`, `--max-chunks`, and `--idea-limit` are
the deliberately small controls for this path. Pass the same optional
`--cache-dir` to later sessions to reuse downloaded full-text files; when it is
omitted, the cache stays inside the current session.

The capability is deterministic when `--model` is omitted. To use the shared
LLM transport for question/query planning and evidence synthesis, opt in
explicitly:

```bash
uv run simple-ar research-brief \
  --topic "reliable agents" \
  --local-document examples/research_brief/fixtures/reliable_agents.md \
  --model "$SIMPLE_AR_MODEL"
```

The run prints and persists the selected mode. LLM mode requires the normal
`.env` provider settings; a missing key or failed model response is reported
as a failed attempt rather than replaced by deterministic prose.

### Retired `simple-ar research-experiment`

The separate design→experiment→analysis creator is retired. Use `research-session`
for research tasks. The old `--synthesis-file` argument is not silently translated.
Domain capabilities remain composable at library level; historical artifacts remain readable.

### `simple-ar research-session` (canonical task-driven entry)

Invalid LLM task plans get at most one correction attempt, never a silent fixed-plan fallback.
Returned proposals and validation errors are saved in `attempts/plan-*/task_plan_proposals.json`;
if no valid plan is produced, the session pauses without executing downstream actions.

Use `simple-ar research-session --config examples/research_config/minimal.toml`
for a file-based start. The [configuration reference](CONFIG_REFERENCE.md) describes
the minimal and advanced templates, which share the same parser and defaults.
Explicit CLI options override the file. `--outputs` selects `summary`, `report`,
and/or `experiments`; `--total-tokens`, `--llm-requests`, `--max-output-tokens`,
`--process-invocations`, and `--process-wall-seconds` expose the corresponding limits.
Add `--session-root PATH` to continue the saved accepted plan. Omitted goal/outputs remain
unchanged; a supplied new goal, output set, local document, or execution configuration is an
explicit revision. The application keeps attempt history and reuses only measurements whose
command, result schema, protocol, preparation lineage, and protected assets still match; other
dependent steps are replanned. Changing task kind still requires a new session.
On resume, saved report settings remain in force unless a report option is
explicitly supplied; on a session that already requests a report, an explicit
report change rebuilds only report outputs. Use `research-report` to add the
report deliverable to a prefix that did not request one.
Changed search/ingestion settings that cannot be revised against the saved
session are rejected before execution; start a new session to apply them.
`[research].max_iterations` controls bounded follow-up rounds; `0` stops after
the first analysis.

#### Interaction policies and pending decisions

New CLI sessions default to `checkpoints`. Select `assisted`, `checkpoints`, or
`autonomous` with `--interaction`; TOML uses `[research] interaction = "checkpoints"`.
Sessions without a saved interaction field keep their legacy behavior. No mode supplies
missing facts, assets, or permissions; those remain hard blockers and cannot be accepted
without providing the required input.

Apply a mode change separately from `--reanalyze` or a report-only refresh; these
combinations are rejected rather than silently ignoring the mode. A delivery
decision reply can include both its report settings and a mode change.

- `assisted` confirms the first execution protocol and key delivery choices, and asks
  about research choices such as a supplement or candidate revision.
- `checkpoints` confirms the first protocol, material research-direction changes, and
  automatic delivery tradeoffs; bounded same-protocol supplements remain authorized.
- `autonomous` chooses among valid options within the saved round limit, but pauses
  when evidence or prerequisites do not support a safe next action.

Rich shows the decision id, question, rationale, options, and continuation commands.
Reply through the same `research-session` entry:

```bash
simple-ar research-session --session-root runs/research-session/<session> --topic "Original research goal" \
  --decision-id ID_FROM_RICH --decision-response accept

simple-ar research-session --session-root runs/research-session/<session> --topic "Original research goal" \
  --decision-id ID_FROM_RICH --decision-response revise \
  --decision-guidance "Add facts or give the revised research direction"
```

`--decision-response` accepts `accept`, `reject`, or `revise`. Revising a delivery
choice also requires a report setting, such as `--report-template analysis_report`.
TOML can provide `decision_id`, `decision_response`, and `decision_guidance` under
`[continuation]`. The proposal and its answer are separate research-decision artifacts;
the answer records its brief, execution, or report revision. If a reply is saved before those
inputs and the process stops, replaying the same decision restores the saved revision; different
revision terms are rejected. An exact replay after persistence does not repeat completed work,
and changed inputs make an old decision stale.
Rich is read-only and never waits for terminal stdin.

Continuation allowances use the same command and may be supplied as CLI options or a
`[continuation]` TOML table. `--authorize-remaining DIMENSION=AMOUNT` (repeatable) defines a
new remaining allowance for calls after this authorization; it does not estimate or resolve
unknown historical usage. `--additional-attempts` and `--additional-no-progress` raise the
persisted caps without resetting prior counters. Every allowance requires a stable
`--authorization-id` and `--authorization-reason`; replaying the same id and terms is
idempotent, while changed terms require a new id. Never edit the ledger or manifest directly.

On a completed session, authorization without input revisions keeps the session completed
and executes no research or report actions. Authorize capacity first, then run
`research-report --refresh` or repeat the original report-configuration command.
A full report refresh normally needs three attempts (writer, assembly, audit); failed retries
cost additional attempts. Report-only recovery does not require increasing process budgets.

```bash
simple-ar research-session --config research.toml --session-root runs/research-session/<session> \
  --local-document new-paper.md \
  --authorization-id review-20260924-1 --authorization-reason "Bounded continuation after review" \
  --additional-attempts 2 --additional-no-progress 1 \
  --authorize-remaining total_tokens=50000 --authorize-remaining llm_requests=8
```

At a paused or completed analysis checkpoint, use `--reanalyze` separately from input
revisions or continuation authorizations to reconsider
existing measurements and resume research decisions without requesting a report.
Historical artifacts and consumed budgets remain intact; an accepted follow-up
may run new experiments within the existing budget. Pending research follow-ups
must be continued before requesting reanalysis.

The completion table resolves the latest implementation, candidate measurement,
and analysis from the accepted plan. Earlier revision refs remain separately
listed so the candidate lineage is still navigable.

**Purpose**: run one bounded session whose accepted plan selects the steps
applicable to the task and supplied assets. With an execution command or
`--code-task-config`, the plan may include preparation, implementation,
measurement, and analysis. Without either, the session remains literature-only
and never creates an execution request or process.

When a model is available, the same application continues through `report` and
`report_audit`; `--no-report` is for debugging or prefix-only inspection. A
later `research-report` call can add that deliverable to a completed canonical
prefix without rerunning its research evidence or experiment. Use
`research-session-continue` only for an explicit recovery/revision decision.

**Usage** (the command must be the final option):

```bash
uv run simple-ar research-session \
  --topic "reliable agents" \
  --local-document examples/research_brief/fixtures/reliable_agents.md \
  --cwd examples/research_brief/fixtures \
  --primary-metric accuracy \
  --metric-direction accuracy=higher \
  --command python -c "print('accuracy: 0.75')"
```

To use the existing Code-Task implementation backend in the same session,
omit `--command` and provide a Code-Task TOML plus a model:

```bash
uv run simple-ar research-session \
  --topic "reliable agents" \
  --local-document examples/research_brief/fixtures/reliable_agents.md \
  --code-task-config examples/code_task_medium_review/configs/code_task.toml \
  --model "$SIMPLE_AR_MODEL" \
  --output-root runs/research-session
```

The TOML remains the source of Code-Task project, benchmark, workspace,
baseline, and execution settings. The generated code-task artifacts stay under
the preparation/implementation attempts of the session and are normalized
into the same canonical result consumed by Analysis; no second code generator
is introduced.

The canonical application reuses the established Code-Task implementation and
validation capabilities inside its isolated preparation workspace. If the
proposal requires the `large` budget, `[execute].allow_large_edits = true` must
be set explicitly in the Code-Task TOML after reviewing the proposal;
otherwise the session preserves its artifacts and stops at the approval
boundary.

The optional `--cache-dir` is forwarded to document ingest. Valid cached
full-text files are reused on later sessions; the default remains session-local
for backward compatibility.

It preserves the same attempt-local artifacts as the individual entries and
adds no implicit retry or repair policy. Use `research-brief` for literature-only preparation; experiments stay in the
canonical research session.
For a session created with `--no-report`, use the narrow
`simple-ar research-report` command later to request the report. It reopens the
canonical application only for the missing report actions and reuses the
persisted synthesis, measurements, and analysis. For a single explicit
invocation, add `--model NAME --with-report` before the final `--command`;
`--report-reviewer`, `--max-review-iterations`, and `--max-section-tokens` are
stored in the same application configuration. The last option accepts `0` to
leave the per-section provider output cap unset.
Writer execution and checkpoints belong to `report/writing.py` in the canonical
application. Historical report-input projections are read-only; the former
standalone report-session execution APIs have been retired.

Omitting `--model` keeps planning, reading, synthesis, design selection, and
analysis deterministic. When `--model NAME` is supplied, the same shared
client is used for planning, bounded reading/screening and paper notes,
synthesis, selection among grounded research ideas, and result analysis;
provider failures remain visible and are not silently converted into offline
output.

To inspect a persisted capability session without rerunning it, use the existing
status command:

```bash
uv run simple-ar status runs/research-session/<session>
```

When the directory contains the canonical `session_manifest.json` (schema
`session_manifest.v2`), status prints the application state, current attempt,
bounded budget, attempt counts, and last decision. A legacy
`session_manifest.v1` remains read-only compatibility input. Existing pipeline
and Code-Task directories keep their original status behavior.

When an open session has no active attempt, status may also print
`Handoff: ready_for_report` or `Continuation: explicit ...`. These are persisted
next-step hints only; they do not mean a background process is running, and the
caller must invoke the next operation explicitly.

### `simple-ar research-session-continue`

**Purpose**: run one caller-supplied recovery experiment in an existing
session. For a canonical `session_manifest.v2`, it reuses literature, design,
and the failed candidate attempt, then runs deterministic analysis again. The
canonical boundary is deliberately limited to a simple explicit experiment;
paired runs, prepared data, and CodeTask failures use their own bounded paths.
Legacy `session_manifest.v1` execution is retired. Migration imports evidence,
not an executable continuation of the old stage plan.

**Usage** (the command must be the final option):

```bash
uv run simple-ar research-session-continue \
  --session-root runs/research-session/<session> \
  --cwd examples/research_brief/fixtures \
  --primary-metric accuracy \
  --metric-direction accuracy=higher \
  --command python -c "print('accuracy: 0.90')"
```

For a canonical session, the corrected command must replace a technical
`failed`/`timed_out` candidate; a scientific negative result is retained as
evidence and is not silently retried. The new attempt records the previous
experiment as its parent, preserves the original attempt directory, and does
not rerun search or design. A report-requesting session returns with the next
report action; otherwise the repaired result is analyzed and the session can
complete. Historical sessions are not modified by this command.

### `simple-ar research-session-migrate`

**Purpose**: create a new canonical session from an old `session_manifest.v1`
without rewriting the old directory. The importer does not guess historical
budget remaining or translate unknown artifacts into current state. Copy only
small, explicitly named state artifacts when their files are available.

**Usage**:

```bash
uv run simple-ar research-session-migrate \
  --source-root runs/legacy/<session> \
  --destination-root runs/research-session/<successor> \
  --artifact search \
  --requested-output report
```

The command prints the new session path, `parent_session`, migration artifact,
imported/skipped counts, and `Historical budget: unknown_not_imported`. The
destination must be empty and outside the legacy session root.

### `simple-ar research-report`

**Purpose**: continue an existing `research-session` whose analysis handoff
is ready for reporting. The command uses the existing report Writer/Reviewer,
assembler, and audit implementations; it does not rerun search or the
experiment.

**Usage**:

```bash
uv run simple-ar research-report \
  --session-root runs/research-session/<session> \
  --model "$SIMPLE_AR_MODEL"
```

Use `--max-section-tokens N` to set an explicit Writer/Reviewer output cap
per section. `0` omits that per-call cap, which is the default for report
runtime configuration; the session budget still applies.

The report and audit are appended as new attempts under the same session. A
second invocation with an existing canonical report is idempotent: it reads
the completed state and does not rerun the Writer. Explicit `--refresh` reuses
the retained analysis and creates new report/audit attempts while retaining old
reports; it does not rerun measurements or analysis. Model-backed
sessions use the model to interpret hypotheses; numeric and execution-status
checks remain deterministic. Refresh uses the remaining session budget.
Omitted template/reviewer/limit flags keep saved settings. Explicit options
update report configuration and rebuild only report outputs. If the persisted
attempt/no-progress cap is exhausted, use `research-session` with an explicit
continuation authorization; report generation has no cap bypass.
Use `--reviewer disabled`
only when an explicit writer-only comparison is wanted; the final audit still
runs. Legacy sessions remain readable but cannot be resumed by a second report
executor. An invalid canonical session fails explicitly instead of falling back
to another workflow.

### Retired segmented CodeTask command

`research-code-task` and its separate session executor have been retired.
Use `research-session --code-task-config` for research tasks or standalone
`code-task` for coding tasks. Historical segmented results remain readable.


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

## Tools And MCP

### `simple-ar tools schema`

**Purpose**: export registered real tool schemas in MCP or OpenAI function-tool
format.

**Usage**:

```bash
uv run simple-ar tools schema --format mcp
uv run simple-ar tools schema --format openai --output tool_schema.json
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `--format` | enum | `mcp` or `openai`. Default: `mcp`. |
| `--output PATH` | path | Optional file output. Prints to stdout when omitted. |

### `simple-ar tools call`

**Purpose**: call one run-local read-only tool and write a compact trace.

**Usage**:

```bash
uv run simple-ar tools call runs/<run-id> list_experiment_artifacts
uv run simple-ar tools call runs/<run-id> search_generated_code --args-json '{"query":"run_experiment","max_matches":10}'
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Existing run directory. |
| `TOOL_NAME` | string | Registered tool name. |
| `--args-json JSON` | object | Tool arguments as a JSON object. |
| `--args-file PATH` | path | Read tool arguments from a JSON file. Useful on shells where inline JSON quoting is awkward. |
| `--debug-payloads` | flag | Keep larger trace payloads. Default traces are compact. |

**Outputs**:

- tool result JSON on stdout;
- `RUN_DIR/tools/tool_trace.jsonl`.

### `simple-ar tools serve-mcp`

**Purpose**: expose run-local read-only tools over MCP stdio.

**Usage**:

```bash
uv run simple-ar tools serve-mcp runs/<run-id>
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `RUN_DIR` | path | Existing run directory whose artifacts tools may inspect. |
| `--debug-payloads` | flag | Keep larger trace payloads. |

**Notes**:

- current server methods: `initialize`, `ping`, `tools/list`, `tools/call`;
- only real registered read-only experiment tools are exposed by default;
- no write, shell, network, or dependency-install tool is enabled by this command.

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
| `--shared-cache` | flag | Strongest shared cleanup: clear shared research indexes, literature provider cache, and external-agent handoff archives. |
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

`--shared-cache` is stronger again: it clears the shared research index,
`.simple_ar_cache/literature`, and `.simple_ar_cache/agent_handoff_archives`,
so future runs may need to re-query literature providers, rebuild local indexes,
and will no longer have prior external-agent handoff transcripts.

## Code Task Commands

Code-task commands prepare an isolated workspace under
`runs/<run-id>/code_task/workspace`. Existing-project runs default to `auto`,
which prefers a detached git worktree and falls back to a guarded copy when Git
cannot be used safely. Explicit `git_worktree` fails with an actionable
checklist instead of silently falling back. Greenfield runs start from an empty
workspace and generate the project there. The original project is never edited.

For normal use, start with the high-level orchestration commands. The low-level
primitive commands are mainly for debugging, learning, or fine-grained human
intervention.

### High-Level Orchestration

For existing projects, ordinary `execute` uses one patch plan. Work-plan/batch
creation is opt-in through `--to-step work-plan` / `--to-step batch` or an
existing work plan, including interactive mode. These are not mandatory stages
for every modification.

#### `simple-ar code-task init`

**Purpose**: create a code-task run, prepare the editable workspace, and build
the first code index.

**Usage**:

```bash
uv run simple-ar code-task init --config examples/code_task_medium_review/configs/code_task.toml
uv run simple-ar code-task init --code-root path/to/project --task-file task.md --benchmark-command "python main.py"
uv run simple-ar code-task init --kind greenfield --task-file task.md --benchmark-command "python generated_project/main.py"
```

**Options**:

| Option | Type | Description |
| --- | --- | --- |
| `--config PATH` | path | TOML config for init settings. CLI flags override config values. |
| `--kind MODE` | enum | `existing_project` for patching an existing codebase, or `greenfield` for from-scratch generation. |
| `--code-root DIR` | path | Source project. Required for `existing_project`; optional scaffold/source root for `greenfield`. |
| `--task-file PATH` | path | Markdown/text task description. Required unless set in config. |
| `--output-root DIR` | path | Directory where the code-task run is created. |
| `--name TEXT` | string | Run name suffix. |
| `--benchmark-command TEXT` | string | Command run inside the workspace before and after edits. |
| `--primary-metric NAME` | string | Primary metric for before/after verdicts. |
| `--metric-direction NAME=DIRECTION` | repeatable | Metric direction: `higher`, `lower`, `resource`, or `ignore`. |
| `--env-mode MODE` | enum | `current` or `external`. |
| `--python PATH` | path | Python executable for `--env-mode external`. |
| `--workspace-mode MODE` | enum | `auto`, `copy`, `git_worktree`, `sparse_copy`, or `empty`. `greenfield` defaults to `empty`; existing projects default to `auto`. |
| `--workspace-include GLOB` | repeatable | Include pattern for `sparse_copy`. |
| `--workspace-exclude GLOB` | repeatable | Additional exclude pattern for `sparse_copy`. |
| `--workspace-reuse-source-venv` | flag | Reuse a detected source `.venv` Python as external execution policy. |
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
| `--to-step STEP` | enum | Stop no later than `probe`, `baseline`, `work-plan`, `batch`, `plan`, `propose-edits`, `apply-edits`, `review`, `validate`, `run`, `analyze-failure`, or `repair`. |
| `--dry-run` | flag | Print the next action without writing artifacts. |
| `--model NAME` | string | Model override for LLM-backed steps. |
| `--no-llm` | flag | Use deterministic fallbacks where possible. |
| `--timeout N` | int | Benchmark timeout. |
| `--baseline-policy MODE` | enum | Existing-project baseline handling: `auto`, `run`, `skip`, `provided`, or `none`. Use `skip`/`none` for expensive baselines, or `provided` with a metrics file. |
| `--baseline-metrics-file PATH` | path | JSON or metric-line file used when `--baseline-policy provided`. |
| `--planning-mode MODE` | enum | Greenfield planning: `tool_agent` uses staged planning and bounded review; `compact` uses a single architecture call before retries. |
| `--yes` | flag | Auto-approve inline review gates in normal execute mode; with `--interactive`, auto-continue primitive prompts. Use only after you are comfortable approving the reviewed plan/proposal. |
| `--interactive` | flag | Debug mode: confirm each primitive step instead of running continuously to the next review gate. |
| `--no-review-inline` | flag | Disable inline review prompts and stop at review gates instead. |
| `--skip-validation` | flag | Run benchmark even when static validation has not passed. |
| `--strict-validation` | flag | Treat higher-risk validation warnings as errors. |
| `--validation-max-file-bytes N` | int | Max file size scanned by static validation. |
| `--apply-proposed-edits` | flag | Apply reviewed `proposed_edits.json` after plan approval. |
| `--allow-large-edits` | flag | Allow a reviewed proposal that exceeds the normal edit budget. |
| `--allow-planning-fallback` | flag | Allow deterministic offline work/patch plans after LLM planning retries fail. |
| `--llm-retry-attempts N` | int | Stage-level LLM attempts for work-plan, patch-plan, greenfield architecture/file generation, and repair before stopping or explicitly falling back. |
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
- `code_task/meta/review_report.json` and `review_report_post_run.json`
- `code_task/meta/validation_report.json`
- `code_task/meta/resource_probe.json` and `resource_decision.json` after `probe`
- `code_task/memory/task_memory.md`, `compressed_memory.md`, and `review_findings.jsonl`
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

After edits are applied, `execute` also runs a structured reviewer step before
static validation, then repeats a post-run review when patched metrics are
available. Blocking reviewer findings are written to `code_task/memory/` so the
next repair attempt can reason from the latest failure evidence.

When `--config PATH` is provided, `execute` also reads standalone code-task
generation settings from `[implementation]` and `[resource]`. This is how
greenfield tasks select the local backend or an explicit Codex/Claude/OpenCode
handoff without adding provider-specific CLI flags.

For greenfield tasks, `execute` also writes
`code_task/meta/dependency_advice.json` and `.md` before implementation
planning. The JSON contains a full scan of installed Python distributions; the
terminal output and model context use the compact task-relevant subset. This is
advice-only and never installs packages automatically.

Greenfield planning defaults to `tool_agent`, which writes intermediate
requirements, architecture, interface, file-plan, and planning-review artifacts
under `code_task/meta/planning/`. Use `--planning-mode compact` for a lower-call
planning path; subsequent file generation, review, and execution are shared.

If greenfield review fails with generic recoverable findings, bounded repair
rounds first ask for structured local actions such as unique old/new replacements
or function-level rewrites. Whole-file replacement is kept for structural
file-level failures. Repairs resync `code_task/meta/code_artifacts.json` before
review/validation continues. If the finding is still blocking, execution stops
with the generated files and review report preserved.

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
- `code_task/meta/resource_probe.json`
- `code_task/meta/resource_decision.json`

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
