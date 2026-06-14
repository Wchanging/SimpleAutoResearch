# Configuration Reference

[中文版本](CONFIG_REFERENCE_zh.md)

This document is the TOML reference for SimpleAutoResearch. It covers both the
outer research pipeline config and standalone/embedded code-task config.

- Command syntax: [CLI Reference](CLI_REFERENCE.md)
- User workflows: [Usage And Configuration](USAGE.md)
- Stage concepts and artifacts: [Workflows And Artifacts](WORKFLOWS.md)

## Loading Rules

- `simple-ar run --config PATH` loads top-level pipeline sections.
- `simple-ar resume --config PATH` applies overrides on top of the saved run config.
- `simple-ar code-task init --config PATH` loads code-task initialization sections.
- `simple-ar code-task execute --config PATH` loads execute/model/budget sections.
- Explicit CLI flags override TOML values.
- Top-level pipeline TOML and code-task TOML are validated with Pydantic before
  they are flattened into runtime settings. Wrong section types now fail early
  instead of being silently ignored.
- Relative paths in top-level run config are resolved relative to the config file when the parser explicitly supports path resolution, such as `[experiment].code_task_config` and `[research].local_documents`.
- When a run config contains legacy code-task sections such as `[code_task]`, `[benchmark]`, `[metrics]`, `[environment]`, `[safety]`, or `[edit_scope]`, the same file can be reused as the embedded code-task config. `[workspace]` is also used by the newer unified task config, so it is not treated as a code-task signal by itself.

## Complete Pipeline Config

This is a complete shape for an 8-stage run with an embedded code-task
experiment. Start with this pattern, then remove sections you do not need.

