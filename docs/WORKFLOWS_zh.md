# 工作流与产物

[English version](WORKFLOWS.md)

本文说明 SimpleAutoResearch 内部在做什么：工作流预设、pipeline 阶段、artifact 归属和模块边界。它不重复完整文件手册；具体命令和文件树见 [使用与配置](USAGE_zh.md)，命令参数见 [CLI 参考](CLI_REFERENCE_zh.md)，TOML 字段见 [配置参考](CONFIG_REFERENCE_zh.md)。

## 工作流预设

当前 8 阶段 pipeline 只是一个预设，不是整个架构本身。SimpleAutoResearch 保持 module-first，这样 literature review、code improvement、experiment execution 和 report writing 可以重新组合。

## Capability 运行

在这些 workflow preset 之外，`simple_ar.core` 为新的可替换能力提供了一层可选
边界。能力通过 `CapabilityContext` 接收已经声明的输入引用，通过 attempt-local
的 `ArtifactStore` 写出结果，并返回 `CapabilityResult`。`SessionController` 可以
持久化一个有界 attempt 和对应 decision，但不会把现有 pipeline 变成不受限制的
任务图。

这层边界是增量式的：它不会自动迁移八个阶段，也不会改变现有命令和 adapter 依赖
的产物路径。`examples/capability_package_minimal/` 提供最小离线 handoff 示例；
具体领域的 schema 应属于对应 capability，不应继续堆进共享 core。

session 还可以选择一个可选的 lifecycle profile，限制本次 session 可以执行的
capability。内置范围包括 `research_brief`、`survey`、`experiment`、`paper_audit`
和 `full_research`；它们只是 allow-list，不是自动运行器。无法识别的 profile 名称
仍按旧调用方式兼容处理。
新建的已知 profile session 如果没有显式预算，controller 会按每个声明能力一次 attempt
再加两次有界恢复机会分配默认预算；调用方可用 `BudgetState` 覆盖，加载旧 manifest 时则
继续使用其中持久化的预算。

如果调用方需要串起多个 capability，可以使用 `SessionStep` 和
`run_session_plan()`。它会在第一个 handler 启动前检查完整的有序序列，持久化每个
attempt，并在第一次非 accept 决策处停止。进程恢复时应先加载 session、查看状态和
attempt lineage，再显式构造下一段序列；core 不会静默重跑中断的 attempt，也不会替领域
规则选择所谓最佳结果。
如果已经人工确认发生了中断，调用方可以使用
`SessionController.recover_interrupted()`，先把遗留的 running attempt 收束为明确失败，
再显式构造 retry 或 repair attempt。该方法不会自动重试，也不会覆盖已有的 result envelope。
只要前一个 attempt 仍标记为 `running`，controller 就会拒绝新 attempt，避免恢复前悄悄形成第二条活动分支。
创建后续 attempt 前，controller 还会根据持久化的当前 attempt 检查实际调用的
capability 是否属于白名单转移。这可以拦住被省略或被替换的路径，同时保留已列出的回退
以及同能力 repair。
显式 plan 入口对续跑序列的第一步也执行同样的检查，并在创建新 attempt 前拒绝缺失的
输入 artifact。

如果后一个 capability 需要使用前一个 attempt 的已声明输出，应调用
`SessionController.attempt_output_refs()`。它只把 attempt 内的相对路径转换成
session 根目录引用，不复制或合并产物；使用哪个 attempt、哪个输出仍由调用方决定。
如果需要从较早的 `completed` 或 `failed` attempt 开始另一条比较路径，可以在
`SessionController.execute()` 中传入它的 `parent_attempt_id`。controller 会记录该
父节点，并按父节点的 capability 校验新的转移；不传时仍沿用当前 attempt 的线性行为。
controller 不会自行推断分支，也不会替调用方选择结果。需要展示某个节点的父链时可使用
`attempt_lineage()`；它只读取持久化的 attempt manifest，不合并产物或调度新工作。

