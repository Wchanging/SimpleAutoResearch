# CLI Reference

[中文版本](CLI_REFERENCE_zh.md)

This document is the command lookup for SimpleAutoResearch. For installation
and workflow walkthroughs, see [Usage And Configuration](USAGE.md). For stage
concepts and artifacts, see [Workflows And Artifacts](WORKFLOWS.md).

## Top-Level Commands

| Command | Purpose |
| --- | --- |
| `simple-ar run` | Start a new 8-stage research pipeline run. |
| `simple-ar resume` | Continue an existing research pipeline run. |
| `simple-ar status` | Print status for either a research run or code-task run. |
| `simple-ar inspect` | Build a local artifact index for a run. |
| `simple-ar search-artifacts` | Search indexed run artifacts with lexical retrieval. |
| `simple-ar code-task ...` | Work with an existing codebase in an isolated editable workspace. |

## Research Pipeline

Start a run:

```bash
uv run simple-ar run --topic "agent simulation" --to-stage report
```

Common options:

| Option | Meaning |
| --- | --- |
| `--config PATH` | TOML config for a repeatable run. Explicit CLI flags override config values. |
| `--topic TEXT` | Research topic. Required unless `[run].topic` is set in `--config`. |
| `--output-root DIR` | Where run directories are created. Default: `runs`. |
| `--from-stage NAME` | First stage to execute. Default: `plan`. |
| `--to-stage NAME` | Last stage to execute. Default: `report`. |
| `--model NAME` | Override the LLM model. |
| `--llm-workers N` | Parallel LLM workers for supported stages. |
| `--max-papers N` | Literature search result limit. |
| `--search-query TEXT` | Override the generated search query. |
| `--experiment-template NAME` | Experiment template name. |
| `--experiment-timeout N` | Subprocess timeout for experiment execution. |
| `--report-mode auto / research_only / experiment` | Report structure mode. |
| `--no-llm` | Use deterministic fallback text instead of LLM calls. |
| `--offline-search` | Skip live literature providers. |
| `--allow-fixture-fallback` | Allow placeholder metadata after live/cache failures. |
| `--strict-search` | Fail instead of using cache/fixture fallback. |
| `--no-retrieval` | Disable local artifact retrieval context. |
| `--retrieval-top-k N` | Number of local artifact chunks to retrieve. |
| `--quiet` | Suppress progress logs. |

Experiment templates:

| Template | Meaning |
| --- | --- |
| `toy_text_classification` | Default deterministic teaching experiment. |
| `llm_code_task_toy_spam` | Bundled toy code-task smoke test. |
| `code_task_project` | Embedded code-task experiment for a user-provided project. |

### Run Config

For multi-option runs, prefer a TOML config over a long CLI command:

```bash
uv run simple-ar run --config examples/run_configs/tiny_digits_mlp_pipeline.toml
```

The complete config below is equivalent to a full `code_task_project` pipeline
run. It includes both outer research-pipeline settings and embedded code-task
settings in one file:

