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
uv run simple-ar run --config examples/full_pipeline_tiny_mlp/configs/pipeline.toml
```

配置文件可以包含 `[run]`、`[llm]`、`[search]`、`[research]`、`[retrieval]`、`[experiment]`、`[report]`，也可以包含和 `code-task init --config` 相同的 `[code_task]`、`[benchmark]`、`[metrics]`、`[environment]`、`[workspace]`、`[safety]`。显式 CLI 参数会覆盖配置文件。完整示例和字段解释见 [配置参考](CONFIG_REFERENCE_zh.md#完整-pipeline-config)。

只做文献分析时，可以先停在 `synthesize`：

```bash
uv run simple-ar run --topic "toy topic" --to-stage synthesize
```

再从已有产物生成 literature-only report：

```bash
uv run simple-ar resume runs/<run-id> --from-stage report
```

如果当前报告已经比较满意，不想被下一次重写覆盖，可以写一份独立报告包：

```bash
uv run simple-ar resume runs/<run-id> --from-stage report --to-stage report \
  --report-output-mode variant --report-output-label survey-v2
```

新报告会写入 `08-report/variants/survey-v2/`，当前主报告
`08-report/report.md` 不会被替换。如果你希望主报告被更新，但覆盖前自动保留旧版本，可以使用
`--report-output-mode archive`，旧报告包会复制到 `08-report/archives/<label>/`。

默认 `report-mode` 是自动判断：如果没有 `results.json`，就写 research-only 结构；如果有实验结果，就写 experiment 结构。也可以强制指定：

```bash
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode research_only
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode experiment
```

#### 报告 source strategy

对于小到中等规模的文献集合，比较实用的默认方式是让 report agent 看到全部已选论文级 handles：

```toml
[report]
max_section_sources = 0
source_strategy = "full"
```

这不会把每篇论文的全部全文 chunk 都塞进每个 prompt。论文级 handles 只包含标题、摘要、短 citation key 和紧凑 metadata；如果启用了 source backtracking，writer/reviewer 仍然可以通过只读 report tools 有界回查更多全文片段。

对于更大的候选集合，可以使用增量起草：

```toml
[report]
max_section_sources = 0
source_strategy = "batch_refine"
source_batch_size = 10
max_source_batches = 0
review_source_batches = false
```

`batch_refine` 会先用第一批 source 起草每个 section，再随着后续批次进入不断修订同一个 section。只有在需要更强的逐批质量控制、且可以接受更高 LLM 成本时，才建议打开 `review_source_batches = true`。

### 哪些部分依赖 LLM，哪些部分是确定性的

- LLM 支持阶段：`plan`、`read`、`synthesize` 和 `report`。
- `05-design` 会在任何代码生成或修改之前，先写出稳定的 experiment contract、result schema、resource plan、dependency plan 和 domain profile。
- `06-code` 现在有三条受控实现路径：固定模板、已有项目的内嵌 code-task patch、以及没有现成源码时的 greenfield 项目生成。
- 内嵌 code-task experiment：`06-code` 可以调用 LLM 生成 work plan、patch plan 和受控 edit proposal，但补丁只会应用到 run 目录下的隔离 workspace。
- Greenfield experiment：`06-code` 可以调用 LLM 做 architecture/file planning 和代码实现；进入 `07-run` 前会写出 review、memory、backend 和 artifact manifest。
- Guarded reports：如果 LLM 报告缺少必要正文引用、虚构 citation key 或夸大 fixture/toy evidence，会回退到结构化 deterministic report。
- `--no-llm` 会让相关阶段使用离线 fallback 内容。

### 搜索模式和边界

默认搜索行为：

- `search` 会先构建内部 research plan，记录本次计划使用的研究问题、query、source、模式和预算；`02-search/planning/research_plan.json` 只会在 `[run].debug_artifacts = true` 时保留。
- 如果没有额外配置，`search` 先查 OpenAlex，再查 Semantic Scholar，最后查 arXiv。
- 当前默认是按顺序补偿：同一个计划 query 一旦某个 live source 返回候选文献，
  后续 source 会跳过，以减少限流压力和重复噪声。
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

如果希望把检索源、query、cache 和预算写成可复用配置，可以在 run config 里加入 `[research]`：

```toml
[search]
offline = false
max_papers = 5
query = "agent simulation evaluation"

