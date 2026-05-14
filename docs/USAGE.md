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

Build a local artifact index:

```bash
uv run simple-ar inspect runs/<run-id>
```

Search run artifacts with lexical retrieval:

```bash
uv run simple-ar search-artifacts runs/<run-id> "accuracy"
uv run simple-ar search-artifacts runs/<run-id> "timeout" --include-operational
```

Retrieval controls during normal runs:

```bash
uv run simple-ar run --topic "toy topic" --to-stage report --retrieval-top-k 4
uv run simple-ar run --topic "toy topic" --to-stage report --no-retrieval
```

## Code Task Workflow

The code-task workflow copies a source project into an isolated workspace and never mutates the original codebase. It is intentionally step-by-step so each stage can be reviewed.

Initialize a code task from an existing codebase:

```bash
uv run simple-ar code-task init \
  --code-root examples/code_tasks/toy_spam_project \
  --task-file examples/code_tasks/tasks/improve_toy_spam_baseline.md \
  --benchmark-command "python -m unittest discover -s tests"
```

Generate a patch plan (LLM optional; offline mode writes a conservative plan):

```bash
uv run simple-ar code-task plan runs/<run-id>
```

Review the plan, then approve it:

```bash
uv run simple-ar code-task decide-plan runs/<run-id> \
  --decision approve \
  --note "small scoped edit"
```

Ask the model for controlled edit proposals (offline mode writes an empty proposal):

```bash
uv run simple-ar code-task propose-edits runs/<run-id>
```

Apply proposed edits inside the copied workspace:

```bash
uv run simple-ar code-task apply-edits runs/<run-id>
```

Validate and run the benchmark:

```bash
uv run simple-ar code-task validate runs/<run-id>
uv run simple-ar code-task run runs/<run-id> --timeout 60
```

Analyze failures and request a bounded repair proposal:

```bash
uv run simple-ar code-task analyze-failure runs/<run-id>
uv run simple-ar code-task repair runs/<run-id>
```

Repair proposals are not applied automatically. Apply a reviewed repair proposal explicitly:

```bash
uv run simple-ar code-task apply-edits runs/<run-id> \
  --edits-file runs/<run-id>/code_task/repairs/repair-001/proposed_edits.json
```

## Command Design

The current CLI exposes primitive steps because this project is still a learning implementation. That makes each step inspectable and testable, but it can become verbose.

Current boundary: the bundled `llm_code_task_toy_spam` template is the only code-task path that is fully embedded in the 8-stage pipeline. For arbitrary user projects, use the standalone `code-task` commands so the plan, approval, edit, validation, and benchmark steps remain reviewable.

The planned direction is to keep primitive commands while adding config-driven convenience presets later:

```bash
uv run simple-ar survey --config simple_ar.toml
uv run simple-ar code-task execute --config simple_ar_code_task.toml
uv run simple-ar experiment --config simple_ar_experiment.toml
```

## Future Config Shape

A future code-task config may look like this:

```toml
[code_task]
code_root = "examples/code_tasks/toy_spam_project"
task_file = "examples/code_tasks/tasks/improve_toy_spam_baseline.md"
output_root = "runs"
name = "toy-spam"

[benchmark]
command = "python -m unittest discover -s tests"
timeout_sec = 60

[llm]
model = "gpt-4o-mini"
use_llm = true

[safety]
require_plan_approval = true
strict_validation = false
max_file_bytes = 2000000
repair_rounds = 1
```

The config layer should shorten common workflows without hiding approval gates, artifact paths, validation results, or benchmark evidence.
