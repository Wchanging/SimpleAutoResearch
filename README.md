# SimpleAutoResearch

SimpleAutoResearch is a teaching-first, lightweight auto-research pipeline. It aims to show how an automated research assistant can move from a topic to literature notes, a small experiment, executable results, and a final Markdown report without hiding the process behind a large agent framework.

This project is inspired by [AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw), but intentionally starts much smaller. The goal is not to reproduce every feature. The goal is to build a clear, inspectable version that is useful for learning, hacking, and extending.

## Goals

- Keep every research step explicit and file-based.
- Use simple stage contracts instead of a heavy orchestration framework.
- Make each run easy to inspect, resume, and debug.
- Prefer small reproducible experiments over unconstrained code generation.
- Produce learning-friendly code that can grow into more capable versions.

## V1 Pipeline

```text
01 plan        Scope the topic and research question
02 search      Collect real paper metadata
03 read        Create literature notes from paper metadata
04 synthesize  Summarize themes and propose a testable hypothesis
05 design      Create a small experiment plan
06 code        Generate experiment code from templates
07 run         Execute the experiment and parse metrics
08 report      Write a final Markdown report with references
```

## Status

V1 is published on `main` as a compact, runnable topic-to-report teaching pipeline. Active V2 work lives on the `feat/v2-retrieval-codegen` branch, where the project is exploring retrieval, local artifact analysis, code-task workflows, and cleaner human-in-the-loop checkpoints.

## V2 Direction

V2 is not just an incremental feature pass over V1. It is a chance to re-examine the architecture and keep the project useful as a learning reference instead of letting every new feature become tightly coupled to one full pipeline.

The main design shift is workflow decoupling:

- `research-run`: the current staged research workflow, upgraded with better retrieval, evidence tracking, and report quality checks.
- `code-task`: a focused workflow for analyzing and improving an existing codebase, benchmark, or experiment script. It should copy the target into an isolated run workspace, build a lightweight code index, ask the model for an edit plan, support human approval before risky changes, run validation commands, and preserve failure evidence.
- `review` or `survey`: a no-code workflow for literature review and report drafting, where search, reading, synthesis, and reporting can run without experiment design, code generation, or benchmark execution.

V2 will stay local-first and inspectable. Local retrieval should start with metadata, snippets, notes, and small indexes rather than blindly storing every full paper. Code execution should prefer isolated run directories, explicit commands, timeouts, and human approval points before moving toward stronger sandboxing.

## What It Is

SimpleAutoResearch is a small reference implementation of a staged research workflow. Each stage reads concrete files, writes concrete files, and leaves enough metadata for a learner to inspect what happened.

It is useful for studying:

- stage contracts and file-based handoffs;
- OpenAI-compatible LLM calls with visible progress and usage logging;
- arXiv-backed literature metadata;
- citation-safe report generation from known paper ids;
- template-based experiments that can be reproduced from the run directory.

## What It Is Not

SimpleAutoResearch is not a fully autonomous paper factory. In the current V1 path, the LLM helps with planning, reading notes, and synthesis, but it does not freely edit or invent experiment code. Experiment scripts are generated from a small whitelist of templates so the first version stays inspectable, reproducible, and safe enough for teaching.

## Quickstart

The current implementation creates the staged run directory, can search arXiv metadata, generates a template-based toy text-classification experiment, runs it in a subprocess, parses metrics, and can optionally use the OpenAI SDK for the plan/read/synthesize stages. Command-line runs show stage progress and LLM token usage by default so the workflow remains inspectable while it is running.

Create a local environment file:

```bash
cp .env.example .env
```

Then set `OPENAI_API_KEY` in `.env`. For third-party OpenAI-compatible APIs, also set `OPENAI_BASE_URL`, for example `https://api.example.com/v1`. You can set `SIMPLE_AR_MODEL`, which defaults to `gpt-4o-mini`.

