# 开发指南

[English version](DEVELOPMENT.md)

本文面向想扩展 SimpleAutoResearch 的贡献者。命令细节见 [CLI 参考](CLI_REFERENCE_zh.md)，TOML schema 见 [配置参考](CONFIG_REFERENCE_zh.md)，安装 walkthrough 见 [使用与配置](USAGE_zh.md)，工作流概念和产物见 [工作流与产物](WORKFLOWS_zh.md)。

## 项目形态

SimpleAutoResearch 现在采用 file-first + state-backed 的形态：

- stage 读取和写入具体文件；
- workflow state 通过 `state.json` 和阶段 contract 可见；
- 测试验证 contract/artifact，而不是依赖隐藏内存状态；
- 高风险代码修改发生在隔离 editable workspace 中，通常是受保护 copy，也可以是 detached git worktree，或实验性 sparse copy。

这样项目更容易学习、调试和重构。

## 新模块的能力边界

新的可替换模块可以使用 `src/simple_ar/core/` 中的轻量能力边界，暂时不必
改动现有 pipeline。`ArtifactRef` 标识已经声明的产物，`ArtifactStore` 提供
相对于 run 和 attempt 的文件读写，`CapabilityContext` 传递已登记的输入和
profile，`CapabilityResult` 返回状态、输出引用、诊断和 provenance。
`CapabilityRegistry` 只使用显式注册，不扫描仓库，也不动态导入任意 provider。

`SessionController` 为新 capability 提供有界 attempt 和 decision 持久化；它
不替代 `PipelineRunner`，不负责调度无限制的研究图，也不会隐式重试。现有的
`simple-ar run`、code-task 命令和旧 projection 仍是兼容入口，直到某条真实能力
线拥有明确的输入/输出契约和回归证据后，才逐步迁移。

最小的端到端参考实现位于 `examples/capability_package_minimal/`。可以运行
`uv run simple-ar-checks core` 做离线验收。新的能力开发应先遵循这组边界，
具体的 request/result schema 放在对应领域模块中，不要继续膨胀 core。

旧的巨型 CLI 和 stage handler 模块已经从 `src/simple_ar/_legacy/` 迁出。
该包现在只保留兼容旧 import path 的别名。新的行为应优先实现到
`core/`、`research/`、`experiment/`、`report/` 和 `code_task/`
这些领域模块中。

CLI 代码按职责拆分：

```text
src/simple_ar/cli/
  parser.py  argparse 命令与参数声明
  main.py    命令分发与用户可见输出
```

Pipeline stage 编排按工作流区域拆分：

```text
src/simple_ar/pipeline_stages/
  research.py    01-04 阶段：plan、search、read、synthesize
  experiment.py  05-07 阶段：design、code、run
  report.py      08 阶段：report packaging 与安全审查
  common.py      LLM access、artifact reads 等共享 stage helpers
  registry.py    PipelineRunner 使用的 HANDLERS registry
  handlers.py    仅兼容聚合；不要在这里继续添加新逻辑
```

顶层实现模块已经收束到领域包中。新代码应直接从 `core/*`、`app/*`、
`integrations/*`、`research/*`、`experiment/*`、`report/*` 或 `code_task/*`
导入，不再重新引入宽泛的 compatibility facade。

Research 代码按 evidence 生命周期分组：

```text
src/simple_ar/research/
  planning/    research questions 和可执行 query plans
  sources/     source plan contracts 与 connector-neutral query objects
  connectors/  OpenAlex、Semantic Scholar、arXiv、本地文件 adapters
  documents/   document records、full-text hints、parser/extractor helpers
  store/       chunks 与本地 index backends
  evidence/    retrieval screening、coverage、可选 debug evidence cards
  outputs/     search-stage artifact writers
```

新的检索、全文、证据和 card 能力应放入这些包中，不再回到旧的 `research/*.py` 平铺结构。
## 添加 Pipeline Stage

向默认 research pipeline 添加 stage 时，需要一起更新：

1. 在 `src/simple_ar/core/stages.py` 中添加 enum value。
2. 在 `src/simple_ar/app/state.py` 和 `src/simple_ar/core/contracts.py`
   中添加或扩展 typed state/contract models。
3. 在对应领域 service 中实现阶段行为，例如
   `src/simple_ar/research/service.py` 或 `src/simple_ar/experiment/service.py`。
4. 在 `src/simple_ar/pipeline_stages/` 的对应模块中添加 stage controller。
   这里应负责阶段编排和产物衔接，不要重新变成新的大杂烩实现层。
5. 在 `HANDLERS` 中注册 handler。
6. 添加聚焦测试，检查 state update 和 declared outputs。

新的 stage 应优先使用显式 `ctx.state.<stage>` 指针和紧凑 stage contract，
而不是反向扫描 run 目录。`ctx.find_artifact(...)` 仅作为 legacy fallback 保留。

## 添加 Experiment Template

