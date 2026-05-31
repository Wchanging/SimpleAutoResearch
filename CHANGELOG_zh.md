# Changelog

[English version](CHANGELOG.md)

本文按倒序记录用户可见的项目变化。规划笔记和设计理由主要放在 `docs/` 和 `MDfiles/`；这里尽量保持为普通 changelog，而不是长期计划文档。

## 2026-05-31

### Added

- 将 Pydantic、Rich、LiteLLM、pyalex 加入直接依赖，作为第一批基础设施替换。
- 新增基于 `WorkspaceState` 的 pipeline reboot core。运行目录现在会写入顶层
  `state.json`，已完成阶段可以写出紧凑的 `contract.json` / `report.md`，
  用于机器可读交接和人工审查。
- 新增可选 `unstructured` 全文解析后端，可通过 `[research].parser_backend = "unstructured"` 启用；如果未安装该可选包，只会在 manifest 中记录失败，不会让 search 阶段失败。
- 新增可选 LanceDB research-index 后端状态，可通过 `[research].index_backend = "lancedb"` 或 `"hybrid_lancedb"` 启用；`chunks.jsonl` 仍然是可移植 source of truth。

### Changed

- 顶层 pipeline TOML 现在先经过 Pydantic schema 校验，再 flatten 成现有运行时配置 dict，配置类型错误会更早、更明确地暴露。
- Code-task TOML 现在也通过 Pydantic section schema 解析 init 和 execute options，替换之前的自由 dict 解析路径。
- LLM client 现在通过 LiteLLM 调用 provider，不再直接构造 OpenAI SDK client；OpenAI-compatible `OPENAI_BASE_URL` 仍然通过 LiteLLM 的 OpenAI provider 路径支持。
- OpenAlex 检索现在使用 pyalex，而不是手写 urllib request client；项目内仍保留 `Paper` normalization 层。
- Pipeline 进度和 developer-check 输出现在通过一个轻量 Rich console wrapper，为后续更清晰的人工审核输出打基础，同时保持现有 CLI 兼容。
- Research SQLite FTS / LanceDB 加速索引默认共享在 `.simple_ar_cache/research_index`，run 目录只保留可审计的 `chunks.jsonl` 和 `index_meta.json`；共享数据库按 `run_id` 更新，避免每个 run 重复创建一份索引。
- Pipeline 阶段依赖现在优先使用显式 `WorkspaceState` 指针，而不是通过
  `find_artifact` 反向扫描 run 目录；旧 helper 仅作为 legacy compatibility 保留。
- 默认 pipeline run 会在 search 阶段 contract 写出后只压缩 `02-search` 的诊断目录。
  `documents/`、`research_index/` 和 `cards/` 等后续 grounding 需要的 evidence 产物会保留在 run 目录；
  需要额外保留 planning、trace、screening 和 coverage-review 诊断产物时，可设置
  `[run].debug_artifacts = true`。
- 紧凑 search run 现在会同步清理 `search_meta.json` 中指向已删除诊断产物的路径，
  避免 metadata 指向不存在的 planning/trace/review 文件。
- 原本巨大的 `stage_handlers.py` 和 `cli.py` 已移动到 `src/simple_ar/legacy/`，
  对外 import path 只保留小型 compatibility wrapper，便于后续逐步拆掉巨石实现。
- Experiment runner/template helpers 已迁移到 `src/simple_ar/coding/`；
  `src/simple_ar/experiment/` 现在保留兼容 wrapper，后续以 coding domain 为主要实现位置。
- Research 模块现在按生命周期分组到 `planning/`、`sources/`、`documents/`、
  `store/`、`evidence/` 和 `outputs/`，不再把所有检索/证据文件平铺在同一目录。
- Code-task 模块现在按生命周期分组到 `runtime/`、`workspace/`、`analysis/`、
  `editing/`、`execution/` 和 `orchestration/`，收缩原先 25 个左右文件平铺的包表面。
- README、Usage、Workflow 和 Config Reference 已说明 `unstructured` 与 LanceDB 是可选后端，而不是基础安装强依赖。

## 2026-05-27

### Added

