# Changelog

[中文版本](CHANGELOG_zh.md)

This file records user-visible project changes in reverse chronological order. Planning notes and design rationale live in `docs/` and `MDfiles/`; this file should stay close to a normal changelog.

## 2026-09-06

### Changed

- Canonical report assembly now carries the selected paper metadata through the
  `research-session` continuation, normalizes model citation keys, and writes a
  verified report body plus deterministic `References`, `references.bib`, and
  `citation_map.json` artifacts. Report audit now receives the pre-reference
  body, so citation checks do not mistake the generated reference list for
  body evidence.
- Tightened V2.8 document management: the V2.8 system plan, session handoff,
  and long-term vision are now versioned as the three active planning documents;
  historical and temporary `MDfiles/` notes remain ignored so they cannot form
  a second route.
- Defined the normal-user-scale V2.8 gate as a second acceptance layer:
  approximately 30--50 raw papers, 10--20 bounded Read candidates, one
  resource-bounded medium experiment, and a complete Markdown report with
  traceable citations and metric audit.
- Selected lightweight image classification on packaged
  `sklearn.datasets.load_digits` through `full_pipeline_tiny_mlp` as the first
  scale-acceptance direction, including explicit research-scope, code/data
  preparation, and report-continuation handoff points.
- Report metric audit now treats equivalent decimal and scientific-notation values
  as the same numeric evidence while preserving optional units and citation-number
  filtering, avoiding false warnings such as `4e-05` versus `4.0e-05`.
- Code-Task report handoff now resolves run-relative and stage-relative nested
  artifact references and supplements an incomplete writer section with a
  deterministic baseline/patched comparison table, so all recorded comparison
  metrics remain visible in the final report.
- The canonical `research-session` report continuation now appends a compact,
  deterministic experiment-evidence appendix after Writer assembly, including
  comparison deltas and metric provenance; this keeps Code-Task evidence intact
  even when the model-generated sections omit a recorded metric.
- The canonical Code-Task handoff now reads the comparison artifact from the
  backend's run directory, preserving baseline/patched deltas in research-session
  results and the final report.

## 2026-09-05

### Changed

- The canonical `research-session` path now connects an explicitly enabled,
  bounded model-assisted Read step. The model can screen and rerank retrieved
  papers and produce structured paper notes, while deterministic evidence and
  provenance checks remain authoritative; no-model and offline behavior remain
  available.
- Read handoffs now preserve inspectable screening decisions, paper notes, and
  source-snippet references for the downstream synthesis stage without copying
  whole documents or adding an autonomous scheduler.
- Added `examples/research_session_smoke.py` and the Linux/AutoDL helper
  `examples/autodl_low_resource_smoke.sh` for local and low-resource acceptance
  checks covering the bounded research-to-report path and report audit.
- Updated the public README, workflow, CLI, usage, development, and examples
  documentation to describe the canonical model-assisted Read path and its
  explicit online limits.

## 2026-09-02

### Changed

- Slimmed the V2.8 capability paths around the canonical `research-session`
  composition. The former monolithic research pipeline is now a frozen legacy
  compatibility implementation, while active application orchestration uses
  the shared core, research, experiment, and report boundaries.
- Removed redundant V2.8 session-plan, decision, tool-contract, and service
  layers after their behavior was covered by the canonical capability paths;
  existing CLI entrypoints and artifact compatibility remain explicit at the
  boundary.
- Added architecture-boundary regression coverage and aligned the public
  workflow, configuration, development, and CLI documentation with the slim
  structure.

## 2026-09-01

### Changed

- `research.synthesis` can now accept a bounded structured candidate list from an
  explicitly enabled LLM. Required fields and evidence references are validated
  before candidates replace the deterministic set; older prose-only responses
  remain compatible, and optional scalar list fields are normalized narrowly.
- `research_design` and the bounded Code-Task candidate path now share a deterministic
  readiness ordering: candidates with more complete execution fields and evidence
  are considered first, while explicit idea selection and existing schemas remain unchanged.
- Selecting a synthesis candidate now carries that candidate's baseline hints and
  expected outcome into the research experiment contract, preventing stale fields
  from an earlier candidate from reaching execution.
- Bounded Code-Task candidate runs can now explicitly continue the selected passed
  candidate into the existing report and audit path with `--with-report`.
- `simple-ar status` now reads persisted `research-session` checkpoints and
  reports attempts, bounded budget, and the last decision without rerunning or
  rewriting capability artifacts. Legacy pipeline and Code-Task status output
  is unchanged.
- Open sessions with a failed or revisable last attempt now show an explicit
  continuation hint in `status`; the hint does not start a retry or imply that
  a background process is still running.
- Bounded Code-Task candidate runs now checkpoint `candidate_summary.json` at
  startup and after each candidate, so interrupted work remains inspectable
  without adding an automatic scheduler or resuming a child workspace.
- `research-brief` and `research-session` now accept an optional `--cache-dir`
  for reusing valid downloaded full-text files across sessions. The default
  remains session-local storage, and cached PDF entries receive only a light
  file-header check before reuse.

## 2026-08-31

### Changed

- `research-session` now exposes a read-only bounded transition recommendation:
  passed execution and analysis recommend the report handoff, while other
  outcomes return to the experiment boundary for an explicit caller-owned
  repair or redesign. It never creates attempts, retries commands, or masks a
  failed session.
- `research-session` can now route its single experiment attempt through the
  existing project-style Code-Task backend when given `--code-task-config`,
  while retaining the same session, attempt, canonical-result, and analysis
  boundaries. The default explicit-command path is unchanged.
- Added an offline regression covering the Code-Task-backed session route and
  kept its configuration and handoff documentation aligned with the CLI.
- Added `research-session-continue` for one explicit, auditable recovery
  experiment. It reuses the persisted literature and design, appends isolated
  experiment/analysis attempts, preserves the failed parent, and leaves report
  continuation available without adding an automatic scheduler.

## 2026-08-30

### Changed

- Application handoffs now inspect capability status before resolving typed
  outputs and preserve capability diagnostics when a required handoff is
  missing. Failed experiment executions still remain available for downstream
  result analysis when their canonical results artifact exists.
- Added the explicit `research-report` CLI handoff for report-ready research
  sessions. It reuses the existing Writer/Reviewer and audit path without
  rerunning search or experiments, and appends report attempts instead of
  replacing earlier session artifacts.
- Added the opt-in `research-session --with-report` convenience path, which
  runs the same report continuation after a passed literature-to-experiment
  prefix without adding a second report engine or scheduler.
- Report continuations now preflight their fixed attempt slots before invoking
  the Writer, so repeated or closed sessions fail without consuming another
  model call or replacing the prior writer trace.
- Research design can now optionally use the shared LLM client to select one
  existing evidence-grounded idea; the deterministic contract builder remains
  authoritative and explicit idea selection remains available.
