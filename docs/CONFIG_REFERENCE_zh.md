# 配置参考

[English version](CONFIG_REFERENCE.md)

本文是 SimpleAutoResearch 的 TOML 配置规范，覆盖外层 research pipeline 配置，以及 standalone / embedded code-task 配置。

- 命令语法：[CLI 参考](CLI_REFERENCE_zh.md)
- 用户实践流程：[使用与配置](USAGE_zh.md)
- 阶段概念和产物：[工作流与产物](WORKFLOWS_zh.md)

## 加载规则

- `simple-ar run --config PATH` 读取顶层 pipeline 配置。
- `simple-ar resume --config PATH` 会在已保存 run 配置上应用覆盖。
- `simple-ar code-task init --config PATH` 读取 code-task 初始化配置。
- `simple-ar code-task execute --config PATH` 读取 execute / model / budget 配置。
- 显式 CLI 参数覆盖 TOML 值。
- 顶层 pipeline TOML 和 code-task TOML 会先经过 Pydantic 校验，再 flatten 成运行时设置；section 类型写错时会更早失败，而不是被静默忽略。
- 顶层 run config 中，相对路径会在解析器支持的字段上相对配置文件解析，例如 `[experiment].code_task_config` 和 `[research].local_documents`。
- 当 run config 包含 `[code_task]`、`[benchmark]`、`[metrics]`、`[environment]`、`[safety]` 或 `[edit_scope]` 这类旧 code-task section 时，同一文件可被复用为 embedded code-task config。`[workspace]` 也会被新的统一任务配置使用，因此不会再单独作为 code-task 信号。

## 完整 Pipeline Config

这是带内嵌 code-task 实验的 8 阶段 run 完整形状。实际使用时，可以从这个模板删掉不需要的 section。