[research]
# lite：元数据/本地笔记；standard：cache/index-ready；strong：后续全文/向量路径。
mode = "standard"
planner = "auto"
sources = ["openalex", "semantic_scholar", "arxiv"]
queries = ["agent simulation evaluation", "multi-agent simulation benchmark"]
auto_query_expansion = true
max_retrieval_rounds = 2
max_queries = 6
required_facets = ["method", "benchmark", "dataset", "code_link"]
use_fulltext = true
allow_pdf_download = false
max_fulltext_documents = 6
max_pdf_mb = 20
keep_raw_pdf = false
parser_backend = "basic"      # basic | pypdf | unstructured
read_screening = "auto"       # auto | llm | deterministic
read_batch_size = 4           # 每个摘要级 LLM 粗筛批次包含几篇论文
read_workers = 3              # 粗筛批次并发数
read_max_shortlist = 12       # 进入深入 Paper Brief/synthesis 的论文上限
cache = true
index_backend = "sqlite_fts"  # keyword | sqlite_fts | hybrid | lancedb | hybrid_lancedb
# SQLite FTS / LanceDB 的共享加速索引目录；如需每个 run 自己保存数据库，可设为 "run" 或 "local"。
index_root = ".simple_ar_cache/research_index"

[research.budget]
max_documents = 20
max_chunks = 200
max_context_tokens = 12000
max_llm_calls = 8
novelty_backend = "local"
```

前几个 research 阶段现在按职责拆分产物。一次 compact run 会生成：

```text
02-search/
  papers.jsonl
  search_meta.json
  documents/
    documents.jsonl
    cache_manifest.json
    fulltext_manifest.json
    fulltext_extraction.json
    extracted_text/  # 只有 HTML/PDF-like 资源被抽取成文本时出现
  research_index/
    chunks.jsonl
    index_meta.json
03-read/
  review/
    screening_decisions.jsonl
    shortlist.jsonl
    reading_table.md
  paper_notes.json
  notes.md
04-synthesize/
  synthesis_brief.json
  synthesis.md
  hypothesis.md
05-design/
  experiment_plan.json
  experiment_contract.json
  experiment_contract.md
  result_schema.json
  resource_plan.json
  dependency_plan.json
  domain_profile.json
  contract_validation.json
  evidence/
    experiment_contract.json  # 兼容旧 research handoff
    experiment_contract.md
```

启用 LLM 时，`03-read` 会走两步式 review：先把 title/abstract 组织成小批次并发粗筛，再对保留下来的集合做重排，输出阅读优先级、证据角色和给 `04-synthesize` 使用的简短提示。最终仍然只写入 `03-read/review/screening_decisions.jsonl`，并由 `shortlist.jsonl` 决定哪些论文进入 notes、cards 和 synthesis。如果希望完全跳过这层 LLM review，可以设置 `[research].read_screening = "deterministic"`。

如果需要查看详细诊断、section tables 和未来 Tool/MCP 接入草案，可以设置 `[run].debug_artifacts = true`：

```text
02-search/
  planning/
    research_plan.json
  traces/
    retrieval_rounds.jsonl
    retrieval_selection.jsonl
  review/
    coverage_report.json
    coverage_report.md
  documents/
    sections.jsonl
03-read/
  cards/
    paper_cards.jsonl
    claim_cards.jsonl
    method_cards.jsonl
    dataset_cards.jsonl
    code_links.jsonl
04-synthesize/
  evidence/
    evidence_pack.json
    evidence_pack.md
    gap_summary.md
    idea_candidates.jsonl
    novelty_checks.jsonl
