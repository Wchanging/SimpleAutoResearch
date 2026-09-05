# SimpleAutoResearch

[中文版本](README_zh.md)

SimpleAutoResearch is a teaching-first, lightweight auto-research project
inspired by [AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw).
It explores how an automated research assistant can move from a topic to
literature notes, small experiments, existing-code improvement tasks,
executable results, and Markdown reports while keeping the process visible and
hackable.

The goal is not to reproduce every feature of a large agent framework. The goal
is to build a clear, inspectable version that is useful for learning,
experimentation, and gradual extension.

## Goals

- Keep research steps explicit and file-based.
- Make runs easy to inspect, resume, and debug.
- Support both literature/report workflows and existing-code improvement
  workflows.
- Prefer controlled, reproducible experiments over unconstrained code
  generation.
- Keep the codebase small enough for learners and contributors to understand.

## What Works Today

- **V2.8 canonical research session**: run the bounded
  `plan -> search -> document_ingest -> read -> synthesize -> research_design
  -> experiment -> analysis -> report -> report_audit` path. The mainline uses
  explicit provider, artifact, metric, timeout, and continuation boundaries;
  the model-backed CLI requests the report by default.
- **Research sources**: the canonical session can search
  OpenAlex/Semantic Scholar/arXiv/local files through the provider-neutral
  connector boundary, with bounded document extraction and evidence cards.
  Optional LLM planning, bounded reading/screening, paper notes, and synthesis
  are explicit. The older facet expansion and multi-round retrieval behavior
  still belongs to the frozen `simple-ar run/resume` compatibility path and is
  not a second V2.8 mainline.
- **Code tasks**: improve an existing codebase or generate a bounded
  greenfield project inside an isolated editable workspace with LLM planning,
  task memory, review gates, controlled patch/generation artifacts,
  validation, benchmark execution, and metric comparison.
- **Workspace strategies**: use `copy` for the safest isolated copy,
  `git_worktree` for larger git repositories where full copying is wasteful,
  or experimental `sparse_copy` for small allowlisted subsets.
- **Research-to-code runs**: the canonical session can explicitly route one
  prepared project and one Code-Task TOML through the isolated Code-Task
  backend, then reuse the normal experiment, analysis, report, and audit
  handoffs. Candidate matrices and autonomous repair loops are out of scope
  for V2.8.
- **Deferred integrations**: read-only tool schemas, MCP exposure, and external
  Agent/Harness adapters exist as boundaries or compatibility surfaces, but are
  frozen for now. Claude Code, Codex, OpenCode, and similar Harness paths are
  post-V2.8 work, not required for the current acceptance target.
- **Reviewable artifacts**: each run writes inspectable files under `runs/`
  instead of hiding decisions inside process memory.
- **Capability boundary for contributors**: new modules can use the small
  `ArtifactStore`/`CapabilityResult`/bounded-attempt API without changing the
  established 8-stage and code-task entry points. The offline reference is in
  `examples/capability_package_minimal/`.
- **Mature library foundation**: pipeline/code-task TOML configs are validated
  through Pydantic, LLM calls use the OpenAI Python SDK by default with a
  LiteLLM compatibility option, OpenAlex access goes through pyalex, and
  terminal progress uses Rich as a first step toward cleaner human-in-the-loop
  review.

## Install And Configure

Clone the repository:

```bash
git clone https://github.com/Wchanging/SimpleAutoResearch.git
cd SimpleAutoResearch
```

Install dependencies with `uv`:

```bash
uv sync
```

Create your local environment file:

```bash
cp .env.example .env
```

On PowerShell:

```powershell
Copy-Item .env.example .env
```

Edit `.env` for LLM-backed stages:

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

For third-party OpenAI-compatible providers, set `OPENAI_BASE_URL` to that
provider's `/v1` endpoint. Price fields are optional; when unset,
SimpleAutoResearch records token counts but leaves estimated cost as `null`.
Each new usage row also records the provider-call count, while legacy usage
rows remain readable.
Transient provider failures such as connection resets, rate limits, timeouts,
and 5xx responses use bounded exponential backoff controlled by the retry
settings above. JSON-producing calls use prompt-only parsing by default for
provider compatibility. Set `SIMPLE_AR_JSON_RESPONSE_FORMAT=auto` or
`json_object` only when your provider supports native JSON response formatting.
`SIMPLE_AR_LLM_BACKEND=openai` uses the OpenAI Python SDK directly and is the
default transport. Set it to `litellm` only when you need the older LiteLLM
compatibility layer for a non-standard provider.
Leave `SIMPLE_AR_LLM_TIMEOUT_SEC` and `SIMPLE_AR_MAX_OUTPUT_TOKENS` empty
or set them to `0`/`off`/`none` to omit client-side timeout and provider
output-limit parameters. Set positive values only when you intentionally want
to bound request time or output size.
`SIMPLE_AR_LLM_API=responses` uses Responses API-style `instructions` and
`input` and retries transient failures on that same API only. Set it to `chat`
when your provider should always use Chat Completions directly. Use the
explicit `auto` mode only when you want a compatibility fallback from
Responses to Chat after the bounded retries.