- 新增 V2.3 Day 10 failure-safe full-text caching：被选中的本地全文会标记为 cached，
  受控远程获取失败会记录到 `fulltext_manifest.json`，search 阶段继续使用 metadata/abstract evidence。
- 新增 V2.3 Day 11 full-text extraction：
  `02-search/documents/fulltext_extraction.json` 现在会记录已缓存/本地全文资源的 parser 结果，
  并在生成 evidence cards 前把解析文本送入 `research_index/chunks.jsonl`。

### Changed

- Search 阶段 contract 现在声明 `documents/fulltext_extraction.json`，命令行完成输出和
  `search_meta.json` 都能看到全文解析产物。
- 本地 research 示例默认启用 `use_fulltext = true`，便于检查本地 Markdown/text 的
  parser -> chunk -> card 链路。
- README、Usage、Config Reference 和 Workflows 文档已更新当前边界：Markdown/text
  和基础 HTML 可解析，PDF 解析是 best-effort，向量检索还未启用。

## 2026-05-26

### Added

- 新增 V2.3 Day 3 research-question 和 query-plan sections，统一放在
  `02-search/planning/research_plan.json` 中。
- 新增可选 LLM-backed research planning，可通过 `[research].planner` 控制；
  离线或 provider 失败时仍保留 deterministic planning 兜底。
- research plan 中新增结构化 `query_specs`，让 LLM research planner 保留
  title/abstract keyword 意图，而不是只输出偏浏览器搜索风格的 query 字符串。
- 新增 V2.3 Day 4 检索 trace 产物：
  `02-search/traces/retrieval_rounds.jsonl` 和
  `02-search/traces/screening_decisions.jsonl`，记录实际执行的 source/query 尝试、
  简洁 query 意图 trace、去重和轻量相关性筛选决策，再写入 `papers.jsonl`。
- 新增 V2.3 Day 5 coverage 产物：
  `02-search/review/coverage_report.json` 和 `02-search/review/coverage_report.md`，
  记录 required facets 覆盖情况、缺失 facets、问题覆盖度和预算内 follow-up query 建议。
- 新增 V2.3 Day 6 document-store 产物：
  `02-search/documents/documents.jsonl` 和
  `02-search/documents/cache_manifest.json`，记录已选 metadata、配置的本地文件、
  extraction status、source counts，以及 cache/full-text 意图，不下载受限全文。
- 新增 V2.3 Day 7 本地 research-index 产物：
  `02-search/research_index/chunks.jsonl` 和
  `02-search/research_index/index_meta.json`；在 `sqlite_fts` / `hybrid` 模式下可选生成 SQLite FTS。
- 新增 V2.3 Day 8 deterministic evidence-card 产物：
  `02-search/cards/paper_cards.jsonl` 和
  `02-search/cards/claim_cards.jsonl`，基于 document chunks 生成，并带 evidence refs 供后续 audit 使用。
- 新增 V2.3 Day 9 full-text planning 产物：
  `02-search/documents/fulltext_manifest.json`，记录 arXiv/OpenAlex/local-file 全文 hints、
  fetch 预算决策，以及 blocked/skipped 原因；默认不会下载远程 PDF。
- 新增 Semantic Scholar 在线 metadata connector，默认位于 OpenAlex 和 arXiv
  之间，让 V2.3 research search 不依赖 fixture metadata 时也有更广的论文来源。
- 新增本地研究源示例 `examples/run_configs/local_research_report.toml`，
  并在 `examples/research/` 下提供配套 Markdown notes。

### Changed

- 搜索执行现在通过 research connector wrapper 调用 OpenAlex、Semantic Scholar、
  arXiv 和本地 Markdown/text 文件源，同时保留已有 fixture 与 cache fallback 行为。
- search 阶段现在会先把 seed queries 扩展成受限的 facet-driven follow-up queries，
  再构建 `research_plan.json`；LLM 开启时，`planner = "auto"` 可以让模型参与
  更强的 question decomposition 和 query 术语扩展。
- 当 coverage 仍有缺口且 retrieval-round 预算允许时，search 会继续跑第二轮
  ordered-fallback follow-up retrieval，并重新筛选候选文献。
