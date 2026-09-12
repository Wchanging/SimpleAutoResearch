# 工作流与产物

[English version](WORKFLOWS.md)

本文说明 SimpleAutoResearch 内部在做什么：工作流预设、pipeline 阶段、artifact 归属和模块边界。它不重复完整文件手册；具体命令和文件树见 [使用与配置](USAGE_zh.md)，命令参数见 [CLI 参考](CLI_REFERENCE_zh.md)，TOML 字段见 [配置参考](CONFIG_REFERENCE_zh.md)。

## 工作流预设

正式研究入口是 `research-session`，在同一会话中维护 attempt、产物、报告与审计，
按请求选择文献或实验任务。旧八阶段执行器已退出，不再作为兼容工作流运行。
SimpleAutoResearch 仍保持 module-first，但模块化发生在内部 capability 和应用层；它让
测试、恢复、开发者接口以及后续 workflow 可以复用能力，不意味着普通用户需要在多条入口
之间自行拼接完整流程。

```text
正式用户入口
research-session
  -> plan -> search -> document_ingest -> read -> synthesize
  -> research_design -> experiment -> analysis -> report -> report_audit

内部可复用/分段接口
research-brief

历史读取入口
simple-ar status RUN_DIR -> 只读展示存档
```

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

如果调用方需要串起多个 capability，应由 application 层按明确顺序调用
`SessionController.execute_attempt()`。它持久化执行事实；是否停止、继续或完成研究由应用决定。
进程恢复时应先加载 session、查看状态和 attempt lineage，再显式构造下一次调用；core
不会静默重跑中断的 attempt，也不会替领域规则选择所谓最佳结果。
如果已经人工确认发生了中断，调用方可以使用
`SessionController.recover_interrupted()`，先把遗留的 running attempt 收束为明确失败，
再显式构造 retry 或 repair attempt。该方法不会自动重试，也不会覆盖已有的 result envelope。
只要前一个 attempt 仍标记为 `running`，controller 就会拒绝新 attempt，避免恢复前悄悄形成第二条活动分支。
Core 在创建 attempt 前检查能力范围、预算和输入 artifact；研究顺序归应用负责，
执行边界不再施加第二套固定阶段转移规则。

如果后一个 capability 需要使用前一个 attempt 的已声明输出，应调用
`SessionController.attempt_output_refs()`。它只把 attempt 内的相对路径转换成
session 根目录引用，不复制或合并产物；使用哪个 attempt、哪个输出仍由调用方决定。
如果需要从较早的 `completed` 或 `failed` attempt 开始另一条比较路径，可以在
`SessionController.execute_attempt()` 中传入它的 `parent_attempt_id`。controller 校验并记录该
父节点；不传时仍沿用当前 attempt 的线性行为。
controller 不会自行推断分支，也不会替调用方选择结果。需要展示某个节点的父链时可使用
`attempt_lineage()`；它只读取持久化的 attempt manifest，不合并产物或调度新工作。

历史研究会话读取器展示记录过的 `next_capability` 和执行证据，不再用已退出的策略
重新推算下一步；新研究的动作选择归 ResearchApplication 负责。

如果库调用方只想得到一个内存中的聚合值，可以使用
`research.brief.build_research_brief()` 只保留为内存中的兼容视图。默认 registry 刻意不再
暴露聚合的 `research_brief` capability：session 分别持久化 `read` 与 `synthesize`。
历史 `research_brief.v1` handoff 仍可读取，但不会再成为第二条可执行 lifecycle。

下面的 `research-brief` 是分段/开发入口，不是 V2.8 完整主线。面向普通用户的完整流程应
优先使用 `simple-ar research-session`；需要只构建研究 handoff、调试阅读或从已有 handoff
开始时，才使用这些较小的组合入口。`research-brief` 现在只是参数/返回值适配器，
请求同一个 `ResearchApplication` 生成文献摘要，采用其生命周期及默认请求/token 预算，
不再维护独立编排器。其研究能力顺序为：

```text
plan -> search -> document_ingest -> read -> synthesize
```

主题检索可以直接运行：

```bash
uv run simple-ar research-brief --topic "reliable agents"
```

如果希望使用可复现的本地输入，可以重复提供 Markdown/TXT 文件：

```bash
uv run simple-ar research-brief --topic "reliable agents" \
  --local-document examples/research_brief/fixtures/reliable_agents.md \
  --output-root runs/research-brief
```