```toml
[run]
# Required unless --topic is supplied.
topic = "improve tiny digits MLP"

# Where timestamped run directories are created.
output_root = "runs"

# Optional. Default from_stage is "plan"; default to_stage is "report".
from_stage = "plan"
to_stage = "report"

[llm]
# true: use configured OpenAI-compatible LLM calls.
# false: use deterministic fallbacks where possible. code_task_project requires
# LLM mode for real patch planning/edit proposal.
enabled = true

# Optional model override. When omitted, SIMPLE_AR_MODEL / provider default is used.
model = "gpt-4o-mini"

# Parallel LLM workers for supported stages such as paper note generation.
workers = 4

[search]
# true: skip live OpenAlex/arXiv and use fixture metadata.
# Useful for local coding smoke tests where literature quality is not the focus.
offline = true

# Maximum paper metadata rows requested from live providers or fixture fallback.
max_papers = 1

# Optional manual search query. When omitted, the topic is used.
query = "tiny digits MLP"

# Optional. When true, allow fixture rows after live/cache search failures.
allow_fixture_fallback = false

# Optional. When true, fail instead of using cache/fixture fallback.
strict = false

[retrieval]
# Whether report/read/synthesize stages may retrieve snippets from local artifacts.
enabled = true
top_k = 4

[experiment]
# "toy_text_classification": deterministic teaching experiment.
# "code_task_project": embedded existing-code workflow.
# "llm_code_task_toy_spam": legacy bundled smoke test.
template = "code_task_project"

# Timeout for 07-run experiment.py. For code_task_project this also constrains
# nested baseline/patched benchmark calls.
timeout = 60

# Optional. Instead of putting [code_task]/[benchmark]/[environment]/[safety] in
# this same file, point to a standalone code-task config.
# code_task_config = "examples/code_tasks/configs/tiny_digits_mlp.toml"

[report]
# "auto": experiment report if results.json exists, research_only otherwise.
# "research_only": survey-style report without experiment claims.
# "experiment": require results.json and use experiment sections.
mode = "auto"

[code_task]
# Source project prepared under 06-code/code_task_run/code_task/workspace.
code_root = "examples/code_tasks/tiny_digits_mlp_project"

# Optional for embedded 8-stage runs. If omitted, 05-design generates
# generated_code_task.md from goal/problem/synthesis/hypothesis plus the
# codebase summary, then 06-code copies that into code_task/task.md.
# Standalone `simple-ar code-task init` still requires a task file.
task_file = "examples/code_tasks/tasks/improve_tiny_digits_mlp.md"

# Optional display name stored in experiment_plan.json and nested manifest.
name = "tiny-digits-mlp-pipeline"

[benchmark]
# Command executed inside the editable workspace before and after the patch.
command = "python benchmark.py"

# Optional primary metric for before/after verdicts.
primary_metric = "accuracy"

[benchmark.metric_directions]
# Directions may be higher, lower, resource, or ignore.
# Unknown metric names are still recorded as deltas, but they do not decide
# improved/regressed unless direction is configured or heuristically known.
accuracy = "higher"
macro_f1 = "higher"
train_time_sec = "resource"
inference_time_ms = "resource"
params = "resource"

[environment]
# current: use the active SimpleAutoResearch Python.
# external: use the interpreter given by python.
mode = "current"
# python = "C:/path/to/python.exe"

[workspace]
# copy: guarded physical copy, safest default.
# git_worktree: detached worktree for repo-root git projects, useful when the
# repository is too large to copy every run.
# sparse_copy: experimental allowlist copy for small known subsets.
mode = "copy"

# Used only by sparse_copy. Defaults are conservative source/config/test globs.
include = ["src/**", "tests/**", "benchmark.py", "pyproject.toml"]
exclude = ["data/**", "models/**"]

# If true and code_root has .venv/ or venv/, init records and uses that Python
# as an external execution policy. No dependency installation is performed.
reuse_source_venv = false

# Recorded for future managed setup support; not executed during init.
setup_hook = ""

[safety]
# Maximum source file size copied in copy/sparse modes. Use 0 to disable.
max_file_bytes = 2000000
```

Section summary:

| Section | Used by | Meaning |
| --- | --- | --- |
| `[run]` | outer pipeline | Topic, run directory, and stage range. |
| `[llm]` | outer pipeline and code task | LLM enablement, model override, and worker count. |
| `[search]` | `02-search` | Literature provider behavior and fallback policy. |
| `[retrieval]` | read/synthesize/report helpers | Local artifact retrieval context. |
| `[experiment]` | `05-design` to `07-run` | Experiment template, timeout, and optional nested code-task config path. |
| `[report]` | `08-report` | Report structure mode. |
| `[code_task]` | embedded or standalone code task | Source project, optional embedded task file, and display name. |
| `[benchmark]` | code task | Benchmark command and primary metric. |
| `[benchmark.metric_directions]` | code task comparison | Metric interpretation rules. |
| `[environment]` | code task execution | Interpreter policy for probe/baseline/patched runs. |
| `[workspace]` | code task init | Workspace mode, source venv reuse, and recorded setup hook. |
| `[safety]` | code task workspace/validation | Copy/sparse file-size guard and future safety settings. |

When a run config contains `[code_task]`, `[benchmark]`, `[metrics]`,
`[environment]`, `[workspace]`, or `[safety]`, the same file is reused as the embedded
code-task config. Alternatively, keep code-task settings in a separate file and
set `[experiment].code_task_config`.

Explicit CLI flags override config values. For example, this keeps the config
but changes the stage range and disables LLM calls:

```bash
uv run simple-ar run \
  --config examples/run_configs/tiny_digits_mlp_pipeline.toml \
  --to-stage design \
  --no-llm
```

`code_task_project` options for `run` and `resume`:

