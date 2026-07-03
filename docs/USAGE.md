# Usage And Configuration

[中文版本](USAGE_zh.md)

This document explains how to install, configure, and run SimpleAutoResearch.
It is the practical user guide; workflow concepts and artifact details live in
[Workflows And Artifacts](WORKFLOWS.md), command details live in
[CLI Reference](CLI_REFERENCE.md), and TOML fields live in
[Configuration Reference](CONFIG_REFERENCE.md).

## Requirements

- Python 3.12 or newer.
- `uv` for dependency management.
- An OpenAI-compatible API key if you want LLM-backed planning, notes, synthesis, report writing, or code edits.

## Installation

Clone the repository:

```bash
git clone https://github.com/Wchanging/SimpleAutoResearch.git
cd SimpleAutoResearch
```

Install dependencies:

```bash
uv sync
```

Check the CLI:

```bash
uv run simple-ar --help
```

## Environment Configuration

Create a local `.env` file:

```bash
cp .env.example .env
```

On PowerShell:

```powershell
Copy-Item .env.example .env
```

Supported settings:

```bash
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.openai.com/v1
SIMPLE_AR_MODEL=gpt-4o-mini
SIMPLE_AR_LLM_BACKEND=openai
SIMPLE_AR_LLM_API=responses
SIMPLE_AR_LLM_TIMEOUT_SEC=
SIMPLE_AR_MAX_OUTPUT_TOKENS=
SIMPLE_AR_LLM_RETRY_ATTEMPTS=3
SIMPLE_AR_LLM_RETRY_BASE_DELAY_SEC=1
SIMPLE_AR_LLM_RETRY_MAX_DELAY_SEC=12
SIMPLE_AR_JSON_RESPONSE_FORMAT=off
SIMPLE_AR_INPUT_PRICE_PER_1M=
SIMPLE_AR_OUTPUT_PRICE_PER_1M=
```

Notes:

- `OPENAI_API_KEY` is required for LLM mode.
- `OPENAI_BASE_URL` can point to OpenAI or a third-party OpenAI-compatible `/v1` endpoint.
- `SIMPLE_AR_MODEL` is the default model when `--model` is not supplied.
- `SIMPLE_AR_LLM_BACKEND` controls the transport implementation. The default
  `openai` uses the OpenAI Python SDK directly; `litellm` keeps the older
  LiteLLM compatibility layer.
- `SIMPLE_AR_LLM_API` controls the request shape. `responses` sends Responses
  API-style `instructions` plus `input` and automatically falls back to Chat
  Completions-style `messages` for transient transport disconnects; `chat`
  sends Chat Completions-style `messages` directly.
- `SIMPLE_AR_LLM_TIMEOUT_SEC` is optional. Leave it empty, or set it to
  `0`/`off`/`none`, to omit a client-side timeout; set a positive value only
  when you deliberately want to bound request time.
- `SIMPLE_AR_MAX_OUTPUT_TOKENS` is optional. Leave it empty, or set it to
  `0`/`off`/`none`, to omit provider output-limit parameters; set a positive
  value only when you deliberately want to bound response size.
- `SIMPLE_AR_LLM_RETRY_ATTEMPTS` and the retry delay settings control bounded
  exponential backoff for transient provider errors such as connection resets,
  rate limits, timeouts, and 5xx responses.
- `SIMPLE_AR_JSON_RESPONSE_FORMAT` controls provider-native JSON mode for
  structured calls. The default `off` uses prompt-only parsing for broad
  provider compatibility. `auto` tries `response_format={"type":"json_object"}`
  and falls back if the provider rejects it; `json_object` always sends it.
- Price fields are optional and only affect cost estimates in usage summaries.

## Research Pipeline (Topic To Report)

Run the default 8-stage pipeline:

```bash
uv run simple-ar run --topic "toy topic" --to-stage report
```

For repeatable multi-option runs, use a top-level TOML config:

```bash
uv run simple-ar run --config examples/full_pipeline_tiny_mlp/configs/pipeline.toml
```

