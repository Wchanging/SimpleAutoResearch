# Usage And Configuration

[中文版本](USAGE_zh.md)

This document explains how to install, configure, and run SimpleAutoResearch. It is the practical user guide; workflow concepts and artifact details live in [Workflows And Artifacts](WORKFLOWS.md).

## Requirements

- Python 3.12 or newer.
- `uv` for dependency management.
- An OpenAI-compatible API key if you want LLM-backed planning, notes, synthesis, report writing, or code edits.

## Installation

Clone the repository:

```bash
git clone https://github.com/Wchanging/SimpleAutoResearch.git
cd SimpleAutoResearch
```

Install dependencies:

```bash
uv sync
```

Check the CLI:

```bash
uv run simple-ar --help
```

## Environment Configuration

Create a local `.env` file:

```bash
cp .env.example .env
```

On PowerShell:

```powershell
Copy-Item .env.example .env
```

Supported settings:

```bash
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.openai.com/v1
SIMPLE_AR_MODEL=gpt-4o-mini
SIMPLE_AR_LLM_TIMEOUT_SEC=120
SIMPLE_AR_MAX_OUTPUT_TOKENS=4096
SIMPLE_AR_INPUT_PRICE_PER_1M=
SIMPLE_AR_OUTPUT_PRICE_PER_1M=
```

Notes:

- `OPENAI_API_KEY` is required for LLM mode.
- `OPENAI_BASE_URL` can point to OpenAI or a third-party OpenAI-compatible `/v1` endpoint.
- `SIMPLE_AR_MODEL` is the default model when `--model` is not supplied.
- `SIMPLE_AR_LLM_TIMEOUT_SEC` bounds each provider request; increase it only
  when deliberately running large prompts.
- `SIMPLE_AR_MAX_OUTPUT_TOKENS` limits the model response size for long coding
  prompts.
- Price fields are optional and only affect cost estimates in usage summaries.

## Research Pipeline (Topic To Report)

Run the default 8-stage pipeline:

```bash
uv run simple-ar run --topic "toy topic" --to-stage report
```

For repeatable multi-option runs, use a top-level TOML config:

```bash
uv run simple-ar run --config examples/run_configs/tiny_digits_mlp_pipeline.toml
```

