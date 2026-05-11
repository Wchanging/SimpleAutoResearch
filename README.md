# SimpleAutoResearch

SimpleAutoResearch is a teaching-first, lightweight auto-research project inspired by [AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw). It explores how an automated research assistant can move from a topic to literature notes, small experiments, executable results, code-task workflows, and Markdown reports while keeping the process visible and hackable.

The goal is not to reproduce every feature of a large agent framework. The goal is to build a clear, inspectable version that is useful for learning, experimentation, and gradual extension.

## Goals

- Keep research steps explicit and file-based.
- Make runs easy to inspect, resume, and debug.
- Support both literature/report workflows and existing-code improvement workflows.
- Prefer controlled, reproducible experiments over unconstrained code generation.
- Keep the codebase small enough for learners and contributors to understand.

## What It Can Do

Current capabilities include:

- an 8-stage topic-to-report research pipeline;
- OpenAI-compatible LLM calls with visible progress and token usage logging;
- OpenAlex/arXiv-backed literature metadata with local cache support;
- local artifact inspection and lexical retrieval;
- citation-bounded Markdown report generation;
- a standalone `code-task` workflow for copying, analyzing, patching, validating, and running an existing codebase.

SimpleAutoResearch is not a fully autonomous paper factory. The coding and experiment paths are intentionally conservative while the validation and execution layers mature.

## Quickstart

Clone the repository:

```bash
git clone https://github.com/Wchanging/SimpleAutoResearch.git
cd SimpleAutoResearch
```

Install dependencies with `uv`:

```bash
uv sync
```

Create your local environment file:

```bash
cp .env.example .env
```

On PowerShell:

```powershell
Copy-Item .env.example .env
```

Edit `.env`:

```bash
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.openai.com/v1
SIMPLE_AR_MODEL=gpt-4o-mini
SIMPLE_AR_INPUT_PRICE_PER_1M=
SIMPLE_AR_OUTPUT_PRICE_PER_1M=
```

For third-party OpenAI-compatible providers, set `OPENAI_BASE_URL` to that provider's `/v1` endpoint. Price fields are optional; when unset, SimpleAutoResearch records token counts but leaves estimated cost as `null`.

Run a basic research pipeline:

```bash
uv run simple-ar run --topic "toy topic" --to-stage report
```

Show run status:

```bash
uv run simple-ar status runs/<run-id>
```

Run tests:

```bash
uv run python -m unittest discover -s tests
```

## Preset Workflows

SimpleAutoResearch is moving toward three preset workflows. The current implementation supports some parts fully and some parts as planned design.

### 1. Research Report

Use this for a literature review or DeepResearch-like report.

```bash
uv run simple-ar run \
  --topic "agent simulation" \
  --to-stage report \
  --max-papers 5
```

Current status: supported through the default research pipeline, although the default 8-stage flow still includes experiment design/code/run stages. A true no-code `survey` preset is planned.

### 2. Code Task

Use this when you already have a codebase or benchmark and want a focused modification, such as improving a baseline algorithm, reducing runtime, or fixing failing tests.

```bash
uv run simple-ar code-task init \
  --code-root examples/code_tasks/toy_spam_project \
  --task-file examples/code_tasks/tasks/improve_toy_spam_baseline.md \
  --benchmark-command "python -m unittest discover -s tests"
```

Then plan, approve, patch, validate, and run:

```bash
uv run simple-ar code-task plan runs/<run-id>
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve
uv run simple-ar code-task propose-edits runs/<run-id>
uv run simple-ar code-task apply-edits runs/<run-id>
uv run simple-ar code-task validate runs/<run-id>
uv run simple-ar code-task run runs/<run-id>
```

Current status: supported as a standalone workflow. Edits are applied only inside a copied workspace, not the original project.

### 3. Research With Experiment

Use this when the goal is to connect literature analysis with an executable experiment and a result-backed report.

```bash
uv run simple-ar run \
  --topic "keyword rules versus bag-of-words logistic regression for toy spam classification" \
  --to-stage report
```

Current status: supported in a narrow teaching form through a whitelisted experiment template. A future version should allow the experiment design stage to hand off to `code-task` or approved generated code.

## Documentation

- [Usage And Configuration](docs/USAGE.md): installation, environment variables, commands, examples, and configuration direction.
- [Workflows And Artifacts](docs/WORKFLOWS.md): workflow presets, the 8-stage pipeline, stage outputs, and artifact layout.
- [Development Guide](docs/DEVELOPMENT.md): how to extend stages, templates, code-task modules, and tests.
- [Changelog](CHANGELOG.md): chronological development progress.

## Reference

The main reference project is [aiming-lab/AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw). SimpleAutoResearch borrows the staged research idea, but keeps the implementation intentionally compact and learning-friendly.

## Community

This is an early learning-oriented project. Issues, suggestions, experiments, and small focused pull requests are welcome, especially around coding-agent workflows, reproducible experiment execution, report quality, and documentation clarity.