## Quickstart

### 1. V2.8 canonical research session

The mainline is `research-session`: it keeps one bounded handoff from
planning and network/local search through document evidence, one prepared
experiment, result analysis, report writing, and audit. Start with the
laptop-safe complete fixture:

```bash
uv run python examples/research_session_smoke.py
```

For the real network + LLM path, use the bounded command in
`examples/README.md`. It requires a valid OpenAI-compatible model/gateway and
does not replace provider failures with fixture output. A prepared existing
project can be added with `--code-task-config`; V2.8 runs one direction at a
time.

### 2. Compatibility research report

```bash
uv run simple-ar run --topic "agent simulation" --to-stage report --max-papers 5
```

For repeatable source settings, use a run config. The bundled research-report
example uses live academic sources, bounded full-text extraction, and
research-only report generation:

```bash
uv run simple-ar run --config examples/research_report/configs/research_report.toml
```

For a literature-only pass, stop at `synthesize`, then resume report generation
from the printed run directory:

```bash
uv run simple-ar run --topic "agent simulation" --to-stage synthesize
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode research_only
```

The V2.4 report path uses Markdown report templates plus an LLM
Writer/Reviewer loop. It supports short citation keys, audit artifacts, report
variants, and either full-source drafting or batch-refine drafting for larger
paper sets. See `examples/research_report/configs/research_report.toml` and
[Usage And Configuration](docs/USAGE.md) for practical commands.

### 3. Existing-Code Code Task

Use this when you already have a project and want the model to propose a
reviewable improvement. First write a small task file, for example
`tasks/improve_model.md`, that says what should change and what benchmark should
improve. Then create a TOML config for your project:

```toml
[code_task]
code_root = "path/to/your/project"
task_file = "tasks/improve_model.md"
output_root = "runs"
name = "my-code-task"

[benchmark]
command = "python benchmark.py"
primary_metric = "accuracy"

[benchmark.metric_directions]
accuracy = "higher"
latency_ms = "resource"

[workspace]
mode = "auto"  # auto | copy | git_worktree | sparse_copy
```

Then run the reviewed flow. `init` prints a run directory such as
`runs/20260523-xxxx-my-code-task`; use that path in place of `runs/<run-id>`.
On an interactive terminal, `code-task execute` can continue through review
gates after you answer `yes`. The explicit commands below are the same flow in
a review-first form that also works in non-interactive shells.

```bash
uv run simple-ar code-task init --config path/to/your_code_task.toml
uv run simple-ar code-task execute runs/<run-id> --config path/to/your_code_task.toml
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve --note "reviewed"
uv run simple-ar code-task execute runs/<run-id> --config path/to/your_code_task.toml --to-step propose-edits
uv run simple-ar code-task execute runs/<run-id> --config path/to/your_code_task.toml --apply-proposed-edits --timeout 60
uv run simple-ar status runs/<run-id>
```

