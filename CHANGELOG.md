# Changelog

[中文版本](CHANGELOG_zh.md)

This file records user-visible project changes in reverse chronological order. Planning notes and design rationale live in `docs/` and `MDfiles/`; this file should stay close to a normal changelog.

## 2026-06-05

### Added

- Added the V2.4 report foundation under `src/simple_ar/report/`: Markdown
  report templates, reviewer criteria files, compact report memory, read-only
  source-backtracking tools, and local report audit artifacts.
- `08-report` now writes `report_memory.json` and `report_audit.json` in
  addition to `report.md`, `references.bib`, `manifest.json`, and
  `report_quality.json`.
- Pipeline config now supports report template/reviewer settings, source
  backtracking budgets, and `[report.audit]` switches.
- Added a bounded report Writer/Reviewer loop. In LLM mode, `08-report` now
  drafts template sections, reviews each section against criteria, performs
  limited revisions, and writes reviewer findings into `report_audit.json`.
- Added report rerun output policies: `overwrite`, `archive`, and `variant`.
  `variant` writes an extra report package under `08-report/variants/<label>/`
  without replacing the current main `report.md`.

### Changed

- `code-task execute` now runs continuously to real review gates by default,
  with Rich step/status output. The `--interactive` flag is reserved for
  primitive-step debugging, and `--yes` only auto-continues those primitive
  prompts.
- LLM work-plan and patch-plan failures now stop as `llm_planning_failed`
  after configured retries instead of silently writing offline fallback plans.
  Use `--allow-planning-fallback` only when a deterministic fallback is
  acceptable.
- `code-task init` now uses the same Rich-facing output style as execute.
- `simple-ar clean --shared-cache` now clears both the shared research index
  and the shared literature provider cache, with an explicit strong-cleanup
  warning.
- The medium review pipeline example now allows `configs/experiment.json` in
  edit scope so phrase-feature implementations can enable the new feature
  family and be measured by the benchmark.
- Embedded code-task experiments now label forwarded logs as patched benchmark
  stdout/stderr to avoid confusion with standalone baseline/patched artifacts.
- Report generation now uses short model-facing citation keys such as `P1` and
  maps them back to verified provider ids before citation audit, reducing
  failures from long OpenAlex/Semantic Scholar id copying.
- Report section planning now uses a configurable `max_section_sources` budget
  instead of hard-coding four source handles per section.
- `max_section_sources = 0` now exposes all selected paper-level handles to
  each report section while leaving full-text chunks for bounded backtracking,
  which is useful for large-context survey runs.
- Survey report drafting now separates drafting order from final section order:
  evidence-heavy body sections are drafted before Introduction/Abstract, while
  the final Markdown still follows the template layout.
- Report drafting now supports an optional `batch_refine` source strategy for
  incrementally integrating larger paper sets into each section.
- `batch_refine` can optionally run reviewer checks after each source batch via
  `review_source_batches = true`.

### Internal

- The public CLI and pipeline stage entry points were split into smaller
  modules under `src/simple_ar/cli/` and `src/simple_ar/pipeline_stages/`.
  The old large modules remain only as private compatibility shims.

## 2026-06-03

### Changed

- Real online full-text checks now recognize common scholarly download URLs
  such as `.../article/download/...` as PDF candidates even when the URL does
  not end with `.pdf`.
- Compact `search_meta.json` now retains a small `source_plan` copy so
  downstream read/synthesize/design stages still know the active sources,
  full-text intent, index backend, and budgets after verbose planning traces are
  removed.
- Retrieval now collects a bounded overfetch set before final selection. This
  helps fill the document budget when one provider returns weak candidates that
  are later dropped by retrieval/read screening.
- Synthesis limitations now distinguish global full-text success from
  shortlisted-paper evidence gaps, so a parsed PDF for a dropped paper does not
  make the retained brief look stronger than it is.
- Compact research runs now use a slimmer canonical handoff chain:
  `papers.jsonl` -> `paper_notes.json` Paper Briefs ->
  `synthesis_brief.json` -> `experiment_contract.json`. The older
  `03-read/cards/*` and `04-synthesize/evidence/*` diagnostics are retained
  only when `[run].debug_artifacts = true`.
- Decoupled research-stage artifact ownership. `02-search` now stops at
  retrieval/document/full-text/index artifacts, `03-read` owns paper/claim/
  method/dataset/code-link cards plus reading review/shortlist artifacts,
  `04-synthesize` owns evidence packs, gaps, ideas, and novelty hints, and
  `05-design` owns experiment contracts and
  optional tool handoff drafts.
