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

如果调用方需要串起多个 capability，应由 application 层按明确顺序调用
`SessionController.execute()`。它会持久化每个 attempt，并在 decision 不允许继续时停止。
进程恢复时应先加载 session、查看状态和 attempt lineage，再显式构造下一次调用；core
不会静默重跑中断的 attempt，也不会替领域规则选择所谓最佳结果。
如果已经人工确认发生了中断，调用方可以使用
`SessionController.recover_interrupted()`，先把遗留的 running attempt 收束为明确失败，
再显式构造 retry 或 repair attempt。该方法不会自动重试，也不会覆盖已有的 result envelope。
只要前一个 attempt 仍标记为 `running`，controller 就会拒绝新 attempt，避免恢复前悄悄形成第二条活动分支。
创建后续 attempt 前，controller 还会根据持久化的当前 attempt 检查实际调用的
capability 是否属于白名单转移。这可以拦住被省略或被替换的路径，同时保留已列出的回退
以及同能力 repair。
controller 对续跑调用执行同样的检查，并在创建新 attempt 前拒绝缺失的输入 artifact。

如果后一个 capability 需要使用前一个 attempt 的已声明输出，应调用
`SessionController.attempt_output_refs()`。它只把 attempt 内的相对路径转换成
session 根目录引用，不复制或合并产物；使用哪个 attempt、哪个输出仍由调用方决定。
如果需要从较早的 `completed` 或 `failed` attempt 开始另一条比较路径，可以在
`SessionController.execute()` 中传入它的 `parent_attempt_id`。controller 会记录该
父节点，并按父节点的 capability 校验新的转移；不传时仍沿用当前 attempt 的线性行为。
controller 不会自行推断分支，也不会替调用方选择结果。需要展示某个节点的父链时可使用
`attempt_lineage()`；它只读取持久化的 attempt manifest，不合并产物或调度新工作。

`research-session` 应用现在通过只读的 `recommended_transition` 属性消费这个边界：执行和分析
都通过时建议继续到 `report`，其他结果则返回 `experiment` 边界，由调用方明确决定修复或重新设计。
这个建议使用核心 transition policy 和 session budget，不会创建 attempt、重新运行命令，也不会把失败
session 伪装成成功。

如果库调用方只想得到一个内存中的聚合值，可以使用
`research.brief.build_research_brief()` 只保留为内存中的兼容视图。默认 registry 刻意不再
暴露聚合的 `research_brief` capability：session 分别持久化 `read` 与 `synthesize`。
历史 `research_brief.v1` handoff 仍可读取，但不会再成为第二条可执行 lifecycle。

面向用户的最小组合入口是 `simple-ar research-brief`，它只负责一条明确的路径：

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

命令会在输出目录下创建带时间戳的 session。每次交接保留在独立 attempt 中，通常位于
`attempts/plan-001/`、`attempts/search-001/`、`attempts/document-001/`、
`attempts/read-001/` 和 `attempts/synthesize-001/`；规范输出分别是
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

下一条小组合入口是 `simple-ar research-experiment`。它接收前一个入口生成的
research direction，调用现有执行后端运行一次明确的命令，再把规范化的 `results.json`
交给现有结果分析能力。输入是持久化的 `research_brief.v1` 或 `synthesis_result.v1`，因此
方向到实验的交接仍然明确可检查。执行失败也会作为证据进入分析，但 retry、repair 和实验
选择仍由调用方负责。
传入 `--model NAME` 会额外启用共享 LLM 的结果分析；省略它时分析保持 deterministic，
实际使用的模式会记录在 analysis capability 的 provenance 中。

如果希望两段交接保留在同一个 session 中，可以使用
`simple-ar research-session`。它复用相同的
`plan -> search -> document_ingest -> read -> synthesize` 前缀，记录一个
`research_design.v1` handoff，再用一个明确提供的 `ExperimentRequest` 进入现有 Analysis
capability。默认实验命令仍由调用方给出；传入 `--code-task-config` 时，experiment attempt
会改用已有的 project-style Code-Task backend，项目、benchmark、workspace、baseline 和
执行设置仍由 TOML 管理，最终输出会规范化为同一份 canonical result。这仍是受控组合，
不是不受限制的研究循环。

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

