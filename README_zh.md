# SimpleAutoResearch

[English version](README.md)

SimpleAutoResearch 是一个以学习为优先、轻量化的自动科研项目，参考了 [AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw) 的阶段式科研思路。它尝试把“研究主题”推进到文献笔记、小型实验、可执行结果、代码任务流程和 Markdown 报告，同时让每一步都能被查看、恢复和调试。

这个项目的目标不是复刻一个庞大的 agent 框架，而是构建一个清晰、可检查、适合学习和逐步扩展的版本。

## 目标

- 让研究流程显式化，并以文件作为主要中间产物。
- 让每次运行易于检查、恢复和调试。
- 同时支持文献/报告流程和已有代码改进流程。
- 优先使用可控、可复现实验，而不是无约束代码生成。
- 让代码规模保持在学习者和贡献者可以理解的范围内。

## 安装与配置

克隆仓库：

```bash
git clone https://github.com/Wchanging/SimpleAutoResearch.git
cd SimpleAutoResearch
```

使用 `uv` 安装依赖：

```bash
uv sync
```

创建本地环境变量文件：

```bash
cp .env.example .env
```

PowerShell：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`。如果要运行 LLM 支持的阶段，需要至少配置 API key：

```bash
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.openai.com/v1
SIMPLE_AR_MODEL=gpt-4o-mini
SIMPLE_AR_LLM_TIMEOUT_SEC=120
SIMPLE_AR_MAX_OUTPUT_TOKENS=4096
SIMPLE_AR_INPUT_PRICE_PER_1M=
SIMPLE_AR_OUTPUT_PRICE_PER_1M=
```

如果使用第三方 OpenAI 兼容接口，把 `OPENAI_BASE_URL` 指向对应服务的 `/v1` 地址即可。`SIMPLE_AR_LLM_TIMEOUT_SEC` 用来限制单次 provider 请求等待时间，`SIMPLE_AR_MAX_OUTPUT_TOKENS` 用来限制较长 coding prompt 的输出规模。价格字段是可选项；不填写时，SimpleAutoResearch 仍会记录 token 数量，但费用估算会显示为 `null`。

## 快速开始：选择一个工作流

### 1. Research Report：文献优先报告

```bash
uv run simple-ar run --topic "agent simulation" --to-stage report --max-papers 5
```

默认 8 阶段流程在到达 `report` 时会包含设计、代码、运行阶段。如果只想做文献综述，可以先停在 `synthesize`：

```bash
uv run simple-ar run --topic "agent simulation" --to-stage synthesize
```

然后从已有产物生成纯研究报告：

```bash
uv run simple-ar resume runs/<run-id> --from-stage report
```

也可以显式指定报告模式：

```bash
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode research_only
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode experiment
```

### 2. Code Task：已有代码库修改

当你已经有一个 baseline 项目，希望 LLM 在隔离 workspace 中提出可审核改进时，推荐使用 TOML 配置加状态感知的 `execute`：

```bash
uv run simple-ar code-task init --config examples/code_tasks/configs/tiny_digits_mlp.toml
```

如果在 PowerShell 里想复制后续命令，可以把最新 run 目录保存到 `$RUN`：

```powershell
$RUN = Join-Path "runs" ((Get-ChildItem .\runs -Directory |
  Where-Object { $_.Name -like "*tiny-digits-mlp*" } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1).Name)
```

运行带人工审核点的 executor 流程：

```powershell
uv run simple-ar code-task execute $RUN --config examples/code_tasks/configs/tiny_digits_mlp.toml

# 阅读 code_task/patch_plan.md 后批准计划。
uv run simple-ar code-task decide-plan $RUN --decision approve --note "reviewed"

# 明确运行到 edit proposal 审核点。
uv run simple-ar code-task execute $RUN `
  --config examples/code_tasks/configs/tiny_digits_mlp.toml `
  --to-step propose-edits

