# CLI Reference

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
| `simple-ar code-task ...` | Work with an existing codebase in a copied workspace. |

## Research Pipeline

Start a run:

```bash
uv run simple-ar run --topic "agent simulation" --to-stage report
```

Common options:

| Option | Meaning |
| --- | --- |
| `--topic TEXT` | Required research topic for a new run. |
| `--output-root DIR` | Where run directories are created. Default: `runs`. |
| `--from-stage NAME` | First stage to execute. Default: `plan`. |
| `--to-stage NAME` | Last stage to execute. Default: `report`. |
| `--model NAME` | Override the LLM model. |
| `--llm-workers N` | Parallel LLM workers for supported stages. |
| `--max-papers N` | Literature search result limit. |
| `--search-query TEXT` | Override the generated search query. |
| `--experiment-template NAME` | Experiment template name. |
| `--experiment-timeout N` | Subprocess timeout for experiment execution. |
| `--report-mode auto|research_only|experiment` | Report structure mode. |
| `--no-llm` | Use deterministic fallback text instead of LLM calls. |
| `--offline-search` | Skip live literature providers. |
| `--allow-fixture-fallback` | Allow placeholder metadata after live/cache failures. |
| `--strict-search` | Fail instead of using cache/fixture fallback. |
| `--no-retrieval` | Disable local artifact retrieval context. |
| `--retrieval-top-k N` | Number of local artifact chunks to retrieve. |
| `--quiet` | Suppress progress logs. |

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

The code-task workflow copies an existing project into `code_task/workspace`.
It does not mutate the original codebase.

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
| `--benchmark-command TEXT` | Command to run inside copied workspace. |
| `--max-file-bytes N` | Maximum copied file size. Use `0` to disable. |
| `--env-mode current|external` | Execution interpreter policy. |
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

[safety]
max_file_bytes = 2000000
```

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
| `--decision approve|reject|revise` | Required decision value. |
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