它只会追加 `experiment-002` 和 `analysis-002`，并把失败的 `experiment-001` 记录为显式父节点。
修正后的命令由调用方提供；不会重复 search、design 或代码生成。每个 session 只允许一次这种恢复，
同时保留原有 attempt 和报告交接槽位，不覆盖旧产物。恢复成功后继续使用
`simple-ar research-report`；恢复失败仍会持久化并以非零 shell 状态返回，方便检查。

同一个 session 还可以通过 `run_research_report_session()` 继续进入现有 Report 边界。
调用方提供 section drafts、`ReportContext` 和需要追踪的 source refs；适配器会追加
`report` 与 `report_audit` attempt，不复制或替换之前的 analysis 产物。因此
`research-session` 前缀在实验和分析完成后会报告 `ready_for_report`，真正的报告 continuation
完成后 session 才会关闭。section drafts 仍由调用方明确提供，writer/revision 策略不会被
塞进生命周期 controller。

已有的 `simple-ar status <session-root>` 命令也支持 capability session 的
`session_manifest.json`。它只报告持久化的 session 检查点、attempt 状态、有限预算和最后一次
决策，不会重新运行或改写任何 capability；旧的 `manifest.json` status 路径保持不变。

如果希望由现有的 Writer/Reviewer 实现负责生成草稿，可以使用
`run_research_report_agent_session()`。它仍然只接收紧凑的 report context 和 memory，
另外显式接收已有的 template、runtime config、LLM client 与 tool gateway，然后把通过
校验的 section drafts 交给同一套 report 和 audit capability。Writer 轨迹会保存为
`inputs/report_agent_result.json`，并作为 report attempt 的输入引用；最终 `report.md`
仍是唯一的成稿正文。这个入口只是复用当前 report agent 的适配器，不复制 prompt，也
不新增另一套报告 pipeline。

对于标准的“文献到实验”路径，`build_research_session_report_inputs()` 会从
`ResearchSessionResult` 确定性整理紧凑的 context 和 memory：持久化的 synthesis、选中的
论文元数据、真实执行结果和结果分析 claims 都会保留为报告输入来源。
`run_research_session_report_agent()` 是这条路径的轻量便捷入口，但 template、运行预算和
LLM client 仍由调用方选择；它只是报告交接，不是自动研究调度器。该入口只接受执行和分析
均通过的 session（`report_ready=True`）。如果需要为失败或部分结果生成诊断报告，应改用
底层的显式报告边界，由调用方明确提供草稿和证据。

进程结束后，如果需要从已有 session 继续报告阶段，可以调用
`load_research_session_result()`。它只根据 `session_manifest.json` 以及声明的
`plan-001`、`search-001`、`document-001`、`read-001`、`synthesize-001`、可选的 `design-001`、`experiment-001`
和 `analysis-001` typed handoff 恢复同一个结果，不会联网、执行命令、重试或自行挑选“最好”的
结果。因此，调用方可以明确地把一个已完成的实验 session 交给 Report/Audit，而不必重跑
前面的阶段；缺失或格式错误的 handoff 会以 `ResearchSessionError` 失败。

如果需要把研究方向交给真实的代码实验，可以在应用层调用
`simple_ar.app.research_code_task.run_research_code_task_session()`。它读取持久化的
`synthesis_result.v1` 或 `research_brief.v1`，复用现有 Code-Task backend 完成隔离、代码
生成、验证、执行和结果分析，并把真实 execution/analysis refs 留在同一个 session 中。
V2.8 路径刻意只执行一个明确选定的研究方向。多候选比较要等单方向路径在真实准备项目
上验证稳定后再考虑，不作为当前主流程的一部分。

需要从命令行运行这一窄路径时，可以直接复用已有 Code-Task TOML：

```bash
uv run simple-ar research-code-task --topic "reliable agents" \
  --synthesis-file runs/research-brief/<session>/attempts/synthesize-001/synthesis_result.json \
  --code-task-config examples/code_task_medium_review/configs/code_task.toml \
  --output-root runs/research-code-task
```

