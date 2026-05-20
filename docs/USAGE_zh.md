# 使用与配置

[English version](USAGE.md)

本文说明如何安装、配置和运行 SimpleAutoResearch。它是面向用户的实践指南；工作流概念和产物结构见 [工作流与产物](WORKFLOWS_zh.md)，完整命令表见 [CLI 参考](CLI_REFERENCE_zh.md)。

## 环境要求

- Python 3.12 或更高版本。
- 使用 `uv` 管理依赖。
- 如果要运行 LLM 支持的 planning、notes、synthesis、report 或 code edits，需要一个 OpenAI 兼容 API key。

## 安装

克隆仓库：

```bash
git clone https://github.com/Wchanging/SimpleAutoResearch.git
cd SimpleAutoResearch
```

安装依赖：

```bash
uv sync
```

检查 CLI 是否可用：

```bash
uv run simple-ar --help
```

## 环境变量配置

创建本地 `.env`：

```bash
cp .env.example .env
```

PowerShell：

```powershell
Copy-Item .env.example .env
```

支持的配置：

```bash
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.openai.com/v1
SIMPLE_AR_MODEL=gpt-4o-mini
SIMPLE_AR_LLM_TIMEOUT_SEC=120
SIMPLE_AR_MAX_OUTPUT_TOKENS=4096
SIMPLE_AR_INPUT_PRICE_PER_1M=
SIMPLE_AR_OUTPUT_PRICE_PER_1M=
```

说明：

- `OPENAI_API_KEY` 是 LLM 模式必需项。
- `OPENAI_BASE_URL` 可以指向 OpenAI，也可以指向第三方 OpenAI 兼容 `/v1` 接口。
- `SIMPLE_AR_MODEL` 是没有传入 `--model` 时的默认模型。
- `SIMPLE_AR_LLM_TIMEOUT_SEC` 限制单次 provider 请求等待时间；较大的 coding prompt 如果确实需要更久，可以适当调高。
- `SIMPLE_AR_MAX_OUTPUT_TOKENS` 限制模型输出长度，避免较长 coding prompt 生成过大的结果。
- 价格字段只影响 usage summary 中的费用估算；不填也会记录 token。

## Research Pipeline：从主题到报告

运行默认 8 阶段流程：

```bash
uv run simple-ar run --topic "toy topic" --to-stage report
```

如果参数较多，推荐使用顶层 TOML 配置：

```bash
uv run simple-ar run --config examples/run_configs/tiny_digits_mlp_pipeline.toml
```

