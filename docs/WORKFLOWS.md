# Workflows And Artifacts

[中文版本](WORKFLOWS_zh.md)

This document explains what SimpleAutoResearch is doing internally: workflow
presets, pipeline stages, artifact ownership, and module boundaries. It avoids
duplicating the full artifact manual; for concrete commands and file trees, see
[Usage And Configuration](USAGE.md). For command flags, see
[CLI Reference](CLI_REFERENCE.md); for TOML fields, see
[Configuration Reference](CONFIG_REFERENCE.md).

## Workflow Presets

The current 8-stage pipeline is one preset, not the whole architecture. SimpleAutoResearch stays module-first so literature review, code improvement, experiment execution, and report writing can be recombined.

## Capability Runs

Alongside the workflow presets, `simple_ar.core` provides an opt-in boundary
for new replaceable capabilities. A capability receives declared input
references through `CapabilityContext`, writes outputs through an
attempt-scoped `ArtifactStore`, and returns a `CapabilityResult`. The
`SessionController` can persist a bounded attempt and its decision without
turning the existing pipeline into an unrestricted task graph.

This boundary is additive: it does not migrate the eight stages automatically
or change the artifact paths expected by existing commands and adapters. The
offline reference package in `examples/capability_package_minimal/` shows the
smallest supported handoff; domain-specific schemas belong to the capability,
not to the shared core.

An optional lifecycle profile narrows the capabilities that a session may
execute. The built-in scopes are `research_brief`, `survey`, `experiment`,
`paper_audit`, and `full_research`; they are allow-lists rather than automatic
workflow runners. Unknown profile names remain compatible with older callers.
For a new named-profile session without an explicit budget, the controller
allocates one attempt per named capability plus two bounded recovery attempts;
callers can override this with `BudgetState`, while loaded legacy manifests
retain their persisted budget.

For a caller-owned multi-capability handoff, the application layer should call
`SessionController.execute()` for each explicit capability in the chosen
sequence. It persists each attempt and stops when the returned decision is not
accepted. A resumed process should load the session, inspect its status and
attempt lineage, and explicitly construct the next call; the core does not
silently rerun an interrupted attempt or choose a domain-specific best result.
When an interruption has been manually confirmed, the caller can use
`SessionController.recover_interrupted()` to close the stale running attempt as
an explicit failure before constructing a retry or repair attempt. The method
does not retry or overwrite an existing result envelope.
The controller rejects any new attempt while a prior attempt is still marked
`running`, so recovery cannot accidentally create a second active branch.
Before creating a subsequent attempt, the controller also checks the actual
capability against the persisted current attempt's allow-listed transitions.
This catches an omitted or replaced route proposal while retaining listed
backtracks and same-capability repairs.
The controller applies the same check to a resumed call, and rejects missing
input artifacts before creating a new attempt.

Use `SessionController.attempt_output_refs()` to pass declared outputs from a
completed or failed attempt to a later capability. Attempt-local paths are
converted to session-root references without copying or merging artifacts;
the caller still decides which attempt and which output to use.
When an alternative should start from an earlier completed or failed attempt,
pass its ID as `parent_attempt_id` to `SessionController.execute()`. The
controller records that parent and validates the new capability against the
parent's route. Without this explicit argument, attempts continue from the
current attempt as before; the controller does not infer branches or select a
winner. Use `attempt_lineage()` to inspect the root-to-node chain for a
comparison or recovery view; it reads only persisted attempt manifests and
does not merge artifacts or schedule work.

The `research-session` application exposes this boundary through its
read-only `recommended_transition` property. A passed execution and analysis
recommend continuing to `report`; any other result is returned to the
`experiment` boundary for an explicit caller-owned repair or redesign. The
recommendation uses the core transition policy and session budget; it never
creates an attempt, reruns a command, or turns a failed session into a success.

For a library caller that wants one in-memory value, use
`research.brief.build_research_brief()` is an in-memory compatibility view only.
The default registry deliberately does not expose a composite `research_brief`
capability: sessions persist `read` and `synthesize` separately. Historical
`research_brief.v1` handoffs remain readable, but they are not a second
executable lifecycle.

