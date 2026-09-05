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

### Compatibility Audit

The repository has two intentionally different execution surfaces:

```text
research-session / research-brief / research-experiment
  -> typed research capabilities -> SessionController -> ArtifactStore

simple-ar run
  -> PipelineRunner -> frozen eight-stage compatibility projection
```

The first line is the V2.8 direction. The second line remains because existing
configs, run directories, and tests still consume its stage-shaped artifacts.
It is not a second place to add new research policy. New capability work belongs
under `research/`, `experiment/`, or `report/`; `pipeline_stages/` should only
adapt or project that behavior for the old command.

The September 2026 cleanup removed confirmed speculative or duplicate layers:
the unused session-plan abstraction, multi-candidate CodeTask scheduler,
standalone research iteration policy, and research Tool/MCP design-contract
artifacts. The existing evidence pack/cards were kept because the legacy debug
path and synthesis tests still consume them. CodeTask's external CLI support is
also kept as a disabled/explicit backend because the current experiment path
uses its provider factory; it is not a V2.8 workflow controller.

This is an explicit keep decision, not a promise that every old path is
permanent. Before deleting another compatibility module, search imports, CLI
dispatch, docs, fixtures, and historical readers, then preserve the old-format
regression while migrating its real consumer.

### Cleanup Policy

Keep a generated artifact when it is a declared handoff, an audit record, a
portable user-facing output, or a compatibility input. Rebuildable caches may
be removed only through the explicit `simple-ar clean` command; the pipeline
must not silently delete them. Before removing code, search imports, CLI
dispatch, documentation, fixtures, and historical readers, then add a focused
regression for the replacement path. Prefer removing dead imports or a proven
duplicate branch over splitting a large but cohesive adapter into more layers.

The cleanup rule is deliberately asymmetric: remove code when its production
consumer is gone, but do not split a cohesive compatibility adapter merely to
make the line count look smaller. The remaining large files are named debt,
not new architectural owners: `pipeline_stages/research.py` is the old
search/read/synthesis adapter, `core/session.py` is the attempt boundary, and
`report/service.py` is the established report writer. Their next migrations
must move one real capability at a time and delete the old implementation only
after the canonical path owns the same behavior and regression evidence.

## Ownership Map

Use this map when deciding where a change belongs. The stable entry is the
small public boundary a new caller may depend on; the final column is equally
important because it prevents domain policy from leaking into the core.

| Area | Stable entry | Owns | Does not own |
| --- | --- | --- | --- |
| Core runtime | `simple_ar.core` | artifact references, attempt lineage, bounded decisions, profiles, and transition validation | domain schemas, LLM calls, code edits, retries, or selecting the best result |
| Sources, documents, and evidence | `research.sources`, `research.documents`, `research.evidence` | provider/parser ports, document bundles, cards, chunks, and provenance-aware handoffs | workflow scheduling, provider-specific policy in core, or copying full text into every handoff |
| Synthesis | `research.synthesis`, `research.brief` | evidence-derived directions, research contracts, and the smallest literature-to-idea composition | claiming novelty, choosing an experiment automatically, or calling a model implicitly |
| Experiment and analysis | `research.experiment`, `research.analysis`, existing `experiment.execution` | explicit run requests, canonical results, metric comparison, and result evidence status | code generation, repair policy, retry policy, or deciding the next research stage |
| Report and audit | `report.capability`, `report.audit`, legacy `report.service` | explicit section assembly, optional figure rendering, citation/metric audit, and legacy report compatibility | hiding missing evidence, inventing figures, or replacing the legacy writer/reviewer without a migration contract |
| Application and benchmarks | `cli`, `pipeline_stages`, `code_task`, and benchmark adapters | user-facing orchestration, legacy projections, code-task policy, and external evaluator integration | becoming a dependency of the core runtime or changing canonical capability semantics for one benchmark |

When a feature appears to span two rows, keep the coordination in the
application or an explicit adapter and pass declared `ArtifactRef` inputs. Do
not make the lower row import private files from the upper row. A new class or
artifact is justified only when an existing boundary cannot express a real
consumer's input, output, or failure state; otherwise add a function, adapter,
or fixture at the existing boundary.