- Added a narrow `research_design` handoff that selects an existing synthesis
  idea and reuses its `ResearchExperimentContract` in the existing Experiment
  and Code-Task application paths, including the standard research-session
  composition; it does not create a second experiment schema or an LLM
  scheduler.
- Added `ResearchIterationPolicy` and bounded research iteration limits for
  caller-proposed transitions. It reads persisted decisions and blocks excess
  steps, capability visits, or repeated failures without executing or retrying
  work itself.
- Standalone research brief/session flows now accept an explicit `--model`
  (or library `llm_client`) and use the shared LLM transport for planning and
  synthesis; experiment sessions can use it for result analysis. Offline mode
  remains available when no model is supplied, and its provenance is explicit.
- Result analysis now fails visibly when LLM mode is requested without a
  client instead of silently returning deterministic output.
- Core session attempts can now explicitly branch from an existing completed or
  failed parent through `parent_attempt_id`, and expose its persisted root-to-node
  lineage for inspection; default linear execution and old manifests remain unchanged.
- Added the bounded `research-code-task` application entry. It consumes a
  persisted research synthesis through the existing project-style Code-Task
  backend, preserves canonical execution/analysis evidence, and can try a
  caller-selected number of isolated research candidates without implicit loops.
- Added a thin research-session-to-report-agent handoff that derives report
  context from persisted synthesis, literature, execution, and analysis
  evidence while reusing the existing Writer/Reviewer and report audit path.
- Added an explicit `research-code-task --with-report` continuation that
  reuses the generic report Writer/Reviewer and audit only after a passed
  single-candidate Code-Task session.
- Added read-only restoration for persisted Code-Task sessions through their
  declared synthesis, execution, and analysis handoffs; restoration never
  reruns or selects an alternative artifact.
- Added a narrow continuation wrapper for report-ready Code-Task sessions;
  it reuses the generic report agent and audit only when the persisted
  decision explicitly names `report` as the next capability.
- Removed confirmed-unused imports and made legacy workspace-state loading lazy
  in the shared artifact helper; no active artifact, compatibility alias, or
  result format was removed.
- Made capability session roots absolute before resolving artifact references,
  so relative output roots work through the same persisted handoff path as
  absolute roots without changing their filesystem spelling.

## 2026-08-29

### Changed

- Atomic code-task file writes now retry a transient Windows destination-file
  lock a small, bounded number of times before failing, while preserving the
  original atomic replacement behavior.
- Added a deterministic `plan` research capability and a small
  `research_plan.v1` to `SearchRequest` handoff; it reuses existing planners
  without adding an LLM call or scheduler. Direct session execution now
  rejects unregistered capabilities before creating an attempt.
- LLM `responses` and `chat` modes now retry transient provider failures on
  the selected API without silently switching surfaces. Cross-API fallback is
  available only through the explicit `auto` mode.
- New LLM usage rows record provider attempts for successful `ask()` requests,
  and run summaries expose the derived retry count while preserving legacy rows.
- Added an explicit, lazy research-capability registration helper for composing
  selected Search/Read/Synthesis/Experiment/Report adapters without a scheduler.
- Added typed restoration for persisted Read handoffs and a small Read-to-Synthesis
  adapter, with an offline Search-to-Synthesis session fixture covering explicit
  attempt lineage.
- Session transitions now validate the actual next capability against the
  persisted current attempt before creating it, while preserving allow-listed
  backtracks and same-capability repairs.
- Added a side-effect-free experiment comparison helper that consumes
  canonical baseline/candidate results, preserves execution-status changes,
  and stays inconclusive when metric direction evidence is missing.
- Added a conservative `AnalysisResult` evidence status and optional
  `analysis_status.json` handoff; it records execution/guard/metric/comparison
  state without selecting retries or research transitions.
- Analysis capability status now mirrors the analysis outcome: only `passed`
  maps to `completed`, while missing evidence, failure, and blocking remain
  `partial`, `failed`, and `blocked` respectively.
- Added typed `AnalysisHandoff` restoration for `analysis_handoff.v1`; it keeps
  the result and execution reference without rerunning or copying artifacts.
- Added `experiment_request_from_synthesis()` to explicitly transfer the
  research-level experiment contract from a persisted synthesis handoff to the
  Experiment boundary; the caller still supplies `RunRequest` and execution
  policy.
- Added research-layer adapters that translate analysis and report-audit
  outcomes into the existing `TransitionRequest` without executing, retrying,
  or scheduling the next capability.
- Added a side-effect-free synthesis-to-transition adapter; `needs_review`
  remains an insufficient-evidence signal and the caller still chooses the
  backtrack target.
- Report capability outputs now explicitly declare generated figure files;
  missing renderer outputs remain visible as `partial` instead of being
  treated as a successful report.
- Read handoffs now validate card evidence references against the original
  document bundle; unresolved references remain diagnostic and downgrade the
  result to `partial` without directory scans or source-text duplication.

## 2026-08-17

### Changed

- Online LLM-backed stages now fail after bounded provider retries by default
  instead of silently producing deterministic fallback output. Explicit offline
  runs and `[llm].allow_fallback = true` retain the compatibility path.
- The V2.8 undergraduate work plan now includes evidence-grounded hypothesis,
  experiment-intent, result-decision, and research-iteration deliverables.

## 2026-08-16

### Added

- Added an opt-in core capability boundary for new modules: declared artifact
  references, run/attempt-local storage, normalized capability results, explicit
  registration, and bounded session attempts.
- Added the offline `examples/capability_package_minimal/` reference package and
  the `simple-ar-checks core` validation group. Existing pipeline and code-task
  entry points remain unchanged.

## 2026-07-18

### Changed

- Structured LLM responses now normalize OpenAI-compatible chat content blocks
  and singleton object arrays before JSON validation. Report writers also
  recognize common nested/body aliases while preserving the canonical
  `draft_markdown` contract, improving interoperability without changing
  evaluator behavior.
- Report execution now accepts `--report-reviewer llm|disabled`. The disabled
  mode skips the reviewer-directed revision loop while retaining report writing
  and post-draft evidence audits. SurveyBench exposes this as an isolated
  `--without-review-guided-revision` ablation namespace and can reuse a
  completed run's upstream artifacts with `--reuse-full-run`.
- SurveyBench standard and thorough topic configurations now use `gpt-4o` for
  generation as well as the documented native content evaluation examples.
  This establishes a clearly labelled paper-comparison protocol; historical
  `gpt-5.1`-generated scores remain development results and are not relabelled.
- Paper-style reports now render their section hierarchy deterministically,
  keeping Abstract and References unnumbered while numbering ordinary sections
  and subsections. Long-form coverage checklists are also kept distinct from
  evidence-derived taxonomy seeds, so operational retrieval facets do not
  become reader-facing section headings by default.
