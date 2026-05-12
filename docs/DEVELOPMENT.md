# Development Guide

This document is for contributors who want to extend SimpleAutoResearch. For usage commands, see [Usage And Configuration](USAGE.md). For workflow concepts and artifacts, see [Workflows And Artifacts](WORKFLOWS.md).

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

Experiment templates live in `src/simple_ar/experiment/templates.py`.

A new template should:

- be added to `SUPPORTED_TEMPLATES`;
- generate a complete standalone `experiment.py`;
- use only dependencies declared in `pyproject.toml`;
- print machine-parseable metric lines like `metric_name: 0.123`;
- avoid network access and uncontrolled downloads;
- have a test in `tests/test_experiment_runner.py`.

The current template system is deliberately not free-form code generation. That boundary keeps the teaching pipeline reproducible while stronger coding workflows develop under `code-task`.

## Extending Code Task

The code-task workflow is split into small modules:

- `workspace.py`: safe source copy.
- `index.py`: codebase inventory and Python AST summaries.
- `planning.py`: patch planning and HITL decisions.
- `patching.py`: controlled old/new edit proposal and application.
- `validation.py`: syntax and static safety checks.
- `runner.py`: benchmark execution in the copied workspace.
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

## Documentation Rules

Use the docs this way:

- `README.md`: project entry, setup, quickstart, workflow overview, links.
- `docs/USAGE.md`: commands, env configuration, examples, future config shape.
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
