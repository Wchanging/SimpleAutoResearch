# CLI 参考

[English version](CLI_REFERENCE.md)

本文是 SimpleAutoResearch 的命令查询手册。安装和完整 walkthrough 见 [使用与配置](USAGE_zh.md)，阶段概念和产物结构见 [工作流与产物](WORKFLOWS_zh.md)。

## 顶层命令

| 命令 | 用途 |
| --- | --- |
| `simple-ar run` | 启动新的 8 阶段 research pipeline。 |
| `simple-ar resume` | 继续已有 research pipeline run。 |
| `simple-ar status` | 查看 research run 或 code-task run 状态。 |
| `simple-ar inspect` | 为某次 run 构建本地 artifact index。 |
| `simple-ar search-artifacts` | 使用 lexical retrieval 搜索 run artifacts。 |
| `simple-ar code-task ...` | 在隔离可编辑 workspace 中处理已有代码库。 |

## Research Pipeline

启动 run：

```bash
uv run simple-ar run --topic "agent simulation" --to-stage report
```

常用参数：

| 参数 | 含义 |
| --- | --- |
| `--config PATH` | 可复现 run 的 TOML 配置。显式 CLI 参数会覆盖配置值。 |
| `--topic TEXT` | 研究主题。除非 `--config` 的 `[run].topic` 已设置，否则必填。 |
| `--output-root DIR` | run 目录创建位置。默认 `runs`。 |
| `--from-stage NAME` | 起始阶段。默认 `plan`。 |
| `--to-stage NAME` | 结束阶段。默认 `report`。 |
| `--model NAME` | 覆盖 LLM 模型。 |
| `--llm-workers N` | 支持阶段的并发 LLM worker 数。 |
| `--max-papers N` | 文献检索数量上限。 |
| `--search-query TEXT` | 覆盖生成的检索 query。 |
| `--experiment-template NAME` | 实验模板名称。 |
| `--experiment-timeout N` | 实验子进程 timeout。 |
| `--report-mode auto / research_only / experiment` | 报告结构模式。 |
| `--no-llm` | 使用确定性 fallback 文本，不调用 LLM。 |
| `--offline-search` | 跳过 live literature provider。 |
| `--allow-fixture-fallback` | live/cache 失败后允许 placeholder metadata。 |
| `--strict-search` | 搜索失败时直接失败，不使用 cache/fixture fallback。 |
| `--no-retrieval` | 禁用本地 artifact retrieval 上下文。 |
| `--retrieval-top-k N` | 本地 artifact chunk 检索数量。 |
| `--quiet` | 减少进度日志输出。 |

实验模板：

| 模板 | 含义 |
| --- | --- |
| `toy_text_classification` | 默认确定性教学实验。 |
| `llm_code_task_toy_spam` | 内置 toy code-task smoke test。 |
| `code_task_project` | 面向用户项目的内嵌 code-task experiment。 |

### Run Config

参数较多时，优先使用 TOML，而不是写很长的 CLI：

```bash
uv run simple-ar run --config examples/run_configs/tiny_digits_mlp_pipeline.toml
```

下面是完整的 `code_task_project` pipeline config 示例，把外层 research pipeline 和内嵌 code-task 参数放在同一个文件中：