当前第一条领域纵向切片可以显式注册
`research.brief.run_research_brief_capability()`。它组合现有的 Read 和 Synthesis 边界，
并为后续 capability 暴露一个结构化的 `research_brief.json` 输出。这是可选的适配器，
不是对现有八阶段 research pipeline 的自动替换。

如果应用需要使用内置适配器，也可以调用
`research.register_research_capabilities(registry, names=...)`。不传
`names` 时注册完整适配器集合，传入时只注册当前路径需要的能力；注册仍然是
显式操作，不会创建调度器。该 helper 覆盖确定性的 research planning、Search、
Document Ingest、Read、Synthesis、Experiment、Analysis、Report、Report Audit
和 Research Brief。
独立结果分析能力的规范名称是 `analysis`；为兼容旧调用方，显式选择时仍保留
`analyze` 这个 registry/session 别名。

其中 `plan` 适配器复用已有的确定性问题、查询和来源预算 builder，写出一个
`research_plan.v1` handoff；它不增加 LLM 调用，也不替调用方选择下一能力。
领域专属的 `design`、`code`、`run` 实现仍由调用方提供。
如果要把该计划交给已有的 `SearchRequest`，可以使用
`research.planning.search_request_from_plan()` 这个内存适配器；它不会调用 provider，
也不增加 retry、去重或候选选择策略。

如果调用方已经拥有输入，也可以分别注册
`research.evidence.reader.run_read_capability()` 或 `research.synthesis.run_synthesis_capability()`：前者
接收 `DocumentBundle` 并写出 `read_result.json`，后者接收 expanded evidence pack 并写出
`synthesis_result.json`。两者都不会自行下载文档、调用 LLM 或决定阶段转移。

如果 session 从检索开始，也可以显式注册
`research.sources.run_search_capability()`。它会在 attempt 目录写出一个
`search_result.json`，包含规范化论文行以及 provider/query 的响应状态。这只是交接产物，
不替代旧 Search projection，也不改变候选选择策略。

如果调用方希望从证据综合直接进入独立的执行适配器，有限 recipe 允许
`synthesize -> experiment -> analysis`。调用方仍需提供 `ExperimentRequest`、执行 backend
和下一步 decision；core 不会替调用方推断 design 或 repair 策略。
若该请求来自持久化的 `synthesis_result.v1`，可以使用
`research.experiment_request_from_synthesis()` 转移已有的 research-level 实验契约；
`RunRequest`、result schema 和是否执行仍由调用方显式提供。该 helper 不批准
`needs_review`，也不隐式执行、重试或选择下一阶段。

如果下一步需要全文资源，可以使用 `DocumentIngestRequest` 显式注册
`research.documents.run_document_ingest_capability()`。它会写出一份可恢复的
`document_bundle.json`，包含文档记录、section、chunk 和 extraction 状态。后续 Read attempt
可以通过 `DocumentBundle.from_handoff_dict()` 加载这份声明过的产物；ingest 本身不选择论文，
也不调用 LLM。

Read attempt 完成后，可以用 `ReadResult.from_handoff_dict(payload, bundle=bundle)`
恢复 typed cards；调用方必须显式提供原始 document bundle，因此 Read 产物不会再次复制
source chunk 原文。如果调用方要直接组合 Read 与 Synthesis，可使用
`research.brief.evidence_pack_from_read()` 这个小型适配器，把 cards 转成 Synthesis 所需的
最小输入。
Read 在生成和恢复时会检查 cards 的 `evidence_refs` 是否仍指向 bundle 中的 chunk；失效引用
会记录诊断并将结果标记为 `partial`，但不会扫描其他文件或阻断 metadata-only 的兼容读取。