```toml
[run]
# Human-readable research or experiment goal for this run.
topic = "improve tiny digits MLP"

# Parent directory for timestamped run folders.
output_root = "runs"

# Stage range. Omit from_stage to start from "plan"; omit to_stage to finish at "report".
from_stage = "plan"
to_stage = "report"

# Suppress progress logs while keeping final paths/status output.
quiet = false

# Keep verbose diagnostics such as search planning/traces/coverage files and
# design tool-handoff drafts inside each run directory. The default false keeps
# diagnostics compact while retaining stage-owned operational artifacts:
# search documents/chunks, read Paper Briefs, synthesis briefs, and design contracts.
debug_artifacts = false

# false archives existing 06-code/07-run reviewed artifacts before reruns.
# Set true only when prior code/run artifacts are intentionally disposable.
overwrite_stage_artifacts = false

[llm]
# true uses the configured OpenAI-compatible model; false uses deterministic fallbacks where possible.
enabled = true

# Optional default model. If omitted, SIMPLE_AR_MODEL or provider default is used.
model = "gpt-4o-mini"

# Parallel LLM workers for supported stages such as paper note generation.
workers = 4

[search]
# true skips live providers and uses fixture/local behavior where configured.
offline = true

# Total maximum paper/record metadata rows requested by the search stage.
max_papers = 5

# Manual search query. If omitted, the topic or research queries are used.
query = "tiny digits MLP baseline improvement"

# Allow fixture metadata only after live/cache search failures.
allow_fixture_fallback = false

# true fails the run instead of falling back to cache or fixture metadata.
strict = false

[research]
# Search strategy profile used by the research planner. The full planning
# artifact is retained only when debug_artifacts = true.
mode = "lite"                 # lite | standard | strong

# Research-question/query planner. auto uses LLM when [llm].enabled is true,
# then falls back to deterministic planning if the provider is unavailable.
planner = "auto"              # auto | llm | deterministic

# Provider order for 02-search.
sources = ["fixture"]         # openalex | semantic_scholar | arxiv | local_files | fixture

# Query list recorded for evidence planning. Search can run follow-up rounds for uncovered facets.
queries = ["tiny digits MLP baseline improvement"]

# Generate facet-driven follow-up queries from the topic and research questions.
auto_query_expansion = true

# Planned retrieval loop depth. Values above 1 allow coverage-driven follow-up search.
max_retrieval_rounds = 2

# Maximum number of seed + expanded queries kept in the research plan.
max_queries = 6

# Evidence facets the planner should try to cover.
required_facets = ["method", "benchmark", "dataset", "code_link"]

# Markdown/text files to expose as local research records; resolved relative to this config.
local_documents = []

# Intent and budget flags for full-text evidence. Remote PDF fetching is
# guarded by these settings; parsing remains a later controlled step.
use_fulltext = false
allow_pdf_download = false
max_fulltext_documents = 6
max_pdf_mb = 20
keep_raw_pdf = false
parser_backend = "basic"      # basic | pypdf | unstructured

# 03-read LLM review settings. The read stage first screens compact abstract
# batches, then reranks the kept set for deeper Paper Briefs and synthesis.
read_screening = "auto"       # auto | llm | deterministic
read_batch_size = 4
read_workers = 3
read_max_shortlist = 12

# Whether live-provider failures may use cached metadata.
cache = true

# Local index backend. chunks.jsonl is always written; stronger backends are optional accelerators.
index_backend = "keyword"     # keyword | sqlite_fts | hybrid | lancedb | hybrid_lancedb

# Shared accelerator-store root for SQLite FTS / LanceDB. Use "run" or "local"
# for per-run databases.
index_root = ".simple_ar_cache/research_index"

[research.budget]
# Maximum research records the evidence engine should keep from all sources.
max_documents = 5

# Planned chunk cap for later document ingestion/indexing.
max_chunks = 50

# Planned prompt context budget for evidence retrieval.
max_context_tokens = 6000

# Planned LLM-call cap for research-side query expansion/screening.
max_llm_calls = 4

# Maximum coverage-driven follow-up queries attempted in a second retrieval round.
max_follow_up_queries = 3

# Backend for novelty hints inside 04-synthesize/synthesis_brief.json. local
# only records lexical risk hints over current Paper Briefs; it is not a
# definitive novelty check.
novelty_backend = "local"      # local

[retrieval]
# Enables local artifact retrieval for read/synthesize/report helpers.
enabled = true

# Number of artifact chunks retrieved when local retrieval is used.
top_k = 4

[experiment]
# Experiment template. code_task_project embeds the existing-code workflow.
template = "code_task_project"

# Timeout in seconds for stage 07 and nested benchmark calls where applicable.
timeout = 60

# Optional external code-task config. If omitted, code-task sections in this file are reused.
# code_task_config = "examples/full_pipeline_tiny_mlp/configs/pipeline.toml"

# V2.5 unified experiment/coding sections are the preferred shape for new
# pipeline configs. They normalize research-first, existing-project code-task,
# and greenfield experiment settings into one runtime task config. The older
# [code_task]/[benchmark]/[environment] sections below are still accepted.
[task]
kind = "existing_project"      # auto | existing_project | greenfield | benchmark_solution
name = "tiny-digits-mlp"
objective = "Improve the local benchmark without editing tests."
code_root = "examples/full_pipeline_tiny_mlp/project"
task_file = "examples/full_pipeline_tiny_mlp/task.md"

[implementation]
mode = "patch_existing"        # auto | patch_existing | generate_project | template
domain_profile = "ml_experiment" # auto | generic_research_experiment | code_experiment | ml_experiment | code_agent_eval
provider = "local"
task_handoff = "user_file"     # user_file | merge; merge combines task_file with research context
allow_external_agent = false
max_repair_attempts = 1

[workspace]
mode = "copy"                  # copy | git_worktree | sparse_copy
reuse_source_venv = false
setup_hook = ""
include = []
exclude = []

[execution]
backend = "local"              # local for the current V2.5 foundation path
command = "python benchmark.py"
timeout_sec = 60
stream_output = "auto"
allow_dependency_install = false

[resource]
max_runtime_sec = 60
max_files = 8
max_generated_lines = 700
max_memory_mb = 2048
allow_gpu = false

[evaluation]
primary_metric = "accuracy"
direction = "maximize"
required_metrics = ["accuracy", "macro_f1"]
success_criteria = ["primary metric should improve or avoid regression"]
metric_directions = { accuracy = "higher", macro_f1 = "higher", train_time_sec = "resource" }

[generation]
enabled = false                # set true for greenfield project generation
max_batches = 2
files_per_batch = 3
review_required = true
allow_fallback_scaffold = false # if true, failed LLM code can be replaced by a safe scaffold

[report]
# auto chooses experiment or research-only report based on available results.
mode = "auto"                 # auto | research_only | experiment

# Built-in template name or custom Markdown path. auto maps research_only to
# survey and experiment to experiment.
template = "auto"             # auto | survey | experiment | reproduction | path/to/template.md

# Built-in reviewer criteria or custom Markdown path. auto follows template.
criteria = "auto"

# Report tone hint consumed by the report agent.
style = "paper"               # paper | technical | concise

# Keep section drafts and full report traces only when needed.
draft_sections = false
debug_artifacts = false

# V2.4 local path uses LLM writer/reviewer as the main quality mechanism.
agent = "llm"                 # llm | disabled
reviewer = "llm"              # llm | disabled
max_review_iterations = 2
max_section_tokens = 1200
max_report_tokens = 5000
# 0 means all selected paper-level handles; positive values bound prompt size.
max_section_sources = 8
# full drafts from the section source set in one call. batch_refine splits
# larger sets into batches and incrementally revises the same section.
source_strategy = "full"       # full | batch_refine
source_batch_size = 10
max_source_batches = 0         # 0 means all batches
review_source_batches = false  # true reviews after each batch-refine batch
review_trace = "meta"         # off | meta | full

# Report write policy:
# - overwrite: replace 08-report/report.md and companion artifacts.
# - archive: copy the existing report package to 08-report/archives/<label>
#   before overwriting it.
# - variant: write a new package to 08-report/variants/<label> without
#   replacing the current report.md when it already exists.
output_mode = "overwrite"     # overwrite | archive | variant
output_label = ""             # optional folder label for archive/variant

# Allow the writer/reviewer to request bounded read-only source context.
allow_source_backtracking = true
max_backtracking_calls = 8
max_backtracking_tokens = 6000

[report.audit]
citations = true
metrics = true
claims = true
strict = false

[code_task]
# Source project copied/worktree-prepared into code_task/workspace.
code_root = "examples/full_pipeline_tiny_mlp/project"

# Task description for standalone or embedded code-task work.
task_file = "examples/full_pipeline_tiny_mlp/task.md"

# Parent directory for standalone code-task runs.
output_root = "runs"

# Optional display name used in run directories and manifests.
name = "tiny-digits-mlp-pipeline"

[benchmark]
# Command executed inside the isolated workspace before and after edits.
command = "python benchmark.py"

# Main metric used for before/after objective verdicts.
primary_metric = "accuracy"

[benchmark.metric_directions]
# higher/lower metrics can decide improved/regressed. resource metrics are reported but do not decide success.
accuracy = "higher"
macro_f1 = "higher"
train_time_sec = "resource"
inference_time_ms = "resource"
params = "resource"

[environment]
# current uses the active SimpleAutoResearch Python; external uses the configured python path.
mode = "current"              # current | external
# python = "C:/path/to/python.exe"

# Workspace settings are declared once in the unified [workspace] section above
# and reused by embedded code-task compatibility.

[edit_scope]
# Optional allowlist for editable files. Empty means every non-protected
# workspace-relative path may be edited.
allowed_patterns = ["digits_mlp/**"]

# Additional protected patterns are appended to the default read-only baseline
# for tests, benchmarks, .env, secrets, and credential-like paths.
protected_patterns = ["configs/locked/**"]

[safety]
# Maximum source file size copied in copy/sparse modes. Use 0 to disable.
max_file_bytes = 2000000

# Maximum file size scanned by static validation.
validation_max_file_bytes = 500000

[execute]
# Last step the state-aware executor may attempt.
to_step = "run"

# false forces deterministic fallbacks for LLM-backed code-task steps.
use_llm = true

# Benchmark timeout in seconds for executor-managed baseline/patched runs.
timeout_sec = 60

# Run benchmarks even if static validation has not passed.
skip_validation = false

# Treat higher-risk validation warnings as errors.
strict_validation = false
validation_max_file_bytes = 500000

# Live benchmark output relay mode.
stream_benchmark_output = "off"     # off | line | auto | summary

# Keep false for review-first flow. Set true only after proposal review.
apply_proposed_edits = false

# Allow proposals that exceed normal budget but fit the large budget.
allow_large_edits = false

# Keep false for real LLM runs. When false, malformed/failed LLM work-plan or
# patch-plan calls stop without writing deterministic fallback plans, so rerun
# the same execute command to retry cleanly.
allow_planning_fallback = false

# LLM work-plan and patch-plan attempts before stopping or explicitly falling back.
llm_retry_attempts = 2

# Number of bounded repair proposals after failure.
repair_rounds = 1

# LLM context file count and per-file character budget for plan/proposal/repair.
max_files = 8
max_source_chars_per_file = 4000

[models]
# Global fallback model for code-task model routing.
default = "gpt-4o-mini"

[models.code_task]
# Optional per-role model routing. Empty/missing values fall back to [models].default, [llm].model, or SIMPLE_AR_MODEL.
planner = "gpt-4o-mini"
editor = "gpt-4o-mini"
repair = "gpt-4o-mini"
summarizer = "gpt-4o-mini"

[budget]
# Active edit budget profile. normal is conservative; large requires explicit review before applying.
profile = "normal"            # normal | large | absolute

# Maximum implementation batches execute should create for one task.
max_batches = 3

# Optional cost guard when provider usage includes estimated cost.
cost_cap_usd = 2.0

[budget.normal]
# Edit proposal limits enforced after the model returns structured JSON.
max_files = 2
max_edits = 4
max_old_chars = 3000
max_new_chars = 4000
max_total_edit_chars = 12000
max_proposal_chars = 24000

[budget.large]
# Larger reviewed profile for multi-file changes.
max_files = 4
max_edits = 8
max_old_chars = 7000
max_new_chars = 12000
max_total_edit_chars = 24000
max_proposal_chars = 42000
```

