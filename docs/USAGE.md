# Usage And Configuration

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
- Deterministic: `design`, `code`, and `run` use fixed experiment templates or the embedded code-task demo. They do not generate free-form code.
- Embedded code-task demo: `06-code` can call the LLM for a patch plan and controlled edit proposal, but the patch is applied only inside the copied demo workspace.
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

The code-task workflow copies a source project into an isolated workspace and never mutates the original codebase. It is intentionally step-by-step so each stage can be reviewed.

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

`init` creates a new `runs/<run-id>/` directory, copies the source project into
`code_task/workspace/`, writes the task to `code_task/task.md`, builds
`code_task/meta/codebase_index.json`, and records the benchmark/environment
policy in `manifest.json`. It does not run code, call the LLM, or modify the
original source project.

Benchmarks should print numeric metric lines as `name: value`. Custom metric
names work when you declare their direction with `--metric-direction` or the
TOML config. See [CLI Reference](CLI_REFERENCE.md#init) for the full option
table and [CLI Reference](CLI_REFERENCE.md#init-config) for the config schema.

Probe the environment and run the unchanged baseline before asking for edits:

```bash
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
the workspace by itself.

Apply proposed edits inside the copied workspace:

```bash
uv run simple-ar code-task apply-edits runs/<run-id>
```

`apply-edits` applies the reviewed proposal only inside
`code_task/workspace/`, writes a human-readable `code_task/patch.diff`, writes
`code_task/meta/applied_edits.json` with changed files and hashes, and updates
the codebase index. It still never mutates the original `--code-root`.

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
current run label. It is deterministic and does not call the LLM.

`repair` uses the failure analysis, latest patch, task, and selected source
context to write a bounded repair proposal under
`code_task/repairs/repair-001/proposed_edits.json`. It does not apply the
repair automatically.

Apply a reviewed repair proposal explicitly:

```bash
uv run simple-ar code-task apply-edits runs/<run-id> \
  --edits-file runs/<run-id>/code_task/repairs/repair-001/proposed_edits.json
```

Detailed code-task command options live in [CLI Reference](CLI_REFERENCE.md#code-task-commands).

## Command Design

The CLI keeps primitive commands because this project is still a learning
implementation. Each step is inspectable, testable, and reviewable. Config files
are used to shorten setup-heavy commands, not to hide approval gates, artifact
paths, validation results, baseline runs, or benchmark evidence.