- Adaptive long-form outline planning now retries once on an invalid LLM plan.
  If it still falls back to the deterministic outline, each section retains
  its configured evidence-source budget and records the decision in
  `08-report/outline_planning.json` rather than silently drafting from only a
  few outline anchors.
- Long-form drafting performs one bounded evidence-preserving completion pass
  when a section materially misses its configured length target. Batch source
  integration also preserves richer prior prose instead of replacing it with a
  shorter later-batch summary. Declared section targets remain writing
  constraints rather than implicit transport truncation limits.
- SurveyBench report-only reuse now temporarily snapshots and restores the
  canonical report package, guaranteeing that `--reuse-full-run` writes only
  the requested sibling variant and cannot pollute the source run's report
  artifacts.
- Report completion retries now preserve their extra completion instruction,
  and tolerate a claim-level evidence status accidentally placed in the
  section lifecycle field by normalizing it to a valid draft state.

## 2026-07-12

### Added

- ARC-Bench batch runs now expose `--planning-review-rounds` for low-cost
  planning-review ablations without editing per-topic TOML files.
- ARC-Bench `summarize` now reports repair counts, execution attempts, repair
  memory entries, and lightweight failure-type counts alongside score,
  runtime, and token means.
- ARC-Bench batch runner now has an `evidence` command that exports compact
  paper-oriented repair/fidelity evidence summaries from an existing state file
  without rerunning experiments.
- SurveyBench batch summaries now support mixed scoring: `score-thorough/`
  results are used first and missing topics can fall back to `score/`, with a
  paper-style Outline / Content / Richness table rendered in the summary.
- SurveyBench native evaluation command metadata is now written next to the
  corresponding score output as `native_command.json` instead of accumulating
  in a global `_native_commands/` directory.

### Changed

- Long-form report generation now carries per-section target words, minimum
  citation counts, and subsection constraints into Writer/Reviewer prompts;
  Abstract, Introduction, and Conclusion are no longer forced to contain nested
  subsections.
- Updated the experiment evidence-chain supplement with the latest mixed
  SurveyBench results and the report-stage long-form upgrades.

## 2026-07-11

### Added

- Report generation now has generic `[report.longform]` controls for long
  evidence-synthesis reports. Survey-style templates can write auditable
  `08-report/longform/` artifacts for paper selection, taxonomy, outline
  planning, citation coverage, and visual planning without adding default LLM
  calls.
- SurveyBench now has a separate `--thorough` profile for high-budget topic
  runs. It uses `configs/topics-thorough/`, `results/topics-thorough/`, and
  `results/score-thorough/` so final long-survey evaluations do not overwrite
  balanced iteration runs.
- Report Writer/Reviewer prompts now receive a compact long-form plan with
  target papers, target length, section citation keys, taxonomy facets, and
  visual goals when a long-form template is active.

### Changed

- SurveyBench topic configs now use `[report.longform]` instead of the older
  `[report.survey]` name. `[report.survey]` remains accepted as a compatibility
  alias, but new configs should prefer the benchmark-neutral name.
- Deterministic SVG figure generation can use the long-form taxonomy and
  visual plan as semantic inputs, improving figure relevance without using
  benchmark reference surveys.

## 2026-07-09

### Added

- Report generation now supports optional deterministic SVG figures through
  `[report.figures]`. The built-in `survey_long` template can insert compact
  local Markdown image assets without extra LLM calls, improving long survey
  richness while keeping compact reports unchanged by default.
- Read-stage screening now supports `read_min_shortlist`, allowing broad
  survey-style runs to backfill plausible candidates when early screening
  over-prunes the paper set.
- Report generation now supports survey task contracts, adaptive survey
  outline/source routing, and `cost_profile` budgets. Long survey templates use
  a balanced profile by default so SurveyBench-style runs no longer process
  unlimited source batches unless `cost_profile = "thorough"` is requested.
- Read-stage shortlist backfill is now facet-aware: when broad survey tasks set
  required facets, deterministic backfill prefers plausible papers that cover
  still-missing facets and records that reason in read artifacts.
- Deterministic report figures can now use the survey contract as a semantic
  fallback for figure nodes, improving topic alignment without adding LLM calls.

### Changed

- The SurveyBench single-topic config enables long-survey figures and keeps a
  larger read shortlist for 20-30-paper survey tests.
- SurveyBench export now copies relative Markdown image assets, such as
  `figures/*.svg`, together with generated survey Markdown.
- SurveyBench single-topic configs now use adaptive survey outline planning and
  balanced report budgets by default.

## 2026-07-08

### Added

- Added a lightweight `benchmark/survey_bench/` adapter that lists SurveyBench
  topics, validates and exports generated survey Markdown, calls SurveyBench's
  native content / quiz evaluation scripts, and summarizes result artifacts as
  JSON/Markdown.
- Added a bounded single-topic SurveyBench config that writes runs under
  `benchmark/survey_bench/results/<topic>/<timestamp-topic>/` instead of the
  global `runs/` tree.
- The SurveyBench adapter keeps native judge prompts untouched and treats
  `HumanSurvey` as evaluation-only reference data. Optional
  `--normalize-headings` performs Markdown heading-number adaptation only, so
  generated surveys can match SurveyBench's outline parser without adding
  content.
- SurveyBench summaries now render paper-style grouped tables with Outline
  Quality, Content Quality, Richness, and per-group averages.
- Added the built-in `survey_long` report template and reviewer criteria for
  longer reader-oriented academic surveys, plus switched the SurveyBench
  single-topic config to that template with larger retrieval/report budgets.

## 2026-07-05

### Changed

- Code-task task contracts now support generic implementation obligations.
  Planning assigns obligations to files, per-file generation receives the owned
  obligations, and generated-project review checks that owned obligations remain
  auditable without adding extra default LLM calls.
- ARC-Bench prepared tasks can now opt in to embedding rubric leaves as generic
  implementation obligations with `--include-contract`. The default prepared
  input remains vanilla ARC-Bench text for cleaner paper-facing comparisons.
- ARC-Bench scoring now builds leaf-targeted code evidence from all discovered
  Python files and adds an `arc-native` score profile for canonical
  ARC-style strict-audit prompting with the existing two-reviewer/adjudication
  protocol.
- ARC-Bench batch runner now has a `summarize` command that aggregates a batch
  into paper-style Code Development, Code Execution, Result Analysis, Overall,
  runtime, command-duration, and LLM token/call means without rerunning
  experiments.
- ARC-Bench batch runner now has a `refresh` command that reuses completed
  run directories to rerun finalize / result-analysis / score only. Refreshed
  outputs are written as variant submission directories with a new state file,
  leaving the original submissions untouched; refreshed summaries report
  incremental post-processing duration and LLM usage.
- ARC-Bench `summarize` now merges source run init/execute cost when summarizing
  refreshed variants. `Total Time` and total tokens represent the full
  experiment view, while `Postprocess` and `Postprocess Tokens` keep the
  rerun-only incremental view.