The small user-facing composition is available as `simple-ar research-brief`.
It owns one explicit path:

```text
plan -> search -> document_ingest -> read -> synthesize
```

For an online topic, the command uses the configured built-in source providers:

```bash
uv run simple-ar research-brief --topic "reliable agents"
```

For a reproducible local run, provide one or more Markdown/text documents:

```bash
uv run simple-ar research-brief --topic "reliable agents" \
  --local-document examples/research_brief/fixtures/reliable_agents.md \
  --output-root runs/research-brief
```

The command creates a timestamped session directory. Each handoff remains in
its own attempt, normally under `attempts/plan-001/`, `attempts/search-001/`,
`attempts/document-001/`, `attempts/read-001/`, and
`attempts/synthesize-001/`. The canonical outputs are `research_plan.json`,
`search_result.json`, `document_bundle.json`, `read_result.json`, and
`synthesis_result.json`; capability results and attempt manifests record their
status and lineage. The command does not silently retry or overwrite a prior
attempt. `--query`, `--provider`, `--max-results`, `--max-chunks`, and
`--idea-limit` are the deliberately small controls for this path; more complex
policies remain application-owned. The aggregate `research_brief.v1` format
is still accepted as an input for older callers.

The standalone path is explicit about model use. Without `--model` it is an
offline/deterministic composition: search, parsing, card derivation, and
structured direction extraction use the supplied inputs. With `--model NAME`,
the existing LLM client is used for research planning, bounded Read screening/
reranking and paper notes, and synthesis; the handoff records the Read
provenance plus `planner: llm` and `generation_mode: llm`. Missing credentials,
transport failures, or malformed model output fail the relevant attempt; they
do not silently become a model-generated result.

The next small composition accepts the resulting research direction through
`simple-ar research-experiment`. It runs one declared command with the
existing execution backend and sends its canonical `results.json` to the
existing result-analysis capability. Its input is a persisted
`research_brief.v1` or `synthesis_result.v1`, so the direction-to-experiment
handoff is explicit and inspectable. Execution failures are analyzed as
evidence, but retries, repair, and experiment selection remain caller-owned.
Passing `--model NAME` also enables the shared LLM result-analysis step;
omitting it keeps analysis deterministic. The selected mode is recorded in
the analysis capability provenance.

For a single session that owns both sides of this handoff, use
`simple-ar research-session`. It reuses the same explicit
`plan -> search -> document_ingest -> read -> synthesize` prefix, records a
`research_design.v1` handoff, and continues with one explicit `ExperimentRequest`
and the existing Analysis capability. By default the command is still supplied
by the caller. Passing `--code-task-config` selects the existing project-style
Code-Task backend for that experiment attempt instead: its project, benchmark,
workspace, baseline, and execution settings remain owned by the TOML, and its
output is normalized into the same canonical result. This is a controlled
composition rather than an unrestricted research loop.

If that session ends with a failed experiment but retains its design and
analysis handoffs, one explicit recovery can reuse the same literature and
design without rebuilding them:

```bash
uv run simple-ar research-session-continue \
  --session-root runs/research-session/<session> \
  --cwd examples/research_brief/fixtures \
  --primary-metric accuracy \
  --metric-direction accuracy=higher \
  --command python -c "print('accuracy: 0.90')"
```

This creates only `experiment-002` and `analysis-002`, with the failed
`experiment-001` as their explicit parent. The revised command is supplied by
the caller; search, design, and code generation are not repeated. The session
reserves the existing report handoff slots, permits one recovery per session,
and leaves the original artifacts untouched. After a successful recovery,
continue with `simple-ar research-report`; a failed recovery remains persisted
for inspection and returns a non-zero shell status.

The same session can be continued into the existing report boundary with
`run_research_report_session()`. The caller supplies section drafts,
`ReportContext`, and any source refs; the adapter appends `report` and
`report_audit` attempts without copying or replacing the earlier analysis
artifact. A successful `research-session` prefix therefore reports
`ready_for_report`, while the report continuation is the operation that can
close the session. Supplying drafts explicitly keeps writer/revision policy
outside the lifecycle controller.