## Capability Boundary For New Modules

New replaceable modules may use the small capability boundary in
`src/simple_ar/core/` without changing the existing pipeline. `ArtifactRef`
identifies a declared artifact, `ArtifactStore` provides run-relative and
attempt-local file access, `CapabilityContext` passes registered inputs and a
profile, and `CapabilityResult` returns status, output references, diagnostics,
and provenance. `CapabilityRegistry` uses explicit registrations; it does not
scan the repository or dynamically import arbitrary providers. When a
controller-managed capability returns an `available` output reference whose
file is absent from its attempt store, the controller marks that ref
`missing`, adds a diagnostic, and downgrades a claimed `completed` result to
`partial`. It performs this check only on declared outputs; it does not scan
the attempt or calculate file hashes. Explicit `missing`, `not_rendered`, and
`failed` artifact statuses remain unchanged.

The built-in research adapters can be registered with
`research.register_research_capabilities(registry, names=...)`. This helper
loads only when called, accepts an explicit subset, and supports replacing a
selected implementation. It does not register the legacy eight-stage handlers
or create a workflow scheduler. The deterministic `plan` adapter is included
because it reuses the existing question, query, and source-budget builders and
writes a `research_plan.v1` handoff without an LLM call. Domain-specific
`design`, `code`, and `run` implementations remain owned by the application
until their contracts are ready.
`research.planning.search_request_from_plan()` is the corresponding in-memory
handoff to `SearchRequest`; it does not invoke a provider or own search policy.

`SessionController` adds bounded attempts and decision persistence for new
capabilities. It does not replace `PipelineRunner`, decide an unrestricted
research graph, or add implicit retries. Existing `simple-ar run`, code-task
commands, and their legacy projections remain the compatibility path until a
real capability has an input/output contract and regression evidence.
The `core` package also contains the historical `pipeline.py` and
`stage_results.py` compatibility modules; they are not the dependency-free
runtime boundary for new capabilities. New modules should depend only on the
artifact/session APIs above, while changes to the legacy collector must retain
the old pipeline and projection behavior.
Direct `execute()` calls also resolve the requested handler before creating an
attempt, so a misspelled or unregistered capability cannot consume budget or
leave a synthetic failure attempt.

`TransitionPolicy` is the small deterministic guard around that controller.
`TransitionRecipe` is an explicit allow-list of permitted next capabilities;
`classify_failure()` normalizes short diagnostic signals into a bounded set of
failure kinds. Semantic inputs such as evidence sufficiency or hypothesis
support can request a revisiting target, but the recipe still rejects
unlisted jumps. The policy never calls an LLM, scans the full run, or retries
implicitly. `DecisionRecord` records the resulting failure kind and next
capability together with the budget counters observed at that decision, while
`list_attempts()` exposes persisted attempt lineage for comparison without
merging their artifacts.
`status_snapshot()` provides a compact JSON-ready view for status displays and
handoffs. It reports session/attempt counts, budget, the latest decision, and
optional profile-visible targets, plus the ID and capability of each running
attempt, but never copies artifact contents or picks a domain-specific best
result. Proposed transition targets are preflighted
before a capability handler runs, so an impossible jump cannot spend a handler
call or create an empty attempt. When a later attempt is requested, the same
recipe is checked against the persisted current capability, so omitting or
replacing a route proposal cannot bypass the allow-list. A newly created attempt and its session
running state are persisted before the handler starts, so an interrupted
process leaves resumable lineage instead of an unmarked invocation.
After a process-level interruption, a caller may load the session and call
`recover_interrupted()` for the manually confirmed running attempt. This writes
an explicit failed capability result and closes that attempt; it never retries,
overwrites an existing result envelope, or chooses the next domain operation.
While any attempt is still marked `running`, a new attempt is rejected until
that explicit recovery is performed. This preserves the one-active-attempt
lineage without silently creating a second branch. A caller that intentionally
wants to compare an alternative from an earlier node may pass
`parent_attempt_id` to `execute()`. The parent must be an existing completed or
failed attempt, and the transition is checked against that parent's capability
before a new attempt is created. The default remains the persisted current
attempt, so ordinary linear runs are unchanged; this option is an explicit
lineage branch, not a graph scheduler or automatic retry.
Use `attempt_lineage()` when a caller needs the root-to-node chain for a
comparison or recovery view. It reads attempt manifests only, does not merge
artifacts, choose a best result, or schedule work; missing parents and cycles
are reported explicitly.

