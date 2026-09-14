# 配置参考

[English version](CONFIG_REFERENCE.md)

研究任务使用 `simple-ar research-session --config PATH`。先复制
[轻量模板](../examples/research_config/minimal.toml)，需要更多控制时参考
[完整示例](../examples/research_config/advanced.toml)。两者是同一格式、同一默认值，不是两套运行模式。
CodeTask 专用选项仍通过下方 CodeTask TOML 复用。
旧八阶段外层配置解析器及别名转换已经退出，历史快照仍可读取，但不是可执行工作流。

## 加载规则

- 优先级：内置默认 → 研究 TOML → 显式 CLI 覆盖。CLI 列表覆盖整份文件列表。
- 研究文件中的相对路径以 TOML 所在目录为基准；命令 argv 原样传给实验进程。
- `[task]`：`goal`、`outputs`（summary/report/experiments）、`output_root`。
- `[model]`：`name`（`env` 使用 `.env` 的 SIMPLE_AR_MODEL）、`max_output_tokens`。
  配置文件省略模型时默认使用环境模型；显式 `name = ""` 用于不调用模型的确定性摘要。
  API key/base URL 继续来自环境，禁止在模板写密钥。
- `[budget]`：`total_tokens`、`llm_requests`、`process_invocations`、`process_wall_seconds`。
  总 token 与单次输出上限不是同一个限制。配置预算用于新会话；恢复继续使用已有账本。
