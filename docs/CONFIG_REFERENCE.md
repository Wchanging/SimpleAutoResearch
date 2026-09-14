# Configuration Reference

[中文版本](CONFIG_REFERENCE_zh.md)

## Research configuration

Run `simple-ar research-session --config examples/research_config/minimal.toml`.
The [minimal](../examples/research_config/minimal.toml) and
[advanced](../examples/research_config/advanced.toml) examples share one format and defaults.
Precedence is built-in defaults, TOML, then explicit CLI options. CLI lists replace file lists.
File-relative paths resolve from the TOML directory; command argv remains literal.

Sections: `task` (goal, outputs, output_root), `model` (name, max_output_tokens),
`budget` (total_tokens, llm_requests, process_invocations, process_wall_seconds),
`research` (providers, queries, max_results, max_chunks, idea_limit, cache_dir,
use_fulltext, allow_pdf_download, keep_raw_pdf),
`assets` (papers), `execution` (command, cwd, timeout_sec, code_task_config,
primary_metric, metrics, metric_directions, pairs, protocol), and `report` (template, reviewer,
max_review_iterations, max_section_tokens). A report section token value of `0` omits the
per-call provider output cap; use a positive value only as an explicit expert limit.
Use model name `env` for SIMPLE_AR_MODEL; credentials remain in the environment.
This is the file-based default; explicit `name = ""` selects deterministic processing without LLM calls.
Outputs may be summary, report, and/or experiments. Explicit outputs cannot be combined
with --with-report/--no-report. Missing execution settings preserve the experiment goal
and pause at that boundary; automatic repository preparation is not yet implemented.
Budget limits initialize new sessions; resuming uses the persisted ledger, not a refreshed allowance.
Unknown fields are rejected; stage-specific research models are not supported yet.
The three full-text options are explicit booleans. The advanced template enables
them; retrieval failures must still be reported as abstract-only or unavailable.

Advanced experiments may use `[[execution.pairs]]` rows with a unique integer
`seed`, `baseline_command` and `candidate_command` (literal argv arrays). Do not
combine pairs with a single execution command. With `code_task_config`, pairs
replace its benchmark commands for the research matrix; baseline policy must be
`auto` or `run`. CodeTask still owns edit scope and implementation settings.
`[execution.protocol]` uses the existing research experiment contract: dataset_refs,
split_spec, metric_specs, comparison_conditions and protected_assets, optionally
contract_id/hypothesis. Protected file paths are relative to the experiment cwd,
not the TOML directory; shared data may use absolute paths. No seed interpolation
or extra scheduler is introduced. Grant process budgets for every matrix run.

The sections below cover the existing CodeTask TOML for `code-task` and
`research-session --code-task-config`. Research TOML can reference this file through
`execution.code_task_config`; it does not duplicate CodeTask's implementation settings.

The old eight-stage outer-pipeline parser and its alias mapping are retired.
Historical configuration snapshots remain readable files, not executable workflows.

## Loading Rules

- `code-task init --config PATH` loads initialization settings.
- `code-task execute --config PATH` loads execution/model/budget settings.
- `research-session --code-task-config PATH` uses the same CodeTask parser.
- Explicit CLI flags override corresponding TOML values.
- Setup and command details: [Usage](USAGE.md), [CLI Reference](CLI_REFERENCE.md).

## CodeTask Field Reference

### Continue a research session after preparing execution

Run `simple-ar research-session --config research.toml --session-root runs/research-session/<session>`.
Keep the original goal/outputs and add the missing execution command or CodeTask configuration.
Existing evidence is reused, implementation task-file instructions are retained, and completed
sessions do not train again. Existing execution settings cannot be silently replaced.
Saved research/report settings and budget limits remain authoritative; editing TOML does not
replenish the ledger. Declare intended process limits when creating a session that will later run experiments.
Automatic repository discovery/download/setup is not implemented.

