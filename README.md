# SimpleAutoResearch

[中文版本](README_zh.md)

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
SIMPLE_AR_LLM_TIMEOUT_SEC=120
SIMPLE_AR_MAX_OUTPUT_TOKENS=4096
SIMPLE_AR_INPUT_PRICE_PER_1M=
SIMPLE_AR_OUTPUT_PRICE_PER_1M=
```

For third-party OpenAI-compatible providers, set `OPENAI_BASE_URL` to that provider's `/v1` endpoint. `SIMPLE_AR_LLM_TIMEOUT_SEC` bounds each provider request, and `SIMPLE_AR_MAX_OUTPUT_TOKENS` keeps oversized generations under control. Price fields are optional; when unset, SimpleAutoResearch records token counts but leaves estimated cost as `null`.

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

Use this when you already have a baseline project and want an LLM to propose a
reviewable improvement inside an isolated workspace. The recommended path is a
TOML config plus the state-aware executor:

```bash
uv run simple-ar code-task init --config examples/code_tasks/configs/tiny_digits_mlp.toml
```

For copyable PowerShell commands, set `RUN` to the latest matching run
directory:

```powershell
$RUN = Join-Path "runs" ((Get-ChildItem .\runs -Directory |
  Where-Object { $_.Name -like "*tiny-digits-mlp*" } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1).Name)
```

Run the reviewed executor flow:

```powershell
uv run simple-ar code-task execute $RUN --config examples/code_tasks/configs/tiny_digits_mlp.toml

# Approve the plan after reading code_task/patch_plan.md.
uv run simple-ar code-task decide-plan $RUN --decision approve --note "reviewed"

# Continue explicitly to edit proposal review.
uv run simple-ar code-task execute $RUN `
  --config examples/code_tasks/configs/tiny_digits_mlp.toml `
  --to-step propose-edits

# Apply the reviewed proposal and run validation/benchmark.
uv run simple-ar code-task execute $RUN `
  --config examples/code_tasks/configs/tiny_digits_mlp.toml `
  --apply-proposed-edits `
  --timeout 60

uv run simple-ar status $RUN
```

The first `execute` stops at `approval_required` after writing environment,
baseline, work-plan, batch-state, and patch-plan artifacts. The second executor
call writes `code_task/meta/proposed_edits.json` for review. The final executor
call applies the reviewed proposal, validates the workspace, runs the patched
benchmark, writes `code_task/run/comparison.json`, and refreshes
`code_task/summary.md`. Treat `objective_improved` as the normal success signal;
if the benchmark passes but the objective is `regressed` or `mixed`, revise the
plan/proposal instead of marking the task complete.
The default editor backend is `controlled_patch`, which produces bounded
old/new replacements and records backend metadata in proposal, apply, batch,
and manifest artifacts.

For a more realistic multi-file example with `main.py`, JSON config, progress
output, and cross-module feature/model wiring, use:

```bash
uv run simple-ar code-task init --config examples/code_tasks/configs/medium_review_pipeline.toml
```

Then follow the same executor sequence above with the medium config path and a
`*medium-review-pipeline*` `$RUN` filter. Because that config enables streamed
benchmark output, `execute` will relay progress lines while still saving the
full logs under `code_task/run/<label>/`. The medium task usually merges
feature, model, and config changes into one reviewed `large` batch, so the final
apply command should include `--allow-large-edits` only after reviewing
`code_task/meta/proposed_edits.json`.

If the patch needs repair, request one bounded repair proposal:

```powershell
uv run simple-ar code-task execute $RUN `
  --config examples/code_tasks/configs/tiny_digits_mlp.toml `
  --to-step repair `
  --repair-rounds 1 `
  --timeout 60
```