- Renamed search-stage metadata screening traces to
  `02-search/traces/retrieval_selection.jsonl`; semantic keep/drop/priority
  decisions now live under `03-read/review/`.
- Read-stage LLM screening can now drop or reprioritize retrieved papers, and
  `paper_notes.json` plus read cards are generated from the resulting shortlist
  instead of blindly reading every retrieved row.
- Read-stage LLM review now uses a scalable two-step path: concurrent coarse
  title/abstract screening batches followed by a focused rerank pass that
  records reading priority, evidence role, and synthesis hints for shortlisted
  papers.
- Pipeline stage descriptions are now shown in Rich progress output so users can
  see the active stage purpose while a run is executing.
- Artifact retrieval now ignores structured intermediate research artifacts such
  as cards, evidence packs, idea candidates, experiment contracts, and tool
  handoff drafts, preventing generated evidence tables from being treated as new
  source material.

## 2026-06-02

### Added

- Added V2.3 Week 3 research-bridge artifacts. Compact runs retain read cards
  under `03-read/cards/`, synthesis evidence under `04-synthesize/evidence/`,
  and design experiment contracts under `05-design/evidence/`; debug runs can
  also retain design handoff drafts such as `tool_context.json/md`,
  `evidence_review.md`, `decision_log.jsonl`, and `eval_report.json/md`.
- Added `[research.budget].novelty_backend`, currently supporting `local`
  lexical novelty-risk hints over the current evidence pack.
- Added V2.3 Day 12 section-aware document extraction. Compact runs use section
  spans for chunk/card construction and debug runs can retain
  `02-search/documents/sections.jsonl` for inspection.
- Added section metadata to research chunks when section records are available,
  so `02-search/research_index/chunks.jsonl` can preserve `section`, `heading`,
  and `section_id` provenance.
- Added V2.3 Day 13 extended evidence cards:
  `method_cards.jsonl`, `dataset_cards.jsonl`, and `code_links.jsonl` under
  `03-read/cards/`.
- Added a compact structured evidence summary for report drafting. Both LLM
  and fallback reports can now use read-stage paper cards, claim cards,
  section records, and extended cards as bounded evidence.
- Added configurable code-task `[edit_scope]` allowlists and additional
  protected patterns. The scope is stored in `code_task/manifest.json` and is
  enforced by repo mapping, context selection, work-plan creation, edit
  proposal, repair, and apply-time patch validation.
- Added debug-only read-only Tool/MCP handoff artifacts under `05-design/tools/`:
  `tool_adapter_contract.json/md`, `tool_trace.jsonl`, and
  `external_agent_backend.md`.
- Added debug-only `05-design/governance/artifact_retention_policy.json/md` to
  classify search artifacts as stable run outputs, evidence tables, cache
  artifacts, traces, debug diagnostics, or rebuildable files.
- Added `simple-ar clean RUN_DIR`, a Rich preview-and-confirm cleanup command
  for rebuildable run caches and this run's shared SQLite research-index rows.

### Changed

- Paper and claim cards now prefer section-aware method, experiment, result,
  and limitation chunks when available instead of treating all text as one flat
  abstract-like source.
- Usage docs now show the updated compact `02-search/` artifact tree and
  separate default evidence artifacts from debug-only diagnostics and tool
  handoff drafts.
- Code-task examples now declare explicit edit scopes so implementation files
  are editable while tests, benchmarks, and locked config stay read-only
  evidence.
- LLM research planning now still runs when query expansion is disabled. The
  query count remains bounded by `[research].max_queries`, but
  `[research].planner = "llm"` no longer silently falls back to deterministic
  planning.
- Retrieval selection now preserves required-facet diversity before filling the
  remaining document budget by rank, reducing cases where one query family
  crowds out overview/benchmark/dataset evidence.
- V2.3 online check configs now default to compact artifacts and avoid mixing
  local demo notes into the online evidence check. Set `[run].debug_artifacts =
  true` when planning/traces/coverage/tool drafts are needed.
- Evidence packs now store artifact references and card ids instead of
  duplicating `cards/*.jsonl`, reducing synthesis artifact sprawl.
- V2.3 release hardening covered layered checks, bundled code-task examples,
  a compact search CLI run, a medium code-task baseline CLI run, and full
  unittest discovery.
- Pipeline progress output now uses restrained Rich panels, stage rules, and
  colored status/message categories so users can see the active stage and key
  events more clearly without changing pipeline behavior.