```toml
[run]
# 除非 CLI 提供 --topic，否则必填。
topic = "improve tiny digits MLP"

# 时间戳 run 目录创建位置。
output_root = "runs"

# 可选。默认 from_stage 为 "plan"，to_stage 为 "report"。
from_stage = "plan"
to_stage = "report"

[llm]
# true：使用配置好的 OpenAI-compatible LLM。
# false：尽可能使用 deterministic fallback。
# code_task_project 的真实 patch planning/edit proposal 需要 LLM。
enabled = true

# 可选模型覆盖。不填时使用 SIMPLE_AR_MODEL 或 provider 默认值。
model = "gpt-4o-mini"

# 支持阶段的并发 LLM worker 数，例如 paper note generation。
workers = 4

[search]
# true：跳过 live OpenAlex/arXiv，使用 fixture metadata。
# 适合本地 coding smoke test，此时文献质量不是重点。
offline = true

# live provider 或 fixture fallback 的 paper metadata 数量上限。
max_papers = 1

# 可选手动 query。不填时使用 topic。
query = "tiny digits MLP"

# 可选。live/cache 失败后是否允许 fixture rows。
allow_fixture_fallback = false

# 可选。true 时搜索失败直接失败，不使用 fallback。
strict = false

[retrieval]
# read/synthesize/report 阶段是否可以检索本地产物片段。
enabled = true
top_k = 4

[experiment]
# "toy_text_classification"：确定性教学实验。
# "code_task_project"：内嵌已有代码项目 workflow。
# "llm_code_task_toy_spam"：legacy bundled smoke test。
template = "code_task_project"

# 07-run experiment.py timeout。对 code_task_project 也约束嵌套 baseline/patched benchmark。
timeout = 60

# 可选。也可以不在本文件写 [code_task]/[benchmark]/[environment]/[safety]，
# 而是指向一个 standalone code-task config。
# code_task_config = "examples/code_tasks/configs/tiny_digits_mlp.toml"

[report]
# "auto"：有 results.json 就写实验报告，否则写 research_only。
# "research_only"：survey-style report，不声明实验结果。
# "experiment"：要求 results.json，并使用实验结构。
mode = "auto"

[code_task]
# 源项目会准备到 06-code/code_task_run/code_task/workspace。
code_root = "examples/code_tasks/tiny_digits_mlp_project"

# 对内嵌 8 阶段 run 是可选项。如果省略，05-design 会基于
# goal/problem/synthesis/hypothesis 和代码摘要生成 generated_code_task.md，
# 06-code 再复制成 code_task/task.md。
# standalone `simple-ar code-task init` 仍要求 task file。
task_file = "examples/code_tasks/tasks/improve_tiny_digits_mlp.md"

# 可选展示名，写入 experiment_plan.json 和嵌套 manifest。
name = "tiny-digits-mlp-pipeline"

[benchmark]
# 在 editable workspace 内 patch 前后都会执行的命令。
command = "python benchmark.py"

# 可选主要指标，用于 before/after verdict。
primary_metric = "accuracy"

[benchmark.metric_directions]
# 方向可以是 higher、lower、resource 或 ignore。
# 未知指标仍会记录 delta，但不会决定 improved/regressed，
# 除非显式配置方向或命中简单启发式规则。
accuracy = "higher"
macro_f1 = "higher"
train_time_sec = "resource"
inference_time_ms = "resource"
params = "resource"

[environment]
# current：使用当前 SimpleAutoResearch Python。
# external：使用 python 指定的解释器。
mode = "current"
# python = "C:/path/to/python.exe"

[workspace]
# copy：受保护的物理复制，最稳妥默认值。
# git_worktree：repo-root git 项目的 detached worktree。
# sparse_copy：实验性 allowlist copy，用于小型明确子集。
mode = "copy"

# 仅 sparse_copy 使用。默认已经包含保守的 source/config/test globs。
include = ["src/**", "tests/**", "benchmark.py", "pyproject.toml"]
exclude = ["data/**", "models/**"]

# 如果 code_root 中存在 .venv/ 或 venv/，记录并使用其中 Python 作为 external execution policy。
# 不会安装依赖。
reuse_source_venv = false

# 为未来 managed setup 记录的命令；init 时不会执行。
setup_hook = ""

[safety]
# copy/sparse 模式的最大源码文件大小。0 表示禁用。
max_file_bytes = 2000000
```

配置段说明：