Optional cost estimates can be enabled by setting `SIMPLE_AR_INPUT_PRICE_PER_1M` and `SIMPLE_AR_OUTPUT_PRICE_PER_1M` in `.env`. When prices are not configured, SimpleAutoResearch still records token counts but leaves cost as `null`.

```bash
uv run simple-ar run --topic "toy topic" --to-stage report --model gpt-4o-mini
uv run simple-ar status runs/<run-id>
```

Use `--max-papers` to control arXiv result count, `--llm-workers` to control concurrent LLM requests in stages that can batch work, and `--quiet` when you only want the final summary:

```bash
uv run simple-ar run --topic "toy topic" --to-stage report --max-papers 5 --llm-workers 4 --experiment-timeout 30
```

Offline mode is available for contract and pipeline testing. `--no-llm` disables model calls, while `--offline-search` disables arXiv and uses fixture paper metadata:

```bash
uv run simple-ar run --topic "toy topic" --to-stage report --no-llm --offline-search
```

Successful arXiv searches are cached locally in `.simple_ar_cache/literature` for short-term reuse. If arXiv returns HTTP 429 or another provider error, SimpleAutoResearch first tries that cache, then falls back to fixture metadata so the pipeline remains runnable for demos and tests. A small circuit breaker also pauses live arXiv attempts briefly after rate limits. Use `--strict-search` when you want a failed arXiv search to stop the run instead of producing a cache-backed or fixture-backed report:

```bash
uv run simple-ar run --topic "agent simulation" --to-stage search --strict-search
```

Resume a run from the next recorded stage, or explicitly choose where to restart:

```bash
uv run simple-ar resume runs/<run-id>
uv run simple-ar resume runs/<run-id> --from-stage run --to-stage report
```

Each run may include:

- `manifest.json`: root run manifest with stage statuses and declared outputs.
- `pipeline_state.json`: last completed stage and next stage for resume.
- `02-search/papers.jsonl`: normalized paper metadata.
- `02-search/search_meta.json`: query, source, status, and result count.
- `llm_usage.jsonl`: one row per successful LLM request.
- `llm_usage_summary.json`: aggregate token counts and optional cost estimate.
- `06-code/experiment.py`: generated from the fixed `toy_text_classification` template.
- `07-run/results.json`: subprocess return code, timeout flag, command, and parsed metrics.
- `08-report/report.md`: final Markdown report assembled from staged artifacts.
- `08-report/references.bib`: BibTeX generated only from `papers.jsonl`.
- `08-report/manifest.json`: report package manifest listing source artifacts, report artifacts, experiment metadata, metrics, and rerun commands.

When LLM mode is enabled, the report stage asks the model to write a more paper-like Markdown report from the staged artifacts. The prompt is evidence-bounded: it may only use known paper ids, staged literature metadata, and numbers from `results.json`. The system still strips any model-written references section and appends verified references from `papers.jsonl`.

## Experiment Templates

The first supported template is `toy_text_classification`. It embeds a tiny spam-classification dataset inside the generated `06-code/experiment.py`, compares keyword rules against bag-of-words logistic regression, prints machine-parseable metric lines, and writes captured results to `07-run/results.json`.

This design deliberately keeps the experiment surface narrow:

- new templates must be added in code and listed in the template whitelist;
- generated scripts run in a subprocess with a timeout;
- stdout, stderr, return code, timeout state, and parsed metrics are saved;
- unsupported template names fail early instead of producing unknown code.

## Why Stage Contracts

The main teaching idea in this repository is that "agentic" behavior becomes easier to understand when every step has a small contract. A stage contract names the files a stage requires and the files it must produce. The runner checks those files before and after each stage, writes `stage_meta.json`, and records pipeline state for resume.

This keeps the first version simple:

- stages communicate through files, not hidden Python objects;
- failed stages say which input or output is missing;
- a run can be inspected with a normal file explorer;
- later agent abstractions can wrap stages without changing the artifact model.

## Run Directory Explained

A completed run looks like this:

```text
runs/<run-id>/
  manifest.json
  pipeline_state.json
  config_snapshot.json
  topic.txt
  llm_usage.jsonl
  llm_usage_summary.json
  01-plan/
  02-search/
  03-read/
  04-synthesize/
  05-design/
  06-code/
  07-run/
  08-report/
```

The root `manifest.json` is the quick index for the run. `pipeline_state.json` stores the last stage and next stage for resume. Each numbered stage directory contains its own outputs plus `stage_meta.json`, which records status, duration, declared inputs, declared outputs, and any error message.

## Pipeline Stages

| Stage | Main outputs | Purpose |
|---|---|---|
| `plan` | `goal.md`, `problem.md` | Scope the topic into a concrete research question. |
| `search` | `papers.jsonl`, `search_meta.json` | Collect normalized arXiv paper metadata or explicit fallback metadata. |
| `read` | `paper_notes.json`, `notes.md` | Convert paper metadata into structured notes. |
| `synthesize` | `synthesis.md`, `hypothesis.md` | Produce a bounded synthesis and testable hypothesis. |
| `design` | `experiment_plan.json` | Select a safe experiment template and parameters. |
| `code` | `experiment.py` | Generate code from the selected template. |
| `run` | `results.json`, `stdout.txt`, `stderr.txt` | Execute the experiment and parse numeric metrics. |
| `report` | `report.md`, `references.bib`, `manifest.json` | Write a paper-like report and reproducibility package. |

## Citation Safety

Reports may only cite paper ids that appear in `02-search/papers.jsonl`. The report stage validates citations before writing the final artifact. If an LLM writes a reference section, SimpleAutoResearch strips it and appends a deterministic reference list generated from the same paper metadata used for validation.

This is intentionally conservative. It does not prove that every cited paper is relevant, but it prevents the most common failure mode: invented citation keys or fabricated bibliography entries.

## Adding a Stage

To add a stage, update these places together:

1. Add the enum value in `src/simple_ar/stages.py`.
2. Add a `StageContract` in `src/simple_ar/contracts.py`.
3. Implement a handler function in `src/simple_ar/stage_handlers.py`.
4. Register the handler in `HANDLERS`.
5. Add a focused test that checks the new files are produced and validated.

Keep the first version file-first. A new stage should read existing artifacts with `ctx.find_artifact(...)` and write its own outputs with `ctx.artifact_path(...)`.

## Adding an Experiment Template

Experiment templates live in `src/simple_ar/experiment/templates.py`. A new template should:

- be added to `SUPPORTED_TEMPLATES`;
- generate a complete standalone `experiment.py`;
- use only dependencies declared in `pyproject.toml`;
- print machine-parseable metric lines like `metric_name: 0.123`;
- avoid network access and uncontrolled downloads;
- have a test in `tests/test_experiment_runner.py`.

The current template system is deliberately not free-form code generation. That boundary is what makes V1 useful for learning and safe enough to run repeatedly.

## Development

Run tests:

```bash
uv run python -m unittest discover -s tests
```

Useful local smoke run:

```bash
uv run simple-ar run --topic "keyword rules versus bag-of-words logistic regression for toy spam classification" --to-stage report --no-llm --offline-search
```

## Roadmap

- Artifact retrieval over local run outputs, paper metadata, notes, and optional user-provided files.
- Decoupled `code-task` workflow for codebase analysis, patch planning, validation, and benchmark-oriented iteration.
- No-code `review` or `survey` workflow for literature analysis and report generation.
- Human-in-the-loop checkpoints before patch application, expensive runs, and final report acceptance.
- More report quality checks, including citation relevance, unsupported numerical claims, and evidence coverage.
- Optional Docker or virtual-environment sandboxing once the local subprocess runner is stable.

## Reference

The main reference project is [aiming-lab/AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw). SimpleAutoResearch borrows the core idea of a staged research pipeline, but keeps the first version intentionally compact.