| Option | Meaning |
| --- | --- |
| `--code-task-config PATH` | TOML config using the same schema as `code-task init --config`. |
| `--code-root DIR` | Source project prepared under `06-code/code_task_run/code_task/workspace`. |
| `--task-file PATH` | Markdown/text task description. Optional for embedded 8-stage runs; if omitted, `05-design` writes `generated_code_task.md` from the research artifacts. |
| `--benchmark-command TEXT` | Benchmark run before and after the patch. |
| `--code-task-name TEXT` | Optional display name stored in `experiment_plan.json`. |
| `--code-task-max-file-bytes N` | Maximum source file size copied in `copy` or `sparse_copy` mode. |
| `--code-task-workspace-mode copy / git_worktree / sparse_copy` | Workspace strategy for the nested code task. Use TOML for sparse include/exclude patterns. |
| `--code-task-workspace-reuse-source-venv` | Use a detected source `.venv` Python for the nested execution policy. |
| `--code-task-workspace-setup-hook TEXT` | Record a setup command for future managed environment support. |
| `--code-task-env-mode current / external` | Interpreter policy for nested probe/baseline/run steps. |
| `--code-task-python PATH` | Interpreter path for `--code-task-env-mode external`. |
| `--primary-metric NAME` | Primary metric for before/after verdicts. |
| `--metric-direction NAME=DIRECTION` | Metric interpretation for embedded comparison. Repeatable. |

The generic embedded path auto-approves the generated patch plan inside the
pipeline workspace so `run --to-stage report` can finish. Use standalone
`code-task` commands when you need manual review before each transition.

Resume:

```bash
uv run simple-ar resume runs/<run-id>
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode research_only
```

Resume supports most `run` options as overrides. Omitted values are preserved
from `config_snapshot.json` when available.

## Artifact Tools

```bash
uv run simple-ar inspect runs/<run-id>
uv run simple-ar search-artifacts runs/<run-id> "accuracy"
uv run simple-ar search-artifacts runs/<run-id> "timeout" --include-operational
```

| Command | Purpose |
| --- | --- |
| `inspect RUN_DIR` | Build `artifact_index.json` and print a compact artifact summary. |
| `search-artifacts RUN_DIR QUERY` | Search local artifact chunks. |
| `--top-k N` | Number of search results. Default: `8`. |
| `--include-operational` | Also search manifests, runner metadata, and other operational files. |

## Code Task Commands

The code-task workflow prepares an existing project under `code_task/workspace`.
By default this is a guarded copy; `git_worktree` can create a detached git
worktree for larger repo-root projects; `sparse_copy` is an experimental
allowlist copy for small, well-understood subsets. Later steps mutate only that
workspace, not the original codebase.

When init cannot prepare the workspace, the CLI reports the failed path and a
short checklist. For `git_worktree`, the common fixes are to pass the baseline
git repository root, make an initial local commit, or choose `copy` mode.

Recommended order:

```text
init -> probe -> baseline -> plan -> decide-plan
-> propose-edits -> apply-edits -> validate -> run
-> analyze-failure -> repair
```

### Init

Minimal:

```bash
uv run simple-ar code-task init \
  --code-root path/to/project \
  --task-file task.md \
  --benchmark-command "python benchmark.py"
```

Config-driven:

```bash
uv run simple-ar code-task init --config code_task.toml
```

Options:

| Option | Meaning |
| --- | --- |
| `--config PATH` | TOML config for init settings. CLI flags override config values. |
| `--code-root DIR` | Source project to copy. Required unless set in config. |
| `--task-file PATH` | Markdown/text task description. Required unless set in config. |
| `--output-root DIR` | Where the run directory is created. Default: `runs`. |
| `--name TEXT` | Run name suffix. Default: based on `code-root`. |
| `--benchmark-command TEXT` | Command to run inside the editable workspace. |
| `--max-file-bytes N` | Maximum copied file size. Use `0` to disable. |
| `--workspace-mode copy / git_worktree / sparse_copy` | Workspace strategy. `copy` is safest; `git_worktree` requires `--code-root` to be the git repository root; `sparse_copy` copies selected patterns. |
| `--workspace-include GLOB` | Sparse-copy include pattern. Repeatable; TOML is clearer for multiple patterns. |
| `--workspace-exclude GLOB` | Additional sparse-copy exclude pattern. Repeatable. |
| `--workspace-reuse-source-venv` | If a source `.venv` or `venv` exists, record and use its Python as the initial external execution policy. |
| `--workspace-setup-hook TEXT` | Record a setup command. It is not executed during init. |
| `--env-mode current / external` | Execution interpreter policy. |
| `--python PATH` | Interpreter path for `--env-mode external`. |
| `--primary-metric NAME` | Main metric for before/after verdicts. |
| `--metric-direction NAME=DIRECTION` | Metric interpretation. Repeatable. |