## 2026-05-31

### Added

- Added direct Pydantic, Rich, LiteLLM, and pyalex dependencies as the
  first infrastructure replacement batch.
- Added a state-backed reboot core for pipeline execution. Runs now write a
  top-level `state.json`, and completed stages can emit compact `contract.json`
  / `report.md` summaries for machine-readable handoff and human review.
- Added optional `unstructured` full-text parser backend support via
  `[research].parser_backend = "unstructured"`. When the optional package is
  not installed, the parser records a manifest failure instead of failing the
  search stage.
- Added optional LanceDB research-index backend status via
  `[research].index_backend = "lancedb"` or `"hybrid_lancedb"`, while keeping
  `chunks.jsonl` as the portable source of truth.

### Changed

- Top-level pipeline TOML loading now uses a Pydantic schema before flattening
  into the existing runtime config dictionary, so malformed config types fail
  earlier and with clearer errors.
- Code-task TOML loading now also uses Pydantic section schemas for init and
  execute options, replacing the previous free-form dict parsing path.
- The LLM client now calls providers through LiteLLM instead of directly
  constructing an OpenAI SDK client. OpenAI-compatible `OPENAI_BASE_URL`
  endpoints are still supported through LiteLLM's OpenAI provider path.
- OpenAlex search now uses pyalex instead of a hand-written urllib request
  client while preserving the project `Paper` normalization layer.
- Pipeline progress and developer-check output now route through a small Rich
  console wrapper, keeping the CLI compatible while preparing for cleaner
  review output.
- Research SQLite FTS / LanceDB accelerators now use a shared store under
  `.simple_ar_cache/research_index` by default. Run directories keep portable
  `chunks.jsonl` plus `index_meta.json`, while reusable index databases are
  keyed by `run_id` instead of duplicated per run.
- Pipeline stage dependencies now prefer explicit `WorkspaceState` pointers
  over reverse-scanning run folders with `find_artifact`. The old lookup helper
  remains only for legacy compatibility.
- Default pipeline runs now compact only diagnostic `02-search` folders after
  the stage contract is written. Search-owned `documents/` and
  `research_index/` stay in the run directory for downstream grounding; later
  stages retain their own cards/evidence/contracts. Set
  `[run].debug_artifacts = true` to also keep planning, trace, screening, and
  coverage-review diagnostics.
- Compact search runs now rewrite `search_meta.json` so it no longer points to
  diagnostic artifacts removed from the run directory.
- The previous monolithic `stage_handlers.py` and `cli.py` entrypoints were
  moved under private `src/simple_ar/_legacy/`, with small compatibility wrappers kept at
  the public import paths. This makes the active project shape easier to evolve
  without breaking existing tests and commands.
- Experiment runner/template helpers now live directly under
  `src/simple_ar/experiment/`; the redundant `src/simple_ar/coding/` package was
  removed so template experiments and code-task automation no longer compete for
  the same "coding" name.
- Research modules are now grouped by lifecycle under `planning/`, `sources/`,
  `documents/`, `store/`, `evidence/`, and `outputs/` instead of being a flat
  folder.
- Code-task modules are now grouped by lifecycle under `runtime/`, `workspace/`,
  `analysis/`, `editing/`, `execution/`, and `orchestration/`, reducing the
  previous flat 25-file package surface.
- Top-level implementation files were collapsed into explicit domain packages:
  `core/` for pipeline primitives, `app/` for config/state/usage/dev checks,
  `integrations/` for LLM providers, `experiment/` for template experiments and
  metrics, and `report/` for report audit helpers. The previous broad facade
  files such as `simple_ar.pipeline`, `simple_ar.artifacts`, and
  `simple_ar.prompts` were removed.
- Config, Usage, Workflow, and README docs now describe `unstructured` and
  LanceDB as optional backends rather than mandatory base dependencies.

## 2026-05-27

### Added

- Added V2.3 Day 10 failure-safe full-text caching: selected local full-text
  resources are marked as cached, guarded remote fetch failures are recorded in
  `fulltext_manifest.json`, and search continues on metadata/abstract evidence.
- Added V2.3 Day 11 full-text extraction:
  `02-search/documents/fulltext_extraction.json` now records parser outcomes
  for cached/local full-text resources, and parsed text is fed into
  `research_index/chunks.jsonl` before the read stage builds evidence cards.

### Changed

- The search-stage contract now declares `documents/fulltext_extraction.json`,
  so completed stage output and `search_meta.json` expose the extraction
  artifact.