That sequence prepares an isolated workspace, runs the baseline benchmark,
builds a work plan, stops for patch-plan review, generates
`code_task/meta/proposed_edits.json`, applies the reviewed proposal, validates
the patched workspace, runs structured post-apply/post-run reviews, runs the
patched benchmark, and writes the final status.
If the result needs a bounded follow-up, use the repair path documented in
[Usage And Configuration](docs/USAGE.md#recommended-path-toml--execute).

The bundled standalone code-task example is
`examples/code_task_medium_review/configs/code_task.toml`; it is documented in
[Usage And Configuration](docs/USAGE.md#recommended-path-toml--execute).

### 4. Compatibility 8-stage research with experiment

Use this when you want the research pipeline to produce literature context,
derive or use a code task, run an experiment, and include the code evidence in
the final report. For your own project, create a top-level run config:

```toml
[run]
topic = "research and improve my model"
output_root = "runs"
to_stage = "report"

[llm]
enabled = true
# Online stages fail after bounded provider retries by default. Set true only
# when an explicit deterministic fallback is acceptable for a demo.
allow_fallback = false

[search]
offline = false
max_papers = 5

[research]
# Optional source planner for 02-search.
mode = "standard"  # lite | standard | strong
sources = ["openalex", "semantic_scholar", "arxiv"]
queries = ["research and improve my model"]
cache = true

[experiment]
template = "code_task_project"
timeout = 120

[code_task]
code_root = "path/to/your/project"
# Optional. If omitted, 05-design generates a task file from research artifacts
# and a compact codebase summary.
task_file = "tasks/improve_model.md"
name = "my-research-code-task"

[benchmark]
command = "python benchmark.py"
primary_metric = "accuracy"

[benchmark.metric_directions]
accuracy = "higher"
latency_ms = "resource"

[workspace]
mode = "auto"  # auto | copy | git_worktree | sparse_copy

[environment]
mode = "current"
```

Then run the full pipeline:

```bash
uv run simple-ar run --config path/to/your_pipeline.toml
```

This creates a normal 8-stage run. During `06-code`, it prepares the configured
project under `06-code/code_task_run/code_task/workspace`, builds repo maps and
context packs, asks the LLM for a work plan and patch proposal, applies the
patch inside the isolated workspace, and validates it. During `07-run`, it runs
the patched benchmark, projects metrics into canonical `results.json`, writes
`guard_report.json`, and compares nested code-task metrics when available.
During `08-report`, it writes a report with deterministic code-task evidence
pointing back to the nested work plan, patch, benchmark, and comparison
artifacts.

The embedded path is designed to finish end to end, so it auto-approves the
patch plan inside the isolated workspace. Use standalone `code-task` commands
when you want explicit human approval before each step. A bundled demo config is
available at `examples/full_pipeline_tiny_mlp/configs/pipeline.toml`; full embedded
workflow details are in [Usage And Configuration](docs/USAGE.md#embedded-code-task-in-the-8-stage-pipeline).
Set `[implementation].task_handoff = "merge"` when you want an existing
`task.md` to stay authoritative while `05-design` enriches it with the earlier
research context before entering code-task execution.

### 5. Greenfield Experiment

Use this when the task has no existing source project yet. The current path
uses the same code-task engine as existing-code tasks: `05-design` writes an
experiment contract, `06-code` creates a nested `kind = "greenfield"` code-task
run under `06-code/code_task_run/`, and the generated project is projected back
to `06-code/generated_project/` for `07-run`. Rerunning `code` or `run` archives
existing reviewed artifacts by default, and reports consume canonical results,
resource limits, guard status, and code-review signals rather than raw stdout
alone.

A lightweight public example is available at
`examples/greenfield_lightweight_training/configs/greenfield_training.toml`.
It asks the pipeline to generate a medium-light CPU-only text-classification
experiment suite from scratch, with deterministic local data, multiple
baseline/model conditions, and parseable metrics:

```bash
uv run simple-ar run --config examples/greenfield_lightweight_training/configs/greenfield_training.toml --to-stage run
```

Use this as a local greenfield structure check: it exercises task Markdown
handoff, architecture/file planning, multi-file generation, code review, run
guards, and diagnosis. For stronger greenfield tasks, keep the same config shape
but raise budgets deliberately and make the task-specific metric schema
explicit.

Greenfield code-task runs also write `code_task/meta/dependency_advice.json`
from a dynamic scan of the active Python environment. The terminal only shows
the task-relevant subset, while the JSON keeps the full package snapshot for
planning and audit. Review failures can trigger bounded LLM repair for generic
recoverable issues such as fallback core files or missing artifact writers; the
repaired file metadata is synced before validation and benchmark execution.

## Capability Boundaries

SimpleAutoResearch is useful as a learning and prototyping framework, but it is
still intentionally conservative.

- Code edits use controlled old/new replacements. This keeps patches auditable,
  but it is weaker than a full autonomous coding agent.
- The default edit scope protects tests, benchmark files, and secret-like paths
  from automated patching.
- `auto` prefers a detached worktree for Git projects with at least one local
  commit; `code_root` may be either the repository root or a project
  subdirectory inside it. If Git cannot be used safely, `auto` falls back to
  copy and records the reason; explicit `git_worktree` fails with a repair
  checklist instead.
- `sparse_copy` is experimental and can omit runtime dependencies if the
  allowlist is too narrow.
- The tool does not yet install project dependencies or manage
  Docker/Conda/GPU/Slurm environments.
- Large code-edit proposals may still produce long LLM completions. Current
  experiment/code execution uses explicit contracts, resource budgets,
  canonical results, guards, bounded greenfield generation, and optional
  external-agent handoff, but it is still not a recommended unattended large
  refactoring tool.
- Literature search now has an auditable source plan and document-store
  metadata, and can use OpenAlex, Semantic Scholar, arXiv, or local
  Markdown/text notes. It can parse local/cached Markdown, text, basic HTML, and
  lightweight `pypdf` PDFs. Optional `unstructured` and LanceDB hooks exist, but
  it is not yet a full section-aware PDF parser or vector-RAG survey system.
- LLM-written reports are guarded by citation, metric, and boundary checks; when
  a draft fails these checks, the tool falls back to a structured deterministic
  report.

## Documentation

- [Usage And Configuration](docs/USAGE.md): setup, workflow-oriented examples,
  artifacts, and troubleshooting.
- [CLI Reference](docs/CLI_REFERENCE.md): command groups and option tables.
- [Configuration Reference](docs/CONFIG_REFERENCE.md): TOML sections, complete
  config examples, and workspace-mode variants.
- [Workflows And Artifacts](docs/WORKFLOWS.md): workflow presets, the 8-stage
  pipeline, and artifact layouts.
- [Development Guide](docs/DEVELOPMENT.md): how to extend stages, templates, and
  code-task modules.
- [Changelog](CHANGELOG.md): chronological development progress.

## Reference

The main reference project is
[aiming-lab/AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw).
SimpleAutoResearch borrows the staged research idea, but keeps the
implementation intentionally compact and learning-friendly.

## Community

This is an early learning-oriented project. Issues, suggestions, experiments,
and small focused pull requests are welcome, especially around coding-agent
workflows, reproducible experiment execution, report quality, and documentation
clarity.