- ARC-Bench `--topic-set all` now includes ML17, matching the prepared ML01-ML25
  package set instead of silently running only 24 topics.
- Greenfield file generation now unwraps a single Markdown code fence returned
  inside the structured `content` field and records failed file-validation
  attempts under `code_task/meta/generation_steps/invalid_files/`. Retry
  feedback now includes concrete syntax, JSON, or non-ASCII identifier
  diagnostics instead of a generic invalid-file message.
- ARC-Bench batch runner pipe-mode logging now enforces command timeouts while
  streaming output. Background or redirected overnight runs can no longer miss
  an outer timeout because the parent process is blocked waiting for child
  stdout.
- Code-task benchmark execution now writes a compact runtime artifact scan for
  generated projects. When a required artifact is empty or missing at the
  expected path but a valid same-name artifact was written elsewhere, the
  quality guard reports an explicit artifact path mismatch for repair instead
  of treating it as a generic benchmark failure.
- Code-task benchmark execution now includes a conservative output watchdog for
  warning/repeated-output floods. Obvious runaway output is stopped early,
  recorded in the execution report, and routed through the normal failure
  analysis and repair path instead of waiting for the full benchmark timeout.
- Generated-project run repair now receives artifact-scan and runtime-watchdog
  evidence and keeps target selection narrower when the failure already has a
  clear contract-level cause.
- Greenfield review now includes a lightweight cross-file return-contract
  check. It can block cases where one generated function returns an aggregate
  mapping while another file still passes that value into a consumer annotated
  for a sequence of records.
- Generated-project run repair now injects return-contract mismatch evidence
  into repair context and reruns deterministic project review after each run
  repair before rerunning the benchmark. This catches producer/consumer drift
  introduced by a repair without adding a default LLM review call.
- Generated-project review repair now applies a deterministic local-API alias
  fix before asking the LLM. If one generated file imports `module.symbol` and
  the target module already contains `_symbol`, the repair adds a public alias
  and rechecks the project instead of relying on brittle old/new string edits.

## 2026-07-04

### Changed

- Greenfield code-task review now respects planned runtime placeholders such as
  output directories and artifact placeholders instead of treating every planned
  item as a source file that must be generated by the LLM.
- Greenfield architecture plans now carry compact metric, artifact, resource,
  and interface contracts into per-file generation, review, and repair prompts,
  helping generated files preserve cross-file evidence and output obligations.
- Code-task review and generated-project run repair now include deterministic
  static resource-risk signals for loop-heavy model fitting/search code, giving
  repair prompts a cheaper way to diagnose timeout or warning-flood failures
  without adding extra LLM calls.
- Greenfield review and repair now reject generated entrypoints that catch broad
  exceptions while suppressing tracebacks. Friendly CLI errors are still allowed
  when the original traceback is printed, logged, or re-raised, preserving
  repair-localization signal for later iterations.
- Greenfield file generation now rejects Python files with non-ASCII
  identifiers and LLM responses that self-report unresolved typos or
  pre-execution fixes, forcing a retry instead of accepting known-bad code.
- Code-task review now includes a task-triggered active-learning integrity
  guard that blocks acquisition/query policies from using hidden pool labels
  during sample selection while leaving non-active-learning tasks unaffected.
- Benchmark stderr streaming now suppresses repeated warning floods in the
  live console after the first few occurrences while preserving full stdout and
  stderr artifacts for diagnosis and repair.
- Generated-project run repair now rejects unapproved public API signature
  drift during structured actions and whole-file rewrites, reducing the chance
  that a later repair fixes one runtime error while silently breaking an
  existing producer/consumer contract.

## 2026-07-03

### Changed

- ARC-Bench batch runs now write per-topic runtime/API accounting artifacts
  (`arc_task_stats.json`) under both the run directory and finalized submission
  directory. The stats include wall-clock duration, per-command duration,
  return codes, log paths, and aggregated LLM request/token/cost information.
- ARC-Bench scoring now selects code evidence by entrypoint, local import graph,
  and task-term relevance instead of raw filename order. Strict Code Development
  reviews are less likely to miss core implementation files such as models,
  data generation, runners, or evaluators when artifact/config files are large.
- ARC-Bench strict scoring prompts now align more closely with the
  AutoResearchClaw manual strict-audit contract for implementation correctness,
  number grounding, verdict-data consistency, coverage penalties, unfinished
  run handling, and false-positive method detection while retaining
  SimpleAutoResearch's own evidence-bundle file selection.
- ARC-Bench `retry-unfinished` now refreshes stale completed topics by rerunning
  only finalize/score as needed; it no longer re-enters code-task execution for
  completed runs merely because a newer analysis or score profile is requested.
- ARC-Bench completed-state detection now reads analysis metadata from both the
  legacy top-level `analysis` field and the newer adapter-contract
  `metadata.analysis` field, avoiding unnecessary reruns after successful strict
  analyzed batches.
- Result-analysis now normalizes a wider range of experiment outputs into
  condition-level evidence tables, including raw `cells`, `cell_records`,
  `condition_summaries`, and method/training-size records. This improves
  evidence reuse for benchmark submissions and ordinary code-task reports.
- Result-analysis now matches common project-level metric aliases such as
  `test_accuracy` to condition-level rows stored as `accuracy`, so primary
  metric tables remain populated when global and per-cell metric names differ.
- Code-task failure analysis now searches source files for extracted error
  tokens when no traceback path is available, giving repair prompts concrete
  candidate files for errors such as missing attributes, missing fields, or
  unexpected keyword arguments.
- Code-task run repair now feeds failure graphs, stdout/stderr, validation
  reports, and recent repair history into runtime repair planning. Structured
  repair also supports explicit `no_change` actions and rejects whole-file
  rewrites that would erase public APIs.
- Greenfield file plans now distinguish source files from runtime directories
  and output placeholders such as `.gitkeep`, `artifacts/`, and
  `submission/results/`, so deterministic placeholders handle these paths
  instead of forcing LLM-generated source files.
- Greenfield review-repair prompts now spell out the required fields for each
  structured edit action, and code-task summaries distinguish a raw partial
  repair failure from a recovered follow-up review.

## 2026-06-30

### Changed

- Code-task now has a shared AgentStep audit layer for greenfield planning and
  per-file generation. Structured LLM substeps record stage, attempt, prompt
  hash, output summary, and failure status without saving full prompts by
  default.
- Greenfield planning-review findings are normalized into structured patch
  requests under `code_task/meta/planning/review_patch_requests.json`, so
  bounded revisions rerun from the earliest affected planning stage instead of
  blindly regenerating every stage.
- Greenfield review repair now reuses code-task memory and previous repair
  context, and writes repair status, changed files, and review-finding
  summaries back into repair memory.
- Code-task summaries now include continuation guidance with the current
  blocker, evidence-chain gaps, attempted repairs, recent review findings, and
  the suggested next repair entrypoint.