The V2.8 application layer owns the ordered capability sequence and calls
`SessionController.execute()` explicitly. This keeps the sequence visible in
the use case instead of hiding it in a generic plan runner. The controller
still preflights registered handlers, allowed transitions, input artifacts and
budgets before an attempt is created; a higher-level workflow must inspect the
returned decision before constructing a bounded continuation. Every supplied
input must be an existing session artifact; missing handoffs fail at this
boundary without creating an attempt or spending session budget.

Attempt outputs are local to their attempt directory. Use
`SessionController.attempt_output_refs()` when a later capability should read
an earlier declared output: it returns session-root references such as
`attempts/attempt-001/result.json` without copying files or selecting a best
attempt. This keeps cross-capability handoff explicit and prevents a relative
artifact path from being resolved against the wrong store.
When a capability emits multiple domain outputs, use
`attempt_output_ref(..., kind=..., schema=...)` to require one unambiguous
artifact instead of relying on output order; ambiguous kinds fail explicitly.

`LifecycleProfile` provides five optional, built-in capability scopes:
`research_brief`, `survey`, `experiment`, `paper_audit`, and `full_research`.
When a session uses one of these names, the controller rejects a capability or
transition outside its allow-list before execution. This is a scope check, not
an automatic workflow or a mandatory start point. Unrecognized profile names
remain unscoped for compatibility with older callers and experiments.
When a new session uses a recognized profile without an explicit `BudgetState`,
its default attempt budget is the number of named capabilities plus two bounded
recovery attempts. An explicit budget always wins; legacy manifests keep their
stored counters and limits.
An attempt may inherit the session profile or omit it; it cannot replace a
scoped session with another profile.
Use `SessionController.allowed_targets(source)` when a caller needs to render
the permitted next steps; do not inspect the recipe or profile internals.
The built-in capability names are the actual stage boundaries: `plan`, `search`,
`document_ingest`, `read`, `synthesize`, `research_design`, `experiment`,
`analysis`, `report`, and `report_audit`. `analyze` remains a legacy alias for
`analysis`. `research_brief` is an application/profile name, not a hidden
composite capability; arbitrary caller-chosen names remain suitable only for an
unscoped or legacy session.

The ordered capability sequence is documented by the application workflow and
is not an implicit scheduler. Callers still provide capability inputs and may
choose a permitted backtrack explicitly.

The smallest end-to-end reference is
`examples/capability_package_minimal/`. Run `uv run simple-ar-checks core` to
verify the boundary offline. New capability work should begin from this
contract and keep domain-specific request/result schemas outside the core.

Each controller-managed attempt records its capability in the attempt manifest
and stores one `capability_result.json`
containing only the `CapabilityResult` status, output references, diagnostics,
usage, and provenance. This preserves the result boundary after the process
ends without copying full text or raw logs; the legacy eight-stage artifact
layout is unchanged.

The old monolithic CLI and stage handler modules have been moved out of
`src/simple_ar/_legacy/`. That package now only contains compatibility aliases
for older imports. New behavior should be implemented in the domain modules
under `core/`, `research/`, `experiment/`, `report/`, and `code_task/`.

CLI code is split by responsibility:

```text
src/simple_ar/cli/
  parser.py  argparse command and option declarations
  main.py    command dispatch and user-facing output
```

Pipeline-stage orchestration is split by workflow area:

```text
src/simple_ar/pipeline_stages/
  research.py    stages 01-04: plan, search, read, synthesize
  experiment.py  stages 05-07: design, code, run
  report.py      stage 08 report packaging and safety checks
  common.py      shared stage helpers such as LLM access and artifact reads
  registry.py    HANDLERS registry used by PipelineRunner
  handlers.py    compatibility aggregation only; do not add new logic here
```

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

### Replacing A Search Provider

