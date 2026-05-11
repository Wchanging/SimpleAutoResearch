# Changelog

This file tracks SimpleAutoResearch's learning and development iterations. The README is intentionally kept focused on project overview, setup, configuration, and current usage.

## Unreleased - V2 Retrieval And Code-Task Branch

Branch: `feat/v2-retrieval-codegen`

Status: active development.

### Direction

V2 is not only an incremental feature pass over V1. It is a chance to keep the architecture understandable while adding more realistic research-assistant behavior.

The main design shift is workflow decoupling:

- `research-run`: the current staged topic-to-report workflow, upgraded with better retrieval, evidence tracking, and report quality checks.
- `code-task`: a focused workflow for analyzing and improving an existing codebase, benchmark, or experiment script. It should copy the target into an isolated run workspace, build a lightweight code index, ask the model for an edit plan, support human approval before risky changes, run validation commands, and preserve failure evidence.
- `review` or `survey`: a no-code workflow for literature review and report drafting, where search, reading, synthesis, and reporting can run without experiment design, code generation, or benchmark execution.

V2 should stay local-first and inspectable. Local retrieval starts with metadata, notes, snippets, and compact indexes rather than blindly storing every full paper. Code execution should prefer isolated run directories, explicit commands, timeouts, and human approval points before moving toward stronger sandboxing.

### Added

- Local artifact inspection with `simple-ar inspect`.
- Local lexical artifact search with `simple-ar search-artifacts`.
- Source-only chunking by default, with `--include-operational` for debugging runner metadata.
- Evidence-aware run artifacts: `source_plan.json`, `activity_log.jsonl`, and `evidence_ledger.jsonl`.
- Retrieval wiring for `read`, `synthesize`, and `report` stages through compact source-labelled snippets.
- Configurable retrieval controls on `run`: `--retrieval-top-k` and `--no-retrieval`.
- More explicit fixture handling with `--allow-fixture-fallback`.
- Live literature search order that tries OpenAlex before arXiv, then falls back to provider-specific cache entries.
- Report citation checks that keep body citations aligned with the generated `references.bib`.
- Initial `code-task init` workflow for copying an existing codebase into an isolated run workspace.
- Python-aware `codebase_index.json` with file hashes, role tags, imports, functions, classes, tests, and entrypoint candidates.
- Tidy code-task artifact layout under `code_task/workspace` and `code_task/meta` instead of adding many files to the run root.
- `code-task plan` for generating a human-reviewable `patch_plan.md` from the task, codebase index, and selected source snippets.
- `code-task decide-plan` for recording human approval, rejection, or revision requests in `code_task/meta/hitl_decisions.jsonl`.
- `code-task propose-edits` for asking the model to produce controlled JSON old/new text replacements after planning.
- `code-task apply-edits` for safely applying controlled replacements inside `code_task/workspace` after approval.
- Patch application artifacts: `patch.diff`, `applied_edits.json`, `pre_patch_manifest.json`, and `post_patch_manifest.json`.

### Commands

```bash
uv run simple-ar inspect runs/<run-id>
uv run simple-ar search-artifacts runs/<run-id> "accuracy"
uv run simple-ar search-artifacts runs/<run-id> "timeout" --include-operational
uv run simple-ar run --topic "toy topic" --to-stage report --retrieval-top-k 4
uv run simple-ar run --topic "toy topic" --to-stage report --no-retrieval
uv run simple-ar run --topic "agent simulation" --to-stage report --allow-fixture-fallback
uv run simple-ar code-task init --code-root path/to/project --task-file path/to/task.md
uv run simple-ar code-task plan runs/<run-id>
uv run simple-ar code-task plan runs/<run-id> --no-llm
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve --note "reviewed"
uv run simple-ar code-task propose-edits runs/<run-id>
uv run simple-ar code-task apply-edits runs/<run-id>
```

### Planned Next

- Add a code validator for syntax, risky imports/calls, and obvious execution hazards.
- Add an isolated benchmark runner for the copied workspace.
- Preserve validation evidence, including commands, stdout, stderr, return codes, and changed files.
- Add minimal failure analysis and one bounded repair attempt.
- Improve report quality checks without turning the project into a rigid paper generator.

## V1 - Runnable Teaching Pipeline

Branch: `main`

Status: published baseline.

V1 established the compact topic-to-report workflow:

```text
01 plan        Scope the topic and research question
02 search      Collect paper metadata
03 read        Create literature notes from paper metadata
04 synthesize  Summarize themes and propose a testable hypothesis
05 design      Create a small experiment plan
06 code        Generate experiment code from templates
07 run         Execute the experiment and parse metrics
08 report      Write a final Markdown report with references
```

### Included

- File-based stage contracts and resumable runs.
- OpenAI-compatible LLM calls with visible progress and usage logging.
- `.env` configuration for API key, base URL, model name, and optional cost estimates.
- OpenAlex/arXiv-backed paper metadata with local cache support.
- Offline fixture mode for deterministic tests and demos.
- Template-based experiment generation through `toy_text_classification`.
- Subprocess experiment execution with timeout, stdout, stderr, return code, and parsed metrics.
- Deterministic BibTeX generation from known paper metadata.
- Unit tests for contracts, pipeline behavior, literature parsing, LLM adapters, report packaging, and experiment execution.
