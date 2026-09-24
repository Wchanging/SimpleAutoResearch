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

## Version status

V2.8 is a milestone baseline for an end-to-end workflow with prepared code,
data, and an execution protocol. Real runs cover search, research and design,
scoped code changes, experiments, analysis, and Markdown reports. Those runs
included framework fixes and recovery; they are not evidence of a single
frozen-version acceptance run or fully validated paper quality. Remaining
report factual-correction validation and frozen-version acceptance move to V2.9
delivery and release work. The V2.9 development branch has exercised supplied-material
analysis and a small code repair through short task plans. Explicit continuation
allowances and selective recovery are implemented and locally tested: they preserve
history, reuse unaffected evidence, and rebuild reports from retained analysis.
New research sessions default to uncapped API usage, with optional explicit caps.
Research input requests pause before further execution; Rich shows decision reasons
and scientific rounds. A real research-loop diagnostic exists, but frozen acceptance,
three interaction policies and Stage E remain incomplete. Outcome-aware report
structure selection is implemented; its live scientific and writing quality still
requires acceptance.
Open-web quality also needs revalidation after earlier provider rate limiting.
The target input is a natural-language task, available assets and necessary resource
and permission settings, not a user-authored stage sequence. Module upgrades accompany
the core stages; see [development targets and handoffs](docs/DEVELOPMENT.md#v29-development-target-decision-context-and-handoffs).

## Goals

- Keep research steps explicit and file-based.
- Make runs easy to inspect, resume, and debug.
- Support both literature/report workflows and existing-code improvement
  workflows.
- Prefer controlled, reproducible experiments over unconstrained code
  generation.
- Keep the codebase small enough for learners and contributors to understand.

## What Works Today

- **V2.8 canonical research session**: the shared application follows a bounded
  accepted task plan; not every task runs every capability in the historical
  `plan -> search -> document_ingest -> read -> synthesize -> research_design
  -> experiment -> analysis -> report -> report_audit` sequence. The mainline uses
  explicit provider, artifact, metric, timeout, and continuation boundaries;
  the model-backed CLI requests the report by default. The path has completed
  one real AutoDL prepared-project run with 60 raw and 10 selected/documents
  across network/LLM/CodeTask/experiment/report; this proves the bounded
  foundation, not autonomous research for arbitrary tasks.
- **Entrypoint hierarchy**: for normal V2.8 use, remember only
  `simple-ar research-session`. `research-session-continue` and
  `research-report` continue the same session; `research-brief` is a segmented development or diagnostic interface.
  The old `research-experiment`, `research-code-task` and `simple-ar run/resume` execution commands are
  retired; historical artifacts remain readable through `status` and artifact tools.
- **Research sources**: the canonical session can search
  OpenAlex/Semantic Scholar/arXiv/local files through the provider-neutral
  connector boundary, with bounded document extraction and evidence cards.
  Optional LLM planning, bounded reading/screening, paper notes, and synthesis
  are explicit. The old eight-stage retrieval strategy is no longer a supported
  execution route.
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
  handoffs. Bounded paired measurements and technical repair/retest are
  available when explicitly configured; autonomous research-direction loops
  remain out of scope for V2.8.
- **Deferred integrations**: read-only tool schemas, MCP exposure, and external
  Agent/Harness adapters exist as boundaries or compatibility surfaces, but are
  frozen for now. Claude Code, Codex, OpenCode, and similar Harness paths are
  post-V2.8 work, not required for the current acceptance target.
- **Reviewable artifacts**: each run writes inspectable files under `runs/`
  instead of hiding decisions inside process memory.
- **Capability boundary for contributors**: new modules can use the small
  `ArtifactStore`/`CapabilityResult`/bounded-attempt API while the formal
  research flow stays orchestrated by `research-session`. The old eight-stage
  and segmented code-task surfaces remain only for compatibility or development
  until migration is complete. The offline reference is in
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

The framework does not require scikit-learn. For the bundled classical-ML
examples and their tests, use `uv sync --extra examples` and run commands with
`uv run --extra examples ...`. For pip installations, use `pip install '.[examples]'`.

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
SIMPLE_AR_CHAT_TOKEN_LIMIT_PARAM=auto
SIMPLE_AR_LLM_REASONING_EFFORT=
SIMPLE_AR_LLM_REASONING_OUTPUT_TOKENS=
SIMPLE_AR_LLM_TIMEOUT_SEC=180
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
`SIMPLE_AR_LLM_TIMEOUT_SEC` defaults to 180 seconds per provider attempt;
set it to a larger positive value for slow providers, or to `0`/`off`/`none`
to explicitly disable the client-side timeout. `SIMPLE_AR_MAX_OUTPUT_TOKENS`
remains uncapped by default; set a positive value only when you intentionally
want to bound response size.
`SIMPLE_AR_LLM_API=responses` uses Responses API-style `instructions` and
`input` and retries transient failures on that same API only. Set it to `chat`
when your provider should always use Chat Completions directly. Use the
explicit `auto` mode only when you want a compatibility fallback from
Responses to Chat after the bounded retries.
For compatible reasoning models, `SIMPLE_AR_LLM_REASONING_EFFORT` forwards a
documented effort value such as `low` to the Chat request; leave it empty for
models that do not document this option. `SIMPLE_AR_LLM_REASONING_OUTPUT_TOKENS`
can expand an explicit per-call cap to leave room for reasoning, but does not
create a cap when the caller has not set one. These are generic capability
settings, not a hard-coded provider integration.

## Quickstart

### 1. V2.8 canonical research session

For file-based use, start with `simple-ar research-session --config examples/research_config/minimal.toml`.
See the [configuration reference](docs/CONFIG_REFERENCE.md) for the shared minimal/advanced format.

The mainline is `research-session`: one bounded application follows the accepted
plan for the task and available assets. Search, document evidence, execution,
analysis, and reporting are included only when that plan and supplied protocol
call for them. Start with the
laptop-safe complete fixture:

```bash
uv run python examples/research_session_smoke.py
```

For the real network + LLM path, use the bounded command in
`examples/README.md`. It requires a valid OpenAI-compatible model/gateway and
does not replace provider failures with fixture output. A prepared existing
project can be added with `--code-task-config`; V2.8 runs one direction at a
time.

For a literature-only pass, omit both `--command` and
`--code-task-config`. Without `--model` the session ends with an evidence-backed
summary; with `--model` it can continue to the research-only Markdown report.
No experiment process is created in this mode.

For a prepared low-cost experiment, one literal command can serve a bounded
seed protocol without hand-writing every pair:

```toml
[execution]
command = ["python", "measure.py"]
cwd = "."
timeout_sec = 30
seed_count = 2
seed_flag = "--seed"
baseline_policy = "skip" # run, skip, or reuse a same-condition artifact
```

The accepted plan records the selected conditions and runs only the existing
execution backend. `reuse` requires a passed framework-produced `baseline_ref`;
it checks the command, result schema, and the declared data/split/metric/condition
and preparation lineage before binding it. A cwd match or narrative contract
match alone is not sufficient. These compact settings do not grant shell,
installer, or arbitrary model-execution authority; in LLM mode, design proposals
must remain within an inspected entrypoint boundary and explicit configuration wins.
Use `--max-research-iterations N` to authorize at most `N` evidence-driven
supplement or candidate-revision rounds after the first analysis. A supplement
requires an explicit new seed and a reconstructable baseline; for a prepared
CodeTask, the supplement prepares an isolated original baseline workspace and
measures the current candidate workspace under the same condition. A revision
explicitly chooses whether to continue from the current candidate workspace or
start from the recorded original baseline; changed candidate workspaces are
never silently treated as old baselines.

The commands below are segmented or compatibility surfaces, not a second V2.8
full workflow. Use them for debugging, persisted handoff continuation, legacy
configuration, or historical benchmark checks.


### 2. Existing-Code Code Task

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
- [Workflows And Artifacts](docs/WORKFLOWS.md): the formal session workflow,
  legacy eight-stage projection, and artifact layouts.
- [Development Guide](docs/DEVELOPMENT.md): how to extend capabilities, templates, and
  code-task modules.
- [Changelog](CHANGELOG.md): chronological development progress.

The current V2.8 closure plan and version boundaries live in local `MDfiles/`
planning notes. That directory remains ignored by project policy and is not the
public GitHub roadmap; contributors should follow the formal-entrypoint boundary
in the development, workflow, and CLI documents above.

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