- Search 阶段的 planning、trace 和 review 产物现在分别放在
  `02-search/planning/`、`02-search/traces/` 和 `02-search/review/` 下，
  避免较大的 research run 把所有中间文件平铺在阶段目录根部。
- research planning 现在合并为一个 `planning/research_plan.json`，避免把研究问题、
  query plan 和 source plan 拆成三个小 JSON，同时保留可审查的内部 sections。
- 本地 Markdown/text 搜索现在使用轻量 keyword-overlap 匹配，而不是要求
  query 字符串逐字出现，使规范化后的 paper-search query 更适合短笔记源。
- 本地 Markdown/text 文档现在会生成带 content hash 的 parsed document record；
  PDF 输入会先记录为 skipped 或 failed，除非明确启用 full-text 意图且可选 parser 可用。
- 摘要和已解析本地文件现在会切成可移植本地 index chunks，为后续 evidence cards
  和 RAG-style retrieval 提供稳定输入，不需要现在就引入 embedding。
- OpenAlex metadata 现在会尽量保留 open-access URL hints；arXiv records 也能推导 provider
  PDF hints，供后续受控全文获取/解析阶段使用。
- search 阶段现在会在 `search_meta.json` 中记录 paper/claim card 数量，方便检查 evidence layer 是否生成完整。
- 拆分公开命令和配置文档：`CLI_REFERENCE_zh.md` 现在只聚焦命令语法、参数表和产物；
  新增 `CONFIG_REFERENCE_zh.md` 集中说明 TOML schema、完整配置和 workspace 模式变体。
- 配置文档补充了更完整的 inline comments 和关键字段说明，便于理解 `max_papers`、
  research budgets、workspace modes 和 execute budgets 等不够自解释的设置。

## 2026-05-24

### Added

- 添加 V2.3 research source-planning 基础：`02-search` 现在会写出
  `planning/research_plan.json`，记录研究问题、计划 query、source 顺序、
  research mode、本地文档、cache/index 偏好和轻量预算。
- 顶层 run config 现在支持 `[research]`，包括 `sources`、`queries`、
  `local_documents`、`cache`、`index_backend` 和 `[research.budget]`。

### Changed

- 搜索执行开始通过 research connector wrapper 调用 OpenAlex、arXiv 和本地
  Markdown/text 文件源，同时保留已有 fixture 与 cache fallback 行为。
- README、Usage、CLI Reference 和 Workflows 文档开始补充
  `02-search/planning/research_plan.json`、`[research]` 配置和本地文件源说明。

## 2026-05-23

### Changed

- 内嵌 8 阶段 `code_task_project` run 现在在 `06-code` 使用 V2.2 的
  code-task 执行形态：repo map / context pack、LLM work plan、attempt/batch
  state、patch plan、受控 edit proposal、apply 和 validation。最终报告证据
  除了 summary、diff、comparison，也会指向 work-plan 和 batch artifacts。
- 新增 medium review pipeline code-task 示例，包含 `main.py` 入口、JSON config、
  多模块 feature/model/metric 结构、可见进度输出，以及启用 streamed benchmark
  output 的 TOML task config。
- `code-task execute --config` 现在可通过 `[execute].stream_benchmark_output = true`
  在 baseline 和 patched benchmark 运行时转发 stdout/stderr。
- Benchmark streaming 现在支持 `"auto"`、`"line"` 和 `"summary"` 等字符串模式；
  `"auto"` 同时兼容普通逐行日志和 `tqdm` 这类 carriage-return 进度输出。
- 文档已把内嵌 research-to-code 路径从旧的直接 patch-plan 流程更新为
  work-plan / batch based flow。
- Work-plan batch 创建现在会把小型串行依赖链合并成一个受控执行批次，例如
  feature producer -> model consumer -> config switch。拆分后的审核项仍然保留，
  `batch_state.json.work_item.source_work_item_ids` 会记录合并范围；如果合并批次
  触发 large budget，应用前仍需要显式审核和 `--allow-large-edits`。
- 应用已审核 large proposal 时，现在会把 apply-time approval 记录到
  `applied_edits.json` 和 `manifest.json.patch.budget`；executor benchmark
  路径也会避免写入重复的 validation history。