- The local research example now enables `use_fulltext = true` by default to
  make the local Markdown/text parser -> chunk -> card path easier to inspect.
- Public README, Usage, Configuration Reference, and workflow docs now describe
  the current boundary: Markdown/text and basic HTML are parseable, PDF parsing
  is best-effort, and vector retrieval is not active yet.

## 2026-05-26

### Added

- Added V2.3 Day 3 research-question and query-plan sections under
  `02-search/planning/research_plan.json`.
- Added optional LLM-backed research planning via `[research].planner`, with
  deterministic planning kept as a fallback for offline or provider-failure
  cases.
- Added structured `query_specs` in the research plan so LLM research planning
  can preserve title/abstract keyword intent instead of only emitting browser-
  style query strings.
- Added V2.3 Day 4 retrieval trace artifacts:
  `02-search/traces/retrieval_rounds.jsonl` and
  `02-search/traces/retrieval_selection.jsonl`, including executed source/query
  attempts, compact query-intent traces, deduplication, and lightweight
  retrieval-selection decisions before `papers.jsonl` is written.
- Added V2.3 Day 5 coverage artifacts:
  `02-search/review/coverage_report.json` and `02-search/review/coverage_report.md`,
  including required-facet coverage, missing facets, question coverage, and
  budgeted follow-up query recommendations.
- Added V2.3 Day 6 document-store artifacts:
  `02-search/documents/documents.jsonl` and
  `02-search/documents/cache_manifest.json`, recording selected metadata,
  configured local files, extraction status, source counts, and cache/full-text
  intent without downloading restricted full text.
- Added V2.3 Day 7 local research-index artifacts:
  `02-search/research_index/chunks.jsonl` and
  `02-search/research_index/index_meta.json`, with optional SQLite FTS creation
  for `sqlite_fts`/`hybrid` modes.
- Added V2.3 Day 8 deterministic evidence-card artifacts:
  `03-read/cards/paper_cards.jsonl` and
  `03-read/cards/claim_cards.jsonl`, grounded in document chunks and marked
  with evidence refs for later audit.
- Added V2.3 Day 9 full-text planning artifacts:
  `02-search/documents/fulltext_manifest.json`, recording arXiv/OpenAlex/local
  full-text hints, fetch-budget decisions, and blocked/skipped reasons. Remote
  PDF download remains off by default.
- Added a Semantic Scholar live metadata connector between OpenAlex and arXiv,
  giving V2.3 research search a broader default source order without relying on
  fixture metadata.
- Added a local research-source example at
  `examples/run_configs/local_research_report.toml` with supporting notes under
  `examples/research/`.

### Changed

- Search execution now runs through research connector wrappers for OpenAlex,
  Semantic Scholar, arXiv, and local Markdown/text files while preserving
  existing fixture and cache fallback behavior.
- The search stage now expands configured seed queries into bounded
  facet-driven follow-up queries before building `research_plan.json`; when LLM
  mode is enabled, `planner = "auto"` can use the model for stronger question
  decomposition and query terminology.
- When coverage gaps remain and retrieval-round budget is available, search can
  run a second ordered-fallback retrieval round and then reselect retrieval
  candidates within the document budget.
- Search-stage planning, trace, and review artifacts are now grouped under
  `02-search/planning/`, `02-search/traces/`, and `02-search/review/` so larger
  research runs do not leave every intermediate file at the stage root.
- Research planning is consolidated into one `planning/research_plan.json`
  instead of three small JSON files, keeping the stage inspectable without
  increasing artifact sprawl.
- Local Markdown/text search now uses lightweight keyword-overlap matching
  rather than exact query-string matching, which makes normalized paper-search
  queries work better with short local notes.
- Local Markdown/text documents now produce parsed document records with content
  hashes; PDF inputs are recorded as skipped or failed unless optional parsing
  is explicitly available and full-text intent is enabled.
- Abstracts and parsed local files are now chunked into a portable local index
  layer, giving later evidence cards and RAG-style retrieval a stable input
  without requiring embeddings yet.
- OpenAlex metadata now preserves open-access URL hints when available, and
  arXiv records can derive provider PDF hints for later controlled full-text
  fetch/parse stages.
- Read-stage evidence artifacts now provide paper/claim card counts in the
  `03-read` stage contract, making the research evidence layer easier to
  inspect without overloading `search_meta.json`.
- Split public command and configuration documentation: `CLI_REFERENCE.md` now
  focuses on command syntax/options/artifacts, while the new
  `CONFIG_REFERENCE.md` centralizes TOML schema, complete configs, and
  workspace-mode variants.