Research preparation honors CodeTask auto/copy/git_worktree and protected paths. Auto preserves
uncommitted source by falling back to copy with a recorded reason; explicit git_worktree uses
committed HEAD. Preparation records workspace/Git provenance, not an automatic commit per candidate.
Sparse/empty workspace options remain limited to standalone CodeTask.

### Code-Task Fields

| Field | Meaning |
| --- | --- |
| `[code_task].kind` | `existing_project` patches an existing source project; `greenfield` starts from an empty workspace and generates a project under `code_task/workspace/generated_project`. |
| `[code_task].code_root` | Source project path for `existing_project`; optional scaffold/source root for `greenfield`. The original project is not edited. |
| `[code_task].task_file` | Task description for standalone CodeTask; research-session prepares its implementation handoff through the canonical application. |
| `[benchmark].command` | Command executed inside `code_task/workspace` before and after edits. It should print parseable metrics such as `accuracy: 0.82`. |
| `[benchmark].primary_metric` | Main metric used for the objective verdict. Unknown metrics are still recorded, but need directions to decide improvement. |
| `[benchmark.metric_directions]` | Direction map for metrics: `higher`, `lower`, `resource`, or `ignore`. |
| `[environment].mode` | `current` uses the active SimpleAutoResearch Python; `external` uses `[environment].python`. No dependencies are installed automatically. |
| `[workspace].mode` | Workspace strategy: `auto`, `copy`, `git_worktree`, `sparse_copy`, or `empty` for greenfield code-task runs. Existing projects default to `auto`, which tries git worktree first and falls back to guarded copy when needed. |
| `[workspace].reuse_source_venv` | If a source `.venv` or `venv` is detected, record and use that Python as the execution interpreter. |
| `[implementation].provider` | Code-task implementation backend. `local` keeps the in-process SimpleAutoResearch path; `fake` is deterministic for tests; `local_llm` uses the configured LLM; `codex`, `claude_code`, `opencode`, and `external_cli` use the external-agent handoff boundary when explicitly enabled. |
| `[implementation].agent_mode` | Implementation mode for the backend: `model` keeps SimpleAutoResearch as the harness, `handoff` ingests candidate files from an external agent package, and `delegated_workspace` is recognized but currently fails explicitly until snapshot/diff/rollback execution is implemented. |
| `[implementation].allow_external_agent` | Required before external CLI backends may launch. External outputs remain untrusted until SimpleAutoResearch review, validation, benchmark, or result guards accept them. |
| `[implementation].agent_model` / `.agent_binary` / `.agent_args` / `.agent_timeout_sec` | Optional external backend launch controls. Leave `agent_model` empty unless you know the external CLI/account supports the model name. |
| `[implementation].max_repair_attempts` | Upper bound for implementation-side repair attempts, including greenfield review repair before validation. |
| `[resource].max_runtime_sec` | Runtime budget surfaced to greenfield planning and external-agent handoff context. |
| `[resource].max_files` / `.max_generated_lines` | Code-task generation budgets. `[execute].max_files` and `[execute].max_generated_lines` override these during `code-task execute`; otherwise execute reads these values. |
| `[resource].max_memory_mb` / `.allow_gpu` | Memory/GPU constraints exposed to planning. They describe the allowed resource profile; dependency installation is still not automatic. |
| `[edit_scope].allowed_patterns` | Optional workspace-relative glob allowlist for files automated edits may touch. Empty means all normalized non-protected workspace paths are editable. |
| `[edit_scope].protected_patterns` | Additional workspace-relative glob patterns treated as read-only evidence. Defaults for tests, benchmarks, `.env`, secrets, and credentials are always retained. |
| `[edit_scope].mode` | Optional label stored in `manifest.json`; it does not change behavior by itself. |
| `[safety].max_file_bytes` | Max copied file size for copy/sparse modes. This avoids accidentally copying huge model/data artifacts. |
| `[safety].validation_max_file_bytes` | Max file size scanned by static validation. |

### Execute And Budget Fields

