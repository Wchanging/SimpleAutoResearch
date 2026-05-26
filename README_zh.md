# SimpleAutoResearch

[English version](README.md)

SimpleAutoResearch 是一个以学习为优先、轻量化的自动科研项目，参考了
[AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw) 的阶段式科研思路。它尝试把“研究主题”推进到文献笔记、小型实验、已有代码改进任务、可执行结果和 Markdown 报告，同时让每一步都能被查看、恢复和调试。

这个项目的目标不是复刻一个庞大的 agent 框架，而是构建一个清晰、可检查、适合学习和逐步扩展的版本。

## 目标

- 让研究流程显式化，并以文件作为主要中间产物。
- 让每次运行易于检查、恢复和调试。
- 同时支持文献/报告流程和已有代码改进流程。
- 优先使用可控、可复现实验，而不是无约束代码生成。
- 让代码规模保持在学习者和贡献者可以理解的范围内。

## 当前可用能力

- **研究报告**：从主题出发，运行可见的阶段式流程，生成文献笔记、综合分析和报告产物。
- **研究源规划**：每次 `02-search` 会写入紧凑的 `planning/research_plan.json`，记录研究问题、query plan、OpenAlex/Semantic Scholar/arXiv/本地文件源、可选 LLM-backed query planning、facet-driven query expansion、retrieval-round traces、screening decisions、coverage reports、follow-up retrieval rounds、document records、cache 策略和轻量预算。
- **Code Task**：在隔离可编辑 workspace 中改进已有代码库，支持 LLM 规划、人工审核点、受控补丁 proposal、验证、benchmark 运行和指标对比。
- **Workspace 策略**：`copy` 是最稳妥的隔离副本；`git_worktree` 适合较大的 git 仓库；实验性 `sparse_copy` 适合你明确知道 include 范围的小型子集。
- **研究到代码实验**：可以把 code task 嵌入 8 阶段流程，生成 repo map、context pack、work plan、patch 证据、benchmark 指标和报告证据。
- **可审查产物**：每次运行都把关键决策写入 `runs/` 下的文件，而不是隐藏在进程内存里。

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

如果使用第三方 OpenAI 兼容接口，把 `OPENAI_BASE_URL` 指向对应服务的 `/v1` 地址即可。价格字段是可选项；不填写时，SimpleAutoResearch 仍会记录 token 数量，但费用估算会显示为 `null`。

## 快速开始

### 1. Research Report：文献优先报告

```bash
uv run simple-ar run --topic "agent simulation" --to-stage report --max-papers 5
```

如果希望把搜索源、query 和本地资料写成可复用配置，可以使用 run config。下面这个本地示例会把 Markdown 笔记作为研究源，并在 `02-search/planning/research_plan.json` 中记录本次检索策略：

```bash
uv run simple-ar run --config examples/run_configs/local_research_report.toml
```

如果只想做文献综述，可以先停在 `synthesize`，再从打印出的 run 目录生成研究报告：

```bash
uv run simple-ar run --topic "agent simulation" --to-stage synthesize
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode research_only
```

### 2. Code Task：已有代码库修改

当你已经有一个项目，希望模型提出可审核的改进时，先写一个简短任务文件，例如 `tasks/improve_model.md`，说明希望修改什么、用什么 benchmark 判断效果。然后为自己的项目创建一个 TOML 配置：

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
mode = "copy"  # copy | git_worktree | sparse_copy
```

接着运行完整的人工审核流程。`init` 会打印一个新的 run 目录，例如
`runs/20260523-xxxx-my-code-task`；后续命令把 `runs/<run-id>` 替换成这个实际路径即可。

```bash
uv run simple-ar code-task init --config path/to/your_code_task.toml
uv run simple-ar code-task execute runs/<run-id> --config path/to/your_code_task.toml
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve --note "reviewed"
uv run simple-ar code-task execute runs/<run-id> --config path/to/your_code_task.toml --to-step propose-edits
uv run simple-ar code-task execute runs/<run-id> --config path/to/your_code_task.toml --apply-proposed-edits --timeout 60
uv run simple-ar status runs/<run-id>
```

这套命令会准备隔离 workspace、运行 baseline benchmark、生成 work plan、停在 patch plan 审核点、生成 `code_task/meta/proposed_edits.json`、应用已审核 proposal、验证 patched workspace、运行 patched benchmark，并写入最终状态。如果结果还需要有限范围的后续修复，可以继续使用 [使用与配置](docs/USAGE_zh.md#推荐路径toml--execute) 中的 repair 流程。

内置 demo 配置，例如 `tiny_digits_mlp.toml` 和 `medium_review_pipeline.toml`，放在 [使用与配置](docs/USAGE_zh.md#推荐路径toml--execute) 中作为辅助示例。

### 3. Research With Experiment：研究流程衔接代码实验

当你希望研究流程先收集文献上下文，再衔接已有代码项目完成实验修改，并把代码证据写入最终报告时，使用这个模式。针对自己的项目，可以创建一个顶层 run config：

```toml
[run]
topic = "research and improve my model"
output_root = "runs"
to_stage = "report"

