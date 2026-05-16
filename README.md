# SimpleAutoResearch

SimpleAutoResearch is a teaching-first, lightweight auto-research project inspired by [AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw). It explores how an automated research assistant can move from a topic to literature notes, small experiments, executable results, code-task workflows, and Markdown reports while keeping the process visible and hackable.

The goal is not to reproduce every feature of a large agent framework. The goal is to build a clear, inspectable version that is useful for learning, experimentation, and gradual extension.

## Goals

- Keep research steps explicit and file-based.
- Make runs easy to inspect, resume, and debug.
- Support both literature/report workflows and existing-code improvement workflows.
- Prefer controlled, reproducible experiments over unconstrained code generation.
- Keep the codebase small enough for learners and contributors to understand.

## Install And Configure

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

Edit `.env` (required for LLM-backed stages):

```bash
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.openai.com/v1
SIMPLE_AR_MODEL=gpt-4o-mini
SIMPLE_AR_INPUT_PRICE_PER_1M=
SIMPLE_AR_OUTPUT_PRICE_PER_1M=
```

For third-party OpenAI-compatible providers, set `OPENAI_BASE_URL` to that provider's `/v1` endpoint. Price fields are optional; when unset, SimpleAutoResearch records token counts but leaves estimated cost as `null`.

## Quickstart (Pick A Workflow)

### 1. Research Report (Literature-First)

```bash
uv run simple-ar run --topic "agent simulation" --to-stage report --max-papers 5
```

The default 8-stage pipeline always includes design/code/run stages when you reach `report`. For a literature-only pass, stop earlier:

```bash
uv run simple-ar run --topic "agent simulation" --to-stage synthesize
```

Then generate a literature-only report from the existing artifacts:

```bash
uv run simple-ar resume runs/<run-id> --from-stage report
```

You can also force a report mode:

```bash
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode research_only
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode experiment
```

### 2. Code Task (Existing Codebase)

```bash
uv run simple-ar code-task init \
  --code-root examples/code_tasks/toy_spam_project \
  --task-file examples/code_tasks/tasks/improve_toy_spam_baseline.md \
  --benchmark-command "python -m unittest discover -s tests"
```

For a lightweight ML-style benchmark:

```bash
uv run simple-ar code-task init \
  --code-root examples/code_tasks/tiny_digits_mlp_project \
  --task-file examples/code_tasks/tasks/improve_tiny_digits_mlp.md \
  --benchmark-command "python benchmark.py" \
  --env-mode current
```

Then probe the environment, capture the unchanged baseline, and generate a patch plan:

```bash
uv run simple-ar code-task probe runs/<run-id>
uv run simple-ar code-task baseline runs/<run-id> --timeout 60
uv run simple-ar code-task plan runs/<run-id>
```

After human review, continue with `decide-plan`, `propose-edits`, `apply-edits`, `validate`, and `run`. When both baseline and patched benchmark artifacts exist, the run summary includes a conservative before/after comparison.

### 3. Research With Experiment

```bash
uv run simple-ar run \
  --topic "LLM-guided improvement of a toy spam baseline" \
  --to-stage report \
  --experiment-template llm_code_task_toy_spam \
  --offline-search \
  --experiment-timeout 60
```

This embedded demo copies a toy project into an isolated workspace, asks the LLM for a patch plan and controlled edits, applies the approved patch, runs a benchmark harness, and reports the result.

## Current Capability Boundaries

SimpleAutoResearch is usable as a learning and prototyping framework, but it is still intentionally conservative.

What works today:

- Topic-to-report runs with visible 8-stage artifacts and resumable execution.
- OpenAI-compatible LLM calls for planning, paper notes, synthesis, report drafting, and code-task patch planning.
- Literature-first report mode: stop at `synthesize`, then resume `report` to produce a survey-style report without experiment claims.
- Existing-code code tasks as a standalone workflow: copy a source project, probe the environment, index files, run a baseline benchmark, generate a context-aware patch plan, require human approval, propose controlled edits, apply edits in the copied workspace, validate, run a patched benchmark, and compare before/after metrics.
- One embedded 8-stage code-task demo through `--experiment-template llm_code_task_toy_spam`.
- Citation, report-boundary, runtime-limit, and metric-visibility checks in the final report package.

Important limits:

- The general "research a topic, modify my arbitrary existing codebase, run my benchmark, then write a report" workflow is not yet one command. Today, that is either the bundled toy demo or the standalone `code-task` workflow.
- Code edits are controlled old/new replacements. This keeps patches auditable, but it is weaker than a full coding agent that can plan and edit many files across multiple autonomous rounds.
- The tool does not install project dependencies, manage Docker/Conda/GPU/Slurm environments, or schedule large experiments.
- Literature search currently works from metadata and local artifact snippets. It is not yet a full PDF-reading or vector-RAG survey system.
- LLM-written reports are guarded. If the draft invents citations, omits required citations, or overstates toy evidence, SimpleAutoResearch falls back to a structured deterministic report.

V2.1 development has started with code-task environment probes, baseline runs, labelled benchmark artifacts, lightweight ML example coverage, and baseline-vs-patched comparison. The next focus is deeper coding loops: multi-round repair, stronger task decomposition, managed environments, and a clearer human-in-the-loop path from existing research code to reproducible results.

## Documentation

- [Usage And Configuration](docs/USAGE.md): installation, environment variables, commands, and examples.
- [Workflows And Artifacts](docs/WORKFLOWS.md): workflow presets, the 8-stage pipeline, and artifact layouts.
- [Development Guide](docs/DEVELOPMENT.md): how to extend stages, templates, and code-task modules.
- [Changelog](CHANGELOG.md): chronological development progress.

## Reference

The main reference project is [aiming-lab/AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw). SimpleAutoResearch borrows the staged research idea, but keeps the implementation intentionally compact and learning-friendly.

## Community

This is an early learning-oriented project. Issues, suggestions, experiments, and small focused pull requests are welcome, especially around coding-agent workflows, reproducible experiment execution, report quality, and documentation clarity.