配置文件可以包含 `[run]`、`[llm]`、`[search]`、`[retrieval]`、`[experiment]`、`[report]`，也可以包含和 `code-task init --config` 相同的 `[code_task]`、`[benchmark]`、`[metrics]`、`[environment]`、`[workspace]`、`[safety]`。显式 CLI 参数会覆盖配置文件。完整示例和字段解释见 [CLI 参考](CLI_REFERENCE_zh.md#run-config)。

只做文献分析时，可以先停在 `synthesize`：

```bash
uv run simple-ar run --topic "toy topic" --to-stage synthesize
```

再从已有产物生成 literature-only report：

```bash
uv run simple-ar resume runs/<run-id> --from-stage report
```

默认 `report-mode` 是自动判断：如果没有 `results.json`，就写 research-only 结构；如果有实验结果，就写 experiment 结构。也可以强制指定：

```bash
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode research_only
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode experiment
```

### 哪些部分依赖 LLM，哪些部分是确定性的

- LLM 支持阶段：`plan`、`read`、`synthesize` 和 `report`。
- 默认确定性阶段：`design`、`code` 和 `run` 使用固定实验模板，除非选择了 code-task experiment template。
- 内嵌 code-task experiment：`06-code` 可以调用 LLM 生成 patch plan 和受控 edit proposal，但补丁只会应用到 run 目录下的隔离 workspace。
- Guarded reports：如果 LLM 报告缺少必要正文引用、虚构 citation key 或夸大 fixture/toy evidence，会回退到结构化 deterministic report。
- `--no-llm` 会让相关阶段使用离线 fallback 内容。

### 搜索模式和边界

默认搜索行为：

- `search` 先查 OpenAlex，再查 arXiv。
- 如果 live provider 失败且没有设置 `--strict-search`，会优先使用本地 cache。

常用控制：

```bash
uv run simple-ar run --topic "agent simulation" --to-stage search --strict-search
uv run simple-ar run --topic "agent simulation" --to-stage report --allow-fixture-fallback
uv run simple-ar run --topic "agent simulation" --to-stage report --offline-search
```

- `--strict-search` 禁用 cache/fixture fallback，live provider 失败就让 run 失败。
- `--allow-fixture-fallback` 允许 live provider 和 cache 都失败后使用 fixture metadata。
- `--offline-search` 跳过 live provider，直接使用 fixture metadata。

### 恢复和查看状态

恢复 run：

```bash
uv run simple-ar resume runs/<run-id>
uv run simple-ar resume runs/<run-id> --from-stage run --to-stage report
```

查看状态：

```bash
uv run simple-ar status runs/<run-id>
```

## Retrieval 和 Artifact 工具

用于检查或搜索某次 run 产生的文件：

```bash
uv run simple-ar inspect runs/<run-id>
uv run simple-ar search-artifacts runs/<run-id> "accuracy"
uv run simple-ar run --topic "toy topic" --to-stage report --retrieval-top-k 4
uv run simple-ar run --topic "toy topic" --to-stage report --no-retrieval
```

参数细节见 [CLI 参考](CLI_REFERENCE_zh.md#artifact-tools)。

## Code Task 工作流

Code Task 会把源项目准备到一个隔离的可编辑 workspace 中，后续所有补丁都只改这个 workspace，不修改原始项目。默认 `copy` 模式最稳妥；V2.2 还支持面向较大 git 项目的 `git_worktree`，以及适合小型 allowlist 子集的实验性 `sparse_copy`。

使用 CLI 参数初始化：

```bash
uv run simple-ar code-task init \
  --code-root examples/code_tasks/toy_spam_project \
  --task-file examples/code_tasks/tasks/improve_toy_spam_baseline.md \
  --benchmark-command "python -m unittest discover -s tests" \
  --env-mode current
```

指标、环境和 workspace 参数较多时，推荐 TOML：

```bash
uv run simple-ar code-task init --config examples/code_tasks/configs/tiny_digits_mlp.toml
```

`init` 会创建新的 `runs/<run-id>/`，把源项目准备到 `code_task/workspace/`，把任务写入 `code_task/task.md`，生成 `code_task/meta/codebase_index.json` 以及分层 `code_task/meta/repo_map.json` / `repo_map_summary.md`，并把 benchmark / environment / workspace 策略记录到 `manifest.json`。它不会运行代码、不会调用 LLM，也不会修改原始项目。

如果使用 `workspace.mode = "git_worktree"` 或 `--workspace-mode git_worktree`，`init` 会在 `code_task/workspace/` 创建 detached git worktree，而不是完整复制文件。当前要求 `code_root` 是目标项目的 git 仓库根目录；如果目录不满足要求，CLI 会给出可操作提示，比如初始化 git、提交初始 baseline、传入 repo root，或者改用 `copy` 模式。

如果使用 `workspace.mode = "sparse_copy"` 或 `--workspace-mode sparse_copy`，只会复制匹配 include pattern 的文件，同时始终排除 `.git`、virtualenv、`runs`、cache/build、`data`、`models`、`.env` 和 secret-like 路径。这个模式适合你明确知道需要哪些文件的小型实验；通用项目仍建议 `copy` 或 `git_worktree`。

benchmark 最好输出 `name: value` 数值行。自定义指标可以用 `--metric-direction` 或 TOML 配置声明解释方向。完整参数表见 [CLI 参考](CLI_REFERENCE_zh.md#init)，配置 schema 见 [CLI 参考](CLI_REFERENCE_zh.md#init-config)。

初始化后有两种执行风格：

- **Manual Path**：手动运行每个 primitive command，适合学习和调试。
- **Executor Path**：使用 `code-task execute` 推进到下一个安全步骤，更短，但仍保留审核点。

任何时候都可以刷新代码地图：

```bash
uv run simple-ar code-task map runs/<run-id>
```

`map` 会扫描当前 `code_task/workspace/`，刷新 `code_task/meta/codebase_index.json`，写入 `code_task/meta/repo_map.json` 和 `code_task/meta/repo_map_summary.md`，并更新 `manifest.json`。它不会调用 LLM、不会安装依赖、不会运行 benchmark，也不会修改原始项目。

定位最可能相关的可编辑文件和只读证据：

```bash
uv run simple-ar code-task locate runs/<run-id> --query "improve spam keyword prediction"
```

`locate` 会写入 `code_task/meta/locate_results.json` 和
`code_task/meta/locate_results.md`。它基于 repo map 对 path、summary、
imports、role tags 和 symbols 做轻量排序，并把 editable targets 与
read-only evidence 分开。它不会调用 LLM，也不会修改文件。

构建受预算限制的 prompt context pack：

```bash
uv run simple-ar code-task context runs/<run-id> --max-files 8 --max-total-chars 20000
```

`context` 会创建 `code_task/context_packs/context-NNN/`，其中包含
`context_pack.json`、`prompt_context.md` 和 `selected_snippets.jsonl`。
这些文件记录选择了哪些源码片段、哪些文件因为预算被省略、哪些内容只作为证据
而不能被自动修改。当前如果存在 latest context pack，`plan` 会优先使用它作为
规划上下文，`propose-edits` 只会读取其中 editable snippets，并继续把 tests /
benchmarks 作为 read-only evidence。

### Manual Path

先探测环境并运行未修改 baseline：

```bash
uv run simple-ar code-task map runs/<run-id>
uv run simple-ar code-task locate runs/<run-id>
uv run simple-ar code-task context runs/<run-id>
uv run simple-ar code-task probe runs/<run-id>
uv run simple-ar code-task baseline runs/<run-id> --timeout 60
```

`probe` 写入 `code_task/meta/environment_report.json`，包含 OS、Python、工具、GPU、依赖文件和 test 目录信号。它不安装依赖，也不运行项目代码。

`baseline` 在任何补丁应用前运行记录的 benchmark command，结果存到 `code_task/run/baseline/`，包括 `execution_report.json`、`stdout.txt`、`stderr.txt` 和解析后的 `metrics.json`，并刷新 `code_task/summary.md`。

生成 patch plan：

```bash
uv run simple-ar code-task plan runs/<run-id>
```

如果已有 `probe`、`validate` 或 `baseline` 产物，plan 会把这些上下文纳入模型/审核者可见信息中。`plan` 写入 `code_task/patch_plan.md`，更新 `manifest.json`，记录选择的上下文文件；它不会修改源文件。

审核并批准：

```bash
uv run simple-ar code-task decide-plan runs/<run-id> \
  --decision approve \
  --note "small scoped edit"
```

`decide-plan` 会把人工决策追加到 `code_task/meta/hitl_decisions.jsonl`，并更新 manifest 中的计划状态。

请求模型生成受控编辑 proposal：

```bash
uv run simple-ar code-task propose-edits runs/<run-id>
```

`propose-edits` 写入 `code_task/meta/proposed_edits.json`。proposal 使用 old/new 文本替换，供人工审核；它本身不会编辑 workspace。默认 tests 和 benchmark 文件是只读证据，proposal 不会给这些路径提供可编辑 snippet，后续 apply 也会再次拒绝保护路径。

应用已审核 edits：

```bash
uv run simple-ar code-task apply-edits runs/<run-id>
```

`apply-edits` 只修改 `code_task/workspace/`，写入 `code_task/patch.diff` 和 `code_task/meta/applied_edits.json`，并重建 codebase index。如果 edit 无法唯一匹配，会在写文件前停止。

验证并运行 patched benchmark：

```bash
uv run simple-ar code-task validate runs/<run-id>
uv run simple-ar code-task run runs/<run-id> --timeout 60
```

`validate` 写入 `code_task/meta/validation_report.json`，包含语法错误、危险 import/call、缺失 import warning 和文件尺寸 warning。它是静态检查，不运行 benchmark。

`run` 把 patched benchmark 存到 `code_task/run/patched/`。当 baseline 和 patched 都存在时，还会写入 `code_task/run/comparison.json`，并在 `summary.md` 中加入前后对比和下一步建议。

失败分析和修复 proposal：

```bash
uv run simple-ar code-task analyze-failure runs/<run-id>
uv run simple-ar code-task repair runs/<run-id>
```

`analyze-failure` 读取最近失败的 validation/benchmark evidence，写出紧凑诊断；它是确定性的，不调用 LLM。`repair` 会使用 failure analysis、最近 patch、task 和选中的源码上下文生成有限范围的 repair proposal，默认不自动应用。

显式应用审核后的 repair proposal：

```bash
uv run simple-ar code-task apply-edits runs/<run-id> \
  --edits-file runs/<run-id>/code_task/repairs/repair-001/proposed_edits.json
```

### Executor Path

最短人工审核路线：

```bash
# 运行到 plan 审核点。
uv run simple-ar code-task execute runs/<run-id>

# 阅读 code_task/patch_plan.md 后批准。
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve

# 运行到 edit proposal 审核点。
uv run simple-ar code-task execute runs/<run-id>

# 应用已审核 proposal，并运行验证和 benchmark。
uv run simple-ar code-task execute runs/<run-id> --apply-proposed-edits --timeout 60
```

重复调用 `execute` 是有意设计的。它的语义是“检查当前 run 状态，并推进到下一个安全停止点”，不是“跳过审核”。

- 第一次 `execute`：写 `environment_report.json`、baseline 产物、`patch_plan.md`，然后以 `approval_required` 停下。
- `decide-plan`：记录你的批准。
- 第二次 `execute`：写 `proposed_edits.json`，然后以 `proposal_review_required` 停下。
- 最后 `execute --apply-proposed-edits`：应用 proposal，写 `patch.diff`，验证 workspace，运行 patched benchmark，更新 `comparison.json` 和 `summary.md`。

只预览下一步，不写产物：

```bash
uv run simple-ar code-task execute runs/<run-id> --dry-run
```

完整 code-task 命令选项见 [CLI 参考](CLI_REFERENCE_zh.md#code-task-commands)。

## 8 阶段流程中的内嵌 Code Task

当你希望普通 research pipeline 在 `06-code` 阶段交给一个已有代码项目，并在 `08-report` 中包含代码实验结果时，使用这个模式。

推荐配置驱动：

```bash
uv run simple-ar run --config examples/run_configs/tiny_digits_mlp_pipeline.toml
```

等价的 split config 形式：

```bash
uv run simple-ar run \
  --topic "improve tiny digits MLP" \
  --to-stage report \
  --experiment-template code_task_project \
  --code-task-config examples/code_tasks/configs/tiny_digits_mlp.toml \
  --offline-search \
  --experiment-timeout 60
```

完全显式参数形式：

```bash
uv run simple-ar run \
  --topic "improve tiny digits MLP" \
  --to-stage report \
  --experiment-template code_task_project \
  --code-root examples/code_tasks/tiny_digits_mlp_project \
  --task-file examples/code_tasks/tasks/improve_tiny_digits_mlp.md \
  --benchmark-command "python benchmark.py" \
  --primary-metric accuracy \
  --metric-direction accuracy=higher \
  --metric-direction macro_f1=higher \
  --offline-search \
  --experiment-timeout 60
```

如果想让流程先研究再生成代码任务，可以省略 `--task-file`，但仍提供 code root 和 benchmark command：

```bash
uv run simple-ar run \
  --topic "research and improve the tiny digits MLP baseline" \
  --to-stage report \
  --experiment-template code_task_project \
  --code-root examples/code_tasks/tiny_digits_mlp_project \
  --benchmark-command "python benchmark.py" \
  --primary-metric accuracy \
  --metric-direction accuracy=higher \
  --offline-search \
  --experiment-timeout 60
```

这种模式下，`05-design` 会从前面研究阶段的产物和紧凑代码摘要中写出 `generated_code_task.md` 和 `generated_code_task_meta.json`，`06-code` 再把生成任务作为普通 `code_task/task.md` 输入。

`code_task_project` 会产生正常 pipeline run，同时在 `06-code/code_task_run/` 下产生嵌套 code-task 产物。`06-code` 会准备项目、探测环境、运行 baseline、生成 patch plan、记录自动 pipeline approval、请求受控 edits、应用补丁并验证。`07-run` 运行 patched benchmark，必要时写入 `comparison.json`，并把 code-task metrics 暴露到 `07-run/results.json`。`08-report` 会加入 deterministic Code Task Evidence 部分。

这个路径方便端到端实验，但会牺牲 standalone workflow 的人工暂停点。对安全敏感或难调试项目，建议先用 standalone `code-task execute` 或手动路径。

## 命令设计原则

CLI 保留 primitive commands 是因为项目仍然是学习实现。每一步都应该可检查、可测试、可审核。配置文件用于缩短很长的设置命令，而不是隐藏 approval gate、artifact path、validation result、baseline run 或 benchmark evidence。