The existing `simple-ar status <session-root>` command also understands a
capability session's `session_manifest.json`. It reports the persisted session
checkpoint, attempt states, bounded budget, and last decision without rerunning
or rewriting any capability. The legacy `manifest.json` status path is
unchanged.

When the existing Writer/Reviewer implementation should own draft generation,
use `run_research_report_agent_session()` instead. It accepts the same compact
report context and memory plus the existing template, runtime config, LLM
client, and tool gateway; it then passes the validated section drafts to the
same report and audit capabilities. The writer trace is stored as
`inputs/report_agent_result.json` and referenced by the report attempt, while
the assembled `report.md` remains the single report body. This is an adapter
over the current report agent, not a second prompt or a new report pipeline.

For the standard literature-to-experiment path,
`build_research_session_report_inputs()` derives that compact context and
memory directly from a `ResearchSessionResult`: the persisted synthesis,
selected paper metadata, execution result, and result-analysis claims remain
the sources of the report inputs. `run_research_session_report_agent()` is the
small convenience wrapper for this path. It still requires the caller to
choose the template, runtime budget, and LLM client; it is a report handoff,
not an automatic research scheduler. It accepts only a session whose
execution and analysis both passed (`report_ready=True`). For failure or
partial-result reports, call the lower-level explicit report boundary with
the caller's chosen drafts and evidence instead.

After the process ends, `load_research_session_result()` restores the same
typed result from `session_manifest.json` and the declared `plan-001`,
`search-001`, `document-001`, `read-001`, `synthesize-001`, optional `design-001`,
`experiment-001`, and `analysis-001` outputs. It performs no network request, execution, retry, or result
selection, so a caller can explicitly continue an existing session into
Report/Audit without rerunning its earlier stages. Missing or malformed
handoffs fail closed with `ResearchSessionError`.

When a research direction should enter a real code experiment, the application
layer exposes `simple_ar.app.research_code_task.run_research_code_task_session()`.
It reads a persisted `synthesis_result.v1` or `research_brief.v1`, reuses the
existing Code-Task backend for workspace isolation, generation, validation,
execution, and result analysis, and retains execution/analysis refs in the same
session. The V2.8 path intentionally runs one explicitly selected research
direction. Multi-candidate comparison is deferred until the single-direction
path has been validated on a real prepared project.

```bash
uv run simple-ar research-code-task --topic "reliable agents" \
  --synthesis-file runs/research-brief/<session>/attempts/synthesize-001/synthesis_result.json \
  --code-task-config examples/code_task_medium_review/configs/code_task.toml \
  --output-root runs/research-code-task
```

`--with-report` continues a passed session through the existing experiment
report Writer/Reviewer and audit path:

```bash
uv run simple-ar research-code-task --topic "reliable agents" --synthesis-file runs/research-brief/<session>/attempts/synthesize-001/synthesis_result.json --code-task-config examples/code_task_medium_review/configs/code_task.toml --output-root runs/research-code-task --model "$SIMPLE_AR_MODEL" --with-report
```

The continuation is accepted only after execution and result analysis pass; it
does not retry or turn a failed run into a formal report.

The command runs one direction. Add `--with-report` when the passed session
should be handed to Report/Audit. The configuration must set
`[execute].use_llm = true`. This first consumer covers the existing project-style
Code-Task backend; it does not create a GPU environment or claim arbitrary
greenfield generation.

A finished or failed single Code-Task session can be restored in a later
process with `load_research_code_task_session_result(session_root)`. The
loader reads the session manifest, the declared synthesis input, and the
`canonical_results.2.5` plus `analysis_handoff.v1` outputs. It does not run
Code-Task, contact a provider, retry, or choose among artifacts; a missing or
inconsistent handoff raises `ResearchCodeTaskSessionError`.

To continue a session that was intentionally opened for reporting, first load
it with the restoration function and then call
`run_research_code_task_report_agent(session, ...)`. The wrapper requires the
persisted final decision to name `report` as the next capability, then reuses
the generic Writer/Reviewer and Report/Audit path.