- 公开文档现在把 README 保持为更简洁的项目入口，把完整 code-task executor
  链路下沉到 Usage，移除冗长的 PowerShell run 目录选择脚本，统一使用
  `runs/<run-id>` 占位符，并把 `copy`、`git_worktree`、`sparse_copy` 作为一等
  workspace 策略展示。

## 2026-05-22

### Changed

- Code-task patched run 现在会把 benchmark 是否通过和任务目标是否达成分开记录：`manifest.json.objective.status` 来自 baseline-vs-patched comparison，因此 benchmark 通过时仍可能被标记为 `regressed`、`mixed` 或 `inconclusive`。
- `code-task execute` 现在会优先选择第一个真正可执行的实现型 work item，而不是盲目把纯检查、纯分析的第一个 work-plan item 当作编辑批次。
- 手动运行 `code-task validate` 和 `code-task run` 时，如果补丁已经应用，也会同步 latest batch、attempt 和 work-plan 状态，使手动路径更接近 executor 路径。
- 应用 repair proposal 时，现在会记录实际使用的 repair proposal path 作为 latest applied proposal；后续 patched benchmark 通过后，旧的 failure/repair section 会在 status 和 summary 中标记为 resolved。
- Failure analysis 现在会捕获 metric floor 和 timing budget 信号，例如 `accuracy below benchmark floor`、`macro_f1` 和 `train_time_sec`。
- 文档补充说明了 objective verdict、implementation-batch selection、repair application state，以及 benchmark pass 但指标退化时应该如何判断。
- `README.md` / `README_zh.md` 和 `docs/USAGE.md` / `docs/USAGE_zh.md` 现在把 TOML + `code-task execute` 作为主要 code-task 使用路径，primitive commands 下沉为高级调试步骤。
- 新增 V2.2 editor backend interface，并把默认 controlled old/new patch 路径迁移到 `controlled_patch` backend 后面，同时保持现有 CLI/API 兼容。proposal、batch、apply、manifest 和 status 产物现在会暴露 backend metadata。
- 新增预留的 `external_agent` editor 边界，为后续 Codex / Claude / OpenCode adapter 做准备。当前包含 provider 规范化、保守权限策略、secret/home 读取阻断规则和可审查 invocation-plan artifact，但默认仍不可执行。

## 2026-05-21

### Added

- 添加 Day 17-20 V2.2 batch-level edit budget enforcement：code-task proposal 现在有 normal / large / absolute 三档预算，并通过 `--allow-large-edits` 做显式大编辑审核。
- 在 `code_task/attempts/attempt-NNN/batches/batch-NNN/` 下补充 batch context、proposal warnings、usage summaries、validation links、benchmark links 和 repair proposal links。
- 添加 `code-task execute --config`，支持读取 `[execute]`、`[models.code_task]` 和 `[budget]`，让模型路由和预算控制可以放进 TOML，而不是堆很长的 CLI 参数。

### Changed

- `code-task execute` 现在可以按配置把 work-plan / patch-plan、edit proposal、repair 分别路由到 planner/editor/repair 模型。
- Active work-item batch 会限制 LLM edit proposal 只能修改该 batch 的 target files；tests 和 benchmark 文件仍然作为只读证据。
- Validation、benchmark 和 repair 步骤现在会更新 active batch state，便于检查中断、失败和修复尝试。
- 超大或超预算模型输出会被 normalizer 转成 warnings / rejected edits，而不是隐式应用。
- 文档现在明确展示正确的 reviewed executor 顺序：批准 plan 后使用 `execute --to-step propose-edits` 生成 proposal，并补充 missing proposal、benchmark regression、repair proposal、exact-text patch failure、large-edit approval 和本地 `uv` cache 权限问题的排错说明。
- Repair 和 edit proposal 现在会拒绝把 unified-diff 片段误写进结构化 `old` / `new` JSON 字段的 edit；`apply-edits` 遇到 patch validation failure 时会输出可读错误，而不是直接打印 Python traceback。

## 2026-05-20

### Added

