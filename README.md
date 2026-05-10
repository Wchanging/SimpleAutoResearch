# SimpleAutoResearch

SimpleAutoResearch is a teaching-first, lightweight auto-research pipeline. It aims to show how an automated research assistant can move from a topic to literature notes, a small experiment, executable results, and a final Markdown report without hiding the process behind a large agent framework.

This project is inspired by [AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw), but intentionally starts much smaller. The goal is not to reproduce every feature. The goal is to build a clear, inspectable version that is useful for learning, hacking, and extending.

## Goals

- Keep every research step explicit and file-based.
- Use simple stage contracts instead of a heavy orchestration framework.
- Make each run easy to inspect, resume, and debug.
- Prefer small reproducible experiments over unconstrained code generation.
- Produce learning-friendly code that can grow into more capable versions.

## Core Pipeline

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

The repository currently provides a runnable topic-to-report pipeline plus local artifact inspection and retrieval tools. This README focuses on project overview, setup, configuration, and day-to-day usage. Version-by-version learning notes, V1/V2 changes, new commands, and planned work live in [CHANGELOG.md](CHANGELOG.md).

## What It Is

SimpleAutoResearch is a small reference implementation of a staged research workflow. Each stage reads concrete files, writes concrete files, and leaves enough metadata for a learner to inspect what happened.

It is useful for studying:

- stage contracts and file-based handoffs;
- OpenAI-compatible LLM calls with visible progress and usage logging;
- arXiv-backed literature metadata;
- citation-safe report generation from known paper ids;
- template-based experiments that can be reproduced from the run directory.

## What It Is Not

SimpleAutoResearch is not a fully autonomous paper factory. In the default research pipeline, the LLM helps with planning, reading notes, synthesis, and report drafting, but it does not freely edit or invent experiment code. Experiment scripts are generated from a small whitelist of templates so the workflow stays inspectable, reproducible, and safe enough for teaching.

## Quickstart

The current implementation creates the staged run directory, can search live literature metadata, generates a template-based toy text-classification experiment, runs it in a subprocess, parses metrics, and can optionally use the OpenAI SDK for the plan/read/synthesize stages. Command-line runs show stage progress and LLM token usage by default so the workflow remains inspectable while it is running.

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

Use `--max-papers` to control live literature result count, `--llm-workers` to control concurrent LLM requests in stages that can batch work, and `--quiet` when you only want the final summary. Use `--retrieval-top-k` to control how many local snippets each evidence query returns, and `--no-retrieval` to disable V2 source planning and evidence logging for a run.

```bash
uv run simple-ar run --topic "toy topic" --to-stage report --max-papers 5 --llm-workers 4 --experiment-timeout 30
```

Offline mode is available for contract and pipeline testing. `--no-llm` disables model calls, while `--offline-search` disables arXiv and uses fixture paper metadata:

```bash
uv run simple-ar run --topic "toy topic" --to-stage report --no-llm --offline-search
```

Successful OpenAlex and arXiv searches are cached locally in `.simple_ar_cache/literature` for short-term reuse. Live search tries OpenAlex first, then arXiv, following the same broad idea used by AutoResearchClaw: prefer more generous scholarly APIs before hitting arXiv's stricter endpoint. If live providers fail, SimpleAutoResearch tries provider-specific cache entries. When no cache exists, the default behavior is to fail clearly rather than silently replacing real literature with fixture metadata. Use `--allow-fixture-fallback` only for demos where continuing with placeholder paper metadata is acceptable. Use `--strict-search` when you also want to disable cache fallback and require a live provider response.