`simple_ar.app.research_code_task_report` can then pass the session's execution
and analysis evidence to the generic Report/Audit boundary. It derives compact
context, metric sources, and claim evidence before reusing the existing report
assembler and audit. Section drafts are still supplied by the caller, so this
adapter should not be read as an automatic paper writer.

Applications that want the built-in adapters can use
`research.register_research_capabilities(registry, names=...)`. The `names`
argument is optional for the complete adapter set or can select only the
capabilities needed by a path; registration is still explicit and does not
create a scheduler. The helper covers deterministic research planning,
Search, Document Ingest, Read, Synthesis, Research Design, Experiment, Analysis, Report,
Report Audit, and Research Brief.
`analysis` is the canonical standalone result-analysis name; `analyze` remains
available as a legacy registry/session alias when explicitly selected.

The `plan` adapter reuses the existing question, query, and source-budget
builders and writes one `research_plan.v1` handoff. It is deterministic by
default; a caller can explicitly pass `use_llm=True` and the shared client to
obtain a normalized model-assisted plan. It does not choose the next
capability. The narrow `research_design` adapter consumes a persisted synthesis,
selects an explicit idea by default, or selects among the persisted candidates
when the caller explicitly enables its shared LLM client, and writes a
`research_design.v1` handoff containing the already-derived
`ResearchExperimentContract`. Without an explicit idea id, deterministic mode
considers candidates in shared evidence/execution-readiness order, preserving
input order for ties. It checks whether the selected contract is minimally
executable, but it does not invent a command, metric value, experiment matrix,
code, or execution plan. Domain-specific code generation and execution
implementations remain caller-owned.
`research.planning.search_request_from_plan()` is the small in-memory adapter
for passing that plan to the existing `SearchRequest`; it does not invoke a
provider or add retry, deduplication, or selection policy.

The individual evidence steps are also available when a caller already owns
their inputs: register `research.evidence.reader.run_read_capability()` for a
`DocumentBundle`, or `research.synthesis.run_synthesis_capability()` for an
expanded evidence pack. They write one `read_result.json` or
`synthesis_result.json` handoff respectively and leave document fetching,
LLM policy, and transition decisions to the caller.

For a session that starts at retrieval, register
`research.sources.run_search_capability()` explicitly. It writes one
attempt-local `search_result.json` containing normalized paper rows and the
provider/query response statuses. It is a handoff, not a replacement for the
legacy Search projection or its candidate-selection policy.

When a caller wants to continue directly from evidence synthesis into the
standalone execution adapter, the bounded recipe permits
`synthesize -> experiment -> analysis`. The caller still supplies the
`ExperimentRequest`, execution backend, and next-step decision; no design or
repair policy is inferred by the core.
When the request comes from a persisted `synthesis_result.v1`,
`research.experiment_request_from_synthesis()` transfers its existing
research-level experiment contract. The caller still supplies the
`RunRequest`, result schema, and execution decision; the helper does not
approve `needs_review` or implicitly execute, retry, or choose the next stage.

When the next step owns full-text access, register
`research.documents.run_document_ingest_capability()` with a
`DocumentIngestRequest`. It writes one restorable `document_bundle.json`
containing document records, sections, chunks, and extraction status. A later
Read attempt can load that declared artifact with
`DocumentBundle.from_handoff_dict()`; ingest itself does not select papers or
call an LLM.

After a Read attempt, `ReadResult.from_handoff_dict(payload, bundle=bundle)`
restores the typed cards while requiring the original document bundle
explicitly; source chunk text is therefore not copied into the Read artifact.
`research.brief.evidence_pack_from_read()` is the small adapter for passing
those cards to Synthesis when a caller composes the two capabilities directly.
Read generation and restoration also validate each declared `evidence_refs`
against the bundle's chunk IDs. Unresolved references remain visible as
diagnostics and downgrade the result to `partial`; the check does not scan
other files or block metadata-only compatibility reads.

