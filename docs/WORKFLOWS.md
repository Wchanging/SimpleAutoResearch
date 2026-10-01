# Workflows And Artifacts

[中文版本](WORKFLOWS_zh.md)

This document explains what SimpleAutoResearch is doing internally: task-driven
capabilities, artifact ownership, recovery boundaries, and module boundaries. It avoids
duplicating the full artifact manual; for concrete commands and file trees, see
[Usage And Configuration](USAGE.md). For command flags, see
[CLI Reference](CLI_REFERENCE.md); for TOML fields, see
[Configuration Reference](CONFIG_REFERENCE.md).

## Task-Driven Execution And Recovery

### Describe and plot existing data (no model required)

```bash
uv run simple-ar start --kind data_analysis --goal "Describe my measurements" \
  --data-file ./observations.csv --value-column score --group-column method \
  --observation-unit "one run" --interaction autonomous --yes
```

Explicit columns and row semantics are required; IDs and metrics are not guessed.
Setup checks bounded input shape and selected column names before saving task files.
Unknown columns report the available names. This preview does not compute measurements
or certify numeric values; ingestion validates values and freezes the actual bytes.
`observations` (default) calculates count, mean and sample standard deviation per group/column.
For bars of existing means or other summaries use `--data-mode values`: labels must be unique,
values are not averaged again and error bars are not inferred. `--data-missing omit`
explicitly allows per-column omission with counts; otherwise missing values fail, not become zero.
Nonfinite/nonnumeric values, duplicate columns, malformed/empty input and nested JSON fail explicitly.

The same session/controller freezes the UTF-8 CSV/TSV or homogeneous JSON records and settings,
then emits `analysis.json`, `analysis.md`, copied data and editable SVGs. No search, model,
training or invented experiment status is involved. A completed calculation does not verify
collection, units, independence, significance or causality. Outputs contain copied data: review
sensitivity before sharing. Physical limits default to 20 MiB input and 100 SVG pages
(`--data-max-mb`, `--data-max-figures` override); excess pages fail rather than drop data.
Separate metrics have separate axes; categories are paginated without dropping data.
`--figure-width column|wide` uses generic 3.5/7-inch targets, not venue-specific dimensions.
Visual checks are initially `not_performed`, separate from numeric computation.

For numeric curves or coordinate pairs, use `--data-mode values --data-plot line|scatter
--x-column step` instead of a grouping column. Repeat `--value-column` for separate
axes; `--x-unit` records the declared x unit. Lines sort by numeric x and require
unique x coordinates; scatter retains duplicate x values. Missing x fails;
explicit `--data-missing omit` keeps missing y records and breaks lines at those gaps.
There is no smoothing, regression, replicate aggregation or guessed error bar.
`--data-max-points` defaults to 10000 rows per coordinate figure and is adjustable;
overflow fails without sampling. The same copied input, resume and rebuilding apply.
See the complete [coordinate case](../examples/data-curves/README.md).

Resume with `research-session --session-root PATH` even if the original data changes/disappears;
completed input snapshots are reused. Copy the completed analysis directory, then rebuild figures
there with `python -m simple_ar.result_analysis.table analysis.json` using an installed package.
The rebuilder refuses changed computed records; it is not a full file-integrity certification.
Supply the completed `analysis.json` as `--material` in a later writing task to
recheck the values and attach its editable figures. Keep its copied `input.csv`,
`input.tsv` or `input.json` beside it; do not submit a raw table as writing JSON.
Complex statistics are not inferred.

Server numerical acceptance: `python scripts/validate_table_nist.py --output-root runs/nist-NEW`.
It compares two official NIST StRD datasets with certified mean/sample-standard-deviation values
and an independent Decimal calculation. This tests arithmetic and CLI delivery, not an agent,
paper-reproduction or complete NIST benchmark score. Official data is fetched once and retained;
if server download is blocked, put the unmodified public `.dat` files in that output's `inputs/`.

### Write from existing material

```bash
uv run simple-ar start --kind writing --goal "Explain my results and limitations" \
  --material ./notes.md --interaction autonomous --yes
```