| 段 | 使用方 | 含义 |
| --- | --- | --- |
| `[run]` | 外层 pipeline | topic、run 目录和阶段范围。 |
| `[llm]` | 外层 pipeline 和 code task | LLM 是否启用、模型覆盖和 worker 数。 |
| `[search]` | `02-search` | 文献 provider 行为和 fallback 策略。 |
| `[retrieval]` | read/synthesize/report helpers | 本地 artifact retrieval 上下文。 |
| `[experiment]` | `05-design` 到 `07-run` | 实验模板、timeout 和可选嵌套 code-task config 路径。 |
| `[report]` | `08-report` | 报告结构模式。 |
| `[code_task]` | 内嵌或 standalone code task | 源项目、可选 task file 和展示名。 |
| `[benchmark]` | code task | benchmark command 和 primary metric。 |
| `[benchmark.metric_directions]` | code task comparison | 指标解释规则。 |
| `[environment]` | code task execution | probe/baseline/patched run 的解释器策略。 |
| `[workspace]` | code task init | workspace 模式、source venv 复用和 setup hook 记录。 |
| `[safety]` | code task workspace/validation | copy/sparse 文件大小保护和未来安全设置。 |

当 run config 包含 `[code_task]`、`[benchmark]`、`[metrics]`、`[environment]`、`[workspace]` 或 `[safety]` 时，同一个文件也会被复用为内嵌 code-task config。也可以把 code-task 设置放到单独文件，然后设置 `[experiment].code_task_config`。

显式 CLI 参数会覆盖配置。例如保留配置但只运行到 design，并禁用 LLM：

```bash
uv run simple-ar run \
  --config examples/run_configs/tiny_digits_mlp_pipeline.toml \
  --to-stage design \
  --no-llm
```

`code_task_project` 在 `run` 和 `resume` 中的参数：

| 参数 | 含义 |
| --- | --- |
| `--code-task-config PATH` | 使用和 `code-task init --config` 相同 schema 的 TOML 配置。 |
| `--code-root DIR` | 源项目，准备到 `06-code/code_task_run/code_task/workspace`。 |
| `--task-file PATH` | Markdown/text 任务描述。内嵌 8 阶段 run 可省略；省略时 `05-design` 从研究产物生成 `generated_code_task.md`。 |
| `--benchmark-command TEXT` | patch 前后运行的 benchmark。 |
| `--code-task-name TEXT` | 写入 `experiment_plan.json` 的可选展示名。 |
| `--code-task-max-file-bytes N` | `copy` 或 `sparse_copy` 模式下的最大复制文件大小。 |
| `--code-task-workspace-mode copy / git_worktree / sparse_copy` | 嵌套 code task 的 workspace 策略。sparse include/exclude 推荐用 TOML。 |
| `--code-task-workspace-reuse-source-venv` | 使用检测到的 source `.venv` Python 作为嵌套 execution policy。 |
| `--code-task-workspace-setup-hook TEXT` | 为未来 managed environment 记录 setup command。 |
| `--code-task-env-mode current / external` | 嵌套 probe/baseline/run 的解释器策略。 |
| `--code-task-python PATH` | `--code-task-env-mode external` 的解释器路径。 |
| `--primary-metric NAME` | before/after verdict 的主要指标。 |
| `--metric-direction NAME=DIRECTION` | 内嵌 comparison 的指标解释，可重复。 |

通用内嵌路径会在 pipeline workspace 内自动批准生成的 patch plan，使 `run --to-stage report` 能完整结束。如果需要每个状态转换前都人工审核，请使用 standalone `code-task` 命令。

恢复：

```bash
uv run simple-ar resume runs/<run-id>
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode research_only
```

`resume` 支持大多数 `run` 参数作为覆盖项；未传值会尽量从 `config_snapshot.json` 保留。

## Artifact Tools

```bash
uv run simple-ar inspect runs/<run-id>
uv run simple-ar search-artifacts runs/<run-id> "accuracy"
uv run simple-ar search-artifacts runs/<run-id> "timeout" --include-operational
```

| 命令/参数 | 用途 |
| --- | --- |
| `inspect RUN_DIR` | 构建 `artifact_index.json`，打印紧凑 artifact summary。 |
| `search-artifacts RUN_DIR QUERY` | 搜索本地 artifact chunks。 |
| `--top-k N` | 搜索结果数量。默认 `8`。 |
| `--include-operational` | 同时搜索 manifest、runner metadata 等运行管理文件。 |

