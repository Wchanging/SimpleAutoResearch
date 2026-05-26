# SimpleAutoResearch

[中文版本](README_zh.md)

SimpleAutoResearch is a teaching-first, lightweight auto-research project
inspired by [AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw).
It explores how an automated research assistant can move from a topic to
literature notes, small experiments, existing-code improvement tasks,
executable results, and Markdown reports while keeping the process visible and
hackable.

The goal is not to reproduce every feature of a large agent framework. The goal
is to build a clear, inspectable version that is useful for learning,
experimentation, and gradual extension.

## Goals

- Keep research steps explicit and file-based.
- Make runs easy to inspect, resume, and debug.
- Support both literature/report workflows and existing-code improvement
  workflows.
- Prefer controlled, reproducible experiments over unconstrained code
  generation.
- Keep the codebase small enough for learners and contributors to understand.

## What Works Today

- **Research reports**: run a visible staged pipeline from topic to literature
  notes, synthesis, and report artifacts.
- **Research source planning**: write a compact
  `02-search/planning/research_plan.json` for each run, with configurable
  OpenAlex/Semantic Scholar/arXiv/local-file sources, optional LLM-backed
  query planning, facet-driven query expansion, retrieval-round traces,
  screening decisions, coverage reports, follow-up retrieval rounds, document
  records, cache policy, and lightweight budgets.
- **Code tasks**: improve an existing codebase inside an isolated editable
  workspace with LLM planning, review gates, controlled patch proposals,
  validation, benchmark execution, and metric comparison.
- **Workspace strategies**: use `copy` for the safest isolated copy,
  `git_worktree` for larger git repositories where full copying is wasteful,
  or experimental `sparse_copy` for small allowlisted subsets.
- **Research-to-code runs**: embed a code task inside the 8-stage pipeline with
  repo maps, context packs, work plans, patch evidence, benchmark metrics, and
  report evidence.
- **Reviewable artifacts**: each run writes inspectable files under `runs/`
  instead of hiding decisions inside process memory.

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

Edit `.env` for LLM-backed stages:

```bash
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.openai.com/v1
SIMPLE_AR_MODEL=gpt-4o-mini
SIMPLE_AR_LLM_TIMEOUT_SEC=120
SIMPLE_AR_MAX_OUTPUT_TOKENS=4096
SIMPLE_AR_INPUT_PRICE_PER_1M=
SIMPLE_AR_OUTPUT_PRICE_PER_1M=
```

For third-party OpenAI-compatible providers, set `OPENAI_BASE_URL` to that
provider's `/v1` endpoint. Price fields are optional; when unset,
SimpleAutoResearch records token counts but leaves estimated cost as `null`.

## Quickstart

### 1. Research Report

```bash
uv run simple-ar run --topic "agent simulation" --to-stage report --max-papers 5
```

For repeatable source settings, use a run config. This local example uses a
Markdown note as a research source and writes the planned source strategy to
`02-search/planning/research_plan.json`:

```bash
uv run simple-ar run --config examples/run_configs/local_research_report.toml
```

For a literature-only pass, stop at `synthesize`, then resume report generation
from the printed run directory:

```bash
uv run simple-ar run --topic "agent simulation" --to-stage synthesize
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode research_only
```

### 2. Existing-Code Code Task

Use this when you already have a project and want the model to propose a
reviewable improvement. First write a small task file, for example
`tasks/improve_model.md`, that says what should change and what benchmark should
improve. Then create a TOML config for your project:

```toml
[code_task]
code_root = "path/to/your/project"
task_file = "tasks/improve_model.md"
output_root = "runs"
name = "my-code-task"

[benchmark]
command = "python benchmark.py"
primary_metric = "accuracy"

[benchmark.metric_directions]
accuracy = "higher"
latency_ms = "resource"

[workspace]
mode = "copy"  # copy | git_worktree | sparse_copy
```

Then run the reviewed flow. `init` prints a run directory such as
`runs/20260523-xxxx-my-code-task`; use that path in place of `runs/<run-id>`.

```bash
uv run simple-ar code-task init --config path/to/your_code_task.toml
uv run simple-ar code-task execute runs/<run-id> --config path/to/your_code_task.toml
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve --note "reviewed"
uv run simple-ar code-task execute runs/<run-id> --config path/to/your_code_task.toml --to-step propose-edits
uv run simple-ar code-task execute runs/<run-id> --config path/to/your_code_task.toml --apply-proposed-edits --timeout 60
uv run simple-ar status runs/<run-id>
```

