# Changelog

This file records user-visible project changes in reverse chronological order. Planning notes and design rationale live in `docs/` and `MDfiles/`; this file should stay close to a normal changelog.

## 2026-05-19

### Added

- Added V2.2 code-task workspace modes: `copy` remains the default, and
  `git_worktree` can create a detached worktree at `code_task/workspace` for
  repo-root git projects.
- Added `[workspace]` config support plus CLI flags for standalone and embedded
  code-task runs: workspace mode, source virtualenv reuse, and recorded setup
  hooks.
- Added a structured `workspace` section to code-task `manifest.json` while
  preserving the old `copy` section for compatibility.

### Changed

- Code-task initialization now goes through a workspace dispatcher instead of
  calling the copy routine directly.
- Codebase indexing now skips `.git`, `.env`, virtualenv, and cache metadata so
  worktree mode does not leak git metadata or secret-like files into model
  context.
- Default edit scope now also protects `.env` and secret/credential-looking
  paths from automated patch proposals.

## 2026-05-18

### Added

- Added research-first task generation for embedded `code_task_project` runs:
  when no task file is provided, `05-design` writes `generated_code_task.md`
  from the prior research artifacts and a compact codebase summary, then
  `06-code` uses it as the normal code-task prompt.
- Added `docs/CODE_TASK_WORKSPACE.md` to document the current V2.1
  workspace/copy data flow, hidden assumptions, and V2.2 workspace-mode
  replacement points.

### Changed

- Made `[code_task].task_file` optional for 8-stage embedded code-task runs
  while keeping it required for standalone `simple-ar code-task init`.
- Updated README, usage, workflow, and CLI reference docs to distinguish
  explicit user-authored task files from generated research-first task files.

## 2026-05-17

### Added

- Added a default code-task edit-scope policy that records protected test and
  benchmark path patterns in `manifest.json`.
- Added the generic `code_task_project` experiment template so an 8-stage
  `simple-ar run --to-stage report` can copy a user-provided project, run a
  baseline benchmark, apply an LLM-controlled patch, run the patched benchmark,
  and include nested code-task evidence in the final report.
- Added top-level `simple-ar run --config` / `resume --config` support for
  repeatable TOML-configured research and embedded code-task runs.
- Added `examples/run_configs/tiny_digits_mlp_pipeline.toml` as the canonical
  config-driven end-to-end code-task pipeline example.
- Added top-level pipeline code-task options for `run` and `resume`, including
  `--code-task-config`, `--code-root`, `--task-file`, `--benchmark-command`,
  `--primary-metric`, repeated `--metric-direction`, and environment policy
  overrides.
- Added Phase 5 failure analysis support for validation-only failures, so code-task runs can diagnose syntax/static validation errors before a benchmark has launched.
- Added bounded repair proposal metadata with source analysis paths, selected repair context files, and explicit repair constraints.
- Added `simple-ar code-task execute`, a conservative state-aware orchestrator that runs safe next steps while stopping at plan approval and proposal review gates.
- Added code-task metric comparison configuration with `--primary-metric` and repeated `--metric-direction METRIC=DIRECTION` flags.
- Added `code-task init --config` for TOML-based initialization, including metric direction settings.
- Added a tiny-digits MLP code-task config example under `examples/code_tasks/configs/`.
- Added `docs/CLI_REFERENCE.md` as the dedicated command and option reference.

### Changed

- Generalized the embedded code-task experiment preparation so the old toy-spam
  demo and user-provided projects share the same baseline/plan/proposal/apply/
  validate harness.
- Renamed the embedded code-task bridge module from `code_task_demo.py` to
  `code_task_experiment.py` so the code structure matches its generic role.
- Final reports now append a deterministic Code Task Evidence section for
  embedded code-task templates when the LLM draft does not include one.
- Code-task proposals, repairs, and patch application now treat tests and
  benchmark files as read-only evidence by default. Protected paths are omitted
  from editable snippets, dropped from model proposals, and rejected again by
  `apply-edits` for manual proposal files.