Repeat `--material` for drafts, notes or result descriptions; `--document` identifies
a separate bibliographic paper. Writing extracts and persists the supplied text, then
uses the shared Writer, Reviewer, assembly and audit capabilities. It does not search,
create innovation candidates, execute experiments or manufacture an empty synthesis.
Inputs are Markdown/text/PDF or a completed `table_analysis.v1` analysis package.
Template section bodies supply the corresponding section's writing requirements,
not just its heading. Material-based writing keeps supplied material visible alongside
papers within the source budget; more references do not replace the task's own results.
For data-backed writing, pass the analysis task's printed `analysis.json` path
to `--material` (optionally alongside notes). The system checks the saved records
against the package-local copied data, freezes that package and regenerates its
native SVGs instead of trusting supplied figure paths or executing scripts.
Malformed packages, missing data, escaping paths and stale values fail before writing.
Writing imports are limited to 20 MiB per JSON/input file; standalone figure
rebuilding retains the original analysis's configured input limit.

The report contains an exact descriptive-value appendix plus a relative-path
`analyses/` directory with copied data, analysis JSON/Markdown and editable SVGs.
Copy the whole report directory to retain those links and rebuildable figures.
`report.figures.enabled = false` or `mode = "off"` omits figures from the report,
not the evidence package; an explicit total figure limit fails instead of dropping figures.
Review copied data for privacy before sharing. Arithmetic rechecking does not
verify collection, semantics, independence, significance or scientific validity;
these are not experiments independently repeated by this session.

The default is a concise analysis report. Add `--template experiment` for a
paper-style draft with explicit evidence gaps. It does not authorize experiments or
guarantee scientific quality. Local source metadata can be incomplete; do not rely on
generated bibliographies without checking them. Template/review files are checked at setup.
Expert custom templates must include the corresponding review criteria; use the
research TOML report settings for explicit criteria paths.

Resume the printed session with `research-session --session-root PATH --model env`.
Persisted extraction is reused, including after a writer failure; editing an original
file does not silently replace the saved evidence. This is task-scoped input reuse,
not a general cross-session memory service.

`start` saves regular task inputs and delegates to `research-session`; it is
not another planner or lifecycle. Preparation alone makes no model/process calls.
Reading-to-synthesis preserves limitations, open questions, confidence and refs,
labels omitted card rows, and does not silently truncate user execution constraints.
This does not certify semantic understanding.
For supplied-source reviews and prepared reproduction, synthesis summarizes
evidence without inventing innovation candidates. An explicitly requested
candidate assessment/design still uses research synthesis. Reading notes remain
model interpretations; report Writer, section Reviewer and document Reviewer receive
the same bounded, identified source passages and source-access status. Truncation
is explicit, and absence from an excerpt is not absence from the paper.
Reviewers can locate omitted passages with `search_source_chunks` over the registered
source's saved extracted text, then request neighboring chunks. This is bounded lexical
retrieval, not semantic verification or a fresh download; an unmatched query is not proof
that the original source lacks the information. A passing review that also requests
evidence is provisional: the fetched evidence is saved and the same draft is rechecked
once before acceptance. Still-pending requests become an explicit source-verification
gap, including when backtracking is disabled or exhausted. Section and document reviews
use the existing tool/revision budgets; recent tool results take priority in bounded prompts,
with omissions recorded. Resume reconstructs consumed tool calls from saved results instead
of resetting the allowance. No-request reviews do not gain an extra model call.
Writer and section Reviewer share a bounded view of adopted section prose and the
frozen section responsibilities. Format recovery retains this view; resume reconstructs
it from saved sections rather than another summary store. Excerpts include head/tail
positions and omitted character counts. They help coherence, not source verification.
The claim view follows current adopted drafts, preserving separate declarations even
when two sections reuse a claim id; rejected revisions do not replace current prose.
Revision verification receives the original findings, requested changes and a bounded
view of the previous draft, alongside source evidence. Removing unsupported or repeated
prose is allowed; preserving supported facts and qualifications matters more than preserving
word count. Checkpoints retain correction requests and editor candidates: resume reuses
them and consumed correction/source-call allowances rather than generating a fresh allowance.
These checks are model-assisted, not certification of every important claim or paper quality.
Whole-document editing can select at most two section targets. Each target uses
the existing `max_review_iterations` allowance, including rejected candidates.
Saved iterations preserve consumed allowance and original findings/instructions;
resuming cannot create a third target or reset correction attempts.
Known publication dates, DOI and author-list coverage notes survive document handoff
and share one metadata projection for writing, references, BibTeX and `citation_map.json`.
Missing details are displayed rather than inferred. Provider metadata is not independent
identity/version verification: same-title records and complete-looking fields can still be wrong.
Extracted text before the first recognized heading is retained as front matter;
Bounded chunk budgets prioritize body evidence and may leave front matter out of indexed chunks.
Reading a byline does not automatically replace metadata.
`get_paper_brief` returns recorded metadata alongside saved front matter, even if
the indexed chunk cap omitted that header. It does not open live file paths or
guess missing headers in old saved bundles; truncation and missing text stay explicit.
For a filename-only local source, a reading note's title proposal can become the
report display title only if it occurs in that same source's saved, bounded front
matter. Original records are unchanged. The citation map exposes title provenance;
unknown authors/date/DOI remain unknown. This is not identity/version validation.
Reading notes receive the user task focus separately from source evidence. Default excerpts
retain an ingest-sized chunk; smaller windows mark further clipping. Note identities and
declared references are checked against their owning document. This prevents misattribution,
not semantic errors, and does not turn a bounded overview into full-text verification.
Report inputs also retain the original task and the recorded source-access mix
(parsed text versus metadata/abstract only). An explicit report citation cap
does not truncate search or reading candidates; it is checked on the final body. Unresolved major
factual reviewer findings or a violated explicit cap fail the report audit;
the failed audit remains inspectable and delivery pauses. Style warnings do
not become scientific failures, and a passing mechanical audit is not a
certificate of semantic support.