# 显式允许应用已审核的 proposal，并运行验证和 benchmark。
uv run simple-ar code-task execute $RUN `
  --config examples/code_tasks/configs/tiny_digits_mlp.toml `
  --apply-proposed-edits `
  --timeout 60

uv run simple-ar status $RUN
```

第一次 `execute` 会在写入环境、baseline、work plan、batch state 和 patch plan 后停在 `approval_required`。第二次 executor 调用会生成 `code_task/meta/proposed_edits.json` 供审核。最后一次 executor 调用会应用 proposal、验证 workspace、运行 patched benchmark、写入 `code_task/run/comparison.json`，并刷新 `code_task/summary.md`。正常成功信号是 `objective_improved`；如果 benchmark 通过但 objective 是 `regressed` 或 `mixed`，应继续修改 plan/proposal，而不是把任务标记为完成。
默认 editor backend 是 `controlled_patch`，它生成受预算限制的 old/new replacement，并在 proposal、apply、batch 和 manifest 产物中记录 backend metadata。

如果想试一个更接近真实项目的多文件示例，包含 `main.py` 入口、JSON config、运行进度输出，以及跨模块的 feature/model wiring，可以使用：

```bash
uv run simple-ar code-task init --config examples/code_tasks/configs/medium_review_pipeline.toml
```

然后沿用上面的 executor 流程，只需要把 config 路径和 `$RUN` 筛选条件换成
medium 示例即可。该配置启用了 streamed benchmark output，所以 `execute`
会把进度行转发到命令行，同时仍把完整日志保存到 `code_task/run/<label>/`。
medium 任务通常会把 feature、model 和 config 改动合并为一个已审核的
`large` batch，因此最后应用 proposal 时，只有在审核
`code_task/meta/proposed_edits.json` 后才应显式加入 `--allow-large-edits`。

如果补丁需要修复，可以请求一个有限范围的 repair proposal：

```powershell
uv run simple-ar code-task execute $RUN `
  --config examples/code_tasks/configs/tiny_digits_mlp.toml `
  --to-step repair `
  --repair-rounds 1 `
  --timeout 60
```