- `[research]`：`providers`、`queries`、`max_results`、`max_chunks`、`idea_limit`、`cache_dir`。
- `[assets].papers`：本地文献路径列表。
- `[execution]`：`command`（字符串数组）、`cwd`、`timeout_sec`，或 `code_task_config`；
  可附 `primary_metric`、`metrics`、`metric_directions`（如 `["accuracy=higher"]）。
- 高级实验可用 `[[execution.pairs]]`：每行包含唯一整数 `seed` 与显式 argv 数组
  `baseline_command`、`candidate_command`，不与单个 command 混用。引用 CodeTask 时，
  这些命令用于研究矩阵，baseline_policy 必须为 auto/run；改码范围仍由 CodeTask 配置负责。
  `[execution.protocol]` 直接使用已有实验契约的 dataset_refs、split_spec、metric_specs、
  comparison_conditions、protected_assets，可附 contract_id/hypothesis。
  保护文件相对路径以实验 cwd 为基准，共享数据可用绝对路径；不是以 TOML 目录为基准。
  不提供种子插值或新调度器；进程预算须覆盖整个矩阵。
- `[report]`：`template`、`reviewer`、`max_review_iterations`。
- 显式 outputs 与 --with-report/--no-report 二选一。只调研不会因配置了材料而训练。
  请求 experiments 但没有执行配置时保留该目标，并在实验处报告准备缺口；自动仓库准备尚未实现。
- 未实现字段和拼写错误显式报错。暂不接受研究阶段模型或多模型协作配置。
- 生效预算、研究参数和输入保存在会话 runtime_config/brief 产物中；认证配置不写入。
  模型连接仍来自当前运行环境，不应将这些产物视为完整的连接配置快照。

- code-task init --config PATH 读取初始化配置。
- code-task execute --config PATH 读取执行、模型与预算配置。
- research-session --code-task-config PATH 使用同一个 CodeTask 解析器。
- 显式 CLI 参数覆盖对应 TOML 选项。
- 安装与命令说明：[使用手册](USAGE_zh.md)、[CLI参考](CLI_REFERENCE_zh.md)。

## CodeTask 字段参考

### 研究会话补齐条件与续接

```bash
simple-ar research-session --config research.toml --session-root runs/research-session/<session>
```

保持原目标和 outputs，在 TOML 中补充 `[execution]` 的 command/cwd/timeout_sec，
或引用已有 CodeTask 配置即可继续缺少实验条件的暂停会话。已有研究产物复用，
CodeTask task_file 中的补充实现要求会保留；不会重新检索或覆盖已有实验协议。
完整会话重复运行不会再次训练。若要修改已有实验条件，使用显式实验修订入口。

续接沿用已保存的研究/报告设置和预算，只有模型连接来自本次环境。
修改配置中的预算不会增加原会话额度；计划稍后实验时，应在首次启动就填写允许的
process_invocations/process_wall_seconds。自动寻找、下载并配置仓库仍不是已实现能力。

研究准备复用 CodeTask 的 auto/copy/git_worktree 选项及自定义保护路径。
auto 对干净仓库使用 worktree；有未提交源码或无法创建 worktree 时使用 copy 并记录原因。
显式 git_worktree 使用已提交 HEAD，不包含未提交修改。准备产物记录 Git 版本和隔离位置；
这不是每轮候选自动提交 Git。sparse_copy/empty 仍限独立 CodeTask 使用。

### Code-Task 字段

| 字段 | 含义 |
| --- | --- |
| `[code_task].kind` | `existing_project` 表示已有源码项目 patch；`greenfield` 从 empty workspace 开始，并在 `code_task/workspace/generated_project` 下生成项目。 |
| `[code_task].code_root` | `existing_project` 的源项目路径；`greenfield` 仅在需要 scaffold/source root 时填写。原始项目不会被直接修改。 |
| `[code_task].task_file` | 独立 CodeTask 的任务描述；research-session 通过正式应用准备 implementation handoff。 |
| `[benchmark].command` | 在 `code_task/workspace` 中 patch 前后运行的命令。建议输出 `accuracy: 0.82` 这类可解析指标。 |
| `[benchmark].primary_metric` | objective verdict 使用的主指标。未知指标仍会记录，但最好声明方向。 |
| `[benchmark.metric_directions]` | 指标方向表：`higher`、`lower`、`resource` 或 `ignore`。 |
| `[environment].mode` | `current` 使用当前 SimpleAutoResearch Python；`external` 使用 `[environment].python`。不会自动安装依赖。 |
| `[workspace].mode` | workspace 策略：`auto`、`copy`、`git_worktree` 或 `sparse_copy`。已有项目默认 `auto`，会优先尝试 git worktree，失败时降级为 copy 并记录原因。 |
| `[workspace].reuse_source_venv` | 检测到 source `.venv` 或 `venv` 时，是否记录并使用其中 Python。 |
| `[implementation].provider` | code-task 实现 backend。`local` 使用 SimpleAutoResearch 进程内路径；`fake` 用于确定性测试；`local_llm` 使用当前 LLM；`codex`、`claude_code`、`opencode` 和 `external_cli` 会在显式启用时走外部 agent handoff 边界。 |
| `[implementation].agent_mode` | backend 实现模式：`model` 表示 SimpleAutoResearch 仍拥有 harness；`handoff` 会从外部 agent package ingest candidate files；`delegated_workspace` 目前只识别并显式失败，等 snapshot/diff/rollback 执行边界完成后再开放。 |
| `[implementation].allow_external_agent` | 外部 CLI backend 启动前必须显式设为 true。外部输出仍需通过 SimpleAutoResearch 的 review、validation、benchmark 或 result guard 才能被接受。 |
| `[implementation].agent_model` / `.agent_binary` / `.agent_args` / `.agent_timeout_sec` | 可选外部 backend 启动设置。除非确认外部 CLI/账号支持某个模型名，否则建议让 `agent_model` 留空。 |
| `[implementation].max_repair_attempts` | implementation 侧修复尝试上限，也覆盖 greenfield validation 前的 review repair。 |
| `[resource].max_runtime_sec` | 暴露给 greenfield planning 和 external-agent handoff 的运行预算。 |
| `[resource].max_files` / `.max_generated_lines` | code-task 生成预算。`code-task execute` 会优先使用 `[execute].max_files` 和 `[execute].max_generated_lines`，缺省时再读取这里。 |
| `[resource].max_memory_mb` / `.allow_gpu` | 暴露给规划阶段的内存/GPU 约束，只描述允许的资源 profile，不会自动安装依赖。 |
| `[edit_scope].allowed_patterns` | automated edits 可以修改的 workspace-relative glob allowlist。空列表表示所有 normalized、非 protected workspace 路径都可编辑。 |
| `[edit_scope].protected_patterns` | 额外只读路径。tests、benchmark、`.env`、secret/credential-like 路径等默认 protected patterns 始终保留。 |
| `[edit_scope].mode` | 可选标签，写入 `manifest.json` 供审计使用；它本身不改变行为。 |
| `[safety].max_file_bytes` | copy/sparse 模式最大复制文件大小，避免误复制大模型、数据或 checkpoint。 |
| `[safety].validation_max_file_bytes` | 静态 validation 扫描文件大小上限。 |

### Execute 与 Budget 字段

| 字段 | 含义 |
| --- | --- |
| `[execute].to_step` | 状态感知 executor 最多推进到哪一步。例如设为 `propose-edits` 可停在应用补丁之前，设为 `review` 可停在应用补丁后的结构化 review 之后。 |
| `[execute].use_llm` | 是否启用 LLM 支持的 work-plan、patch-plan、edit-proposal 和 repair 步骤。 |
| `[execute].timeout_sec` | executor 管理的 baseline 和 patched benchmark timeout。省略时会依次回退到 `[benchmark].timeout` 和 `[resource].max_runtime_sec`。 |
| `[execute].stream_benchmark_output` | 实时 benchmark log 模式：`off`、`line`、`auto` 或 `summary`。有 tqdm 这类进度条时建议 `auto`。 |
| `[execute].baseline_policy` | 已有项目 baseline 策略：`auto`/`run` 会运行未修改 benchmark；`skip`/`none` 不做 baseline comparison；`provided` 从 `[execute].baseline_metrics_file` 记录用户已有指标。 |
| `[execute].baseline_metrics_file` | `baseline_policy = "provided"` 时使用的 JSON 或 `metric=0.82` 文本指标文件。JSON 支持 `{"accuracy": 0.8}`、`{"metrics": {...}}` 和 `{"metric_values": {...}}`。 |
| `[execute].apply_proposed_edits` | 允许 execute 应用已经审核过的 proposal。review-first 流程建议保持 false。 |
| `[execute].allow_large_edits` | 允许应用超过 normal 预算但落在 large 预算内的已审核 proposal。 |
| `[execute].allow_planning_fallback` | LLM work-plan / patch-plan / greenfield 架构规划 / greenfield 文件生成重试后仍失败时，是否允许 deterministic fallback。真实 LLM run 建议保持 false，这样坏输出会安全停止并可重跑。 |
| `[execute].planning_mode` | Greenfield 规划：`tool_agent` 分需求、架构、接口、文件计划及有限审阅，正常至少五次调用；`compact` 在重试前仅一次架构调用。两者共用文件生成和执行，调用数不代表研究质量。 |
| `[execute].planning_review_rounds` | standalone code-task 中 greenfield planning reviewer 可触发的最大回修轮数。默认 `2`；轻量 smoke example 可设为 `1`，大型服务器任务可按需调大。 |
| `[execute].llm_retry_attempts` | work-plan、patch-plan、greenfield 架构规划和 greenfield 文件生成的 LLM 尝试次数；全部失败后才停止或显式 fallback。 |
| `[execute].repair_rounds` | validation/benchmark 失败后最多生成几轮 bounded repair proposal；repair 仍需审核。 |
| `[execute].max_files` | plan/proposal/repair 步骤纳入 LLM 上下文的最大文件数。 |
| `[execute].max_source_chars_per_file` | LLM 上下文中单个文件的 source snippet 字符预算。 |
| `[execute].max_generated_lines` | greenfield 生成行数预算。省略时会回退到 `[resource].max_generated_lines`，再回退到保守默认值。 |
| `[models.code_task].planner` | work-plan 和 patch-plan 使用的模型。 |
| `[models.code_task].editor` | edit proposal 使用的模型。 |
| `[models.code_task].repair` | 失败后 repair proposal 使用的模型。 |
| `[budget].profile` | 当前 edit budget profile。`normal` 保守，`large` 用于已审核的多文件修改。 |
| `[budget].max_batches` | 一个 code task 中 executor 最多创建多少个实现批次。 |
| `[budget].cost_cap_usd` | provider usage 返回费用估计时可用的成本上限。 |
| `[budget.*].max_files` | 单个 edit proposal 最多修改多少个文件。 |
| `[budget.*].max_edits` | 单个 proposal 最多包含多少个 old/new replacement。 |
| `[budget.*].max_old_chars` / `[budget.*].max_new_chars` | 单个 edit 的 old/new 文本字符限制。 |
| `[budget.*].max_total_edit_chars` | 全部 edits 的总字符预算。 |
| `[budget.*].max_proposal_chars` | 序列化 proposal 的总字符预算。 |

## Workspace Mode Variants

### `auto`

`auto` 是已有项目推荐默认模式。它会在 `code_root` 位于本地 Git 仓库且仓库至少有一次 commit 时优先创建 detached `git_worktree`；如果 Git 条件不满足，则降级为受保护的 `copy`，并在 manifest 与终端输出中记录 `fallback_reason` 和 `user_next_steps`。

```toml
[workspace]
mode = "auto"
reuse_source_venv = false
```

如果 `code_root` 是较大仓库中的项目子目录，SimpleAutoResearch 会在仓库根创建 worktree，并把对应子目录作为后续索引、修改和运行的 project root。

### `copy`

`copy` 会在 `code_task/workspace` 下创建受保护的物理复制。它适合非 Git 项目、尚未提交但希望包含当前文件状态的实验，或者你明确不想使用 Git worktree 的场景。

```toml
[workspace]
mode = "copy"
reuse_source_venv = false