## Section Reference

| Section | Used by | Meaning |
| --- | --- | --- |
| `[run]` | `run`, `resume` | Topic, output root, stage range, quiet mode, debug artifact retention, and rerun overwrite policy. |
| `[llm]` | pipeline and code task | LLM enablement, default model, and worker count. |
| `[search]` | `02-search` | Provider behavior, fallback policy, result limit, and manual query. |
| `[research]` | `02-search` | Research-question planning, query expansion, provider order, local documents, cache/index hints. |
| `[research.budget]` | `02-search` and future evidence stages | Lightweight caps used by research planning; retained in `planning/research_plan.json` only when debug artifacts are enabled. |
| `[retrieval]` | read/synthesize/report helpers | Local artifact retrieval context. |
| `[experiment]` | `05-design` to `07-run` | Experiment template, timeout, and optional nested code-task config path. |
| `[task]` | `05-design` and future implementation stages | Unified task identity, objective, optional task file, and optional source code root. |
| `[implementation]` | `05-design` and `06-code` | How code should be produced or changed: existing-project patch, fixed template path, or bounded greenfield generation. |
| `[workspace]` | code-task init and unified task config | Workspace strategy and setup metadata. |
| `[execution]` | design/run/code-task compatibility | Backend, command, timeout, streaming, and dependency-install policy. |
| `[resource]` | design and future implementation gates | Runtime, file-count, generated-line, memory, and GPU budgets. |
| `[evaluation]` | design, comparison, report | Primary metric, direction, required metrics, and success criteria. |
| `[generation]` | greenfield path | Batch/file generation budget and review policy. |
| `[report]` | `08-report` | Report structure mode. |
| `[code_task]` | standalone or embedded code task | Source project, task file, output root, display name. |
| `[benchmark]` | code task | Benchmark command and primary metric. |
| `[benchmark.metric_directions]` | code task comparison | Metric interpretation rules. |
| `[metrics]` | code task comparison | Alternative place for `primary`, `primary_metric`, `directions`, or `metric_directions`. |
| `[environment]` | code task execution | Python execution policy. |
| `[edit_scope]` | code-task init and all patch gates | Optional editable allowlist and extra read-only patterns. |
| `[safety]` | code-task init/validation | Copy size and validation scan limits. |
| `[execute]` | code-task execute | State-machine limits, runtime settings, repair rounds, output streaming. |
| `[models]` | code-task execute | Default model routing. |
| `[models.code_task]` | code-task execute | Planner/editor/repair/summarizer model routing. |
| `[budget]` | code-task execute | Edit budget profile, batch cap, cost cap. |
| `[budget.normal]`, `[budget.large]` | code-task execute | Per-profile edit proposal limits. |

