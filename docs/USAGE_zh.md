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
uv run simple-ar run --config examples/run_configs/tiny_digits_mlp_pipeline.toml
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

默认 `report-mode` 是自动判断：如果没有 `results.json`，就写 research-only 结构；如果有实验结果，就写 experiment 结构。也可以强制指定：

```bash
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode research_only
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode experiment
```

### 哪些部分依赖 LLM，哪些部分是确定性的

- LLM 支持阶段：`plan`、`read`、`synthesize` 和 `report`。
- 默认确定性阶段：`design`、`code` 和 `run` 使用固定实验模板，除非选择了 code-task experiment template。
- 内嵌 code-task experiment：`06-code` 可以调用 LLM 生成 work plan、patch plan 和受控 edit proposal，但补丁只会应用到 run 目录下的隔离 workspace。
- Guarded reports：如果 LLM 报告缺少必要正文引用、虚构 citation key 或夸大 fixture/toy evidence，会回退到结构化 deterministic report。
- `--no-llm` 会让相关阶段使用离线 fallback 内容。

### 搜索模式和边界

默认搜索行为：

- `search` 会先生成 `02-search/planning/research_plan.json`，记录本次计划使用的研究问题、query、source、模式和预算。
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
cache = true
index_backend = "sqlite_fts"  # keyword | sqlite_fts | hybrid | lancedb | hybrid_lancedb
# SQLite FTS / LanceDB 的共享加速索引目录；如需每个 run 自己保存数据库，可设为 "run" 或 "local"。
index_root = ".simple_ar_cache/research_index"

[research.budget]
max_documents = 20
max_chunks = 200
max_context_tokens = 12000
max_llm_calls = 8
```

搜索阶段主要会生成下面这棵 `02-search/` 结构：

```text
02-search/
  papers.jsonl
  search_meta.json
  planning/
    research_plan.json
  traces/
    retrieval_rounds.jsonl
    screening_decisions.jsonl
  review/
    coverage_report.json
    coverage_report.md
  documents/
    documents.jsonl
    cache_manifest.json
    fulltext_manifest.json
    fulltext_extraction.json
    extracted_text/  # 只有 HTML/PDF-like 资源被抽取成文本时出现
  research_index/
    chunks.jsonl
    index_meta.json
  cards/
    paper_cards.jsonl
    claim_cards.jsonl
```

共享加速索引默认写在 run 目录外：

```text
.simple_ar_cache/
  research_index/
    sqlite_fts.db  # 按 run_id 区分 rows 的共享 SQLite FTS store
    lancedb/       # 启用并安装 LanceDB 后使用的共享 LanceDB store
