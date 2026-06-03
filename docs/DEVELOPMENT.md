# Development Guide

[中文版本](DEVELOPMENT_zh.md)

This document is for contributors who want to extend SimpleAutoResearch. For command details, see [CLI Reference](CLI_REFERENCE.md). For TOML schema details, see [Configuration Reference](CONFIG_REFERENCE.md). For setup walkthroughs, see [Usage And Configuration](USAGE.md). For workflow concepts and artifacts, see [Workflows And Artifacts](WORKFLOWS.md).

## Project Shape

SimpleAutoResearch is now file-first plus state-backed:

- stages read and write concrete files;
- workflow state is visible in `state.json` and stage contracts;
- tests verify contracts/artifacts instead of hidden in-memory state;
- risky code changes happen in isolated editable workspaces, usually a guarded
  copy, optionally a detached git worktree, and experimentally a sparse copy.

This keeps the project easier to learn, debug, and refactor.

The old monolithic CLI and stage handler modules live under private
`src/simple_ar/_legacy/` during the reboot. Public imports still work through
small compatibility wrappers, but new behavior should be implemented in the
domain modules under `core/`, `research/`, `experiment/`, and `code_task/`.

Top-level implementation modules have been collapsed into domain packages.
Prefer direct imports from `core/*`, `app/*`, `integrations/*`, `research/*`,
`experiment/*`, `report/*`, or `code_task/*`. Do not reintroduce broad
compatibility facades for new code.

Research code is grouped by evidence lifecycle:

```text
src/simple_ar/research/
  planning/    research questions and executable query plans
  sources/     source plan contracts plus connector-neutral query objects
  connectors/  OpenAlex, Semantic Scholar, arXiv, and local-file adapters
  documents/   document records, full-text hints, parser/extractor helpers
  store/       chunks and local index backends
  evidence/    retrieval screening, coverage, optional debug evidence cards
  outputs/     search-stage artifact writers
```

Keep new retrieval/evidence work inside these packages instead of returning to
the old flat `research/*.py` layout.

## Adding A Pipeline Stage

To add a stage to the default research pipeline, update these places together:

1. Add the enum value in `src/simple_ar/core/stages.py`.
2. Add or extend the typed state/contract models in `src/simple_ar/app/state.py`
   and `src/simple_ar/core/contracts.py`.
3. Implement stage behavior in the responsible domain service, for example
   `src/simple_ar/research/service.py` or `src/simple_ar/experiment/service.py`.
4. Add a thin adapter in the pipeline registry. During the reboot this still
   means `src/simple_ar/_legacy/stage_handlers.py`; once that file is retired,
   use the new domain registry instead.
5. Register the handler in `HANDLERS`.
6. Add a focused test that checks state updates and declared outputs.

A new stage should prefer explicit `ctx.state.<stage>` pointers and compact
stage contracts over reverse-scanning run folders. `ctx.find_artifact(...)`
exists for legacy fallback only.

## Adding An Experiment Template

Fixed script templates primarily live in `src/simple_ar/experiment/templates.py`.
Embedded 8-stage code-task templates live in
`src/simple_ar/experiment/code_task_experiment.py` because they prepare an existing
workspace before writing the run harness.

Use `src/simple_ar/experiment/runner.py` for fixed generated-template
subprocesses. Use `src/simple_ar/code_task/` for LLM-guided project editing,
workspace isolation, patching, validation, and benchmark comparison.

Top-level run config parsing lives in `src/simple_ar/app/run_config.py`. Keep it as
a thin TOML-to-runtime-options layer; code-task-specific config semantics should
continue to live in `src/simple_ar/code_task/runtime/config.py` so standalone
and embedded code-task runs do not drift apart.

A new template should:

- be added to `SUPPORTED_TEMPLATES`;
- generate a complete standalone `experiment.py`;
- use only dependencies declared in `pyproject.toml`;
- print machine-parseable metric lines like `metric_name: 0.123`, parsed by
  `src/simple_ar/experiment/metrics.py`;
- avoid network access and uncontrolled downloads;
- have a test in `tests/test_experiment_runner.py`.

The current template system is deliberately not free-form code generation. That boundary keeps the teaching pipeline reproducible while stronger coding workflows develop under `code-task`.

For embedded code-task templates, keep the automatic approval boundary explicit:
they should copy a workspace, use controlled old/new edits, write a compact
stage artifact such as `code_task_experiment.json`, and run the benchmark
through `07-run` instead of silently mutating source code during reporting.
The generic `code_task_project` template should remain a thin bridge over the
standalone code-task modules rather than a separate coding implementation.

## Extending Code Task

The code-task workflow is grouped by lifecycle rather than being a flat folder:

```text
src/simple_ar/code_task/
  runtime/        config and path/manifest helpers
  workspace/      copy, git worktree, sparse-copy preparation
  analysis/       codebase index, repo map, locate, context packs
  editing/        work plan, patch plan, edit budgets, patch proposal/application
  execution/      environment probe, validation, benchmark, comparison, repair
  orchestration/  init and execute flows that compose the lower-level modules
```

New code should go into the lifecycle package that owns the behavior. Avoid
adding more flat files directly under `code_task/` unless the file is a public
facade or a genuinely cross-cutting boundary.

When adding a new code-task feature:

- keep original source directories read-only;
- write artifacts under `code_task/meta`, `code_task/run`, or `code_task/repairs`;
- keep CLI steps explicit until the underlying behavior is stable;
- add tests that exercise both the library function and CLI path when useful;
- prefer small composable functions over a single agent loop.

Metric comparison should stay conservative. Unknown numeric metrics may be
recorded as deltas, but they should not decide an improved/regressed verdict
unless their direction is known from explicit manifest configuration or a
simple local heuristic. Add new default heuristics only when the metric naming
convention is common enough to be unsurprising.

### Code-Task Environment Policy

The current V2.2 code-task runner has workspace isolation through `copy`,
`git_worktree`, or experimental `sparse_copy`, command timeouts, optional
streamed benchmark output, captured stdout/stderr, a restricted environment
map, and an explicit execution interpreter policy. It supports `current` and
`external` modes, but it does not yet create or install into a separate Python
environment. Unless a future feature explicitly changes this, do not install
user project dependencies into SimpleAutoResearch's own `.venv` by default.

Environment support should evolve in layers:

- `current`: run with the current SimpleAutoResearch Python. This is simple and
  useful for demos, but it is not dependency isolation. Supported now.
- `external`: run with a user-provided Python or Conda interpreter. This should
  be the first practical escape hatch for real projects that already have an
  environment. Supported now.
- `project-venv`: create a per-run environment under
  `code_task/.venv/`. This isolates well but can waste disk space. Planned.
- `shared-env-cache`: create or reuse environments under a cache directory such
  as `.simple_ar_cache/envs/<env-hash>/`, keyed by OS, Python version, and
  dependency files. This is the preferred long-term default. Planned.
- `docker`: run inside a container for stronger isolation. Keep this separate
  from the Python runner because Windows, GPU, and image-build behavior need
  careful handling. Planned.

Future environment creation or dependency installation must be explicit,
auditable, and recorded in artifacts. A safe implementation should record the
selected mode, interpreter path, dependency files, install commands, exit codes,
and warnings in `code_task/meta/environment_report.json` or a dedicated
environment artifact.

## Documentation Rules

Use the docs this way:

- `README.md`: project entry, setup, quickstart, workflow overview, links.
- `docs/USAGE.md`: installation, env configuration, and workflow walkthroughs.
- `docs/CLI_REFERENCE.md`: command groups and option tables.
- `docs/CONFIG_REFERENCE.md`: TOML schema and configuration examples.
- `docs/WORKFLOWS.md`: what each workflow/stage does and what files it produces.
- `docs/DEVELOPMENT.md`: contributor guidance.
- `CHANGELOG.md`: chronological development progress.
- `MDfiles/`: private or learning-heavy planning notes, usually ignored from GitHub.

## Tests

Use layered checks during development:

```bash
uv run simple-ar-checks --list
uv run simple-ar-checks quick
uv run simple-ar-checks code-task
uv run simple-ar-checks pipeline
uv run simple-ar-checks research
uv run simple-ar-checks code-task-examples
```

The same runner can be called without the console script:

```bash
uv run python scripts/run_checks.py code-task
```

Recommended validation layers:

| Change area | Suggested check |
| --- | --- |
| Docs only | `git diff --check` plus manual link review. |
| Small parser, prompt, metric, or CLI changes | `uv run simple-ar-checks quick`. |
| Code-task internals, workspace, repo-map, patching, validation, runner, repair | `uv run simple-ar-checks code-task`. |
| Bundled code-task examples or benchmark examples | `uv run simple-ar-checks code-task-examples`. |
| Pipeline, stages, experiment templates, run config | `uv run simple-ar-checks pipeline`. |
| Literature, retrieval, evidence ledger, report generation, LLM adapter | `uv run simple-ar-checks research`. |
| Before commit/push or broad refactors | `uv run simple-ar-checks all`. |

Run the full test suite directly when needed:

```bash
uv run python -m unittest discover -s tests
```

Run the realistic code-task example:

```bash
uv run python -m unittest tests.test_code_task_examples
```

Run the experiment runner tests:

```bash
uv run python -m unittest tests.test_experiment_runner
```

## Git Hygiene

- Keep unrelated refactors out of feature commits.
- Do not commit `.env`, run outputs, caches, or private learning notes.
- Keep README concise; move detailed behavior into docs.
- Update `CHANGELOG.md` when user-facing commands, artifacts, or workflow behavior changes.
