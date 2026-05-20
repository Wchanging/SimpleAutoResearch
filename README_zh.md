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
SIMPLE_AR_INPUT_PRICE_PER_1M=
SIMPLE_AR_OUTPUT_PRICE_PER_1M=
```

如果使用第三方 OpenAI 兼容接口，把 `OPENAI_BASE_URL` 指向对应服务的 `/v1` 地址即可。价格字段是可选项；不填写时，SimpleAutoResearch 仍会记录 token 数量，但费用估算会显示为 `null`。

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

```bash
uv run simple-ar code-task init \
  --code-root examples/code_tasks/toy_spam_project \
  --task-file examples/code_tasks/tasks/improve_toy_spam_baseline.md \
  --benchmark-command "python -m unittest discover -s tests"
```

一个更接近真实机器学习场景的轻量示例：

```bash
uv run simple-ar code-task init \
  --code-root examples/code_tasks/tiny_digits_mlp_project \
  --task-file examples/code_tasks/tasks/improve_tiny_digits_mlp.md \
  --benchmark-command "python benchmark.py" \
  --primary-metric accuracy \
  --metric-direction accuracy=higher \
  --env-mode current
```

Code Task 有两种运行方式。

手动路径，适合学习每个步骤：

```bash
uv run simple-ar code-task probe runs/<run-id>
uv run simple-ar code-task baseline runs/<run-id> --timeout 60
uv run simple-ar code-task plan runs/<run-id>
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve
uv run simple-ar code-task propose-edits runs/<run-id>
uv run simple-ar code-task apply-edits runs/<run-id>
uv run simple-ar code-task validate runs/<run-id>
uv run simple-ar code-task run runs/<run-id> --timeout 60
```

较短的人工审核路径：

```bash
# 运行到 plan 审核点。
uv run simple-ar code-task execute runs/<run-id>

# 阅读 code_task/patch_plan.md 后批准计划。
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve

# 运行到 edit proposal 审核点。
uv run simple-ar code-task execute runs/<run-id>

# 显式允许应用已审核的 proposal，并运行验证和 benchmark。
uv run simple-ar code-task execute runs/<run-id> --apply-proposed-edits --timeout 60
```

`execute` 是一个状态感知的便捷命令。它会查看当前 run 目录里已经有哪些产物，然后推进到下一个安全停止点。它不会跳过人工审核。第一次通常停在 `approval_required`；批准计划后会生成 `proposed_edits.json` 并再次停下；只有提供 `--apply-proposed-edits` 后才会应用补丁并运行验证和 benchmark。

benchmark 最好输出 `name: value` 形式的数值指标。`--primary-metric` 指定主要目标，`--metric-direction METRIC=higher|lower|resource|ignore` 指定指标解释规则。详见 [CLI 参考](docs/CLI_REFERENCE_zh.md#init)。

如果指标和环境参数较多，建议放到 TOML 配置：

```bash
uv run simple-ar code-task init --config examples/code_tasks/configs/tiny_digits_mlp.toml
```

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

这个流程会把配置中的项目准备到 `06-code/code_task_run/code_task/workspace`，运行 baseline benchmark，调用 LLM 生成 patch plan 和受控编辑 proposal，在隔离 workspace 内应用补丁，运行 patched benchmark，并把 code-task 证据写入最终报告。如果 `code_task_project` 没有提供 task file，`05-design` 会基于前面研究阶段的产物和紧凑代码摘要生成 `generated_code_task.md`，然后 `06-code` 将它作为正常的 `code_task/task.md` 输入。

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
- Standalone code task：准备已有项目，使用 `copy`、`git_worktree` 或实验性 `sparse_copy` 建立隔离 workspace，探测环境，索引代码，运行 baseline，生成可审核 patch plan，提出受控 edits，应用补丁，验证，运行 patched benchmark，并比较前后指标。
- 默认 edit scope 会保护 tests、benchmark 文件和 secret-like 路径，模型可以读取这些信息作为证据，但不能自动修改它们来刷指标。
- 支持通过 CLI 或 TOML 配置 benchmark metric 的解释方式。
- 支持通过 `code_task_project` 把已有代码任务嵌入 8 阶段流程。task file 可以由用户提供，也可以在 `05-design` 自动生成。
- 最终报告包含 citation、metric visibility、runtime limit 和 toy-evidence boundary 等规则检查。

重要限制：

- 通用 8 阶段 code-task 路径是真实可运行的，但仍偏保守。它还不是深度多轮、自主配置环境、自动 Docker/Conda/GPU/Slurm 调度的大型 coding agent。
- 8 阶段内嵌 code-task 为了跑完整流程，会在隔离 workspace 内自动批准 patch plan；如果需要强人工审核，应使用 standalone `code-task`。
- 当前代码编辑是受控 old/new replacement。它更可审计，但弱于完整 coding agent 的自由多文件、多轮重构能力。
- 默认拒绝自动修改 `tests/**`、`test_*.py`、`benchmark.py`、`*benchmark*.py` 等保护路径。
- 目前不会自动安装项目依赖，也不会自动管理 Docker/Conda/GPU/Slurm 环境。
- 文献检索主要基于元数据和本地产物片段，还不是完整 PDF 阅读或向量 RAG survey 系统。
- LLM 报告有规则保护；如果引用、指标或边界声明不合格，会回退到结构化 deterministic report。

V2.2 正在推进 workspace-mode abstraction、git worktree、实验性 sparse-copy，以及更深入的 coding loop：repo map、context pack、多轮 attempt、更强任务拆解和更清晰的人类审核路径。

## 文档

- [使用与配置](docs/USAGE_zh.md)：安装、环境变量、常用命令和示例。
- [CLI 参考](docs/CLI_REFERENCE_zh.md)：命令组、参数表和配置 schema。
- [工作流与产物](docs/WORKFLOWS_zh.md)：预设工作流、8 阶段流程和产物布局。
- [开发指南](docs/DEVELOPMENT_zh.md)：如何扩展 stage、template 和 code-task 模块。
- [Code Task Workspace 笔记](docs/CODE_TASK_WORKSPACE_zh.md)：V2.2 workspace mode 的设计、假设和迁移点。
- [Changelog](CHANGELOG_zh.md)：按时间记录的开发进展。

## 参考项目

主要参考项目是 [aiming-lab/AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw)。SimpleAutoResearch 借鉴了阶段式自动科研的思想，但实现保持紧凑、可学习、可逐步扩展。

## 社区

这是一个早期、学习导向的项目。欢迎 issue、建议、实验结果和小而聚焦的 pull request，尤其是 coding-agent workflow、可复现实验执行、报告质量和文档清晰度相关方向。
