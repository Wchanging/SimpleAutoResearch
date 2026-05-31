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
- 当 run config 包含 `[code_task]`、`[benchmark]`、`[metrics]`、`[environment]`、`[workspace]` 或 `[safety]` 时，同一文件可被复用为 embedded code-task config。

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

# true 会在 run 目录中保留 planning、retrieval trace、screening 和 coverage review 等诊断目录；
# 默认 false 只压缩诊断文件。documents、全文 manifest、chunks 和 cards 这类后续阶段需要的 evidence 产物仍会保留。
debug_artifacts = false

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
# 写入 planning/research_plan.json 的检索策略档位；全文解析能力仍是后续规划。
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
# code_task_config = "examples/code_tasks/configs/tiny_digits_mlp.toml"

[report]
# auto 会根据是否有实验结果选择 experiment 或 research_only 报告结构。
mode = "auto"                 # auto | research_only | experiment

[code_task]
# 源项目会被复制或 worktree 准备到 code_task/workspace。
code_root = "examples/code_tasks/tiny_digits_mlp_project"

# code-task 的任务说明文件。
task_file = "examples/code_tasks/tasks/improve_tiny_digits_mlp.md"

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

[workspace]
# copy 最稳；git_worktree 适合 git repo root；sparse_copy 只复制 allowlist。
mode = "copy"                 # copy | git_worktree | sparse_copy

# 如果检测到 source .venv/venv，是否复用其 Python；不会自动安装依赖。
reuse_source_venv = false

# 为未来 managed environment 记录；init 不会执行这个命令。
setup_hook = ""

[safety]
# copy/sparse 模式最多复制多大的源文件；0 表示禁用限制。
max_file_bytes = 2000000

# 静态 validation 最多扫描多大的文件。
validation_max_file_bytes = 500000

[execute]
# 状态感知 executor 最多推进到哪一步。
to_step = "run"

# false 时 code-task LLM 步骤尽量使用 deterministic fallback。
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

## Section Reference

| Section | 使用方 | 含义 |
| --- | --- | --- |
| `[run]` | `run`, `resume` | topic、输出目录、阶段范围和 quiet 模式。 |
| `[llm]` | pipeline 和 code task | LLM 是否启用、默认模型和 worker 数。 |
| `[search]` | `02-search` | provider 行为、fallback 策略、结果数量和手动 query。 |
| `[research]` | `02-search` | research-question 规划、query expansion、provider 顺序、本地文档、cache/index hints。 |
| `[research.budget]` | `02-search` 和后续 evidence stages | 写入 `planning/research_plan.json` 的轻量预算上限。 |
| `[retrieval]` | read/synthesize/report helpers | 本地 artifact retrieval 上下文。 |
| `[experiment]` | `05-design` 到 `07-run` | 实验模板、timeout 和可选嵌套 code-task config 路径。 |
| `[report]` | `08-report` | 报告结构模式。 |
| `[code_task]` | standalone 或 embedded code task | 源项目、任务文件、输出目录和展示名。 |
| `[benchmark]` | code task | benchmark command 和主指标。 |
| `[benchmark.metric_directions]` | code task comparison | 指标解释规则。 |
| `[metrics]` | code task comparison | `primary`、`primary_metric`、`directions` 或 `metric_directions` 的替代位置。 |
| `[environment]` | code task execution | Python 执行策略。 |
| `[workspace]` | code-task init | workspace 模式和 setup metadata。 |
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
| `[run].debug_artifacts` | 是否保留 search 阶段的诊断目录，例如 planning、trace、screening 和 coverage review。默认 false 只压缩诊断文件；documents、全文 manifest、chunks 和 cards 仍会作为 evidence 产物保留。 |
| `[llm].enabled` | 是否启用 LLM 支持的 planning、notes、synthesis、report 和 code-task 步骤。真实 code-task 通常需要 LLM 才有实际意义。 |
| `[llm].workers` | 支持并发的 LLM 阶段使用的 worker 数；并不代表所有 pipeline 阶段都会并发。 |
| `[search].offline` | 跳过 live literature provider，适合本地 demo 和 deterministic test。 |
| `[search].max_papers` | search 阶段最多请求/保留多少条 metadata 记录，是总记录上限，不是 PDF 页数或 chunk 上限。 |
| `[search].query` | 手动 provider query。省略时使用 topic 或 research queries 中的第一个可用 query。 |
| `[search].allow_fixture_fallback` | live/cache 搜索失败后是否允许使用 placeholder fixture metadata。认真收集证据时建议保持 false。 |
| `[search].strict` | 搜索无法产出真实或 cached 结果时直接失败。用于避免 fixture fallback 掩盖坏 run。 |
| `[retrieval].top_k` | 启用 artifact retrieval 时，后续 prompt 检索多少个本地 artifact chunk。 |
| `[report].mode` | `auto` 根据是否存在实验结果选择报告结构；`research_only` 避免实验结论；`experiment` 要求有结果证据。 |