```bash
uv run simple-ar run --topic "agent simulation" --to-stage search --strict-search
uv run simple-ar run --topic "agent simulation" --to-stage report --allow-fixture-fallback
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
- `artifact_index.json`: local artifact index generated by `inspect` or `search-artifacts`.
- `artifact_chunks.jsonl`: line-addressable chunks generated for local artifact retrieval.
- `artifact_search_results.json`: last artifact search result with scored snippets.
- `source_plan.json`: local source plan describing which artifacts each stage should consult.
- `activity_log.jsonl`: structured activity log for source planning and retrieval actions.
- `evidence_ledger.jsonl`: ledger of snippets used by stages, each with path and line range.
- `06-code/experiment.py`: generated from the fixed `toy_text_classification` template.
- `07-run/results.json`: subprocess return code, timeout flag, command, and parsed metrics.
- `08-report/report.md`: final Markdown report assembled from staged artifacts.
- `08-report/references.bib`: BibTeX generated from the body-cited subset of `papers.jsonl`.
- `08-report/manifest.json`: report package manifest listing source artifacts, report artifacts, experiment metadata, metrics, and rerun commands.

When LLM mode is enabled, the report stage asks the model to write a more paper-like Markdown report from the staged artifacts. The prompt is evidence-bounded: it may only use known paper ids, staged literature metadata, source-labelled retrieval snippets, and numbers from `results.json`. The report prompt includes an explicit allowed citation-key list, and the system rejects model reports that omit body citations when paper metadata exists. The system still strips any model-written references section, keeps only papers cited in the report body, and appends verified references from that cited subset of `papers.jsonl`.

## Artifact Tools

Existing run directories can be inspected and searched without rerunning earlier stages:

```bash
uv run simple-ar inspect runs/<run-id>
uv run simple-ar search-artifacts runs/<run-id> "accuracy"
```

`inspect` writes `artifact_index.json` with relative paths, file kinds, inferred stages, sizes, hashes, timestamps, summaries, and tags. It skips hidden directories, cache directories, bytecode, and retrieval-generated files.

`search-artifacts` writes `artifact_chunks.jsonl` and `artifact_search_results.json`. The chunker supports Markdown sections, JSON keys/items, JSONL rows, Python imports/classes/functions, and plain text windows. To keep retrieval lean, chunks default to source artifacts and skip runner bookkeeping such as `stage_meta.json`, root manifests, pipeline state, usage logs, and generated retrieval files. Use `--include-operational` when you explicitly want to search those files for debugging. Search is lexical and deterministic, which keeps it cheap, testable, and provider-independent.

During normal pipeline runs, retrieval is enabled by default. The read, synthesize, and report stages create a deterministic `source_plan.json`, log retrieval activity to `activity_log.jsonl`, and append source-labelled snippets to `evidence_ledger.jsonl`. LLM prompts can then receive compact evidence snippets with paths and line ranges instead of a single unlabelled blob of context.

You can tune or disable this behavior:

```bash
uv run simple-ar run --topic "toy topic" --to-stage report --retrieval-top-k 4
uv run simple-ar run --topic "toy topic" --to-stage report --no-retrieval
```

## Code Task Workspace

`code-task init` starts a separate workflow for existing codebases or benchmarks. It copies the source project into an isolated run workspace and builds a Python-aware code index, but it does not call an LLM, apply patches, or run benchmarks.

`code-task plan` then turns the task, code index, and selected source snippets into a human-reviewable `patch_plan.md`. It may use the configured OpenAI-compatible LLM, or `--no-llm` for a deterministic offline plan. Planning never edits files.

```bash
uv run simple-ar code-task init \
  --code-root path/to/project \
  --task-file path/to/task.md \
  --benchmark-command "python -m unittest discover -s tests"

uv run simple-ar code-task plan runs/<run-id>
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve --note "reviewed"
```

The original `--code-root` is not modified. Generated files are kept under `runs/<run-id>/code_task/`, with the editable copy in `workspace/`, `patch_plan.md` at the workflow root, and metadata such as `codebase_index.json` and `hitl_decisions.jsonl` in `meta/`. By default, the copier skips common cache/build directories, symlinks, `.env` secrets, bytecode, and files larger than 2 MB. Use `--max-file-bytes 0` only when you explicitly want to disable the size guard.

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
| --- | --- | --- |
| `plan` | `goal.md`, `problem.md` | Scope the topic into a concrete research question. |
| `search` | `papers.jsonl`, `search_meta.json` | Collect normalized OpenAlex/arXiv paper metadata or explicit offline fixture metadata. |
| `read` | `paper_notes.json`, `notes.md` | Convert paper metadata into structured notes. |
| `synthesize` | `synthesis.md`, `hypothesis.md` | Produce a bounded synthesis and testable hypothesis. |
| `design` | `experiment_plan.json` | Select a safe experiment template and parameters. |
| `code` | `experiment.py` | Generate code from the selected template. |
| `run` | `results.json`, `stdout.txt`, `stderr.txt` | Execute the experiment and parse numeric metrics. |
| `report` | `report.md`, `references.bib`, `manifest.json` | Write a paper-like report and reproducibility package. |

## Citation Safety

Reports may only cite paper ids that appear in `02-search/papers.jsonl`. The report stage validates citations before writing the final artifact. If an LLM writes a reference section, SimpleAutoResearch strips it and appends a deterministic reference list generated from the papers that are actually cited in the report body. The full retrieved metadata remains in `02-search/papers.jsonl` and the report manifest.

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

The current template system is deliberately not free-form code generation. That boundary is what makes the project useful for learning and safe enough to run repeatedly.

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

See [CHANGELOG.md](CHANGELOG.md) for version history, active V2 work, and planned next steps.

## Reference

The main reference project is [aiming-lab/AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw). SimpleAutoResearch borrows the core idea of a staged research pipeline, but keeps the first version intentionally compact.