## Code Task Commands

Code-task workflow 会把已有项目准备到 `code_task/workspace`。默认是受保护 copy；`git_worktree` 可为较大 repo-root 项目创建 detached git worktree；`sparse_copy` 是实验性 allowlist copy。后续步骤只修改 workspace，不修改原始代码库。

当 init 无法准备 workspace 时，CLI 会报告失败路径和简短检查清单。对 `git_worktree`，常见修复是传入 baseline git 仓库根目录、创建初始本地 commit，或者选择 `copy` 模式。

推荐顺序：

```text
init -> map -> locate -> context -> probe -> baseline -> plan -> decide-plan
-> propose-edits -> apply-edits -> validate -> run
-> analyze-failure -> repair
```

### Init

最小形式：

```bash
uv run simple-ar code-task init \
  --code-root path/to/project \
  --task-file task.md \
  --benchmark-command "python benchmark.py"
```

配置形式：

```bash
uv run simple-ar code-task init --config code_task.toml
```

参数：

| 参数 | 含义 |
| --- | --- |
| `--config PATH` | init 设置的 TOML 配置。CLI 参数覆盖配置值。 |
| `--code-root DIR` | 源项目。除非配置中已设置，否则必填。 |
| `--task-file PATH` | Markdown/text 任务描述。除非配置中已设置，否则必填。 |
| `--output-root DIR` | run 目录创建位置。默认 `runs`。 |
| `--name TEXT` | run 名称后缀。默认基于 `code-root`。 |
| `--benchmark-command TEXT` | 在 editable workspace 内运行的命令。 |
| `--max-file-bytes N` | 最大复制文件大小。`0` 表示禁用。 |
| `--workspace-mode copy / git_worktree / sparse_copy` | workspace 策略。`copy` 最稳妥；`git_worktree` 要求 `--code-root` 是 git 仓库根目录；`sparse_copy` 只复制选中 patterns。 |
| `--workspace-include GLOB` | sparse-copy include pattern，可重复。多个 pattern 用 TOML 更清晰。 |
| `--workspace-exclude GLOB` | sparse-copy 额外 exclude pattern，可重复。 |
| `--workspace-reuse-source-venv` | 如果 source 有 `.venv` 或 `venv`，记录并使用其中 Python 作为初始 external execution policy。 |
| `--workspace-setup-hook TEXT` | 记录 setup command。init 不执行它。 |
| `--env-mode current / external` | 执行解释器策略。 |
| `--python PATH` | `--env-mode external` 的解释器路径。 |
| `--primary-metric NAME` | before/after verdict 的主要指标。 |
| `--metric-direction NAME=DIRECTION` | 指标解释，可重复。 |

指标方向：

| 方向 | 含义 |
| --- | --- |
| `higher` | 越大越好，例如 accuracy/F1/reward。 |
| `lower` | 越小越好，例如 loss/error/perplexity。 |
| `resource` | 运行时间/成本/资源指标；展示但不参与 verdict。 |
| `ignore` | 记录但不解释。 |

### Init Config

```toml
[code_task]
code_root = "path/to/project"
task_file = "task.md"
output_root = "runs"
name = "my-code-task"

[benchmark]
command = "python benchmark.py"
primary_metric = "accuracy"

[benchmark.metric_directions]
accuracy = "higher"
macro_f1 = "higher"
latency_ms = "resource"
val_loss = "lower"

[environment]
mode = "current"  # current | external
python = ""       # mode = "external" 时可选

[workspace]
mode = "copy"                  # copy | git_worktree | sparse_copy
include = ["src/**", "tests/**", "benchmark.py", "pyproject.toml"]
exclude = ["data/**", "models/**"]
reuse_source_venv = false      # 检测 source .venv Python 并使用
setup_hook = ""                # 只记录；init 时不执行

[safety]
max_file_bytes = 2000000
```

`sparse_copy` 会始终应用内置排除规则：`.git`、virtualenv、`runs`、cache/build、`data`、`models`、`.env` 和 secret-like 路径。它适合小型 allowlisted 实验，但可能遗漏运行依赖；通用项目优先用 `copy` 或 `git_worktree`。