执行切片遵循同一规则：session 需要运行 `RunRequest` 时，显式注册
`research.experiment.run_experiment_capability()`。它把现有 canonical result 暴露为
`results.json`，并把捕获到的 stdout/stderr 以同一 attempt 下的
`execution/stdout.txt`、`execution/stderr.txt` 声明。分析步骤可以显式注册
`research.analysis.analyze_experiment_capability()` 读取该引用；失败或超时执行不会被
转换为成功 capability，诊断日志仍可供后续 capability 使用。分析缺少必要证据时返回
`partial`，只有分析状态明确为 `passed` 才返回 `completed`。持久化的
`analysis_handoff.v1` 可通过 `AnalysisHandoff.from_handoff_dict()` 恢复；恢复只验证结构
并保留 execution ref，不会重新执行实验。
如果已有两份完成的结果 mapping，可以使用
`research.analysis.compare_experiment_results()` 生成状态与指标比较，并作为
`ExperimentRequest.comparisons` 传入；方向未知或证据不足时保持为 `inconclusive`，后续
是否继续实验仍由调用方决定。
对应的 `AnalysisResult` 还提供保守的证据状态：`passed`、`failed`、`blocked`、
`incomplete` 或 `metric_below_target`。它要求显式的 execution handoff，不会自动安排
retry 或阶段转移；持久化的独立分析还会写出 `analysis_status.json`。
需要把领域结果交给有限 session policy 时，可使用
`research.decisions.transition_request_from_synthesis()`、
`transition_request_from_analysis()` 或 `transition_request_from_report_audit()`。它们只生成
已有 transition 输入，不执行下一步、不自动重试，也不覆盖原有 attempt。Synthesis 的
`needs_review` 只表达证据不足；回到 Search、Read 还是继续综合仍由调用方决定。

如果 session 只需要审查已经组装好的报告，可以显式注册
`report.audit.run_report_audit_capability()`。调用方传入报告 artifact 引用和 typed report
状态；适配器保持现有 `report_audit.json` 格式，并把 warning 报告为 partial，不会静默当成
干净通过。

如果调用方已经拥有完成的 section draft，可以先显式注册
`report.capability.run_report_capability()`。它复用现有 report assembler、可选标题编号和
可选的计划图表 renderer，在 attempt 目录生成 `report.md`；生成的图文件也会作为同一 attempt
的 `figure` 输出引用登记，figure manifest 只是索引，缺失图文件会报告为 `partial`。它不会
调用 writer，也不会生成 audit。随后把声明出的 report 引用传给独立的 audit capability。

### 1. Research Report：文献优先

适合想要 literature review、survey 或 DeepResearch-like report，而不强调实验执行的场景。

概念流程：

```text
plan -> search -> read -> synthesize -> report
```

当前现实边界：

- `run --to-stage report` 仍会执行 design/code/run，因为默认 pipeline 是教学 demo。
- 如果只想做纯文献流程，先停在 `synthesize`，再 resume 到 `report`；`auto` 模式会因为没有 `results.json` 而生成 research-only report。

### 2. Code Task：已有代码库

适合已经有代码，希望进行有目标的修改、优化、修复或 benchmark improvement。

概念流程：

```text
init workspace -> index code -> map repo -> probe environment
-> apply baseline policy -> plan patch -> approve -> propose edits -> apply edits
-> review changes -> validate -> run patched benchmark -> post-run review
-> compare results
-> analyze failure -> repair proposal
```

关键边界：

- 源项目会准备到 `code_task/workspace`。已有项目默认 `auto`：优先为已有 commit 的 Git 项目创建 detached `git_worktree`，如果 Git 条件不满足则降级为受保护的 `copy`，并记录原因与下一步建议。monorepo 场景下会在仓库根创建 worktree，并把对应项目子目录作为可编辑 project root。实验性 `sparse_copy` 只复制配置的 include patterns，并始终排除 data/model/cache/secret-like 路径。原始代码不会被修改。
- Patch application 必须经过显式人工 approval gate。
- Edit proposal 是保守 old/new replacement，不是自由形式重写。
- 默认 editor backend 是 `controlled_patch`；backend interface 现在已经显式存在，后续外部 agent 可以接到同一套安全和审核 gate 后面。
- 同一个文件可以有多个有序 edit，但每个 `old` block 必须保持唯一匹配；无效 proposal 会在写文件前停止。
- `code-task execute` 可以推进下一步，但会在 plan approval 和 proposal review 处停下，除非用户显式继续。
- Work-plan item 应该是可执行的 implementation batch。executor 在选择第一个 active batch 时会跳过明显的纯分析 item，因此 LLM 生成的“先 inspect 项目”不会意外限制后续 edit 阶段。
- 如果多个已审核 work-plan item 形成小型串行依赖链，且必须一起落地才可运行，比如 feature producer、model consumer 和 config switch，active batch 可以把它们合并。拆分后的计划仍然可见，`batch_state.json.work_item.source_work_item_ids` 和合并后的 `target_files` 会记录实际执行范围。
- benchmark 通过的 repair 不自动等于任务成功。最终是否 improved 要看 `code_task/run/comparison.json`；如果 patched 指标仍低于 baseline，只能说明流程恢复到可运行或超过 benchmark floor，还没有真正完成“提升”目标。
- baseline 运行是策略，不是无条件成本。`auto`/`run` 会记录未修改指标，`skip`/`none` 会继续执行但不做 comparison，`provided` 会把用户提供的指标写入 artifacts 并标注来源。
- 当前执行有 workspace isolation 和明确 interpreter policy。支持 `current` 和 `external`；自动创建环境留到后续。`workspace.reuse_source_venv` 可以把 worktree/copy/sparse run 指向 source 项目已有 `.venv` Python，但不会安装依赖。