## Key Field Notes

### Research Pipeline Fields

| Field | Meaning |
| --- | --- |
| `[run].topic` | Main user goal. It is used by planning, default search query generation, and report framing. |
| `[run].from_stage` / `[run].to_stage` | Stage range for partial runs. Use these to stop at `synthesize`, rerun `report`, or resume a subset. |
| `[run].debug_artifacts` | When `true`, keeps verbose diagnostics and draft handoff files such as search planning, provider traces, retrieval-selection rows, coverage review, section tables, debug cards, legacy evidence-pack diagnostics, design tool contracts, evidence review, eval report, and retention policy. Keep it `false` for compact default runs; operational artifacts are still retained under their owning stages: `02-search` documents/chunks, `03-read` review/Paper Briefs, `04-synthesize` synthesis brief/Markdown, and `05-design` experiment contracts. |
| `[run].overwrite_stage_artifacts` | Defaults to `false`. When rerunning `06-code` or `07-run`, existing reviewed artifacts are copied to `archives/<timestamp>/` before new outputs are written. Set `true` only when you explicitly want reruns to overwrite prior code/run artifacts without archive protection. |
| `[llm].enabled` | Turns LLM-backed planning/notes/synthesis/report/code-task steps on or off. Some real code-task steps need LLM mode to be useful. |
| `[llm].workers` | Parallelism for supported LLM stages. It does not make every pipeline stage concurrent. |
| `[search].offline` | Skips live literature providers. Useful for local demos and deterministic tests. |
| `[search].max_papers` | Maximum number of metadata rows requested/kept by the search stage across the selected source path. It is not a PDF-page or chunk limit. |
| `[search].query` | Manual provider query. If omitted, SimpleAutoResearch falls back to the topic or the first research query. |
| `[search].allow_fixture_fallback` | Allows placeholder metadata only when live/cache search fails. Keep false for serious evidence collection. |
| `[search].strict` | Fails immediately when search cannot produce real/cache results. Use this when fixture fallback would hide a bad run. |
| `[retrieval].top_k` | Number of local artifact chunks retrieved into later prompts when artifact retrieval is enabled. |
| `[report].mode` | `auto` chooses based on available experiment results; `research_only` avoids experiment claims; `experiment` expects results. |
| `[report].template` | Built-in report template name (`survey`, `experiment`, `reproduction`) or a custom Markdown path. `auto` follows `mode`. |
| `[report].criteria` | Built-in reviewer criteria or a custom Markdown path. `auto` follows `template`. |
| `[report].style` | Tone hint for report writing: `paper`, `technical`, or `concise`. |
| `[report].draft_sections` | When true, keeps Writer Agent section drafts under `08-report/sections/`. Default false keeps compact reports. |
| `[report].debug_artifacts` | When true, keeps reviewer findings, tool results, and iteration traces under `08-report/audit/` and `08-report/iterations/`. Default false. |
| `[report].agent` / `[report].reviewer` | Report writer/reviewer backend. V2.4 local path expects LLM for quality; disabled mode is a fallback. |
| `[report].max_review_iterations` | Maximum writer/reviewer revision rounds. |
| `[report].max_section_tokens` / `[report].max_report_tokens` | Token budgets for section drafting and final report assembly. |
| `[report].max_section_sources` | Maximum model-facing source handles assigned to each section plan. `0` exposes all selected paper-level handles; full-text chunks stay available through bounded backtracking tools. |
| `[report].source_strategy` | `full` drafts each section from the configured source set in one pass. `batch_refine` splits larger source sets and revises each section incrementally. |
| `[report].source_batch_size` | Number of source handles per batch when `source_strategy = "batch_refine"`. |
| `[report].max_source_batches` | Maximum batches per section in `batch_refine`; `0` means all batches. |
| `[report].review_source_batches` | When true, reviewer checks run after each `batch_refine` integration batch. This improves control but increases LLM cost. |
| `[report].output_mode` / `.output_label` | Controls reruns of 08-report: overwrite in place, archive the old report before overwriting, or write a separate variant package. |
| `[report].review_trace` | Reviewer trace retention: `off`, `meta`, or `full`. |
| `[report].allow_source_backtracking` | Allows report tools to retrieve bounded extra evidence from current-run source handles. |
| `[report].max_backtracking_calls` / `[report].max_backtracking_tokens` | Source-backtracking call and token budgets. |
| `[report.audit].citations` / `.metrics` / `.claims` | Enables citation, metric, and claim audit components. |
| `[report.audit].strict` | Reserved strict mode for blocking final reports on warnings; default false. |