固定脚本模板主要位于 `src/simple_ar/experiment/templates.py`。内嵌 8 阶段
code-task templates 位于 `src/simple_ar/experiment/code_task_bridge/`，
因为它们会在写 run harness 前准备已有 workspace。旧的
`src/simple_ar/experiment/code_task_experiment.py` 只作为兼容 facade 保留；
新代码应直接从 `code_task_bridge` 导入。

`src/simple_ar/experiment/runner.py` 用于固定模板生成脚本的 subprocess 运行。
`src/simple_ar/code_task/` 则负责 LLM-guided 项目编辑、workspace 隔离、patch、
validation 和 benchmark comparison。

顶层 run config 解析位于 `src/simple_ar/app/run_config.py`。它应该保持为薄的 TOML-to-runtime-options 层；code-task 专属 config 语义应继续放在 `src/simple_ar/code_task/runtime/config.py`，避免 standalone 和 embedded code-task 行为漂移。

新的 template 应满足：

- 添加到 `SUPPORTED_TEMPLATES`；
- 生成完整 standalone `experiment.py`；
- 只使用 `pyproject.toml` 中声明的依赖；
- 打印机器可解析指标行，例如 `metric_name: 0.123`，由 `src/simple_ar/experiment/metrics.py` 解析；
- 避免网络访问和不受控下载；
- 在 `tests/test_experiment_runner.py` 中有测试。

当前 template system 故意不做自由形式 code generation。这个边界能保证教学 pipeline 可复现，同时把更强 coding workflow 放在 `code-task` 下逐步发展。

对内嵌 code-task template，应保持 automatic approval boundary 显式：它们应准备 workspace，使用 controlled old/new edits，写紧凑阶段产物，例如 `code_task_experiment.json`，并通过 `07-run` 运行 benchmark，而不是在 reporting 时悄悄修改源码。通用 `code_task_project` template 应保持为 standalone code-task modules 的薄桥接层，而不是另一套 coding 实现。

## 扩展 Code Task

Code-task workflow 现在按生命周期分包，而不是把所有文件平铺在同一层：

```text
src/simple_ar/code_task/
  runtime/        config、path 和 manifest helpers
  workspace/      copy、git worktree、sparse-copy 准备
  analysis/       codebase index、repo map、locate、context packs
  editing/        work plan、patch plan、edit budgets、patch proposal/application
  execution/      environment probe、validation、benchmark、comparison、repair
  orchestration/  init 和 execute 这类组合下层模块的流程
```

新增 code-task 能力时，应放入拥有该行为的生命周期包中。除非是 public facade 或真正跨切面的边界，不要继续在 `code_task/` 根目录新增平铺文件。

新增 code-task 功能时：

- 原始 source directory 必须保持只读；
- artifacts 写入 `code_task/meta`、`code_task/run` 或 `code_task/repairs`；
- 底层行为稳定前，CLI 步骤应保持显式；
- 适合时同时测试 library function 和 CLI path；
- 优先使用小而可组合的函数，不要过早塞进单个 agent loop。

Metric comparison 应保持保守。未知数值指标可以记录 delta，但除非 manifest 显式配置方向或命中简单本地 heuristic，否则不应用于 improved/regressed verdict。只有当指标命名约定足够常见、不令人意外时，才添加新的默认 heuristic。

## 扩展 Report 与 Audit

Report system 是 V2.4 用来承接 research-only survey、experiment report 和
embedded code-task result 的出口。它应该保持 template-driven 和 evidence-aware，
不要退回到单个大 prompt 或单个大 service 文件。

```text
src/simple_ar/report/
  schema.py        context、memory、tools、draft、review 的 Pydantic models
  context.py       收集 papers、synthesis、metrics 和 code-task comparison
  templates.py     加载 Markdown 模板和 reviewer criteria
  memory.py        紧凑 section plan、evidence handles、claims、limitations
  tools.py         report tool schema definitions
  tool_gateway.py  有边界的只读 tool execution
  retrieval.py     source-handle backtracking
  agent.py         Writer/Reviewer orchestration
  citations.py     citation key 映射、显示标签和 citation cleanup
  audit.py         citation、metric、claim 和 reviewer audit 汇总
  assembler.py     section drafts 组装为最终 Markdown
  quality.py       deterministic report quality checks
  service.py       stage entrypoint 和 artifact packaging
```

新增 report 行为时：

- schema 放在 `schema.py`，不要在 `service.py` 里继续堆自由 dict；
- source lookup / backtracking 放到 `context.py`、`retrieval.py` 或
  `tool_gateway.py`；
- Writer/Reviewer loop 行为放到 `agent.py`；
- citation 映射、显示转换和 cleanup 放到 `citations.py`；
- 机械一致性检查放到 `audit.py` 或 `quality.py`；
- 模板和审查标准放在 `templates/report/`，不要硬编码成长 prompt；
- `service.py` 只保留 stage-level coordinator 和 artifact writer 的职责。