```toml
[run]
# 本次运行的人类可读研究/实验目标。
topic = "improve tiny digits MLP"

# 时间戳 run 目录的父目录。
output_root = "runs"

# 阶段范围；不写 from_stage 默认从 plan 开始，不写 to_stage 默认到 report 结束。
from_stage = "plan"
to_stage = "report"

# 是否减少命令行进度日志；最终路径和状态仍会输出。
quiet = false

# true 会在 run 目录中保留 search planning/traces/coverage 文件和 design tool-handoff 草案；
# 默认 false 只压缩诊断文件。search documents/chunks、read Paper Brief、synthesis brief
# 和 design contracts 这类后续阶段需要的核心产物仍会保留。
debug_artifacts = false

# false 表示重跑 06-code/07-run 前先归档旧的已审核产物。
# 只有明确不需要保留旧代码/运行证据时才设为 true。
overwrite_stage_artifacts = false

[llm]
# true 使用 OpenAI-compatible 模型；false 尽量使用 deterministic fallback。
enabled = true

# 可选默认模型；省略时使用 SIMPLE_AR_MODEL 或 provider 默认值。
model = "gpt-4o-mini"

# 支持并发的 LLM 阶段使用的 worker 数，例如 paper note generation。
workers = 4

[search]
# true 跳过 live provider，使用 fixture/local 行为。
offline = true

# search 阶段最多请求/保留多少条论文或研究记录 metadata；不是 PDF 页数或 chunk 数。
max_papers = 5

# 手动搜索 query；省略时使用 topic 或 research queries。
query = "tiny digits MLP baseline improvement"

# live/cache 失败后是否允许使用 fixture metadata。
allow_fixture_fallback = false

# true 表示搜索失败就让 run 失败，不再回退到 cache 或 fixture。
strict = false

[research]
# research planner 使用的检索策略档位；完整 planning artifact 只在 debug_artifacts = true 时保留。
mode = "lite"                 # lite | standard | strong

# research-question/query planner。auto 会在 [llm].enabled = true 时调用 LLM；
# provider 不可用时回退到 deterministic planning。
planner = "auto"              # auto | llm | deterministic

# 02-search 的 provider 顺序。
sources = ["fixture"]         # openalex | semantic_scholar | arxiv | local_files | fixture

# evidence planning 的 query 列表；search 可针对未覆盖 facets 执行 follow-up round。
queries = ["tiny digits MLP baseline improvement"]

# 是否根据 topic 和 research questions 生成 facet-driven follow-up queries。
auto_query_expansion = true

# retrieval loop 的计划轮数；大于 1 时允许 coverage-driven follow-up search。
max_retrieval_rounds = 2

# research plan 中最多保留多少个 seed + expanded queries。
max_queries = 6

# planner 应尝试覆盖的 evidence facets。
required_facets = ["method", "benchmark", "dataset", "code_link"]

# 作为本地研究记录读取的 Markdown/text 文件；路径相对当前 config 解析。
local_documents = []

# full-text evidence 的意图与预算开关。远程 PDF 获取受这些设置保护；
# 正文解析仍由后续受控步骤处理。
use_fulltext = false
allow_pdf_download = false
max_fulltext_documents = 6
max_pdf_mb = 20
keep_raw_pdf = false
parser_backend = "basic"      # basic | pypdf | unstructured

# 03-read LLM review：先并发粗筛 title/abstract 小批次，再对保留集合重排。
read_screening = "auto"       # auto | llm | deterministic
read_batch_size = 4
read_workers = 3
read_max_shortlist = 12

# live provider 失败后是否允许使用 cached metadata。
cache = true

# 本地索引后端；chunks.jsonl 总会写出，更强后端作为可选加速层。
index_backend = "keyword"     # keyword | sqlite_fts | hybrid | lancedb | hybrid_lancedb

# SQLite FTS / LanceDB 的共享加速索引目录；如需每个 run 自己保存数据库，可设为 "run" 或 "local"。
index_root = ".simple_ar_cache/research_index"

[research.budget]
# evidence engine 最多保留多少条研究记录。
max_documents = 5

# 后续文档 ingestion/indexing 的 chunk 数上限。
max_chunks = 50

# evidence retrieval 放进 prompt 的计划 token 预算。
max_context_tokens = 6000

# research 侧 query expansion/screening 等 LLM 调用上限。
max_llm_calls = 4

# 第二轮 coverage-driven follow-up retrieval 最多尝试几个 query。
max_follow_up_queries = 3

# 04-synthesize/synthesis_brief.json 内 novelty hints 的 backend。local 只在当前 Paper Brief 内做词面重合风险提示，
# 不是正式 novelty check。
novelty_backend = "local"      # local

[retrieval]
# read/synthesize/report helper 是否启用本地 artifact retrieval。
enabled = true

# 启用 retrieval 时检索多少个 artifact chunk。
top_k = 4

[experiment]
# 实验模板；code_task_project 会内嵌已有代码项目 workflow。
template = "code_task_project"

# 07-run 和适用情况下嵌套 benchmark 的 timeout 秒数。
timeout = 60

# 可选外部 code-task config；省略时复用本文件中的 code-task sections。
# code_task_config = "examples/full_pipeline_tiny_mlp/configs/pipeline.toml"

# V2.5 开始，新 pipeline config 推荐使用下面这些统一 section。它们把
# research-first、已有项目 code-task、greenfield experiment 的关键参数
# 归一到同一个运行时 TaskConfig。下面旧的 [code_task]/[benchmark]/[environment]
# 仍然兼容，但新配置应优先使用这一组。
[task]
kind = "existing_project"      # auto | existing_project | greenfield | benchmark_solution
name = "tiny-digits-mlp"
objective = "Improve the local benchmark without editing tests."
code_root = "examples/full_pipeline_tiny_mlp/project"
task_file = "examples/full_pipeline_tiny_mlp/task.md"

[implementation]
mode = "patch_existing"        # auto | patch_existing | generate_project | template
domain_profile = "ml_experiment" # auto | generic_research_experiment | code_experiment | ml_experiment | code_agent_eval
provider = "local"             # local | fake | local_llm | codex | claude_code | opencode | external_cli
agent_mode = "model"           # model | handoff | delegated_workspace
agent_model = ""               # 可选：覆盖外部 CLI 模型；留空则使用 CLI 默认模型
agent_binary = ""              # 可选：外部 provider 的 CLI binary/path
agent_args = []                # 可选：追加到外部 CLI 的参数
agent_timeout_sec = 600        # 单次 backend invocation timeout
task_handoff = "user_file"     # user_file | merge；merge 会把 task_file 与研究上下文合并
allow_external_agent = false   # 启动外部 CLI provider 前必须显式开启
max_repair_attempts = 1

[workspace]
mode = "copy"                  # copy | git_worktree | sparse_copy
reuse_source_venv = false
setup_hook = ""
include = []
exclude = []

[execution]
backend = "local"              # 当前 V2.5 foundation 路径先支持 local
command = "python benchmark.py"
timeout_sec = 60
stream_output = "auto"
allow_dependency_install = false

[resource]
max_runtime_sec = 60
max_files = 8
max_generated_lines = 700
max_memory_mb = 2048
allow_gpu = false

[evaluation]
primary_metric = "accuracy"
direction = "maximize"
required_metrics = ["accuracy", "macro_f1"]
success_criteria = ["primary metric should improve or avoid regression"]
metric_directions = { accuracy = "higher", macro_f1 = "higher", train_time_sec = "resource" }

[generation]
enabled = false                # greenfield 项目生成时设为 true；已有项目 patch 不需要打开
max_batches = 2
files_per_batch = 3
review_required = true
allow_fallback_scaffold = false # true 时，失败的 LLM 代码可被安全 scaffold 替换

[report]
# auto 会根据是否有实验结果选择 experiment 或 research_only 报告结构。
mode = "auto"                 # auto | research_only | experiment

# 内置模板名或自定义 Markdown 路径。auto 会把 research_only 映射到 survey，
# 把 experiment 映射到 experiment。
template = "auto"             # auto | survey | experiment | reproduction | path/to/template.md

# 内置 reviewer criteria 或自定义 Markdown 路径。auto 跟随 template。
criteria = "auto"

# 报告语气提示。
style = "paper"               # paper | technical | concise

# 默认保持紧凑；需要逐节草稿和完整 trace 时再打开。
draft_sections = false
debug_artifacts = false

# V2.4 本地路径以 LLM writer/reviewer 为报告质量主体。
agent = "llm"                 # llm | disabled
reviewer = "llm"              # llm | disabled
max_review_iterations = 2
max_section_tokens = 1200
max_report_tokens = 5000
# 0 表示每节都暴露全部已选论文级 handles；正数用于控制 prompt 长度。
max_section_sources = 8
source_strategy = "full"       # full | batch_refine
source_batch_size = 10
max_source_batches = 0         # 0 表示使用全部批次
review_source_batches = false  # true 表示每个 batch_refine 批次后都审查
review_trace = "meta"         # off | meta | full

# 报告写入策略：
# - overwrite：覆盖 08-report/report.md 及配套产物。
# - archive：覆盖前把旧报告包复制到 08-report/archives/<label>。
# - variant：在已有 report.md 时写入 08-report/variants/<label>，
#   不替换当前主报告。
output_mode = "overwrite"     # overwrite | archive | variant
output_label = ""             # archive/variant 的可选目录标签

# 允许 writer/reviewer 在预算内只读回查当前 run 的 source handles。
allow_source_backtracking = true
max_backtracking_calls = 8
max_backtracking_tokens = 6000

[report.audit]
citations = true
metrics = true
claims = true
strict = false

[code_task]
# 源项目会被复制或 worktree 准备到 code_task/workspace。
code_root = "examples/full_pipeline_tiny_mlp/project"

# code-task 的任务说明文件。
task_file = "examples/full_pipeline_tiny_mlp/task.md"

# standalone code-task run 的父目录。
output_root = "runs"

# 可选展示名，用于 run 目录和 manifest。
name = "tiny-digits-mlp-pipeline"

[benchmark]
# 在隔离 workspace 中 patch 前后执行的命令。
command = "python benchmark.py"

# before/after objective verdict 使用的主指标。
primary_metric = "accuracy"

[benchmark.metric_directions]
# higher/lower 可决定 improved/regressed；resource 只展示，不决定成功。
accuracy = "higher"
macro_f1 = "higher"
train_time_sec = "resource"
inference_time_ms = "resource"
params = "resource"

[environment]
# current 使用当前 SimpleAutoResearch Python；external 使用配置的 python 路径。
mode = "current"              # current | external
# python = "C:/path/to/python.exe"

# workspace 设置只在上面的统一 [workspace] section 声明一次，并复用于
# embedded code-task 兼容层。

[edit_scope]
# automated edits 可触碰的 workspace-relative glob allowlist。
# 空列表表示所有非 protected workspace 文件都可作为候选。
allowed_patterns = ["digits_mlp/**"]

# 在默认 protected patterns 之外，额外作为只读证据的路径。
protected_patterns = ["configs/locked/**"]

[safety]
# copy/sparse 模式最多复制多大的源文件；0 表示禁用限制。
max_file_bytes = 2000000

# 静态 validation 最多扫描多大的文件。
validation_max_file_bytes = 500000

[execute]
# 状态感知 executor 最多推进到哪一步。
to_step = "run"

# false 会关闭 LLM，使用 deterministic fallback；true 会调用模型。
use_llm = true

# executor 管理的 baseline/patched benchmark timeout 秒数。
timeout_sec = 60

# 即使静态 validation 未通过，也继续运行 benchmark。
skip_validation = false

# 将较高风险 validation warning 视为 error。
strict_validation = false
validation_max_file_bytes = 500000

# 实时 benchmark 输出转发模式。
stream_benchmark_output = "off"     # off | line | auto | summary

# review-first 流程建议保持 false；审核 proposal 后再显式应用。
apply_proposed_edits = false

# 允许超过 normal 预算但落在 large 预算内的 proposal。
allow_large_edits = false

# 真实 LLM run 建议保持 false：如果 work-plan / patch-plan 的模型输出格式坏了，
# execute 会停止并允许你重跑同一条命令，而不是静默写入 deterministic fallback plan。
allow_planning_fallback = false

# work-plan / patch-plan 的 LLM 重试次数；全部失败后才停止或显式 fallback。
llm_retry_attempts = 2

# 失败后最多生成多少轮 bounded repair proposal。
repair_rounds = 1

# plan/proposal/repair 放入 LLM 上下文的文件数和单文件字符预算。
max_files = 8
max_source_chars_per_file = 4000

[models]
# code-task 模型路由的全局 fallback 模型。
default = "gpt-4o-mini"

[models.code_task]
# 可选分角色模型路由；空值会回退到 [models].default、[llm].model 或 SIMPLE_AR_MODEL。
planner = "gpt-4o-mini"
editor = "gpt-4o-mini"
repair = "gpt-4o-mini"
summarizer = "gpt-4o-mini"

[budget]
# 当前 edit budget profile；normal 保守，large 需要显式审核后应用。
profile = "normal"            # normal | large | absolute

# 单个 code task 中 execute 最多创建多少个实现批次。
max_batches = 3

# provider usage 提供费用估计时可用的成本上限。
cost_cap_usd = 2.0

[budget.normal]
# 模型返回结构化 JSON 后，本地会强制检查这些 edit proposal 限制。
max_files = 2
max_edits = 4
max_old_chars = 3000
max_new_chars = 4000
max_total_edit_chars = 12000
max_proposal_chars = 24000

[budget.large]
# 更大的人工审核 profile，用于多文件修改。
max_files = 4
max_edits = 8
max_old_chars = 7000
max_new_chars = 12000
max_total_edit_chars = 24000
max_proposal_chars = 42000
```

