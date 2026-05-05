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

## Quickstart

The current implementation creates the staged run directory, writes placeholder artifacts for the later stages, and can optionally use the OpenAI SDK for the plan/read/synthesize stages. Command-line runs show stage progress by default so the workflow remains inspectable while it is running.

Create a local environment file:

```bash
cp .env.example .env
```

Then set `OPENAI_API_KEY` in `.env`. For third-party OpenAI-compatible APIs, also set `OPENAI_BASE_URL`, for example `https://api.example.com/v1`. You can set `SIMPLE_AR_MODEL`, which defaults to `gpt-4o-mini`.

```bash
uv run simple-ar run --topic "toy topic" --to-stage report --model gpt-4o-mini
uv run simple-ar status runs/<run-id>
```

Use `--llm-workers` to control concurrent LLM requests in stages that can batch work, and `--quiet` when you only want the final summary:

```bash
uv run simple-ar run --topic "toy topic" --to-stage report --llm-workers 4
```

Offline mode is available for contract and pipeline testing:

```bash
uv run simple-ar run --topic "toy topic" --to-stage report --no-llm
```

Run tests:

```bash
uv run python -m unittest discover -s tests
```

## Reference

The main reference project is [aiming-lab/AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw). SimpleAutoResearch borrows the core idea of a staged research pipeline, but keeps the first version intentionally compact.