Metric directions:

| Direction | Meaning |
| --- | --- |
| `higher` | Larger is better, such as accuracy/F1/reward. |
| `lower` | Smaller is better, such as loss/error/perplexity. |
| `resource` | Runtime/cost/resource metric; shown but not used for verdict. |
| `ignore` | Record but do not interpret. |

### Init Config

```toml
[code_task]
code_root = "path/to/project"
task_file = "task.md"
output_root = "runs"
name = "my-code-task"

[benchmark]
command = "python benchmark.py"
primary_metric = "accuracy"

[benchmark.metric_directions]
accuracy = "higher"
macro_f1 = "higher"
latency_ms = "resource"
val_loss = "lower"

[environment]
mode = "current"  # current | external
python = ""       # optional when mode = "external"

[workspace]
mode = "copy"                  # copy | git_worktree | sparse_copy
include = ["src/**", "tests/**", "benchmark.py", "pyproject.toml"]
exclude = ["data/**", "models/**"]
reuse_source_venv = false      # use source .venv Python if detected
setup_hook = ""                # recorded only; not executed during init

[safety]
max_file_bytes = 2000000
```

`sparse_copy` always applies built-in exclusions for `.git`, virtualenvs,
`runs`, cache/build directories, `data`, `models`, `.env`, and secret-like
paths before user patterns. It is useful for small allowlisted experiments, but
it can omit runtime dependencies; prefer `copy` or `git_worktree` for general
projects.

Code-task runs also record an `edit_scope` in `manifest.json`. The current
default treats tests, benchmark files, `.env`, and secret/credential-looking
paths as read-only evidence for patching:
`tests/**`, `test_*.py`, `*_test.py`, `conftest.py`, `benchmark.py`,
`bench.py`, `*benchmark*.py`, `.env*`, `*secret*`, and `*credential*`.
These files may be indexed for planning when appropriate, but they are omitted
from editable snippets and rejected by `apply-edits`.

Bundled example:

```bash
uv run simple-ar code-task init --config examples/code_tasks/configs/tiny_digits_mlp.toml
```

### Manual Command Path

Use the manual path when you want to run and inspect each primitive step
yourself:

```bash
uv run simple-ar code-task probe runs/<run-id>
uv run simple-ar code-task baseline runs/<run-id> --timeout 60
uv run simple-ar code-task plan runs/<run-id>
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve
uv run simple-ar code-task propose-edits runs/<run-id>
uv run simple-ar code-task apply-edits runs/<run-id>
uv run simple-ar code-task validate runs/<run-id>
uv run simple-ar code-task run runs/<run-id> --timeout 60
```

The following sections describe each primitive command.

#### Environment And Baseline

```bash
uv run simple-ar code-task probe runs/<run-id>
uv run simple-ar code-task baseline runs/<run-id> --timeout 60
```

| Command/Option | Meaning |
| --- | --- |
| `probe RUN_DIR` | Write `code_task/meta/environment_report.json`. |
| `baseline RUN_DIR` | Run benchmark before patching under `code_task/run/baseline/`. |
| `--command TEXT` | Override the recorded benchmark command for this run. |
| `--timeout N` | Benchmark timeout in seconds. |
| `--skip-validation` | Run benchmark even when static validation has not passed. |
| `--env-mode`, `--python` | Override execution interpreter policy. |

#### Planning And Approval

```bash
uv run simple-ar code-task plan runs/<run-id>
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve
```

| Command/Option | Meaning |
| --- | --- |
| `plan RUN_DIR` | Generate `code_task/patch_plan.md`. |
| `--model NAME` | Override model for planning. |
| `--no-llm` | Write deterministic fallback plan. |
| `--force` | Regenerate an existing plan. |
| `--max-files N` | Maximum selected context files. |
| `--max-source-chars-per-file N` | Source snippet budget per file. |
| `decide-plan RUN_DIR` | Record plan approval/rejection/revision. |
| `--decision approve / reject / revise` | Required decision value. |
| `--note TEXT` | Optional review note. |
| `--reviewer TEXT` | Reviewer label. Default: `user`. |

### Executor Path

