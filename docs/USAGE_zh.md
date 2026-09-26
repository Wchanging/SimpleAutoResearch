# 使用与配置

[English version](USAGE.md)

本文说明如何安装、配置和运行 SimpleAutoResearch。它是面向用户的实践指南；工作流概念和产物结构见 [工作流与产物](WORKFLOWS_zh.md)，完整命令表见 [CLI 参考](CLI_REFERENCE_zh.md)，TOML 字段见 [配置参考](CONFIG_REFERENCE_zh.md)。

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
SIMPLE_AR_LLM_BACKEND=openai
SIMPLE_AR_LLM_API=responses
SIMPLE_AR_LLM_STREAM=false
SIMPLE_AR_CHAT_TOKEN_LIMIT_PARAM=auto
SIMPLE_AR_LLM_REASONING_EFFORT=
SIMPLE_AR_LLM_REASONING_OUTPUT_TOKENS=
SIMPLE_AR_LLM_TIMEOUT_SEC=180
SIMPLE_AR_MAX_OUTPUT_TOKENS=
SIMPLE_AR_LLM_RETRY_ATTEMPTS=3
SIMPLE_AR_LLM_RETRY_BASE_DELAY_SEC=1
SIMPLE_AR_LLM_RETRY_MAX_DELAY_SEC=12
SIMPLE_AR_JSON_RESPONSE_FORMAT=off
SIMPLE_AR_INPUT_PRICE_PER_1M=
SIMPLE_AR_OUTPUT_PRICE_PER_1M=
```

说明：

- `OPENAI_API_KEY` 是 LLM 模式必需项。
- `OPENAI_BASE_URL` 可以指向 OpenAI，也可以指向第三方 OpenAI 兼容 `/v1` 接口。
- `SIMPLE_AR_MODEL` 是没有传入 `--model` 时的默认模型。
- `SIMPLE_AR_LLM_BACKEND` 控制传输实现。默认 `openai` 使用 OpenAI Python SDK 直连；`litellm` 保留旧的 LiteLLM 兼容层。
- `SIMPLE_AR_LLM_API` 控制请求形态。`responses` 会发送 Responses API 风格的 `instructions` 和 `input`，临时错误只在同一接口内有限重试；`chat` 会直接发送 Chat Completions 风格的 `messages`。已有的 `auto` 模式才会在 Responses 重试后再尝试 Chat，用于兼容只暴露其中一种接口的网关。
- `SIMPLE_AR_LLM_STREAM=true` 在 `SIMPLE_AR_LLM_API=chat` 时启用 Chat Completions
  流式传输；客户端会在解析前拼接 chunks，服务商提供最终 usage 时仍会记录它。Responses
  调用保持非流式。流式可以减少兼容网关的长时间非流式连接卡顿，但不会取消服务商或客户端超时。
- `SIMPLE_AR_CHAT_TOKEN_LIMIT_PARAM` 可选地指定 Chat Completions 的输出参数名：`max_tokens` 或 `max_completion_tokens`；`auto` 会在可能时根据模型名选择。
- `SIMPLE_AR_LLM_REASONING_EFFORT` 是可选的、由模型文档定义的推理强度，例如 `low` 或 `high`，只会通过 Chat Completions 的 provider 扩展字段转发。`SIMPLE_AR_LLM_REASONING_OUTPUT_TOKENS` 可以在调用方设置了输出上限时为推理过程扩大传输上限；调用方未设置上限时不会凭空增加新的上限。
- `SIMPLE_AR_LLM_TIMEOUT_SEC` 默认是每次 provider 尝试 180 秒；慢速服务商可以设置更大的正数，只有明确设为 `0` / `off` / `none` / `unlimited` 才不向 provider 传客户端超时。
- `SIMPLE_AR_MAX_OUTPUT_TOKENS` 是可选项；留空或设为 `0` / `off` / `none` / `unlimited` 时，不向 provider 传输出上限。只有你确实想限制模型输出长度时才设置正数。
- `SIMPLE_AR_LLM_RETRY_ATTEMPTS` 和 retry delay 设置控制临时 provider 错误的有限指数退避重试，例如连接中断、限流、超时、5xx 响应和 Cloudflare 524 origin timeout。
- 在线 pipeline 阶段在这些重试耗尽后默认失败，并保留可恢复状态。只有明确设置
  `[llm].allow_fallback = true` 才会写入 deterministic fallback；`--no-llm` 仍然是清晰的离线路径。
- `SIMPLE_AR_JSON_RESPONSE_FORMAT` 控制结构化 JSON 调用是否使用 provider 原生格式。默认 `off` 表示只靠 prompt 和本地解析，兼容性最好；`auto` 会尝试发送 `response_format={"type":"json_object"}`，仅在接口明确不支持时退回普通提示；`json_object` 表示强制发送。
- 价格字段只影响 usage summary 中的费用估算；不填也会记录 token。

## V2.8 Research Session：从主题到报告

V2.8 的正式用户入口是 `research-session`。它在同一个 session 中按固定顺序执行
`plan -> search -> document_ingest -> read -> synthesize -> research_design -> experiment
-> analysis`，在模型可用且未关闭报告时继续完成 `report -> report_audit`。实验命令、baseline、
数据集、代码范围和资源限制仍由用户或配置明确提供；它是有界闭环，不是无限自主研究循环。

如果只需要文献研究，可以同时省略实验命令和 `--code-task-config`。不提供模型时，session
会在有证据支持的 summary 处完成；提供模型时，可以继续进入 research-only 报告路径。这种
形态不会创建实验进程或 execution 预算。

适合笔记本的离线完整 smoke：

```bash
uv run python scripts/research_session_smoke.py
```

真实网络、LLM 和准备好的代码项目的低资源示例见 `examples/README.md`。遇到失败时，保留
session 目录并检查具体 attempt；不要用 fixture 结果覆盖真实失败。


## Retrieval 和 Artifact 工具

用于检查或搜索某次 run 产生的文件：

```bash
uv run simple-ar inspect runs/<run-id>
uv run simple-ar search-artifacts runs/<run-id> "accuracy"
```

参数细节见 [CLI 参考](CLI_REFERENCE_zh.md#artifact-tools)。

## Tool 和外部 Agent Handoff 预览

V2.6 新增内部 common tool 与 agent-handoff 层。它是未来 Codex、Claude Code、
OpenCode、OpenAI tool calling 或 MCP adapter 的受控扩展点，不是新的默认运行路径。

当前行为：

- 注册的 tool 必须是真实可用的本地 report / experiment tool，不添加空壳 MCP stub；
- 可以导出 OpenAI-style 和 MCP-style tool schema；
- 默认权限策略是 read-only / plan-only；
- 外部 agent handoff package 写到 `runs/<run-id>/agent_handoff/<name>/`；
- backend 输出如果被收集，会进入 `runs/<run-id>/agent_outputs/<name>/`，
  仍必须经过 SimpleAutoResearch validation，才能影响 patch、result 或 report。

handoff package 会显式列出上下文、权限和期望输出：

```text
runs/<run-id>/agent_handoff/<name>/
  instructions.md           # task、backend profile、permission summary
  tool_schema.json          # 真实 tool schema
  permission_policy.json    # write/shell/network/secret policy
  artifact_handles.json     # 暴露给 backend 的 run artifacts
  expected_outputs.json     # backend 可产出的 canonical files
  workspace_manifest.json   # 紧凑 run/workspace 视图
  context/
