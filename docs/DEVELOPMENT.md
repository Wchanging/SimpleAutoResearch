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

## Capability Boundary For New Modules

New replaceable modules may use the small capability boundary in
`src/simple_ar/core/` without changing the existing pipeline. `ArtifactRef`
identifies a declared artifact, `ArtifactStore` provides run-relative and
attempt-local file access, `CapabilityContext` passes registered inputs and a
profile, and `CapabilityResult` returns status, output references, diagnostics,
and provenance. `CapabilityRegistry` uses explicit registrations; it does not
scan the repository or dynamically import arbitrary providers.

`SessionController` adds bounded attempts and decision persistence for new
capabilities. It does not replace `PipelineRunner`, decide an unrestricted
research graph, or add implicit retries. Existing `simple-ar run`, code-task
commands, and their legacy projections remain the compatibility path until a
real capability has an input/output contract and regression evidence.

The smallest end-to-end reference is
`examples/capability_package_minimal/`. Run `uv run simple-ar-checks core` to
verify the boundary offline. New capability work should begin from this
contract and keep domain-specific request/result schemas outside the core.

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

### Reusing Document Ingest

`research.documents.ingest.build_document_bundle()` is the narrow composition
boundary for document metadata, permitted full-text handling, sections, and
chunks. It reuses the existing research records without calling an LLM or
writing stage artifacts. Search keeps ownership of index persistence and
legacy JSON/JSONL projections. Downstream code can use
`research.service.load_search_document_bundle(ctx)` to hydrate that same typed
bundle from state aliases or legacy Search paths, so a reader does not need to
know which provider or directory layout produced it.

### Reusing The Read Boundary

`research.evidence.reader.ReadRequest` accepts a `DocumentBundle` and optional
document or paper identifiers. `read_documents()` returns typed evidence cards
and diagnostics without calling an LLM or writing files. The existing
`write_read_card_artifacts()` function remains a compatibility projection over
that boundary, so stage artifact paths and legacy callers stay unchanged.

## Adding A Pipeline Stage

To add a stage to the default research pipeline, update these places together:

1. Add the enum value in `src/simple_ar/core/stages.py`.
2. Add or extend the typed state/contract models in `src/simple_ar/app/state.py`
   and `src/simple_ar/core/contracts.py`.
3. Implement stage behavior in the responsible domain service, for example
   `src/simple_ar/research/service.py` or `src/simple_ar/experiment/service.py`.
4. Add the stage controller to the appropriate module under
   `src/simple_ar/pipeline_stages/`. Keep it as orchestration over domain
   services, not a new all-purpose implementation dump.
5. Register the handler in `HANDLERS`.
6. Add a focused test that checks state updates and declared outputs.

A new stage should prefer explicit `ctx.state.<stage>` pointers and compact
stage contracts over reverse-scanning run folders. `ctx.find_artifact(...)`
exists for legacy fallback only.

## Adding An Experiment Template

Fixed script templates primarily live in `src/simple_ar/experiment/templates.py`.
Embedded 8-stage code-task templates live under
`src/simple_ar/experiment/code_task_bridge/` because they prepare an existing
workspace before writing the run harness. The older
`src/simple_ar/experiment/code_task_experiment.py` module is a compatibility
facade only; new code should import from `code_task_bridge`.

Use `src/simple_ar/experiment/runner.py` for fixed generated-template
subprocesses. Use `src/simple_ar/code_task/` for LLM-guided project editing,
workspace isolation, patching, validation, and benchmark comparison.

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