The execution slice follows the same rule: register
`research.experiment.run_experiment_capability()` explicitly when a session
needs to run a `RunRequest`. It exposes the existing canonical result as
`results.json` and declares the captured streams as
`execution/stdout.txt` and `execution/stderr.txt` in the same attempt; register
`research.analysis.analyze_experiment_capability()` for the separate analysis
step. Failed or timed-out execution is never converted into a successful
capability, and its diagnostic streams remain available to later capabilities.
Missing analysis evidence is reported as `partial`; only a `passed` analysis
is exposed as `completed`. Persisted `analysis_handoff.v1` data can be restored
with `AnalysisHandoff.from_handoff_dict()` without rerunning or copying the
execution artifact.
For two completed result mappings, `research.analysis.compare_experiment_results()`
provides a small status-and-metric comparison that can be passed as
`ExperimentRequest.comparisons`; unknown directions and missing evidence remain
`inconclusive`, and the caller still owns any follow-up decision.
The resulting `AnalysisResult` also exposes a conservative evidence status
(`passed`, `failed`, `blocked`, `incomplete`, or `metric_below_target`).
It requires an explicit execution handoff and never schedules a retry or
transition; persisted standalone analyses additionally write
`analysis_status.json`.
When an upper-layer session needs to consume this status, use
`research.decisions.transition_request_from_analysis()`. It only builds the
existing transition input and never executes a next step, retries, or
overwrites an attempt; the caller still owns the recovery choice.

For an already assembled report, register
`report.audit.run_report_audit_capability()` when a session needs a standalone
audit. The caller passes explicit report artifact references and typed report
state; the adapter writes the existing `report_audit.json` shape and reports
warnings as partial rather than silently treating them as a clean pass.

When a caller owns completed section drafts, register
`report.capability.run_report_capability()` first. It assembles those drafts
with the existing report assembler, optional heading numbering, and optional
planned figure renderer, producing an attempt-local `report.md`. Generated
figures are declared as the same attempt's `figure` outputs, while the figure
manifest remains an index; a missing renderer output is reported as `partial`.
It does not write an audit or invoke the writer; pass the declared report
reference to the separate audit capability.

### 1. Research Report (Literature-First)

Use this when you want a literature review, survey, or DeepResearch-like report without emphasizing experiments.

Conceptual flow:

```text
plan -> search -> read -> synthesize -> report
```

Reality check today:

- `run --to-stage report` still executes design/code/run stages because the default pipeline is a teaching demo.
- For a pure literature pass, stop at `synthesize`, then resume `report`; `auto`
  mode will produce a research-only report because no `results.json` exists.

### 2. Code Task (Existing Codebase)

Use this when you already have code and want a focused modification, optimization, repair, or benchmark improvement.

Conceptual flow:

```text
init workspace -> index code -> map repo -> probe environment
-> apply baseline policy -> build context pack -> work-plan -> create batch
-> plan patch -> approve -> propose edits -> apply edits
-> review changes -> validate -> run patched benchmark -> post-run review
-> compare results
-> analyze failure -> repair proposal
```

Key boundaries:

- The source project is prepared under `code_task/workspace`; existing-project
  runs default to `auto`, which prefers a detached `git_worktree` for committed
  Git projects and falls back to a guarded `copy` with recorded next-step hints.
  For monorepos, the worktree is created at the repository root and the matching
  project subdirectory becomes the editable project root. Experimental
  `sparse_copy` copies only configured include patterns and always excludes
  data/model/cache/secret-like paths. The original code is never modified.
- Patch application is gated by an explicit human approval step.
- Edit proposals are conservative old/new replacements, not free-form rewrites.
- The default editor backend is `controlled_patch`; the backend interface is
  now explicit so future external agents can plug in behind the same safety and
  review gates.
- Multiple ordered edits may target one file, but every `old` block must remain
  uniquely matchable; invalid proposals stop before workspace files are written.
- `code-task execute` can run the next safe steps, but it stops at plan approval
  and proposal review unless the user explicitly continues.
- Work-plan items are meant to be executable implementation batches. The
  executor skips obvious analysis-only items when choosing the first active
  batch, so an LLM-generated "inspect the project" item does not constrain the
  edit stage by accident.