```

外部工具仍然只是可选 strong path adapter。本地 research、report、greenfield
experiment 和 code-task workflow 不需要 Codex、Claude Code、OpenCode 或 MCP server
也能继续运行。

V2.6 也在这个边界后面接入了可运行 backend：

- `fake`：deterministic dry-run backend，用于集成测试；
- `local_llm`：用当前 LLM 生成有边界的 review 产物；
- `codex`、`claude_code`、`opencode`、`external_cli`：可选 CLI backend。

如果要试 agent-backed greenfield generation 或 repair，可以设置
`[implementation].provider`。外部 CLI provider 还必须显式设置
`[implementation].allow_external_agent = true`。如果 executable 不在 `PATH`，或者需要
provider-specific flags，可以使用 `[implementation].agent_binary`、`.agent_args` 和
`.agent_timeout_sec`。即使开启，backend 也只能把候选文件
写到 handoff 目录；SimpleAutoResearch 会再复制到 run workspace，并继续执行原有的
code review、result guard、benchmark 或 code-task validation gate。

`[implementation].agent_mode` 是这一层唯一新增的模式开关：

- `model`：SimpleAutoResearch 仍然拥有 harness，只把有界生成交给本地/模型 backend。
- `handoff`：为 Codex、Claude Code、OpenCode 或其他外部 CLI 写出可审计 handoff package，
  再把 candidate files 收回 SimpleAutoResearch 的 gate。
- `delegated_workspace`：预留给未来“外部 harness 接管 workspace loop”的强路径。当前版本会识别
  这个值，但执行时会显式失败，不会静默降级。

run-local 只读 tools 也可以通过 MCP stdio 暴露：

```bash
uv run simple-ar tools schema --format mcp
uv run simple-ar tools call runs/<run-id> list_experiment_artifacts
uv run simple-ar tools serve-mcp runs/<run-id>
```

配置 Codex/MCP 集成时，使用
`[implementation].provider = "codex"`，并默认保持 `[implementation].agent_model = ""`，
让 Codex CLI 使用当前账号配置的默认模型。只有确认 CLI/账号支持某个模型名时，
再显式填写 `agent_model`。

## Code Task 工作流

Code Task 会把代码任务准备到一个隔离的可编辑 workspace 中，后续所有补丁或生成都只发生在这个 workspace，不修改原始项目。已有项目默认 `auto` 模式：优先为已有 commit 的 Git 项目创建 `git_worktree`，如果 Git 条件不满足则降级为受保护的 `copy`，并在 manifest 和终端输出中记录原因与下一步建议。也可以显式使用 `copy`、`git_worktree`，以及适合小型 allowlist 子集的实验性 `sparse_copy`。从零生成项目时使用 `kind = "greenfield"`，默认从 `empty` workspace 开始，并把生成项目写到 `code_task/workspace/generated_project/`。

推荐先从 TOML 配置初始化，把项目路径、benchmark 指标、workspace 模式、模型路由和编辑预算都放在一个可审核文件里。内置 standalone 示例使用 medium review pipeline：

```bash
uv run simple-ar code-task init --config examples/code_task_medium_review/configs/code_task.toml
```

这个示例会运行 `python main.py --config configs/experiment.json --show-progress`，baseline / patched run 中会打印逐轮进度行，并通过 `[execute].stream_benchmark_output = "auto"` 让 `code-task execute` 在保存 stdout/stderr 产物的同时，把 benchmark 进度转发到命令行。`auto` 模式同时兼容普通 `print` 日志和 `tqdm` 这类 carriage-return 进度输出。

`init` 会创建一个新的 run 目录，核心结构如下：

```text
runs/<run-id>/
  manifest.json                 # benchmark、workspace、environment、safety policy
  code_task/
    task.md                     # 任务说明
    workspace/                  # 隔离 copy/worktree 根目录
      ...                       # monorepo 场景下 project root 可能是其中的子目录
    meta/
      codebase_index.json       # 文件级代码索引
      repo_map.json             # 分层 repo/symbol map
      repo_map_summary.md       # 给人看的 repo-map 摘要