- Expanded configuration docs with inline comments and key-field notes so less
  obvious settings such as `max_papers`, research budgets, workspace modes, and
  execute budgets are easier to understand.

## 2026-05-24

### Added

- Added the V2.3 research source-planning foundation. `02-search` now writes
  `planning/research_plan.json` with research questions, planned queries,
  source order, research mode, local-document hints, cache/index preferences,
  and lightweight budgets.
- Added `[research]` support in top-level run configs, including `sources`,
  `queries`, `local_documents`, `cache`, `index_backend`, and
  `[research.budget]` fields.

### Changed

- Search execution started routing through research connector wrappers for
  OpenAlex, arXiv, and local Markdown/text files while preserving existing
  fixture and cache fallback behavior.
- Public README, Usage, CLI Reference, and Workflows docs started documenting
  `02-search/planning/research_plan.json`, `[research]` config, and local-file
  source settings.

## 2026-05-23

### Changed

- Embedded 8-stage `code_task_project` runs now use the V2.2 code-task
  execution shape during `06-code`: repo map/context pack, LLM work plan,
  attempt/batch state, patch plan, controlled edit proposal, apply, and
  validation. The final report evidence now points to work-plan and batch
  artifacts in addition to summary, diff, and comparison outputs.
- Added the medium review pipeline code-task example with a `main.py` entrypoint,
  JSON config, multi-module feature/model/metric structure, visible progress
  output, and a TOML task config that enables streamed benchmark output.
- `code-task execute --config` can now relay benchmark stdout/stderr during
  baseline and patched runs via `[execute].stream_benchmark_output = true`.
- Benchmark streaming now supports string modes including `"auto"`, `"line"`,
  and `"summary"`; `"auto"` is compatible with normal line logs and
  carriage-return progress output from tools such as `tqdm`.
- Documentation now describes the embedded research-to-code path as a
  work-plan/batch-based flow instead of the older direct patch-plan flow.
- Work-plan batch creation now merges small serial dependency chains, such as
  feature producer -> model consumer -> config switch, into one bounded
  execution batch. The reviewed items remain visible, while
  `batch_state.json.work_item.source_work_item_ids` records the merged scope and
  the larger batch still requires explicit large-edit review when applicable.
- Applying a reviewed large proposal now records apply-time approval in
  `applied_edits.json` and `manifest.json.patch.budget`, and executor benchmark
  runs avoid duplicate validation history entries.
- Public docs now keep README as a concise project entry point, move the full
  code-task executor sequence into Usage, replace long PowerShell run-directory
  selectors with `runs/<run-id>` placeholders, and surface `copy`,
  `git_worktree`, and `sparse_copy` as first-class workspace strategies.

## 2026-05-22

### Changed

- Code-task patched runs now record a separate `objective.status` from
  baseline-vs-patched comparison, so a passing benchmark can still be reported
  as `regressed`, `mixed`, or `inconclusive` when the measured goal was not met.
- `code-task execute` now prefers the first executable implementation work item
  instead of blindly batching an inspection-only first work-plan item.
- Manual `code-task validate` and `code-task run` now synchronize latest
  batch/attempt/work-plan state after a patch has been applied, matching the
  executor path more closely.
- Applying repair proposals now records the actual repair proposal path as the
  latest applied proposal, and later passing patched benchmarks resolve stale
  failure/repair sections in status and summaries.
- Failure analysis now captures metric-floor and timing-budget signals such as
  `accuracy below benchmark floor`, `macro_f1`, and `train_time_sec`.
- Documentation now explains objective verdicts, implementation-batch selection,
  repair application state, and how to handle benchmark pass with metric
  regression.
- `README.md` and `docs/USAGE.md` now present the TOML + `code-task execute`
  route as the primary code-task workflow, with primitive commands moved later
  as advanced debugging steps.
- Added the V2.2 editor backend interface and migrated the default controlled
  old/new patch path behind the `controlled_patch` backend while preserving the
  existing CLI/API surface. Proposal, batch, apply, manifest, and status
  artifacts now expose backend metadata.
- Added the reserved `external_agent` editor boundary for future
  Codex/Claude/OpenCode adapters. It now has provider normalization,
  conservative permissions, blocked secret/home read patterns, and a reviewable
  invocation-plan artifact, but it remains non-executable by default.

## 2026-05-21

### Added

- Added Day 17-20 V2.2 batch-level edit budget enforcement for code-task
  proposals, including normal/large/absolute profiles and explicit
  `--allow-large-edits` review gates.