Code-task run 也会在 `manifest.json` 中记录 `edit_scope`。当前默认把 tests、benchmark 文件、`.env` 和 secret/credential-like 路径作为只读证据：
`tests/**`、`test_*.py`、`*_test.py`、`conftest.py`、`benchmark.py`、`bench.py`、`*benchmark*.py`、`.env*`、`*secret*`、`*credential*`。这些文件可被索引用于 planning，但不会作为可编辑 snippet，并会被 `apply-edits` 拒绝。

内置示例：

```bash
uv run simple-ar code-task init --config examples/code_tasks/configs/tiny_digits_mlp.toml
```

### Map

从当前 editable workspace 构建或刷新分层 repo-map artifacts：

```bash
uv run simple-ar code-task map runs/<run-id>
```

| 命令/参数 | 含义 |
| --- | --- |
| `map RUN_DIR` | 从 `code_task/workspace/` 重建 `code_task/meta/codebase_index.json`、`repo_map.json` 和 `repo_map_summary.md`。 |
| `--no-refresh-index` | 不重新扫描当前 workspace，直接复用已有 `codebase_index.json`。 |
| `--show-summary` | 写入后打印 `repo_map_summary.md`。 |

`map` 是确定性步骤。它不会调用 LLM、不会运行项目代码、不会安装依赖，也不会修改文件。它的用途是让项目结构可检查，并为后续 locate/context-pack 提供基础 artifact。

### Locate And Context Pack

在规划或编辑前，先对可能相关的文件进行排序：

```bash
uv run simple-ar code-task locate runs/<run-id> --query "improve spam keyword prediction"
```

| 命令/参数 | 含义 |
| --- | --- |
| `locate RUN_DIR` | 写入 `code_task/meta/locate_results.json` 和 `locate_results.md`。 |
| `--query TEXT` | 可选 query；不填时使用 `code_task/task.md`。 |
| `--top-k N` | 每组 editable/evidence 保留的候选数量，默认 `8`。 |
| `--refresh-map` | 排序前重建 codebase index 和 repo map。 |
| `--no-read-only` | 不输出 tests、benchmarks 等只读证据。 |
| `--show-summary` | 写入后打印 `locate_results.md`。 |

`locate` 是确定性步骤，不调用 LLM。它从 `repo_map.json` 中读取 path、
summary、imports、role tags 和 symbols，分开输出可编辑目标和只读证据，
用于回答“大项目里应该先看哪里”。

构建可直接放进 prompt 的受限上下文包：

```bash
uv run simple-ar code-task context runs/<run-id> \
  --query "improve spam keyword prediction" \
  --max-files 8 \
  --max-total-chars 20000
```

| 命令/参数 | 含义 |
| --- | --- |
| `context RUN_DIR` | 创建新的 `code_task/context_packs/context-NNN/`。 |
| `--query TEXT` | 可选 locate query；不填时使用 `code_task/task.md`。 |
| `--top-k N` | 传给 locate 的每组候选预算，默认 `8`。 |
| `--max-files N` | editable 和 read-only 文件合计最多纳入多少个 snippet。 |
| `--max-source-chars-per-file N` | 每个文件的源码片段字符预算。 |
| `--max-total-chars N` | 全部 snippet 的总字符预算。 |
| `--refresh-map` | context pack 前刷新 repo map。 |
| `--show-prompt` | 打印生成的 `prompt_context.md`。 |

`context` 会写入 `context_pack.json`、`prompt_context.md` 和
`selected_snippets.jsonl`。它不调用 LLM，也不修改 workspace。当前如果存在
latest context pack，`plan` 会优先使用它作为规划上下文，`propose-edits` 只会读取其中
editable snippets，把 tests/benchmarks 等保护文件继续作为 read-only evidence。

生成面向批次执行的 work plan：

```bash
uv run simple-ar code-task work-plan runs/<run-id>
uv run simple-ar code-task batch runs/<run-id> --work-item W1
```