| Field | Meaning |
| --- | --- |
| `[execute].to_step` | Last step the state-aware executor may attempt. Use `propose-edits` to stop before applying patches, or `review` to stop after post-apply structured review. |
| `[execute].use_llm` | Enables or disables LLM-backed work-plan, patch-plan, edit-proposal, and repair steps. |
| `[execute].timeout_sec` | Benchmark timeout used by executor-managed baseline and patched runs. If omitted, execute falls back to `[benchmark].timeout`, then `[resource].max_runtime_sec`. |
| `[execute].stream_benchmark_output` | Live benchmark log mode: `off`, `line`, `auto`, or `summary`. Use `auto` for tqdm-like progress output. |
| `[execute].baseline_policy` | Existing-project baseline behavior: `auto`/`run` executes the unchanged benchmark; `skip`/`none` continues without baseline comparison; `provided` records metrics from `[execute].baseline_metrics_file`. |
| `[execute].baseline_metrics_file` | JSON or metric-line file used with `baseline_policy = "provided"`. Accepted JSON shapes include `{"accuracy": 0.8}`, `{"metrics": {...}}`, and `{"metric_values": {...}}`. |
| `[execute].apply_proposed_edits` | Lets execute apply an already reviewed proposal. Keep false for review-first workflows. |
| `[execute].allow_large_edits` | Allows application of reviewed proposals that exceed the normal budget but fit the large budget. |
| `[execute].allow_planning_fallback` | Allows deterministic offline work/patch plans and greenfield architecture/file fallbacks after all LLM retries fail. Keep false for real LLM runs so malformed model output stops safely and can be retried. |
| `[execute].planning_mode` | Greenfield planning: `tool_agent` uses requirements, architecture, interfaces, file plan, and bounded review (at least five calls); `compact` uses one architecture call before retries. Both share file generation and execution; call counts do not establish research quality. |
| `[execute].planning_review_rounds` | Maximum reviewer-directed greenfield planning revision rounds for standalone code-task runs. Default `2`; lower to `1` for lightweight smoke examples, raise only when plan convergence matters more than token/time cost. |
| `[execute].llm_retry_attempts` | Number of stage-level LLM work-plan, patch-plan, greenfield architecture, and greenfield file-generation attempts before stopping or explicitly falling back. Each stage attempt still uses the provider-level retry/backoff configured by `SIMPLE_AR_LLM_RETRY_ATTEMPTS`. |
| `[execute].repair_rounds` | Number of bounded repair proposals after validation/benchmark failure. Repairs still require review. |
| `[execute].max_files` | Max files included in LLM context for plan/proposal/repair steps. |
| `[execute].max_source_chars_per_file` | Per-file source snippet budget for LLM context. |
| `[execute].max_generated_lines` | Greenfield generation line budget. If omitted, execute falls back to `[resource].max_generated_lines`, then to a conservative default. |
| `[models.code_task].planner` | Model used for work-plan, patch-plan, and greenfield architecture planning. |
| `[models.code_task].writer` | Model used for greenfield file generation. Existing-project edit proposals use `editor`. |
| `[models.code_task].reviewer` | Model used for code-task patch review and greenfield generated-project review. |
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

## Workspace Mode Variants

### `auto`

`auto` is the recommended existing-project default. It prefers a detached
`git_worktree` when `code_root` is inside a local Git repository with at least
one commit. If worktree preparation is not possible, it falls back to `copy`
and records the reason plus next steps in the manifest and CLI output.

```toml
[workspace]
mode = "auto"
reuse_source_venv = false
```

When `code_root` points to a subdirectory inside a larger repository,
SimpleAutoResearch creates the worktree at the repository root and uses the
matching subdirectory as the project root for indexing, editing, and running.

### `copy`

`copy` creates a guarded physical copy under `code_task/workspace`. Use it for
non-Git projects, dirty filesystem experiments that are not committed yet, or
cases where you deliberately want to include the current working tree state.