Report diagrams require labels actually present in the section: no generic
filler or inferred arrows. Paired figures keep input metric order (at most four
by default), without favoring continual-learning metrics. Rendering is not
scientific validation.

The formal research entrypoint is `research-session`. It owns attempts, artifacts,
reports and audits in one session, while `ResearchApplication` selects only the
capabilities justified by the task, supplied assets and accepted execution
constraints. There is no user-facing fixed stage sequence.

```text
task + assets + constraints
  -> short accepted plan
  -> capability execution and observed artifact
  -> application decision: next action, revision, delivery, or stop
  -> explicit recovery/reload from persisted refs without repeating valid work
```

The application owns plan acceptance, capability order, comparison decisions and
delivery choices. Each capability owns its input contract and attempt output;
`SessionController` owns attempt, budget, lineage and artifact persistence. A
resume reads those facts and reconstructs the next accepted action; it does not
replay completed side effects or let the core invent a domain-specific stage.

Public entrypoints remain intentionally small:

```text
simple-ar research-session       # canonical task-driven entry and recovery
simple-ar research-brief         # compatibility request/result adapter
simple-ar status RUN_DIR         # read-only archive display
```

## Capability Runs

Alongside the task-driven entrypoint, `simple_ar.core` provides an opt-in boundary
for new replaceable capabilities. A capability receives declared input
references through `CapabilityContext`, writes outputs through an
attempt-scoped `ArtifactStore`, and returns a `CapabilityResult`. The
`SessionController` can persist a bounded attempt and its decision without
turning the application into an unrestricted task graph.

This boundary is compositional: it does not schedule arbitrary actions or
change the artifact paths expected by existing commands and adapters. The
offline reference package in `tests/fixtures/capability_package_minimal/` shows the
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
`SessionController.execute_attempt()` for each explicit capability in the chosen
sequence. It persists execution facts; the application decides whether to stop,
continue or complete the research. A resumed process should inspect status and
attempt lineage, and explicitly construct the next call; the core does not
silently rerun an interrupted attempt or choose a domain-specific best result.
When an interruption has been manually confirmed, the caller can use
`SessionController.recover_interrupted()` to close the stale running attempt as
an explicit failure before constructing a retry or repair attempt. The method
does not retry or overwrite an existing result envelope.
The controller rejects any new attempt while a prior attempt is still marked
`running`, so recovery cannot accidentally create a second active branch.
Core validates capability scope, budget and input artifacts before creating a
new attempt. Research sequencing belongs to the application, not a second
fixed transition recipe in the execution boundary.

