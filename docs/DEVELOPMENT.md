# Development Guide

This document is for contributors who want to extend SimpleAutoResearch. For command details, see [CLI Reference](CLI_REFERENCE.md). For setup walkthroughs, see [Usage And Configuration](USAGE.md). For workflow concepts and artifacts, see [Workflows And Artifacts](WORKFLOWS.md).

## Project Shape

SimpleAutoResearch is intentionally file-first:

- stages read and write concrete files;
- workflow state is visible in run directories;
- tests verify artifacts instead of hidden in-memory state;
- risky code changes happen in copied workspaces.

This keeps the project easier to learn, debug, and refactor.

## Adding A Pipeline Stage

To add a stage to the default research pipeline, update these places together:

1. Add the enum value in `src/simple_ar/stages.py`.
2. Add a `StageContract` in `src/simple_ar/contracts.py`.
3. Implement a handler function in `src/simple_ar/stage_handlers.py`.
4. Register the handler in `HANDLERS`.
5. Add a focused test that checks required inputs and declared outputs.

A new stage should read existing artifacts with `ctx.find_artifact(...)` and write its own outputs with `ctx.artifact_path(...)`.

## Adding An Experiment Template

Fixed script templates live in `src/simple_ar/experiment/templates.py`. The
experimental 8-stage code-task demo lives in
`src/simple_ar/experiment/code_task_demo.py` because it prepares an existing
workspace before writing the run harness.

A new template should:

- be added to `SUPPORTED_TEMPLATES`;
- generate a complete standalone `experiment.py`;
- use only dependencies declared in `pyproject.toml`;
- print machine-parseable metric lines like `metric_name: 0.123`, parsed by
  `src/simple_ar/metrics.py`;
- avoid network access and uncontrolled downloads;
- have a test in `tests/test_experiment_runner.py`.

The current template system is deliberately not free-form code generation. That boundary keeps the teaching pipeline reproducible while stronger coding workflows develop under `code-task`.

For embedded code-task templates, keep the automatic approval boundary explicit:
they should copy a workspace, use controlled old/new edits, write a compact
stage artifact such as `code_task_experiment.json`, and run the benchmark
through `07-run` instead of silently mutating source code during reporting.

## Extending Code Task

The code-task workflow is split into small modules:

- `workspace.py`: safe source copy.
- `config.py`: TOML config and CLI override resolution for code-task init.
- `environment.py`: environment observation and execution-interpreter policy.
- `index.py`: codebase inventory and Python AST summaries.
- `planning.py`: patch planning and HITL decisions.
- `patching.py`: controlled old/new edit proposal and application.
- `validation.py`: syntax and static safety checks.
- `runner.py`: benchmark execution in the copied workspace.
- `comparison.py`: baseline-vs-patched metric comparison.
- `failure.py`: deterministic failure analysis.
- `repair.py`: bounded repair proposal generation.
- `summary.py`: human-readable code-task status summaries.
- `state.py`: shared paths, manifest helpers, and workspace path safety.

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

The current V2.1 code-task runner has workspace isolation, command timeouts,
captured stdout/stderr, a restricted environment map, and an explicit execution
interpreter policy. It supports `current` and `external` modes, but it does not
yet create or install into a separate Python environment. Unless a future
feature explicitly changes this, do not install user project dependencies into
SimpleAutoResearch's own `.venv` by default.

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
- `docs/CLI_REFERENCE.md`: command groups, option tables, and config schema.
- `docs/WORKFLOWS.md`: what each workflow/stage does and what files it produces.
- `docs/DEVELOPMENT.md`: contributor guidance.
- `CHANGELOG.md`: chronological development progress.
- `MDfiles/`: private or learning-heavy planning notes, usually ignored from GitHub.

## Tests

Run the full test suite:

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