That sequence prepares an isolated workspace, runs the baseline benchmark,
builds a work plan, stops for patch-plan review, generates
`code_task/meta/proposed_edits.json`, applies the reviewed proposal, validates
the patched workspace, runs the patched benchmark, and writes the final status.
If the result needs a bounded follow-up, use the repair path documented in
[Usage And Configuration](docs/USAGE.md#recommended-path-toml--execute).

Bundled demo configs such as `tiny_digits_mlp.toml` and
`medium_review_pipeline.toml` are documented in
[Usage And Configuration](docs/USAGE.md#recommended-path-toml--execute).

### 3. Research With Experiment

Use this when you want the research pipeline to produce literature context,
derive or use a code task, run an experiment, and include the code evidence in
the final report. For your own project, create a top-level run config:

```toml
[run]
topic = "research and improve my model"
output_root = "runs"
to_stage = "report"

[llm]
enabled = true

[search]
offline = false
max_papers = 5

[research]
# Optional source planner for 02-search.
mode = "standard"  # lite | standard | strong
sources = ["openalex", "semantic_scholar", "arxiv"]
queries = ["research and improve my model"]
cache = true

[experiment]
template = "code_task_project"
timeout = 120

[code_task]
code_root = "path/to/your/project"
# Optional. If omitted, 05-design generates a task file from research artifacts
# and a compact codebase summary.
task_file = "tasks/improve_model.md"
name = "my-research-code-task"

[benchmark]
command = "python benchmark.py"
primary_metric = "accuracy"

[benchmark.metric_directions]
accuracy = "higher"
latency_ms = "resource"

[workspace]
mode = "copy"  # copy | git_worktree | sparse_copy

[environment]
mode = "current"
```

Then run the full pipeline:

```bash
uv run simple-ar run --config path/to/your_pipeline.toml
```

This creates a normal 8-stage run. During `06-code`, it prepares the configured
project under `06-code/code_task_run/code_task/workspace`, builds repo maps and
context packs, asks the LLM for a work plan and patch proposal, applies the
patch inside the isolated workspace, and validates it. During `07-run`, it runs
the patched benchmark and compares metrics. During `08-report`, it writes a
report with deterministic code-task evidence pointing back to the nested work
plan, patch, benchmark, and comparison artifacts.

The embedded path is designed to finish end to end, so it auto-approves the
patch plan inside the isolated workspace. Use standalone `code-task` commands
when you want explicit human approval before each step. A bundled demo config is
available at `examples/run_configs/tiny_digits_mlp_pipeline.toml`; full embedded
workflow details are in [Usage And Configuration](docs/USAGE.md#embedded-code-task-in-the-8-stage-pipeline).

## Capability Boundaries

SimpleAutoResearch is useful as a learning and prototyping framework, but it is
still intentionally conservative.

- Code edits use controlled old/new replacements. This keeps patches auditable,
  but it is weaker than a full autonomous coding agent.
- The default edit scope protects tests, benchmark files, and secret-like paths
  from automated patching.
- `git_worktree` requires a git repository root with at least one local commit;
  it does not require a GitHub remote.
- `sparse_copy` is experimental and can omit runtime dependencies if the
  allowlist is too narrow.
- The tool does not yet install project dependencies or manage
  Docker/Conda/GPU/Slurm environments.
- Large code-edit proposals may still produce long LLM completions. V2.2 is
  adding bounded proposal contracts, context requests, multi-round attempts, and
  future external coding-agent adapters before recommending unattended large
  refactors.
- Literature search now has an auditable source plan and document-store
  metadata, and can use OpenAlex, Semantic Scholar, arXiv, or local
  Markdown/text notes, but it is not yet a full PDF-reading, parser-backed, or
  vector-RAG survey system.
- LLM-written reports are guarded by citation, metric, and boundary checks; when
  a draft fails these checks, the tool falls back to a structured deterministic
  report.

## Documentation

- [Usage And Configuration](docs/USAGE.md): setup, workflow-oriented examples,
  artifacts, and troubleshooting.
- [CLI Reference](docs/CLI_REFERENCE.md): command groups and option tables.
- [Configuration Reference](docs/CONFIG_REFERENCE.md): TOML sections, complete
  config examples, and workspace-mode variants.
- [Workflows And Artifacts](docs/WORKFLOWS.md): workflow presets, the 8-stage
  pipeline, and artifact layouts.
- [Development Guide](docs/DEVELOPMENT.md): how to extend stages, templates, and
  code-task modules.
- [Changelog](CHANGELOG.md): chronological development progress.

## Reference

The main reference project is
[aiming-lab/AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw).
SimpleAutoResearch borrows the staged research idea, but keeps the
implementation intentionally compact and learning-friendly.

## Community

This is an early learning-oriented project. Issues, suggestions, experiments,
and small focused pull requests are welcome, especially around coding-agent
workflows, reproducible experiment execution, report quality, and documentation
clarity.