Use `SessionController.attempt_output_refs()` to pass declared outputs from a
completed or failed attempt to a later capability. Attempt-local paths are
converted to session-root references without copying or merging artifacts;
the caller still decides which attempt and which output to use.
When an alternative should start from an earlier completed or failed attempt,
pass its ID as `parent_attempt_id` to `SessionController.execute_attempt()`. The
controller validates and records that parent. Without this argument, attempts continue from the
current attempt as before; the controller does not infer branches or select a
winner. Use `attempt_lineage()` to inspect the root-to-node chain for a
comparison or recovery view; it reads only persisted attempt manifests and
does not merge artifacts or schedule work.

Historical research-session readers expose the stored `next_capability` and
execution evidence. They do not recompute a next-step recommendation with a
retired policy; new research decisions belong to ResearchApplication.

For a library caller that wants one in-memory value, use
`research.brief.build_research_brief()` is an in-memory compatibility view only.
The default registry deliberately does not expose a composite `research_brief`
capability: sessions persist `read` and `synthesize` separately. Historical
`research_brief.v1` handoffs remain readable, but they are not a second
executable lifecycle.

`simple-ar research-brief` is a compatibility request/result adapter over the
same `ResearchApplication` used by `research-session`, not another orchestrator.
It requests a literature summary and uses the canonical lifecycle and default
request/token budgets. Its research capabilities are:

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
  --local-document tests/fixtures/research/reliable_agents.md \
  --output-root runs/research-brief
```

The command creates a timestamped v2 session directory. Each handoff remains in
its own dynamically named attempt. Read `session_manifest.json.state_refs` for
the actual paths; use the CLI's printed `Synthesis handoff` path for downstream
commands instead of constructing an attempt ID. The canonical outputs are `research_plan.json`,
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

The segmented `research-experiment` creator is retired. Experiment and analysis
capabilities remain shared by the formal application, without a separate session lifecycle.

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

When neither an execution command nor `--code-task-config` is supplied, the same
entrypoint remains a literature-only composition. It ends at the evidence-backed
summary without creating an execution request; with a model, it can continue to
the research-only report path. This is the supported no-experiment form, not an
implicit placeholder experiment.

If that session ends with a failed experiment but retains its design and
analysis handoffs, one explicit recovery can reuse the same literature and
design without rebuilding them:

```bash
uv run simple-ar research-session-continue \
  --session-root runs/research-session/<session> \
  --cwd tests/fixtures/research \
  --primary-metric accuracy \
  --metric-direction accuracy=higher \
  --command python -c "print('accuracy: 0.90')"