- 添加 Day 8 V2.2 分层 repo-map 产物：`code_task/meta/repo_map.json` 和 `code_task/meta/repo_map_summary.md`。
- Repo-map schema 包含 project、directory、file、symbol、entrypoint、test、benchmark、config 和 prompt-budget 层，同时保留 `codebase_index.json` 兼容旧流程。
- 添加 `simple-ar code-task map`，可以作为独立步骤从当前 workspace 重建 repo-map 产物。
- 添加 `simple-ar code-task locate`，可以从 repo map 中排序 likely editable targets 和 protected read-only evidence。
- 添加 `simple-ar code-task context`，可以在 `code_task/context_packs/context-NNN/` 下生成受预算限制的 prompt context pack。
- 添加 Day 15-16 V2.2 work-plan artifacts：`code_task/work_plan.json` 和 `code_task/work_plan.md`，以及 `simple-ar code-task work-plan`。
- 添加初始 attempt/batch 状态目录：`code_task/attempts/attempt-NNN/batches/batch-NNN/`，以及 `simple-ar code-task batch --work-item W1`。
- 添加 `simple-ar-checks` 和 `scripts/run_checks.py`，支持 `quick`、`code-task`、`pipeline`、`research`、`all` 等分层开发验证组。

### Changed

- Code-task 初始化现在同时写旧 codebase index 和新 repo map；补丁应用后也会同步重建两个产物。
- Code-task 文档现在说明 `map -> locate -> context` 路径，便于大项目在规划/编辑前先缩小上下文。
- Patch planning 现在会在存在 latest context pack 时优先使用它；controlled edit proposal 只读取其中 editable snippets，并继续把保护文件作为 read-only evidence。
- Code-task planning 现在增加更高一层的 work-plan，用于先把宽泛任务拆成小批次，再进入 patch proposal。
- `code-task execute` 现在会在正常路径中包含 work-plan 和 batch setup；除非传 `--no-llm`，否则 work-plan 使用配置好的 LLM。
- 开发文档现在推荐迭代时使用目标测试组，把完整测试发现保留给提交、推送或大范围重构前。
- V2.2 计划和 workspace 文档现在记录 Day 14 真实 LLM smoke 暴露的问题：普通 JSON patch proposal 可能触发很长 completion，因此后续 editor-backend 工作需要加入 bounded proposal contract、context request artifact、多轮 attempt 和 future external coding-agent routing。

## 2026-05-19

### Added

- 添加 V2.2 code-task workspace modes：`copy` 仍是默认模式，`git_worktree` 可为 repo-root git 项目在 `code_task/workspace` 创建 detached worktree。
- 添加实验性 `sparse_copy` workspace mode，支持 include/exclude patterns、data/model/cache/secret-like 路径内置排除，并在 manifest 中记录 pattern/risk。
- 添加 `[workspace]` 配置，以及 standalone 和 embedded code-task 的 workspace mode、source virtualenv reuse、setup hook 记录相关 CLI flags。
- 在 code-task `manifest.json` 中新增结构化 `workspace` section，同时保留旧 `copy` section 以兼容。

### Changed

- Code-task initialization 改为通过 workspace dispatcher，而不是直接调用 copy routine。
- `code-task init` 遇到 workspace 或 task-file 设置问题时，改为输出用户可读的下一步检查清单，而不是原始 Python traceback。
- Codebase indexing 会跳过 `.git`、`.env`、virtualenv 和 cache metadata，避免 worktree mode 把 git metadata 或 secret-like 文件放进模型上下文。
- 默认 edit scope 现在也保护 `.env` 和 secret/credential-looking paths。

## 2026-05-18

### Added

- 为内嵌 `code_task_project` run 添加 research-first task generation：如果没有 task file，`05-design` 会从前面研究产物和紧凑代码摘要生成 `generated_code_task.md`，再由 `06-code` 作为 code-task prompt 使用。

### Changed

- 对 8 阶段内嵌 code-task run，`[code_task].task_file` 变为可选；standalone `simple-ar code-task init` 仍要求 task file。
- 更新 README、usage、workflow 和 CLI reference，区分用户显式 task file 与 research-first generated task file。

## 2026-05-17