| 命令/参数 | 含义 |
| --- | --- |
| `work-plan RUN_DIR` | 写入 `code_task/work_plan.json` 和 `code_task/work_plan.md`。 |
| `--model NAME` | 覆盖 work-plan 生成使用的模型。 |
| `--no-llm` | 使用 deterministic fallback planner。 |
| `--force` | 重新生成已有 work-plan artifacts。 |
| `--max-files N` | 规划时最多纳入多少个上下文文件。 |
| `--max-source-chars-per-file N` | 每个文件的源码 snippet budget。 |
| `batch RUN_DIR --work-item W1` | 为某个 work-plan item 创建 attempt/batch 状态目录。 |
| `--attempt-id attempt-001` | 复用或创建指定 attempt id。 |
| `--force` | 即使该 work item 已有 batch，也强制创建一个新 batch。 |

`work-plan` 是 V2.2 中从“大任务”过渡到“受控编辑批次”的桥。它会记录
target files、read-only evidence、validation hints、context requests 和 budget
profiles。`batch` 会在
`code_task/attempts/attempt-NNN/batches/batch-NNN/` 下写入持久状态；它暂时
不调用 LLM，也不修改文件。后续 active batch 的 edit proposal 会被限制在
该 batch 的 target files 内，并额外写入 `batch_context.json`、
`proposed_edits.json`、`proposal_warnings.json` 和 `usage_summary.json` 等
批次级产物。

### Manual Command Path

当你想自己运行并检查每个 primitive step 时，使用手动路径：

```bash
uv run simple-ar code-task map runs/<run-id>
uv run simple-ar code-task locate runs/<run-id>
uv run simple-ar code-task context runs/<run-id>
uv run simple-ar code-task probe runs/<run-id>
uv run simple-ar code-task baseline runs/<run-id> --timeout 60
uv run simple-ar code-task work-plan runs/<run-id>
uv run simple-ar code-task batch runs/<run-id> --work-item W1
uv run simple-ar code-task plan runs/<run-id>
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve
uv run simple-ar code-task propose-edits runs/<run-id>
uv run simple-ar code-task apply-edits runs/<run-id>
uv run simple-ar code-task validate runs/<run-id>
uv run simple-ar code-task run runs/<run-id> --timeout 60
```

#### Environment And Baseline

```bash
uv run simple-ar code-task probe runs/<run-id>
uv run simple-ar code-task baseline runs/<run-id> --timeout 60
```

| 命令/参数 | 含义 |
| --- | --- |
| `probe RUN_DIR` | 写入 `code_task/meta/environment_report.json`。 |
| `baseline RUN_DIR` | patch 前运行 benchmark，产物放在 `code_task/run/baseline/`。 |
| `--command TEXT` | 覆盖本次 benchmark command。 |
| `--timeout N` | benchmark timeout 秒数。 |
| `--skip-validation` | 即使静态验证未通过也运行 benchmark。 |
| `--env-mode`, `--python` | 覆盖执行解释器策略。 |

#### Planning And Approval

```bash
uv run simple-ar code-task work-plan runs/<run-id>
uv run simple-ar code-task batch runs/<run-id> --work-item W1
uv run simple-ar code-task plan runs/<run-id>
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve
```

| 命令/参数 | 含义 |
| --- | --- |
| `plan RUN_DIR` | 生成 `code_task/patch_plan.md`。 |
| `--model NAME` | 覆盖 planning 模型。 |
| `--no-llm` | 写 deterministic fallback plan。 |
| `--force` | 重新生成已有 plan。 |
| `--max-files N` | 选择上下文文件数量上限。 |
| `--max-source-chars-per-file N` | 每个文件的源码 snippet budget。 |
| `work-plan RUN_DIR` | 生成用于 batch execution 的 `code_task/work_plan.json` 和 `work_plan.md`。 |
| `batch RUN_DIR --work-item W1` | 创建 `code_task/attempts/attempt-NNN/batches/batch-NNN/batch_state.json`。 |
| `decide-plan RUN_DIR` | 记录 plan 审核结果。 |
| `--decision approve / reject / revise` | 必填决策。 |
| `--note TEXT` | 可选审核备注。 |
| `--reviewer TEXT` | 审核人标签，默认 `user`。 |