The config can provide `[run]`, `[llm]`, `[search]`, `[retrieval]`,
`[experiment]`, `[report]`, and the same `[code_task]`/`[benchmark]`/`[metrics]`
sections used by `code-task init --config`. Explicit CLI flags override config
values. See [CLI Reference](CLI_REFERENCE.md#run-config) for a complete
commented config and field-by-field explanation.

Stop early for a literature-only pass (no experiment code/run artifacts):

```bash
uv run simple-ar run --topic "toy topic" --to-stage synthesize
```

Then generate a literature-only report from the existing artifacts:

```bash
uv run simple-ar resume runs/<run-id> --from-stage report
```

By default, report drafting is automatic: if `results.json` is missing, the
report switches to a literature-only structure. You can force a mode:

```bash
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode research_only
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode experiment
```

### What Is LLM-Backed vs Deterministic

- LLM-backed when enabled: `plan`, `read`, `synthesize`, and `report` stages.
- Deterministic by default: `design`, `code`, and `run` use fixed experiment templates unless a code-task experiment template is selected.
- Embedded code-task experiment: `06-code` can call the LLM for a work plan, patch plan, and controlled edit proposal, but the patch is applied only inside an isolated workspace under the run directory.
- Guarded reports: if an LLM-written report omits required body citations, invents citation keys, or overstates fixture/toy evidence, the report stage writes a structured fallback report instead.
- `--no-llm` forces offline fallbacks with placeholder content in `goal.md`, `notes.md`, `synthesis.md`, and `report.md`.

### Search Modes And Boundaries

Default search behavior:

- `search` queries OpenAlex first, then arXiv.
- If a live provider fails and `--strict-search` is not set, cached metadata is used when available.

Explicit search controls:

```bash
uv run simple-ar run --topic "agent simulation" --to-stage search --strict-search
uv run simple-ar run --topic "agent simulation" --to-stage report --allow-fixture-fallback
uv run simple-ar run --topic "agent simulation" --to-stage report --offline-search
```

- `--strict-search` disables cache fallback for live providers.
- `--allow-fixture-fallback` allows placeholder metadata when live providers and cache fail.
- `--offline-search` skips live providers and uses fixture metadata immediately.

### Resume And Status

Resume a run:

```bash
uv run simple-ar resume runs/<run-id>
uv run simple-ar resume runs/<run-id> --from-stage run --to-stage report
```

Show run status:

```bash
uv run simple-ar status runs/<run-id>
```

## Retrieval And Artifact Tools

Use these when you want to inspect or search files produced by a run:

```bash
uv run simple-ar inspect runs/<run-id>
uv run simple-ar search-artifacts runs/<run-id> "accuracy"
uv run simple-ar run --topic "toy topic" --to-stage report --retrieval-top-k 4
uv run simple-ar run --topic "toy topic" --to-stage report --no-retrieval
```

See [CLI Reference](CLI_REFERENCE.md#artifact-tools) for option details.

## Code Task Workflow

The code-task workflow prepares a source project under an isolated editable
workspace and never mutates the original codebase. The default `copy` mode is
the safest choice; V2.2 also supports `git_worktree` for larger repo-root git
projects where a full copy is wasteful, plus experimental `sparse_copy` for
small allowlisted subsets. The workflow is intentionally step-by-step so each
stage can be reviewed.

Initialize from a TOML config so project paths, benchmark metrics, workspace
mode, model routing, and edit budgets stay in one reviewable file:

```bash
uv run simple-ar code-task init --config examples/code_tasks/configs/tiny_digits_mlp.toml
```

For a slightly more realistic local example, use the medium review pipeline:

```bash
uv run simple-ar code-task init --config examples/code_tasks/configs/medium_review_pipeline.toml
```

That example runs `python main.py --config configs/experiment.json
--show-progress`, prints newline progress bars during baseline/patched runs,
and uses `[execute].stream_benchmark_output = "auto"` so `code-task execute`
relays benchmark progress while still saving stdout/stderr artifacts. The
`auto` mode handles both normal `print` logs and carriage-return progress
output such as `tqdm`.

`init` creates a new `runs/<run-id>/` directory, prepares the source project under
`code_task/workspace/`, writes the task to `code_task/task.md`, builds
`code_task/meta/codebase_index.json` plus the layered
`code_task/meta/repo_map.json` / `repo_map_summary.md`, and records the
benchmark/environment policy in `manifest.json`. It does not run code, call the
LLM, or modify the original source project.

When `workspace.mode = "git_worktree"` or `--workspace-mode git_worktree` is
used, `init` creates a detached git worktree at the same
`code_task/workspace/` path instead of copying files. This mode currently
requires `code_root` to be the repository root, records git provenance under
`manifest.json.workspace`, and keeps `.git`/`.env` metadata out of the codebase
index and model context. It still does not install dependencies.

If `git_worktree` init fails, the CLI prints a checklist instead of a Python
traceback. The usual fixes are: pass the baseline repository root as
`--code-root`, create an initial local commit with `git init`, `git add .`, and
`git commit -m "initial baseline"`, or switch back to `--workspace-mode copy`.

When `workspace.mode = "sparse_copy"` or `--workspace-mode sparse_copy` is
used, init copies only selected files. Configure patterns with
`[workspace].include` / `[workspace].exclude` or repeated
`--workspace-include` / `--workspace-exclude`. Built-in exclusions still block
`.git`, virtualenvs, `runs`, cache/build directories, `data`, `models`, `.env`,
and secret-like paths. This mode is useful for small known subsets, but it can
omit runtime dependencies; prefer `copy` or `git_worktree` for general projects.

Benchmarks should print numeric metric lines as `name: value`. Custom metric
names work when you declare their direction in TOML. Explicit CLI flags are
still supported for experiments and quick tests, but the TOML path is the
recommended public workflow. See [CLI Reference](CLI_REFERENCE.md#init) for the
full option table and [CLI Reference](CLI_REFERENCE.md#init-config) for the
config schema.

### Recommended Path: TOML + Execute

For normal use, prefer a TOML config plus the state-aware executor. This keeps
commands short while preserving review gates for the patch plan and edit
proposal.

Start a run:

```bash
uv run simple-ar code-task init --config examples/code_tasks/configs/tiny_digits_mlp.toml
```

The commands below use the tiny digits MLP config. To run the Day27 medium
review pipeline with visible benchmark progress, replace the config path with
`examples/code_tasks/configs/medium_review_pipeline.toml` and use the run-name
filter `*medium-review-pipeline*` when setting `$RUN`. During `execute`, you
should see relayed lines such as `benchmark stdout: round 1/4 ...` while the
same output is captured under `code_task/run/<label>/stdout.txt`. Because the
medium task naturally touches feature extraction, model scoring, and config, it
usually produces a reviewed `large` batch; add `--allow-large-edits` to the
final apply command only after inspecting the proposal.

For the medium example, the matching PowerShell run selector is:

```powershell
$RUN = Join-Path "runs" ((Get-ChildItem .\runs -Directory |
  Where-Object { $_.Name -like "*medium-review-pipeline*" } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1).Name)
```

Set `RUN` to the newest matching run directory if you want copyable commands:

```powershell
$RUN = Join-Path "runs" ((Get-ChildItem .\runs -Directory |
  Where-Object { $_.Name -like "*tiny-digits-mlp*" } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1).Name)
```

`init` writes the isolated workspace and static project map. The important
artifacts are:

- `code_task/workspace/`: editable copy/worktree used by the model.
- `code_task/task.md`: the task prompt copied from the config.
- `code_task/meta/codebase_index.json`: lightweight file index.
- `code_task/meta/repo_map.json` and `repo_map_summary.md`: layered project map.
- `manifest.json`: benchmark, workspace, environment, and safety policy.

Continue to the first human review gate:

```powershell
uv run simple-ar code-task execute $RUN --config examples/code_tasks/configs/tiny_digits_mlp.toml
```

This usually writes:

- `code_task/meta/environment_report.json`: OS, Python, GPU, dependency, and test signals.
- `code_task/run/baseline/metrics.json`: baseline benchmark metrics.
- `code_task/work_plan.md`: LLM-generated implementation batches.
- `code_task/attempts/attempt-001/batches/batch-001/batch_state.json`: active batch state.
- `code_task/patch_plan.md`: reviewable patch plan.

Read `code_task/work_plan.md` and `code_task/patch_plan.md`. If the plan is
reasonable, approve it:

```powershell
uv run simple-ar code-task decide-plan $RUN --decision approve --note "reviewed"
```

Generate an edit proposal, but do not apply it yet:

```powershell
uv run simple-ar code-task execute $RUN `
  --config examples/code_tasks/configs/tiny_digits_mlp.toml `
  --to-step propose-edits
```

Review:

- `code_task/meta/proposed_edits.json`: controlled old/new replacements.
- `code_task/meta/llm_usage_summary.json`: LLM token usage summary.
- latest `code_task/attempts/.../proposal_warnings.json`, when present.

The default editor backend is `controlled_patch`. Its metadata is recorded in
`proposed_edits.json`, the active batch state, `applied_edits.json`, and
`manifest.json.patch`. The backend does not run benchmarks, approve plans, or
write reports; those gates remain owned by the code-task workflow.

Apply the reviewed proposal and evaluate the patched workspace:

```powershell
uv run simple-ar code-task execute $RUN `
  --config examples/code_tasks/configs/tiny_digits_mlp.toml `
  --apply-proposed-edits `
  --timeout 60
```

Then inspect the result:

```powershell
uv run simple-ar status $RUN
```

Key output files:

- `code_task/patch.diff`: readable diff for manual review.
- `code_task/meta/applied_edits.json`: applied proposal path and changed file hashes.
- `code_task/meta/validation_report.json`: static validation results.
- `code_task/run/patched/metrics.json`: patched benchmark metrics.
- `code_task/run/comparison.json`: baseline-vs-patched verdict and metric deltas.
- `code_task/summary.md`: compact final summary and next-step guidance.

Treat `objective_improved` or `objective.status = "improved"` as the normal
success signal. A patched benchmark can pass while `objective.status` is
`regressed` or `mixed`; in that case, the code ran but the measured task goal
was not really met.

If the proposal needs repair, ask for one bounded repair proposal:

```powershell
uv run simple-ar code-task execute $RUN `
  --config examples/code_tasks/configs/tiny_digits_mlp.toml `
  --to-step repair `
  --repair-rounds 1 `
  --timeout 60
```

Review the newest `code_task/repairs/repair-NNN/proposed_edits.json`, then
apply it explicitly:

```powershell
uv run simple-ar code-task apply-edits $RUN `
  --edits-file "$RUN\code_task\repairs\repair-NNN\proposed_edits.json"
uv run simple-ar code-task validate $RUN
uv run simple-ar code-task run $RUN --timeout 60
uv run simple-ar status $RUN
```

Preview the next executor action without writing artifacts:

```powershell
uv run simple-ar code-task execute $RUN --config examples/code_tasks/configs/tiny_digits_mlp.toml --dry-run
```

### Optional Mapping And Context Tools

Refresh the code map at any time:

```bash
uv run simple-ar code-task map runs/<run-id>
```

`map` scans the current `code_task/workspace/`, refreshes
`code_task/meta/codebase_index.json`, writes `code_task/meta/repo_map.json` and
`code_task/meta/repo_map_summary.md`, and updates `manifest.json`. It does not
call the LLM, install dependencies, run benchmark code, or modify the original
source project.

Locate likely files before planning or editing:

```bash
uv run simple-ar code-task locate runs/<run-id> --query "improve spam keyword prediction"
```

`locate` writes `code_task/meta/locate_results.json` and
`code_task/meta/locate_results.md`. It ranks editable targets separately from
read-only evidence such as tests and benchmarks, using the repo map rather than
loading the whole project into a prompt. It does not call the LLM or read files
outside the prepared workspace.

Build a bounded prompt context pack:

```bash
uv run simple-ar code-task context runs/<run-id> --max-files 8 --max-total-chars 20000
```

`context` creates `code_task/context_packs/context-NNN/` containing
`context_pack.json`, `prompt_context.md`, and `selected_snippets.jsonl`. The
pack records token-like character budgets, selected editable files,
read-only evidence, truncated snippets, and omitted files. It is a reviewable
intermediate artifact for LLM planning/editing. When a latest context pack
exists, `plan` uses it for planning context, while `propose-edits` uses only
its editable snippets and keeps tests/benchmarks as read-only evidence.

### Manual Primitive Path

The executor path above calls these primitive commands for you. Use this manual
path when you are learning the internals, debugging one step, or intentionally
building a custom workflow.

Probe the environment and run the unchanged baseline before asking for edits:

```bash
uv run simple-ar code-task map runs/<run-id>
uv run simple-ar code-task locate runs/<run-id>
uv run simple-ar code-task context runs/<run-id>
uv run simple-ar code-task probe runs/<run-id>
uv run simple-ar code-task baseline runs/<run-id> --timeout 60
uv run simple-ar code-task work-plan runs/<run-id>
uv run simple-ar code-task batch runs/<run-id> --work-item W1
```

`probe` writes `code_task/meta/environment_report.json` with OS, Python, tool, GPU, dependency-file, and test-directory signals. It does not install dependencies or run project code.

`baseline` runs the recorded benchmark command inside `code_task/workspace/`
before any patch is applied. It stores `execution_report.json`, `stdout.txt`,
`stderr.txt`, and parsed `metrics.json` under `code_task/run/baseline/`, and
updates `code_task/summary.md`.

Generate a higher-level work plan when the task is broad or may need multiple
edit batches:

```bash
uv run simple-ar code-task work-plan runs/<run-id>
uv run simple-ar code-task batch runs/<run-id> --work-item W1
```

`work-plan` writes `code_task/work_plan.json` and `code_task/work_plan.md`.
It records work items, target files, read-only evidence, validation hints,
context requests, and budget profiles. It does not generate code or edit
files. `batch` creates durable attempt state under
`code_task/attempts/attempt-NNN/batches/batch-NNN/`, which is the V2.2
foundation for later multi-round, per-batch editing and recovery. When a batch
is active, edit proposals are constrained to that batch's target files and
write extra batch-local review artifacts.

Work-plan items are intended to be executable implementation batches, not
standalone analysis notes. The LLM prompt asks the planner to put inspection
needs in `context_request`. If a model still returns an analysis-only first
item, `code-task execute` prefers the first later item that looks like a real
code change, so a broad "inspect the project" step does not accidentally become
the active edit batch.

If a model splits one tightly coupled implementation into a serial chain, for
example feature extraction -> scorer wiring -> config enablement, the batch
creator can merge that small dependent chain into one execution batch. The
reviewed `work_plan.md` still shows the separate items, but
`batch_state.json.work_item.source_work_item_ids` records the merged item ids
and `target_files` becomes the union of the coupled files. Because these merged
batches may touch more than two files, they usually use the `large` budget
profile and require explicit review before `--allow-large-edits` is used.

Generate a patch plan (LLM optional; offline mode writes a conservative plan):

```bash
uv run simple-ar code-task plan runs/<run-id>
```

If `probe`, `validate`, or `baseline` artifacts already exist, the generated
plan includes that run context so the model and reviewer can reason from
recorded environment and benchmark evidence instead of starting cold.

`plan` writes `code_task/patch_plan.md`, updates `manifest.json`, and records
selected context files. It does not change source files. In LLM mode it records
token usage under `code_task/meta/llm_usage.jsonl`; with `--no-llm` it writes a
conservative offline plan.

Review the plan, then approve it:

```bash
uv run simple-ar code-task decide-plan runs/<run-id> \
  --decision approve \
  --note "small scoped edit"
```

`decide-plan` appends a human decision to
`code_task/meta/hitl_decisions.jsonl` and updates the plan status in
`manifest.json`. Approval is the normal gate before model-generated edits can
be applied.

Ask the model for controlled edit proposals (offline mode writes an empty proposal):

```bash
uv run simple-ar code-task propose-edits runs/<run-id>
```

`propose-edits` writes `code_task/meta/proposed_edits.json`. The proposal uses
controlled old/new text replacements and is meant for review. It does not edit
the workspace by itself. A proposal may include multiple ordered edits for the
same file; each `old` block must still match uniquely when applied in sequence.
The proposal also records `editor.backend = "controlled_patch"` so future
backends can be audited through the same artifact shape.
The reserved `external_agent` backend is intentionally non-executable in this
version. It can build a reviewable invocation plan for future
Codex/Claude/OpenCode adapters, including provider, command preview, blocked
read patterns, timeout, network/shell permissions, log path, and diff path. Any
future external-agent result must still become a captured diff/proposal before
SimpleAutoResearch applies validation, benchmark execution, and summary logic.
By default, tests and benchmark files are treated as read-only evidence:
`propose-edits` omits them from editable snippets, and any model edit targeting
paths such as `tests/**`, `test_*.py`, `benchmark.py`, or `*benchmark*.py` is
dropped from the proposal. V2.2 also applies an edit budget after the model
returns JSON. Oversized proposals are written with warnings and rejected edits
instead of being applied; if the proposal fits the larger review budget, rerun
with `--allow-large-edits` only after reading the JSON.

Apply proposed edits inside the editable workspace:

```bash
uv run simple-ar code-task apply-edits runs/<run-id>
```

`apply-edits` applies the reviewed proposal only inside
`code_task/workspace/`, writes a human-readable `code_task/patch.diff`, writes
`code_task/meta/applied_edits.json` with changed files and hashes, and updates
the codebase index. It still never mutates the original `--code-root`. If an
edit cannot be matched safely, `execute` stops with `patch_apply_failed` before
workspace files are changed.
`applied_edits.json` records the proposal path and editor backend used for the
application, including manually supplied or repair proposal files.
`apply-edits` also re-checks the edit scope, so manually supplied JSON cannot
modify protected tests or benchmark files even if it bypassed the LLM proposal
step.

Validate and run the patched benchmark:

```bash
uv run simple-ar code-task validate runs/<run-id>
uv run simple-ar code-task run runs/<run-id> --timeout 60
```

`validate` writes `code_task/meta/validation_report.json` with syntax errors,
risky imports/calls, missing import warnings, and file-size warnings. It is a
static check; it does not run the benchmark.

`run` stores the patched benchmark under `code_task/run/patched/`.
When both baseline and patched artifacts exist, SimpleAutoResearch also writes
`code_task/run/comparison.json` and includes outcome, next-step guidance, and
metric deltas in `code_task/summary.md`.

Patched benchmark success is separated from task-objective success. A run may
pass the benchmark floor but still regress against baseline metrics. In that
case `manifest.json` records `objective.status = "regressed"`, `simple-ar
status` prints the objective verdict, and `summary.md` points you back to
`code_task/run/comparison.json` instead of treating the task as complete.

Analyze failures and request a bounded repair proposal:

```bash
uv run simple-ar code-task analyze-failure runs/<run-id>
uv run simple-ar code-task repair runs/<run-id>
```

`analyze-failure` reads the latest failed validation/benchmark evidence and
writes a compact diagnosis, usually under `code_task/run/patched/` or the
current run label. If the benchmark was blocked before launch by static
validation, it writes `code_task/meta/failure_analysis.md` instead. It is
deterministic and does not call the LLM.

`repair` uses the failure analysis, latest patch, task, and selected source
context to write a bounded repair proposal under
`code_task/repairs/repair-001/proposed_edits.json`. The proposal records the
source analysis path, selected context files, and repair constraints. It does
not apply the repair automatically. Repair proposal context follows the same
edit-scope rule: tests and benchmark files may inform diagnosis, but they are
not supplied as editable snippets by default. `code_task/summary.md` is
refreshed with a Repair section.

Apply a reviewed repair proposal explicitly:

```bash
uv run simple-ar code-task apply-edits runs/<run-id> \
  --edits-file runs/<run-id>/code_task/repairs/repair-001/proposed_edits.json
```

When a repair proposal is applied, `manifest.json.patch.latest_applied_proposal`
and `code_task/meta/applied_edits.json` record the repair proposal path. After a
later patched benchmark passes, stale failure-analysis and repair sections are
marked resolved so `status` and `summary.md` reflect the current state rather
than an older failed attempt.

### Troubleshooting Code Task Runs

`proposed_edits.json` was not created after `execute`:

- This is normal after the first executor call. A fresh run stops at
  `approval_required` after writing `code_task/patch_plan.md`.
- Review `code_task/patch_plan.md`, then run:

```powershell
uv run simple-ar code-task decide-plan $RUN --decision approve --note "reviewed"
uv run simple-ar code-task execute $RUN `
  --config examples/code_tasks/configs/tiny_digits_mlp.toml `
  --to-step propose-edits
```

- Check `manifest.json`: `plan.status` should be `approved`. The decision log
  is `code_task/meta/hitl_decisions.jsonl`.

Validation passed but patched benchmark failed:

- This means the patch was syntactically acceptable but behavior or metrics got
  worse. Inspect:

```bash
code_task/run/patched/execution_report.json
code_task/run/patched/stdout.txt
code_task/run/patched/stderr.txt
code_task/run/comparison.json
code_task/summary.md
```

- Ask for a bounded repair proposal:

```powershell
uv run simple-ar code-task execute $RUN `
  --config examples/code_tasks/configs/tiny_digits_mlp.toml `
  --to-step repair `
  --repair-rounds 1 `
  --timeout 60
```

- Review the newest `code_task/repairs/repair-NNN/proposed_edits.json`, then
  apply it explicitly:

```powershell
uv run simple-ar code-task apply-edits $RUN `
  --edits-file "$RUN\code_task\repairs\repair-NNN\proposed_edits.json"
uv run simple-ar code-task validate $RUN
uv run simple-ar code-task run $RUN --timeout 60
```

- A repair can make the benchmark pass without truly improving over baseline.
  Use `code_task/run/comparison.json`, `manifest.json.objective.status`, and
  `simple-ar status` to decide whether the task goal was met.

Patched benchmark passed but the objective is `regressed` or `mixed`:

- This is not a runtime failure; it means the patch did not satisfy the metric
  goal compared with the recorded baseline.
- Inspect `code_task/run/comparison.json` first. It lists metric deltas,
  directions, the primary metric when configured, and the conservative verdict.
- Treat this like a quality failure: revise the task/plan, regenerate a tighter
  proposal, or request a repair only if the comparison gives enough evidence for
  a bounded follow-up patch.

`apply-edits` reports `old text was not found`:

- No workspace files are changed when this happens. It means a proposal's
  `old` text does not exactly match the current workspace, or the model put
  unified-diff markers inside the structured JSON.
- Regenerate the proposal or edit the JSON manually. Each edit must use exact
  current file text in `old` and replacement file text in `new`; do not include
  `+`, `-`, `@@`, `---`, or `+++` diff markers inside either field.
- If several edits target the same nearby block, combine them into one larger
  exact old/new replacement so later edits do not invalidate earlier ones.

Large-edit approval is required:

- Read `code_task/meta/proposed_edits.json` and any
  `proposal_warnings.json` under `code_task/meta/` or the latest
  `code_task/attempts/.../batch-NNN/` directory.
- If the larger change is intentional, rerun the apply/executor command with
  `--allow-large-edits`. Do not use this flag just to silence an unclear model
  proposal.

Proposal covers only the first part of a coupled plan:

- Check `code_task/work_plan.md` and the latest
  `code_task/attempts/.../batch_state.json`. If a plan has serial items such as
  feature -> model -> config, the active batch should list all coupled ids in
  `work_item.source_work_item_ids` and all editable files in `work_item.target_files`.
- For older runs created before this behavior, create a fresh batch with
  `uv run simple-ar code-task batch $RUN --work-item W1 --force`, then regenerate
  the proposal with `uv run simple-ar code-task propose-edits $RUN --force`.
- If the merged batch is marked `large`, review the full proposal before using
  `--allow-large-edits`.

`uv run` fails with a local cache permission error:

- This is an environment issue outside the run artifacts. Fix the uv cache
  permissions or run the project virtualenv entrypoint directly, for example
  `.\.venv\Scripts\simple-ar.exe ...` on PowerShell.

## Embedded Code Task In The 8-Stage Pipeline

Use this when you want the normal research pipeline to hand off to a configured
existing-code task during `06-code` and include the result in `08-report`.

Config-driven user project:

```bash
uv run simple-ar run --config examples/run_configs/tiny_digits_mlp_pipeline.toml
```

The example config is intentionally complete: it includes the outer pipeline
settings and the embedded code-task settings in one file. See
[CLI Reference](CLI_REFERENCE.md#run-config) before adapting it to your own
project.

The equivalent split config form points the pipeline at a standalone code-task
config:

```bash
uv run simple-ar run \
  --topic "improve tiny digits MLP" \
  --to-stage report \
  --experiment-template code_task_project \
  --code-task-config examples/code_tasks/configs/tiny_digits_mlp.toml \
  --offline-search \
  --experiment-timeout 60
```

And the fully explicit flag form is:

```bash
uv run simple-ar run \
  --topic "improve tiny digits MLP" \
  --to-stage report \
  --experiment-template code_task_project \
  --code-root examples/code_tasks/tiny_digits_mlp_project \
  --task-file examples/code_tasks/tasks/improve_tiny_digits_mlp.md \
  --benchmark-command "python benchmark.py" \
  --primary-metric accuracy \
  --metric-direction accuracy=higher \
  --metric-direction macro_f1=higher \
  --offline-search \
  --experiment-timeout 60
```

For a more research-first run, omit `--task-file` while still providing the
code root and benchmark command:

```bash
uv run simple-ar run \
  --topic "research and improve the tiny digits MLP baseline" \
  --to-stage report \
  --experiment-template code_task_project \
  --code-root examples/code_tasks/tiny_digits_mlp_project \
  --benchmark-command "python benchmark.py" \
  --primary-metric accuracy \
  --metric-direction accuracy=higher \
  --offline-search \
  --experiment-timeout 60
```

In that mode, `05-design` writes `generated_code_task.md` and
`generated_code_task_meta.json` from the prior research artifacts and a compact
codebase summary. `06-code` then uses the generated task as the normal
`code_task/task.md` input for planning and edit proposal.

`code_task_project` writes a normal pipeline run plus nested code-task artifacts
under `06-code/code_task_run/`. During `06-code`, it copies or worktrees the
user project, probes the environment, runs a baseline benchmark, builds a repo
map/context pack, generates a batch-oriented work plan, creates an
attempt/batch record, generates a patch plan, records an automatic pipeline
approval, asks for controlled edits, applies them inside the prepared
workspace, and validates the result. During `07-run`, the harness runs the
patched benchmark, writes `comparison.json` when baseline and patched metrics
are both available, and exposes code-task metrics through `07-run/results.json`.
During `08-report`, the report includes a deterministic Code Task Evidence
section pointing back to the nested work plan, batch state, summary, diff, and
comparison artifacts. The embedded path uses the same edit-scope guard as the
standalone workflow, so the patch cannot rewrite protected tests or benchmark
files just to improve reported metrics.

This path is convenient for end-to-end experiments, but it deliberately trades
away the standalone workflow's review pauses. For safety-sensitive or
hard-to-debug projects, use standalone `code-task execute` or the manual path
first, then move to `code_task_project` after the benchmark and task are stable.

Legacy bundled toy smoke test:

```bash
uv run simple-ar run \
  --topic "LLM-guided improvement of a toy spam baseline" \
  --to-stage report \
  --experiment-template llm_code_task_toy_spam \
  --offline-search \
  --experiment-timeout 60
```

## Command Design

The CLI keeps primitive commands because this project is still a learning
implementation. Each step is inspectable, testable, and reviewable. Config files
are used to shorten setup-heavy commands, not to hide approval gates, artifact
paths, validation results, baseline runs, or benchmark evidence.
