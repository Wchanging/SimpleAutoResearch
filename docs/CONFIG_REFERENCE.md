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
- Relative paths in top-level run config are resolved relative to the config file when the parser explicitly supports path resolution, such as `[experiment].code_task_config` and `[research].local_documents`.
- When a run config contains `[code_task]`, `[benchmark]`, `[metrics]`, `[environment]`, `[workspace]`, or `[safety]`, the same file can be reused as the embedded code-task config.

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
# Search strategy profile recorded in planning/research_plan.json. Full-text behavior is still future-facing.
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
parser_backend = "basic"

# Whether live-provider failures may use cached metadata.
cache = true

# Planned local index backend. Current V2.3 search records this choice for later ingestion.
index_backend = "keyword"     # keyword | sqlite_fts | hybrid

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
# code_task_config = "examples/code_tasks/configs/tiny_digits_mlp.toml"

[report]
# auto chooses experiment or research-only report based on available results.
mode = "auto"                 # auto | research_only | experiment

[code_task]
# Source project copied/worktree-prepared into code_task/workspace.
code_root = "examples/code_tasks/tiny_digits_mlp_project"

# Task description for standalone or embedded code-task work.
task_file = "examples/code_tasks/tasks/improve_tiny_digits_mlp.md"

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

[workspace]
# copy is safest; git_worktree is lighter for git repo roots; sparse_copy is allowlist-based.
mode = "copy"                 # copy | git_worktree | sparse_copy

# Reuse source .venv/venv Python if detected. No dependency installation is performed.
reuse_source_venv = false

# Recorded for future managed environments; init does not execute this command.
setup_hook = ""

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
| `[run]` | `run`, `resume` | Topic, output root, stage range, and quiet mode. |
| `[llm]` | pipeline and code task | LLM enablement, default model, and worker count. |
| `[search]` | `02-search` | Provider behavior, fallback policy, result limit, and manual query. |
| `[research]` | `02-search` | Research-question planning, query expansion, provider order, local documents, cache/index hints. |
| `[research.budget]` | `02-search` and future evidence stages | Lightweight caps written to `planning/research_plan.json`. |
| `[retrieval]` | read/synthesize/report helpers | Local artifact retrieval context. |
| `[experiment]` | `05-design` to `07-run` | Experiment template, timeout, and optional nested code-task config path. |
| `[report]` | `08-report` | Report structure mode. |
| `[code_task]` | standalone or embedded code task | Source project, task file, output root, display name. |
| `[benchmark]` | code task | Benchmark command and primary metric. |
| `[benchmark.metric_directions]` | code task comparison | Metric interpretation rules. |
| `[metrics]` | code task comparison | Alternative place for `primary`, `primary_metric`, `directions`, or `metric_directions`. |
| `[environment]` | code task execution | Python execution policy. |
| `[workspace]` | code-task init | Workspace mode and setup metadata. |
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
| `[llm].enabled` | Turns LLM-backed planning/notes/synthesis/report/code-task steps on or off. Some real code-task steps need LLM mode to be useful. |
| `[llm].workers` | Parallelism for supported LLM stages. It does not make every pipeline stage concurrent. |
| `[search].offline` | Skips live literature providers. Useful for local demos and deterministic tests. |
| `[search].max_papers` | Maximum number of metadata rows requested/kept by the search stage across the selected source path. It is not a PDF-page or chunk limit. |
| `[search].query` | Manual provider query. If omitted, SimpleAutoResearch falls back to the topic or the first research query. |
| `[search].allow_fixture_fallback` | Allows placeholder metadata only when live/cache search fails. Keep false for serious evidence collection. |
| `[search].strict` | Fails immediately when search cannot produce real/cache results. Use this when fixture fallback would hide a bad run. |
| `[retrieval].top_k` | Number of local artifact chunks retrieved into later prompts when artifact retrieval is enabled. |
| `[report].mode` | `auto` chooses based on available experiment results; `research_only` avoids experiment claims; `experiment` expects results. |

### Evidence Source Fields