### Evidence Source 字段

| 字段 | 含义 |
| --- | --- |
| `[research].mode` | 记录计划中的 evidence 深度：`lite` 表示 metadata/本地笔记，`standard` 表示 cache/index-ready，`strong` 预留给全文/向量工作流。 |
| `[research].planner` | research-question 和 query-expansion 后端。`auto` 会在 `[llm].enabled = true` 时调用 LLM，并在 provider 不可用时回退；`llm` 显式要求走该路径；`deterministic` 禁用额外 LLM planner 调用。 |
| `[research].sources` | search 阶段 provider 顺序。当前 connector 支持 `openalex`、`semantic_scholar`、`arxiv` 和 `local_files`；`fixture` 用于记录离线 fixture。 |
| `[research].queries` | 作为 seed queries 写入 `02-search/planning/research_plan.json`。Search 会按 ordered-fallback rounds 执行 planned queries，并可把后续轮次预算用于未覆盖 facets。LLM planner 还会记录带 title/abstract keyword hints 的 `query_specs`。 |
| `[research].auto_query_expansion` | 是否启用 facet-driven follow-up queries。deterministic 模式下为规则扩展；LLM planner 模式下模型可以在相同 query 预算内补充更强术语。想完全使用手写 query 时可以设为 false。 |
| `[research].max_retrieval_rounds` | DeepResearch loop 计划运行的 retrieval/screening 轮数。大于 `1` 时允许在最终写出 `papers.jsonl` 前执行 coverage-driven follow-up retrieval。 |
| `[research].max_queries` | `planning/research_plan.json` 的 `query_plan` section 中最多保留多少个 seed + expanded queries。 |
| `[research].required_facets` | 希望覆盖的 evidence facets，例如 `method`、`benchmark`、`dataset`、`code_link` 或 `limitation`。这些会驱动 research questions 和 query expansion。 |
| `[research].local_documents` | 作为本地研究记录读取的 Markdown/text 文件，路径相对配置文件解析，并会写入 `02-search/documents/documents.jsonl`，记录 parser/hash 状态。 |
| `[research].use_fulltext` | 全文 evidence 工作流的意图开关。开启后，`documents/fulltext_manifest.json` 会在预算内选择可用的本地/远程全文 hint，`documents/fulltext_extraction.json` 会记录已缓存/本地输入的 parser 结果。 |
| `[research].allow_pdf_download` | 受控远程 PDF 获取步骤的权限开关。除非明确需要 parser-backed full-text handling，否则保持 false。 |
| `[research].max_fulltext_documents` | 全文获取/解析最多选择多少篇文档。它不同于 `[research.budget].max_documents`，后者控制保留多少条 metadata/document record。 |
| `[research].max_pdf_mb` | 单个 PDF 的大小上限。超过该限制的本地 PDF 会被跳过，后续远程下载器也应遵守这个限制。 |
| `[research].keep_raw_pdf` | fetch/parser 是否保留原始 PDF。只需要 parsed text 和 section chunks 时建议保持 false。 |
| `[research].parser_backend` | parser 后端。`basic` 直接解析 Markdown/text 和基础 HTML；`pypdf` 使用轻量 PDF parser；`unstructured` 是可选的更强文档解析后端，未安装时只会在 manifest 中记录失败状态。 |
| `[research].cache` | live provider 失败后是否允许使用 cached metadata。 |
| `[research].index_backend` | 本地索引后端。`keyword` 只写可移植 chunks；`sqlite_fts` / `hybrid` 会更新共享 SQLite FTS store；`lancedb` / `hybrid_lancedb` 会更新共享可选 LanceDB store，未安装 LanceDB 时只记录状态，不影响 `chunks.jsonl`。 |
| `[research].index_root` | SQLite FTS / LanceDB 的共享加速索引目录。默认 `.simple_ar_cache/research_index`，也可通过 `SIMPLE_AR_RESEARCH_INDEX_ROOT` 覆盖。只有明确需要每个 run 自己保存数据库时，才设为 `run` 或 `local`。 |
| `[research.budget].max_documents` | evidence 阶段从所有 source 中最多保留多少条记录。 |
| `[research.budget].max_chunks` | 后续全文/本地文档 ingestion 后最多保留多少 chunk。 |
| `[research.budget].max_context_tokens` | evidence retrieval 放入 prompt 的计划 token 预算。 |
| `[research.budget].max_llm_calls` | research 侧 query expansion、screening 等 LLM 操作的计划调用上限。 |
| `[research.budget].max_follow_up_queries` | 第二轮 coverage-driven follow-up retrieval 最多尝试几个 query。 |