`research.sources.base` defines the small provider port:
`SearchQuery -> SearchResponse`. `research.sources.registry.SearchProviderRegistry`
owns explicit connector factories, while the search stage keeps query planning,
deduplication, caching, and artifact projection. The default pipeline remains
backward compatible, but library callers can pass `provider_registry=` to
`execute_search` or register a new source name without changing those policies.
Provider implementations should stay focused on source access and return
normalized `Paper` objects; they should not write run artifacts or decide
research coverage.

### Using The Standalone Search Boundary

`research.sources.capability` provides the smallest multi-source search entry
point for library callers:

```python
from simple_ar.research.sources import (
    SearchRequest,
    default_search_provider_registry,
    search_sources,
)

result = search_sources(
    SearchRequest(
        queries=("research topic",),
        providers=("openalex", "arxiv"),
        max_results_per_query=5,
    ),
    registry=default_search_provider_registry(),
)
```

The result preserves one response for each provider/query pair and uses
`completed`, `partial`, `empty`, or `failed` to distinguish usable results,
source failures, and successful empty searches. This boundary does not write
stage files, select candidates, deduplicate papers, or download full text;
those remain policies of the existing Search stage and its callers.
When a caller needs a session handoff, `run_search_capability()` persists the
same normalized paper rows and provider/query response metadata as one
attempt-local `search_result.json`. It reports partial or empty searches as
non-complete outcomes and does not add retry or selection policy.
`SearchResult.from_handoff_dict()` restores that persisted `search_handoff.v1`
without network access, while retaining diagnostics for response rows that
refer to missing paper metadata.

### Reusing Document Ingest

`research.documents.ingest.build_document_bundle()` is the narrow composition
boundary for document metadata, permitted full-text handling, sections, and
chunks. It reuses the existing research records without calling an LLM or
writing stage artifacts. Search keeps ownership of index persistence and
legacy JSON/JSONL projections. Downstream code can use
`research.service.load_search_document_bundle(ctx)` to hydrate that same typed
bundle from state aliases or legacy Search paths, so a reader does not need to
know which provider or directory layout produced it.

`research.documents.ports` provides the small `DocumentResolver` and
`DocumentParser` ports used after a manifest has selected a local resource.
`build_local_document_bundle()` is the direct local-document entry point; it
reuses the existing bundle, section, chunk, and Read logic without running
Search. The default resolver and parser preserve the existing local/cache
behavior, while callers can inject a resolver or parser for another storage or
document service.
`research.documents.LocalDocumentParser` is the reusable default implementation
for the existing plain-text, HTML, optional-PDF, and `unstructured` paths. The
legacy extraction helper still delegates to it, so selecting a different parser
does not require changing bundle construction or the old Search projection.
`DocumentBundle.to_handoff_dict()` and `from_handoff_dict()` define the
restorable `document_bundle.v1` representation. For a session-owned ingest,
`run_document_ingest_capability()` writes that bundle once as
`document_bundle.json` and exposes it through the attempt manifest; Read can
then be run in a later process by explicitly loading the bundle, without
re-fetching or duplicating it into another stage artifact.

### Reusing The Read Boundary

`research.evidence.reader.ReadRequest` accepts a `DocumentBundle` and optional
document or paper identifiers. `read_documents()` returns typed evidence cards
and diagnostics without calling an LLM or writing files. The existing
`write_read_card_artifacts()` function remains a compatibility projection over
that boundary, so stage artifact paths and legacy callers stay unchanged.
For a session-owned attempt, `run_read_capability()` persists the same cards and
source locations as one `read_result.json` handoff; it does not copy chunk text,
fetch documents, or expand the selection.
Both generated and restored Read results validate declared `evidence_refs`
against the chunks in the same `DocumentBundle`; an unresolved reference is
recorded as a diagnostic and downgrades the result to `partial`. The same
side-effect-free check is available as `validate_read_evidence()`. It validates
explicit references only; it does not scan files or judge semantic correctness.

### Reusing The Synthesis Boundary