## Section 参考

| Section | 使用方 | 含义 |
| --- | --- | --- |
| `[run]` | `run`, `resume` | topic、输出目录、阶段范围和 quiet 模式。 |
| `[llm]` | pipeline 和 code task | LLM 是否启用、默认模型和 worker 数。 |
| `[search]` | `02-search` | provider 行为、fallback 策略、结果数量和手动 query。 |
| `[research]` | `02-search` | research-question 规划、query expansion、provider 顺序、本地文档、cache/index hints。 |
| `[research.budget]` | `02-search` 和后续 evidence stages | research planning 使用的轻量预算上限；只有启用 debug artifacts 时才会保留到 `planning/research_plan.json`。 |
| `[retrieval]` | read/synthesize/report helpers | 本地 artifact retrieval 上下文。 |
| `[experiment]` | `05-design` 到 `07-run` | 实验模板、timeout 和可选嵌套 code-task config 路径。 |
| `[task]` | `05-design` 和后续 implementation stages | 统一任务身份、目标、可选任务文件和可选源码根目录。 |
| `[implementation]` | `05-design` 和 `06-code` | 代码如何产生或修改：已有项目 patch、固定模板，或受控 greenfield 生成。 |
| `[workspace]` | code-task init 和统一任务配置 | workspace 策略和 setup metadata。 |
| `[execution]` | design/run/code-task 兼容层 | 执行后端、命令、timeout、输出流和依赖安装策略。 |
| `[resource]` | design 和后续 implementation gates | 运行时间、文件数、生成行数、内存和 GPU 预算。 |
| `[evaluation]` | design、comparison、report | 主指标、方向、必需指标和成功条件。 |
| `[generation]` | greenfield path | 分批/文件生成预算和 review 策略。 |
| `[report]` | `08-report` | 报告结构模式。 |
| `[code_task]` | standalone 或 embedded code task | 源项目、任务文件、输出目录和展示名。 |
| `[benchmark]` | code task | benchmark command 和主指标。 |
| `[benchmark.metric_directions]` | code task comparison | 指标解释规则。 |
| `[metrics]` | code task comparison | `primary`、`primary_metric`、`directions` 或 `metric_directions` 的替代位置。 |
| `[environment]` | code task execution | Python 执行策略。 |
| `[edit_scope]` | code-task init 和全部 patch gates | 可选 editable allowlist 和额外 read-only patterns。 |
| `[safety]` | code-task init/validation | 复制大小和 validation 扫描限制。 |
| `[execute]` | code-task execute | 状态机限制、运行设置、repair 轮数和输出流模式。 |
| `[models]` | code-task execute | 默认模型路由。 |
| `[models.code_task]` | code-task execute | planner/editor/repair/summarizer 模型路由。 |
| `[budget]` | code-task execute | edit budget profile、batch cap 和 cost cap。 |
| `[budget.normal]`, `[budget.large]` | code-task execute | 分 profile 的 edit proposal 限制。 |