### Executor Path

当你希望 CLI 根据当前 run 状态继续到下一个安全停止点时，使用 executor：

```bash
# 运行到 plan 审核点。
uv run simple-ar code-task execute runs/<run-id>

# 阅读 code_task/patch_plan.md 后批准。
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve

# 明确运行到 edit proposal 审核点。
uv run simple-ar code-task execute runs/<run-id> --to-step propose-edits

# 应用已审核 proposal，并运行验证/benchmark。
uv run simple-ar code-task execute runs/<run-id> --apply-proposed-edits --timeout 60
```

重复调用 `execute` 是预期行为。它会读取 run artifacts，执行下一段安全工作，并在 review boundary 停止。

| 命令/参数 | 含义 |
| --- | --- |
| `execute RUN_DIR` | 基于当前 artifacts 运行下一组安全 code-task 步骤。 |
| `--config PATH` | 可选 TOML 配置，用于 execute 的模型路由、预算和运行参数。 |
| `--to-step STEP` | 最多运行到 `probe`、`baseline`、`work-plan`、`batch`、`plan`、`propose-edits`、`apply-edits`、`validate`、`run`、`analyze-failure` 或 `repair`。 |
| `--dry-run` | 只打印下一步动作，不写产物。 |
| `--no-llm`, `--model NAME` | 控制 plan/proposal/repair 的 LLM 使用。 |
| `--apply-proposed-edits` | 允许 execute 在 plan 已批准后应用审核过的 `proposed_edits.json`。 |
| `--allow-large-edits` | 允许接受/应用超过 normal 预算但仍在 large 预算内的已审核 proposal。 |
| `--repair-rounds N` | validation/benchmark 失败后的 bounded repair proposal 轮数上限。repair 不会自动应用。 |
| `--timeout N` | baseline 和 patched run 的 benchmark timeout。 |
| `--strict-validation`, `--validation-max-file-bytes N` | orchestrated validate step 的控制项。 |
| `--env-mode`, `--python` | 覆盖 probe 和 benchmark run 的解释器策略。 |

Review gate 会保留。fresh run 会先用配置好的 LLM 创建真实的 `work_plan.json`（除非显式传 `--no-llm`），再创建第一份 attempt/batch 状态，然后在 `patch_plan.md` 后以 `approval_required` 停止。批准后，建议运行 `execute --to-step propose-edits` 明确生成 `proposed_edits.json`；生成后仍会以 `proposal_review_required` 停止，除非提供 `--apply-proposed-edits`。

`execute --config` 可以复用 `code-task init --config` 的 TOML 文件，并读取下面这些额外 section：

```toml
[execute]
to_step = "run"
use_llm = true
timeout_sec = 60
repair_rounds = 1
max_files = 8
max_source_chars_per_file = 4000
apply_proposed_edits = false
allow_large_edits = false

[models.code_task]
# 省略时使用 SIMPLE_AR_MODEL 或 --model。
planner = "gpt-5.1"
editor = "gpt-5.1"
repair = "gpt-5.1"
summarizer = "gpt-5.1"

[budget]
profile = "normal"       # normal | large | absolute
max_batches = 3
cost_cap_usd = 2.0       # 仅当 provider usage 返回费用时强制生效

[budget.normal]
max_files = 2
max_edits = 4
max_old_chars = 3000
max_new_chars = 4000
max_total_edit_chars = 12000
max_proposal_chars = 24000
```

编辑预算会在模型返回 JSON 后由本地 normalizer 强制检查。超预算 proposal 会写入 warnings 和 rejected edits，而不是直接应用。如果 proposal 落在 large profile 内，需要人工审核后再显式加 `--allow-large-edits`。

预览下一步：

```bash
uv run simple-ar code-task execute runs/<run-id> --dry-run
```