```

它不会运行代码、不会调用 LLM，也不会修改原始项目。

如果是 standalone 从零生成项目，也使用同一套 code-task 命令，只是不需要 `code_root`：

```bash
uv run simple-ar code-task init --kind greenfield --task-file task.md --benchmark-command "python generated_project/main.py"
uv run simple-ar code-task execute runs/<run-id> --to-step run
```

这种模式会复用 code-task 的 memory、reviewer、validation、runner 和 repair 产物，只是实现步骤不再应用 patch，而是在隔离 workspace 内生成 `generated_project/`。

在规划 greenfield 实现前，`execute` 会写出 `code_task/meta/dependency_advice.json`
和 `.md`。它会扫描当前 Python 环境，把完整 installed-package snapshot 写入 JSON，
并在终端只展示和任务相关的可用库子集。内置 dependency catalog 只是语义提示，不是
白名单，因此服务器上额外安装的任务库也可以进入规划上下文。这只是建议，不会自动安装
依赖或修改环境；如果你希望模型走更强实现路径，可以按提示先手动执行 `uv add ...`，
然后重跑 execute。

如果省略或显式使用 `workspace.mode = "auto"`，已有项目会先尝试 detached git worktree。如果 Git 不可用、不在仓库内、仓库还没有 commit，或 worktree 无法安全创建，本次 run 会降级为受保护的 copy，并在 `manifest.json.workspace` 写入 `requested_mode`、`selected_mode`、`fallback_reason` 和 `user_next_steps`。

如果使用 `workspace.mode = "git_worktree"` 或 `--workspace-mode git_worktree`，`init` 会在 `code_task/workspace/` 创建 detached git worktree，而不是完整复制文件。这个模式要求 `code_root` 位于本地 Git 仓库中，并且仓库至少有一次 commit；`code_root` 可以是仓库根目录，也可以是 monorepo 中的项目子目录。子目录场景下，系统会在仓库根创建 worktree，并把 worktree 中对应子目录作为实际可编辑 project root，用于索引、修改和 benchmark 执行。如果目录不满足要求，CLI 会给出可操作提示，比如初始化 git、提交初始 baseline、传入正确项目路径，或者改用 `copy` 模式包含当前未提交文件状态。

如果使用 `workspace.mode = "sparse_copy"` 或 `--workspace-mode sparse_copy`，只会复制匹配 include pattern 的文件，同时始终排除 `.git`、virtualenv、`runs`、cache/build、`data`、`models`、`.env` 和 secret-like 路径。这个模式适合你明确知道需要哪些文件的小型实验；通用项目优先使用 `auto`，或者按需求显式选择 `git_worktree` / `copy`。

benchmark 最好输出稳定的数值指标行。当前支持 `name: value` 和 `METRIC name=value`；
后者更适合 generated project 或外部 agent 项目，因为它明显是 machine-readable 输出。
自定义指标推荐在 TOML 中声明解释方向。显式 CLI 参数仍然支持，适合临时实验和快速测试，
但公开使用路径建议优先用 TOML。完整参数表见
[CLI 参考](CLI_REFERENCE_zh.md#simple-ar-code-task-init)，配置 schema 见
[配置参考](CONFIG_REFERENCE_zh.md#standalone-code-task-config)。

已有项目任务可以用 `[execute].baseline_policy` 控制是否先跑未修改 baseline。
默认 `auto` 会在需要比较证据时运行 baseline；baseline 很贵、或任务只是验收式目标时，
可以设为 `skip` 或 `none`；如果你已经有可信 baseline 指标，可以设为 `provided`，
并通过 `baseline_metrics_file` 提供 JSON 或 `metric=0.82` 文本文件。provided baseline
会在 summary 中标注为用户提供指标，不会伪装成本次复测结果。

### 推荐路径：TOML + Execute

普通已有项目默认只走 patch plan、批准、改码和验证，不自动生成 work plan 或批次状态。
需要任务分解时显式使用 `execute --to-step work-plan` 或 `--to-step batch`；已有 work plan
也会保留分批流程。交互执行遵循同一规则。

正常使用时，推荐把项目路径、benchmark、指标方向、模型路由和预算放进 TOML，然后用 `code-task execute` 推进。这样命令更短，但仍然保留 patch plan 和 edit proposal 两个审核点。下面示例使用 medium review pipeline 配置，覆盖多文件修改、项目配置变更、指标解析和可见 benchmark 进度。

1. 初始化 run：

```bash
uv run simple-ar code-task init --config examples/code_task_medium_review/configs/code_task.toml
```

这个命令会打印一个 run 目录，例如 `runs/20260523-xxxx-medium-review-pipeline`。下面命令中的 `runs/<run-id>` 都替换成这个实际路径即可。

`init` 会写入隔离 workspace 和静态项目地图：

```text
runs/<run-id>/
  manifest.json
  code_task/
    task.md
    workspace/
    meta/
      codebase_index.json
      repo_map.json
      repo_map_summary.md