```

其中最重要的文件是：

- `02-search/planning/research_plan.json`：一个紧凑的计划产物，内部包含 `research_questions`、`query_plan` 和 `source_plan` 三个 section，记录子问题、seed/expanded queries、带 title/abstract keyword hints 的 `query_specs`、计划 sources、检索模式、本地文档、cache/index 偏好和预算。
- `02-search/traces/retrieval_rounds.jsonl`：每次实际执行的 source/query 尝试，包括状态、返回数量、错误/cache 命中，以及 facet、title/abstract keywords 等简洁 query 意图 trace。
- `02-search/traces/screening_decisions.jsonl`：对返回 metadata 的去重和轻量 relevance screening 决策。
- `02-search/review/coverage_report.json` 和 `02-search/review/coverage_report.md`：required facets 覆盖情况、缺失研究问题和 follow-up query 决策。
- `02-search/documents/documents.jsonl`：标准化 document records，覆盖已选 metadata 和配置的本地文件，并记录 `metadata_only`、`parsed`、`skipped`、`failed` 等 extraction status。
- `02-search/documents/cache_manifest.json`：cache/extraction 汇总，包含 source counts、status counts 和 full-text/PDF 意图开关。
- `02-search/documents/fulltext_manifest.json`：全文 hint 和 fetch 预算决策。远程获取失败会记录在该 manifest 中，不会让 search 阶段失败。
- `02-search/documents/fulltext_extraction.json`：对已缓存/本地全文的 best-effort parser 结果。Markdown/text 和基础 HTML 不需要额外依赖；PDF 解析默认使用轻量 `pypdf`；如果安装了可选依赖，也可以用 `parser_backend = "unstructured"`。
- `02-search/research_index/chunks.jsonl`：从摘要、已解析本地文件和已抽取全文生成的可移植本地 chunks。
- `02-search/research_index/index_meta.json`：本地 index manifest，记录 backend、run id、可移植 chunk 路径，以及共享 SQLite FTS / LanceDB store 路径。SQLite 和 LanceDB 默认共享在 `.simple_ar_cache/research_index`，不会在每个 run 目录里复制一份数据库。
- `02-search/cards/paper_cards.jsonl`：deterministic paper-level evidence cards，包含 problem/method/metric/limitation hints 和 source chunk refs。
- `02-search/cards/claim_cards.jsonl`：保守的 claim cards，每条都绑定 chunk id。这些还不是最终报告 claim，后续 report 阶段仍需要 audit 后再使用。
- `02-search/search_meta.json`：最终选用 source、状态、返回数量，以及 planning/trace/review artifact 路径。
- `02-search/papers.jsonl`：传给 `read` 阶段的标准化论文 metadata。

`[research].planner = "auto"` 会在 `[llm].enabled = true` 时调用 LLM planner，
用于生成更强的 research questions 和 query expansion；provider 不可用时会回退到
deterministic planning。想要完全可复现、无额外 LLM 调用时设为 `"deterministic"`；
明确希望模型参与检索规划时设为 `"llm"`。

当 `[research].max_retrieval_rounds` 大于 `1` 时，search 阶段会根据仍未覆盖的
required facets，在写出最终 `papers.jsonl` 前执行一个有预算限制的第二轮 follow-up 检索。

本地 Markdown/text 笔记也可以作为保守的研究源使用，不需要调用 live literature provider：

```bash
uv run simple-ar run --config examples/run_configs/local_research_report.toml
```

这个示例设置了 `[research].sources = ["local_files"]`，并把 `[research].local_documents` 指向 `examples/research/local_agent_simulation_notes.md`。当前 local-file connector 仍然很克制：只把 `.md` / `.txt` 当作 metadata-like records 读取，并使用轻量 keyword-overlap 匹配，而不是要求完整 query 字符串逐字出现。启用 `[research].use_fulltext = true` 后，search 阶段会把本地/缓存全文的 parser 结果写入 `documents/fulltext_extraction.json`，并在生成 evidence cards 前把抽取文本送入 `research_index/chunks.jsonl`。PDF 输入仍是 best-effort：只有在可选 parser 可用且明确启用 full-text 意图时才解析，失败不会中断 search。

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

推荐先从 TOML 配置初始化，把项目路径、benchmark 指标、workspace 模式、模型路由和编辑预算都放在一个可审核文件里：

```bash
uv run simple-ar code-task init --config examples/code_tasks/configs/tiny_digits_mlp.toml
```

如果想试一个更接近真实项目的本地示例，可以使用 medium review pipeline：

```bash
uv run simple-ar code-task init --config examples/code_tasks/configs/medium_review_pipeline.toml
```

这个示例会运行 `python main.py --config configs/experiment.json --show-progress`，baseline / patched run 中会打印逐轮进度行，并通过 `[execute].stream_benchmark_output = "auto"` 让 `code-task execute` 在保存 stdout/stderr 产物的同时，把 benchmark 进度转发到命令行。`auto` 模式同时兼容普通 `print` 日志和 `tqdm` 这类 carriage-return 进度输出。

`init` 会创建新的 `runs/<run-id>/`，把源项目准备到 `code_task/workspace/`，把任务写入 `code_task/task.md`，生成 `code_task/meta/codebase_index.json` 以及分层 `code_task/meta/repo_map.json` / `repo_map_summary.md`，并把 benchmark / environment / workspace 策略记录到 `manifest.json`。它不会运行代码、不会调用 LLM，也不会修改原始项目。

如果使用 `workspace.mode = "git_worktree"` 或 `--workspace-mode git_worktree`，`init` 会在 `code_task/workspace/` 创建 detached git worktree，而不是完整复制文件。当前要求 `code_root` 是目标项目的 git 仓库根目录；如果目录不满足要求，CLI 会给出可操作提示，比如初始化 git、提交初始 baseline、传入 repo root，或者改用 `copy` 模式。

如果使用 `workspace.mode = "sparse_copy"` 或 `--workspace-mode sparse_copy`，只会复制匹配 include pattern 的文件，同时始终排除 `.git`、virtualenv、`runs`、cache/build、`data`、`models`、`.env` 和 secret-like 路径。这个模式适合你明确知道需要哪些文件的小型实验；通用项目仍建议 `copy` 或 `git_worktree`。

benchmark 最好输出 `name: value` 数值行。自定义指标推荐在 TOML 中声明解释方向。显式 CLI 参数仍然支持，适合临时实验和快速测试，但公开使用路径建议优先用 TOML。完整参数表见 [CLI 参考](CLI_REFERENCE_zh.md#simple-ar-code-task-init)，配置 schema 见 [配置参考](CONFIG_REFERENCE_zh.md#standalone-code-task-config)。

### 推荐路径：TOML + Execute

正常使用时，推荐把项目路径、benchmark、指标方向、模型路由和预算放进 TOML，然后用 `code-task execute` 推进。这样命令更短，但仍然保留 patch plan 和 edit proposal 两个审核点。下面示例默认使用 tiny digits MLP 配置；如果要运行带进度输出的多文件 medium 示例，把 config 路径替换为 `examples/code_tasks/configs/medium_review_pipeline.toml`。

1. 初始化 run：

```bash
uv run simple-ar code-task init --config examples/code_tasks/configs/tiny_digits_mlp.toml
```

这个命令会打印一个 run 目录，例如 `runs/20260523-xxxx-tiny-digits-mlp`。下面命令中的 `runs/<run-id>` 都替换成这个实际路径即可。

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

> Note：medium 任务通常会联动 feature extraction、model scoring 和 config，因此可能生成一个已审核的 `large` batch。只有在检查 `code_task/meta/proposed_edits.json` 后，最后应用 proposal 时才应加入 `--allow-large-edits`。

2. 推进到第一个人工审核点：

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_tasks/configs/tiny_digits_mlp.toml
```