`research.synthesis.SynthesisRequest` accepts the expanded evidence pack already
assembled by the research pipeline. `synthesize_evidence()` returns bounded
`IdeaCandidate`, `NoveltyCheck`, and optional `ExperimentContract` objects plus
an evidence-gap summary. It is deterministic by default and does not write
files. A caller may explicitly provide an LLM client through
`SynthesisRequest(use_llm=True, llm_client=...)`; that keeps the structured
derivation and adds model-generated, evidence-grounded prose. The stage-level
policy still owns persistence and any broader writing workflow.
The existing synthesis artifact writer uses this facade for its structured
evidence derivation, while legacy artifact paths remain unchanged. Compact
persisted packs contain card references, so callers should hydrate the card
rows before invoking the boundary.
For a session-owned attempt, `run_synthesis_capability()` persists the complete
bounded direction handoff as `synthesis_result.json`. It accepts an expanded
pack supplied by the caller and does not read private stage paths or decide
whether an experiment should run.
`SynthesisResult.from_handoff_dict()` restores that `synthesis_result.v1` handoff
without network or LLM access, including its idea rows, novelty checks, and
optional research-level experiment contract.

The research-level `ExperimentContract` in `research.contracts` describes a
grounded hypothesis and proposed change. It is distinct from the execution
contract with the same historical name in `experiment.contracts`, which carries
command, metric, resource, dependency, and implementation settings. New code
should import from the module matching the contract's responsibility.
`ResearchExperimentContract.from_row()` restores the research-level handoff,
and `ExperimentRequest` accepts either that typed object or the historical
mapping form; canonical execution results preserve the contract without
merging it with the legacy execution contract.
In the vertical fixture, the restored contract is passed into the explicit
`ExperimentRequest`, so the execution result records the research-to-experiment
handoff rather than reconstructing the hypothesis from a private stage path.
If a typed research contract is supplied without an execution result schema,
its declared metric names form a minimal expected-metric view for downstream
analysis; an explicit execution schema always takes precedence. Historical
mapping inputs retain their previous behavior.
When that handoff should feed the standalone Experiment boundary, use
`experiment_request_from_synthesis()`. It restores `synthesis_result.v1`,
transfers only its existing research-level contract, and requires the caller
to provide an explicit `RunRequest`; it does not approve `needs_review`, choose
a command, execute, retry, or select a next stage merely because a contract is
present.

### Composing A Research Brief

`research.brief.build_research_brief()` is a small in-memory convenience: it
calls the Read boundary and passes the resulting evidence cards to the
Synthesis boundary. It accepts a Search-produced `DocumentBundle`, cached
documents, or a local-document bundle and returns `ready`, `partial`,
`needs_review`, or `empty`. Metadata-only input is not reported as sufficient
evidence. It does not search or write files; synthesis is deterministic by
default, while `ResearchBriefRequest(use_llm=True, llm_client=...)` explicitly
enables the shared LLM for grounded prose.

The user-facing `research-brief` and `research-session` applications do not
hide this composition anymore: they persist separate `read-001` and
`synthesize-001` attempts. The aggregate helper remains available for library
callers and old `research_brief.v1` handoffs, but it is not another lifecycle
stage.

There is intentionally no default session adapter for this aggregate. A
session must persist the `read` and `synthesize` attempts separately; callers
that only need an in-memory value can use `build_research_brief()`. Historical
`research_brief.v1` files remain readable, but they are not a second executable
lifecycle.

For a multi-attempt composition, restore a persisted Read result with
`ReadResult.from_handoff_dict(..., bundle=...)` and use
`evidence_pack_from_read()` to form the minimal Synthesis input. Keeping the
bundle explicit is intentional: it preserves one owner for source text while
leaving selection and sequencing to the caller.
The same rule applies downstream: restore the persisted analysis handoff and
derive report sections from its observed result data before passing them to the
standalone report assembler. The assembler does not infer or invent analysis
values from an input reference.

### Reusing The Experiment Boundary

`research.experiment.ExperimentRequest` wraps the existing execution
`RunRequest` with optional result-schema, contract, artifact, comparison, and
guard metadata. `run_experiment()` accepts the existing `ExecutionBackend`
protocol, defaults to `LocalExecutionBackend`, and returns the existing
`RunResult` together with canonical normalized results. It does not write files
or decide how an experiment is analyzed. `run_and_analyze()` is the small
composition when a caller wants both operations: it copies an
`AnalysisContext`, adds the observed metrics and canonical execution record,
and delegates to the existing result-analysis service. It preserves failed and
timed-out executions as analysis inputs, performs no retry or repair, and only
persists analysis artifacts when an output directory is supplied. The code-task
implementation therefore remains a backend, not a second experiment API.
When the request carries primary or required metrics, the composition exposes
those requirements to analysis without requiring callers to duplicate them in
the context.