```

`workspace/` 是唯一可编辑副本或 worktree，`task.md` 是任务说明，`meta/`
里是初始代码地图，`manifest.json` 记录 benchmark、workspace、environment
和 safety policy。

> Tip：medium review pipeline 会运行 `python main.py --config configs/experiment.json --show-progress`。执行时可以看到类似 `benchmark stdout: round 1/4 ...` 的转发行，同时完整 stdout 仍会保存到 `code_task/run/<label>/stdout.txt`。

> Note：medium 任务通常会联动 feature extraction、model scoring 和 config。示例 edit scope 允许修改 `configs/experiment.json`，因为新实现的 feature family 需要在配置中启用，benchmark 才能测到它。因此它可能生成一个已审核的 `large` batch。只有在检查 `code_task/meta/proposed_edits.json` 后，最后应用 proposal 时才应加入 `--allow-large-edits`。

2. 运行状态感知 executor：

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml
```

在真实终端里，这一条命令可以一路经过 plan 审核、proposal 审核、应用补丁、结构化 review、验证和
patched benchmark；每个真实审核门都会用黄色 Rich 面板提示你看什么、下一步会做什么。
如果在非交互 shell 中运行，或者你回答 `no`，它会停在当前审核门，方便你之后重跑。
第一个审核门通常会生成：

