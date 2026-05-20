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
SIMPLE_AR_INPUT_PRICE_PER_1M=
SIMPLE_AR_OUTPUT_PRICE_PER_1M=
```

Notes:

- `OPENAI_API_KEY` is required for LLM mode.
- `OPENAI_BASE_URL` can point to OpenAI or a third-party OpenAI-compatible `/v1` endpoint.
- `SIMPLE_AR_MODEL` is the default model when `--model` is not supplied.
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
- Embedded code-task experiment: `06-code` can call the LLM for a patch plan and controlled edit proposal, but the patch is applied only inside an isolated workspace under the run directory.
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

Initialize from explicit CLI flags:

```bash
uv run simple-ar code-task init \
  --code-root examples/code_tasks/toy_spam_project \
  --task-file examples/code_tasks/tasks/improve_toy_spam_baseline.md \
  --benchmark-command "python -m unittest discover -s tests" \
  --env-mode current
```

Or initialize from a TOML config when there are several benchmark metrics or
environment settings:

```bash
uv run simple-ar code-task init --config examples/code_tasks/configs/tiny_digits_mlp.toml
```

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
names work when you declare their direction with `--metric-direction` or the
TOML config. See [CLI Reference](CLI_REFERENCE.md#init) for the full option
table and [CLI Reference](CLI_REFERENCE.md#init-config) for the config schema.

After init, choose one of two execution styles:

- **Manual path**: run every primitive command yourself. This is best while
  debugging or learning the internals.
- **Executor path**: use `code-task execute` to continue to the next safe step.
  This is shorter, but it still stops at review gates.

Refresh the code map at any time:

```bash
uv run simple-ar code-task map runs/<run-id>
```

`map` scans the current `code_task/workspace/`, refreshes
`code_task/meta/codebase_index.json`, writes `code_task/meta/repo_map.json` and
`code_task/meta/repo_map_summary.md`, and updates `manifest.json`. It does not
call the LLM, install dependencies, run benchmark code, or modify the original
source project.

### Manual Path

Probe the environment and run the unchanged baseline before asking for edits:

```bash
uv run simple-ar code-task map runs/<run-id>
uv run simple-ar code-task probe runs/<run-id>
uv run simple-ar code-task baseline runs/<run-id> --timeout 60
```

`probe` writes `code_task/meta/environment_report.json` with OS, Python, tool, GPU, dependency-file, and test-directory signals. It does not install dependencies or run project code.

`baseline` runs the recorded benchmark command inside `code_task/workspace/`
before any patch is applied. It stores `execution_report.json`, `stdout.txt`,
`stderr.txt`, and parsed `metrics.json` under `code_task/run/baseline/`, and
updates `code_task/summary.md`.

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
By default, tests and benchmark files are treated as read-only evidence:
`propose-edits` omits them from editable snippets, and any model edit targeting
paths such as `tests/**`, `test_*.py`, `benchmark.py`, or `*benchmark*.py` is
dropped from the proposal.

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

### Executor Path

The executor path is the shortest reviewed route through the same workflow:

```bash
# Continue to plan review.
uv run simple-ar code-task execute runs/<run-id>

# Approve the plan after reading code_task/patch_plan.md.
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve

# Continue to edit proposal review.
uv run simple-ar code-task execute runs/<run-id>

# Apply the reviewed proposal and run validation/benchmark.
uv run simple-ar code-task execute runs/<run-id> --apply-proposed-edits --timeout 60
```

Those repeated `execute` calls are intentional. `execute` means "inspect the
current run and continue to the next safe stop." It does not mean "skip review."

- First `execute`: writes `environment_report.json`, baseline artifacts,
  `patch_plan.md`, then stops with `approval_required`.
- `decide-plan`: records your approval in `hitl_decisions.jsonl`.
- Second `execute`: writes `proposed_edits.json`, then stops with
  `proposal_review_required`.
- Final `execute --apply-proposed-edits`: applies the reviewed proposal, writes
  `patch.diff`, validates the workspace, runs the patched benchmark, updates
  `comparison.json`, and refreshes `summary.md`.

Preview the next executor action without writing artifacts:

```bash
uv run simple-ar code-task execute runs/<run-id> --dry-run
```

Detailed code-task command options live in [CLI Reference](CLI_REFERENCE.md#code-task-commands).

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
under `06-code/code_task_run/`. During `06-code`, it copies the user project,
probes the environment, runs a baseline benchmark, generates a patch plan,
records an automatic pipeline approval, asks for controlled edits, applies
them inside the prepared workspace, and validates the result. During `07-run`,
the harness runs the patched benchmark, writes `comparison.json` when baseline
and patched metrics are both available, and exposes code-task metrics through
`07-run/results.json`. During `08-report`, the report includes a deterministic
Code Task Evidence section pointing back to the nested summary, diff, and
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