`research.experiment.run_experiment_capability()` is the opt-in session adapter
for execution. It registers under a caller-chosen name, writes the existing
canonical result as `results.json`, and stores the captured stdout/stderr under
the same attempt as declared `execution/stdout.txt` and `execution/stderr.txt`
artifacts. The canonical `passed`, `failed`, or `timed_out` status remains in
the result, while the raw streams remain available for diagnosis and alternate
downstream capabilities. Every non-passed execution maps to a failed
capability result, so the session layer cannot mistake a timeout for a
successful experiment. Analysis remains a separate capability and
`research.analysis.analyze_experiment_capability()` can consume the declared
result reference explicitly, writing a single `analysis.json` handoff with a
pointer back to the execution artifact. It returns `completed` only when the
analysis status is `passed`; missing evidence maps to `partial`, while explicit
failure and blocking remain visible to the session controller. Consumers that
cross a process boundary can use `AnalysisHandoff.from_handoff_dict()` to
restore the execution reference, observed execution status, and
`AnalysisResult` without rerunning or copying execution artifacts.
The adapter also reuses the existing result guard and diagnosis functions. It
writes `guard_report.json`, `diagnosis.json`, and a compact `diagnosis.md` in
the same attempt; a guard error fails the capability while the canonical result
keeps the underlying execution status. It does not retry, repair, or choose a
research transition.

### Reusing The Result-Analysis Boundary

`research.analysis.AnalysisRequest` and `analyze_results()` provide the
standalone analysis entry point. They reuse the existing metric normalization,
claim grounding, and audit implementation; deterministic analysis is the
default and persistence is opt-in through `output_dir`. The boundary does not
invent metrics, run code, or decide a research transition.

When a caller has two canonical execution results, use
`research.analysis.compare_experiment_results()` to produce the compact
`experiment_comparison.v1` mapping. It compares shared numeric metrics using
explicit directions (or directions embedded in the result schema), preserves
execution-status changes, and returns `inconclusive` when the required evidence
is missing or non-directional. The mapping can be supplied through
`ExperimentRequest.comparisons`; the helper does not retry, select a winner, or
choose a session transition.

`AnalysisResult.status` is the corresponding evidence-state summary. It is
derived only from an explicit canonical execution record, its guard, required
metrics, and explicit comparison verdicts: `passed`, `failed`, `blocked`,
`incomplete`, or `metric_below_target`. A standalone analysis without an
execution record remains `incomplete`; the status never chooses a retry or a
research transition. When persistence is requested, the same small handoff is
also written to `analysis_status.json`.

When an upper-layer workflow needs to pass an analysis outcome to the session
policy, use `research.decisions.transition_request_from_analysis()`. This pure
adapter only creates the existing `TransitionRequest`; it does not invoke
handlers, retry, choose a next capability, or add another decision schema.
Both typed results and persisted mappings are accepted for cross-process
handoff. Recovery policy remains with the caller and the core session budget.

### Reusing The Report Figure Port

`report.ports.FigureRenderer` is the small substitution point for report
visuals. `DeterministicFigureRenderer` wraps the existing SVG implementation
and remains the default used by the report service. A future image or chart
backend can implement the same render method and consume the existing
`ReportDocumentPlan`, `ReportFigureConfig`, and `ReportFigureResult`; it does
not need to change writer, citation audit, or report assembly code. Callers
that own report orchestration can pass another renderer to
`execute_report(..., figure_renderer=...)`; the pipeline entry point omits it
and therefore keeps the existing behavior.

### Using The Report Assembly Boundary