内置示例：

- `examples/research_report/`：纯 research-only 流程，覆盖 search/read/synthesize/report，并支持 live academic sources 和 report variant。
- `examples/code_task_medium_review/`：standalone code-task 流程，目标是一个多模块 review classifier，入口是 `main.py`，使用 JSON config，运行时有进度输出，任务自然涉及 feature extraction、model scoring 和配置文件之间的联动。
- `examples/full_pipeline_tiny_mlp/`：完整 8 阶段流程，目标是轻量 NumPy MLP benchmark，适合不依赖 GPU 的端到端本地检查。

### 3. Research With Experiment：研究衔接实验

适合希望从研究想法走到可执行实验和有结果支撑的报告。

概念流程：

```text
plan -> search -> read -> synthesize -> design experiment
-> template codegen or embedded code-task -> run benchmark -> report
```

当前状态：

- `06-code` 可以生成白名单 template experiment、为已有项目准备内嵌 code-task workspace，也可以在没有现成源码时调用统一 code-task greenfield engine。greenfield 情况下，真实嵌套 run 位于 `06-code/code_task_run/`，兼容产物会再投影回 `06-code/generated_project/`。
- `--experiment-template code_task_project` 是通用内嵌 handoff，会接入 code-task workflow。它接受 `--code-task-config`，也接受显式 `--code-root`、可选 `--task-file` 和 `--benchmark-command`。如果没有 task file，`05-design` 会基于前面研究产物和紧凑代码摘要生成 `generated_code_task.md`。
- 内嵌生成任务会包含来自 synthesis/design artifacts 的 Research-to-Code Bridge，让 code-task planning 能看到方法迁移线索、实现假设、指标契约、消融目标、资源约束和风险提示。
- `simple-ar run --config ...` 是保持多参数 research/code-task run 可读、可复现的推荐方式。
- `--experiment-template llm_code_task_toy_spam` 仍保留为 bundled smoke-test template。
- 内嵌路径是端到端的：它会构建和 standalone code-task 一致的 repo map / context pack、work plan、attempt/batch 证据，然后在准备好的 workspace 内自动批准 patch plan。standalone code-task 仍是更安全的人工审核路径。
- Report generation 有保护：只有 citation、metric visibility、fixture disclosure 和 toy-demo boundary 检查通过时，才接受 LLM draft。

## 默认 8 阶段 Pipeline

```text
01 plan        限定主题和研究问题
02 search      检索论文 metadata、全文和本地 chunks
03 read        筛选、排序并结构化阅读检索结果
04 synthesize  分析主题、gap 和可实验假设
05 design      创建实验计划
06 code        生成实验代码或准备内嵌 code task
07 run         执行实验并解析指标
08 report      写带引用的 Markdown 报告
```