`report/service.py`、`pipeline_stages/research.py` 和 `cli/main.py` 仍然偏大，
需要视为黄灯。不要继续往这些文件里添加无关行为；新的工作应该迁移到对应领域模块，
或者顺手减少这些文件的职责。这是维护规则，不是要求把每个小 helper 都拆成独立文件。

## 扩展 Tools 和外部 Agent Backend

V2.6 新增 common tool 与 handoff 层，但不替换已有领域实现：

```text
src/simple_ar/tools/
  specs.py        CommonToolSpec、ToolCall、ToolResult、permission/risk enums
  registry.py     把 report 和 experiment tools 组合成统一 registry
  gateway.py      带权限检查的本地 dispatch 和紧凑 trace 写入
  permissions.py  read/write/execution/network policy checks
  openai_schema.py / mcp_schema.py
                  只导出 schema；默认不启动 server
  mcp_server.py   显式启动的 stdio MCP server，只暴露 run-local 只读 tools

src/simple_ar/agent_backends/
  base.py         AgentBackend protocol 和 run result models
  policy.py       写入 handoff 的外部 agent permission policy
  handoff.py      workspace-scoped handoff package 和不可信输出收集
  factory.py      fake/local_llm/Codex/Claude/OpenCode 的 provider selection
  fake.py         deterministic backend，用于集成测试和 dry-run
  local_llm.py    LLM-backed bounded reviewer/planner backend
  external_cli.py subprocess wrapper，包含 cwd、timeout、env allowlist 和日志
  profiles/       Codex / Claude Code / OpenCode profile Markdown
```

这个 common layer 刻意保持很薄。`experiment/tools/` 和
`report/tool_gateway.py` 仍然拥有具体业务逻辑；`tools/` 只负责给未来
OpenAI tool calling、MCP adapter 和外部 agent backend 提供统一、可审计的出口。

新增 tool/backend 时：

- 只注册真实可用的工具；不要为了展示 MCP/OpenAI schema 添加 stub tool；
- write、shell、network、secret access 默认关闭，除非配置和审批路径明确开启；
- 外部 agent 上下文写入 `agent_handoff/<name>/`，默认不要写用户全局工具目录；
- 外部 agent 输出一律视为不可信。先收集到 `agent_outputs/<name>/`，再交给已有
  patch、result guard、report audit 或 code-task validation 路径；
- 外部 CLI provider 必须保持 opt-in。`fake` 和 `local_llm` 可用于测试与本地 review；
  `codex`、`claude_code`、`opencode` 和 `external_cli` 必须等配置显式允许后才能启动；
- trace 默认保持紧凑。raw prompt、raw output 或大 payload 只能在 debug 设置下保留。

### Code-Task Environment Policy

当前 code-task runner 通过 `copy`、`git_worktree` 或实验性 `sparse_copy` 提供 workspace isolation，并支持 command timeout、可选 benchmark output streaming、stdout/stderr 捕获、受限 environment map 和显式 execution interpreter policy。它支持 `current` 和 `external`，但还不会创建或安装到单独 Python environment。除非未来功能明确改变这一点，否则不要默认把用户项目依赖安装到 SimpleAutoResearch 自己的 `.venv`。

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
- `docs/CLI_REFERENCE.md` / `docs/CLI_REFERENCE_zh.md`：命令组和参数表。
- `docs/CONFIG_REFERENCE.md` / `docs/CONFIG_REFERENCE_zh.md`：TOML schema 和配置示例。
- `docs/WORKFLOWS.md` / `docs/WORKFLOWS_zh.md`：每个 workflow/stage 做什么，以及产物结构。
- `docs/DEVELOPMENT.md` / `docs/DEVELOPMENT_zh.md`：贡献者指南。
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
uv run simple-ar-checks core
```

也可以不用 console script，直接运行脚本入口：

```bash
uv run python scripts/run_checks.py code-task
```

推荐验证层级：

| 修改范围 | 建议检查 |
| --- | --- |
| 仅文档 | `git diff --check` 加人工检查链接。 |
| 小型 parser、prompt、config、metric 或 CLI 改动 | `uv run simple-ar-checks quick`。 |
| Code-task 内部、workspace、repo-map、patching、validation、runner、repair | `uv run simple-ar-checks code-task`。 |
| 内置 code-task 示例或 benchmark 示例 | `uv run simple-ar-checks code-task-examples`。 |
| Pipeline、stages、experiment templates、run config | `uv run simple-ar-checks pipeline`。 |
| Literature、retrieval、evidence ledger、report generation、LLM adapter | `uv run simple-ar-checks research`。 |
| Core capability boundary、registry、attempt store 和能力包示例 | `uv run simple-ar-checks core`。 |
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

运行配置解析和公开 example 配置加载测试：

```bash
uv run python -m unittest tests.test_run_config
```

## Git 卫生

- 保持 feature commit 聚焦，避免混入无关重构。
- 不提交 `.env`、run outputs、caches 或私有学习笔记。
- README 保持简洁；详细行为放到 docs。
- 用户可见命令、artifact 或 workflow 行为变化时，更新 `CHANGELOG.md`。