- Code-task generation now uses shared normalization helpers for paths, text,
  lists, and mappings across writer, planning, review, and repair code.
- Generated-project review/run repair now has a shared structured edit-action
  application layer. LLM repair prefers auditable local actions such as
  `replace_block` and `rewrite_function`; the code side enforces unique matches,
  AST function boundaries, public API diffs, and compile checks before falling
  back to whole-file replacement.
- Result-analysis and ARC scoring no longer save full prompt text on successful
  paths; prompt/raw-response diagnostic files are written only when parsing or
  scoring fails. Code-task subprocesses also disable `.pyc` writes to reduce
  cache-only filesystem noise.

### Fixed

- Fixed greenfield file-plan handling for runtime artifact paths such as
  `artifacts` and `submission/results`. Directories and output placeholders are
  now deterministic placeholders instead of missing source files, avoiding
  duplicated or missing `artifacts/results.json` paths.
- Generated-project review no longer treats defensive text such as "do not use
  placeholder values" as an executable placeholder path, while still blocking
  real dummy/stub outputs.
- Generated-project review now checks planned public APIs against exported
  public APIs and warns about generic producer/consumer contract drift.

## 2026-06-28

### Changed

- `SIMPLE_AR_LLM_API` now defaults to `responses`, while `chat` remains
  available for providers that require Chat Completions-style `messages`.
- Greenfield planning now stops when bounded planning review still reports
  high or critical blockers, instead of continuing into code generation with a
  known-bad architecture or file plan.
- Greenfield file generation now receives a compact per-file planning context
  instead of the full architecture JSON, reducing oversized writer prompts and
  making dependency/consumer contracts easier for the model to follow.
- Generated-project review now uses a generic layered review flow. It first
  builds a full ReviewIndex, then reviews bounded clusters such as
  entrypoint/orchestration, data flow, core logic, metric artifacts, and
  config/docs. The review writes `code_task/meta/review_index.json` and
  `code_task/meta/review_clusters.json`, avoiding full-code prompt stuffing and
  fixed filename sampling.
- Existing-project code-task review now uses the same ReviewIndex / cluster
  context while preserving patch diff, edit scope, validation report, and
  benchmark run-record evidence.
- Greenfield tool-agent planning now writes
  `code_task/meta/planning/agent_steps.jsonl` with stage, attempt, prompt hash,
  output summary, and failure status for each planning sub-agent.
- Generated-project review/run repair prompts now include compact ReviewIndex
  context, and review-repair target selection reads finding evidence and
  recommendations instead of relying only on a few categories or fixed
  filenames.

### Fixed

- Greenfield file generation now refuses to silently truncate a project when
  the generated files would exceed the configured line budget.
- Greenfield code-task summaries are refreshed after validation and benchmark
  reruns, avoiding stale `review_failed` summaries after later stages pass.
- LLM review remains non-blocking by default for older call sites, while
  generated-project layered review can preserve concrete blocking findings so
  missing deliverables, schema drift, and result-trust issues are not silently
  downgraded.
- ReviewIndex skips `.git`, `.venv`, `node_modules`, cache, and build
  directories by default so worktree/copy workspaces do not pollute review
  context with dependency or build artifacts.

## 2026-06-27

### Added

- Added a tool-agent greenfield planning mode for code-task generation. The
  default planner now decomposes architecture planning into requirements,
  architecture, interface contracts, file plan, and a bounded planning-review
  loop, with intermediate artifacts written under `code_task/meta/planning/`.
  Use `[execute].planning_mode = "compact"` or `--planning-mode compact` only
  when debugging the older single-call planner.

### Changed

- JSON-producing LLM calls now support optional provider-native
  `response_format={"type":"json_object"}` through
  `SIMPLE_AR_JSON_RESPONSE_FORMAT`. The default remains prompt-only JSON parsing
  (`off`) for broad third-party provider compatibility; use `auto` or
  `json_object` only with providers that support the parameter.
- LLM requests can now choose the LiteLLM API surface through
  `SIMPLE_AR_LLM_API`. `responses` uses Responses API-style `instructions` and
  `input`; `chat` keeps Chat Completions-style `messages` for providers that
  require that surface.
- Greenfield tool-agent planning now uses smaller stage prompts, less duplicated
  task context, stricter compact-JSON instructions, and a more tolerant JSON
  extractor. This reduces failures where the requirements stage hit the output
  cap and returned no recoverable JSON.
- Code-task model routing now covers greenfield planner/writer/reviewer roles
  in addition to existing-project planner/editor/repair routing. Configure
  `[models.code_task].planner`, `.writer`, `.reviewer`, `.editor`, and `.repair`
  in TOML while keeping provider keys and base URLs in environment variables.
- Greenfield code-task generation no longer silently mixes deterministic
  scaffolds into real LLM runs. Architecture planning and file generation now
  use bounded LLM retries, then stop by default if the model/client still fails.
  Deterministic fallback is available only when LLM mode is disabled or
  `[execute].allow_planning_fallback = true` is explicitly set.
- Added a generic task-contract extractor for greenfield code tasks. It turns
  explicit `task.md` requirements, deliverables, constraints, data requirements,
  dependency hints, and metric expectations into a durable contract consumed by
  architecture planning, per-file generation, implementation memory, review,
  and repair prompts.
- Per-file greenfield generation now receives dependency advice and compact
  implementation memory, including prior generated file summaries and public
  APIs. This gives each file writer global project continuity instead of
  asking it to infer cross-file schemas from the current file alone.
- Greenfield architecture prompts now explicitly ask for shared record/result
  schemas and concrete artifact flows before implementation, reducing schema
  drift between data, processing, runner, analysis, reporting, and validation
  files.
- Architecture and per-file prompts now use a compact prompt-facing task
  contract instead of serializing the full `task.md` on every request. The full
  task remains in artifacts for auditability, while the model receives the
  objective, bounded task excerpt, explicit requirements, deliverables,
  constraints, dependency hints, and metric contract needed for implementation.
- Greenfield architecture planning now uses a smaller per-request output budget
  and a stricter architecture-specific contract view. This makes the first
  planning call less likely to be cut off by slow providers or HTTP proxies,
  while file generation can still use the normal larger output budget.
- Code-task LLM planning now has stronger stage-level retry behavior for real
  runs. Greenfield architecture planning and per-file generation wait with
  bounded exponential backoff between stage attempts and print retry progress,
  while the lower-level LiteLLM provider retry still handles individual
  connection, timeout, rate-limit, and 5xx failures.
- The default code-task stage-level `llm_retry_attempts` is now 3. ARC-Bench
  prepared configs default to 4 and the batch runner can override this at run
  time with `--llm-retry-attempts N`, avoiding fragile mass failures during
  transient server-side connection drops.

### Fixed