## 关键字段说明

### Research Pipeline 字段

| 字段 | 含义 |
| --- | --- |
| `[run].topic` | 用户的主要研究/实验目标，会影响 planning、默认搜索 query 和报告表述。 |
| `[run].from_stage` / `[run].to_stage` | 部分运行的阶段范围。可用于停在 `synthesize`、只重跑 `report`，或 resume 某一段。 |
| `[run].debug_artifacts` | 是否保留详细诊断和草案交接文件，例如 search planning、provider traces、retrieval-selection rows、coverage review、section tables、debug cards、旧 evidence-pack 诊断产物，以及 design 阶段的 tool contracts、evidence review、eval report 和 retention policy。默认 false 会保持精简输出；核心产物仍按阶段保留：`02-search` documents/chunks、`03-read` review/Paper Brief、`04-synthesize` synthesis brief/Markdown、`05-design` experiment contracts。 |
| `[run].overwrite_stage_artifacts` | 默认 `false`。从 `06-code` 或 `07-run` 重跑时，会先把旧的关键代码/运行产物复制到 `archives/<timestamp>/`，再写入新产物。只有明确想无归档覆盖旧产物时才设为 `true`。 |
| `[llm].enabled` | 是否启用 LLM 支持的 planning、notes、synthesis、report 和 code-task 步骤。真实 code-task 通常需要 LLM 才有实际意义。 |
| `[llm].workers` | 支持并发的 LLM 阶段使用的 worker 数；并不代表所有 pipeline 阶段都会并发。 |
| `[search].offline` | 跳过 live literature provider，适合本地 demo 和 deterministic test。 |
| `[search].max_papers` | search 阶段最多请求/保留多少条 metadata 记录，是总记录上限，不是 PDF 页数或 chunk 上限。 |
| `[search].query` | 手动 provider query。省略时使用 topic 或 research queries 中的第一个可用 query。 |
| `[search].allow_fixture_fallback` | live/cache 搜索失败后是否允许使用 placeholder fixture metadata。认真收集证据时建议保持 false。 |
| `[search].strict` | 搜索无法产出真实或 cached 结果时直接失败。用于避免 fixture fallback 掩盖坏 run。 |
| `[retrieval].top_k` | 启用 artifact retrieval 时，后续 prompt 检索多少个本地 artifact chunk。 |
| `[report].mode` | `auto` 根据是否存在实验结果选择报告结构；`research_only` 避免实验结论；`experiment` 要求有结果证据。 |
| `[report].template` | 内置报告模板名（`survey`、`experiment`、`reproduction`）或自定义 Markdown 路径。`auto` 跟随 `mode`。 |
| `[report].criteria` | 内置 reviewer criteria 或自定义 Markdown 路径。`auto` 跟随 `template`。 |
| `[report].style` | 报告语气提示：`paper`、`technical` 或 `concise`。 |
| `[report].draft_sections` | 是否把 Writer Agent 的分节草稿保留到 `08-report/sections/`；默认 false 保持紧凑输出。 |
| `[report].debug_artifacts` | 是否把 reviewer findings、tool results 和 iteration traces 保留到 `08-report/audit/` 与 `08-report/iterations/`；默认 false。 |
| `[report].agent` / `[report].reviewer` | 报告 writer/reviewer 后端。V2.4 本地路径建议用 LLM；disabled 只是 fallback。 |
| `[report].max_review_iterations` | writer/reviewer 最多修订轮数。 |
| `[report].max_section_tokens` / `[report].max_report_tokens` | section 起草和最终报告组装的 token 预算。 |
| `[report].max_section_sources` | 每个 section plan 分配给模型的最多 source handles。`0` 表示暴露全部已选论文级 handles；全文 chunks 仍通过有界 backtracking tools 按需回查。 |
| `[report].source_strategy` | `full` 表示每节一次性使用配置的 source set；`batch_refine` 表示把较大的 source set 分批，并在同一 section 草稿上逐批增量修订。 |
| `[report].source_batch_size` | `source_strategy = "batch_refine"` 时每批 source handles 数量。 |
| `[report].max_source_batches` | `batch_refine` 下每节最多处理多少批；`0` 表示处理全部批次。 |
| `[report].review_source_batches` | 为 true 时，每个 `batch_refine` 批次整合后都会调用 reviewer。质量控制更强，但 LLM 成本更高。 |
| `[report].output_mode` / `.output_label` | 控制重跑 08-report 时的写入方式：直接覆盖、覆盖前归档旧报告，或写一份不覆盖主报告的 variant。 |
| `[report].review_trace` | reviewer trace 保留策略：`off`、`meta` 或 `full`。 |
| `[report].allow_source_backtracking` | 是否允许 report tools 在当前 run 的 source handles 中有界回查更多证据。 |
| `[report].max_backtracking_calls` / `[report].max_backtracking_tokens` | source backtracking 调用次数和返回 token 预算。 |
| `[report.audit].citations` / `.metrics` / `.claims` | 启用 citation、metric 和 claim audit 组件。 |
| `[report.audit].strict` | 后续 strict mode 可把 warning 作为阻断条件；默认 false。 |

