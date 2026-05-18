# Code Task Workspace Notes

This note captures the V2.1 workspace flow before the V2.2 workspace-mode
refactor. It is intentionally descriptive: it should help contributors see
which assumptions must stay compatible while `copy`, `git_worktree`, and future
workspace strategies are introduced.

## Current Flow

```text
user config / CLI
-> load_code_task_init_options
-> initialize_code_task
-> copy_code_workspace
-> build_codebase_index
-> probe_code_task_environment
-> run_code_task_baseline
-> generate_patch_plan
-> record_plan_decision
-> propose_patch_edits
-> apply_patch_edits
-> validate_code_task
-> run_code_task_benchmark
-> compare_code_task_runs
-> write_code_task_summary
```

The key path is always:

```text
code_root
-> code_task/workspace
-> code_task/meta/codebase_index.json
-> code_task/run/<baseline|patched>/
```

The original `code_root` is never mutated after initialization. All patching,
validation, and benchmark execution operate inside `code_task/workspace`.

## Current Responsibilities

| Module | Current responsibility | Workspace assumption |
| --- | --- | --- |
| `code_task/config.py` | Merge TOML and CLI options for init. | No workspace yet; only resolves `code_root`, `task_file`, benchmark, metric, and environment options. |
| `code_task/workflow.py` | Creates run layout, writes `task.md`, copies source, builds the first codebase index, writes `manifest.json`. | Calls `copy_code_workspace` directly and assumes `code_task/workspace` is a copied directory. |
| `code_task/workspace.py` | Performs conservative recursive copy with ignored dirs/files, symlink rejection, file-size guard, and path safety. | Only supports copy mode. |
| `code_task/state.py` | Centralizes run paths and manifest helpers. | Hard-codes `code_task/workspace` as the editable root. |
| `code_task/index.py` | Builds inventory and AST summaries from the workspace. | Reads local files under `workspace_dir`. |
| `code_task/environment.py` | Observes platform, tools, dependency files, test dirs, GPU, and interpreter policy. | Probes `workspace_dir` only; does not install dependencies. |
| `code_task/runner.py` | Runs benchmark commands with timeout and restricted env. | Uses `cwd=workspace_dir`; injects `workspace` and `workspace/src` into `PYTHONPATH`. |
| `code_task/planning.py` | Selects context files and asks for a patch plan. | Reads files from `workspace_dir` and references workspace-relative paths. |
| `code_task/patching.py` | Proposes and applies controlled old/new edits. | Mutates only files resolved under `workspace_dir`. |
| `code_task/validation.py` | Runs static validation before benchmark. | Iterates files under `workspace_dir`. |
| `experiment/code_task_experiment.py` | Bridges the 8-stage pipeline into code-task. | Creates a nested code-task run under `06-code/code_task_run`. |

## Existing Safety Boundaries

- `copy_code_workspace` skips hidden/cache/build directories, symlinks, `.env`,
  bytecode, and oversized files.
- `state.workspace_file` and patching helpers reject absolute paths and `..`.
- Edit scope treats tests and benchmark files as read-only evidence by default.
- Benchmark commands are split without a shell and reject common shell control
  operators.
- Benchmark execution uses a restricted environment map and records stdout,
  stderr, parsed metrics, timeout, and interpreter policy.
- Environment probing is observational; it does not import project modules,
  run tests, install dependencies, or mutate the workspace.

## Hidden V2.1 Assumptions

These are the assumptions V2.2 must either preserve through compatibility
adapters or replace deliberately:

- `code_task/workspace` is always a physical copied directory.
- Workspace creation and copy report are stored under a top-level `copy`
  manifest section, not a general `workspace` section.
- The editable root and benchmark `cwd` are always the same directory.
- There is one active patch path, so `patch_plan.md`, `proposed_edits.json`,
  `patch.diff`, validation, and patched benchmark artifacts behave like
  "latest" outputs.
- The initial codebase index is built immediately after copy and is rebuilt
  after applying edits.
- `current` and `external` interpreter modes choose Python, but neither mode
  creates or installs project dependencies.
- The 8-stage embedded template auto-approves the patch plan inside the copied
  nested workspace so the pipeline can finish end to end.

## V2.2 Replacement Points

The first refactor should introduce a workspace-mode layer without changing the
public V2.1 behavior.

Recommended minimal contracts:

```python
@dataclass(frozen=True)
class WorkspaceSpec:
    code_root: Path
    run_dir: Path
    task_dir: Path
    mode: str = "copy"
    max_file_bytes: int = 2_000_000
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    reuse_source_venv: bool = False
    setup_hook: str = ""


@dataclass(frozen=True)
class WorkspaceResult:
    mode: str
    source_root: Path
    workspace_dir: Path
    writable_root: Path
    read_only_roots: tuple[Path, ...] = ()
    created_at: str = ""
    copy_report: dict[str, object] | None = None
    git: dict[str, object] | None = None
    environment_mapping: dict[str, object] | None = None
    cleanup_hint: str = ""
```

For V2.2, `copy` should be the compatibility implementation that wraps the
current `copy_code_workspace`. `git_worktree` can then return the same
`workspace_dir` / `writable_root` shape while recording git provenance and
environment mapping. `sparse_copy` should remain experimental until dependency
breakage and include/exclude behavior are well understood.

## Migration Order

1. Add `workspace_modes.py` with `WorkspaceSpec`, `WorkspaceResult`, and a
   `create_workspace(spec)` dispatcher.
2. Implement `copy` by delegating to `copy_code_workspace`.
3. Update `initialize_code_task` to use the dispatcher while still writing the
   old `copy` manifest section for compatibility.
4. Add a new manifest `workspace` section:

```json
{
  "workspace": {
    "mode": "copy",
    "source_root": "...",
    "workspace_dir": "code_task/workspace",
    "writable_root": "code_task/workspace",
    "read_only_roots": [],
    "created_at": "...",
    "copy_report": {}
  }
}
```

5. Keep `code_task_paths(...).workspace_dir` stable until all downstream modules
   are attempt-aware and workspace-mode-aware.
6. Add `git_worktree` after copy mode is fully covered by tests.

## Day 1 Conclusions

- The current implementation is clear and safe for small projects, but the copy
  behavior is embedded directly in initialization.
- The lowest-risk V2.2 first step is to add a workspace-mode abstraction that
  preserves `code_task/workspace` as the editable root.
- `git_worktree` must ship with environment mapping from the start; otherwise
  it solves copy cost but creates confusing dependency failures.
- `sparse_copy` should be treated as experimental and validated with explicit
  include/exclude tests before being recommended for real projects.