- Generated-project review now blocks common hollow-success patterns such as
  filling missing required metrics with `0.0`, returning placeholder records,
  or leaving stub execution paths in tasks that require measured outputs.
- Review repair prompts now clarify that implementation findings must be fixed
  in the code path itself, not hidden by documentation-only changes or default
  metric values.

## 2026-06-26

### Changed

- Code-task repair now preserves continuity across repeated execute runs. Recent
  repair context is collected from task memory and prior repair artifacts, then
  injected into runtime repair planning and file-level repair prompts so the
  model can see what was already tried.
- Runtime repair prompts now explicitly require the model to explain why a
  previous fix was insufficient when the same failure survives a repair. This
  discourages repeatedly patching the same file or applying the same failed
  strategy.

### Fixed

- Generated-project runtime repair now skips repeated deterministic quick
  patches when previous repair context shows the same failure signal survived,
  allowing bounded LLM repair to inspect broader producer/consumer contracts
  instead of looping on a narrow attribute or entrypoint guess.

## 2026-06-25

### Added

- Added a generic result-analysis layer under `src/simple_ar/result_analysis/`.
  It normalizes run/submission result bundles, summarizes metrics and issues,
  and can call the configured LLM to produce structured analysis artifacts for
  downstream benchmark adapters or reporting surfaces.

### Changed

- Generated-project run repair now performs a diagnosis-first planning step before
  rewriting files. The repair planner receives benchmark stderr, failure analysis,
  candidate file excerpts, public APIs, task contract, dependency advice, and
  result schema, then selects target files and a repair strategy for the bounded
  file-level repair pass.
- File-level run repair prompts now include the runtime repair plan and relevant
  project context, so cross-file producer/consumer contract failures can be fixed
  with more complete context instead of repeatedly patching whichever entrypoint
  or warning file appeared first.
- The repair target ranker now treats missing dataset/source/field failures as
  generic data-contract issues and includes configuration files in the role
  model, improving fallback behavior when the diagnosis LLM call is unavailable.

## 2026-06-24

### Fixed

- Greenfield benchmark execution now rejects hollow successful runs where
  `generated_project/artifacts/results.json` contains no condition-level
  records or summaries and all non-resource metrics are zero. These runs are
  recorded as benchmark failures so repair can continue instead of accepting a
  parseable but meaningless metric table.
- Greenfield run repair prompts and target selection now discourage swallowing
  unresolved runtime errors as all-zero metrics. Empty-evidence failures target
  the entrypoint and experiment execution/analysis path, while data-contract
  failures prioritize input and processing modules before entrypoint changes.
- Code-task generated-project review and run repair no longer assume fixed
  filenames such as `generated_experiment/runner.py` or
  `generated_experiment/inputs.py` when choosing repair targets. They now rank
  actual project files by generic roles such as entrypoint, orchestration,
  data loading, preprocessing, core logic, and artifact/report writing.
- Code-task failure analysis now prefers failed benchmark stderr over static
  validation warnings. Runtime messages such as `Experiment failed` and
  `has no attribute` are preserved as repair signals, and generated-project
  run repair expands attribute/data-shape failures toward data, preprocessing,
  core, and orchestration files instead of repeatedly rewriting only the
  entrypoint.

## 2026-06-23

### Changed

- Greenfield dependency advice now uses a dynamic scan of the active Python
  environment plus task-relevant filtering. `code_task/meta/dependency_advice.json`
  keeps the full installed-package snapshot, while terminal output and model
  context use the compact relevant subset. The built-in catalog is now semantic
  hints rather than a whitelist.
- Greenfield review repair can run bounded LLM regeneration before validation
  for generic recoverable findings such as fallback core files, missing artifact
  writers, or missing local APIs. After repair, `code_task/meta/code_artifacts.json`
  is synchronized so stale metadata does not keep repaired files marked as
  fallback; partial repairs now also refresh `review_report.json` so follow-up
  runs target the remaining findings instead of replaying stale review output.
  Generic resource-detection support modules now have a deterministic repair
  path, so transient provider failures do not strand generated experiments on
  a non-domain fallback file.
- LiteLLM provider calls now use bounded exponential backoff for transient
  failures such as connection resets, timeouts, rate limits, and 5xx responses.
  The behavior is controlled by `SIMPLE_AR_LLM_RETRY_ATTEMPTS`,
  `SIMPLE_AR_LLM_RETRY_BASE_DELAY_SEC`, and
  `SIMPLE_AR_LLM_RETRY_MAX_DELAY_SEC`.
- Greenfield run repair now falls back to bounded LLM file regeneration when
  deterministic runtime repairs cannot handle a benchmark failure. The repair
  prompt includes stderr, failure analysis, current file content, project APIs,
  dependency advice, and metric schema, then compiles the generated project
  before the next validation/run loop.
- ARC-Bench adapter configs now generate `benchmark.metric_directions` from the
  topic manifest's declared experiment metrics, plus `runtime_sec = "resource"`.
  Structural completeness signals such as `condition_count`, `dataset_count`,
  and `hypothesis_coverage` may still appear in result artifacts, but they are
  no longer predeclared as benchmark objectives for every topic.

## 2026-06-18

### Added

- Added first-class greenfield code-task support. `code-task init` can now use
  `--kind greenfield` without a `code_root`; the workspace is created with the
  new `empty` mode and generated code lives under
  `code_task/workspace/generated_project/`.
- Added code-task memory artifacts under `code_task/memory/`, including
  `task_memory.json`, `task_memory.md`, `edit_history.jsonl`,
  `review_findings.jsonl`, and `repair_memory.jsonl`. The memory is a compact
  index over existing artifacts rather than a replacement for canonical logs,
  patches, or benchmark outputs.
- Added shared code-task resource artifacts:
  `code_task/meta/resource_probe.json` records compact machine signals, while
  `code_task/meta/resource_decision.json` turns them into a bounded execution
  profile for greenfield generation and external-agent handoffs.
- Added automatic code-task memory compaction. When active memory grows too
  large, older events are summarized into `compressed_memory.json` and
  `compressed_memory.md`, while recent events remain in `task_memory.*`.
- Added a shared reviewer contract plus code-task review artifacts. Code-task
  execute now writes structured `review_report*.json` files and stores
  reviewer findings in task memory so repair prompts can reuse them.
- Added external-agent output normalization during ingestion:
  `output_snapshot.json` and `normalized_outputs.json` record file hashes,
  generated-file manifests, patch/proposal outputs, and changed files without
  trusting agent self-reports.
- Added read-only code-task tools to the common tool harness:
  `read_code_task_memory`, `list_code_task_files`, `search_code_task_code`,
  `read_code_task_file_range`, `find_code_task_symbol`,
  `find_code_task_related_files`, and `list_code_task_recent_edits`.