| 阶段 | 主要输出 | 目的 |
| --- | --- | --- |
| `plan` | `goal.md`, `problem.md` | 把主题收束成具体研究问题；启用 LLM 时由 LLM 支持。 |
| `search` | `papers.jsonl`、`search_meta.json`、`documents/`、`research_index/` | 检索和摄取 metadata/全文，记录 provider provenance，并构建本地 chunks。它可以为了预算做候选选择，但不做语义阅读审查。 |
| `read` | `review/`、`paper_notes.json`、`notes.md` | 对检索结果做筛选和阅读优先级排序，再把 shortlist 转成规范化 Paper Brief；启用 LLM 且检索量较大时，先按 title/abstract 小批次粗筛，再重排保留集合。 |
| `synthesize` | `synthesis_brief.json`、`synthesis.md`、`hypothesis.md` | 基于 read 阶段 Paper Brief 分析主题、gap、有限 ideas 和可测试假设；启用 LLM 时由 LLM 支持。 |
| `design` | `experiment_plan.json`、`experiment_contract.json`、`result_schema.json`、`resource_plan.json`、`dependency_plan.json`、`domain_profile.json`、`contract_validation.json` | 选择安全实验模板，并写出可执行契约、指标 schema、资源/依赖预算、domain profile 和代码前检查。 |
| `code` | `code_task_run/`、`generated_project/`、`experiment.py` 或模板代码 | 基于 design contract 准备内嵌已有项目 code-task、运行统一 greenfield code-task 生成，或写出白名单模板实验。 |
| `run` | `results.json`、`guard_report.json`、`stdout.txt`、`stderr.txt` | 执行实验，写出 canonical results，并在报告前检查缺失/异常指标。 |
| `report` | `report.md`, `references.bib`, `manifest.json`, `report_quality.json`, `report_memory.json`, `report_audit.json` | 基于模板写带 citation 的报告，并保留有界 source backtracking、报告记忆和审计产物；启用 LLM 时由 LLM 支持。 |

## Search 与 LLM 边界

Search 是检索入口，不是完整证据引擎。它会收束研究问题、选择 source 顺序、检索候选文献、记录 provider provenance，并构建 document/full-text/index 产物。它可以为了预算做候选排序和截断，但语义筛选、结构化阅读、综合和实验契约分别由后续阶段负责。

普通运行默认只保留紧凑产物：

```text
02-search/
  papers.jsonl / search_meta.json
  documents/       # 标准化 document records，以及 full-text/cache manifests
  research_index/  # 可迁移 chunks 与本地索引 metadata
```

`03-read` 负责筛选、重排和 Paper Brief。LLM 模式下，它会先并发粗筛紧凑 title/abstract 批次，再给保留集合分配阅读优先级、证据角色和 synthesis hint。`04-synthesize` 默认生成 `synthesis_brief.json`、`synthesis.md` 和 `hypothesis.md`；旧的 cards/evidence pack 诊断产物只在 `[run].debug_artifacts = true` 时保留。`05-design` 负责 experiment contract 和可选 tool handoff 草案。

当 `[run].debug_artifacts = true` 时，search 还会保留 planning 文件、retrieval traces、retrieval-selection rows、coverage-review reports 和 section tables。Design 的 debug 模式可以保留只读 tool-context 草案、adapter notes 和 governance artifacts。

共享加速索引默认放在 run 目录之外的 `.simple_ar_cache/research_index`，按 run/source metadata 组织。run-local 的 PDF 下载缓存和 extracted text 属于可重建内容，可以用 `simple-ar clean` 预览和清理。

LLM 参与是有边界的。research planner 可以使用 deterministic、`auto` 或 LLM 模式；coverage check 和本地 novelty check 只是风险信号，不是原创性证明。`--no-llm` 会让 plan/read/synthesize/report 使用 deterministic fallback 文本。

完整 search-stage 文件树和逐文件说明见 [使用与配置](USAGE_zh.md)。search、cache、parser 和 debug artifact 配置见 [配置参考](CONFIG_REFERENCE_zh.md)。

## Artifact 归属概览

WORKFLOWS 只保留产物归属层面的说明；完整文件树放在 [使用与配置](USAGE_zh.md)。概括来说：