### Added

- 添加默认 code-task edit-scope policy，并在 `manifest.json` 中记录受保护 test/benchmark path patterns。
- 添加通用 `code_task_project` experiment template，使 8 阶段 `simple-ar run --to-stage report` 可以复制用户项目、运行 baseline benchmark、应用 LLM-controlled patch、运行 patched benchmark，并把 nested code-task evidence 写入最终报告。
- 添加顶层 `simple-ar run --config` / `resume --config`，支持可复现 TOML-configured research 和 embedded code-task runs。
- 添加 `examples/run_configs/tiny_digits_mlp_pipeline.toml`，作为 config-driven end-to-end code-task pipeline 示例。
- 添加 pipeline code-task options：`--code-task-config`、`--code-root`、`--task-file`、`--benchmark-command`、`--primary-metric`、重复 `--metric-direction` 和 environment policy overrides。
- 添加 Phase 5 failure analysis 对 validation-only failures 的支持。
- 添加 bounded repair proposal metadata，包括 source analysis paths、selected repair context files 和 explicit repair constraints。
- 添加 `simple-ar code-task execute`，作为保守 state-aware orchestrator，在 plan approval 和 proposal review gates 停止。
- 添加 code-task metric comparison 配置：`--primary-metric` 和重复 `--metric-direction METRIC=DIRECTION`。
- 添加 `code-task init --config`，支持 TOML 初始化，包括 metric direction 设置。
- 添加 tiny-digits MLP code-task config 示例。
- 添加 `docs/CLI_REFERENCE.md` 作为专门命令和参数参考。

### Changed

- 泛化内嵌 code-task experiment 准备流程，使旧 toy-spam demo 和用户项目共享 baseline/plan/proposal/apply/validate harness。
- 将内嵌 code-task bridge module 从 `code_task_demo.py` 重命名为 `code_task_experiment.py`。
- Final reports 在 LLM draft 缺少 Code Task Evidence 时，会追加 deterministic Code Task Evidence section。
- Code-task proposals、repairs 和 patch application 默认把 tests 与 benchmark files 作为只读证据。
- Comparison artifacts 记录配置的 metric directions，并把未知 metrics 作为 delta 记录，但不用于 improved/regressed verdict。
- 将 code-task init config parsing 从 `cli.py` 移到 `code_task/config.py`。
- Repair context selection 优先考虑当前 patch 修改的文件，再考虑 traceback/test files，减少误改 tests 的风险。
- `code_task/summary.md` 在 repair proposal 生成后加入 Repair section。
- Patch application 支持同一文件多个有序 edit。
- `code-task execute` 遇到无效 edit proposal 时报告 `patch_apply_failed`，不直接暴露 Python traceback。
- 文档明确 `code-task execute` 是 primitive code-task commands 的便捷层，不取代可审核步骤。
- 将共享 metric parsing 从 `experiment.metrics` 移到顶层 `simple_ar.metrics`。
- 移除未使用的 code-task environment config helper 和旧 unlabelled benchmark artifact fallback。
- 简化 `docs/USAGE.md`，把详细命令/配置表移到 CLI reference。
- Code-task summaries 现在从 outcome 和 next-step section 开始，`simple-ar status` 也展示 summary、metric config 和 comparison deltas。

## 2026-05-16

### Added

- 添加 `code-task probe`，用于 V2.1 environment inspection。
- 添加 `code_task/meta/environment_report.json`，包含 OS、Python、tool、GPU、dependency-file 和 test-directory signals。
- 在 code-task summaries 和 `simple-ar status` 中加入 environment status。
- 添加 `code-task baseline`，在 `code_task/run/baseline/` 捕获 pre-patch benchmark results。
- 添加 code-task `--env-mode current|external` 和 `--python`，用于选择 benchmark command 的解释器。
- 添加轻量 `tiny_digits_mlp_project` 示例，支持无需下载和 GPU 的本地 ML-style benchmark。
- 添加 code-task baseline-vs-patched comparison artifacts。

### Changed