命令会在输出目录下创建带时间戳的 v2 session。每次交接保留在动态命名的独立 attempt 中，
实际路径从 `session_manifest.json.state_refs` 读取；下游命令使用 CLI 输出的
`Synthesis handoff` 路径，不再拼接固定 attempt ID。规范输出分别是
`research_plan.json`、`search_result.json`、`document_bundle.json`、`read_result.json` 和
`synthesis_result.json`。能力结果与 attempt manifest 会记录状态和 lineage。该入口不会
静默重试或覆盖旧 attempt；`--query`、`--provider`、`--max-results`、`--max-chunks` 和
`--idea-limit` 是这条路径保留的少量控制项，更复杂的策略仍由上层应用负责。旧的
`research_brief.v1` 聚合格式仍可作为输入交给后续入口。

这条 standalone 路径会明确区分模型模式。省略 `--model` 时，它是离线/确定性组合：搜索、
解析、card derivation 和结构化方向提取只使用已有输入；传入 `--model NAME` 后，使用现有
LLM client 完成研究规划、有界 Read 筛选/重排、paper notes 和综合，并在 handoff 中记录
Read provenance、`planner: llm` 与 `generation_mode: llm`。缺少凭据、传输失败或模型返回格式
错误时，对应 attempt 会失败，不会静默伪造模型结果。

分段 `research-experiment` 创建器已退出。实验与分析能力继续由正式应用复用，
不再为旧 synthesis 文件另建一套会话生命周期。

如果希望把完整流程保留在同一个 session 中，应使用唯一正式主线
`simple-ar research-session`。它复用相同的
`plan -> search -> document_ingest -> read -> synthesize` 前缀，记录一个
`research_design.v1` handoff，再用一个明确提供的 `ExperimentRequest` 进入现有 Analysis
capability。默认实验命令仍由调用方给出；传入 `--code-task-config` 时，experiment attempt
会改用已有的 project-style Code-Task backend，项目、benchmark、workspace、baseline 和
执行设置仍由 TOML 管理，最终输出会规范化为同一份 canonical result。这仍是受控组合，
不是不受限制的研究循环。

如果既没有提供实验命令，也没有传入 `--code-task-config`，同一个入口也支持 literature-only
组合：在有证据支持的 summary 处结束，不创建 execution 请求；提供模型时，可以继续进入
research-only 报告路径。这是明确支持的无实验形态，不会隐式生成占位实验。

如果 session 的实验失败但仍保留了 design 和 analysis handoff，可以显式追加一次恢复实验，
复用已有文献和研究设计，不重新检索：

```bash
uv run simple-ar research-session-continue \
  --session-root runs/research-session/<session> \
  --cwd examples/research_brief/fixtures \
  --primary-metric accuracy \
  --metric-direction accuracy=higher \
  --command python -c "print('accuracy: 0.90')"
```

对于 canonical `session_manifest.v2`，它会创建动态命名的新 experiment attempt，把失败候选记录为父节点，
然后复用已有文献和 design 做确定性 analysis。修正后的命令由调用方提供；不会重复 search 或 design，
原有 attempt 也不会被覆盖。这个边界只处理普通显式实验的技术失败；成对实验、数据准备和 CodeTask
使用各自的有界恢复路径。科学负结果是证据，不会被静默重跑。旧 v1 session 仍保留固定的
`experiment-002`/`analysis-002` 兼容行为。

使用 `--no-report` 创建的正式 session 可通过 `research-report` 补齐报告动作，复用既有证据
和测量。Writer 与检查点统一在 `report/writing.py`，组装和审计通过同一应用生命周期完成。

历史 session 可检查和读取，但不再由第二套 Writer/report/audit 执行器续写。
`build_research_session_report_inputs()` 和 `build_code_task_report_inputs()` 只投影已有证据，
不执行或修改会话。分段 `research-code-task` 创建入口已退出；完整任务使用
`research-session --code-task-config`。

如果应用需要使用内置适配器，也可以调用
`research.register_research_capabilities(registry, names=...)`。不传
`names` 时注册完整适配器集合，传入时只注册当前路径需要的能力；注册仍然是
显式操作，不会创建调度器。该 helper 覆盖确定性的 research planning、Search、
Document Ingest、Read、Synthesis、Research Design、Experiment、Analysis、Report、Report Audit
和 Research Brief。
独立结果分析能力的规范名称是 `analysis`；为兼容旧调用方，显式选择时仍保留
`analyze` 这个 registry/session 别名。