### Unified Experiment And Coding Fields

These V2.5 foundation sections are preferred for new pipeline configs. They are
normalized into `task_config` and also mapped to legacy code-task keys where
needed, so existing embedded `code_task_project` runs keep working.

| Field | Meaning |
| --- | --- |
| `[task].kind` | `existing_project` uses a source project and controlled patches; `greenfield` / `benchmark_solution` use the bounded project-generation path. |
| `[task].code_root` / `.task_file` | Source project root and task Markdown. Paths are resolved relative to the config file. |
| `[implementation].mode` | `patch_existing` maps to controlled code-task behavior. `generate_project` plans, writes, reviews, and runs a bounded generated project under `06-code/generated_project`. |
| `[implementation].domain_profile` | Chooses planning defaults such as `generic_research_experiment`, `code_experiment`, `ml_experiment`, or `code_agent_eval`. |
| `[implementation].task_handoff` | Embedded existing-project runs only. `user_file` passes `[task].task_file` through unchanged. `merge` writes `05-design/generated_code_task.md` by combining the user task file as hard requirements with goal/problem/synthesis/hypothesis context. |
| `[execution].command` / `.timeout_sec` | Command and timeout used for benchmark/execution planning; for existing projects these map to legacy benchmark settings. |
| `[resource].max_files` / `.max_generated_lines` | Pre-code generation/edit budget written into `05-design/resource_plan.json`. |
| `[resource].max_memory_mb` / `.allow_gpu` | Runtime resource budget recorded in `resource_plan.json` and surfaced as contract constraints before code is generated or modified. |
| `[evaluation].primary_metric` / `.metric_directions` | Result schema and metric direction rules written into `05-design/result_schema.json`; existing code-task comparison also consumes them. |
| `[evaluation].required_metrics` / `.success_criteria` | Required metric checks and success notes used by `07-run/guard_report.json` and the final report. |
| `[generation].enabled` | Enables the greenfield project-generation path. Leave false for existing-project code-task runs. |
| `[generation].max_batches` / `.files_per_batch` / `.review_required` | Project-generation planning and review budget recorded into `05-design/experiment_contract.json` and consumed by `06-code`. |
| `[generation].allow_fallback_scaffold` | Defaults to false. When false, failed generated code stays available for inspection instead of being silently replaced by a deterministic scaffold. |

During `05-design`, these fields materialize as `experiment_plan.json`,
`experiment_contract.json`, `result_schema.json`, `resource_plan.json`,
`dependency_plan.json`, `domain_profile.json`, and `contract_validation.json`.
`06-code` refuses to continue when `contract_validation.json` reports a failed
pre-code contract.
During `07-run`, `results.json` is the canonical experiment result and includes
metric values, execution provenance, comparisons/verdicts when available, and
compact references to `resource_plan.json`, `code_review.json`, and
`guard_report.json` / `diagnosis.json`. `diagnosis.json` turns guard,
code-review, runtime, and missing-metric signals into readable repair/report
context. Reports should read experiment numbers from this canonical result
package rather than parsing stdout directly.

### Evidence Source Fields