- Comparison artifacts now record configured metric directions and keep unknown metrics as deltas without using them for improved/regressed verdicts.
- Moved code-task init config parsing out of `cli.py` and into `code_task/config.py`.
- Repair context selection now prioritizes files changed by the current patch before traceback/test files, making benchmark-failure repairs less likely to edit tests by accident.
- `code_task/summary.md` now includes a Repair section after a repair proposal is generated.
- Patch application now supports multiple ordered edits in the same file when each old-text block remains uniquely matchable.
- `code-task execute` now reports invalid edit proposals as `patch_apply_failed` instead of surfacing a Python traceback.
- Documented `code-task execute` as a convenience layer over primitive code-task commands, not a replacement for reviewable steps.
- Moved shared metric parsing from `experiment.metrics` to top-level `simple_ar.metrics`, removing an unnecessary code-task dependency on the experiment package.
- Removed unused code-task environment configuration helper and old unlabelled benchmark artifact fallbacks.
- Slimmed `docs/USAGE.md` so detailed command/config tables live in the CLI reference.
- Code-task summaries now start with an outcome and next-step section, and `simple-ar status` shows summary, metric config, and comparison deltas.

## 2026-05-16

### Added

- Added `code-task probe` for V2.1 environment inspection.
- Added `code_task/meta/environment_report.json` with OS, Python, tool, GPU, dependency-file, and test-directory signals.
- Added environment status output to code-task summaries and `simple-ar status`.
- Added `code-task baseline` to capture pre-patch benchmark results under `code_task/run/baseline/`.
- Added code-task `--env-mode current|external` and `--python` support for selecting the interpreter used by benchmark commands.
- Added a lightweight `tiny_digits_mlp_project` code-task example for local ML-style benchmark testing without downloads or GPU requirements.
- Added code-task baseline-vs-patched comparison artifacts with metric deltas and conservative verdicts.

### Changed

- Documented the new code-task environment probe in usage and workflow docs.
- Documented the V2.1 code-task environment isolation direction: current interpreter first, explicit external interpreters next, then per-run venvs, shared environment cache, and Docker later.
- Updated README and development docs to reflect the current V2.1 code-task baseline, comparison, and module structure.
- Code-task benchmark runs now use labelled artifact directories (`baseline` and `patched`) so before/after execution evidence can coexist.
- Benchmark execution reports now record the selected environment mode and Python executable.
- Code-task patch planning now includes recorded environment, validation, and baseline metric context when those artifacts exist.

## 2026-05-14

### Added

- Added README capability boundaries for current research, report, and code-task workflows.
- Added stricter report prompt rules for research-only survey reports and embedded code-task demo reports.
- Added report-bound checks for common toy-demo overclaims such as broad accuracy, effectiveness, feasibility, or generalization language.
- Added a fixture/code-task fallback discussion that treats offline fixture synthesis as traceability context rather than real literature evidence.

### Changed

- Updated `report_quality.json` wording so metric-table checks are clearly conditional on parsed metrics existing.
- Updated docs to explain guarded LLM report drafting and the current boundary between standalone `code-task` and the embedded 8-stage demo.

### Verified

- Ran a real LLM-backed literature-only flow through `synthesize -> report`.
- Ran a real LLM-backed 8-stage `llm_code_task_toy_spam` demo, including patch planning, controlled edit proposal, benchmark execution, and report generation.

## 2026-05-13

### Added

- Added automatic report mode selection: when `results.json` is missing, the report drafts a literature-only narrative; when results exist, it uses experiment sections.
- Added `--report-mode {auto,research_only,experiment}` for `simple-ar run` and `simple-ar resume` to force report structure.
- Added research-only report fallback sections (`Search Scope`, `Thematic Synthesis`, `Approach Patterns`, `Open Questions`, `Limitations`, `Conclusion`) that avoid implying experiment execution.
- Added report mode recording in `08-report/manifest.json` for reproducibility.

### Changed

- Relaxed the report stage contract to no longer require `results.json`, enabling `synthesize -> report` flows.
- Updated report LLM prompt to switch structure and rules between research-only and experiment modes.
- Documented report-mode behavior and synthesize-to-report flow in `docs/USAGE.md` and `docs/WORKFLOWS.md`.

## 2026-05-12

### Added

- Added `08-report/report_quality.json`, a rule-based report quality artifact that checks citation provenance, body-cited papers, metric visibility, and runtime/fallback disclosure.
- Added automatic `code_task/summary.md` generation after code-task benchmark runs and failure analysis.
- Added experimental `llm_code_task_toy_spam` 8-stage experiment template, which embeds the safer code-task patch workflow into the normal plan/search/read/synthesize/design/code/run/report pipeline.

### Changed

- Reworked `README.md` into a cleaner open-source project entry page with setup, environment configuration, quickstart commands, preset workflows, documentation links, reference, and community notes.
- Reduced code-task patch IO by removing full pre/post workspace manifest artifacts; `applied_edits.json` now keeps before/after hashes only for changed files.
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