其中 `plan` 适配器复用已有的问题、查询和来源预算 builder，写出一个
`research_plan.v1` handoff；默认使用确定性路径，调用方显式传入
`use_llm=True` 和共享 client 时才会得到规范化的模型辅助计划。它不替调用方选择下一能力。
窄的 `research_design` 适配器接收持久化的 synthesis，默认选择调用方指定的研究方向；
调用方显式提供共享 LLM 时，它也可以只在已有候选方向中选择一个，并写出包含已有
`ResearchExperimentContract` 的 `research_design.v1` handoff。它只检查契约是否具备最小可执行
字段，不会自行创造 command、metric value、实验矩阵、代码或执行计划；领域专属的代码生成和
执行实现仍由调用方提供。
如果要把该计划交给已有的 `SearchRequest`，可以使用
`research.planning.search_request_from_plan()` 这个内存适配器；它不会调用 provider，
也不增加 retry、去重或候选选择策略。

如果调用方已经拥有输入，也可以分别注册
`research.evidence.reader.run_read_capability()` 或 `research.synthesis.run_synthesis_capability()`：前者
接收 `DocumentBundle` 并写出 `read_result.json`，后者接收 expanded evidence pack 并写出
`synthesis_result.json`。前者不会自行下载文档或调用 LLM；后者默认使用确定性结构推导，
只有显式传入 client 才调用 LLM。两者都不会决定阶段转移。

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
应用直接消费分析证据并负责下一动作。

如果 session 只需要审查已经组装好的报告，可以显式注册
`report.audit.run_report_audit_capability()`。调用方传入报告 artifact 引用和 typed report
状态；适配器保持现有 `report_audit.json` 格式，并把 warning 报告为 partial，不会静默当成
干净通过。

如果调用方已经拥有完成的 section draft，可以先显式注册
`report.capability.run_report_capability()`。它复用现有 report assembler、可选标题编号和
可选的计划图表 renderer，在 attempt 目录生成 `report.md`；生成的图文件也会作为同一 attempt
的 `figure` 输出引用登记，figure manifest 只是索引，缺失图文件会报告为 `partial`。它不会
调用 writer，也不会生成 audit。随后把声明出的 report 引用传给独立的 audit capability。

### 1. Research Report：文献优先（分段/高级用例）

适合想要 literature review、survey 或 DeepResearch-like report，而不强调实验执行的场景。
普通用户的完整 V2.8 主线仍应使用 `research-session`；这里描述的是可复用的分段能力边界。

概念流程：

```text
plan -> search -> read -> synthesize -> report
```

未提供执行命令或 CodeTask 配置时，`research-session` 使用纯文献路径，不启动实验。
报告属于同一生命周期；只有显式 `--no-report` 才省略该交付。

### 2. Code Task：已有代码库

普通 `execute` 默认只生成 patch plan；显式执行 `--to-step work-plan` / `--to-step batch`
或已有 work plan 时才使用分批路径。交互执行遵循同一规则，分批任务的作用域、批准与恢复记录保留。

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
- 受控补丁提案与应用直接调用实现，重复编辑适配层已退出；产物保留 `controlled_patch` 来源标记。外部 Harness 接入属于后续工作。
- 同一个文件可以有多个有序 edit，但每个 `old` block 必须保持唯一匹配；无效 proposal 会在写文件前停止。
- `code-task execute` 可以推进下一步，但会在 plan approval 和 proposal review 处停下，除非用户显式继续。
- Work-plan item 应该是可执行的 implementation batch。executor 在选择第一个 active batch 时会跳过明显的纯分析 item，因此 LLM 生成的“先 inspect 项目”不会意外限制后续 edit 阶段。
- 如果多个已审核 work-plan item 形成小型串行依赖链，且必须一起落地才可运行，比如 feature producer、model consumer 和 config switch，active batch 可以把它们合并。拆分后的计划仍然可见，`batch_state.json.work_item.source_work_item_ids` 和合并后的 `target_files` 会记录实际执行范围。
- benchmark 通过的 repair 不自动等于任务成功。最终是否 improved 要看 `code_task/run/comparison.json`；如果 patched 指标仍低于 baseline，只能说明流程恢复到可运行或超过 benchmark floor，还没有真正完成“提升”目标。
- baseline 运行是策略，不是无条件成本。`auto`/`run` 会记录未修改指标，`skip`/`none` 会继续执行但不做 comparison，`provided` 会把用户提供的指标写入 artifacts 并标注来源。
- 当前执行有 workspace isolation 和明确 interpreter policy。支持 `current` 和 `external`；自动创建环境留到后续。`workspace.reuse_source_venv` 可以把 worktree/copy/sparse run 指向 source 项目已有 `.venv` Python，但不会安装依赖。