- Added code-task-level greenfield external-agent handoff. Standalone
  `kind = "greenfield"` tasks can now route generation through `[implementation]`
  providers such as `fake`, `local_llm`, `codex`, `claude_code`, `opencode`, or
  `external_cli`, while still ingesting candidate files through the normal
  review, validation, benchmark, memory, and repair path.
- Added `examples/code_task_greenfield_ml_suite/`, a larger standalone
  greenfield code-task acceptance scenario for server or stronger local testing.
  It asks for a modular ML experiment workbench with packaged/local open
  datasets when available, synthetic fallback only when necessary, multiple
  model families, ablations, parseable metrics, and resource-aware execution.
- Added greenfield dependency advice artifacts. Before implementation planning,
  standalone greenfield code tasks now write `code_task/meta/dependency_advice.*`
  and print installed/missing recommended packages plus optional install
  commands. This remains advice-only and never installs dependencies
  automatically.

### Changed

- Code-task work planning, patch planning, edit proposal, and repair prompts now
  receive compact task memory so reruns and external-agent handoffs can preserve
  prior decisions, failed attempts, validation results, and repair context.
- Code-task execute now records memory events for probe, baseline, work-plan,
  patch-plan, proposal, apply, validation, patched run, failure analysis, and
  repair proposal steps. Patch validation blocks and static validation findings
  are also written as review findings.
- Code-task external-agent handoff packages now include task memory context and
  code-task-only tool schemas, while greenfield handoffs expose experiment-only
  tools. This keeps external agents focused on the current workflow.
- Embedded code-task runs inside the 8-stage pipeline now store active memory
  under `06-code/memory/`, keeping stage-local continuity beside code artifacts
  instead of burying it under `06-code/code_task_run/`.
- Greenfield code review now receives implementation memory, architecture, and
  resource context, and also exposes a normalized `review_contract` for report
  and guard consumers while keeping the legacy `code_review.v1` shape intact.
- External-agent greenfield generation now attempts one bounded retry when the
  first handoff finishes without a valid non-empty `generated_files/` output.
- Reserved `delegated_workspace` mode now writes
  `06-code/delegated_workspace_dry_run.json` before failing explicitly, making
  the future snapshot/diff/rollback boundary inspectable without enabling the
  dangerous path.
- The `simple_ar.code_task` package facade now lazy-loads public exports,
  preventing small submodule imports such as code-task tools from importing the
  entire code-task and agent-backend graph.
- Greenfield experiment generation in the 8-stage pipeline now delegates to a
  nested unified code-task run under `06-code/code_task_run/`, then projects
  compatibility artifacts back to `06-code/generated_project/` for `07-run`.
  This keeps standalone greenfield code tasks and research-to-code greenfield
  tasks on the same workspace, memory, review, validation, and run path.

## 2026-06-14

### Added

- Added the V2.6 common tool harness foundation under `src/simple_ar/tools/`.
  It provides shared tool specs, permission/risk levels, a registry that
  composes existing report and experiment tools, permissioned local dispatch,
  compact `tool_trace.jsonl` writing, and OpenAI/MCP-style schema export.
- Added the V2.6 external-agent handoff foundation under
  `src/simple_ar/agent_backends/`. It can write workspace-scoped
  `agent_handoff/<name>/` packages with instructions, real tool schemas,
  permission policy, artifact handles, expected outputs, context files, and
  workspace manifests.
- Added untrusted external-agent output ingestion into
  `agent_outputs/<name>/`, keeping backend results separate until the existing
  validation, result guard, report audit, or code-task patch checks approve
  them.
- Added Codex, Claude Code, and OpenCode profile Markdown files for future
  optional backends. These profiles are workspace-scoped guidance assets, not
  default global installations.
- Added runnable V2.6 backend wrappers: deterministic `fake`, `local_llm`,
  generic `external_cli`, and Codex / Claude Code / OpenCode CLI wrappers with
  cwd, timeout, env allowlist, stdout/stderr capture, and `agent_run.json`
  provenance.
- Added `simple-ar tools schema`, `simple-ar tools call`, and
  `simple-ar tools serve-mcp`. The MCP server is stdio-based and exposes real
  run-local read-only experiment tools through `tools/list` and `tools/call`.
- Added `examples/tool_mcp_codex_agent/`, a bounded Codex external-agent
  example with an MCP server template. The example leaves
  `[implementation].agent_model` empty by default so Codex CLI can use the
  account's configured model.
- Added `[implementation].agent_mode` as the single V2.6 external-agent mode
  switch: `model`, `handoff`, and the reserved `delegated_workspace` contract.

### Changed

- Development and usage docs now describe the V2.6 tool/agent boundary:
  external tools are optional strong-path adapters, while local research,
  report, experiment, and code-task workflows remain available without MCP or
  external agent CLIs.
- Greenfield generation and greenfield repair can now route through the agent
  handoff boundary when `[implementation].provider` selects an agent backend.
  External outputs remain untrusted candidate files and still pass the existing
  code review, result guard, rerun, and validation gates.
- The reserved code-task `external_agent` adapter can now launch enabled
  backends through the common handoff/ingestion path, while the default remains
  non-executing invocation-plan output.
- External agent wrappers now accept `[implementation].agent_model`, but
  examples leave it empty by default. Codex, Claude Code, and OpenCode tests
  should only set it when the CLI/account is known to support that model name.
- External agent failures now surface a concise stderr/stdout tail in the main
  runtime error, including hints for unsupported model names and missing CLI
  binaries.
- Agent-backed greenfield/code-task paths now normalize and validate
  `agent_mode`, record it in backend artifacts, and fail explicitly for the
  reserved delegated-workspace path instead of silently treating it as a normal
  handoff.
- External CLI backends now resolve Windows command shims such as `codex.cmd`
  before launching subprocesses, and the Codex wrapper uses an absolute
  handoff root with `--skip-git-repo-check`.
- External-agent handoff packages are now archived before reruns, preventing
  stale `stdout.txt`, `stderr.txt`, or `agent_result.json` files from steering
  a later Codex/Claude/OpenCode attempt. Stale handoffs are moved to the ignored
  local cache instead of a sibling `agent_handoff/archives/` directory so the
  next external agent cannot accidentally read old failure logs. Existing
  legacy sibling archives are relocated to the same cache before creating a new
  handoff.
- `simple-ar clean --shared-cache` now also clears
  `.simple_ar_cache/agent_handoff_archives`, so the existing cleanup command can
  fully remove cross-run external-agent handoff transcripts.
- Agent-backed greenfield generation now requires non-empty `generated_files/`
  before copying into `06-code/generated_project`, so empty directory proposals
  fail at the handoff boundary instead of surfacing later as a confusing missing
  `main.py` review error.

## 2026-06-13

### Added

- Added `07-run/diagnosis.json` and `diagnosis.md` for experiment runs. The
  diagnosis consolidates result guard issues, code-review warnings, missing
  metrics, stdout/stderr tails, and bounded repair suggestions into one
  repair/report context.