[safety]
max_file_bytes = 2000000
```

### `git_worktree`

`git_worktree` 会创建 detached worktree。显式选择该模式时，如果 Git 隔离不可用会直接失败并给出修复提示，而不会静默降级。`code_root` 可以是仓库根目录，也可以是仓库中的项目子目录；源项目必须位于本地 git 仓库中，并至少有一次 commit；不需要连接远程 GitHub 仓库。

```toml
[workspace]
mode = "git_worktree"
reuse_source_venv = true
```

### `sparse_copy`

`sparse_copy` 只复制 allowlist 路径，并始终应用内置排除规则：`.git`、虚拟环境、`runs`、cache/build 目录、data/model 目录、`.env` 和 secret-like 路径。

```toml
[workspace]
mode = "sparse_copy"
include = ["src/**", "tests/**", "configs/**", "main.py", "pyproject.toml"]
exclude = ["data/**", "models/**", "checkpoints/**"]
reuse_source_venv = false
```

只有在你理解项目依赖关系时才建议使用 `sparse_copy`；它可能漏掉运行时 import 需要的文件。

## 独立 Code-Task Config

用于 `simple-ar code-task init --config PATH`，后续也可传给
`simple-ar code-task execute RUN_DIR --config PATH`。

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
agent_model = ""              # 留空表示使用外部 CLI / 账号默认模型
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

独立 greenfield code-task 不需要填写 `code_root`，除非你有意从 scaffold/template
目录开始。workspace 默认使用 `empty`，`execute` 会把生成项目写到
`code_task/workspace/generated_project`：

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

如果要用同一个 greenfield 任务测试 Codex、Claude Code 或 OpenCode handoff，
保留 `[code_task]`、`[benchmark]` 和 `[resource]`，只切换实现 backend：

```toml
[implementation]
provider = "codex"
agent_mode = "handoff"
allow_external_agent = true
agent_model = ""          # 使用外部 CLI / 账号当前配置的模型
agent_timeout_sec = 1800
```

外部 agent 写出的仍然只是未信任候选文件。SimpleAutoResearch 会先 ingest，
再复制到 `code_task/workspace/generated_project`，之后继续走 review、validation、
benchmark、guard、memory 和 repair 路径。

## 在研究会话中使用 CodeTask

通过 research-session --code-task-config 提供现有项目 CodeTask TOML。
CodeTask 负责编辑与验证，应用负责实验测量和报告交付。

## Execute 与 Budget

`execute` 是状态感知调度器。下面配置控制它最多推进到哪一步、使用哪些模型、纳入多少上下文，以及允许多大的 edit proposal。

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

[execute.ablation]

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

`stream_benchmark_output` 取值：

| 值 | 含义 |
| --- | --- |
| `off` / `false` | 不实时转发 benchmark logs。 |
| `line` | 转发普通换行日志。 |
| `auto` / `true` | 同时处理普通日志和 `tqdm` 这类 carriage-return 进度输出。 |
| `summary` | benchmark 结束后只打印尾部摘要。 |

`baseline_policy` 取值：

| 值 | 含义 |
| --- | --- |
| `auto` | 默认行为；需要比较时运行未修改 baseline。 |
| `run` | 强制运行未修改 baseline。 |
| `skip` | 跳过未修改 baseline，但仍允许代码理解、review、validation 和最终 benchmark。 |
| `provided` | 从 `baseline_metrics_file` 记录用户已有指标；summary 会标注这些指标不是本次复测得到。 |
| `none` | 任务没有有意义的 baseline comparison，适合纯生成或验收式任务。 |

## Edit Scope Behavior

`[edit_scope]` 已经是 code-task 的真实安全契约，而不是只写给模型看的提示词。它会在多个位置重复生效：

- `init` 会把 `allowed_patterns`、`protected_patterns` 和可选 `mode` 写入 `code_task/manifest.json`；
- repo map、locate、context、work-plan、patch-plan、edit proposal 和 repair 会把 allowlist 之外的路径标为只读证据；
- `apply-edits` 写文件前还会再次校验 workspace-relative path、allowed patterns、protected patterns、当前 batch target files 和 old-text 精确匹配；
- 默认 protected patterns 始终保留，因此 tests、benchmark 文件、`.env`、secret/credential-like 路径等不会因为用户配置了 allowlist 而变成可写；
- `protected_patterns` 是在默认规则之外额外添加只读路径，不是替换默认规则。

常见配置如下：

```toml
[edit_scope]
# 允许模型改应用代码和必要的实验配置；测试、benchmark 与数据仍可作为 evidence 读取。
allowed_patterns = ["review_pipeline/**", "main.py", "configs/experiment.json"]

# 此示例不额外锁定配置；内置规则仍会保护 tests、数据、secret 和 credential-like 路径。
protected_patterns = []
```

如果 `allowed_patterns` 为空，含义是“允许所有非 protected 的 workspace 文件作为候选”。如果需要最严格的生产级流程，建议显式声明 allowlist，并把数据、配置、测试和 benchmark 放进 protected 范围。
