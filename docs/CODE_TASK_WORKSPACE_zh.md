# Code Task Workspace 笔记

[English version](CODE_TASK_WORKSPACE.md)

本文记录 V2.1 workspace 流程和 V2.2 第一轮 workspace-mode 重构。它是描述性文档，目的是帮助贡献者理解 `copy`、`git_worktree`、`sparse_copy` 以及未来 workspace strategy 演进时必须保持兼容的假设。

## 当前流程

```text
user config / CLI
-> load_code_task_init_options
-> initialize_code_task
-> create_workspace(mode=copy|git_worktree|sparse_copy)
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

关键路径始终是：

```text
code_root
-> code_task/workspace
-> code_task/meta/codebase_index.json
-> code_task/run/<baseline|patched>/
```

初始化后，原始 `code_root` 不会被修改。所有 patching、validation 和 benchmark execution 都在 `code_task/workspace` 内发生。`copy` 模式下这是受保护物理复制；`git_worktree` 模式下这是从 source repository root 创建的 detached git worktree；`sparse_copy` 模式下这是受 include/exclude pattern 控制的实验性子集复制。

## 当前职责

| 模块 | 当前职责 | Workspace 假设 |
| --- | --- | --- |
| `code_task/config.py` | 合并 TOML 和 CLI init options。 | 解析 `code_root`、`task_file`、benchmark、metric、environment 和 workspace options。 |
| `code_task/workflow.py` | 创建 run layout、写 `task.md`、创建 workspace、构建初始 codebase index、写 `manifest.json`。 | 保持 `code_task/workspace` 稳定，同时委托 workspace creation。 |
| `code_task/workspace.py` | 保守递归 copy，包含 ignored dirs/files、symlink rejection、file-size guard 和 path safety。 | copy/sparse-copy 的底层实现。 |
| `code_task/workspace_modes.py` | 调度 workspace creation strategies。 | 拥有 `copy` compatibility mode、repo-root `git_worktree` 和实验性 `sparse_copy`。 |
| `code_task/state.py` | 集中管理 run paths、manifest helpers 和 safe workspace path helpers。 | 将 `code_task/workspace` 作为 editable root。 |
| `code_task/index.py` | 从 workspace 构建 inventory 和 AST summaries。 | 只读取 `workspace_dir` 下的本地文件。 |
| `code_task/environment.py` | 观察 platform、tools、dependency files、test dirs、GPU 和 interpreter policy。 | 只 probe `workspace_dir`，不安装依赖。 |
| `code_task/runner.py` | 带 timeout 和 restricted env 运行 benchmark commands。 | 使用 `cwd=workspace_dir`，并把 `workspace` 和 `workspace/src` 注入 `PYTHONPATH`。 |
| `code_task/planning.py` | 选择上下文文件并请求 patch plan。 | 从 `workspace_dir` 读取文件，使用 workspace-relative paths。 |
| `code_task/patching.py` | 生成和应用受控 old/new edits。 | 只修改解析到 `workspace_dir` 内的文件。 |
| `code_task/validation.py` | benchmark 前运行静态验证。 | 遍历 `workspace_dir` 下文件。 |
| `experiment/code_task_experiment.py` | 把 8 阶段 pipeline 接到 code-task。 | 在 `06-code/code_task_run` 下创建嵌套 code-task run。 |

## 已有安全边界

- `copy_code_workspace` 会跳过 hidden/cache/build dirs、symlinks、`.env`、bytecode 和超大文件。
- `sparse_copy` 会先应用内置危险路径排除，再应用用户 include/exclude。
- `build_codebase_index` 忽略 `.git`、`.env`、virtualenv 和 cache metadata，避免 worktree mode 泄露 git metadata 或 secret-like 文件到模型上下文。
- `state.workspace_file` 和 patching helpers 拒绝 absolute paths 和 `..`。
- Edit scope 默认把 tests、benchmark 文件、`.env` 和 secret/credential-like 路径作为只读证据。
- Benchmark commands 不通过 shell 执行，并拒绝常见 shell control operators。
- Benchmark execution 使用受限 environment map，并记录 stdout、stderr、parsed metrics、timeout 和 interpreter policy。
- Environment probing 是观察性的：不 import project modules、不运行 tests、不安装依赖、不修改 workspace。

## 隐含的 V2.1 假设

V2.2 必须通过兼容 adapter 保留这些假设，或有意识地替换它们：

- 旧 V2.1 文档假设 `code_task/workspace` 总是物理 copy。V2.2 保留路径，但允许它是 git worktree 或 sparse copy。
- Workspace creation 和 copy report 曾经存放在顶层 `copy` manifest section，而不是通用 `workspace` section。
- Editable root 和 benchmark `cwd` 总是同一个目录。
- 当前只有一条 active patch path，因此 `patch_plan.md`、`proposed_edits.json`、`patch.diff`、validation 和 patched benchmark artifacts 都像 “latest outputs”。
- 初始 codebase index 在 workspace creation 后立即构建，并在 apply edits 后重建。
- `current` 和 `external` interpreter mode 只选择 Python，不创建或安装项目依赖。
- 8 阶段内嵌 template 会在 nested isolated workspace 中自动批准 patch plan，使 pipeline 能端到端完成。

## V2.2 Workspace Modes

V2.2 添加 `simple_ar.code_task.workspace_modes`，当前支持三种模式：

| 模式 | 行为 | 适合场景 | 当前限制 |
| --- | --- | --- | --- |
| `copy` | 使用已有安全跳过规则递归复制源码。 | 小项目、教学示例和最稳妥默认 run。 | 大仓库会慢，并占用重复存储。 |
| `git_worktree` | 从当前 source commit 在 `code_task/workspace` 创建 detached git worktree。 | 中大型 git 仓库，避免每次复制所有文件。 | 当前要求 `code_root` 是仓库根目录；subdirectory worktree 留到 cwd 和 dependency mapping 更丰富后处理。 |
| `sparse_copy` | 应用内置和用户 exclude 后，只复制选中的 include patterns。 | 用户明确知道所需文件的小型子集或实验。 | 实验性；可能遗漏运行依赖，不推荐作为通用项目默认值。 |

`manifest.json` 现在保留旧顶层 `copy` section 以兼容，同时新增顶层 `workspace` section：

```json
{
  "workspace": {
    "schema_version": 1,
    "mode": "git_worktree",
    "source_root": "...",
    "workspace_dir": "code_task/workspace",
    "writable_root": "code_task/workspace",
    "read_only_roots": ["..."],
    "copy_report": {},
    "git": {
      "origin_branch": "main",
      "origin_commit": "...",
      "mode": "detached"
    },
    "environment_mapping": {
      "dependency_files": ["pyproject.toml"],
      "reuse_source_venv": false,
      "setup_hook_executed": false
    },
    "patterns": {
      "include": ["src/**", "tests/**", "benchmark.py"],
      "exclude": ["data/**", "models/**"],
      "risk": "sparse_copy may omit runtime dependencies."
    }
  }
}
```

Environment mapping 故意保守。Init 只记录 dependency files 和可选 source `.venv` 复用，不安装依赖，也不执行 setup hooks。如果 `workspace.reuse_source_venv = true` 且检测到 source virtualenv Python，初始 execution policy 会把它作为 `external` interpreter。

## V2.2 替换点

第一轮重构应引入 workspace-mode 层，但不改变 V2.1 公开行为。

建议的最小 contracts：

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

V2.2 中，`copy` 是兼容实现，包装当前 `copy_code_workspace`。`git_worktree` 返回同样的 `workspace_dir` / `writable_root` 形状，同时记录 git provenance 和 environment mapping。`sparse_copy` 是实验性 allowlist copy，并在 manifest 中记录 include/exclude patterns 和 dependency risk note。

## 迁移顺序

1. 添加 `workspace_modes.py`，包含 `WorkspaceSpec`、`WorkspaceResult` 和 `create_workspace(spec)` dispatcher。已完成。
2. 通过委托 `copy_code_workspace` 实现 `copy`。已完成。
3. 更新 `initialize_code_task` 使用 dispatcher，同时继续写旧 `copy` manifest section 以兼容。已完成。
4. 添加新的 manifest `workspace` section。已完成：

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

5. 在所有下游模块 attempt-aware 和 workspace-mode-aware 前，保持 `code_task_paths(...).workspace_dir` 稳定。仍然有效。
6. copy mode 测试充分后添加 `git_worktree`。已实现最小 repo-root 支持；subdirectory worktree 仍暂缓。
7. worktree diagnostics 稳定后添加实验性 `sparse_copy`。已完成 include/exclude patterns、内置风险路径排除和 manifest recording；文档中仍标注为非默认实验模式。

## Day 1 结论

- 当前实现对小项目清晰且安全，但 copy 行为曾经直接嵌入 initialization。
- V2.2 最低风险第一步是增加 workspace-mode abstraction，同时保留 `code_task/workspace` 作为 editable root。
- `git_worktree` 必须从一开始就带 environment mapping，否则虽然解决复制成本，却会制造令人困惑的依赖失败。
- `sparse_copy` 应被视为实验性能力，并用显式 include/exclude 测试验证，再推荐给真实项目。