The config can provide `[run]`, `[llm]`, `[search]`, `[research]`,
`[retrieval]`, `[experiment]`, `[report]`, and the same
`[code_task]`/`[benchmark]`/`[metrics]` sections used by
`code-task init --config`. Explicit CLI flags override config values. See
[Configuration Reference](CONFIG_REFERENCE.md#complete-pipeline-config) for a
complete commented config and field-by-field explanation.

Stop early for a literature-only pass (no experiment code/run artifacts):

```bash
uv run simple-ar run --topic "toy topic" --to-stage synthesize
```

Then generate a literature-only report from the existing artifacts:

```bash
uv run simple-ar resume runs/<run-id> --from-stage report
```

If the current report is worth keeping, generate a separate report package
instead of replacing `08-report/report.md`:

```bash
uv run simple-ar resume runs/<run-id> --from-stage report --to-stage report \
  --report-output-mode variant --report-output-label survey-v2
```

This writes the new package under
`08-report/variants/survey-v2/` and keeps the current `report.md` unchanged.
Use `--report-output-mode archive` when you prefer to overwrite the main report
but automatically copy the previous package to `08-report/archives/<label>/`
first.

By default, report drafting is automatic: if `results.json` is missing, the
report switches to a literature-only structure. You can force a mode:

```bash
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode research_only
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode experiment
```

#### Report Source Strategy

For small or medium literature sets, the practical default is to let the report
agent see all selected paper-level handles:

```toml
[report]
max_section_sources = 0
source_strategy = "full"
```

This does not place every full-text chunk into every prompt. Paper-level
handles carry titles, abstracts, short citation keys, and compact metadata;
the writer/reviewer can still request bounded full-text context through the
report tools when backtracking is enabled.

For larger candidate sets, use incremental drafting:

```toml
[report]
max_section_sources = 0
source_strategy = "batch_refine"
source_batch_size = 10
max_source_batches = 0
review_source_batches = false
```

`batch_refine` drafts each section from the first source batch, then revises
the same section as later batches arrive. Enable `review_source_batches = true`
only when you want stronger per-batch control and accept the extra LLM cost.

### What Is LLM-Backed vs Deterministic

- LLM-backed when enabled: `plan`, `read`, `synthesize`, and `report` stages.
- `05-design` writes a stable experiment contract, result schema, resource plan,
  dependency plan, and domain profile before any code is generated or patched.
- `06-code` has three bounded implementation paths: fixed templates,
  embedded code-task patches for existing projects, and greenfield project
  generation for tasks with no source project yet.
- Embedded code-task experiment: `06-code` can call the LLM for a work plan,
  patch plan, and controlled edit proposal, but the patch is applied only inside
  an isolated workspace under the run directory.
- Greenfield experiment: `06-code` can call the LLM for architecture/file
  planning and implementation; it always writes review, memory, backend, and
  artifact manifests before `07-run` validates canonical metrics.
- Guarded reports: if an LLM-written report omits required body citations, invents citation keys, or overstates fixture/toy evidence, the report stage writes a structured fallback report instead.
- `--no-llm` forces offline fallbacks with placeholder content in `goal.md`, `notes.md`, `synthesis.md`, and `report.md`.

### Search Modes And Boundaries

Default search behavior:

- `search` builds an internal research plan before querying providers; the
  `02-search/planning/research_plan.json` file is retained only when
  `[run].debug_artifacts = true`.
- Unless configured otherwise, `search` queries OpenAlex first, then Semantic
  Scholar, then arXiv.
- For each planned query, the current default is ordered fallback: once a live
  source returns candidates, downstream sources are skipped for that query to
  reduce rate-limit pressure and duplicate noise.
- If a live provider fails and `--strict-search` is not set, cached metadata is used when available.

Explicit search controls:

```bash
uv run simple-ar run --topic "agent simulation" --to-stage search --strict-search
uv run simple-ar run --topic "agent simulation" --to-stage report --allow-fixture-fallback
uv run simple-ar run --topic "agent simulation" --to-stage report --offline-search
```

- `--strict-search` disables cache fallback for live providers.
- `--allow-fixture-fallback` allows placeholder metadata when live providers and cache fail.
- `--offline-search` skips live providers and uses fixture metadata immediately.

For repeatable research-source settings, use the `[research]` section in a run
config:

```toml
[search]
offline = false
max_papers = 5
query = "agent simulation evaluation"

[research]
# lite: metadata/local notes; standard: cache/index-ready; strong: future full-text/vector path.
mode = "standard"
planner = "auto"
sources = ["openalex", "semantic_scholar", "arxiv"]
queries = ["agent simulation evaluation", "multi-agent simulation benchmark"]
auto_query_expansion = true
max_retrieval_rounds = 2
max_queries = 6
required_facets = ["method", "benchmark", "dataset", "code_link"]
use_fulltext = true
allow_pdf_download = false
max_fulltext_documents = 6
max_pdf_mb = 20
keep_raw_pdf = false
parser_backend = "basic"      # basic | pypdf | unstructured
read_screening = "auto"       # auto | llm | deterministic
read_batch_size = 4           # papers per abstract-level LLM screening batch
read_workers = 3              # concurrent screening batches
read_max_shortlist = 12       # papers sent to deeper Paper Briefs/synthesis
cache = true
index_backend = "sqlite_fts"  # keyword | sqlite_fts | hybrid | lancedb | hybrid_lancedb
# Shared accelerator-store root for SQLite FTS / LanceDB. Use "run" or "local"
# when you intentionally want per-run index databases.
index_root = ".simple_ar_cache/research_index"

[research.budget]
max_documents = 20
max_chunks = 200
max_context_tokens = 12000
max_llm_calls = 8
novelty_backend = "local"
```

The early research stages now keep stage ownership separated. A compact run
writes this layout:

```text
02-search/
  papers.jsonl
  search_meta.json
  documents/
    documents.jsonl
    cache_manifest.json
    fulltext_manifest.json
    fulltext_extraction.json
    extracted_text/  # only when HTML/PDF-like resources are parsed to text
  research_index/
    chunks.jsonl
    index_meta.json
03-read/
  review/
    screening_decisions.jsonl
    shortlist.jsonl
    reading_table.md
  paper_notes.json
  notes.md
04-synthesize/
  synthesis_brief.json
  synthesis.md
  hypothesis.md
05-design/
  experiment_plan.json
  experiment_contract.json
  experiment_contract.md
  result_schema.json
  resource_plan.json
  dependency_plan.json
  domain_profile.json
  contract_validation.json
  evidence/
    experiment_contract.json  # compatibility research handoff
    experiment_contract.md
```

When LLM mode is enabled, `03-read` uses a two-step review path for larger
retrieval sets: a concurrent coarse pass screens compact title/abstract batches,
then a smaller rerank pass assigns reading priority, evidence role, and a short
synthesis hint. The final decisions are still written to
`03-read/review/screening_decisions.jsonl`, and only kept papers flow into
`shortlist.jsonl`, Paper Briefs, notes, and synthesis. Set
`[research].read_screening = "deterministic"` when you want to skip this extra
LLM review and keep the retrieval order.

Set `[run].debug_artifacts = true` when you also want verbose diagnostics and
future-tool handoff drafts:

```text
02-search/
  planning/
    research_plan.json
  traces/
    retrieval_rounds.jsonl
    retrieval_selection.jsonl
  review/
    coverage_report.json
    coverage_report.md
  documents/
    sections.jsonl
03-read/
  cards/
    paper_cards.jsonl
    claim_cards.jsonl
    method_cards.jsonl
    dataset_cards.jsonl
    code_links.jsonl
04-synthesize/
  evidence/
    evidence_pack.json
    evidence_pack.md
    gap_summary.md
    idea_candidates.jsonl
    novelty_checks.jsonl
05-design/
  evidence/
    tool_context.json
    tool_context.md
    evidence_review.md
    decision_log.jsonl
    eval_report.json
    eval_report.md
  tools/
    tool_adapter_contract.json
    tool_adapter_contract.md
    tool_trace.jsonl
    external_agent_backend.md
  governance/
    artifact_retention_policy.json
    artifact_retention_policy.md
```

Shared accelerator stores are written outside the run by default:

```text
.simple_ar_cache/
  agent_handoff_archives/ # prior external-agent handoff transcripts
  literature/      # shared provider metadata cache
  research_index/
    sqlite_fts.db  # shared SQLite FTS rows, keyed by run_id
    lancedb/       # shared LanceDB store when enabled and installed
```

To clean rebuildable cache data for one run, use the top-level clean command:

```bash
uv run simple-ar clean runs/<run-id>
```

It first prints a Rich tree preview: red items will be deleted and green items
will be kept. Type `yes` to proceed. By default, `clean` removes bulky
run-local cache folders such as `02-search/documents/fulltext_cache/` and
`02-search/documents/extracted_text/`, plus this run's rows in the shared
SQLite research index. It keeps reports, manifests, normalized paper metadata,
parser audit files such as `fulltext_extraction.json`, read-stage Paper Briefs,
synthesis briefs, retained debug coverage reports when present, and portable
`research_index/chunks.jsonl`.

For a stronger cleanup that removes every known rebuildable cache and index for
that run, use:

```bash
uv run simple-ar clean runs/<run-id> --all-caches
```

This mode shows an additional red warning panel before confirmation. It also
removes artifact retrieval caches, run-local research indexes, code-task repo
maps, locate results, and context packs while keeping final reports, metadata,
manifests, and benchmark outputs.

To clear only the shared research index store across runs:

```bash
uv run simple-ar clean --shared-index
```

This previews and then clears the shared SQLite FTS / LanceDB accelerator store,
usually `.simple_ar_cache/research_index`. It does not delete any run directory
or run-local audit files, but cross-run index acceleration and cache hits are
lost until future runs rebuild the store. Use `--index-root PATH` when the
shared index lives elsewhere. Paths outside the current workspace require
`--allow-external-index-root`, because that can affect other projects.

For the strongest shared cleanup, clear the shared research index, the shared
literature-provider cache, and prior external-agent handoff archives:

```bash
uv run simple-ar clean --shared-cache
```

This usually removes `.simple_ar_cache/research_index/`,
`.simple_ar_cache/literature/`, and
`.simple_ar_cache/agent_handoff_archives/`. It does not delete run directories,
but future runs may need to re-query providers, rebuild local search
acceleration, and will no longer keep old external-agent handoff transcripts.

Key files, grouped by directory:

- Root of `02-search/`
  - `papers.jsonl`: normalized paper metadata passed to `read`.
  - `search_meta.json`: selected source, status, returned-paper count, and
    pointers to retained retrieval artifacts. Compact runs also keep a small
    `source_plan` copy here so downstream stages still know the active sources,
    full-text intent, index backend, and budgets after verbose planning traces
    are removed.
- `planning/` (debug-only)
  - `research_plan.json`: compact plan with `research_questions`, `query_plan`,
    and `source_plan`, including scoped sub-questions, seed/expanded queries,
    source order, retrieval mode, local document hints, cache/index preferences,
    and lightweight budgets.
- `traces/` (debug-only)
  - `retrieval_rounds.jsonl`: one row per source/query attempt, including
    status, returned count, errors/cache hits, and compact query intent.
  - `retrieval_selection.jsonl`: deduplication, lexical ranking, and
    budget-capping decisions for returned metadata. This is retrieval selection,
    not semantic paper review.
- `review/` (debug-only)
  - `coverage_report.json` / `.md`: required-facet coverage, missing research
    questions, and follow-up query decisions.
- `documents/`
  - `documents.jsonl`: normalized records for selected metadata and configured
    local files, with extraction status such as `metadata_only`, `parsed`,
    `skipped`, or `failed`.
  - `cache_manifest.json`: source counts, status counts, and full-text/PDF
    intent flags.
  - `fulltext_manifest.json`: full-text hints and fetch-budget decisions;
    remote fetch failures are recorded here without failing the search stage.
  - `fulltext_extraction.json`: best-effort parser results for cached/local
    full-text inputs. Markdown/text and basic HTML parse without extra
    dependencies; PDF parsing uses lightweight `pypdf`; optional `unstructured`
    can be selected with `parser_backend = "unstructured"`.
  - `sections.jsonl` (debug-only): conservative section-aware spans such as
    `abstract`, `method`, `experiments`, `results`, and `limitations`.
- `research_index/`
  - `chunks.jsonl`: portable local chunks built from abstracts and parsed or
    extracted full text. Section metadata is included when available.
  - `index_meta.json`: backend/run manifest and shared SQLite FTS / LanceDB
    store paths. Shared accelerators live under `.simple_ar_cache/research_index`
    by default instead of being copied into every run.
- `03-read/review/`
  - `screening_decisions.jsonl`: read-stage keep/drop/priority decisions for
    retrieved papers. LLM mode can drop or reprioritize papers; deterministic
    fallback keeps retrieved papers but records why each paper is eligible for
    structured reading.
  - `shortlist.jsonl`: compact reading shortlist used by Paper Briefs, notes,
    and synthesis.
  - `reading_table.md`: human-readable review table with coverage caveats.
- `03-read/`
  - `paper_notes.json`: canonical structured Paper Briefs. Each row records the
    evidence role, concise summary, method/dataset/metric hints, conservative
    claims, limitations, synthesis hint, possible experiment hooks, and open
    questions for one shortlisted paper.
  - `notes.md`: human-readable rendering of the same Paper Briefs.
  - `cards/*.jsonl` (debug-only): deterministic paper/claim/method/dataset/code
    hints retained only when `[run].debug_artifacts = true`.
- `04-synthesize/`
  - `synthesis_brief.json`: compact bridge from read-stage Paper Briefs to
    synthesis and design. It contains role counts, coverage/provenance,
    grouped themes, gaps, bounded idea candidates, local novelty-risk hints,
    and limitations without duplicating cards.
  - `synthesis.md` / `hypothesis.md`: human-readable synthesis and the
    experimentable hypothesis produced from the Paper Briefs.
  - `evidence/*.jsonl` / `.md` (debug-only): legacy evidence-pack diagnostics
    retained only when `[run].debug_artifacts = true`.
- `05-design/`
  - `experiment_plan.json`: selected execution/template plan for the next code
    stage.
  - `experiment_contract.json` / `.md`: V2.5 executable contract, including
    task kind, objective, implementation mode, source project, benchmark
    command, risks, constraints, and result/resource/dependency references.
  - `result_schema.json`: primary metric, required metrics, directions, and
    success criteria used by run guards and later reports.
  - `resource_plan.json`: runtime, file-count, generated-line, memory, GPU, and
    streaming budget before code is generated or patched.
  - `dependency_plan.json`: dependency-install policy, concrete expected
    entrypoints, profile candidate entrypoints, and setup-hook notes. It records
    policy; it does not install packages. Candidate entrypoints are advisory and
    should not be treated as files that must all be generated.
  - `domain_profile.json`: planning defaults for generic, existing-code, ML,
    or code-agent evaluation tasks.
  - `contract_validation.json`: pre-code validation status; `06-code` stops
    when this reports a failed contract.
- `05-design/evidence/`
  - `experiment_contract.json` / `.md`: compatibility research handoff from
    V2.3 synthesis artifacts. The executable V2.5 contract lives at
    `05-design/experiment_contract.json`.
  - `tool_context.json` / `.md` (debug-only): read-only handoff for future
    MCP/tool/agent integrations before any code workspace is opened.
  - `evidence_review.md`, `decision_log.jsonl`, `eval_report.json` / `.md`
    (debug-only): human-review checklist and simple research artifact quality
    checks.
- `05-design/tools/` (debug-only)
  - `tool_adapter_contract.json` / `.md`: read-only Tool/MCP adapter contract
    with allowed artifact reads, trace writes, forbidden actions, request/response
    shape, and fallback rules.
  - `tool_trace.jsonl`: append-only tool audit trace.
  - `external_agent_backend.md`: boundary for Codex, Claude Code, OpenCode, and
    similar external agent backends.
- `05-design/governance/` (debug-only)
  - `artifact_retention_policy.json` / `.md`: classifies stable outputs,
    evidence tables, cache artifacts, traces, debug diagnostics, and rebuildable
    files so cleanup stays explicit.
- `08-report/`
  - `report.md`: final Markdown report assembled from the active report
    template, known evidence, and experiment/code-task results when present.
  - `references.bib`: bounded bibliography generated from actually cited
    papers, not arbitrary model-suggested references.
  - `manifest.json`: report mode, template/criteria paths, cited paper IDs,
    quality status, and audit status for reproducibility.
  - `report_memory.json`: compact section plan, source handles, metric claims,
    and limitations used to keep later writer/reviewer passes coherent.
  - `report_quality.json`: deterministic safety checks for citation
    provenance, cited-paper coverage, metric visibility, and runtime/fallback
    disclosure.
  - `report_audit.json`: compact audit package for citations, metrics,
    claim-support warnings, and Writer/Reviewer findings when agent mode is
    enabled.

`[research].planner = "auto"` uses an LLM planner when `[llm].enabled = true`
and falls back to deterministic planning when the provider is unavailable.
Set it to `"deterministic"` when you want repeatable no-extra-LLM query
planning, or `"llm"` when you explicitly want model-backed question and query
expansion.

When `[research].max_retrieval_rounds` is greater than `1`, the search stage can
use uncovered required facets to run a bounded second follow-up round before it
writes the final `papers.jsonl`.

The public research-only example uses live academic sources and bounded
full-text extraction:

```bash
uv run simple-ar run --config examples/research_report/configs/research_report.toml
```

That config sets `[report].mode = "research_only"`, so the pipeline skips
design/code/run and goes directly from search/read/synthesize to report.
Local Markdown/text notes are still supported with
`[research].sources = ["local_files"]`; see the local-notes snippet in
`CONFIG_REFERENCE.md` when you want an offline private-corpus run.

### Resume And Status

Resume a run:

```bash
uv run simple-ar resume runs/<run-id>
uv run simple-ar resume runs/<run-id> --from-stage run --to-stage report
```

Show run status:

```bash
uv run simple-ar status runs/<run-id>
```

## Retrieval And Artifact Tools

Use these when you want to inspect or search files produced by a run:

```bash
uv run simple-ar inspect runs/<run-id>
uv run simple-ar search-artifacts runs/<run-id> "accuracy"
uv run simple-ar run --topic "toy topic" --to-stage report --retrieval-top-k 4
uv run simple-ar run --topic "toy topic" --to-stage report --no-retrieval
```

See [CLI Reference](CLI_REFERENCE.md#artifact-tools) for option details.

## Tool And External Agent Handoff Preview

V2.6 adds an internal common tool and agent-handoff layer. This is a controlled
extension point for future Codex, Claude Code, OpenCode, OpenAI tool-calling, or
MCP adapters; it is not a new default runtime path.

Current behavior:

- registered tools are real local report/experiment tools, not placeholder MCP
  stubs;
- schema export is available for OpenAI-style and MCP-style tool definitions;
- default permission policy is read-only/plan-only;
- external-agent handoff packages are written under
  `runs/<run-id>/agent_handoff/<name>/`;
- backend outputs, when collected, go to `runs/<run-id>/agent_outputs/<name>/`
  and still require SimpleAutoResearch validation before they can affect a
  patch, result, or report.

The handoff package is deliberately explicit:

```text
runs/<run-id>/agent_handoff/<name>/
  instructions.md           # task + backend profile + permission summary
  tool_schema.json          # exported real tool schemas
  permission_policy.json    # write/shell/network/secret policy
  artifact_handles.json     # reviewable run artifacts exposed to the backend
  expected_outputs.json     # canonical files the backend may produce
  workspace_manifest.json   # compact run/workspace view
  context/
```

External tools remain optional strong-path adapters. Local research, report,
greenfield experiment, and code-task workflows continue to work without Codex,
Claude Code, OpenCode, or an MCP server.

V2.6 also adds runnable backend wrappers behind this boundary:

- `fake`: deterministic dry-run backend used for integration tests;
- `local_llm`: uses the configured LLM to produce bounded review artifacts;
- `codex`, `claude_code`, `opencode`, `external_cli`: optional CLI backends.

Set `[implementation].provider` to one of those names when experimenting with
agent-backed greenfield generation or repair. External CLI providers also
require `[implementation].allow_external_agent = true`. Use
`[implementation].agent_binary`, `.agent_args`, and `.agent_timeout_sec` when
the executable is not on `PATH` or needs provider-specific flags. Even then, the backend
may only write candidate files inside the handoff directory; SimpleAutoResearch
copies them into the run workspace, then runs the normal code review, result
guard, benchmark, or code-task validation gates.

`[implementation].agent_mode` is the only mode switch for this layer:

- `model`: keep SimpleAutoResearch as the harness and use a local/model backend
  only for bounded generation.
- `handoff`: write an auditable handoff package for Codex, Claude Code,
  OpenCode, or another external CLI, then ingest candidate files back through
  SimpleAutoResearch gates.
- `delegated_workspace`: reserved for a future strong path where an external
  harness owns the workspace loop. The value is recognized today, but execution
  fails explicitly instead of silently falling back.

Run-local read-only tools can also be exposed over MCP stdio:

```bash
uv run simple-ar tools schema --format mcp
uv run simple-ar tools call runs/<run-id> list_experiment_artifacts
uv run simple-ar tools serve-mcp runs/<run-id>
```

The canonical Codex/MCP integration example lives in
`examples/tool_mcp_codex_agent/`. It uses `[implementation].provider = "codex"`
and leaves `[implementation].agent_model = ""` by default so Codex CLI can use
the model configured for your account. Set `agent_model` only when you have
confirmed the model name is supported by that CLI/account.

## Code Task Workflow

The code-task workflow prepares an isolated editable workspace and never mutates
the original source project. Existing-project tasks default to `auto`, which
prefers `git_worktree` for committed Git projects and falls back to `copy` with
a recorded reason and next-step hints. It also supports explicit `copy`,
explicit `git_worktree`, and experimental `sparse_copy` for small allowlisted
subsets. Greenfield tasks use
`kind = "greenfield"` and default to an `empty` workspace where the generated
project is written. The workflow is intentionally step-by-step so each stage can
be reviewed.

Initialize from a TOML config so project paths, benchmark metrics, workspace
mode, model routing, and edit budgets stay in one reviewable file. The bundled
standalone example uses the medium review pipeline:

```bash
uv run simple-ar code-task init --config examples/code_task_medium_review/configs/code_task.toml
```

That example runs `python main.py --config configs/experiment.json
--show-progress`, prints newline progress bars during baseline/patched runs,
and uses `[execute].stream_benchmark_output = "auto"` so `code-task execute`
relays benchmark progress while still saving stdout/stderr artifacts. The
`auto` mode handles both normal `print` logs and carriage-return progress
output such as `tqdm`.

`init` creates one run directory with this core layout:

```text
runs/<run-id>/
  manifest.json                 # benchmark, workspace, environment, safety policy
  code_task/
    task.md                     # task prompt
    workspace/                  # isolated copy/worktree root
      ...                       # project root may be a subdirectory in monorepos
    meta/
      codebase_index.json       # file-level code index
      repo_map.json             # layered symbol/repo map
      repo_map_summary.md       # human-readable repo-map summary
```

It does not run code, call the LLM, or modify the original source project.

For a standalone from-scratch project, use the same code-task command with
`kind = "greenfield"` and no `code_root`:

```bash
uv run simple-ar code-task init --kind greenfield --task-file task.md --benchmark-command "python generated_project/main.py"
uv run simple-ar code-task execute runs/<run-id> --to-step run
```

In this mode, `execute` uses the shared code-task memory, reviewer,
validation, runner, and repair artifacts, but the implementation step generates
`code_task/workspace/generated_project/` instead of applying a patch to copied
source files.

For a larger server-oriented acceptance task, use the standalone greenfield ML
suite:

```bash
uv run simple-ar code-task init --config examples/code_task_greenfield_ml_suite/configs/code_task.toml
uv run simple-ar code-task execute runs/code-task-greenfield-ml-suite/<run-id> --config examples/code_task_greenfield_ml_suite/configs/code_task.toml --yes
```

This example is intentionally heavier than the laptop smoke tests. It asks for
a modular ML workbench with packaged/local open datasets when available,
synthetic fallback only when necessary, multiple model families, ablations,
resource-aware execution, and parseable metrics. Edit `[implementation]` in the
config when you want to test a Codex/Claude/OpenCode handoff instead of the
local LLM path.

Before planning the greenfield implementation, execute writes
`code_task/meta/dependency_advice.json` and `.md`. It scans the active Python
environment, records a full installed-package snapshot in JSON, and prints the
task-relevant subset in the terminal. The built-in dependency catalog is used as
semantic hints, not as a whitelist, so packages installed for your server task
can still be surfaced to the planner. This is advice-only: the command may show
an optional `uv add ...` suggestion for a stronger implementation path, but it
will not install dependencies or mutate the environment for you.

When `workspace.mode = "auto"` is omitted or selected explicitly, existing
projects first try a detached git worktree. If Git cannot be used safely, the
run falls back to a guarded copy and records `requested_mode`, `selected_mode`,
`fallback_reason`, and `user_next_steps` under `manifest.json.workspace`.

When `workspace.mode = "git_worktree"` or `--workspace-mode git_worktree` is
used explicitly, `init` creates a detached git worktree at
`code_task/workspace/` and stops with an actionable checklist if Git isolation
is not possible. `code_root` may be either the repository root or a project
subdirectory inside a larger repository. In the subdirectory case,
SimpleAutoResearch creates the worktree at the repository root and uses the
matching subdirectory as the editable project root for indexing, editing, and
benchmark execution. It records git provenance under `manifest.json.workspace`
and keeps `.git`/`.env` metadata out of the codebase index and model context.
It still does not install dependencies.

If `git_worktree` init fails, the CLI prints a checklist instead of a Python
traceback. The usual fixes are: pass a path inside the intended baseline Git
repository as `--code-root`, create an initial local commit with `git init`,
`git add .`, and `git commit -m "initial baseline"`, or switch to
`--workspace-mode copy` when the current filesystem state should be included
without committing first.

When `workspace.mode = "sparse_copy"` or `--workspace-mode sparse_copy` is
used, init copies only selected files. Configure patterns with
`[workspace].include` / `[workspace].exclude` or repeated
`--workspace-include` / `--workspace-exclude`. Built-in exclusions still block
`.git`, virtualenvs, `runs`, cache/build directories, `data`, `models`, `.env`,
and secret-like paths. This mode is useful for small known subsets, but it can
omit runtime dependencies; prefer `auto` or explicit `git_worktree`/`copy` for
general projects.

Use `[edit_scope]` when the workspace contains files that may be read but must
not be changed by the model. `[workspace]` controls what is copied or mounted;
`[edit_scope]` controls what later work-plan, proposal, repair, and apply gates
may modify.

```toml
[edit_scope]
# Empty allowed_patterns means every non-protected workspace path may be edited.
allowed_patterns = ["review_pipeline/**", "main.py"]

# These are added to the built-in protected tests/benchmarks/.env/secrets list.
protected_patterns = ["configs/locked/**"]
```

Benchmarks should print stable numeric metric lines. Supported formats are
`name: value` and `METRIC name=value`; the prefixed form is useful for generated
or external-agent projects because it is visibly machine-readable. Custom
metric names work when you declare their direction in TOML. Explicit CLI flags
are still supported for experiments and quick tests, but the TOML path is the
recommended public workflow. See
[CLI Reference](CLI_REFERENCE.md#simple-ar-code-task-init) for the full option
table and [Configuration Reference](CONFIG_REFERENCE.md#standalone-code-task-config)
for the config schema.

For existing-project tasks, `[execute].baseline_policy` controls whether the
unchanged benchmark is run before editing. The default `auto` behavior runs it
when comparison evidence is useful. Use `skip` or `none` when the baseline is
too expensive or the task is acceptance-style, and use `provided` with
`baseline_metrics_file` when you already have trusted baseline metrics. Provided
baselines are recorded as user-supplied evidence in the run summary; they are
not presented as reproduced results.

### Recommended Path: TOML + Execute

For normal use, prefer a TOML config plus the state-aware executor. This keeps
commands short while preserving review gates for the patch plan and edit
proposal. The examples below use the medium review pipeline config because it
exercises multi-file edits, project config changes, metrics, and visible
benchmark progress.

1. Initialize a run:

```bash
uv run simple-ar code-task init --config examples/code_task_medium_review/configs/code_task.toml
```

The command prints a run directory such as
`runs/20260523-xxxx-medium-review-pipeline`. Replace `runs/<run-id>` in the following
commands with that printed path.

`init` writes the isolated workspace and static project map:

```text
runs/<run-id>/
  manifest.json
  code_task/
    task.md
    workspace/
    meta/
      codebase_index.json
      repo_map.json
      repo_map_summary.md
```

The workspace is the only editable copy/worktree. `task.md` is the task prompt,
the `meta/` files are the initial code map, and `manifest.json` records
benchmark, workspace, environment, and safety policy.

> Tip: The medium review pipeline runs `python main.py --config
> configs/experiment.json --show-progress` and can relay progress lines such as
> `benchmark stdout: round 1/4 ...` while still saving the full log under
> `code_task/run/<label>/stdout.txt`.

> Note: The medium task often touches feature extraction, model scoring, and
> config together. Its sample edit scope allows `configs/experiment.json`
> because a newly implemented feature family must be enabled before the
> benchmark can measure it. It may create a reviewed `large` batch; add
> `--allow-large-edits` to the final apply command only after inspecting
> `code_task/meta/proposed_edits.json`.

2. Run the state-aware executor:

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml
```

On an interactive terminal, this one command can walk through the plan review,
proposal review, apply, structured review, validation, and patched benchmark gates. On a
non-interactive shell, or when you answer `no`, it stops at the current review
gate and can be rerun after review. The first review gate usually writes:

```text
code_task/
  work_plan.md
  patch_plan.md
  meta/
    environment_report.json
    review_report.json
  attempts/
    attempt-001/
      batches/
        batch-001/
          batch_state.json
  run/
    baseline/
      metrics.json
```

At this point the original project is still untouched and the workspace has not
received model edits.

`execute` renders the step state with Rich and runs continuously until a real
review gate is reached. In an interactive terminal, those gates are handled
inline: a yellow review panel points to `patch_plan.md`, `proposed_edits.json`,
or large-edit approval and asks whether to continue. In non-interactive shells,
it stops cleanly at the gate unless `--yes` is supplied. If a run is
interrupted, rerun the same `code-task execute` command: completed steps are
detected and shown as skipped before the workflow advances. Use `--interactive`
only for debug mode when you want to confirm each primitive step; `--yes`
auto-continues those primitive prompts, and in normal execute mode also
auto-approves inline review gates. Use it only when automated approval is
intentional. Use `--no-review-inline` if you prefer the older stop-and-rerun
flow.

If LLM work planning or patch planning returns malformed JSON, `execute` stops
with `llm_planning_failed` and leaves the fallback artifacts unwritten. Rerun
the same command to retry the LLM step. Use `--no-llm` for a deterministic
offline plan, or `--allow-planning-fallback` only when that weaker fallback is
acceptable for the task.

After edits are applied, `execute` writes `code_task/meta/review_report.json`
before static validation. After the patched benchmark runs, it writes
`code_task/meta/review_report_post_run.json`. Blocking findings are also
recorded under `code_task/memory/` so repair prompts can reuse the latest
review evidence.
Structured review also writes `code_task/meta/review_index*.json` and
`code_task/meta/review_clusters*.json`: the former is the full project index,
and the latter records the semantic file clusters actually shown to reviewers.
Greenfield planning additionally writes
`code_task/meta/planning/agent_steps.jsonl` to make requirements,
architecture, interfaces, file-plan, and planning-review failures easier to
trace.

For greenfield runs, the same review gate checks generated files before
validation. If the review detects generic recoverable issues such as fallback
core files, missing artifact writers, or missing local APIs, `execute` can use a
bounded LLM repair round that first asks for structured local repair actions
such as unique old/new replacements or function-level rewrites. Whole-file
replacement remains available for structural file-level failures, but it is no
longer the default repair shape. The repair records an edit application, resyncs
`code_task/meta/code_artifacts.json`, and reruns review. If the problem is still
blocking, the run stops with the reviewed artifacts intact for inspection.

3. At the patch-plan review panel, read `code_task/work_plan.md` and
`code_task/patch_plan.md`. If the plan is reasonable, answer `yes` to continue.
If you are running non-interactively, answered `no`, or used
`--no-review-inline`, approve it explicitly:

```bash
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve --note "reviewed"
```

4. If the first executor command did not already continue, generate an edit
proposal next. Do not apply it until the inline proposal review panel appears:

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml --to-step propose-edits
```

Review:

- `code_task/meta/proposed_edits.json`: controlled old/new replacements.
- `code_task/meta/llm_usage_summary.json`: LLM token usage summary.
- latest `code_task/attempts/.../proposal_warnings.json`, when present.

The default editor backend is `controlled_patch`. Its metadata is recorded in
`proposed_edits.json`, the active batch state, `applied_edits.json`, and
`manifest.json.patch`. The backend does not run benchmarks, approve plans, or
write reports; those gates remain owned by the code-task workflow.

5. At the proposal review panel, inspect the generated edits. Answer `yes` to
apply and evaluate the patched workspace. If you are running non-interactively,
answered `no`, or used `--no-review-inline`, apply explicitly:

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml --apply-proposed-edits --timeout 60
```

6. Inspect the result:

```bash
uv run simple-ar status runs/<run-id>
```

Key output files:

```text
code_task/
  summary.md
  patch.diff
  meta/
    applied_edits.json
    validation_report.json
  run/
    patched/
      metrics.json
    comparison.json
```

`patch.diff` and `applied_edits.json` show what changed, `validation_report.json`
shows static checks, `metrics.json` records the patched run, and
`comparison.json` is the before/after objective verdict.

Treat `objective_improved` or `objective.status = "improved"` as the normal
success signal. A patched benchmark can pass while `objective.status` is
`regressed` or `mixed`; in that case, the code ran but the measured task goal
was not really met.

7. If the proposal needs repair, ask for one bounded repair proposal:

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml --to-step repair --repair-rounds 1 --timeout 60
```

Review the newest `code_task/repairs/repair-NNN/proposed_edits.json`, then
apply it explicitly:

```bash
uv run simple-ar code-task apply-edits runs/<run-id> --edits-file runs/<run-id>/code_task/repairs/repair-NNN/proposed_edits.json
uv run simple-ar code-task validate runs/<run-id>
uv run simple-ar code-task run runs/<run-id> --timeout 60
uv run simple-ar status runs/<run-id>
```

Preview the next executor action without writing artifacts:

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml --dry-run
```

### Optional Mapping And Context Tools

Refresh the code map at any time:

```bash
uv run simple-ar code-task map runs/<run-id>
```

`map` scans the current workspace and refreshes the static code-map artifacts:

```text
code_task/
  workspace/                  # scanned source tree
  meta/
    codebase_index.json       # file-level code index
    repo_map.json             # layered repo/symbol map
    repo_map_summary.md       # human-readable summary
manifest.json                 # updated map/workspace metadata
```

It does not call the LLM, install dependencies, run benchmark code, or modify
the original source project.

Locate likely files before planning or editing:

```bash
uv run simple-ar code-task locate runs/<run-id> --query "improve spam keyword prediction"
```

`locate` writes `code_task/meta/locate_results.json` and
`code_task/meta/locate_results.md`. It ranks editable targets separately from
read-only evidence such as tests and benchmarks, using the repo map rather than
loading the whole project into a prompt. It does not call the LLM or read files
outside the prepared workspace.

Build a bounded prompt context pack:

```bash
uv run simple-ar code-task context runs/<run-id> --max-files 8 --max-total-chars 20000
```

`context` creates `code_task/context_packs/context-NNN/` containing
`context_pack.json`, `prompt_context.md`, and `selected_snippets.jsonl`. The
pack records token-like character budgets, selected editable files,
read-only evidence, truncated snippets, and omitted files. It is a reviewable
intermediate artifact for LLM planning/editing. When a latest context pack
exists, `plan` uses it for planning context, while `propose-edits` uses only
its editable snippets and keeps tests/benchmarks as read-only evidence.

### Manual Primitive Path

The executor path above calls these primitive commands for you. Use this manual
path when you are learning the internals, debugging one step, or intentionally
building a custom workflow.

Probe the environment and run the unchanged baseline before asking for edits:

```bash
uv run simple-ar code-task map runs/<run-id>
uv run simple-ar code-task locate runs/<run-id>
uv run simple-ar code-task context runs/<run-id>
uv run simple-ar code-task probe runs/<run-id>
uv run simple-ar code-task baseline runs/<run-id> --timeout 60
uv run simple-ar code-task work-plan runs/<run-id>
uv run simple-ar code-task batch runs/<run-id> --work-item W1
```

`probe` writes `code_task/meta/environment_report.json` with OS, Python, tool, GPU, dependency-file, and test-directory signals. It also writes `resource_probe.json` and `resource_decision.json`, which give greenfield and external-agent paths a compact hardware profile. It does not install dependencies or run project code.

`baseline` runs the recorded benchmark command inside `code_task/workspace/`
before any patch is applied. It stores `execution_report.json`, `stdout.txt`,
`stderr.txt`, and parsed `metrics.json` under `code_task/run/baseline/`, and
updates `code_task/summary.md`.

Generate a higher-level work plan when the task is broad or may need multiple
edit batches:

```bash
uv run simple-ar code-task work-plan runs/<run-id>
uv run simple-ar code-task batch runs/<run-id> --work-item W1
```

`work-plan` writes `code_task/work_plan.json` and `code_task/work_plan.md`.
It records work items, target files, read-only evidence, validation hints,
context requests, and budget profiles. It does not generate code or edit
files. `batch` creates durable attempt state under
`code_task/attempts/attempt-NNN/batches/batch-NNN/`, which is the foundation
for later multi-round, per-batch editing and recovery. When a batch
is active, edit proposals are constrained to that batch's target files and
write extra batch-local review artifacts.

Work-plan items are intended to be executable implementation batches, not
standalone analysis notes. The LLM prompt asks the planner to put inspection
needs in `context_request`. If a model still returns an analysis-only first
item, `code-task execute` prefers the first later item that looks like a real
code change, so a broad "inspect the project" step does not accidentally become
the active edit batch.

If a model splits one tightly coupled implementation into a serial chain, for
example feature extraction -> scorer wiring -> config enablement, the batch
creator can merge that small dependent chain into one execution batch. The
reviewed `work_plan.md` still shows the separate items, but
`batch_state.json.work_item.source_work_item_ids` records the merged item ids
and `target_files` becomes the union of the coupled files. Because these merged
batches may touch more than two files, they usually use the `large` budget
profile and require explicit review before `--allow-large-edits` is used.

Generate a patch plan (LLM optional; offline mode writes a conservative plan):

```bash
uv run simple-ar code-task plan runs/<run-id>
```

If `probe`, `validate`, or `baseline` artifacts already exist, the generated
plan includes that run context so the model and reviewer can reason from
recorded environment and benchmark evidence instead of starting cold.

`plan` writes `code_task/patch_plan.md`, updates `manifest.json`, and records
selected context files. It does not change source files. In LLM mode it records
token usage under `code_task/meta/llm_usage.jsonl`; with `--no-llm` it writes a
conservative offline plan.

Review the plan, then approve it:

```bash
uv run simple-ar code-task decide-plan runs/<run-id> \
  --decision approve \
  --note "small scoped edit"
```

`decide-plan` appends a human decision to
`code_task/meta/hitl_decisions.jsonl` and updates the plan status in
`manifest.json`. Approval is the normal gate before model-generated edits can
be applied.

Ask the model for controlled edit proposals (offline mode writes an empty proposal):

```bash
uv run simple-ar code-task propose-edits runs/<run-id>
```

`propose-edits` writes `code_task/meta/proposed_edits.json`. The proposal uses
controlled old/new text replacements and is meant for review. It does not edit
the workspace by itself. A proposal may include multiple ordered edits for the
same file; each `old` block must still match uniquely when applied in sequence.
The proposal also records `editor.backend = "controlled_patch"` so future
backends can be audited through the same artifact shape.
The reserved `external_agent` backend is intentionally non-executable in this
version. It can build a reviewable invocation plan for future
Codex/Claude/OpenCode adapters, including provider, command preview, blocked
read patterns, timeout, network/shell permissions, log path, and diff path. Any
future external-agent result must still become a captured diff/proposal before
SimpleAutoResearch applies validation, benchmark execution, and summary logic.
By default, tests and benchmark files are treated as read-only evidence:
`propose-edits` omits them from editable snippets, and any model edit targeting
paths such as `tests/**`, `test_*.py`, `benchmark.py`, or `*benchmark*.py` is
dropped from the proposal. The executor also applies an edit budget after the
model returns JSON. Oversized proposals are written with warnings and rejected
edits instead of being applied; if the proposal fits the larger review budget, rerun
with `--allow-large-edits` only after reading the JSON.

Apply proposed edits inside the editable workspace:

```bash
uv run simple-ar code-task apply-edits runs/<run-id>
```

`apply-edits` applies the reviewed proposal only inside
`code_task/workspace/`, writes a human-readable `code_task/patch.diff`, writes
`code_task/meta/applied_edits.json` with changed files and hashes, and updates
the codebase index. The next `execute` step runs the structured reviewer and
writes `code_task/meta/review_report.json` before validation. It still never
mutates the original `--code-root`. If an edit cannot be matched safely,
`execute` stops with `patch_apply_failed` before workspace files are changed.
`applied_edits.json` records the proposal path and editor backend used for the
application, including manually supplied or repair proposal files.
`apply-edits` also re-checks the edit scope, so manually supplied JSON cannot
modify protected tests or benchmark files even if it bypassed the LLM proposal
step.

Validate and run the patched benchmark:

```bash
uv run simple-ar code-task validate runs/<run-id>
uv run simple-ar code-task run runs/<run-id> --timeout 60
```

`validate` writes `code_task/meta/validation_report.json` with syntax errors,
risky imports/calls, missing import warnings, and file-size warnings. It is a
static check; it does not run the benchmark.

`run` stores the patched benchmark under `code_task/run/patched/`.
When both baseline and patched artifacts exist, SimpleAutoResearch also writes
`code_task/run/comparison.json` and includes outcome, next-step guidance, and
metric deltas in `code_task/summary.md`.

Patched benchmark success is separated from task-objective success. A run may
pass the benchmark floor but still regress against baseline metrics. In that
case `manifest.json` records `objective.status = "regressed"`, `simple-ar
status` prints the objective verdict, and `summary.md` points you back to
`code_task/run/comparison.json` instead of treating the task as complete.

Analyze failures and request a bounded repair proposal:

```bash
uv run simple-ar code-task analyze-failure runs/<run-id>
uv run simple-ar code-task repair runs/<run-id>
```

`analyze-failure` reads the latest failed validation/benchmark evidence and
writes a compact diagnosis, usually under `code_task/run/patched/` or the
current run label. If the benchmark was blocked before launch by static
validation, it writes `code_task/meta/failure_analysis.md` instead. It is
deterministic and does not call the LLM.

`repair` uses the failure analysis, latest patch, task, and selected source
context to write a bounded repair proposal under
`code_task/repairs/repair-001/proposed_edits.json`. The proposal records the
source analysis path, selected context files, and repair constraints. It does
not apply the repair automatically. Repair proposal context follows the same
edit-scope rule: tests and benchmark files may inform diagnosis, but they are
not supplied as editable snippets by default. `code_task/summary.md` is
refreshed with a Repair section.

Apply a reviewed repair proposal explicitly:

```bash
uv run simple-ar code-task apply-edits runs/<run-id> \
  --edits-file runs/<run-id>/code_task/repairs/repair-001/proposed_edits.json
```

When a repair proposal is applied, `manifest.json.patch.latest_applied_proposal`
and `code_task/meta/applied_edits.json` record the repair proposal path. After a
later patched benchmark passes, stale failure-analysis and repair sections are
marked resolved so `status` and `summary.md` reflect the current state rather
than an older failed attempt.

### Troubleshooting Code Task Runs

`proposed_edits.json` was not created after `execute`:

- This is normal after the first executor call. A fresh run stops at
  `approval_required` after writing `code_task/patch_plan.md`.
- Review `code_task/patch_plan.md`, then run:

```bash
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve --note "reviewed"
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml --to-step propose-edits
```

- Check `manifest.json`: `plan.status` should be `approved`. The decision log
  is `code_task/meta/hitl_decisions.jsonl`.

`execute` stopped with `llm_planning_failed`:

- This means the model call returned invalid/missing structured JSON for the
  work plan or patch plan. No deterministic fallback plan is written by default.
- Rerun the same `code-task execute ... --config ...` command to retry from the
  same point; completed earlier steps will be detected as skipped.
- If you want to proceed without LLM planning, rerun with `--no-llm`. If you
  still want the LLM first but accept deterministic fallback after retries, use
  `--allow-planning-fallback` or set `[execute].allow_planning_fallback = true`.

Validation passed but patched benchmark failed:

- This means the patch was syntactically acceptable but behavior or metrics got
  worse. Inspect:

```bash
code_task/run/patched/execution_report.json
code_task/run/patched/stdout.txt
code_task/run/patched/stderr.txt
code_task/run/comparison.json
code_task/summary.md
```

- Ask for a bounded repair proposal:

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml --to-step repair --repair-rounds 1 --timeout 60
```

- Review the newest `code_task/repairs/repair-NNN/proposed_edits.json`, then
  apply it explicitly:

```bash
uv run simple-ar code-task apply-edits runs/<run-id> --edits-file runs/<run-id>/code_task/repairs/repair-NNN/proposed_edits.json
uv run simple-ar code-task validate runs/<run-id>
uv run simple-ar code-task run runs/<run-id> --timeout 60
```

- A repair can make the benchmark pass without truly improving over baseline.
  Use `code_task/run/comparison.json`, `manifest.json.objective.status`, and
  `simple-ar status` to decide whether the task goal was met.

Patched benchmark passed but the objective is `regressed` or `mixed`:

- This is not a runtime failure; it means the patch did not satisfy the metric
  goal compared with the recorded baseline.
- Inspect `code_task/run/comparison.json` first. It lists metric deltas,
  directions, the primary metric when configured, and the conservative verdict.
- Treat this like a quality failure: revise the task/plan, regenerate a tighter
  proposal, or request a repair only if the comparison gives enough evidence for
  a bounded follow-up patch.

`apply-edits` reports `old text was not found`:

- No workspace files are changed when this happens. It means a proposal's
  `old` text does not exactly match the current workspace, or the model put
  unified-diff markers inside the structured JSON.
- Regenerate the proposal or edit the JSON manually. Each edit must use exact
  current file text in `old` and replacement file text in `new`; do not include
  `+`, `-`, `@@`, `---`, or `+++` diff markers inside either field.
- If several edits target the same nearby block, combine them into one larger
  exact old/new replacement so later edits do not invalidate earlier ones.

Large-edit approval is required:

- Read `code_task/meta/proposed_edits.json` and any
  `proposal_warnings.json` under `code_task/meta/` or the latest
  `code_task/attempts/.../batch-NNN/` directory.
- If the larger change is intentional, rerun the apply/executor command with
  `--allow-large-edits`. Do not use this flag just to silence an unclear model
  proposal.

Proposal covers only the first part of a coupled plan:

- Check `code_task/work_plan.md` and the latest
  `code_task/attempts/.../batch_state.json`. If a plan has serial items such as
  feature -> model -> config, the active batch should list all coupled ids in
  `work_item.source_work_item_ids` and all editable files in `work_item.target_files`.
- For older runs created before this behavior, create a fresh batch with
  `uv run simple-ar code-task batch runs/<run-id> --work-item W1 --force`, then
  regenerate the proposal with
  `uv run simple-ar code-task propose-edits runs/<run-id> --force`.
- If the merged batch is marked `large`, review the full proposal before using
  `--allow-large-edits`.

`uv run` fails with a local cache permission error:

- This is an environment issue outside the run artifacts. Fix the uv cache
  permissions or run the project virtualenv entrypoint directly, for example
  `.\.venv\Scripts\simple-ar.exe ...` on PowerShell.

## Embedded Code Task In The 8-Stage Pipeline

Use this when you want the normal research pipeline to hand off to a configured
existing-code task during `06-code` and include the result in `08-report`.

Config-driven user project:

```bash
uv run simple-ar run --config examples/full_pipeline_tiny_mlp/configs/pipeline.toml
```

The example config is intentionally complete: it includes the outer pipeline
settings and the embedded code-task settings in one file. See
[Configuration Reference](CONFIG_REFERENCE.md#complete-pipeline-config) before adapting it to your own
project.

The equivalent split config form points the pipeline at a standalone code-task
config:

```bash
uv run simple-ar run \
  --topic "improve tiny digits MLP" \
  --to-stage report \
  --experiment-template code_task_project \
  --code-task-config examples/full_pipeline_tiny_mlp/configs/pipeline.toml \
  --offline-search \
  --experiment-timeout 60
```

And the fully explicit flag form is:

```bash
uv run simple-ar run \
  --topic "improve tiny digits MLP" \
  --to-stage report \
  --experiment-template code_task_project \
  --code-root examples/full_pipeline_tiny_mlp/project \
  --task-file examples/full_pipeline_tiny_mlp/task.md \
  --benchmark-command "python benchmark.py" \
  --primary-metric accuracy \
  --metric-direction accuracy=higher \
  --metric-direction macro_f1=higher \
  --offline-search \
  --experiment-timeout 60
```

For a more research-first run, omit `--task-file` while still providing the
code root and benchmark command:

```bash
uv run simple-ar run \
  --topic "research and improve the tiny digits MLP baseline" \
  --to-stage report \
  --experiment-template code_task_project \
  --code-root examples/full_pipeline_tiny_mlp/project \
  --benchmark-command "python benchmark.py" \
  --primary-metric accuracy \
  --metric-direction accuracy=higher \
  --offline-search \
  --experiment-timeout 60
```

In that mode, `05-design` writes `generated_code_task.md` and
`generated_code_task_meta.json` from the prior research artifacts and a compact
codebase summary. `06-code` then uses the generated task as the normal
`code_task/task.md` input for planning and edit proposal.

When you already have a precise task file but still want the 8-stage research
context to shape implementation priorities, set
`[implementation].task_handoff = "merge"` in the pipeline config. The user
task file remains the hard requirement, while `05-design` writes a merged
`generated_code_task.md` that adds goal/problem/synthesis/hypothesis context,
benchmark criteria, and codebase signals for the embedded code-task run.
The generated task also includes a compact Research-to-Code Bridge built from
the synthesis brief, experiment contract, metric schema, and resource plan, so
the code stage can see method-transfer hints, implementation hypotheses,
ablation targets, metric directions, and known risks.

`code_task_project` writes a normal pipeline run plus nested code-task artifacts
under `06-code/code_task_run/`. During `06-code`, it copies or worktrees the
user project, probes the environment, applies the configured baseline policy,
builds a repo map/context pack, generates a batch-oriented work plan, creates an
attempt/batch record, generates a patch plan, records an automatic pipeline
approval, asks for controlled edits, applies them inside the prepared
workspace, validates the result, and runs the patched benchmark once before
leaving `06-code`. If that verification benchmark fails, the bridge records a
failure analysis and attempts one bounded repair from the execution evidence.
To keep unattended pipeline runs small and reviewable, the embedded path
executes the first concrete work item as a single batch instead of automatically
merging a serial dependency chain into a large patch. During `07-run`, the
harness reruns the verified patched benchmark, writes `comparison.json` when
baseline and patched metrics are both available, and exposes code-task metrics
through canonical `07-run/results.json`.
During `08-report`, the report includes a deterministic Code Task Evidence
section pointing back to the nested work plan, batch state, summary, diff, and
comparison artifacts; report memory also exposes baseline, patched, and delta
metrics from the nested `comparison.json`. The embedded path uses the same edit-scope guard as the
standalone workflow, so the patch cannot rewrite protected tests or benchmark
files just to improve reported metrics.

The embedded artifact layout is:

```text
06-code/
  code_task_experiment.json
  code_task_run/
    manifest.json
    code_task/
      task.md
      workspace/
      work_plan.md
      patch_plan.md
      patch.diff
      summary.md
      meta/
        repo_map.json
        proposed_edits.json
        validation_report.json
      run/
        baseline/
        patched/
        comparison.json
07-run/
  results.json          # canonical metrics/execution/comparison result
  guard_report.json     # missing metric, timeout, NaN/Inf, empty-run checks
  diagnosis.json        # guard/code-review/runtime signals for repair/report context
  diagnosis.md          # human-readable diagnosis and suggested actions
  stdout.txt
  stderr.txt
08-report/
  report.md
  references.bib
  manifest.json
  report_quality.json
  report_memory.json
  report_audit.json
```

This path is convenient for end-to-end experiments, but it deliberately trades
away the standalone workflow's review pauses. For safety-sensitive or
hard-to-debug projects, use standalone `code-task execute` or the manual path
first, then move to `code_task_project` after the benchmark and task are stable.

## Greenfield Experiment In The 8-Stage Pipeline

Use this when you have a research or benchmark-style task but no existing source
project yet. The greenfield path is intentionally bounded: `05-design` creates
the executable contract and budgets, `06-code` now calls the unified code-task
greenfield engine under `06-code/code_task_run/`, then projects the generated
project back to `06-code/generated_project/` for compatibility with `07-run`.
`07-run` only trusts parseable metrics in canonical `results.json`.

Run the lightweight local greenfield example:

```bash
uv run simple-ar run --config examples/greenfield_lightweight_training/configs/greenfield_training.toml --to-stage run
```

The example asks the pipeline to generate a medium-light CPU-only
text-classifier experiment suite from scratch. It uses a task Markdown file as
the detailed implementation brief, explicit `[resource]` limits, and an
`[evaluation]` metric schema that requires condition-level baseline/model
metrics, ablation gain, data size, timing, and parameter-count metrics. Use it
as a local greenfield structure check; for stronger server-side tasks, keep the
same config shape and raise budgets deliberately.

Reruns from `code` or `run` are archive-safe by default. Existing
`06-code`/`07-run` reviewed artifacts are copied to
`archives/<timestamp>/` before new outputs are written:

```bash
uv run simple-ar resume runs/<run-id> --from-stage code --to-stage report
uv run simple-ar resume runs/<run-id> --from-stage run --to-stage report
```

Use `--overwrite-stage-artifacts` only when you intentionally want to discard
the previous code/run artifacts without an archive.

The compact artifact layout is:

```text
05-design/
  experiment_contract.json
  result_schema.json
  resource_plan.json
  dependency_plan.json
  domain_profile.json
  contract_validation.json
06-code/
  implementation_plan.json
  architecture_plan.json
  architecture_plan.md
  file_plan.json
  generated_project/
    main.py
    generated_experiment/
      runner.py
  code_artifacts.json
  implementation_memory.json
  code_review.json
  code_backend.json
  experiment.py
07-run/
  results.json
  guard_report.json
  diagnosis.json
  diagnosis.md
  stdout.txt
  stderr.txt
  repair_summary.json  # only when bounded repair is attempted
  archives/<timestamp>/ # previous run artifacts when rerun safely
08-report/
  report.md
  report_memory.json
  report_audit.json
```

Greenfield is not a free-form autonomous agent. It is constrained by
`[resource]`, `[evaluation]`, and `[generation]`: generated files and lines are
bounded, dependency installation is disabled unless explicitly allowed, required
metrics are checked by `guard_report.json`, and `diagnosis.json` consolidates
guard, code-review, stdout/stderr, and missing-metric signals into repair/report
context. Repair currently uses that execution evidence only for narrow,
schema-driven fixes. The report stage reads experiment claims from
`07-run/results.json`, `guard_report.json`, `diagnosis.json`, resource plans,
and code-review artifacts rather than from raw stdout alone.

Legacy bundled toy smoke test:

```bash
uv run simple-ar run \
  --topic "LLM-guided improvement of a toy spam baseline" \
  --to-stage report \
  --experiment-template llm_code_task_toy_spam \
  --offline-search \
  --experiment-timeout 60
```

## Command Design

The CLI keeps primitive commands because this project is still a learning
implementation. Each step is inspectable, testable, and reviewable. Config files
are used to shorten setup-heavy commands, not to hide approval gates, artifact
paths, validation results, baseline runs, or benchmark evidence.