```text
code_task/
  work_plan.md
  patch_plan.md
  meta/
    environment_report.json
    review_report.json
  attempts/
    attempt-001/
      batches/
        batch-001/
          batch_state.json
  run/
    baseline/
      metrics.json
```

这时原始项目仍未被修改，workspace 也还没有应用模型 edits。

`execute` 会用 Rich 显示步骤状态，并默认连续运行到真正需要人工判断的审核门。
真实终端里的审核门会 inline 询问是否继续；非交互 shell 中会干净停住，除非显式传入
`--yes`。中途中断后，重新运行同一条 `code-task execute` 命令即可：已完成步骤会被检测并显示为
skipped，然后 workflow 从下一个需要处理的位置继续。只有在调试 primitive 步骤时才建议加
`--interactive` 逐步确认；`--yes` 会自动继续这些 interactive primitive prompts，
并且在普通 execute 模式下也会自动批准 inline 审核门。只有明确想自动审批 plan/proposal 时才使用它。使用 `--no-review-inline` 可以恢复“停住、下次再跑”的行为。

如果 LLM work-plan 或 patch-plan 返回了无法解析的 JSON，`execute` 会停在
`llm_planning_failed`，并且默认不会写入 offline fallback plan。此时直接重跑同一条
命令即可重新尝试模型调用；如果你明确想完全离线规划，使用 `--no-llm`；如果你希望
先尝试 LLM、失败后接受较弱的 deterministic fallback，再使用
`--allow-planning-fallback`。

补丁应用后，`execute` 会在静态验证前写入 `code_task/meta/review_report.json`；
patched benchmark 完成后还会写入 `code_task/meta/review_report_post_run.json`。
阻塞性发现会同步记录到 `code_task/memory/`，后续 repair prompt 可以直接利用这些
最新失败证据。
结构化 review 还会写入 `code_task/meta/review_index*.json` 和
`code_task/meta/review_clusters*.json`：前者是完整项目索引，后者是分层审查时实际给
reviewer 的语义文件组。Greenfield planning 额外写入
`code_task/meta/planning/agent_steps.jsonl`，用于排查 requirements / architecture /
interfaces / file-plan / planning-review 哪一步失败或反复回修。

对于 greenfield run，同一套 review gate 会在验证前检查生成项目。如果 review 发现
通用可修复问题，例如核心文件仍是 fallback、缺少 artifact writer、缺少本地 API 等，
`execute` 可以用有限轮次的 LLM repair 优先生成结构化局部修复 action，例如唯一
old/new 文本替换或函数级替换；只有文件职责整体错误时才回退到整文件替换。修复会记录
edit application、重新同步 `code_task/meta/code_artifacts.json`，再跑一次 review。若问题仍然阻塞，run 会停住并
保留当前生成产物和 review 报告，方便人工接管。

3. 在 patch-plan 审核面板出现时，阅读 `code_task/work_plan.md` 和
`code_task/patch_plan.md`。如果计划合理，输入 `yes` 继续。如果你在非交互环境运行、
回答了 `no`，或使用了 `--no-review-inline`，则可以显式批准：

```bash
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve --note "reviewed"
```

4. 如果第一条 executor 命令没有已经继续到 proposal，则继续生成 edit proposal。
在 proposal 审核面板出现前，不会应用补丁：

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml --to-step propose-edits
```

重点审核：

- `code_task/meta/proposed_edits.json`：受控 old/new replacement。
- `code_task/meta/llm_usage_summary.json`：LLM token 用量摘要。
- 最新 `code_task/attempts/.../proposal_warnings.json`，如果存在。

受控编辑的 `controlled_patch` 来源 metadata 会记录在
`proposed_edits.json`、active batch state、`applied_edits.json` 和
`manifest.json.patch` 中。提案/应用函数不负责运行 benchmark、批准计划或写报告；这些 gate 仍由 code-task workflow 管理。

5. 在 proposal 审核面板中检查 edits。确认无误后输入 `yes`，即可应用补丁并运行验证和
patched benchmark。如果你在非交互环境运行、回答了 `no`，或使用了 `--no-review-inline`，
则显式应用：

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml --apply-proposed-edits --timeout 60
```