```toml
[workspace]
mode = "copy"
reuse_source_venv = false

[safety]
max_file_bytes = 2000000
```

### `git_worktree`

`git_worktree` creates a detached worktree and fails instead of falling back
when Git isolation is not possible. `code_root` may be the repository root or a
project subdirectory inside the repository. The source project must be a local
Git repository with at least one commit. A remote GitHub repository is not
required.

```toml
[workspace]
mode = "git_worktree"
reuse_source_venv = true
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
kind = "existing_project"     # existing_project | greenfield
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

[safety]
max_file_bytes = 2000000

[execute]
to_step = "run"
use_llm = true
timeout_sec = 120
repair_rounds = 1
stream_benchmark_output = "auto"
baseline_policy = "auto"
apply_proposed_edits = false
allow_large_edits = false
allow_planning_fallback = false
planning_mode = "tool_agent"
planning_review_rounds = 2
llm_retry_attempts = 3
max_files = 8
max_source_chars_per_file = 4000
max_generated_lines = 1600

[implementation]
provider = "local"            # local | fake | local_llm | codex | claude_code | opencode | external_cli
agent_mode = "model"          # model | handoff | delegated_workspace
allow_external_agent = false
agent_model = ""              # empty means use the external CLI/account default
agent_binary = ""
agent_args = []
agent_timeout_sec = 600

[resource]
max_runtime_sec = 120
max_files = 8
max_generated_lines = 1600
max_memory_mb = 4096
allow_gpu = false

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

For a standalone greenfield code task, omit `code_root` unless you deliberately
want to start from a scaffold/template directory. The workspace defaults to
`empty`, and `execute` generates the project under
`code_task/workspace/generated_project`:

```toml
[code_task]
kind = "greenfield"
task_file = "tasks/build_new_project.md"
name = "greenfield-project"

[benchmark]
command = "python generated_project/main.py"
primary_metric = "accuracy"

[workspace]
mode = "empty"

[execute]
to_step = "run"
max_files = 16
max_generated_lines = 4000
baseline_policy = "none"

[implementation]
provider = "local"
agent_mode = "model"
allow_external_agent = false

[resource]
max_runtime_sec = 600
max_files = 16
max_generated_lines = 4000
allow_gpu = false
```

To test a Codex/Claude/OpenCode handoff for the same greenfield task, keep the
same `[code_task]`, `[benchmark]`, and `[resource]` sections, then change only
the implementation backend:

```toml
[implementation]
provider = "codex"
agent_mode = "handoff"
allow_external_agent = true
agent_model = ""          # use the model configured by the external CLI/account
agent_timeout_sec = 1800
```

The external agent still writes untrusted candidate files first. SimpleAutoResearch
ingests them, copies them into `code_task/workspace/generated_project`, and then
runs the normal review, validation, benchmark, guard, memory, and repair path.

## CodeTask In A Research Session

Use `research-session --code-task-config` with the existing-project CodeTask TOML.
CodeTask owns editing and validation; the application owns measurement and reporting.

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
baseline_policy = "auto"
apply_proposed_edits = false
allow_large_edits = false
allow_planning_fallback = false
planning_review_rounds = 2
llm_retry_attempts = 3

[models.code_task]
planner = "gpt-4o-mini"
writer = "gpt-4o-mini"
reviewer = "gpt-4o-mini"
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

`baseline_policy` values for existing-project tasks:

| Value | Meaning |
| --- | --- |
| `auto` | Default behavior. Run the unchanged benchmark when a baseline is needed for comparison. |
| `run` | Force an unchanged baseline run. |
| `skip` | Skip the unchanged baseline but still allow code understanding, review, validation, and final benchmark execution. |
| `provided` | Record user-supplied metrics from `baseline_metrics_file`; summaries mark them as provided, not reproduced. |
| `none` | Mark the task as having no meaningful baseline comparison. Useful for pure generation or acceptance-style tasks. |

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
