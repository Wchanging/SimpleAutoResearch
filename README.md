# SimpleAutoResearch

SimpleAutoResearch is a teaching-first, lightweight auto-research pipeline. It aims to show how an automated research assistant can move from a topic to literature notes, a small experiment, executable results, and a final Markdown report without hiding the process behind a large agent framework.

This project is inspired by [AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw), but intentionally starts much smaller. The goal is not to reproduce every feature. The goal is to build a clear, inspectable version that is useful for learning, hacking, and extending.

## Goals

- Keep every research step explicit and file-based.
- Use simple stage contracts instead of a heavy orchestration framework.
- Make each run easy to inspect, resume, and debug.
- Prefer small reproducible experiments over unconstrained code generation.
- Produce learning-friendly code that can grow into more capable versions.

## Planned V1 Pipeline

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

This repository is in early development. The first milestone is a minimal V1 that can run one complete topic-to-report workflow in about one week of focused implementation.

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

Each run may include:

- `02-search/papers.jsonl`: normalized paper metadata.
- `02-search/search_meta.json`: query, source, status, and result count.
- `llm_usage.jsonl`: one row per successful LLM request.
- `llm_usage_summary.json`: aggregate token counts and optional cost estimate.
- `06-code/experiment.py`: generated from the fixed `toy_text_classification` template.
- `07-run/results.json`: subprocess return code, timeout flag, command, and parsed metrics.
- `08-report/references.bib`: BibTeX generated only from `papers.jsonl`.

## Experiment Templates

The first supported template is `toy_text_classification`. It embeds a tiny spam-classification dataset inside the generated `06-code/experiment.py`, compares keyword rules against bag-of-words logistic regression, prints machine-parseable metric lines, and writes captured results to `07-run/results.json`.

This design deliberately keeps the experiment surface narrow:

- new templates must be added in code and listed in the template whitelist;
- generated scripts run in a subprocess with a timeout;
- stdout, stderr, return code, timeout state, and parsed metrics are saved;
- unsupported template names fail early instead of producing unknown code.

Run tests:

```bash
uv run python -m unittest discover -s tests
```

## Reference

The main reference project is [aiming-lab/AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw). SimpleAutoResearch borrows the core idea of a staged research pipeline, but keeps the first version intentionally compact.