Detailed primitive commands, explicit CLI-flag initialization, artifact
descriptions, and troubleshooting notes live in
[Usage And Configuration](docs/USAGE.md#code-task-workflow) and
[CLI Reference](docs/CLI_REFERENCE.md#code-task-commands).

### 3. Research With Experiment

Use a user project through the generic embedded code-task template. The shortest
form is a top-level run config:

```bash
uv run simple-ar run --config examples/run_configs/tiny_digits_mlp_pipeline.toml
```

The same run can be expressed with CLI flags when you want quick overrides:

```bash
uv run simple-ar run \
  --topic "improve tiny digits MLP" \
  --to-stage report \
  --experiment-template code_task_project \
  --code-task-config examples/code_tasks/configs/tiny_digits_mlp.toml \
  --offline-search \
  --experiment-timeout 60
```

This prepares the configured project under `06-code/code_task_run/code_task/workspace`, runs a baseline benchmark, builds a repo map/context pack, asks the LLM for a batch-oriented work plan, creates an attempt/batch record, asks for a patch plan and controlled edits, applies the patch inside that isolated workspace, runs the patched benchmark, and writes code-task evidence into the final report. If no task file is supplied for `code_task_project`, `05-design` now derives `generated_code_task.md` from the earlier research artifacts and a compact codebase summary, then `06-code` uses it as the normal `code_task/task.md`. Because the 8-stage pipeline must finish end to end, it auto-approves the patch plan inside that isolated workspace. Use standalone `code-task` commands when you want explicit human approval before each step.

There is also a legacy bundled toy-spam smoke test, kept mostly for quick regression checks:

```bash
uv run simple-ar run \
  --topic "LLM-guided improvement of a toy spam baseline" \
  --to-stage report \
  --experiment-template llm_code_task_toy_spam \
  --offline-search \
  --experiment-timeout 60
```

The toy template is useful for smoke testing because it has a tiny deterministic benchmark.

## Current Capability Boundaries

SimpleAutoResearch is usable as a learning and prototyping framework, but it is still intentionally conservative.

What works today:

- Topic-to-report runs with visible 8-stage artifacts and resumable execution.
- OpenAI-compatible LLM calls for planning, paper notes, synthesis, report drafting, and code-task patch planning.
- Literature-first report mode: stop at `synthesize`, then resume `report` to produce a survey-style report without experiment claims.
- Existing-code code tasks as a standalone workflow: prepare a source project with `copy`, `git_worktree`, or experimental `sparse_copy`, probe the environment, index files, build repo maps, locate likely files, package bounded context, run a baseline benchmark, generate a context-aware patch plan, require human approval, call the default `controlled_patch` editor backend for bounded edits, apply edits in the isolated workspace, validate, run a patched benchmark, and compare before/after metrics.
- Default code-task edit scope: tests, benchmark files, and secret-like paths are read-only evidence, so the model can use allowed context but cannot patch them to improve metrics.
- Configurable benchmark metric interpretation for code tasks through `--primary-metric` and repeated `--metric-direction METRIC=DIRECTION` flags.
- Embedded 8-stage code-task experiments through `--experiment-template code_task_project` plus a code-task TOML config or explicit code-root/benchmark flags. A task file can be provided by the user or generated during `05-design`; `06-code` then uses the same repo-map, context-pack, work-plan, and attempt/batch evidence model as standalone code tasks.
- One bundled 8-stage smoke-test demo through `--experiment-template llm_code_task_toy_spam`.
- Citation, report-boundary, runtime-limit, and metric-visibility checks in the final report package.

Important limits:

- The generic 8-stage code-task path is real but still conservative. It can prepare a user project with copy mode, repo-root `git_worktree` mode, or experimental sparse-copy mode and run one bounded LLM work-plan/batch/patch pass, but it is not yet a full autonomous coding agent with deep multi-round planning, dependency installation, Docker/Conda setup, or large experiment scheduling.
- The 8-stage code-task path auto-approves the model patch plan inside the isolated workspace so the pipeline can complete end to end. Use standalone `code-task` for stronger human-in-the-loop review.
- Code edits are controlled old/new replacements. This keeps patches auditable, but it is weaker than a full coding agent that can plan and edit many files across multiple autonomous rounds.
- The editor backend interface now exists. The reserved `external_agent` backend
  has a design-time permission model and invocation-plan artifact, but
  Codex/Claude/OpenCode adapters are not executable yet.
- Large code-edit proposals can still produce very long LLM completions. V2.2 treats this as an editor-backend design target: bounded proposal contracts, context requests, multi-round attempts, and future external coding-agent adapters are planned before recommending the tool for large unattended refactors.
- Reviewed proposals may contain multiple ordered edits in one file, but invalid old/new replacements are rejected before workspace files are changed.
- By default, code-task patches reject protected paths such as `tests/**`, `test_*.py`, `benchmark.py`, and `*benchmark*.py`. If the real task is to update tests or benchmarks, handle that as a separate human-reviewed repository change rather than an automated metric-improvement patch.
- The tool does not install project dependencies, manage Docker/Conda/GPU/Slurm environments, or schedule large experiments.
- Literature search currently works from metadata and local artifact snippets. It is not yet a full PDF-reading or vector-RAG survey system.
- LLM-written reports are guarded. If the draft invents citations, omits required citations, or overstates toy evidence, SimpleAutoResearch falls back to a structured deterministic report.

V2.2 development has started with workspace-mode abstraction, minimal git worktree support, experimental sparse-copy support, layered repo-map artifacts, deterministic locate results, and bounded context packs. The next focus is deeper coding loops: multi-round attempts, stronger task decomposition, managed environments, and a clearer human-in-the-loop path from existing research code to reproducible results.

## Documentation

- [Usage And Configuration](docs/USAGE.md): installation, environment variables, commands, and examples.
- [CLI Reference](docs/CLI_REFERENCE.md): command groups, options, and code-task init config schema.
- [Workflows And Artifacts](docs/WORKFLOWS.md): workflow presets, the 8-stage pipeline, and artifact layouts.
- [Development Guide](docs/DEVELOPMENT.md): how to extend stages, templates, and code-task modules.
- [Changelog](CHANGELOG.md): chronological development progress.

## Reference

The main reference project is [aiming-lab/AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw). SimpleAutoResearch borrows the staged research idea, but keeps the implementation intentionally compact and learning-friendly.

## Community

This is an early learning-oriented project. Issues, suggestions, experiments, and small focused pull requests are welcome, especially around coding-agent workflows, reproducible experiment execution, report quality, and documentation clarity.