### 统一实验与代码字段

V2.5 foundation 起，新 pipeline config 推荐优先使用这些 section。它们会被归一成
`task_config`，同时在需要时映射到旧 code-task key，因此现有 embedded
`code_task_project` 仍能继续运行。

| 字段 | 含义 |
| --- | --- |
| `[task].kind` | `existing_project` 表示已有源码项目和受控 patch；`greenfield` / `benchmark_solution` 使用受控项目生成路径。 |
| `[task].code_root` / `.task_file` | 源项目根目录和任务 Markdown；路径相对配置文件解析。 |
| `[implementation].mode` | `patch_existing` 映射到当前受控 code-task 行为；`generate_project` 会在 `06-code/generated_project` 下规划、生成、审查并运行一个受控项目。 |
| `[implementation].domain_profile` | 选择规划默认值，例如 `generic_research_experiment`、`code_experiment`、`ml_experiment` 或 `code_agent_eval`。 |
| `[implementation].provider` | 代码实现 backend。`local` 是默认的进程内路径；`fake` 是 deterministic 测试 backend；`local_llm` 通过当前 LLM 写出有边界的 review 产物；`codex`、`claude_code`、`opencode` 和 `external_cli` 会走 V2.6 外部 agent handoff 边界，除非 `[implementation].allow_external_agent = true`，否则不会启动外部 CLI。 |
| `[implementation].agent_mode` | 选择 backend 能接管多少实现循环。`model` 表示仍由 SimpleAutoResearch 拥有 harness，只把有界文本/代码生成交给模型或本地 backend；`handoff` 会写出可审计的 `agent_handoff/<name>/` package，并从外部 agent 收集 candidate files；`delegated_workspace` 是未来让外部 harness 接管 workspace loop 的强路径，现在只识别并显式失败，不会静默降级。 |
| `[implementation].agent_model` / `.agent_binary` / `.agent_args` / `.agent_timeout_sec` | 可选外部 backend 启动设置。`agent_model` 留空时会使用 Codex/Claude/OpenCode CLI 当前账号配置的默认模型；只有确认该 CLI/账号支持某个模型名时才建议显式填写。`agent_binary` 可指定自定义 executable path，也可用于 generic `external_cli`；`agent_args` 会追加 CLI 参数；`agent_timeout_sec` 限制单次 backend 调用时间。 |
| `[implementation].task_handoff` | 仅用于 8 阶段内嵌已有项目代码任务。`user_file` 会原样使用 `[task].task_file`；`merge` 会在 `05-design/generated_code_task.md` 中把用户任务作为硬约束，并融合 goal/problem/synthesis/hypothesis 上下文。 |
| `[implementation].allow_external_agent` | 是否允许启动可选外部 CLI，用于 agent-backed generation 或 repair。外部输出会先进入 `agent_outputs/<name>/`，仍需通过 SimpleAutoResearch 的 review、result guard 或 code-task validation 后才会影响结果。普通本地运行建议保持 false。 |
| `[execution].command` / `.timeout_sec` | benchmark/execution 规划使用的命令和 timeout；已有项目会映射到旧 benchmark 设置。 |
| `[resource].max_files` / `.max_generated_lines` | 写入 `05-design/resource_plan.json` 的代码前预算。 |
| `[resource].max_memory_mb` / `.allow_gpu` | runtime 资源预算，会写入 `resource_plan.json`，并在生成或修改代码前作为 contract constraints 暴露。 |
| `[evaluation].primary_metric` / `.metric_directions` | 写入 `05-design/result_schema.json` 的结果 schema 和指标方向；现有 code-task comparison 也会消费这些字段。 |
| `[evaluation].required_metrics` / `.success_criteria` | 必需指标检查和成功条件说明，会被 `07-run/guard_report.json` 和最终报告使用。 |
| `[generation].enabled` | 启用 greenfield 项目生成路径；已有项目 code-task 运行保持 false。 |
| `[generation].max_batches` / `.files_per_batch` / `.review_required` | 后续项目生成路径的计划提示；已有项目 patch run 只记录用于审计。 |
| `[generation].allow_fallback_scaffold` | 默认 false。false 时，生成代码失败会保留产物供检查，而不是静默替换成 deterministic scaffold。 |