```

For a canonical `session_manifest.v2`, this creates a new dynamically named
experiment attempt whose parent is the failed candidate, then reuses the
existing literature and design for deterministic analysis. The revised command
is supplied by the caller; search and design are not repeated. The original
attempt remains intact. This boundary accepts only a simple explicit technical
failure; paired, prepared-data, and CodeTask sessions keep their dedicated
bounded recovery paths. A scientific negative result is evidence and is not
silently retried. Legacy `session_manifest.v1` sessions retain the fixed
`experiment-002`/`analysis-002` compatibility behavior.

The canonical session can continue through `research-report` after `--no-report`.
It reuses persisted evidence and measurements, adding only missing report actions.
Writer execution and checkpoints belong to `report/writing.py`; assembly and audit
remain separate canonical capabilities in the same application lifecycle.

Writing and both review levels distinguish declared protocol, recorded execution,
and independent implementation checks. Where the registered document bundle is
available, reviewers can request bounded passages around a cited chunk; cached
excerpts alone are not reported as fresh source reading. Revision traces retain
candidate text and whole-document adoption decisions even when verification fails.
Inspect unresolved findings before treating a generated report as a checked paper.

An assembled report can be exported independently with `report-export`:
the canonical citation-key body and bibliography become an editable ACM
manuscript. Export reuses the saved text and figures; it does not repeat reading,
writing or measurements. Rendering/compilation status is separate from report
audit status. See the [command reference](CLI_REFERENCE.md#simple-ar-report-export).

The pdfLaTeX demonstration handles common scientific Greek/math Unicode glyphs
through fixed export-owned declarations, without changing the original Markdown.
This is not general multilingual font support. Unsupported characters or missing
TeX packages retain the editable project and compilation diagnostics rather than
silently changing the content or claiming PDF delivery.

Historical sessions remain inspectable, but no second Writer/report/audit executor
resumes them. `build_research_session_report_inputs()` and
`build_code_task_report_inputs()` only project existing evidence without executing
or modifying the session. The segmented `research-code-task` creator is retired;
use `research-session --code-task-config` for complete tasks.

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
The application consumes the analysis evidence directly and owns the next action.

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

### 1. Research Report (Literature-First, Segmented/Advanced)

Use this when you want a literature review, survey, or DeepResearch-like report without emphasizing experiments.
For an ordinary complete research task, use `research-session`; this section describes a reusable
capability boundary.

Conceptual flow:

```text
plan -> search -> read -> synthesize -> report
```

Without an execution command or CodeTask configuration, `research-session`
uses the literature-only path and does not start an experiment. Reports belong
to the same lifecycle; `--no-report` explicitly omits that delivery.

### 2. Code Task (Existing Codebase)

Use this when you already have code and want a focused modification, optimization, repair, or benchmark improvement.

Conceptual flow:

```text
init workspace -> index code -> map repo -> probe environment
-> apply baseline policy -> build context pack
-> plan patch -> approve -> propose edits -> apply edits
-> review changes -> validate -> run patched benchmark -> post-run review
-> compare results
-> analyze failure -> repair proposal
```

Key boundaries:

- Ordinary `execute` uses one patch plan. Decomposition is opt-in through
  `execute --to-step work-plan` / `--to-step batch` or an existing work plan.
  Interactive execution follows the same rule. Explicit batch workflows retain
  their scope, approval, completion and repair history.
- The source project is prepared under `code_task/workspace`; existing-project
  runs default to `auto`, which prefers a detached `git_worktree` for committed
  Git projects and falls back to a guarded `copy` with recorded next-step hints.
  For monorepos, the worktree is created at the repository root and the matching
  project subdirectory becomes the editable project root. Experimental
  `sparse_copy` copies only configured include patterns and always excludes
  data/model/cache/secret-like paths. The original code is never modified.
- Patch application is gated by an explicit human approval step.
- Edit proposals are conservative old/new replacements, not free-form rewrites.
- Controlled patch proposals and application call their implementation directly.
  The duplicate editor-adapter layer has been retired; artifact metadata retains
  `controlled_patch` for provenance. External Harness integration is later work.
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

- `scripts/research_session_smoke.py`: canonical research workflow smoke;
  literature-only reports use the same application, not an old pipeline config.
- `examples/code_task_medium_review/`: standalone code-task workflow over a
  multi-module review classifier with a `main.py` entrypoint, JSON config,
  visible progress output, and a task that naturally touches feature extraction,
  model scoring, and configuration.
- `examples/code_task_digits_mlp/`: standalone coding on a lightweight NumPy
  MLP benchmark, useful for real local CPU measurements without GPU.

### 3. Research With Experiment

Use `research-session` for a research task with an explicit execution command or
`--code-task-config`. The application owns the research lifecycle; CodeTask
implements changes in the prepared workspace, while the experiment capability
owns measurements.

Conceptual flow:

```text
plan -> search -> document ingest -> read -> synthesize -> design
-> prepare/implement when requested -> experiment -> analysis -> report -> audit
```

- Research inputs and constraints are passed to CodeTask through the research
  handoff. Ordinary modification uses one approved patch plan; explicit or
  existing work plans retain their batch workflow.
- Implementation produces frozen patch, validation, review and plan evidence.
  These checks do not themselves establish a scientific improvement.
- Experiment results and comparisons keep their execution references. A failed
  process is not a valid measurement; a valid negative result is not an
  instruction to repair forever.
- Report generation uses the recorded literature, implementation and measurement
  evidence. The audit checks that a metric name and value appear in the same
  prose line or table row, and compares framework-rendered experiment tables
  with persisted results; altered baseline/candidate cells fail the audit.
  This is a consistency check, not independent validation of the measurements.
  Mechanical audit success is not proof of publication quality or semantic
  correctness (`semantic_review_status` remains `semantic_unchecked`).
- Terminal delivery shows the latest measured candidate's method-evidence status
  separately from code validation and report audit. Paired runs cross-check each
  candidate measurement against the collection's implementation reference;
  mixed or incomplete lineage is shown as unavailable. `not independently checked`
  is an evidence limit, not a failed experiment or an implicit method approval.
- Resuming report work must not rerun experiments that have completed. See the
  session commands above and `scripts/research_session_smoke.py` for the entry
  point; the offline smoke is a fixture, not real scientific validation.

## Historical Eight-Stage Artifacts

The old eight-stage executor has been removed. `run`, `resume`,
`research-code-task` and `research-experiment` reject execution and point to
`research-session`; old template flags do not select a supported workflow.

Existing stage-shaped archives are not deleted or rewritten. `status RUN_DIR`
can display their recorded manifest and pipeline state. The private
`_legacy.documents.load_search_document_bundle(search_dir)` reads an archived
Search directory without creating a runtime context. Historical artifact names
describe stored evidence, not a second executable pipeline.

## Search And LLM Boundaries

Search retrieves records and records provenance. Document ingest handles local
or permitted remote text; Read selects and analyzes evidence, Synthesis combines
it, and Design proposes an experiment. Missing full text remains an explicit
limitation, not a claim that the paper was read in full.

Paper notes use a bounded combination of section overview and lexical matches
to the task, searched across retained substantive chunks. Matching excerpts
include nearby same-source context within the existing excerpt budget. No
embedding index, extra model round or task-specific paper rule is required.
Each new note records `reading_coverage`: available chunk count, shown chunk
IDs and an explicit `semantic_verification = not_performed` marker. A match
helps locate evidence; it does not prove a claim or establish that no contrary
passage exists. Unmatched or differently worded questions still need targeted
source reading, and source acquisition/extraction failures remain limitations.

Model notes may request up to two specific followup queries. The reader searches
only that source's retained text, with one round and at most six bounded windows
per document (including neighbors). New text permits one note-revision call;
different hits share the window budget across questions, with omitted candidates
recorded rather than interpreted as absence from the source.
no request or no new window adds no call. Queries, original excerpts and unresolved
questions reach synthesis and writing; superseded interpretations remain in the
read artifact, not the current synthesis input. No match is not evidence of
absence. Unresolved requests keep the result partial, without recursive searches
or downloads. Recovery uses the existing read attempt, not a new per-paper API
checkpoint; a failed read may repeat its calls when resumed.

LLM-generated ideas and local novelty checks are research suggestions, not proof
of originality. Offline fixture output is not model-backed scientific analysis.
See the capability entrypoints above for typed inputs and outputs.

## Artifact Ownership Summary

- `session_manifest.json` records session and attempt state; the application
  chooses research actions, and the shared budget ledger records usage.
- Each `attempts/` directory owns its declared artifacts. References, rather
  than fixed stage numbers, connect search, reading, synthesis, implementation,
  experiments, analysis, writing and audit.
- Implementation freezes the patch and checks used for the measured revision.
  Experiment execution owns observed metrics; analysis interprets them.
- Report writing, assembly and audit retain their own attempt artifacts. A
  completed process, a completed workflow and a publication-quality paper are
  different claims.
- Historical numbered stage directories remain archives only. Rebuildable caches
  are not the authority for results and must not replace source artifacts.

## Code Task Artifact Boundaries

Standalone code tasks and research-session implementations use the same conceptual
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

## Why Split Capabilities Internally

Internal capability boundaries do not mean exposing parallel product mainlines.
They keep the implementation from becoming one rigid pipeline while keeping
the formal entrypoint simple.

- If the user wants a survey, code stages should be skipped.
- If the user wants to optimize existing code, literature stages should be optional.
- `research-session` can include a prepared code experiment while keeping a
  bounded lifecycle boundary.
- Tests, recovery, developers, and future workflows can compose modules without
  making ordinary users understand the internal assembly.
- Each module can be upgraded independently, but there must not be a second
  session state, artifact contract, or report core.

This follows one practical lesson from AutoResearchClaw: complex behavior is easier to control when it is exposed as workflow modes and capabilities, not as one ever-growing sequence of flags.