6. 查看整体状态：

```bash
uv run simple-ar status runs/<run-id>
```

关键结果文件：

```text
code_task/
  summary.md
  patch.diff
  meta/
    applied_edits.json
    validation_report.json
  run/
    patched/
      metrics.json
    comparison.json
```

`patch.diff` 和 `applied_edits.json` 说明改了什么，`validation_report.json`
说明静态检查结果，`metrics.json` 是 patched run 指标，`comparison.json`
是 baseline-vs-patched 的目标判断。

正常成功信号是 `objective_improved` 或 `objective.status = "improved"`。patched benchmark 通过并不等于任务目标一定完成；如果 objective 是 `regressed` 或 `mixed`，说明代码能跑，但指标目标没有真正达成。

7. 如果需要修复，先请求一个有限范围的 repair proposal：

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml --to-step repair --repair-rounds 1 --timeout 60
```

审核最新的 `code_task/repairs/repair-NNN/proposed_edits.json`，再显式应用：

```bash
uv run simple-ar code-task apply-edits runs/<run-id> --edits-file runs/<run-id>/code_task/repairs/repair-NNN/proposed_edits.json
uv run simple-ar code-task validate runs/<run-id>
uv run simple-ar code-task run runs/<run-id> --timeout 60
uv run simple-ar status runs/<run-id>
```

如果只想预览下一步，不写任何产物：

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml --dry-run
```

### 可选的代码地图和上下文工具

任何时候都可以刷新代码地图：

```bash
uv run simple-ar code-task map runs/<run-id>
```

`map` 会扫描当前 workspace，并刷新静态代码地图产物：

```text
code_task/
  workspace/                  # 被扫描的源码树
  meta/
    codebase_index.json       # 文件级代码索引
    repo_map.json             # 分层 repo/symbol map
    repo_map_summary.md       # 给人看的摘要
manifest.json                 # 更新 map/workspace metadata
```

它不会调用 LLM、不会安装依赖、不会运行 benchmark，也不会修改原始项目。

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

### 手动 Primitive 路径

上面的 executor 会按状态自动调用这些 primitive command。只有在学习内部机制、调试某一步，或者刻意组装自定义流程时，才建议手动逐步运行。

先探测环境并运行未修改 baseline：

```bash
uv run simple-ar code-task map runs/<run-id>
uv run simple-ar code-task locate runs/<run-id>
uv run simple-ar code-task context runs/<run-id>
uv run simple-ar code-task probe runs/<run-id>
uv run simple-ar code-task baseline runs/<run-id> --timeout 60
uv run simple-ar code-task work-plan runs/<run-id>
uv run simple-ar code-task batch runs/<run-id> --work-item W1
```

`probe` 写入 `code_task/meta/environment_report.json`，包含 OS、Python、工具、GPU、依赖文件和 test 目录信号。同时还会写出 `resource_probe.json` 和 `resource_decision.json`，给 greenfield 和外部 agent 路径提供紧凑硬件画像。它不安装依赖，也不运行项目代码。

`baseline` 在任何补丁应用前运行记录的 benchmark command，结果存到 `code_task/run/baseline/`，包括 `execution_report.json`、`stdout.txt`、`stderr.txt` 和解析后的 `metrics.json`，并刷新 `code_task/summary.md`。

如果任务比较宽泛，或者可能需要多批次修改，可以先生成更高一层的 work plan：

```bash
uv run simple-ar code-task work-plan runs/<run-id>
uv run simple-ar code-task batch runs/<run-id> --work-item W1
```

`work-plan` 写入 `code_task/work_plan.json` 和 `code_task/work_plan.md`，
记录 work items、target files、read-only evidence、validation hints、
context requests 和 budget profiles。它不生成代码，也不修改文件。`batch`
会在 `code_task/attempts/attempt-NNN/batches/batch-NNN/` 下创建持久状态，
这是后续做多轮、分批编辑和失败恢复的基础。active batch 存在时，
edit proposal 会被限制在该 batch 的 target files 内，并写入额外的批次级
review 产物。