`05-design` 会把这些字段落成 `experiment_plan.json`、`experiment_contract.json`、
`result_schema.json`、`resource_plan.json`、`dependency_plan.json`、
`domain_profile.json` 和 `contract_validation.json`。如果
`contract_validation.json` 报告失败，`06-code` 会拒绝继续进入代码阶段。
`07-run/results.json` 是实验结果的 canonical 入口，会集中记录指标、执行 provenance、comparison/verdict，以及 `resource_plan.json`、`code_review.json`、`guard_report.json` 和 `diagnosis.json` 的紧凑证据信号。`diagnosis.json` 会把 guard/code-review/runtime 问题整理成可读诊断和修复建议；报告阶段应引用这组 canonical 结果，而不是直接从 stdout 猜测实验结论。

### Evidence Source 字段

| 字段 | 含义 |
| --- | --- |
| `[research].mode` | 记录计划中的 evidence 深度：`lite` 表示 metadata/本地笔记，`standard` 表示 cache/index-ready，`strong` 预留给全文/向量工作流。 |
| `[research].planner` | research-question 和 query-expansion 后端。`auto` 会在 `[llm].enabled = true` 时调用 LLM，并在 provider 不可用时回退；`llm` 显式要求走该路径；`deterministic` 禁用额外 LLM planner 调用。 |
| `[research].sources` | search 阶段 provider 顺序。当前 connector 支持 `openalex`、`semantic_scholar`、`arxiv` 和 `local_files`；`fixture` 用于记录离线 fixture。 |
| `[research].queries` | 作为 research planner 的 seed queries。Search 会按 ordered-fallback rounds 执行 planned queries，并可把后续轮次预算用于未覆盖 facets。LLM planner 还会记录带 title/abstract keyword hints 的 `query_specs`；完整 plan 只在启用 debug artifacts 时保留。 |
| `[research].auto_query_expansion` | 是否启用 facet-driven follow-up queries。deterministic 模式下为规则扩展；LLM planner 模式下模型可以在相同 query 预算内补充更强术语。想完全使用手写 query 时可以设为 false。 |
| `[research].max_retrieval_rounds` | DeepResearch loop 计划运行的 retrieval/screening 轮数。大于 `1` 时允许在最终写出 `papers.jsonl` 前执行 coverage-driven follow-up retrieval。 |
| `[research].max_queries` | 内部 `query_plan` 中最多保留多少个 seed + expanded queries。 |
| `[research].required_facets` | 希望覆盖的 evidence facets，例如 `method`、`benchmark`、`dataset`、`code_link` 或 `limitation`。这些会驱动 research questions 和 query expansion。 |
| `[research].local_documents` | 作为本地研究记录读取的 Markdown/text 文件，路径相对配置文件解析，并会写入 `02-search/documents/documents.jsonl`，记录 parser/hash 状态。 |
| `[research].use_fulltext` | 全文 evidence 工作流的意图开关。开启后，`documents/fulltext_manifest.json` 会在预算内选择可用的本地/远程全文 hint，`documents/fulltext_extraction.json` 会记录已缓存/本地输入的 parser 结果。 |
| `[research].allow_pdf_download` | 受控远程 PDF 获取步骤的权限开关。除非明确需要 parser-backed full-text handling，否则保持 false。 |
| `[research].max_fulltext_documents` | 全文获取/解析最多选择多少篇文档。它不同于 `[research.budget].max_documents`，后者控制保留多少条 metadata/document record。 |
| `[research].max_pdf_mb` | 单个 PDF 的大小上限。超过该限制的本地 PDF 会被跳过，后续远程下载器也应遵守这个限制。 |
| `[research].keep_raw_pdf` | fetch/parser 是否保留原始 PDF。只需要 parsed text 和 section chunks 时建议保持 false。 |
| `[research].parser_backend` | parser 后端。`basic` 直接解析 Markdown/text 和基础 HTML；`pypdf` 使用轻量 PDF parser；`unstructured` 是可选的更强文档解析后端，未安装时只会在 manifest 中记录失败状态。 |
| `[research].read_screening` | Read 阶段 review 后端。`auto` 会在 `[llm].enabled = true` 时使用 LLM 两步式粗筛/重排；`llm` 显式要求该路径；`deterministic` 跳过额外 LLM review，按检索顺序保留。 |
| `[research].read_batch_size` | 每个粗筛 prompt 放入几篇论文的 title/abstract。值越小越精细但 LLM 调用更多；默认 `4`，限制在 `1..8`。 |
| `[research].read_workers` | 粗筛批次的并发 LLM worker 数。默认取 `3` 和 `[llm].workers` 的较小值，用来避免 50+ 篇论文时塞进一个巨大 prompt。 |
| `[research].read_max_shortlist` | 粗筛和重排后进入深入 Paper Brief 与 synthesis 的论文上限。省略时，小检索集默认全保留，大检索集默认使用有界 shortlist。 |
| `[research].cache` | live provider 失败后是否允许使用 cached metadata。 |
| `[research].index_backend` | 本地索引后端。`keyword` 只写可移植 chunks；`sqlite_fts` / `hybrid` 会更新共享 SQLite FTS store；`lancedb` / `hybrid_lancedb` 会更新共享可选 LanceDB store，未安装 LanceDB 时只记录状态，不影响 `chunks.jsonl`。 |
| `[research].index_root` | SQLite FTS / LanceDB 的共享加速索引目录。默认 `.simple_ar_cache/research_index`，也可通过 `SIMPLE_AR_RESEARCH_INDEX_ROOT` 覆盖。只有明确需要每个 run 自己保存数据库时，才设为 `run` 或 `local`。 |
| `[research.budget].max_documents` | evidence 阶段从所有 source 中最多保留多少条记录。 |
| `[research.budget].max_chunks` | 后续全文/本地文档 ingestion 后最多保留多少 chunk。 |
| `[research.budget].max_context_tokens` | evidence retrieval 放入 prompt 的计划 token 预算。 |
| `[research.budget].max_llm_calls` | research 侧 query expansion、screening 等 LLM 操作的计划调用上限。 |
| `[research.budget].max_follow_up_queries` | 第二轮 coverage-driven follow-up retrieval 最多尝试几个 query。 |
| `[research.budget].novelty_backend` | `04-synthesize/synthesis_brief.json` 内 novelty hints 的 backend。当前稳定值是 `local`，只基于当前 Paper Brief 做词面重合风险提示。 |