[llm]
enabled = true

[search]
offline = false
max_papers = 5

[research]
# 可选：02-search 的 source planner。
mode = "standard"  # lite | standard | strong
sources = ["openalex", "semantic_scholar", "arxiv"]
queries = ["research and improve my model"]
cache = true

[experiment]
template = "code_task_project"
timeout = 120

[code_task]
code_root = "path/to/your/project"
# 可选。如果不提供，05-design 会根据研究产物和紧凑代码摘要生成任务文件。
task_file = "tasks/improve_model.md"
name = "my-research-code-task"

[benchmark]
command = "python benchmark.py"
primary_metric = "accuracy"

[benchmark.metric_directions]
accuracy = "higher"
latency_ms = "resource"

[workspace]
mode = "copy"  # copy | git_worktree | sparse_copy

[environment]
mode = "current"
```

然后运行完整流程：

```bash
uv run simple-ar run --config path/to/your_pipeline.toml
```

这会创建一次正常的 8 阶段 run。在 `06-code` 中，系统会把配置中的项目准备到 `06-code/code_task_run/code_task/workspace`，构建 repo map 和 context pack，调用 LLM 生成 work plan 与 patch proposal，在隔离 workspace 内应用补丁并验证。`07-run` 会运行 patched benchmark 并比较指标；`08-report` 会生成最终报告，并把嵌套的 work plan、patch、benchmark 和 comparison 产物作为确定性 code-task 证据写进去。

内嵌路径的目标是端到端跑完，因此会在隔离 workspace 中自动批准 patch plan。如果你希望每一步都先人工审核，应使用 standalone `code-task` 命令。内置 demo 配置在 `examples/run_configs/tiny_digits_mlp_pipeline.toml`；完整说明见 [使用与配置](docs/USAGE_zh.md#8-阶段流程中的内嵌-code-task)。

## 当前能力边界

SimpleAutoResearch 已经可以作为学习和原型实验框架使用，但它仍然故意保持保守。

- 当前代码编辑是受控 old/new replacement，更可审计，但弱于完整自主 coding agent。
- 默认 edit scope 会保护 tests、benchmark 文件和 secret-like 路径，避免模型通过修改评测来刷指标。
- `git_worktree` 要求目标项目是 git 仓库根目录，并且至少有一个本地 commit；不要求连接 GitHub 远程仓库。
- `sparse_copy` 仍是实验性功能，如果 include 范围过窄，可能漏掉运行依赖。
- 目前不会自动安装项目依赖，也不会自动管理 Docker/Conda/GPU/Slurm 环境。
- 较大的代码修改 proposal 仍可能触发很长的 LLM completion。V2.2 会继续加入 bounded proposal contract、context request、多轮 attempt 和未来 external coding-agent adapter；在这些能力成熟前，不建议把它当作大型无人值守重构工具。
- 文献检索现在会写入可审计的 source plan 和 document-store metadata，并支持 OpenAlex、Semantic Scholar、arXiv 和本地 Markdown/text 笔记，但还不是完整 PDF 解析、parser-backed 或向量 RAG survey 系统。
- LLM 报告有引用、指标和边界规则保护；如果草稿不合格，会回退到结构化 deterministic report。

## 文档

- [使用与配置](docs/USAGE_zh.md)：安装、工作流示例、产物说明和排错流程。
- [CLI 参考](docs/CLI_REFERENCE_zh.md)：命令组和参数表。
- [配置参考](docs/CONFIG_REFERENCE_zh.md)：TOML section、完整配置示例和 workspace 模式变体。
- [工作流与产物](docs/WORKFLOWS_zh.md)：预设工作流、8 阶段流程和产物布局。
- [开发指南](docs/DEVELOPMENT_zh.md)：如何扩展 stage、template 和 code-task 模块。
- [Changelog](CHANGELOG_zh.md)：按时间记录的开发进展。

## 参考项目

主要参考项目是 [aiming-lab/AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw)。SimpleAutoResearch 借鉴了阶段式自动科研的思想，但实现保持紧凑、可学习、可逐步扩展。

## 社区

这是一个早期、学习导向的项目。欢迎 issue、建议、实验结果和小而聚焦的 pull request，尤其是 coding-agent workflow、可复现实验执行、报告质量和文档清晰度相关方向。