Work-plan item 应该是可以产生代码修改的 implementation batch，而不是单独的分析笔记。现在 prompt 会要求模型把“还需要看什么”放进 `context_request`；如果模型仍然把第一个 item 写成纯 `inspect/review/measure` 之类的分析步骤，`code-task execute` 会优先选择后面第一个真正像代码修改的 item，避免把“先了解项目”误当成 active edit batch。

如果模型把一个必须联动落地的实现拆成串行依赖链，比如 feature extraction -> scorer wiring -> config enablement，`batch` 会把这个小链条合并成一个执行批次。`work_plan.md` 仍然保留分开的审核项；实际执行范围记录在 `batch_state.json.work_item.source_work_item_ids` 和合并后的 `target_files` 中。由于这种批次可能触碰两个以上文件，通常会升级为 `large` budget profile，应用前仍需要人工审核并显式使用 `--allow-large-edits`。

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
proposal 也会记录 `editor.backend = "controlled_patch"`，方便后续接入其他 backend 后仍能按同一产物形态审计。
预留的 `external_agent` backend 在当前版本不能执行。它只会为未来 Codex / Claude / OpenCode adapter 构建可审查 invocation plan，其中包含 provider、command preview、blocked read patterns、timeout、network/shell permissions、log path 和 diff path。未来外部 agent 的结果也必须先变成 captured diff/proposal，再经过 SimpleAutoResearch 的 validation、benchmark 和 summary。
执行器还会在模型返回 JSON 后执行本地编辑预算检查。超预算 proposal 会写入 warnings 和 rejected edits，而不是直接应用；如果 proposal 仍在 larger review budget 内，审核 JSON 后再显式使用 `--allow-large-edits`。

应用已审核 edits：

```bash
uv run simple-ar code-task apply-edits runs/<run-id>
```

`apply-edits` 只修改 `code_task/workspace/`，写入 `code_task/patch.diff` 和 `code_task/meta/applied_edits.json`，并重建 codebase index。下一次 `execute` 会先运行结构化 reviewer，写入 `code_task/meta/review_report.json`，再进入静态验证。如果 edit 无法唯一匹配，会在写文件前停止。
`applied_edits.json` 会记录实际应用的 proposal path 和 editor backend，包括手动提供的 edits file 或 repair proposal。

验证并运行 patched benchmark：

```bash
uv run simple-ar code-task validate runs/<run-id>
uv run simple-ar code-task run runs/<run-id> --timeout 60
```

`validate` 写入 `code_task/meta/validation_report.json`，包含语法错误、危险 import/call、缺失 import warning 和文件尺寸 warning。它是静态检查，不运行 benchmark。

`run` 把 patched benchmark 存到 `code_task/run/patched/`。当 baseline 和 patched 都存在时，还会写入 `code_task/run/comparison.json`，并在 `summary.md` 中加入前后对比和下一步建议。

patched benchmark 通过不等于任务目标一定完成。现在系统会把“代码可运行”和“指标目标是否达成”分开：如果 patched benchmark 通过，但相对 baseline 指标退化，`manifest.json` 会写入 `objective.status = "regressed"`，`simple-ar status` 会显示 Objective，`summary.md` 也会引导你查看 `code_task/run/comparison.json`，而不是把它当作真正成功。

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

应用 repair proposal 后，`manifest.json.patch.latest_applied_proposal` 和 `code_task/meta/applied_edits.json` 会记录实际应用的是哪一份 repair proposal。后续 patched benchmark 通过后，旧的 failure-analysis 和 repair section 会被标记为 resolved，`status` 和 `summary.md` 会反映当前状态，而不是继续展示早前失败的尝试。

### Code Task 运行排错

`execute` 后没有生成 `proposed_edits.json`：

- 这是第一次 executor 调用后的正常情况。fresh run 会先停在 `approval_required`，此时应该已经有 `code_task/patch_plan.md`。
- 先审核并批准计划，再明确推进到 proposal：

```bash
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve --note "reviewed"
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml --to-step propose-edits
```

- 可检查 `manifest.json` 中的 `plan.status` 是否为 `approved`；人工决策记录在 `code_task/meta/hitl_decisions.jsonl`。

`execute` 停在 `llm_planning_failed`：