更完整的 primitive commands、显式 CLI 参数初始化、产物说明和排错流程见 [使用与配置](docs/USAGE_zh.md#code-task-工作流) 与 [CLI 参考](docs/CLI_REFERENCE_zh.md#code-task-commands)。

### 3. Research With Experiment：研究流程衔接代码实验

推荐使用顶层 run config：

```bash
uv run simple-ar run --config examples/run_configs/tiny_digits_mlp_pipeline.toml
```

也可以用 CLI 参数快速覆盖：

```bash
uv run simple-ar run \
  --topic "improve tiny digits MLP" \
  --to-stage report \
  --experiment-template code_task_project \
  --code-task-config examples/code_tasks/configs/tiny_digits_mlp.toml \
  --offline-search \
  --experiment-timeout 60
```

这个流程会把配置中的项目准备到 `06-code/code_task_run/code_task/workspace`，运行 baseline benchmark，构建 repo map / context pack，调用 LLM 生成批次式 work plan，再创建 attempt/batch 记录，随后生成 patch plan 和受控编辑 proposal，在隔离 workspace 内应用补丁，运行 patched benchmark，并把 code-task 证据写入最终报告。如果 `code_task_project` 没有提供 task file，`05-design` 会基于前面研究阶段的产物和紧凑代码摘要生成 `generated_code_task.md`，然后 `06-code` 将它作为正常的 `code_task/task.md` 输入。

还有一个 legacy toy-spam smoke test，主要用于快速回归测试：

```bash
uv run simple-ar run \
  --topic "LLM-guided improvement of a toy spam baseline" \
  --to-stage report \
  --experiment-template llm_code_task_toy_spam \
  --offline-search \
  --experiment-timeout 60
```

## 当前能力边界

SimpleAutoResearch 已经可以作为学习和原型实验框架使用，但它仍然故意保持保守。

当前可用能力：

- 从 topic 到 report 的 8 阶段流程，产物可见，并支持 resume。
- OpenAI 兼容 LLM 调用，用于 planning、paper notes、synthesis、report drafting 和 code-task patch planning。
- 文献优先报告模式：停在 `synthesize` 后继续 `report`，生成 survey 风格报告。
- Standalone code task：准备已有项目，使用 `copy`、`git_worktree` 或实验性 `sparse_copy` 建立隔离 workspace，探测环境，索引代码，构建 repo map，定位相关文件，生成受限 context pack，运行 baseline，生成批次式 work plan，创建 attempt/batch，生成可审核 patch plan，调用默认 `controlled_patch` editor backend 生成受控 edits，应用补丁，验证，运行 patched benchmark，并比较前后指标。
- 默认 edit scope 会保护 tests、benchmark 文件和 secret-like 路径，模型可以读取这些信息作为证据，但不能自动修改它们来刷指标。
- 支持通过 CLI 或 TOML 配置 benchmark metric 的解释方式。
- 支持通过 `code_task_project` 把已有代码任务嵌入 8 阶段流程。task file 可以由用户提供，也可以在 `05-design` 自动生成。
- 最终报告包含 citation、metric visibility、runtime limit 和 toy-evidence boundary 等规则检查。

重要限制：

- 通用 8 阶段 code-task 路径是真实可运行的，但仍偏保守。它还不是深度多轮、自主配置环境、自动 Docker/Conda/GPU/Slurm 调度的大型 coding agent。
- 8 阶段内嵌 code-task 为了跑完整流程，会在隔离 workspace 内自动创建 context pack、work plan 和首个 implementation batch，并自动批准 patch plan；如果需要强人工审核，应使用 standalone `code-task`。
- 当前代码编辑是受控 old/new replacement。它更可审计，但弱于完整 coding agent 的自由多文件、多轮重构能力。
- editor backend 接口已经存在。预留的 `external_agent` backend 现在有设计期权限模型和 invocation-plan artifact，但 Codex / Claude / OpenCode adapter 还不能执行。
- 较大的代码修改 proposal 仍可能触发很长的 LLM completion。V2.2 会把它作为 editor backend 设计目标处理：增加 bounded proposal contract、context request、多轮 attempt，以及未来 external coding-agent adapter；在这些能力成熟前，不建议把它当作大型无人值守重构工具。
- 默认拒绝自动修改 `tests/**`、`test_*.py`、`benchmark.py`、`*benchmark*.py` 等保护路径。
- 目前不会自动安装项目依赖，也不会自动管理 Docker/Conda/GPU/Slurm 环境。
- 文献检索主要基于元数据和本地产物片段，还不是完整 PDF 阅读或向量 RAG survey 系统。
- LLM 报告有规则保护；如果引用、指标或边界声明不合格，会回退到结构化 deterministic report。

V2.2 正在推进 workspace-mode abstraction、git worktree、实验性 sparse-copy、分层 repo-map、确定性 locate results 和受限 context pack。下一步会继续推进多轮 attempt、更强任务拆解、环境管理和更清晰的人类审核路径。

## 文档

- [使用与配置](docs/USAGE_zh.md)：安装、环境变量、常用命令和示例。
- [CLI 参考](docs/CLI_REFERENCE_zh.md)：命令组、参数表和配置 schema。
- [工作流与产物](docs/WORKFLOWS_zh.md)：预设工作流、8 阶段流程和产物布局。
- [开发指南](docs/DEVELOPMENT_zh.md)：如何扩展 stage、template 和 code-task 模块。
- [Changelog](CHANGELOG_zh.md)：按时间记录的开发进展。

## 参考项目

主要参考项目是 [aiming-lab/AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw)。SimpleAutoResearch 借鉴了阶段式自动科研的思想，但实现保持紧凑、可学习、可逐步扩展。

## 社区

这是一个早期、学习导向的项目。欢迎 issue、建议、实验结果和小而聚焦的 pull request，尤其是 coding-agent workflow、可复现实验执行、报告质量和文档清晰度相关方向。
