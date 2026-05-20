# 开发指南

[English version](DEVELOPMENT.md)

本文面向想扩展 SimpleAutoResearch 的贡献者。命令细节见 [CLI 参考](CLI_REFERENCE_zh.md)，安装 walkthrough 见 [使用与配置](USAGE_zh.md)，工作流概念和产物见 [工作流与产物](WORKFLOWS_zh.md)，当前 code-task workspace 流程和 V2.2 替换点见 [Code Task Workspace 笔记](CODE_TASK_WORKSPACE_zh.md)。

## 项目形态

SimpleAutoResearch 故意保持 file-first：

- stage 读取和写入具体文件；
- workflow state 在 run 目录中可见；
- 测试验证 artifacts，而不是依赖隐藏内存状态；
- 高风险代码修改发生在隔离 editable workspace 中，通常是受保护 copy，也可以是 detached git worktree，或实验性 sparse copy。

这样项目更容易学习、调试和重构。

## 添加 Pipeline Stage

向默认 research pipeline 添加 stage 时，需要一起更新：

1. 在 `src/simple_ar/stages.py` 中添加 enum value。
2. 在 `src/simple_ar/contracts.py` 中添加 `StageContract`。
3. 在 `src/simple_ar/stage_handlers.py` 中实现 handler function。
4. 在 `HANDLERS` 中注册 handler。
5. 添加聚焦测试，检查 required inputs 和 declared outputs。

新的 stage 应使用 `ctx.find_artifact(...)` 读取已有产物，用 `ctx.artifact_path(...)` 写自己的输出。

## 添加 Experiment Template

固定脚本模板位于 `src/simple_ar/experiment/templates.py`。内嵌 8 阶段 code-task templates 位于 `src/simple_ar/experiment/code_task_experiment.py`，因为它们会在写 run harness 前准备已有 workspace。

顶层 run config 解析位于 `src/simple_ar/run_config.py`。它应该保持为薄的 TOML-to-runtime-options 层；code-task 专属 config 语义应继续放在 `src/simple_ar/code_task/config.py`，避免 standalone 和 embedded code-task 行为漂移。

新的 template 应满足：

- 添加到 `SUPPORTED_TEMPLATES`；
- 生成完整 standalone `experiment.py`；
- 只使用 `pyproject.toml` 中声明的依赖；
- 打印机器可解析指标行，例如 `metric_name: 0.123`，由 `src/simple_ar/metrics.py` 解析；
- 避免网络访问和不受控下载；
- 在 `tests/test_experiment_runner.py` 中有测试。

当前 template system 故意不做自由形式 code generation。这个边界能保证教学 pipeline 可复现，同时把更强 coding workflow 放在 `code-task` 下逐步发展。

对内嵌 code-task template，应保持 automatic approval boundary 显式：它们应准备 workspace，使用 controlled old/new edits，写紧凑阶段产物，例如 `code_task_experiment.json`，并通过 `07-run` 运行 benchmark，而不是在 reporting 时悄悄修改源码。通用 `code_task_project` template 应保持为 standalone code-task modules 的薄桥接层，而不是另一套 coding 实现。

## 扩展 Code Task

Code-task workflow 拆分成小模块：

- `workspace.py`：安全源码复制实现。
- `workspace_modes.py`：workspace strategy dispatcher，支持 `copy`、`git_worktree`、实验性 `sparse_copy` 和未来模式。修改 workspace layout 或 creation behavior 前请阅读 [Code Task Workspace 笔记](CODE_TASK_WORKSPACE_zh.md)。
- `config.py`：code-task init 的 TOML config 和 CLI override 解析。
- `environment.py`：环境观察和执行解释器策略。
- `index.py`：代码清单和 Python AST summaries。
- `repo_map.py`：从 index 派生分层 repo map。
- `locate.py`：基于 repo map 确定性排序 editable targets 和 read-only evidence。
- `context.py`：从 locate results 和 workspace snippets 构建受预算限制的 context pack。
- `planning.py`：patch planning 和 HITL decisions；存在 latest context pack 时优先使用它，否则回退到 index selector。
- `patching.py`：controlled old/new edit proposal 与应用；proposal 阶段只使用 context pack 中的 editable snippets。
- `validation.py`：语法和静态安全检查。
- `runner.py`：在 editable workspace 中执行 benchmark。
- `comparison.py`：baseline-vs-patched metric comparison。
- `failure.py`：确定性 failure analysis。
- `repair.py`：bounded repair proposal generation。
- `summary.py`：人类可读 code-task status summary。
- `state.py`：共享路径、manifest helpers 和 workspace path safety。

新增 code-task 功能时：