- 这表示模型在 work-plan 或 patch-plan 阶段返回了格式错误/缺失的结构化 JSON。默认情况下不会写入 deterministic fallback plan，避免把弱计划伪装成 LLM 计划继续执行。
- 直接重跑同一条 `code-task execute ... --config ...` 命令即可从同一位置重试；已完成的前置步骤会显示为 skipped。
- 如果你想完全跳过 LLM planning，重跑时加 `--no-llm`。如果你仍想先试 LLM，但失败后接受 deterministic fallback，可以加 `--allow-planning-fallback`，或在 TOML 里设置 `[execute].allow_planning_fallback = true`。

validation 通过，但 patched benchmark 失败：

- 这说明代码语法和静态规则没问题，但行为或指标变差了。优先查看：

```bash
code_task/run/patched/execution_report.json
code_task/run/patched/stdout.txt
code_task/run/patched/stderr.txt
code_task/run/comparison.json
code_task/summary.md
```

- 请求一个有限范围的修复 proposal：

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml --to-step repair --repair-rounds 1 --timeout 60
```

- 审核最新的 `code_task/repairs/repair-NNN/proposed_edits.json`，再显式应用、验证和重跑：

```bash
uv run simple-ar code-task apply-edits runs/<run-id> --edits-file runs/<run-id>/code_task/repairs/repair-NNN/proposed_edits.json
uv run simple-ar code-task validate runs/<run-id>
uv run simple-ar code-task run runs/<run-id> --timeout 60
```

- 修复后 benchmark pass 不等于任务真正完成。比如修复可能只是恢复到可运行或接近 baseline；是否真的 improved 要看 `code_task/run/comparison.json`、`manifest.json.objective.status` 和 `simple-ar status`。

patched benchmark 通过，但 objective 是 `regressed` 或 `mixed`：

- 这不是运行失败，而是说明补丁没有在记录的 baseline 指标上完成目标。
- 先看 `code_task/run/comparison.json`，其中包含指标 delta、方向、主指标和保守 verdict。
- 把它当成质量失败处理：可以修改任务/计划，重新生成更聚焦的 proposal；只有当 comparison 提供了足够明确的证据时，再请求有限范围的 repair。

`apply-edits` 报 `old text was not found`：

- 发生这种情况时，workspace 文件不会被修改。原因通常是 proposal 里的 `old` 不是当前文件中的精确原文，或者模型把 unified diff 的 `+` / `-` / `@@` 等标记写进了结构化 JSON。
- 重新生成 proposal，或手工修正 JSON。每个 edit 的 `old` 必须是当前文件里唯一匹配的连续文本，`new` 是替换后的文件文本；不要在 `old` / `new` 里写 `+`、`-`、`@@`、`---`、`+++` 这类 diff 标记。
- 如果同一文件的多个 edit 位置很近，最好合并成一个更大的 old/new replacement，避免前一个 edit 让后一个 edit 的 `old` 失效。

提示需要 large-edit approval：

- 先阅读 `code_task/meta/proposed_edits.json`，以及 `code_task/meta/` 或最新 `code_task/attempts/.../batch-NNN/` 下的 `proposal_warnings.json`。
- 如果确认这是必要的大修改，再加 `--allow-large-edits`。不要只为了绕过模型输出异常而使用这个参数。

Proposal 只覆盖了联动计划的第一部分：

- 先检查 `code_task/work_plan.md` 和最新的 `code_task/attempts/.../batch_state.json`。如果计划是 feature -> model -> config 这类串行联动，active batch 应该在 `work_item.source_work_item_ids` 中列出合并的 item id，并在 `work_item.target_files` 中列出所有允许修改的文件。
- 对于旧 run，如果 batch 是在这次行为更新前创建的，可以重新创建批次并重新生成 proposal：`uv run simple-ar code-task batch runs/<run-id> --work-item W1 --force`，然后 `uv run simple-ar code-task propose-edits runs/<run-id> --force`。
- 如果合并后的 batch 被标记为 `large`，先审核完整 proposal，再决定是否使用 `--allow-large-edits`。

`uv run` 出现本地 cache 权限错误：

- 这是本机环境问题，不是 run artifact 问题。可以修复 uv cache 权限，或者在 PowerShell 中直接使用项目虚拟环境入口，例如 `.\.venv\Scripts\simple-ar.exe ...`。


## 命令设计原则

CLI 保留 primitive commands 是因为项目仍然是学习实现。每一步都应该可检查、可测试、可审核。配置文件用于缩短很长的设置命令，而不是隐藏 approval gate、artifact path、validation result、baseline run 或 benchmark evidence。