| Field | Meaning |
| --- | --- |
| `[research].mode` | Records intended evidence depth: `lite` for metadata/local notes, `standard` for cache/index-ready use, `strong` for future full-text/vector workflows. |
| `[research].planner` | Research-question and query-expansion backend. `auto` calls the LLM when `[llm].enabled = true` and falls back to deterministic planning; `llm` explicitly requests that path; `deterministic` disables the extra LLM planner call. |
| `[research].sources` | Provider order for the search stage. Supported connector names today are `openalex`, `semantic_scholar`, `arxiv`, and `local_files`; `fixture` records offline fixture use. |
| `[research].queries` | Seed query list used by the research planner. Search executes planned queries in ordered-fallback rounds and can spend later round budget on uncovered facets. LLM planner output also records `query_specs` with title/abstract keyword hints; the full plan is retained only when debug artifacts are enabled. |
| `[research].auto_query_expansion` | Enables facet-driven follow-up queries from the planned research questions. In deterministic mode these are rule-based; in LLM planner mode the model can add stronger terminology within the same query budget. Disable it when you want only hand-written queries. |
| `[research].max_retrieval_rounds` | Planned number of retrieval/screening rounds for the DeepResearch loop. Values above `1` allow coverage-driven follow-up retrieval before `papers.jsonl` is finalized. |
| `[research].max_queries` | Maximum seed + expanded queries kept by the internal query plan. |
| `[research].required_facets` | Evidence facets to cover, such as `method`, `benchmark`, `dataset`, `code_link`, or `limitation`. These drive research questions and query expansion. |
| `[research].local_documents` | Markdown/text files treated as local research records. These paths are resolved relative to the config file and are also written to `02-search/documents/documents.jsonl` with parser/hash status. |
| `[research].use_fulltext` | Intent flag for full-text evidence workflows. When true, `documents/fulltext_manifest.json` can select eligible local/remote full-text hints within budget, and `documents/fulltext_extraction.json` records parser outcomes for cached/local inputs. |
| `[research].allow_pdf_download` | Permission flag for guarded remote PDF fetching. Keep false unless you explicitly want parser-backed full-text handling. |
| `[research].max_fulltext_documents` | Maximum number of documents that can be selected for full-text fetch/parse work. This is separate from `[research.budget].max_documents`, which caps kept metadata records. |
| `[research].max_pdf_mb` | Per-PDF size ceiling used by full-text planning. Local PDFs above this limit are skipped; future remote fetchers should enforce the same cap. |
| `[research].keep_raw_pdf` | Whether fetch/parsing steps should retain raw PDF files in cache. Keep false when you only need parsed text and section chunks. |
| `[research].parser_backend` | Parser backend. `basic` parses Markdown/text and simple HTML directly; `pypdf` uses the lightweight PDF parser when available; `unstructured` is an optional heavier parser backend and records a manifest failure if the package is not installed. |
| `[research].read_screening` | Read-stage review backend. `auto` uses the LLM two-step screen/rerank path when `[llm].enabled = true`; `llm` explicitly requests it; `deterministic` skips the extra review and keeps retrieval order. |
| `[research].read_batch_size` | Number of papers placed in each coarse title/abstract screening prompt. Smaller values are more precise but spend more LLM calls; defaults to `4` and is clamped to `1..8`. |
| `[research].read_workers` | Concurrent LLM workers for coarse screening batches. Defaults to the lower of `3` and `[llm].workers`, so large retrieval sets can be screened without one giant prompt. |
| `[research].read_max_shortlist` | Maximum number of papers kept for deeper Paper Briefs and synthesis after coarse screening and reranking. When omitted, small retrieval sets keep all papers and larger sets default to a bounded shortlist. |
| `[research].cache` | Allows live-provider failures to fall back to cached metadata when available. |
| `[research].index_backend` | Local index backend. `keyword` writes portable chunks only; `sqlite_fts` and `hybrid` update the shared SQLite FTS store; `lancedb` / `hybrid_lancedb` update the shared optional LanceDB store and degrade to a recorded status when LanceDB is not installed. |
| `[research].index_root` | Shared accelerator-store root for SQLite FTS / LanceDB. Defaults to `.simple_ar_cache/research_index`, or `SIMPLE_AR_RESEARCH_INDEX_ROOT` when set. Use `run` or `local` only when you intentionally want per-run index databases under `02-search/research_index/`. |
| `[research.budget].max_documents` | Max records the evidence stage should keep from all sources. |
| `[research.budget].max_chunks` | Planned cap for chunks after later full-text/local-document ingestion. |
| `[research.budget].max_context_tokens` | Planned prompt budget for evidence retrieval context. |
| `[research.budget].max_llm_calls` | Planned cap for research-side LLM actions such as query expansion and screening. |
| `[research.budget].max_follow_up_queries` | Maximum coverage-driven follow-up queries attempted in a second retrieval round. |
| `[research.budget].novelty_backend` | Backend for novelty hints inside `04-synthesize/synthesis_brief.json`. Current stable value is `local`, which records lexical risk hints from the current Paper Briefs only. |

### Code-Task Fields