05-design/
  evidence/
    tool_context.json
    tool_context.md
    evidence_review.md
    decision_log.jsonl
    eval_report.json
    eval_report.md
  tools/
    tool_adapter_contract.json
    tool_adapter_contract.md
    tool_trace.jsonl
    external_agent_backend.md
  governance/
    artifact_retention_policy.json
    artifact_retention_policy.md
```

共享加速索引默认写在 run 目录外：

```text
.simple_ar_cache/
  literature/      # 共享 literature provider metadata cache
  research_index/
    sqlite_fts.db  # 按 run_id 区分 rows 的共享 SQLite FTS store
    lancedb/       # 启用并安装 LanceDB 后使用的共享 LanceDB store
```

如需清理某次 run 的可重建缓存，使用顶层 clean 命令：

```bash
uv run simple-ar clean runs/<run-id>
```

它会先打印 Rich tree 预览：红色表示将删除的缓存，绿色表示会保留的审计产物。
输入 `yes` 后才会执行删除。默认会清理 `02-search/documents/fulltext_cache/`、
`02-search/documents/extracted_text/` 等较大的 run-local 缓存，以及共享 SQLite
research index 中属于该 run 的 rows；不会删除 report、manifest、papers、
`fulltext_extraction.json` 等解析审计文件、read 阶段 Paper Brief、synthesis brief、
已保留的 debug coverage reports 和可移植的 `research_index/chunks.jsonl`。

如果希望把该 run 下所有已知可重建缓存和索引都清掉，可以使用：

```bash
uv run simple-ar clean runs/<run-id> --all-caches
```

这个模式会在确认前额外显示红色警告面板，并删除 artifact retrieval caches、
run-local research indexes、code-task repo map、locate results 和 context packs；
但仍会保留最终 report、metadata、manifest 和 benchmark outputs。

如果只想清空跨 run 共享的 research index store：

```bash
uv run simple-ar clean --shared-index
```

这个命令会先预览，然后清空共享 SQLite FTS / LanceDB 加速索引，通常位于
`.simple_ar_cache/research_index`。它不会删除任何 run 目录或 run-local 审计文件，
但跨 run 的索引加速和 cache 命中会丢失，后续运行需要重新构建。共享索引在别处时
使用 `--index-root PATH`；如果路径在当前 workspace 外，还必须显式加
`--allow-external-index-root`，因为这可能影响其他项目。

如果要进行最强共享清理，同时清空 research index 和 literature provider cache：

```bash
uv run simple-ar clean --shared-cache
```

这通常会删除 `.simple_ar_cache/research_index/` 和
`.simple_ar_cache/literature/`。它不会删除任何 run 目录，但后续运行可能需要重新请求
literature provider，并重新构建本地检索加速索引。

关键文件按目录看：

- `02-search/` 根目录
  - `papers.jsonl`：传给 `read` 阶段的标准化论文 metadata。
  - `search_meta.json`：最终选用 source、状态、返回数量，以及已保留的 evidence artifact 路径。compact run 还会在这里保留一份小型 `source_plan`，让后续阶段在 verbose planning traces 被清理后仍能知道实际 source、全文意图、index backend 和预算。
- `planning/`（debug-only）
  - `research_plan.json`：紧凑计划产物，包含 `research_questions`、`query_plan` 和 `source_plan`，记录子问题、seed/expanded queries、source 顺序、检索模式、本地文档、cache/index 偏好和预算。
- `traces/`（debug-only）
  - `retrieval_rounds.jsonl`：每次 source/query 尝试，包括状态、返回数量、错误/cache 命中和简洁 query 意图。
  - `retrieval_selection.jsonl`：对返回 metadata 的去重、词面排序和预算截断决策。它只是检索选择，不是语义阅读筛选。
- `review/`（debug-only）
  - `coverage_report.json` / `.md`：required facets 覆盖情况、缺失研究问题和 follow-up query 决策。
- `documents/`
  - `documents.jsonl`：标准化 document records，覆盖已选 metadata 和配置的本地文件，并记录 `metadata_only`、`parsed`、`skipped`、`failed` 等状态。
  - `cache_manifest.json`：source counts、status counts 和 full-text/PDF 意图开关。
  - `fulltext_manifest.json`：全文 hint 和 fetch 预算决策。远程获取失败只会记录在这里，不会让 search 阶段失败。
  - `fulltext_extraction.json`：已缓存/本地全文的 best-effort parser 结果。Markdown/text 和基础 HTML 不需要额外依赖；PDF 默认使用轻量 `pypdf`；可选 `unstructured` 可通过 `parser_backend = "unstructured"` 启用。
  - `sections.jsonl`（debug-only）：保守识别出的 section-aware 文本片段，例如 `abstract`、`method`、`experiments`、`results`、`limitations`。
- `research_index/`
  - `chunks.jsonl`：从摘要、已解析本地文件和已抽取全文生成的可移植 chunks；存在 section records 时会带上 section metadata。
  - `index_meta.json`：记录 backend、run id、可移植 chunk 路径，以及共享 SQLite FTS / LanceDB store 路径。共享索引默认在 `.simple_ar_cache/research_index`，不会复制到每个 run。
- `03-read/review/`
  - `screening_decisions.jsonl`：read 阶段对检索结果的 keep/drop/priority 决策。LLM 模式可以丢弃或重排论文；deterministic fallback 会保留已检索入库的论文，并记录其进入结构化阅读的理由。
  - `shortlist.jsonl`：供 notes、cards 和 synthesis 使用的紧凑阅读 shortlist。
  - `reading_table.md`：面向人工检查的阅读表格，并记录 coverage caveats。
- `03-read/`
  - `paper_notes.json`：默认主产物，也就是结构化 Paper Brief；每条记录包含 evidence role、摘要、方法/数据集/指标提示、保守 claims、limitations、synthesis hint、实验 hook 和 open questions。
  - `notes.md`：同一组 Paper Brief 的人类可读版本。
  - `cards/*.jsonl`（debug-only）：只有设置 `[run].debug_artifacts = true` 时才保留的旧式 paper/claim/method/dataset/code hints。
- `04-synthesize/`
  - `synthesis_brief.json`：从 Paper Brief 汇总出的紧凑桥接产物，包含 role counts、coverage/provenance、themes、gaps、idea candidates、local novelty-risk hints 和 limitations，不再重复保存 cards 表。
  - `synthesis.md` / `hypothesis.md`：面向人类阅读的综合分析和可实验假设。
  - `evidence/*.jsonl` / `.md`（debug-only）：只有设置 `[run].debug_artifacts = true` 时才保留的旧 evidence-pack 诊断产物。
- `05-design/`
  - `experiment_plan.json`：下一步 code 阶段使用的执行/模板计划。
  - `experiment_contract.json` / `.md`：V2.5 可执行契约，记录 task kind、目标、implementation mode、源码项目、benchmark command、风险、约束，以及结果/资源/依赖引用。
  - `result_schema.json`：主指标、必需指标、方向和成功条件，供 run guard 和后续 report 使用。
  - `resource_plan.json`：在生成或修改代码前明确 runtime、文件数、生成行数、内存、GPU 和输出流预算。
  - `dependency_plan.json`：依赖安装策略、预期入口和 setup-hook notes。它只记录策略，不自动安装依赖。
  - `domain_profile.json`：generic、已有代码、ML 或 code-agent evaluation 任务的规划默认值。
  - `contract_validation.json`：进入 code 前的契约检查；如果它报告失败，`06-code` 会停止。
- `05-design/evidence/`
  - `experiment_contract.json` / `.md`：来自 V2.3 synthesis artifacts 的兼容 research handoff。可执行的 V2.5 contract 位于 `05-design/experiment_contract.json`。
  - `tool_context.json` / `.md`（debug-only）：给未来 MCP/tool/agent 的只读 handoff，在打开代码 workspace 前只允许读取和规划。
  - `evidence_review.md`、`decision_log.jsonl`、`eval_report.json` / `.md`（debug-only）：人工审核清单和简单 research artifact quality checks。
- `05-design/tools/`（debug-only）
  - `tool_adapter_contract.json` / `.md`：只读 Tool/MCP adapter 契约，定义输入、输出、权限边界、错误/fallback 和 trace 规则。
  - `tool_trace.jsonl`：工具审计 trace。
  - `external_agent_backend.md`：Codex、Claude Code、OpenCode 等外部 agent backend 的接入边界说明。
- `05-design/governance/`（debug-only）
  - `artifact_retention_policy.json` / `.md`：把 search artifacts 分为稳定产物、evidence table、cache、trace、debug 和可重建文件，避免无边界新增 JSON/JSONL。
- `08-report/`
  - `report.md`：基于当前报告模板、已知证据以及可用的实验/code-task 结果组装最终 Markdown 报告。
  - `references.bib`：只为正文实际引用的论文生成有界 bibliography，不保留模型随口给出的外部引用。
  - `manifest.json`：记录 report mode、模板/审查标准路径、正文引用论文、质量状态和审计状态，便于复现。
  - `report_memory.json`：紧凑保存 section plan、source handles、metric claims 和 limitations，用于后续 writer/reviewer 多轮调用时维持任务记忆。
  - `report_quality.json`：确定性安全检查，覆盖 citation provenance、正文引用覆盖、metric visibility 和 runtime/fallback disclosure。
  - `report_audit.json`：紧凑审计包，记录 citation、metric、claim-support warning，以及启用 agent mode 时的 Writer/Reviewer findings。

`[research].planner = "auto"` 会在 `[llm].enabled = true` 时调用 LLM planner，
用于生成更强的 research questions 和 query expansion；provider 不可用时会回退到
deterministic planning。想要完全可复现、无额外 LLM 调用时设为 `"deterministic"`；
明确希望模型参与检索规划时设为 `"llm"`。

当 `[research].max_retrieval_rounds` 大于 `1` 时，search 阶段会根据仍未覆盖的
required facets，在写出最终 `papers.jsonl` 前执行一个有预算限制的第二轮 follow-up 检索。

公开的 research-only 示例使用 live academic sources 和有边界的 full-text extraction：

```bash
uv run simple-ar run --config examples/research_report/configs/research_report.toml
```

这个配置设置了 `[report].mode = "research_only"`，因此 pipeline 会跳过 design/code/run，直接从 search/read/synthesize 进入 report。本地 Markdown/text 笔记仍然可以通过 `[research].sources = ["local_files"]` 使用；如果要做离线私有语料运行，请参考 `CONFIG_REFERENCE_zh.md` 里的 local-notes 配置片段。

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

Code Task 会把源项目准备到一个隔离的可编辑 workspace 中，后续所有补丁都只改这个 workspace，不修改原始项目。默认 `copy` 模式最稳妥；工作流也支持面向较大 git 项目的 `git_worktree`，以及适合小型 allowlist 子集的实验性 `sparse_copy`。

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
    workspace/                  # 隔离可编辑副本或 worktree
    meta/
      codebase_index.json       # 文件级代码索引
      repo_map.json             # 分层 repo/symbol map
      repo_map_summary.md       # 给人看的 repo-map 摘要
```

它不会运行代码、不会调用 LLM，也不会修改原始项目。

如果使用 `workspace.mode = "git_worktree"` 或 `--workspace-mode git_worktree`，`init` 会在 `code_task/workspace/` 创建 detached git worktree，而不是完整复制文件。当前要求 `code_root` 是目标项目的 git 仓库根目录；如果目录不满足要求，CLI 会给出可操作提示，比如初始化 git、提交初始 baseline、传入 repo root，或者改用 `copy` 模式。

如果使用 `workspace.mode = "sparse_copy"` 或 `--workspace-mode sparse_copy`，只会复制匹配 include pattern 的文件，同时始终排除 `.git`、virtualenv、`runs`、cache/build、`data`、`models`、`.env` 和 secret-like 路径。这个模式适合你明确知道需要哪些文件的小型实验；通用项目仍建议 `copy` 或 `git_worktree`。

benchmark 最好输出 `name: value` 数值行。自定义指标推荐在 TOML 中声明解释方向。显式 CLI 参数仍然支持，适合临时实验和快速测试，但公开使用路径建议优先用 TOML。完整参数表见 [CLI 参考](CLI_REFERENCE_zh.md#simple-ar-code-task-init)，配置 schema 见 [配置参考](CONFIG_REFERENCE_zh.md#standalone-code-task-config)。

### 推荐路径：TOML + Execute

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

在真实终端里，这一条命令可以一路经过 plan 审核、proposal 审核、应用补丁、验证和
patched benchmark；每个真实审核门都会用黄色 Rich 面板提示你看什么、下一步会做什么。
如果在非交互 shell 中运行，或者你回答 `no`，它会停在当前审核门，方便你之后重跑。
第一个审核门通常会生成：

```text
code_task/
  work_plan.md
  patch_plan.md
  meta/
    environment_report.json
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

默认 editor backend 是 `controlled_patch`。它的 metadata 会记录在
`proposed_edits.json`、active batch state、`applied_edits.json` 和
`manifest.json.patch` 中。backend 不负责运行 benchmark、批准计划或写报告；这些 gate 仍由 code-task workflow 管理。

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

`probe` 写入 `code_task/meta/environment_report.json`，包含 OS、Python、工具、GPU、依赖文件和 test 目录信号。它不安装依赖，也不运行项目代码。

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

`apply-edits` 只修改 `code_task/workspace/`，写入 `code_task/patch.diff` 和 `code_task/meta/applied_edits.json`，并重建 codebase index。如果 edit 无法唯一匹配，会在写文件前停止。
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

## 8 阶段流程中的内嵌 Code Task

当你希望普通 research pipeline 在 `06-code` 阶段交给一个已有代码项目，并在 `08-report` 中包含代码实验结果时，使用这个模式。

推荐配置驱动：

```bash
uv run simple-ar run --config examples/full_pipeline_tiny_mlp/configs/pipeline.toml
```

等价的 split config 形式：

```bash
uv run simple-ar run \
  --topic "improve tiny digits MLP" \
  --to-stage report \
  --experiment-template code_task_project \
  --code-task-config examples/full_pipeline_tiny_mlp/configs/pipeline.toml \
  --offline-search \
  --experiment-timeout 60
```

完全显式参数形式：

```bash
uv run simple-ar run \
  --topic "improve tiny digits MLP" \
  --to-stage report \
  --experiment-template code_task_project \
  --code-root examples/full_pipeline_tiny_mlp/project \
  --task-file examples/full_pipeline_tiny_mlp/task.md \
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
  --code-root examples/full_pipeline_tiny_mlp/project \
  --benchmark-command "python benchmark.py" \
  --primary-metric accuracy \
  --metric-direction accuracy=higher \
  --offline-search \
  --experiment-timeout 60
```

这种模式下，`05-design` 会从前面研究阶段的产物和紧凑代码摘要中写出 `generated_code_task.md` 和 `generated_code_task_meta.json`，`06-code` 再把生成任务作为普通 `code_task/task.md` 输入。

如果你已经写好了精确的 `task.md`，但仍希望 8 阶段前面的 goal/problem/synthesis/hypothesis 帮助收束实现优先级，可以在 pipeline config 里设置 `[implementation].task_handoff = "merge"`。这时用户任务会作为硬约束保留，`05-design` 会额外生成融合后的 `generated_code_task.md`，再交给内嵌 code-task 执行。

`code_task_project` 会产生正常 pipeline run，同时在 `06-code/code_task_run/` 下产生嵌套 code-task 产物。`06-code` 会准备项目、探测环境、运行 baseline、构建 repo map / context pack、生成批次式 work plan、创建 attempt/batch 状态、生成 patch plan、记录自动 pipeline approval、请求受控 edits、应用补丁、静态验证，并先运行一次 patched benchmark 做阶段内验证。如果这个验证 benchmark 失败，bridge 会基于 failure evidence 写出诊断并尝试一次受控 repair。`07-run` 会重新运行已验证的 patched benchmark，必要时写入 `comparison.json`，并把 code-task metrics 暴露到 canonical `07-run/results.json`。`08-report` 会加入 deterministic Code Task Evidence 部分，指向嵌套 work plan、batch state、summary、diff 和 comparison artifacts。

内嵌产物结构大致是：

```text
06-code/
  code_task_experiment.json
  code_task_run/
    manifest.json
    code_task/
      task.md
      workspace/
      work_plan.md
      patch_plan.md
      patch.diff
      summary.md
      meta/
        repo_map.json
        proposed_edits.json
        validation_report.json
      run/
        baseline/
        patched/
        comparison.json
07-run/
  results.json          # canonical metrics/execution/comparison result
  guard_report.json     # 缺失指标、timeout、NaN/Inf、空跑检查
  stdout.txt
  stderr.txt
08-report/
  report.md
  references.bib
  manifest.json
  report_quality.json
  report_memory.json
  report_audit.json
```

这个路径方便端到端实验，但会牺牲 standalone workflow 的人工暂停点。对安全敏感或难调试项目，建议先用 standalone `code-task execute` 或手动 primitive 路径。

## 8 阶段流程中的 Greenfield Experiment

当你只有研究或 benchmark-style 任务、还没有现成源码项目时，可以使用 greenfield 路径。它不是开放式自主 agent：`05-design` 先写出可执行 contract 和预算，`06-code` 只在 run 目录下生成一个受控小项目并审查，`07-run` 只信任 canonical `results.json` 中可解析的指标。

当前公开 examples 暂不内置一个很小的 greenfield toy 项目。对于从零实现任务，建议新建配置并明确设置 `[task].kind = "greenfield"`、`[implementation].mode = "generate_project"`、匹配真实任务规模的 `[resource]` 限制，以及 `[evaluation]` 指标 schema。这样 greenfield 能力仍然可用，但不会被一个低价值 demo 绑定；后续更适合放到服务器或真实 benchmark-style 任务中测试。

从 `code` 或 `run` 重跑时，默认会先把旧的 `06-code` / `07-run` 关键产物复制到
`archives/<timestamp>/`，再写入新的代码或运行结果：

```bash
uv run simple-ar resume runs/<run-id> --from-stage code --to-stage report
uv run simple-ar resume runs/<run-id> --from-stage run --to-stage report
```

只有明确想无归档覆盖旧产物时才使用 `--overwrite-stage-artifacts`。

紧凑产物结构大致是：

```text
05-design/
  experiment_contract.json
  result_schema.json
  resource_plan.json
  dependency_plan.json
  domain_profile.json
  contract_validation.json
06-code/
  implementation_plan.json
  architecture_plan.json
  architecture_plan.md
  file_plan.json
  generated_project/
    main.py
    generated_experiment/
      runner.py
  code_artifacts.json
  implementation_memory.json
  code_review.json
  code_backend.json
  experiment.py
07-run/
  results.json
  guard_report.json
  stdout.txt
  stderr.txt
  repair_summary.json  # 只有尝试受控修复时才会出现
  archives/<timestamp>/ # 安全重跑时保存旧运行产物
08-report/
  report.md
  report_memory.json
  report_audit.json
```

Greenfield 路径受 `[resource]`、`[evaluation]` 和 `[generation]` 共同约束：生成文件数和行数有上限，依赖安装默认关闭，required metrics 会被 `guard_report.json` 检查，code review warning 会投影到 canonical `results.json`，repair 目前只基于运行证据做窄范围 schema 修复。报告阶段会优先读取 `07-run/results.json`、`guard_report.json`、resource plan 和 code review，而不是直接从 stdout 猜测实验结论。

## 命令设计原则

CLI 保留 primitive commands 是因为项目仍然是学习实现。每一步都应该可检查、可测试、可审核。配置文件用于缩短很长的设置命令，而不是隐藏 approval gate、artifact path、validation result、baseline run 或 benchmark evidence。