### Code-Task 字段

| 字段 | 含义 |
| --- | --- |
| `[experiment].template` | `code_task_project` 会把 code-task workflow 嵌入 8 阶段 pipeline；其他模板多为教学/demo 路径。 |
| `[experiment].timeout` | stage `07-run` 的 timeout；对 embedded code task，也会约束嵌套 benchmark 调用。 |
| `[experiment].code_task_config` | 可选 standalone code-task TOML 路径。想把 pipeline 和 code-task 配置拆开时使用。 |
| `[code_task].code_root` | 源项目路径。原始项目不会被直接修改，系统会在 run 目录下准备 workspace。 |
| `[code_task].task_file` | 用户任务说明。standalone `code-task init` 必填；内嵌 8 阶段 run 可在省略时自动生成，也可在 `[implementation].task_handoff = "merge"` 时与研究上下文融合。 |
| `[benchmark].command` | 在 `code_task/workspace` 中 patch 前后运行的命令。建议输出 `accuracy: 0.82` 这类可解析指标。 |
| `[benchmark].primary_metric` | objective verdict 使用的主指标。未知指标仍会记录，但最好声明方向。 |
| `[benchmark.metric_directions]` | 指标方向表：`higher`、`lower`、`resource` 或 `ignore`。 |
| `[environment].mode` | `current` 使用当前 SimpleAutoResearch Python；`external` 使用 `[environment].python`。不会自动安装依赖。 |
| `[workspace].mode` | workspace 策略：`copy`、`git_worktree` 或 `sparse_copy`。 |
| `[workspace].reuse_source_venv` | 检测到 source `.venv` 或 `venv` 时，是否记录并使用其中 Python。 |
| `[workspace].setup_hook` | 为未来 managed environment 支持预留记录；init 阶段不执行。 |
| `[edit_scope].allowed_patterns` | automated edits 可以修改的 workspace-relative glob allowlist。空列表表示所有 normalized、非 protected workspace 路径都可编辑。 |
| `[edit_scope].protected_patterns` | 额外只读路径。tests、benchmark、`.env`、secret/credential-like 路径等默认 protected patterns 始终保留。 |
| `[edit_scope].mode` | 可选标签，写入 `manifest.json` 供审计使用；它本身不改变行为。 |
| `[safety].max_file_bytes` | copy/sparse 模式最大复制文件大小，避免误复制大模型、数据或 checkpoint。 |
| `[safety].validation_max_file_bytes` | 静态 validation 扫描文件大小上限。 |