- run 根目录文件（`state.json`、`manifest.json`、`config_snapshot.json`、usage logs，以及可选 artifact indexes）负责 resume state、配置快照和可观测性。
- 阶段目录（`01-plan` 到 `08-report`）各自拥有自己的 contract、report 和稳定 handoff artifact。
- `02-search` 负责 retrieval、document/full-text 状态和本地 chunks。
- `03-read` 负责 reading review、shortlist、literature cards 和结构化阅读笔记。
- `04-synthesize` 负责从 read 阶段产物推导出的紧凑 evidence bridge、gaps、ideas、novelty hints、synthesis 和 hypothesis。
- `05-design` 负责 experiment contracts、result schemas、resource/dependency
  plans、domain profiles、contract validation 和 experiment plans。
- 当 research pipeline 衔接代码执行时，`06-code/code_task_run` 会嵌入与 standalone code task 相同形态的 artifact。
- `08-report` 负责最终报告包：报告正文、references、manifest、紧凑报告记忆、source/citation/metric audit 和质量检查。

这样可以让详细运行文件保持可追踪，同时不要求读者在理解 workflow 前先读完每个 JSON/JSONL。主要用于诊断或可重建的文件，应该通过 `debug_artifacts` 管控，或明确标注为 cleanup-safe。

## Code Task Artifact 边界

Standalone code task 和嵌入 8 阶段 pipeline 的 code task 使用相同的概念布局。重点不是记住每个文件名，而是理解每组 artifact 的职责：

- `workspace/`：隔离后的可编辑项目副本、worktree 或 sparse subset。
- `meta/`：环境报告、repo map、locate results、edit proposals、validation reports、applied-edit summaries 和 LLM usage。
- `context_packs/`：从候选可编辑文件和受保护只读证据中组装出来的有界 prompt context。
- `attempts/`：多步骤实现和 repair loop 的 work-plan / batch state。
- `run/`：baseline/patched benchmark 日志、metrics、execution reports、failure analysis 和 before/after comparison。
- `repairs/`：按 repair attempt 分组的有界修复 proposal。

tests、benchmarks、环境文件、secrets 和用户配置的 protected paths 默认作为只读证据被索引，不应被 proposal、repair 或 apply 步骤修改。Edit scope 行为和完整 artifact 路径见 [使用与配置](USAGE_zh.md) 与 [配置参考](CONFIG_REFERENCE_zh.md)。

## Code-Task 环境策略

环境处理和源码隔离是两件事：

- 源码隔离：用户代码会先准备到 `code_task/workspace`，再应用任何补丁。默认 `auto` 通常为已提交的 Git 项目创建 detached worktree，Git 不可用时降级为受保护 copy；monorepo 子目录会成为实际可编辑 project root。`sparse_copy` 是实验性 allowlist copy。
- 执行隔离：benchmark 使用选择的 Python/runtime 环境运行。

今天 code-task 已经有第一类隔离，并通过 `meta/environment_report.json` 记录环境信号。它可以选择当前 SimpleAutoResearch Python，也可以选择用户提供的 external interpreter。它还不会自动创建 venv 或安装依赖。

计划中的环境模式：

- `current`：使用当前 SimpleAutoResearch Python。已支持。
- `external`：使用用户提供的 Python 或 Conda interpreter。已支持。
- `project-venv`：在 run 目录内创建 per-run 环境。计划中。
- `shared-env-cache`：按 dependency-file 和 platform hash 复用环境。计划中。
- `docker`：需要更强隔离时在容器中运行。计划中。

默认应保持保守：依赖安装必须显式、可审核，并且不应默默把用户项目包安装进 SimpleAutoResearch 自己的环境。

## 为什么要拆分工作流

拆分能避免项目变成一个僵硬的大 pipeline：

- 用户只想写 survey 时，不应强制运行代码阶段。
- 用户只想优化已有代码时，文献阶段应可选。
- 用户想做完整 automatic-research loop 时，可以组合模块。
- 每个模块都可以独立升级。

这也来自 AutoResearchClaw 的一个实践启发：复杂行为如果暴露成 workflow modes 和 capabilities，会比塞进一条不断膨胀的 flag 序列更可控。