加入 `--with-report` 后，会在同一个成功的 session 上继续使用已有的实验报告
Writer/Reviewer 和 audit：

```bash
uv run simple-ar research-code-task --topic "reliable agents" --synthesis-file runs/research-brief/<session>/attempts/synthesize-001/synthesis_result.json --code-task-config examples/code_task_medium_review/configs/code_task.toml --output-root runs/research-code-task --model "$SIMPLE_AR_MODEL" --with-report
```

只有执行和结果分析都通过时才允许这次接续；它不会重试，也不会把失败 session 伪装成正式报告。

该命令只执行一个研究方向。它要求配置中的 `[execute].use_llm = true`，并且当前只接入已有 project-style
Code-Task，不会替用户自动创建 GPU 环境或任意 greenfield 工程。

完成或失败的单个 Code-Task session 可以在后续进程中通过
`load_research_code_task_session_result(session_root)` 恢复。该入口只读取 session manifest、
声明的 synthesis input，以及 `canonical_results.2.5` 和 `analysis_handoff.v1` 输出；不会重新
执行 Code-Task、访问 provider、重试或在多个产物中自行选优。handoff 缺失或引用不一致时会以
`ResearchCodeTaskSessionError` 明确失败。

如果 session 在运行时显式为报告阶段保留了下一能力，可以先用上述恢复函数读取它，
再调用 `run_research_code_task_report_agent(session, ...)` 继续。这个 wrapper 要求持久化的
最后一条 decision 明确把 `report` 作为下一能力，然后复用通用 Writer/Reviewer 与
Report/Audit 路径；已经收束的 session 不会被偷偷重新打开。

`simple_ar.app.research_code_task_report` 可以继续把上述 session 的 execution 和 analysis
证据交给通用 Report/Audit。它只生成紧凑的 context、metric sources 和 claim evidence，
再复用现有 report assembler/audit；section drafts 仍需由调用方提供，不能把这条适配器
误解为自动论文 writer。

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
需要把分析结果交给 session policy 时，可使用
`research.decisions.transition_request_from_analysis()`。它只生成已有 transition 输入，不执行下一步、
不自动重试，也不覆盖原有 attempt；恢复策略仍由调用方和核心 session budget 负责。

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
- 内嵌路径是端到端的：它会构建和 standalone code-task 一致的 repo map / context pack、work plan、attempt/batch 证据，然后在准备好的 workspace 内自动批准 patch plan。严格的串行依赖链会合并为一个有界 batch（最多 3 个 work item、4 个目标文件），避免实现、接线和配置被静默拆到不同 attempt。此类 batch 通常使用 `large` budget；内嵌路径会读取 Code-Task TOML 的 `[execute].allow_large_edits`，没有显式批准时会保留产物并以清晰的失败状态结束。需要人工检查较大 proposal 时，standalone code-task 仍然是更合适的入口。
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
| `synthesize` | `synthesis_brief.json`、`synthesis.md`、`hypothesis.md` | 基于 read 阶段 Paper Brief 分析主题、gap、有限 ideas 和可测试假设。默认推导保持确定性；显式启用 LLM 时可以提出有界候选，但每条 motivation reference 都会对照输入证据校验。 |
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

`03-read` 负责筛选、重排和 Paper Brief。LLM 模式下，它会先并发粗筛紧凑 title/abstract 批次，再给保留集合分配阅读优先级、证据角色和 synthesis hint。`04-synthesize` 默认生成 `synthesis_brief.json`、`synthesis.md` 和 `hypothesis.md`；旧的 cards/evidence pack 诊断产物只在 `[run].debug_artifacts = true` 时保留。`05-design` 负责 experiment contract。

当 `[run].debug_artifacts = true` 时，search 还会保留 planning 文件、retrieval traces、retrieval-selection rows、coverage-review reports 和 section tables。V2.8 research 路径不再生成超前的 Tool/MCP handoff 产物；它们属于后置的 external Harness 阶段。

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