| Field | Meaning |
| --- | --- |
| `[experiment].template` | `code_task_project` embeds the code-task workflow in the 8-stage pipeline. Other templates are deterministic teaching/demo paths. |
| `[experiment].timeout` | Timeout for stage `07-run`; for embedded code tasks it also constrains nested benchmark calls. |
| `[experiment].code_task_config` | Optional path to a standalone code-task TOML. Use this when you want pipeline and code-task settings in separate files. |
| `[code_task].code_root` | Source project path. The original project is not edited; a workspace is prepared under the run directory. |
| `[code_task].task_file` | User-facing task description. Required for standalone `code-task init`; embedded 8-stage runs can generate one when omitted, or merge it with research context when `[implementation].task_handoff = "merge"`. |
| `[benchmark].command` | Command executed inside `code_task/workspace` before and after edits. It should print parseable metrics such as `accuracy: 0.82`. |
| `[benchmark].primary_metric` | Main metric used for the objective verdict. Unknown metrics are still recorded, but need directions to decide improvement. |
| `[benchmark.metric_directions]` | Direction map for metrics: `higher`, `lower`, `resource`, or `ignore`. |
| `[environment].mode` | `current` uses the active SimpleAutoResearch Python; `external` uses `[environment].python`. No dependencies are installed automatically. |
| `[workspace].mode` | Workspace strategy: `copy`, `git_worktree`, or `sparse_copy`. |
| `[workspace].reuse_source_venv` | If a source `.venv` or `venv` is detected, record and use that Python as the execution interpreter. |
| `[workspace].setup_hook` | Stored for future managed environment support. It is not executed during init. |
| `[edit_scope].allowed_patterns` | Optional workspace-relative glob allowlist for files automated edits may touch. Empty means all normalized non-protected workspace paths are editable. |
| `[edit_scope].protected_patterns` | Additional workspace-relative glob patterns treated as read-only evidence. Defaults for tests, benchmarks, `.env`, secrets, and credentials are always retained. |
| `[edit_scope].mode` | Optional label stored in `manifest.json`; it does not change behavior by itself. |
| `[safety].max_file_bytes` | Max copied file size for copy/sparse modes. This avoids accidentally copying huge model/data artifacts. |
| `[safety].validation_max_file_bytes` | Max file size scanned by static validation. |

### Execute And Budget Fields

| Field | Meaning |
| --- | --- |
| `[execute].to_step` | Last step the state-aware executor may attempt. Use `propose-edits` to stop before applying patches. |
| `[execute].use_llm` | Enables or disables LLM-backed work-plan, patch-plan, edit-proposal, and repair steps. |
| `[execute].timeout_sec` | Benchmark timeout used by executor-managed baseline and patched runs. |
| `[execute].stream_benchmark_output` | Live benchmark log mode: `off`, `line`, `auto`, or `summary`. Use `auto` for tqdm-like progress output. |
| `[execute].apply_proposed_edits` | Lets execute apply an already reviewed proposal. Keep false for review-first workflows. |
| `[execute].allow_large_edits` | Allows application of reviewed proposals that exceed the normal budget but fit the large budget. |
| `[execute].allow_planning_fallback` | Allows deterministic offline work/patch plans after all LLM planning retries fail. Keep false for real LLM runs so malformed model output stops safely and can be retried. |
| `[execute].llm_retry_attempts` | Number of LLM work-plan and patch-plan attempts before stopping or explicitly falling back. |
| `[execute].repair_rounds` | Number of bounded repair proposals after validation/benchmark failure. Repairs still require review. |
| `[execute].max_files` | Max files included in LLM context for plan/proposal/repair steps. |
| `[execute].max_source_chars_per_file` | Per-file source snippet budget for LLM context. |
| `[models.code_task].planner` | Model used for work-plan and patch-plan generation. |
| `[models.code_task].editor` | Model used for edit proposal generation. |
| `[models.code_task].repair` | Model used for repair proposals after failures. |
| `[budget].profile` | Active edit budget profile. `normal` is conservative; `large` is for reviewed multi-file changes. |
| `[budget].max_batches` | Maximum number of implementation batches the executor may create for one code task. |
| `[budget].cost_cap_usd` | Optional cost cap when provider usage includes cost estimates. |
| `[budget.*].max_files` | Max files a single edit proposal may touch. |
| `[budget.*].max_edits` | Max old/new replacement operations in a proposal. |
| `[budget.*].max_old_chars` / `[budget.*].max_new_chars` | Per-edit old/new text character limits. |
| `[budget.*].max_total_edit_chars` | Total character budget across all edits. |
| `[budget.*].max_proposal_chars` | Total serialized proposal budget. |

## Research Source Variants

### Live OpenAlex/Semantic Scholar/arXiv Metadata

```toml
[search]
offline = false
max_papers = 10
query = "multi-agent collaboration for code generation"
allow_fixture_fallback = false
strict = false

[research]
mode = "standard"
planner = "auto"
sources = ["openalex", "semantic_scholar", "arxiv"]
queries = [
  "multi-agent collaboration for code generation",
  "LLM agents software engineering benchmark",
]
auto_query_expansion = true
max_retrieval_rounds = 2
max_queries = 6
required_facets = ["method", "benchmark", "dataset", "code_link"]
cache = true
index_backend = "keyword"
use_fulltext = false
allow_pdf_download = false
max_fulltext_documents = 6
max_pdf_mb = 20
keep_raw_pdf = false
parser_backend = "basic"
```

### Local Notes Only

```toml
[search]
offline = true
max_papers = 5

[research]
mode = "lite"
planner = "deterministic"
sources = ["local_files"]
queries = ["agent simulation evaluation"]
auto_query_expansion = true
max_retrieval_rounds = 1
max_queries = 4
required_facets = ["overview", "method", "benchmark"]
local_documents = [
  "../private_corpus/agent_simulation_notes.md",
]
cache = true
index_backend = "keyword"
```

### Offline Fixture Metadata

```toml
[search]
offline = true
max_papers = 3

[research]
mode = "lite"
planner = "deterministic"
sources = ["fixture"]
queries = ["tiny digits MLP baseline improvement"]
auto_query_expansion = true
max_retrieval_rounds = 1
max_queries = 4
```

