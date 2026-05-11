# Changelog

This file records user-visible project changes in reverse chronological order. Planning notes and design rationale live in `docs/` and `MDfiles/`; this file should stay close to a normal changelog.

## 2026-05-12

### Changed

- Reworked `README.md` into a cleaner open-source project entry page with setup, environment configuration, quickstart commands, preset workflows, documentation links, reference, and community notes.
- Consolidated detailed documentation into three primary docs:
  - `docs/USAGE.md` for installation, environment variables, CLI commands, examples, and future config shape.
  - `docs/WORKFLOWS.md` for preset workflows, the default 8-stage pipeline, stage outputs, and artifact layout.
  - `docs/DEVELOPMENT.md` for contributor guidance, stage extension, experiment templates, code-task modules, and testing.
- Removed overlapping docs that made the documentation tree harder to navigate:
  - `docs/CODE_TASK.md`
  - `docs/RUN_ARTIFACTS.md`
  - `docs/CLI_AND_CONFIG.md`
  - `docs/EXTENDING.md`

### Notes

- `CHANGELOG.md` is now kept as a chronological development log rather than a version-planning document.
- Planning-heavy notes remain in `MDfiles/` and are separate from public-facing docs.

## 2026-05-11

### Added

- Added `code-task validate` for lightweight Python syntax checks, risky import/call warnings, missing import warnings, and strict-mode execution hazard errors.
- Added `code-task run` for executing the recorded benchmark command inside the copied workspace with timeout, captured stdout/stderr, return code, and parsed metrics.
- Added `code-task analyze-failure` for turning the latest failed benchmark run into a compact Markdown diagnosis.
- Added `code-task repair` for generating a bounded repair edit proposal from failure analysis without applying it automatically.
- Added `src/simple_ar/code_task/state.py` to centralize code-task path, manifest, and safe workspace path helpers.
- Added a realistic code-task smoke example under `examples/code_tasks/toy_spam_project` with package layout, tests, and a task file.
- Added `tests/test_code_task_examples.py` to verify that the example benchmark fails first, passes after a workspace patch, and does not mutate the original source project.
- Added initial detailed docs for code-task usage, artifact layout, CLI/config direction, workflow composition, and extension guidance.

### Changed

- Moved long command and artifact explanations out of `README.md` into dedicated docs.
- Kept `README.md` focused on project overview, setup, quickstart, and documentation entry points.
- Grouped code-task execution artifacts under `code_task/run` and repair attempts under `code_task/repairs`.
- Documented the future CLI/config direction: keep primitive commands for learning, then add config-driven convenience workflows after the underlying steps stabilize.

### Verified

- `uv run python -m unittest tests.test_code_task_examples`
- `uv run python -m unittest discover -s tests`

## 2026-05-10

### Added

- Added controlled `code-task propose-edits` for model-generated JSON old/new replacements.
- Added controlled `code-task apply-edits` for safely applying approved replacements inside `code_task/workspace`.
- Added patch artifacts:
  - `code_task/patch.diff`
  - `code_task/meta/proposed_edits.json`
  - `code_task/meta/applied_edits.json`
  - `code_task/meta/pre_patch_manifest.json`
  - `code_task/meta/post_patch_manifest.json`

### Changed

- Updated code-task status output to include patch state and changed files.
- Updated README code-task quick usage before the detailed docs were split out.

### Verified

- `uv run python -m unittest discover -s tests`

## 2026-05-09

### Added

- Added initial `code-task init` workflow for copying an existing codebase into an isolated run workspace.
- Added Python-aware `codebase_index.json` with file hashes, role tags, imports, functions, classes, tests, and entrypoint candidates.
- Added `code-task plan` for generating a human-reviewable patch plan from the task, codebase index, and selected snippets.
- Added `code-task decide-plan` for recording human approval, rejection, or revision requests.

### Changed

- Kept code-task artifacts under `code_task/workspace` and `code_task/meta` instead of adding more files to the run root.

## 2026-05-08

### Added

- Added local artifact inspection with `simple-ar inspect`.
- Added local lexical artifact search with `simple-ar search-artifacts`.
- Added source-only chunking by default, with `--include-operational` for debugging runner metadata.
- Added evidence-aware run artifacts:
  - `source_plan.json`
  - `activity_log.jsonl`
  - `evidence_ledger.jsonl`
- Wired retrieval snippets into `read`, `synthesize`, and `report` stages.
- Added configurable retrieval controls:
  - `--retrieval-top-k`
  - `--no-retrieval`

### Changed

- Improved report citation checks so body citations stay aligned with generated `references.bib`.
- Made fixture fallback explicit with `--allow-fixture-fallback`.
- Updated live literature search order to try OpenAlex before arXiv, then provider-specific cache.

## 2026-05-06

### Added

- Published the V1 teaching pipeline baseline:
  - `01 plan`
  - `02 search`
  - `03 read`
  - `04 synthesize`
  - `05 design`
  - `06 code`
  - `07 run`
  - `08 report`
- Added file-based stage contracts and resumable runs.
- Added OpenAI-compatible LLM calls with visible progress and usage logging.
- Added `.env` configuration for API key, base URL, model name, and optional cost estimates.
- Added OpenAlex/arXiv-backed paper metadata with local cache support.
- Added offline fixture mode for deterministic tests and demos.
- Added template-based experiment generation through `toy_text_classification`.
- Added subprocess experiment execution with timeout, stdout, stderr, return code, and parsed metrics.
- Added deterministic BibTeX generation from known paper metadata.

### Verified

- Unit tests for contracts, pipeline behavior, literature parsing, LLM adapters, report packaging, and experiment execution.