| Field | Meaning |
| --- | --- |
| `[research].mode` | Records intended evidence depth: `lite` for metadata/local notes, `standard` for cache/index-ready use, `strong` for future full-text/vector workflows. |
| `[research].planner` | Research-question and query-expansion backend. `auto` calls the LLM when `[llm].enabled = true` and falls back to deterministic planning; `llm` explicitly requests that path; `deterministic` disables the extra LLM planner call. |
| `[research].sources` | Provider order for the search stage. Supported connector names today are `openalex`, `semantic_scholar`, `arxiv`, and `local_files`; `fixture` records offline fixture use. |
| `[research].queries` | Seed query list written into `02-search/planning/research_plan.json`. Search executes planned queries in ordered-fallback rounds and can spend later round budget on uncovered facets. LLM planner output also records `query_specs` with title/abstract keyword hints. |
| `[research].auto_query_expansion` | Enables facet-driven follow-up queries from the `research_questions` section of `planning/research_plan.json`. In deterministic mode these are rule-based; in LLM planner mode the model can add stronger terminology within the same query budget. Disable it when you want only hand-written queries. |
| `[research].max_retrieval_rounds` | Planned number of retrieval/screening rounds for the DeepResearch loop. Values above `1` allow coverage-driven follow-up retrieval before `papers.jsonl` is finalized. |
| `[research].max_queries` | Maximum seed + expanded queries kept in the `query_plan` section of `planning/research_plan.json`. |
| `[research].required_facets` | Evidence facets to cover, such as `method`, `benchmark`, `dataset`, `code_link`, or `limitation`. These drive research questions and query expansion. |
| `[research].local_documents` | Markdown/text files treated as local research records. These paths are resolved relative to the config file and are also written to `02-search/documents/documents.jsonl` with parser/hash status. |
| `[research].use_fulltext` | Intent flag for full-text evidence workflows. When true, `documents/fulltext_manifest.json` can select eligible local/remote full-text hints within budget, and `documents/fulltext_extraction.json` records parser outcomes for cached/local inputs. |
| `[research].allow_pdf_download` | Permission flag for guarded remote PDF fetching. Keep false unless you explicitly want parser-backed full-text handling. |
| `[research].max_fulltext_documents` | Maximum number of documents that can be selected for full-text fetch/parse work. This is separate from `[research.budget].max_documents`, which caps kept metadata records. |
| `[research].max_pdf_mb` | Per-PDF size ceiling used by full-text planning. Local PDFs above this limit are skipped; future remote fetchers should enforce the same cap. |
| `[research].keep_raw_pdf` | Whether fetch/parsing steps should retain raw PDF files in cache. Keep false when you only need parsed text and section chunks. |
| `[research].parser_backend` | Parser backend hint, such as `basic`, `pypdf`, `pymupdf`, or `external`. The current implementation parses Markdown/text and basic HTML directly, and uses optional `pypdf` for PDFs when available. |
| `[research].cache` | Allows live-provider failures to fall back to cached metadata when available. |
| `[research].index_backend` | Local index backend. `keyword` writes portable chunks only; `sqlite_fts` writes chunks plus a SQLite FTS database; `hybrid` is reserved for FTS plus future stronger adapters. |
| `[research.budget].max_documents` | Max records the evidence stage should keep from all sources. |
| `[research.budget].max_chunks` | Planned cap for chunks after later full-text/local-document ingestion. |
| `[research.budget].max_context_tokens` | Planned prompt budget for evidence retrieval context. |
| `[research.budget].max_llm_calls` | Planned cap for research-side LLM actions such as query expansion and screening. |
| `[research.budget].max_follow_up_queries` | Maximum coverage-driven follow-up queries attempted in a second retrieval round. |

### Code-Task Fields

| Field | Meaning |
| --- | --- |
| `[experiment].template` | `code_task_project` embeds the code-task workflow in the 8-stage pipeline. Other templates are deterministic teaching/demo paths. |
| `[experiment].timeout` | Timeout for stage `07-run`; for embedded code tasks it also constrains nested benchmark calls. |
| `[experiment].code_task_config` | Optional path to a standalone code-task TOML. Use this when you want pipeline and code-task settings in separate files. |
| `[code_task].code_root` | Source project path. The original project is not edited; a workspace is prepared under the run directory. |
| `[code_task].task_file` | User-facing task description. Required for standalone `code-task init`; embedded 8-stage runs can generate one when omitted. |
| `[benchmark].command` | Command executed inside `code_task/workspace` before and after edits. It should print parseable metrics such as `accuracy: 0.82`. |
| `[benchmark].primary_metric` | Main metric used for the objective verdict. Unknown metrics are still recorded, but need directions to decide improvement. |
| `[benchmark.metric_directions]` | Direction map for metrics: `higher`, `lower`, `resource`, or `ignore`. |
| `[environment].mode` | `current` uses the active SimpleAutoResearch Python; `external` uses `[environment].python`. No dependencies are installed automatically. |
| `[workspace].mode` | Workspace strategy: `copy`, `git_worktree`, or `sparse_copy`. |
| `[workspace].reuse_source_venv` | If a source `.venv` or `venv` is detected, record and use that Python as the execution interpreter. |
| `[workspace].setup_hook` | Stored for future managed environment support. It is not executed during init. |
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
  "../research/local_agent_simulation_notes.md",
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
`[code_task]`, `[benchmark]`, `[metrics]`, `[environment]`, `[workspace]`, or
`[safety]`, the run config itself is reused as the embedded code-task config.

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

## Current Edit Scope Behavior

Current public TOML does not yet expose a custom `[edit_scope]` allow/deny
section. Code-task runs still enforce a default edit-scope baseline:

- source project is edited only inside `code_task/workspace`
- tests, benchmark files, `.env`, and secret/credential-looking paths are treated as read-only evidence
- work-plan target files constrain later edit proposals
- `apply-edits` rechecks workspace-relative paths, protected patterns, allowed target files, and exact old-text matches before writing

Configurable allow/deny edit-scope rules are planned for V2.3, but should not
be treated as implemented until they appear in this reference.