- 在 usage 和 workflow docs 中记录 code-task environment probe。
- 记录 V2.1 code-task environment isolation 方向：current interpreter、explicit external interpreter、per-run venv、shared environment cache 和 Docker。
- 更新 README 和 development docs，反映当前 V2.1 code-task baseline、comparison 和 module structure。
- Code-task benchmark runs 使用 labelled artifact directories（`baseline` 和 `patched`）。
- Benchmark execution reports 记录 selected environment mode 和 Python executable。
- Patch planning 会包含已记录 environment、validation 和 baseline metric context。

## 2026-05-14

### Added

- 添加 README capability boundaries，说明当前 research、report 和 code-task 能力边界。
- 强化 research-only survey report 和 embedded code-task demo report 的 prompt rules。
- 添加 report-bound checks，限制 toy-demo 对 accuracy、effectiveness、feasibility、generalization 的过度声称。
- 添加 fixture/code-task fallback discussion，把 offline fixture synthesis 视为 traceability context，而不是真实文献证据。

### Changed

- 更新 `report_quality.json` 文案，使 metric-table checks 明确只在 parsed metrics 存在时适用。
- 更新文档，解释 guarded LLM report drafting，以及 standalone `code-task` 与 embedded 8-stage demo 的当前边界。

### Verified

- 运行真实 LLM-backed literature-only flow：`synthesize -> report`。
- 运行真实 LLM-backed 8-stage `llm_code_task_toy_spam` demo，包括 patch planning、controlled edit proposal、benchmark execution 和 report generation。

## 2026-05-13

### Added

- 添加 automatic report mode selection：缺少 `results.json` 时生成 literature-only narrative，有结果时使用 experiment sections。
- 为 `simple-ar run` 和 `simple-ar resume` 添加 `--report-mode {auto,research_only,experiment}`。
- 添加 research-only report fallback sections：`Search Scope`、`Thematic Synthesis`、`Approach Patterns`、`Open Questions`、`Limitations`、`Conclusion`。
- 在 `08-report/manifest.json` 中记录 report mode。

### Changed

- 放宽 report stage contract，不再要求 `results.json`，支持 `synthesize -> report`。
- 更新 report LLM prompt，根据 research-only 和 experiment mode 切换结构和规则。
- 在 `docs/USAGE.md` 和 `docs/WORKFLOWS.md` 中记录 report-mode 行为和 synthesize-to-report flow。

## 2026-05-12

### Added

- 添加 `08-report/report_quality.json`，检查 citation provenance、body-cited papers、metric visibility 和 runtime/fallback disclosure。
- 添加 code-task benchmark runs 和 failure analysis 后的 `code_task/summary.md` 自动生成。
- 添加实验性 `llm_code_task_toy_spam` 8-stage experiment template，把更安全的 code-task patch workflow 嵌入 plan/search/read/synthesize/design/code/run/report pipeline。

### Changed

- 重写 `README.md`，整理为更清晰的开源项目入口，包含 setup、environment configuration、quickstart、workflow presets、docs links、reference 和 community。
- 减少 code-task patch IO，移除完整 pre/post workspace manifest artifacts；`applied_edits.json` 只保留修改文件的 before/after hashes。
- 将详细文档整合为 `docs/USAGE.md`、`docs/WORKFLOWS.md` 和 `docs/DEVELOPMENT.md`。
- 移除重叠文档：`docs/CODE_TASK.md`、`docs/RUN_ARTIFACTS.md`、`docs/CLI_AND_CONFIG.md`、`docs/EXTENDING.md`。

### Notes

- `CHANGELOG.md` 保持为按时间记录的开发日志，而不是版本规划文档。
- 规划重的笔记留在 `MDfiles/`，与公开文档分离。

## 2026-05-11

### Added

- 添加 `code-task validate`，提供轻量 Python syntax checks、risky import/call warnings、missing import warnings 和 strict-mode hazard errors。
- 添加 `code-task run`，在 copied workspace 中执行 benchmark command，包含 timeout、stdout/stderr、return code 和 parsed metrics。
- 添加 `code-task analyze-failure`，把最近失败的 benchmark run 转成紧凑 Markdown diagnosis。
- 添加 `code-task repair`，从 failure analysis 生成 bounded repair edit proposal，但不自动应用。
- 添加 `src/simple_ar/code_task/state.py`，集中管理 code-task path、manifest 和 safe workspace path helpers。
- 添加 realistic code-task smoke example：`examples/code_tasks/toy_spam_project`。
- 添加 `tests/test_code_task_examples.py`，验证示例 benchmark 先失败、workspace patch 后通过，并且不修改原始项目。
- 添加初始 code-task usage、artifact layout、CLI/config direction、workflow composition 和 extension guidance 文档。