内置示例：

- `examples/research_session_smoke.py`：正式研究应用 smoke；纯文献报告也使用同一应用，不再提供旧 pipeline 配置。
- `examples/code_task_medium_review/`：standalone code-task 流程，目标是一个多模块 review classifier，入口是 `main.py`，使用 JSON config，运行时有进度输出，任务自然涉及 feature extraction、model scoring 和配置文件之间的联动。
- `examples/code_task_digits_mlp/`：独立 CodeTask 的轻量 NumPy MLP benchmark，适合无 GPU 的真实 CPU 测量。

### 3. Research With Experiment：研究衔接实验

使用 `research-session`，显式提供执行命令或 `--code-task-config`。
应用负责研究生命周期；CodeTask 在准备好的工作区实现修改，experiment 能力负责实测。

概念流程：

```text
plan -> search -> document ingest -> read -> synthesize -> design
-> 按请求准备/实现 -> experiment -> analysis -> report -> audit
```

- 研究目标与限制通过 research handoff 传给 CodeTask。普通修改只需一份批准后的 patch plan；
  显式请求或已有 work plan 的任务保留分批路径。
- 实现产出冻结的 patch、validation、review 和计划证据；通过这些检查不等于科学上有提升。
- 实验结果和对照保留执行来源。失败进程不构成有效测量，合法负结果也不意味着应无限修复。
- 报告使用已记录的文献、实现和实验证据。机械审计通过不等于论文达到发表质量或语义已经正确。
- 恢复报告不能重跑已完成实验。入口参见上方会话命令与 `examples/research_session_smoke.py`；
  离线 smoke 使用 fixture，不是真实科研验收。

## 历史八阶段产物

旧八阶段执行器已经删除。`run`、`resume`、`research-code-task` 和
`research-experiment` 拒绝执行并提示使用 `research-session`；旧模板参数不再代表可执行工作流。

现有阶段目录存档不会被删除或改写。`status RUN_DIR` 可以展示其中记录的 manifest 和 pipeline state。
私有接口 `_legacy.documents.load_search_document_bundle(search_dir)` 可只读加载历史 Search
目录，不创建运行时 Context。历史产物名称描述的是存档证据，不是另一套可执行 pipeline。

## Search 与 LLM 边界

Search 检索记录并保留来源；文档摄取处理本地或允许访问的远程正文，Read 选择和分析证据，
Synthesis 综合证据，Design 提出实验方案。缺少全文应保留为明确限制，不能声称已完成全文阅读。

LLM idea 与本地新颖性检查只是研究建议，不是原创性证明；离线 fixture 输出不是模型完成的科研分析。
具体输入输出见上方 capability 入口。

## Artifact 归属概览

- `session_manifest.json` 记录会话和 attempt 状态；应用选择研究动作，共享 budget ledger 记录用量。
- `attempts/` 下各次执行拥有其声明产物；通过引用而非固定阶段编号连接检索、阅读、综合、实现、
  实验、分析、写作与审计。
- 实现冻结所测版本的 patch 与检查证据；实验执行拥有实测指标，分析模块负责解释。
- 报告写作、组装、审计保留各自的 attempt 产物。进程完成、工作流完成和论文达到发表质量是不同结论。
- 历史编号阶段目录仅作为存档。可重建缓存不是结果的事实来源，不能替代原始产物。

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

## 为什么内部要拆分能力

内部拆分能力并不等于把普通用户暴露到多条并行主线。它的作用是避免实现变成一个无法维护的
大 pipeline，同时让正式入口保持简单：

- 用户只想写 survey 时，不应强制运行代码阶段。
- 用户只想优化已有代码时，文献阶段应可选。
- `research-session` 可以按任务配置选择是否接入准备好的代码实验，但流程边界仍然固定。
- 测试、恢复、开发者和未来 workflow 可以组合模块；普通用户不需要理解内部组合细节。
- 每个模块可以独立升级，但不得形成第二套 session 状态、artifact 或报告核心。

这也来自 AutoResearchClaw 的一个实践启发：复杂行为如果暴露成 workflow modes 和 capabilities，会比塞进一条不断膨胀的 flag 序列更可控。