- Added per-batch artifacts under
  `code_task/attempts/attempt-NNN/batches/batch-NNN/`, including batch context,
  proposal warnings, usage summaries, validation links, benchmark links, and
  repair proposal links.
- Added `code-task execute --config` support for `[execute]`,
  `[models.code_task]`, and `[budget]` settings, so model routing and budget
  caps can live in TOML instead of long CLI commands.

### Changed

- `code-task execute` now routes work-plan/patch-plan, edit proposal, and repair
  steps through planner/editor/repair model slots when configured.
- Active work-item batches now constrain LLM edit proposals to the batch target
  files; protected tests and benchmark files still remain read-only evidence.
- Validation, benchmark, and repair steps now update the active batch state so
  interrupted or failed attempts are easier to inspect and resume manually.
- Large or over-budget model outputs are normalized into warnings/rejected edits
  instead of being applied implicitly.
- Documentation now shows the correct reviewed executor sequence with
  `execute --to-step propose-edits`, and adds troubleshooting notes for missing
  proposals, benchmark regressions, repair proposals, exact-text patch failures,
  large-edit approval, and local `uv` cache permission issues.
- Repair and edit proposal handling now rejects accidental unified-diff
  fragments inside structured `old`/`new` JSON fields, and `apply-edits` reports
  patch validation failures without a Python traceback.

## 2026-05-20

### Added

- Added Day 8 V2.2 layered repo-map artifacts for code-task runs:
  `code_task/meta/repo_map.json` and `code_task/meta/repo_map_summary.md`.
- Added project, directory, file, symbol, entrypoint, test, benchmark, config,
  and prompt-budget layers to the repo-map schema while preserving
  `codebase_index.json` for compatibility.
- Added `simple-ar code-task map` to rebuild repo-map artifacts from the
  current workspace as a standalone step.
- Added `simple-ar code-task locate` to rank likely editable targets and
  protected read-only evidence from the repo map.
- Added `simple-ar code-task context` to build bounded prompt context packs
  under `code_task/context_packs/context-NNN/`.
- Added Day 15-16 V2.2 work-plan artifacts:
  `code_task/work_plan.json` and `code_task/work_plan.md`, plus
  `simple-ar code-task work-plan`.
- Added initial attempt/batch state directories under
  `code_task/attempts/attempt-NNN/batches/batch-NNN/`, plus
  `simple-ar code-task batch --work-item W1`.
- Added `simple-ar-checks` and `scripts/run_checks.py` for layered developer
  validation groups such as `quick`, `code-task`, `pipeline`, `research`, and
  `all`.

### Changed

- Code-task initialization now writes both the legacy codebase index and the
  new repo map, and patch application rebuilds both artifacts after edits.
- Code-task docs now describe the map -> locate -> context path before
  planning/editing for larger projects.
- Patch planning now uses the latest context pack when available, while
  controlled edit proposals use only editable context-pack snippets and keep
  protected files as read-only evidence.
- Code-task planning now has a higher-level work-plan layer for splitting
  broad tasks into small batches before asking for patch proposals.
- `code-task execute` now includes the work-plan and batch setup steps in its
  normal path, using the configured LLM unless `--no-llm` is set.
- Development docs now recommend targeted check groups during iteration and
  reserving full test discovery for commits, pushes, or broad refactors.
- V2.2 planning and workspace docs now record the Day 14 real-LLM smoke finding:
  ordinary JSON patch proposals can trigger very long completions, so the next
  editor-backend work should add bounded proposal contracts, context-request
  artifacts, multi-round attempts, and future external coding-agent routing.

## 2026-05-19

### Added

- Added V2.2 code-task workspace modes: `copy` remains the default, and
  `git_worktree` can create a detached worktree at `code_task/workspace` for
  repo-root git projects.
- Added experimental `sparse_copy` workspace mode with include/exclude patterns,
  built-in exclusions for data/model/cache/secret-like paths, and manifest
  pattern/risk recording.
- Added `[workspace]` config support plus CLI flags for standalone and embedded
  code-task runs: workspace mode, source virtualenv reuse, and recorded setup
  hooks.
- Added a structured `workspace` section to code-task `manifest.json` while
  preserving the old `copy` section for compatibility.

### Changed

- Code-task initialization now goes through a workspace dispatcher instead of
  calling the copy routine directly.
- `code-task init` now reports workspace and task-file setup problems with
  user-facing next-step checklists instead of raw Python tracebacks.
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