- Added the read-only `read_experiment_diagnosis` experiment tool and included
  diagnosis context in `inspect_execution_failure`.

### Changed

- Greenfield repair now consumes diagnosis context in addition to guard issues,
  so missing required metrics have one stable contract even if guard internals
  evolve.
- Experiment report context now exposes `artifact:experiment_diagnosis` beside
  canonical results and result guards.
- Pipeline stage output summaries now display the stage's real artifacts instead
  of the internal `contract.json` / `report.md` summaries, so `07-run` surfaces
  `results.json`, `guard_report.json`, `diagnosis.json`, stdout, and stderr.
- Greenfield schema repair now rewrites the generated project's actual
  `main.py` entrypoint and records repaired-result provenance on later reruns,
  rather than patching an unused fallback module.
- The greenfield training example is now a medium-light experiment-suite task
  instead of a tiny smoke project, with more condition-level metrics and a
  larger file/line budget.
- Greenfield architecture planning now treats 8+ file budgets as medium-light
  projects and asks for purposeful modules for data, features, models, metrics,
  evaluation, reporting, and self-checks.
- Developer quick checks now include run-config and public example-config loading
  tests, so example paths and unified config fields are guarded before broader
  pipeline tests.

## 2026-06-12

### Added

- Added the V2.5 experiment/code reliability foundation: top-level pipeline
  configs can now use unified `[task]`, `[implementation]`, `[execution]`,
  `[resource]`, `[evaluation]`, and `[generation]` sections.
- `05-design` now writes a compact experiment contract package:
  `experiment_contract.json/.md`, `result_schema.json`, `resource_plan.json`,
  `dependency_plan.json`, `domain_profile.json`, and
  `contract_validation.json`.
- Added domain profiles for generic experiments, existing-code experiments,
  ML experiments, and code-agent evaluation tasks.
- Added the V2.5 execution foundation under `src/simple_ar/experiment/execution/`:
  `RunRequest` / `RunResult`, a local execution backend, canonical result
  normalization, and result guard checks.
- Added a bounded greenfield implementation path for tasks without an existing
  source project. `06-code` now writes `architecture_plan.json/.md`,
  `file_plan.json`, `generated_project/`, `code_artifacts.json`,
  `implementation_memory.json`, `code_review.json`, `code_backend.json`, and a
  runnable `experiment.py` harness.
- Added an experiment tool contract layer under
  `src/simple_ar/experiment/tools/`, including read-only local gateway tools
  and OpenAI tool-schema export for future MCP/external-agent adapters.
- Added a lightweight greenfield training example at
  `examples/greenfield_lightweight_training/configs/greenfield_training.toml`.

### Changed

- Unified task settings are normalized into `task_config` and mapped back to
  legacy code-task keys where needed, so existing `code_task_project` runs keep
  working while new configs can use one coherent shape.
- `06-code` now stops before code generation when
  `05-design/contract_validation.json` reports a failed experiment contract.
- `07-run/results.json` now uses a canonical schema while keeping legacy
  top-level `metrics`, `returncode`, and `timed_out` fields for compatibility.
  `07-run/guard_report.json` records timeout, non-zero exit, missing metric,
  and NaN/Inf checks.
- Embedded code-task pipeline runs now project nested baseline-vs-patched
  comparison data into canonical `07-run/results.json.comparisons`.
- Embedded code-task pipeline runs now verify the patched benchmark during
  `06-code` before handing control to `07-run`; if the benchmark fails, the
  bridge performs one bounded repair attempt based on failure evidence instead
  of letting a broken patch become report evidence.
- `07-run` can now attempt one bounded greenfield repair when execution evidence
  shows schema-level missing metrics, then rerun the generated experiment and
  record `repair_summary.json`.
- Rerunning `06-code` or `07-run` now preserves existing reviewed artifacts under
  `archives/<timestamp>/` by default. Use `--overwrite-stage-artifacts` or
  `[run].overwrite_stage_artifacts = true` only when old code/run artifacts are
  intentionally disposable.
- Canonical `07-run/results.json` now carries compact resource-plan,
  code-review, and guard signals. `08-report` exposes those signals as
  experiment evidence so result claims can be qualified when code review or
  result guards warn.
- The embedded code-task bridge was split out of the old experiment facade into
  `src/simple_ar/experiment/code_task_bridge/`, and pipeline experiment stage
  logic was split into design/code/run modules so `pipeline_stages/experiment.py`
  remains a thin wrapper.
- The full-pipeline tiny MLP example now includes the new unified task/config
  sections while retaining legacy code-task sections for compatibility.
- Configuration reference docs now document the unified V2.5 sections and no
  longer treat `[workspace]` alone as a signal that a run config is an embedded
  code-task config.
- Greenfield experiment contracts now include a bounded excerpt of
  `[task].task_file`, so from-scratch code generation can follow detailed task
  Markdown instead of only seeing the file path.
- Greenfield code review no longer silently replaces LLM-generated projects
  with a deterministic scaffold by default. Deterministic review failures now
  keep artifacts for inspection unless `[generation].allow_fallback_scaffold`
  is explicitly enabled; LLM reviewer findings are retained as warnings.

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

- Embedded `code_task_project` runs now keep the first execution batch small by
  default instead of automatically merging serial dependent work-plan items into
  a large patch. Standalone `code-task execute` keeps the larger merge behavior
  for interactive code-task workflows.
- Report context now treats nested code-task `run/comparison.json` as first-class
  experiment evidence. `08-report` can cite baseline/patched/delta metrics and
  the fallback report includes a before/after Code Task Evidence table.
- A strict embedded code-task pipeline check now covers the full path from
  `06-code` through `08-report`; the bundled tiny-digits MLP run improved
  accuracy from `0.766667` to `0.913333` and macro F1 from `0.756898` to
  `0.913254`.
- Removed the hidden legacy single-prompt report drafting branch from the V2.4
  report service. LLM-backed reports now go through the Writer/Reviewer agent
  loop, and failures fall back to the structured deterministic report.
- Report citation mapping/display/cleanup helpers moved into
  `src/simple_ar/report/citations.py`, and the report tool gateway now accepts
  source handles when resolving paper briefs so reviewer tool requests match the
  advertised schema.
- Public examples are now organized around four maintained entrypoints:
  `examples/research_report/`, `examples/code_task_medium_review/`, and
  `examples/full_pipeline_tiny_mlp/`, plus the V2.5
  `examples/greenfield_lightweight_training/` entrypoint. Older narrow or
  transitional configs were removed from `examples/`, and the toy spam project
  moved to `tests/fixtures`.
- `code-task execute` now runs continuously to real review gates by default,
  with Rich step/status output. The `--interactive` flag is reserved for
  primitive-step debugging; `--yes` now explicitly auto-approves inline review
  gates in normal execute mode and auto-continues primitive prompts in
  interactive mode.
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