- When several reviewed work-plan items form a small serial dependency chain
  that must land together, such as feature producer, model consumer, and config
  switch, the active batch may merge them. The separate plan remains visible,
  while `batch_state.json.work_item.source_work_item_ids` and `target_files`
  show the bounded execution scope used by the edit proposal.
- A benchmark-passing repair is not automatically a task success. The
  before/after verdict comes from `code_task/run/comparison.json`; if patched
  metrics remain below baseline, the system has recovered execution but has not
  achieved an improvement objective yet.
- Baseline execution is a policy, not an unconditional cost. `auto`/`run`
  records unchanged metrics, `skip`/`none` continues without comparison, and
  `provided` stores user-supplied metrics with an explicit provenance marker.
- Current execution uses workspace isolation plus an explicit interpreter
  policy. It supports `current` and `external`; managed environment creation is
  planned later. `workspace.reuse_source_venv` can point a worktree/copy/sparse
  run at an existing source `.venv` Python without installing dependencies.

Bundled examples:

- `examples/research_report/`: research-only search/read/synthesize/report
  workflow with live academic sources and report variants.
- `examples/code_task_medium_review/`: standalone code-task workflow over a
  multi-module review classifier with a `main.py` entrypoint, JSON config,
  visible progress output, and a task that naturally touches feature extraction,
  model scoring, and configuration.
- `examples/full_pipeline_tiny_mlp/`: full 8-stage pipeline over a lightweight
  NumPy MLP benchmark, useful for end-to-end local checks without GPU.

### 3. Research With Experiment

Use this when you want a research idea to become an executable experiment and a result-backed report.

Conceptual flow:

```text
plan -> search -> read -> synthesize -> design experiment
-> template codegen or embedded code-task -> run benchmark -> report
```

Current status:

- `06-code` can generate a whitelisted template experiment, prepare an embedded
  code-task workspace for existing projects, or call the unified code-task
  greenfield engine when no source project exists yet. In the greenfield case,
  the real nested run lives under `06-code/code_task_run/`, while compatibility
  artifacts are projected back to `06-code/generated_project/`.
- `--experiment-template code_task_project` is the generic embedded handoff into the code-task workflow. It accepts either `--code-task-config` or explicit `--code-root`, optional `--task-file`, and `--benchmark-command` flags. If no task file is supplied, `05-design` generates `generated_code_task.md` from the earlier research artifacts and a compact codebase summary.
- The generated embedded task includes a compact Research-to-Code Bridge from
  synthesis/design artifacts, so code-task planning sees method-transfer hints,
  implementation hypotheses, metric contracts, ablation targets, resource
  constraints, and risk notes.
- `simple-ar run --config ...` is the preferred way to keep multi-option research/code-task runs readable and repeatable.
- `--experiment-template llm_code_task_toy_spam` remains only as a bundled smoke-test template.
- The embedded path is end-to-end: it builds the same repo-map/context-pack,
  work-plan, and attempt/batch evidence as standalone code tasks, then
  auto-approves the patch plan inside the prepared workspace. A strict serial
  dependency chain is merged into one bounded batch (at most three work items
  and four target files) so implementation, wiring, and configuration are not
  silently split across separate attempts. Such a batch normally uses the
  `large` budget; the embedded path reads `[execute].allow_large_edits` from
  the Code-Task TOML and preserves the run with a clear failure if explicit
  approval is absent. Standalone code-task remains the better place for a
  human to inspect a larger proposal interactively.
- The final report receives the nested code-task comparison as experiment
  evidence, so before/after metrics can appear in the Code Task Evidence section
  instead of being hidden inside `06-code/`.
- Report generation is guarded: LLM drafts are accepted only when citations, metric visibility, fixture disclosure, and toy-demo boundaries pass rule-based checks.

## Default 8-Stage Pipeline

```text
01 plan        Scope the topic and research question
02 search      Retrieve paper metadata, full text, and local chunks
03 read        Screen, shortlist, and structure retrieved papers
04 synthesize  Analyze themes, gaps, and experimentable hypotheses
05 design      Create an experiment plan
06 code        Generate experiment code or prepare an embedded code task
07 run         Execute the experiment and parse metrics
08 report      Write a Markdown report with references
```