### Changed

- 将长命令和 artifact 说明从 `README.md` 移入专门 docs。
- Code-task artifacts 保持在 `code_task/workspace` 和 `code_task/meta` 下，避免 run root 膨胀。
- 将 code-task execution artifacts 分组到 `code_task/run`，repair attempts 分组到 `code_task/repairs`。
- 记录未来 CLI/config 方向：底层步骤稳定前保留 primitive commands，再添加 config-driven convenience workflows。

### Verified

- `uv run python -m unittest tests.test_code_task_examples`
- `uv run python -m unittest discover -s tests`

## 2026-05-10

### Added

- 添加 controlled `code-task propose-edits`，用于模型生成 JSON old/new replacements。
- 添加 controlled `code-task apply-edits`，在 `code_task/workspace` 中安全应用 approved replacements。
- 添加 patch artifacts：`code_task/patch.diff`、`code_task/meta/proposed_edits.json`、`code_task/meta/applied_edits.json`。

### Changed

- 更新 code-task status output，展示 patch state 和 changed files。
- 在详细 docs 拆分前更新 README code-task quick usage。

### Verified

- `uv run python -m unittest discover -s tests`

## 2026-05-09

### Added

- 添加初始 `code-task init` workflow，将已有代码库复制到隔离 run workspace。
- 添加 Python-aware `codebase_index.json`，记录 file hashes、role tags、imports、functions、classes、tests 和 entrypoint candidates。
- 添加 `code-task plan`，根据 task、codebase index 和 selected snippets 生成 human-reviewable patch plan。
- 添加 `code-task decide-plan`，记录人工 approval、rejection 或 revision request。

### Changed

- Code-task artifacts 放在 `code_task/workspace` 和 `code_task/meta` 下，而不是向 run root 增加更多文件。

## 2026-05-08

### Added

- 添加本地 artifact inspection：`simple-ar inspect`。
- 添加本地 lexical artifact search：`simple-ar search-artifacts`。
- 默认只做 source-only chunking，`--include-operational` 用于调试 runner metadata。
- 添加 evidence-aware run artifacts：`source_plan.json`、`activity_log.jsonl`、`evidence_ledger.jsonl`。
- 将 retrieval snippets 接入 `read`、`synthesize` 和 `report` 阶段。
- 添加 retrieval controls：`--retrieval-top-k`、`--no-retrieval`。

### Changed

- 改进 report citation checks，使 body citations 和生成的 `references.bib` 保持一致。
- 使用 `--allow-fixture-fallback` 显式控制 fixture fallback。
- Live literature search 顺序更新为 OpenAlex、arXiv，然后 provider-specific cache。

## 2026-05-06

### Added

- 发布 V1 teaching pipeline baseline：
  - `01 plan`
  - `02 search`
  - `03 read`
  - `04 synthesize`
  - `05 design`
  - `06 code`
  - `07 run`
  - `08 report`
- 添加 file-based stage contracts 和 resumable runs。
- 添加 OpenAI-compatible LLM calls，并显示进度和 usage logging。
- 添加 `.env` 配置：API key、base URL、model name 和可选 cost estimates。
- 添加 OpenAlex/arXiv-backed paper metadata 和 local cache support。
- 添加 offline fixture mode，支持 deterministic tests 和 demos。
- 添加基于模板的 `toy_text_classification` 实验生成。
- 添加 subprocess experiment execution，包含 timeout、stdout、stderr、return code 和 parsed metrics。
- 根据已知 paper metadata 生成 deterministic BibTeX。

### Verified

- 覆盖 contracts、pipeline behavior、literature parsing、LLM adapters、report packaging 和 experiment execution 的单元测试。