`report.capability.ReportAssemblyRequest` and `run_report_capability()` expose
the downstream report boundary for callers that already have section drafts.
The adapter reuses `assemble_report_sections()`, the existing heading
numbering policy, and the `FigureRenderer` port, then writes one attempt-local
`report.md`. Each renderer-reported figure is also declared as a local
`figure` output ref; the optional figure manifest remains an index for readers.
If a renderer reports a file that is not present, the capability returns
`partial` with a `missing` ref instead of claiming completion. It does not
choose an outline, call an LLM, revise prose, or audit citations; those remain
separate concerns. This makes the report-to-audit handoff explicit without
introducing a second writer implementation or changing the legacy report stage.

`report.audit.ReportAuditRequest` and `audit_report()` provide the corresponding
side-effect-free audit boundary. They reuse the existing citation, metric, and
claim checks; report writing and revision remain orchestration concerns.
`ReportAuditCapabilityRequest` and `run_report_audit_capability()` are the
optional session adapter: callers pass explicit report/body artifact refs plus
the typed report context and memory, and receive one `report_audit.json`
artifact in the attempt. A warning maps to a partial capability result and a
failed audit maps to failed; the adapter never retries, rewrites the report, or
searches for an implicit “latest” artifact.

## Adding A Canonical Capability

New V2.8 work belongs to the capability/session path, not to the frozen
eight-stage implementation. Add a capability in this order:

1. Define a typed request/result and its stable handoff schema in the owning
   domain package.
2. Keep domain behavior independent of CLI arguments, `Context`, and run-folder
   scanning; put external effects behind an explicit adapter or port.
3. Add the session adapter that writes one attempt-local artifact and maps
   domain status to `CapabilityResult`.
4. Register it in the relevant capability registry (for example
   `research.registry`) and compose it in an `app/` use case with explicit
   inputs, outputs, budget, and transition.
5. Add contract, application, failure/recovery, and CLI/example coverage as
   appropriate.

Canonical capabilities should use explicit artifact references and compact
   handoffs. `ctx.find_artifact(...)`, `Stage`, and `HANDLERS` are legacy
   compatibility mechanisms only. Modify `pipeline_stages/` only when keeping
   an existing `simple-ar run/resume` input/output contract; do not add new
   research behavior there.

## Adding An Experiment Template

Fixed script templates primarily live in `src/simple_ar/experiment/templates.py`.
Embedded 8-stage code-task templates live under
`src/simple_ar/experiment/code_task_bridge/` because they prepare an existing
workspace before writing the run harness. The former
`src/simple_ar/experiment/code_task_experiment.py` facade has been removed;
new code and compatibility adapters should import from `code_task_bridge`.

Use `src/simple_ar/experiment/runner.py` for fixed generated-template
subprocesses. Use `src/simple_ar/code_task/` for LLM-guided project editing,
workspace isolation, patching, validation, and benchmark comparison.

`experiment.execution.backend.RunResult` is the canonical subprocess result
model. `experiment.runner.ExperimentRunResult` remains only as a compatibility
alias, so new execution and analysis code should depend on `RunResult`.

New LLM usage rows also record the number of provider attempts used by a
successful `ask()` request. Usage summaries expose the derived retry count;
older rows without this field remain readable and are treated as one attempt.

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

## Extending Report And Audit

The report system is the V2.4 outlet for research-only surveys, experiment
reports, and embedded code-task results. Keep it template-driven and
evidence-aware rather than turning it back into a single prompt or a single
large service file.

```text
src/simple_ar/report/
  schema.py        Pydantic models for context, memory, tools, drafts, reviews
  context.py       collect papers, synthesis, metrics, and code-task comparison
  templates.py     load Markdown templates and reviewer criteria
  memory.py        compact section plan, evidence handles, claims, limitations
  tools.py         report tool schema definitions
  tool_gateway.py  bounded read-only tool execution
  retrieval.py     source-handle backtracking
  agent.py         Writer/Reviewer orchestration
  citations.py     citation key mapping, display labels, and citation cleanup
  audit.py         citation, metric, claim, and reviewer audit aggregation
  assembler.py     section drafts to final Markdown
  quality.py       deterministic report quality checks
  service.py       stage entrypoint and artifact packaging
```

When adding report behavior:

- put schemas in `schema.py`, not ad hoc dictionaries in `service.py`;
- put new source lookup or backtracking logic in `context.py`,
  `retrieval.py`, or `tool_gateway.py`;