### Execute 与 Budget 字段

| 字段 | 含义 |
| --- | --- |
| `[execute].to_step` | 状态感知 executor 最多推进到哪一步。例如设为 `propose-edits` 可停在应用补丁之前。 |
| `[execute].use_llm` | 是否启用 LLM 支持的 work-plan、patch-plan、edit-proposal 和 repair 步骤。 |
| `[execute].timeout_sec` | executor 管理的 baseline 和 patched benchmark timeout。 |
| `[execute].stream_benchmark_output` | 实时 benchmark log 模式：`off`、`line`、`auto` 或 `summary`。有 tqdm 这类进度条时建议 `auto`。 |
| `[execute].apply_proposed_edits` | 允许 execute 应用已经审核过的 proposal。review-first 流程建议保持 false。 |
| `[execute].allow_large_edits` | 允许应用超过 normal 预算但落在 large 预算内的已审核 proposal。 |
| `[execute].allow_planning_fallback` | LLM work-plan / patch-plan 重试后仍失败时，是否允许写入 deterministic fallback plan。真实 LLM run 建议保持 false，这样坏输出会安全停止并可重跑。 |
| `[execute].llm_retry_attempts` | work-plan / patch-plan 的 LLM 尝试次数。 |
| `[execute].repair_rounds` | validation/benchmark 失败后最多生成几轮 bounded repair proposal；repair 仍需审核。 |
| `[execute].max_files` | plan/proposal/repair 步骤纳入 LLM 上下文的最大文件数。 |
| `[execute].max_source_chars_per_file` | LLM 上下文中单个文件的 source snippet 字符预算。 |
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

## Research Source Variants

### Live OpenAlex/Semantic Scholar/arXiv Metadata

```toml
[search]
offline = false
max_papers = 10
query = "multi-agent collaboration for code generation"
allow_fixture_fallback = false
strict = false

[research]
mode = "standard"
planner = "auto"
sources = ["openalex", "semantic_scholar", "arxiv"]
queries = [
  "multi-agent collaboration for code generation",
  "LLM agents software engineering benchmark",
]
auto_query_expansion = true
max_retrieval_rounds = 2
max_queries = 6
required_facets = ["method", "benchmark", "dataset", "code_link"]
cache = true
index_backend = "keyword"
use_fulltext = false
allow_pdf_download = false
max_fulltext_documents = 6
max_pdf_mb = 20
keep_raw_pdf = false
parser_backend = "basic"
```

### Local Notes Only

```toml
[search]
offline = true
max_papers = 5

[research]
mode = "lite"
planner = "deterministic"
sources = ["local_files"]
queries = ["agent simulation evaluation"]
auto_query_expansion = true
max_retrieval_rounds = 1
max_queries = 4
required_facets = ["overview", "method", "benchmark"]
local_documents = [
  "../private_corpus/agent_simulation_notes.md",
]
cache = true
index_backend = "keyword"
```

### Offline Fixture Metadata

```toml
[search]
offline = true
max_papers = 3

[research]
mode = "lite"
planner = "deterministic"
sources = ["fixture"]
queries = ["tiny digits MLP baseline improvement"]
auto_query_expansion = true
max_retrieval_rounds = 1
max_queries = 4
```

## Workspace Mode Variants

### `copy`

`copy` 是最稳妥的默认模式，会在 `code_task/workspace` 下创建受保护的物理复制。

```toml
[workspace]
mode = "copy"
reuse_source_venv = false
setup_hook = ""

[safety]
max_file_bytes = 2000000
```

### `git_worktree`

`git_worktree` 会为 repo-root git 项目创建 detached worktree。源项目必须是本地 git 仓库，并至少有一次 commit；不需要连接远程 GitHub 仓库。

```toml
[workspace]
mode = "git_worktree"
reuse_source_venv = true
setup_hook = ""
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
setup_hook = ""

[safety]
max_file_bytes = 2000000

[execute]
to_step = "run"
use_llm = true
timeout_sec = 120
repair_rounds = 1
stream_benchmark_output = "auto"
apply_proposed_edits = false
allow_large_edits = false
allow_planning_fallback = false
llm_retry_attempts = 2

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

## 内嵌 Code-Task Config

对 `simple-ar run --config PATH`，可以把 code-task 设置放在同一个 run config，也可以指向单独的 code-task config：

```toml
[experiment]
template = "code_task_project"
timeout = 120
code_task_config = "../code_tasks/configs/my_project.toml"
```

如果省略 `[experiment].code_task_config`，但 run config 中包含 `[code_task]`、`[benchmark]`、`[metrics]`、`[environment]`、`[safety]` 或 `[edit_scope]`，则 run config 自身会被复用为 embedded code-task config。

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
apply_proposed_edits = false
allow_large_edits = false
allow_planning_fallback = false
llm_retry_attempts = 2

[models.code_task]
planner = "gpt-4o-mini"
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