Use the executor path when you want the CLI to continue to the next safe stop:

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

Repeated `execute` calls are expected. The command is state-aware: it reads the
run artifacts, performs the next safe work, and stops at review boundaries.

| Command/Option | Meaning |
| --- | --- |
| `execute RUN_DIR` | Run the next safe code-task steps based on current artifacts. |
| `--to-step STEP` | Stop no later than `probe`, `baseline`, `plan`, `propose-edits`, `apply-edits`, `validate`, `run`, `analyze-failure`, or `repair`. |
| `--dry-run` | Print the next action without writing artifacts. |
| `--no-llm`, `--model NAME` | Control LLM use for plan/proposal/repair steps. |
| `--apply-proposed-edits` | Allow execute to apply reviewed `proposed_edits.json` after plan approval. |
| `--repair-rounds N` | Maximum bounded repair proposals after validation/benchmark failure. Repair proposals are not auto-applied. |
| `--timeout N` | Benchmark timeout for baseline and patched runs. |
| `--strict-validation`, `--validation-max-file-bytes N` | Validation controls used by the orchestrated validate step. |
| `--env-mode`, `--python` | Override execution interpreter policy for probe and benchmark runs. |

Review gates are preserved. A fresh run stops after `patch_plan.md` with
`approval_required`. After approval, execute may generate
`proposed_edits.json`, but it stops again with `proposal_review_required`
unless `--apply-proposed-edits` is set.

Dry-run preview:

```bash
uv run simple-ar code-task execute runs/<run-id> --dry-run
```

#### Patch, Validate, Run

```bash
uv run simple-ar code-task propose-edits runs/<run-id>
uv run simple-ar code-task apply-edits runs/<run-id>
uv run simple-ar code-task validate runs/<run-id>
uv run simple-ar code-task run runs/<run-id> --timeout 60
```

| Command/Option | Meaning |
| --- | --- |
| `propose-edits RUN_DIR` | Ask the model for controlled old/new replacements. |
| `apply-edits RUN_DIR` | Apply approved edit proposals inside workspace. |
| `--edits-file PATH` | Apply a specific proposal file. |
| `--allow-unapproved-plan` | Bypass approval gate for local tests/demos. |
| `validate RUN_DIR` | Run syntax/static safety checks. |
| `--strict` | Treat higher-risk validation warnings as errors. |
| `run RUN_DIR` | Run patched benchmark under `code_task/run/patched/`. |

When both baseline and patched runs exist, SimpleAutoResearch writes
`code_task/run/comparison.json` and updates `code_task/summary.md`.

`proposed_edits.json` may contain multiple ordered edits for the same file.
Each edit is applied against the current in-memory text, and each `old` block
must match exactly once. Invalid proposals stop before file writes; under
`execute`, this appears as `patch_apply_failed`.

Edit-scope validation is checked twice: model proposals for protected paths are
dropped from `proposed_edits.json`, and `apply-edits` rejects protected paths
again for both model-generated and manually supplied edit files.

#### Failure And Repair

```bash
uv run simple-ar code-task analyze-failure runs/<run-id>
uv run simple-ar code-task repair runs/<run-id>
```

| Command/Option | Meaning |
| --- | --- |
| `analyze-failure RUN_DIR` | Summarize the latest failed benchmark or validation result. |
| `repair RUN_DIR` | Propose bounded repair edits from failure context. |
| `--model NAME` | Override repair model. |
| `--no-llm` | Write deterministic empty repair proposal. |
| `--max-files N` | Maximum context files. |
| `--max-source-chars-per-file N` | Source snippet budget per file. |

Repair proposals are not applied automatically. Review them, then apply with:

`analyze-failure` writes `failure_analysis.md` beside the failed benchmark run,
or under `code_task/meta/` when static validation failed before benchmark
launch. `repair` writes a proposal JSON with `source_analysis`,
`selected_files`, `constraints`, normalized `edits`, and `warnings`; edits
outside the selected repair context are dropped. It also refreshes
`code_task/summary.md` with a Repair section.

```bash
uv run simple-ar code-task apply-edits runs/<run-id> \
  --edits-file runs/<run-id>/code_task/repairs/repair-001/proposed_edits.json
```

## Status

```bash
uv run simple-ar status runs/<run-id>
```

For code-task runs, status prints environment, plan, patch, validation,
benchmark, primary metric, metric directions, comparison deltas,
failure-analysis, repair pointers, and the `code_task/summary.md` path when
available.