这一步通常会生成第一批执行产物：

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

3. 阅读 `code_task/work_plan.md` 和 `code_task/patch_plan.md`。如果计划合理，批准它：

```bash
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve --note "reviewed"
```

4. 生成 edit proposal，但先不要应用：

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_tasks/configs/tiny_digits_mlp.toml --to-step propose-edits
```

重点审核：

- `code_task/meta/proposed_edits.json`：受控 old/new replacement。
- `code_task/meta/llm_usage_summary.json`：LLM token 用量摘要。
- 最新 `code_task/attempts/.../proposal_warnings.json`，如果存在。

默认 editor backend 是 `controlled_patch`。它的 metadata 会记录在
`proposed_edits.json`、active batch state、`applied_edits.json` 和
`manifest.json.patch` 中。backend 不负责运行 benchmark、批准计划或写报告；这些 gate 仍由 code-task workflow 管理。

5. 确认 proposal 后，应用补丁并运行验证和 patched benchmark：

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_tasks/configs/tiny_digits_mlp.toml --apply-proposed-edits --timeout 60
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
uv run simple-ar code-task execute runs/<run-id> --config examples/code_tasks/configs/tiny_digits_mlp.toml --to-step repair --repair-rounds 1 --timeout 60
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
uv run simple-ar code-task execute runs/<run-id> --config examples/code_tasks/configs/tiny_digits_mlp.toml --dry-run
```

### 可选的代码地图和上下文工具

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
这是 V2.2 后续做多轮、分批编辑和失败恢复的基础。active batch 存在时，
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
V2.2 还会在模型返回 JSON 后执行本地编辑预算检查。超预算 proposal 会写入 warnings 和 rejected edits，而不是直接应用；如果 proposal 仍在 larger review budget 内，审核 JSON 后再显式使用 `--allow-large-edits`。

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
uv run simple-ar code-task execute runs/<run-id> --config examples/code_tasks/configs/tiny_digits_mlp.toml --to-step propose-edits
```

- 可检查 `manifest.json` 中的 `plan.status` 是否为 `approved`；人工决策记录在 `code_task/meta/hitl_decisions.jsonl`。

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
uv run simple-ar code-task execute runs/<run-id> --config examples/code_tasks/configs/tiny_digits_mlp.toml --to-step repair --repair-rounds 1 --timeout 60
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

`code_task_project` 会产生正常 pipeline run，同时在 `06-code/code_task_run/` 下产生嵌套 code-task 产物。`06-code` 会准备项目、探测环境、运行 baseline、构建 repo map / context pack、生成批次式 work plan、创建 attempt/batch 状态、生成 patch plan、记录自动 pipeline approval、请求受控 edits、应用补丁并验证。`07-run` 运行 patched benchmark，必要时写入 `comparison.json`，并把 code-task metrics 暴露到 `07-run/results.json`。`08-report` 会加入 deterministic Code Task Evidence 部分，指向嵌套 work plan、batch state、summary、diff 和 comparison artifacts。

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
  results.json
08-report/
  report.md
  references.bib
  manifest.json
  report_quality.json
```

这个路径方便端到端实验，但会牺牲 standalone workflow 的人工暂停点。对安全敏感或难调试项目，建议先用 standalone `code-task execute` 或手动 primitive 路径。

## 命令设计原则

CLI 保留 primitive commands 是因为项目仍然是学习实现。每一步都应该可检查、可测试、可审核。配置文件用于缩短很长的设置命令，而不是隐藏 approval gate、artifact path、validation result、baseline run 或 benchmark evidence。