## Workspace Mode Variants

### `copy`

`copy` is the safest default. It creates a guarded physical copy under
`code_task/workspace`.

```toml
[workspace]
mode = "copy"
reuse_source_venv = false
setup_hook = ""

[safety]
max_file_bytes = 2000000
```

### `git_worktree`

`git_worktree` creates a detached worktree for repo-root git projects. The
source project must be a local git repository with at least one commit. A remote
GitHub repository is not required.

```toml
[workspace]
mode = "git_worktree"
reuse_source_venv = true
setup_hook = ""
```

### `sparse_copy`

`sparse_copy` copies only allowlisted paths and always applies built-in
exclusions for `.git`, virtualenvs, `runs`, cache/build directories, data/model
directories, `.env`, and secret-like paths.

```toml
[workspace]
mode = "sparse_copy"
include = ["src/**", "tests/**", "configs/**", "main.py", "pyproject.toml"]
exclude = ["data/**", "models/**", "checkpoints/**"]
reuse_source_venv = false
```

Use `sparse_copy` only when you understand the project dependency graph. It can
omit files that runtime imports need.

## Standalone Code-Task Config

Use this with `simple-ar code-task init --config PATH` and later
`simple-ar code-task execute RUN_DIR --config PATH`.

```toml
[code_task]
code_root = "path/to/project"
task_file = "tasks/improve_model.md"
output_root = "runs"
name = "my-code-task"

[benchmark]
command = "python main.py --config configs/experiment.json"
primary_metric = "accuracy"

[benchmark.metric_directions]
accuracy = "higher"
macro_f1 = "higher"
latency_ms = "resource"
loss = "lower"

[environment]
mode = "current"
# mode = "external"
# python = ".venv/Scripts/python.exe"

[workspace]
mode = "copy"
reuse_source_venv = false
setup_hook = ""

[safety]
max_file_bytes = 2000000

[execute]
to_step = "run"
use_llm = true
timeout_sec = 120
repair_rounds = 1
stream_benchmark_output = "auto"
apply_proposed_edits = false
allow_large_edits = false
allow_planning_fallback = false
llm_retry_attempts = 2

[budget]
profile = "normal"
max_batches = 3

[budget.normal]
max_files = 2
max_edits = 4
max_old_chars = 3000
max_new_chars = 4000
max_total_edit_chars = 12000
max_proposal_chars = 24000
```

## Embedded Code-Task Config

For `simple-ar run --config PATH`, you can either keep code-task settings in
the same run config or point to a standalone code-task config:

```toml
[experiment]
template = "code_task_project"
timeout = 120
code_task_config = "../code_tasks/configs/my_project.toml"
```

If `[experiment].code_task_config` is omitted and the run config contains
`[code_task]`, `[benchmark]`, `[metrics]`, `[environment]`, `[safety]`, or
`[edit_scope]`, the run config itself is reused as the embedded code-task config.

## Execute And Budget

`execute` is state-aware. The config below controls how far it may proceed,
which models it uses, how much context it includes, and what edit size is
allowed.

```toml
[execute]
to_step = "run"
use_llm = true
timeout_sec = 60
repair_rounds = 1
max_files = 8
max_source_chars_per_file = 4000
stream_benchmark_output = "auto"
apply_proposed_edits = false
allow_large_edits = false
allow_planning_fallback = false
llm_retry_attempts = 2

[models.code_task]
planner = "gpt-4o-mini"
editor = "gpt-4o-mini"
repair = "gpt-4o-mini"

[budget]
profile = "normal"
max_batches = 3
cost_cap_usd = 2.0

[budget.normal]
max_files = 2
max_edits = 4
max_old_chars = 3000
max_new_chars = 4000
max_total_edit_chars = 12000
max_proposal_chars = 24000
```

`stream_benchmark_output` values:

| Value | Meaning |
| --- | --- |
| `off` / `false` | Do not relay benchmark logs live. |
| `line` | Relay newline-delimited output. |
| `auto` / `true` | Handle regular line logs and carriage-return progress such as `tqdm`. |
| `summary` | Print only a tail summary after the benchmark finishes. |

## Edit Scope Behavior

`[edit_scope]` is enforced in multiple places: repo-map role tagging, context
selection, work-plan normalization, edit proposal normalization, repair
proposal normalization, and final `apply-edits` validation.

- The source project is edited only inside `code_task/workspace`.
- If `allowed_patterns` is empty, any normalized workspace-relative path may be
  edited unless protected.
- If `allowed_patterns` is set, an edit path must match at least one allowed
  pattern and must not match any protected pattern.
- Default protected patterns for tests, benchmarks, `.env`, secrets, and
  credential-like paths are always retained. User `protected_patterns` add to
  that baseline rather than replacing it.
- Work-plan target files still constrain the current batch, so a file can be
  allowed by `[edit_scope]` but rejected for being outside the active batch.
- `apply-edits` rechecks workspace-relative paths, edit scope, active batch
  target files, and exact old-text matches before writing.