| Stage | Main outputs | Purpose |
| --- | --- | --- |
| `plan` | `goal.md`, `problem.md` | Scope the topic into a concrete research question (LLM-backed when enabled). |
| `search` | `papers.jsonl`, `search_meta.json`, `documents/`, `research_index/` | Retrieve and ingest metadata/full text, record provider provenance, and build local chunks. It may select candidates within budget but does not perform semantic review. |
| `read` | `review/`, `paper_notes.json`, `notes.md` | Screen and prioritize retrieved papers, then convert the shortlist into canonical Paper Briefs (LLM-backed when enabled). Larger LLM runs use coarse title/abstract batches before reranking the kept set. |
| `synthesize` | `synthesis_brief.json`, `synthesis.md`, `hypothesis.md` | Analyze read-stage Paper Briefs into themes, gaps, bounded ideas, and testable hypotheses. The default derivation is deterministic; an explicitly enabled LLM may propose a bounded candidate list, but every motivation reference is checked against the supplied evidence. |
| `design` | `experiment_plan.json`, `experiment_contract.json`, `result_schema.json`, `resource_plan.json`, `dependency_plan.json`, `domain_profile.json`, `contract_validation.json` | Select a safe experiment template and write the executable contract, metric schema, resource/dependency budget, domain profile, and pre-code validation. |
| `code` | `code_task_run/`, `generated_project/`, `experiment.py`, or template code | Prepare an embedded existing-code task, run unified greenfield code-task generation, or write a whitelisted template experiment from the design contract. |
| `run` | `results.json`, `guard_report.json`, `stdout.txt`, `stderr.txt` | Execute the experiment, normalize canonical results, and guard against missing/invalid metrics before reporting. |
| `report` | `report.md`, `references.bib`, `manifest.json`, `report_quality.json`, `report_memory.json`, `report_audit.json` | Write a template-guided report with citations, bounded source backtracking, and audit artifacts (LLM-backed when enabled). |

## Search And LLM Boundaries

Search is the retrieval gate, not the whole evidence engine. It scopes research
questions, chooses source order, retrieves candidates, records provider
provenance, and builds document/full-text/index artifacts. It may rank and cap
retrieved candidates to stay within budget, but semantic screening, structured
reading, synthesis, and experiment-contract work are owned by later stages.

Normal runs keep compact artifacts by default:

```text
02-search/
  papers.jsonl / search_meta.json
  documents/       # normalized document records and full-text/cache manifests
  research_index/  # portable chunks and local-index metadata
```

`03-read` owns screening, reranking, and canonical Paper Briefs. In LLM mode it
first coarse-screens compact title/abstract batches, then reranks the kept set
with reading priorities, evidence roles, and synthesis hints. `04-synthesize`
owns `synthesis_brief.json`, `synthesis.md`, and `hypothesis.md`; legacy
cards/evidence-pack diagnostics are retained only when
`[run].debug_artifacts = true`. `05-design` owns the experiment contract.

When `[run].debug_artifacts = true`, search also keeps planning files, retrieval
traces, retrieval-selection rows, coverage-review reports, and section tables.
The V2.8 research path does not generate speculative Tool/MCP handoff artifacts;
those belong to the deferred external-Harness phase.

Shared accelerator stores live outside the run by default under
`.simple_ar_cache/research_index`, keyed by run/source metadata. Run-local cache
folders such as downloaded PDFs and extracted text are rebuildable and can be
previewed or cleaned with `simple-ar clean`.

LLM participation is bounded. The research planner can run in deterministic,
`auto`, or LLM mode; lightweight coverage checks and local novelty checks are
risk signals, not proof of originality. `--no-llm` keeps plan/read/synthesize/report
on deterministic fallback text.

For the full search-stage file tree and per-file descriptions, see
[Usage And Configuration](USAGE.md). For search, cache, parser, and debug-artifact
settings, see [Configuration Reference](CONFIG_REFERENCE.md).