- 原始 source directory 必须保持只读；
- artifacts 写入 `code_task/meta`、`code_task/run` 或 `code_task/repairs`；
- 底层行为稳定前，CLI 步骤应保持显式；
- 适合时同时测试 library function 和 CLI path；
- 优先用小而可组合的函数，不要过早塞进单个 agent loop。

Metric comparison 应保持保守。未知数值指标可以记录 delta，但除非 manifest 显式配置方向或命中简单本地 heuristic，否则不应决定 improved/regressed verdict。只有当指标命名约定足够常见、不令人意外时，才添加新的默认 heuristic。

### Code-Task Environment Policy

当前 V2.1 code-task runner 有 workspace isolation、command timeout、stdout/stderr 捕获、受限 environment map 和显式 execution interpreter policy。它支持 `current` 和 `external`，但还不会创建或安装到单独 Python environment。除非未来功能明确改变这一点，否则不要默认把用户项目依赖安装到 SimpleAutoResearch 自己的 `.venv`。

环境支持应分层演进：

- `current`：使用当前 SimpleAutoResearch Python。简单，适合 demo，但不是依赖隔离。已支持。
- `external`：使用用户提供的 Python 或 Conda interpreter。是真实项目已有环境的第一逃生口。已支持。
- `project-venv`：在 `code_task/.venv/` 下创建 per-run 环境。隔离好，但可能浪费磁盘。计划中。
- `shared-env-cache`：在 `.simple_ar_cache/envs/<env-hash>/` 之类的缓存目录下创建或复用环境，按 OS、Python version 和 dependency files 哈希。长期推荐方向。计划中。
- `docker`：在容器内运行以获得更强隔离。应与 Python runner 分离，因为 Windows、GPU 和 image build 行为需要谨慎处理。计划中。

未来任何环境创建或依赖安装都必须显式、可审计，并记录到 artifacts。安全实现应记录 selected mode、interpreter path、dependency files、install commands、exit codes 和 warnings 到 `code_task/meta/environment_report.json` 或专门 environment artifact。

## 文档规则

文档分工：

- `README.md` / `README_zh.md`：项目入口、安装、quickstart、workflow 概览和链接。
- `docs/USAGE.md` / `docs/USAGE_zh.md`：安装、环境变量和 workflow walkthrough。
- `docs/CLI_REFERENCE.md` / `docs/CLI_REFERENCE_zh.md`：命令组、参数表和配置 schema。
- `docs/WORKFLOWS.md` / `docs/WORKFLOWS_zh.md`：每个 workflow/stage 做什么，以及产物结构。
- `docs/DEVELOPMENT.md` / `docs/DEVELOPMENT_zh.md`：贡献者指南。
- `docs/CODE_TASK_WORKSPACE.md` / `docs/CODE_TASK_WORKSPACE_zh.md`：workspace mode 和替换点说明。
- `CHANGELOG.md`：按时间记录开发进展。
- `MDfiles/`：私有或学习型规划笔记，通常不提交 GitHub。

英文文档应链接到对应中文版本；中文文档内部链接应优先指向中文版本。

## 测试

开发时优先使用分层 checks：

```bash
uv run simple-ar-checks --list
uv run simple-ar-checks quick
uv run simple-ar-checks code-task
uv run simple-ar-checks pipeline
uv run simple-ar-checks research
uv run simple-ar-checks code-task-examples
```

也可以不用 console script，直接运行脚本入口：

```bash
uv run python scripts/run_checks.py code-task
```

推荐验证层级：

| 修改范围 | 建议检查 |
| --- | --- |
| 仅文档 | `git diff --check` 加人工检查链接。 |
| 小型 parser、prompt、metric 或 CLI 改动 | `uv run simple-ar-checks quick`。 |
| Code-task 内部、workspace、repo-map、patching、validation、runner、repair | `uv run simple-ar-checks code-task`。 |
| 内置 code-task 示例或 benchmark 示例 | `uv run simple-ar-checks code-task-examples`。 |
| Pipeline、stages、experiment templates、run config | `uv run simple-ar-checks pipeline`。 |
| Literature、retrieval、evidence ledger、report generation、LLM adapter | `uv run simple-ar-checks research`。 |
| 提交/推送前或大范围重构 | `uv run simple-ar-checks all`。 |

必要时仍可直接运行完整测试：

```bash
uv run python -m unittest discover -s tests
```

运行真实 code-task 示例测试：

```bash
uv run python -m unittest tests.test_code_task_examples
```

运行 experiment runner tests：

```bash
uv run python -m unittest tests.test_experiment_runner
```

## Git 卫生

- 保持 feature commit 聚焦，避免混入无关重构。
- 不提交 `.env`、run outputs、caches 或私有学习笔记。
- README 保持简洁；详细行为放到 docs。
- 用户可见命令、artifact 或 workflow 行为变化时，更新 `CHANGELOG.md`。