- put Writer/Reviewer loop behavior in `agent.py`;
- put citation mapping, display conversion, and citation cleanup in
  `citations.py`;
- put mechanical checks in `audit.py` or `quality.py`;
- keep templates and criteria in `templates/report/`, not hard-coded prompt
  strings;
- keep `service.py` as the stage-level coordinator and artifact writer.

`report/service.py`, `pipeline_stages/research.py`, and `cli/main.py` are still
large enough to be treated as yellow lights. Do not add unrelated behavior to
them. New work should either move logic into the owning domain module or reduce
these files. This is a maintenance rule, not a demand to split every small
helper into a separate file.

## Extending Tools And External Agent Backends

V2.6 introduces a common tool and handoff layer without replacing the existing
domain implementations:

```text
src/simple_ar/tools/
  specs.py        CommonToolSpec, ToolCall, ToolResult, permission/risk enums
  registry.py     compose report and experiment tools into one registry
  gateway.py      permissioned local dispatch and compact trace writing
  permissions.py  read/write/execution/network policy checks
  openai_schema.py / mcp_schema.py
                  schema export only; no server is started by default
  mcp_server.py   explicit stdio MCP server for read-only run-local tools

src/simple_ar/agent_backends/
  base.py         AgentBackend protocol and run result models
  policy.py       external-agent permission policy serialized into handoff
  handoff.py      workspace-scoped handoff package and untrusted output ingestion
  factory.py      provider selection for fake/local_llm/Codex/Claude/OpenCode
  fake.py         deterministic backend for integration tests and dry-runs
  local_llm.py    LLM-backed bounded reviewer/planner backend
  external_cli.py subprocess wrapper with cwd, timeout, env allowlist, and logs
  profiles/       Codex / Claude Code / OpenCode profile Markdown
```

The common layer is intentionally thin. `experiment/tools/` and
`report/tool_gateway.py` still own their business logic; `tools/` only provides
one audited surface for future OpenAI tool calling, MCP adapters, and external
agent backends.

Rules for new tool/backend work:

- register real tools only; do not add MCP/OpenAI schemas for stub tools;
- keep write, shell, network, and secret access disabled unless a config and
  approval path explicitly enables them;
- write external-agent context into `agent_handoff/<name>/`, never into a
  user's global tool directory by default;
- treat external-agent outputs as untrusted. Ingest them under
  `agent_outputs/<name>/`, then route them through the existing patch,
  result-guard, report-audit, or code-task validation paths;
- keep external CLI providers opt-in. `fake` and `local_llm` are useful for
  tests and local review; `codex`, `claude_code`, `opencode`, and
  `external_cli` must stay disabled until the config explicitly allows them;
- keep trace rows compact by default. Raw prompts, raw outputs, or large
  payloads belong behind debug settings.

### Code-Task Environment Policy

The current code-task runner has workspace isolation through `copy`,
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
uv run simple-ar-checks core
```

The same runner can be called without the console script:

```bash
uv run python scripts/run_checks.py code-task
```

Recommended validation layers:

| Change area | Suggested check |
| --- | --- |
| Docs only | `git diff --check` plus manual link review. |
| Small parser, prompt, config, metric, or CLI changes | `uv run simple-ar-checks quick`. |
| Code-task internals, workspace, repo-map, patching, validation, runner, repair | `uv run simple-ar-checks code-task`. |
| Bundled code-task examples or benchmark examples | `uv run simple-ar-checks code-task-examples`. |
| Pipeline, stages, experiment templates, run config | `uv run simple-ar-checks pipeline`. |
| Literature, retrieval, evidence ledger, report generation, LLM adapter | `uv run simple-ar-checks research`. |
| Core capability boundary, registry, attempt store, and package example | `uv run simple-ar-checks core`. |
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

Run config and public example config loading tests:

```bash
uv run python -m unittest tests.test_run_config
```

## Git Hygiene

- Keep unrelated refactors out of feature commits.
- Do not commit `.env`, run outputs, caches, or private learning notes.
- Keep README concise; move detailed behavior into docs.
- Update `CHANGELOG.md` when user-facing commands, artifacts, or workflow behavior changes.