## Artifact Ownership Summary

WORKFLOWS intentionally stays at the ownership level; the complete file tree lives
in [Usage And Configuration](USAGE.md). At a high level:

- Root run files (`state.json`, `manifest.json`, `config_snapshot.json`, usage
  logs, and optional artifact indexes) track resume state, configuration, and
  observability.
- Stage directories (`01-plan` through `08-report`) own their own contracts,
  reports, and stable handoff artifacts.
- `02-search` owns retrieval, document/full-text status, and local chunks.
- `03-read` owns reading review, shortlists, literature cards, and structured
  reading notes.
- `04-synthesize` owns the compact evidence bridge, gaps, ideas, novelty hints,
  synthesis, and hypothesis derived from read-stage artifacts.
- `05-design` owns experiment contracts, result schemas, resource/dependency
  plans, domain profiles, contract validation, and experiment plans.
- `06-code/code_task_run` embeds the same artifact shape as a standalone code
  task when the research pipeline hands off to code execution.
- `08-report` owns the final report package: report text, references, manifest,
  compact report memory, source/citation/metric audit, and quality checks.

This split keeps detailed operational files available without forcing readers to
learn every JSON/JSONL artifact before they understand the workflow. When a file
is primarily diagnostic or rebuildable, it should either be gated by
`debug_artifacts` or documented as cleanup-safe.

## Code Task Artifact Boundaries

Standalone code tasks and embedded pipeline code tasks use the same conceptual
layout. The important boundary is what each group is responsible for:

- `workspace/`: isolated editable project copy, worktree, or sparse subset.
- `meta/`: environment reports, repo maps, locate results, edit proposals,
  validation reports, applied-edit summaries, and LLM usage.
- `context_packs/`: bounded prompt context assembled from ranked editable files
  and protected read-only evidence.
- `attempts/`: durable work-plan and batch state for multi-step implementation
  and repair loops.
- `run/`: baseline/patched benchmark logs, metrics, execution reports, failure
  analysis, and before/after comparison.
- `repairs/`: bounded repair proposals grouped by repair attempt.

Tests, benchmarks, environment files, secrets, and user-configured protected paths
are indexed as read-only evidence by default and should not be edited by proposal,
repair, or apply steps. Edit-scope behavior and full artifact paths are described
in [Usage And Configuration](USAGE.md) and [Configuration Reference](CONFIG_REFERENCE.md).

## Code-Task Environment Strategy

Environment handling is intentionally separated from source-code isolation:

- Source-code isolation means user code is prepared under `code_task/workspace`
  before any patch is applied. In default `auto` mode this is usually a detached
  worktree for committed Git projects, with guarded copy fallback when Git is
  unavailable. The editable project root may be a subdirectory inside the
  worktree for monorepo-style code roots.
- Execution isolation means benchmarks run with a selected Python/runtime environment.

Today, code-task has the first kind of isolation and records environment signals
with `meta/environment_report.json`. It can select either the active
SimpleAutoResearch Python environment or a user-provided external interpreter.
It does not yet create virtual environments or install dependencies automatically.

The planned environment modes are:

- `current`: use the active SimpleAutoResearch Python environment. Supported now.
- `external`: use a user-provided Python or Conda interpreter. Supported now.
- `project-venv`: create a per-run environment inside the run directory. Planned.
- `shared-env-cache`: reuse environments keyed by dependency-file and platform hashes. Planned.
- `docker`: run in a container when stronger isolation is needed. Planned.

The default should remain conservative: dependency installation must be explicit
and reviewable, and user project packages should not be silently installed into
SimpleAutoResearch's own environment.

## Why This Split Matters

The split keeps the project from becoming one rigid pipeline.

- If the user wants a survey, code stages should be skipped.
- If the user wants to optimize existing code, literature stages should be optional.
- If the user wants a full automatic-research loop, modules can be composed.
- Each module can be upgraded independently.

This follows one practical lesson from AutoResearchClaw: complex behavior is easier to control when it is exposed as workflow modes and capabilities, not as one ever-growing sequence of flags.