#### Patch, Validate, Run

```bash
uv run simple-ar code-task propose-edits runs/<run-id>
uv run simple-ar code-task apply-edits runs/<run-id>
uv run simple-ar code-task validate runs/<run-id>
uv run simple-ar code-task run runs/<run-id> --timeout 60
```

| 命令/参数 | 含义 |
| --- | --- |
| `propose-edits RUN_DIR` | 请求模型生成受控 old/new replacements。 |
| `--allow-large-edits` | 人工审核后接受 large 预算内的较大 proposal。 |
| `apply-edits RUN_DIR` | 在 workspace 内应用已批准 edit proposal。 |
| `--edits-file PATH` | 应用指定 proposal 文件。 |
| `--allow-unapproved-plan` | 为本地测试/demo 绕过 approval gate。 |
| `--allow-large-edits` | 应用一个标记为需要 large-edit 审批的已审核 proposal。 |
| `validate RUN_DIR` | 运行 syntax/static safety checks。 |
| `--strict` | 把较高风险 validation warning 当作 error。 |
| `run RUN_DIR` | 在 `code_task/run/patched/` 下运行 patched benchmark。 |

当 baseline 和 patched run 都存在时，SimpleAutoResearch 会写入 `code_task/run/comparison.json` 并更新 `code_task/summary.md`。

`proposed_edits.json` 可以包含同一文件的多个有序 edit。每个 edit 都在当前内存文本上应用，且每个 `old` block 必须唯一匹配。无效 proposal 会在写文件前停止；在 `execute` 中表现为 `patch_apply_failed`。

proposal 是结构化 JSON，不是 unified diff。`old` 和 `new` 必须包含精确文件文本，不要在其中写 `+`、`-`、`@@`、`---`、`+++` 这类 diff 标记。如果 repair proposal 中出现这些标记，normalizer 会丢弃该 edit 并写入 warning；如果手工 proposal 无法匹配当前 workspace，`apply-edits` 会输出简洁的 validation error，并保持文件不变。

Edit-scope 会检查两次：模型 proposal 中的保护路径会被丢弃，`apply-edits` 对模型和手工 proposal 都会再次拒绝保护路径。

#### Failure And Repair

```bash
uv run simple-ar code-task analyze-failure runs/<run-id>
uv run simple-ar code-task repair runs/<run-id>
```

| 命令/参数 | 含义 |
| --- | --- |
| `analyze-failure RUN_DIR` | 总结最近失败的 benchmark 或 validation。 |
| `repair RUN_DIR` | 根据 failure context 提出 bounded repair edits。 |
| `--model NAME` | 覆盖 repair 模型。 |
| `--no-llm` | 写 deterministic empty repair proposal。 |
| `--max-files N` | 上下文文件数量上限。 |
| `--max-source-chars-per-file N` | 每个文件的源码 snippet budget。 |

Repair proposal 不会自动应用。审核后使用：

```bash
uv run simple-ar code-task apply-edits runs/<run-id> \
  --edits-file runs/<run-id>/code_task/repairs/repair-001/proposed_edits.json
```

`analyze-failure` 会把 `failure_analysis.md` 写在失败 benchmark run 旁边；如果静态 validation 在 benchmark 启动前失败，则写到 `code_task/meta/`。`repair` 会写 proposal JSON，包含 `source_analysis`、`selected_files`、`constraints`、规范化 `edits` 和 `warnings`，并刷新 `summary.md` 的 Repair section。

repair proposal 仍然只是 proposal。应用后还要重新 validate 和 run。benchmark pass 也不一定代表任务目标已经完成；如果 patched 指标仍低于 baseline，说明只是恢复可运行或恢复到阈值以上，是否完成要看 `run/comparison.json` 的 verdict 和指标差值。

## Status

```bash
uv run simple-ar status runs/<run-id>
```

对 code-task run，status 会打印 environment、plan、patch、validation、benchmark、primary metric、metric directions、comparison deltas、failure-analysis、repair pointers，以及可用时的 `code_task/summary.md` 路径。