### Code-Task 字段

| 字段 | 含义 |
| --- | --- |
| `[experiment].template` | `code_task_project` 会把 code-task workflow 嵌入 8 阶段 pipeline；其他模板多为教学/demo 路径。 |
| `[experiment].timeout` | stage `07-run` 的 timeout；对 embedded code task，也会约束嵌套 benchmark 调用。 |
| `[experiment].code_task_config` | 可选 standalone code-task TOML 路径。想把 pipeline 和 code-task 配置拆开时使用。 |
| `[code_task].code_root` | 源项目路径。原始项目不会被直接修改，系统会在 run 目录下准备 workspace。 |
| `[code_task].task_file` | 用户任务说明。standalone `code-task init` 必填；内嵌 8 阶段 run 可在省略时自动生成。 |
| `[benchmark].command` | 在 `code_task/workspace` 中 patch 前后运行的命令。建议输出 `accuracy: 0.82` 这类可解析指标。 |
| `[benchmark].primary_metric` | objective verdict 使用的主指标。未知指标仍会记录，但最好声明方向。 |
| `[benchmark.metric_directions]` | 指标方向表：`higher`、`lower`、`resource` 或 `ignore`。 |
| `[environment].mode` | `current` 使用当前 SimpleAutoResearch Python；`external` 使用 `[environment].python`。不会自动安装依赖。 |
| `[workspace].mode` | workspace 策略：`copy`、`git_worktree` 或 `sparse_copy`。 |
| `[workspace].reuse_source_venv` | 检测到 source `.venv` 或 `venv` 时，是否记录并使用其中 Python。 |
| `[workspace].setup_hook` | 为未来 managed environment 支持预留记录；init 阶段不执行。 |
| `[safety].max_file_bytes` | copy/sparse 模式最大复制文件大小，避免误复制大模型、数据或 checkpoint。 |
| `[safety].validation_max_file_bytes` | 静态 validation 扫描文件大小上限。 |

### Execute And Budget 字段

| 字段 | 含义 |
| --- | --- |
| `[execute].to_step` | 状态感知 executor 最多推进到哪一步。例如设为 `propose-edits` 可停在应用补丁之前。 |
| `[execute].use_llm` | 是否启用 LLM 支持的 work-plan、patch-plan、edit-proposal 和 repair 步骤。 |
| `[execute].timeout_sec` | executor 管理的 baseline 和 patched benchmark timeout。 |
| `[execute].stream_benchmark_output` | 实时 benchmark log 模式：`off`、`line`、`auto` 或 `summary`。有 tqdm 这类进度条时建议 `auto`。 |
| `[execute].apply_proposed_edits` | 允许 execute 应用已经审核过的 proposal。review-first 流程建议保持 false。 |
| `[execute].allow_large_edits` | 允许应用超过 normal 预算但落在 large 预算内的已审核 proposal。 |
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
  "../research/local_agent_simulation_notes.md",
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

## Standalone Code-Task Config

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

## Embedded Code-Task Config

对 `simple-ar run --config PATH`，可以把 code-task 设置放在同一个 run config，也可以指向单独的 code-task config：

```toml
[experiment]
template = "code_task_project"
timeout = 120
code_task_config = "../code_tasks/configs/my_project.toml"
```

如果省略 `[experiment].code_task_config`，但 run config 中包含 `[code_task]`、`[benchmark]`、`[metrics]`、`[environment]`、`[workspace]` 或 `[safety]`，则 run config 自身会被复用为 embedded code-task config。

## Execute And Budget

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

## Current Edit Scope Behavior

当前公开 TOML 还没有自定义 `[edit_scope]` allow/deny 配置。现有 code-task 仍会执行默认 edit-scope 基线：

- 源项目只会在 `code_task/workspace` 中被修改；
- tests、benchmark 文件、`.env` 和 secret/credential-like 路径会作为只读证据；
- work-plan target files 会限制后续 edit proposal；
- `apply-edits` 写文件前会再次检查 workspace-relative path、protected patterns、allowed target files 和 old-text 精确匹配。

可配置 allow/deny edit-scope 规则已纳入 V2.3 计划；在它进入本文档前，不应把它当成已实现能力。
