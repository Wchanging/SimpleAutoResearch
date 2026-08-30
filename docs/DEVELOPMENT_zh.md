# 开发指南

[English version](DEVELOPMENT.md)

本文面向想扩展 SimpleAutoResearch 的贡献者。命令细节见 [CLI 参考](CLI_REFERENCE_zh.md)，TOML schema 见 [配置参考](CONFIG_REFERENCE_zh.md)，安装 walkthrough 见 [使用与配置](USAGE_zh.md)，工作流概念和产物见 [工作流与产物](WORKFLOWS_zh.md)。

## 项目形态

SimpleAutoResearch 现在采用 file-first + state-backed 的形态：

- stage 读取和写入具体文件；
- workflow state 通过 `state.json` 和阶段 contract 可见；
- 测试验证 contract/artifact，而不是依赖隐藏内存状态；
- 高风险代码修改发生在隔离 editable workspace 中，通常是受保护 copy，也可以是 detached git worktree，或实验性 sparse copy。

这样项目更容易学习、调试和重构。

### 兼容性审计

当前的源代码依赖审计没有发现可以安全删除的遗留实现。`core.pipeline` 和
`core.stage_results` 仍被 pipeline、experiment、report 以及测试层直接使用；
`Context.find_artifact()` 仍被需要兼容旧路径的 report 和 stage reader 使用。
`simple_ar._legacy` 下的模块以及 `pipeline_stages.handlers` 只是别名或很小的
导出聚合，不是第二套实现，因此继续保留以支持旧 import。历史 run reader 和
projection 也继续保留，直到 canonical artifact 有真实消费者完成迁移并有回归证据。

这是一项明确的保留决定，不表示所有旧路径永久不能清理。后续清理前应重新执行
依赖扫描；只有消费者已经迁移且旧格式回归仍然通过的路径，才可以删除。

### 清理规则

如果一个生成产物是声明过的 handoff、审计记录、可移植的用户输出，或旧格式的兼容输入，
就应当保留。可重建缓存只能通过显式的 `simple-ar clean` 清理；pipeline 不应静默删除它们。
删除代码前，应搜索 import、CLI 分发、文档、fixture 和历史 reader，并为替代路径补一个针对性
回归测试。优先删除已经确认的无效导入或有证据的重复分支；不要仅仅因为某个 adapter 较长，
就在没有真实消费者的情况下继续拆出更多层。

本轮清洗只删除了确认无用的导入，将 `core.artifacts` 对旧 workspace state 的导入改为按需加载，
并在解析持久化 handoff 前将 capability session root 转为绝对路径。没有删除领域包、阶段产物、兼容别名或
结果格式，因为它们仍有实际消费者或兼容测试。较大的模块暂时作为后续重构债务记录，不在没有真实
消费者时强行拆分。

## 职责边界表

决定改动放在哪里时先看这张表。稳定入口是新调用方可以依赖的最小公开边界；
最后一列同样重要，它用来防止领域策略泄漏进 core。

| 区域 | 稳定入口 | 负责内容 | 不负责内容 |
| --- | --- | --- | --- |
| Core runtime | `simple_ar.core` | 产物引用、attempt lineage、有界决策、profile 和转移校验 | 领域 schema、LLM 调用、代码编辑、重试或选择最佳结果 |
| Sources、documents、evidence | `research.sources`、`research.documents`、`research.evidence` | provider/parser port、文档 bundle、cards、chunks 和带 provenance 的 handoff | workflow 调度、把 provider 专属策略塞进 core，或把全文复制进每份 handoff |
| Synthesis | `research.synthesis`、`research.brief` | 基于证据的方向、研究契约和最小的文献到想法组合 | 自动宣称创新性、自动选择实验或隐式调用模型 |
| Experiment 与 analysis | `research.experiment`、`research.analysis`、现有 `experiment.execution` | 显式运行请求、规范化结果、指标比较和结果证据状态 | 代码生成、repair 策略、重试策略或决定下一研究阶段 |
| Report 与 audit | `report.capability`、`report.audit`、旧 `report.service` | 显式章节组装、可选图表渲染、引用/指标审计和旧报告兼容 | 隐藏缺失证据、凭空生成图表，或没有迁移契约就替换旧 writer/reviewer |
| Application 与 benchmark | `cli`、`pipeline_stages`、`code_task` 和 benchmark adapter | 面向用户的编排、旧 projection、code-task 策略和外部评测接入 | 成为 core runtime 的依赖，或为了单个 benchmark 改变通用 capability 语义 |

如果一个功能看起来跨越两行，应把协调放在 application 或显式 adapter 中，
通过声明的 `ArtifactRef` 传递输入；不要让下层直接导入上层的私有文件。只有当
现有边界无法表达真实消费者的输入、输出或失败状态时，才新增 class 或 artifact；
否则优先在现有边界增加函数、adapter 或 fixture。

## 新模块的能力边界

新的可替换模块可以使用 `src/simple_ar/core/` 中的轻量能力边界，暂时不必
改动现有 pipeline。`ArtifactRef` 标识已经声明的产物，`ArtifactStore` 提供
相对于 run 和 attempt 的文件读写，`CapabilityContext` 传递已登记的输入和
profile，`CapabilityResult` 返回状态、输出引用、诊断和 provenance。
`CapabilityRegistry` 只使用显式注册，不扫描仓库，也不动态导入任意 provider。
controller 管理的 capability 如果声明了 `available` 输出，但该文件并不存在于自己的
attempt store 中，controller 会将引用标为 `missing`、追加诊断，并把原本声称的
`completed` 降为 `partial`。这里只检查 capability 明确声明的输出，不扫描整个 attempt，
也不计算文件 hash；显式的 `missing`、`not_rendered` 和 `failed` 状态保持不变。

内置 research 适配器可以通过
`research.register_research_capabilities(registry, names=...)` 注册。这个 helper
只有被调用时才加载实现，支持显式选择子集，也支持替换指定实现；它不会注册旧的
八阶段 handler，也不会创建 workflow 调度器。`plan` 适配器默认复用已有的
问题、查询和来源预算 builder 并写出 `research_plan.v1`；调用方显式传入
`use_llm=True` 和共享 client 时，才会调用 research-planner 生成并规范化模型辅助的计划。
领域专属的 `design`、`code`、`run` 实现仍由应用调用方负责，直到它们的
契约足够稳定。
`research.planning.search_request_from_plan()` 是交给 `SearchRequest` 的对应内存
适配器；它不调用 provider，也不负责检索策略。

`SessionController` 为新 capability 提供有界 attempt 和 decision 持久化；它
不替代 `PipelineRunner`，不负责调度无限制的研究图，也不会隐式重试。现有的
`simple-ar run`、code-task 命令和旧 projection 仍是兼容入口，直到某条真实能力
线拥有明确的输入/输出契约和回归证据后，才逐步迁移。
`core` 目录中还保留历史的 `pipeline.py` 和 `stage_results.py` 兼容模块；它们
不是新 capability 应依赖的无领域底层边界。新模块只应依赖上面的 artifact/session
API；如果修改旧 collector，必须保持旧 pipeline 和 projection 的行为。
直接调用 `execute()` 时也会在创建 attempt 前解析请求的 handler；拼写错误或未注册的
capability 因此不会消耗预算，也不会留下伪造的失败 attempt。

`TransitionPolicy` 是 controller 外围的轻量确定性约束。
`TransitionRecipe` 是允许的下一能力白名单，`classify_failure()` 将短诊断信号
归一化为有限的 failure kind。证据是否充足、假设是否得到支持等语义信号可以请求
回看某个目标，但 recipe 仍会拒绝未列出的跳转。该策略不调用 LLM、不扫描整个 run，
也不会隐式重试；`DecisionRecord` 会记录 failure kind 和 next capability，
以及决策发生时观察到的预算计数；`list_attempts()` 则提供持久化 attempt lineage，
便于比较且不会把不同 attempt 的产物混在一起。
`status_snapshot()` 提供给状态界面和交接使用的紧凑 JSON 视图，包含 session/attempt
计数、预算、最近决策、可选的 profile 可见目标以及每个运行中 attempt 的 ID 和
capability，但不会复制产物内容，也不会替领域规则选择所谓“最佳结果”。调用 capability
前会先校验 proposed target；创建后续 attempt 时还会根据持久化的当前 capability 再检查一次
实际转移，因此省略或替换路径提案也不能绕过 allow-list；非法跳转不会
消耗 handler 调用，也不会留下空 attempt。新 attempt 会在 handler 启动前先持久化为
running，session 也会同步保存运行状态；进程中断时因此仍能留下可恢复的 lineage，
而不是一个没有标记的调用。
如果进程级中断已经得到人工确认，调用方可以重新加载 session，并对对应的 running
attempt 显式调用 `recover_interrupted()`。它只会写入一个明确的 failed capability result
并关闭该 attempt；不会自动重试、覆盖已有的 result envelope，也不会替领域逻辑选择下一步。
只要仍有 attempt 保持 `running`，controller 就会拒绝创建新的 attempt，直到调用方显式完成
上述恢复；这样可以保持单一活动 attempt 的 lineage，不会悄悄产生第二条分支。
如果调用方确实需要从较早节点比较另一条假设或修复路径，可以在
`execute()` 中明确传入 `parent_attempt_id`。父节点必须是已经收束为
`completed` 或 `failed` 的现有 attempt，且创建新 attempt 前仍会按该父节点的
capability 检查转移规则。默认行为仍使用持久化的当前 attempt，因此普通线性运行
不变；这只是显式 lineage 分支，不是图调度器或自动重试。
如果调用方需要为比较或恢复界面展示从根节点到某个节点的父链，可以使用
`attempt_lineage()`。它只读取 attempt manifest，不合并产物、不选择最佳结果，也不调度新
工作；父节点缺失或链路成环时会显式报错。

`SessionStep` 和 `run_session_plan()` 在这层边界之上提供最小的多能力交接入口。
调用方负责给出有序步骤、领域 handler 参数以及可选的证据充足性或是否需要补实验等
controller 信号；该入口会在第一次 attempt 之前预检已注册的 handler 以及允许的
transition，并在第一次非 accept 决策处停止。它刻意不是调度器、重试循环或任意 DAG
执行器；更高层 workflow 可以读取返回的 decision，再显式构造下一段有界序列。
对已有 session 继续执行时，预检还会把序列第一步与持久化的当前 attempt 做转移校验，
因此非法续跑会在任何新 handler 启动前被拒绝。所有传入的 input 都必须是 session 中已经
存在的 artifact；缺失 handoff 会在边界处失败，不会创建 attempt 或消耗 session 预算。

attempt manifest 中的输出路径相对于各自的 attempt 目录。若后一个 capability 需要读取前一个
能力的输出，应使用 `SessionController.attempt_output_refs()`；它会返回例如
`attempts/attempt-001/result.json` 这样的 session 根目录引用，不复制文件，也不替调用方
选择最佳 attempt。这样跨能力交接仍然是显式的，也不会把相对路径解析到错误的 store。
如果一个 capability 有多个领域输出，应使用
`attempt_output_ref(..., kind=..., schema=...)` 精确要求一个 artifact，而不要依赖输出顺序；
kind 不唯一时会明确失败。

`LifecycleProfile` 提供五个可选的内置能力范围：`research_brief`、`survey`、
`experiment`、`paper_audit` 和 `full_research`。session 使用这些名称之一时，
controller 会在执行前拒绝超出 allow-list 的 capability 或 transition。这只是
范围校验，不是自动工作流，也不强制规定起始能力；无法识别的旧 profile 仍保持
不加范围限制，以兼容历史调用和实验。
新建 session 如果使用已知 profile 但没有显式传入 `BudgetState`，默认 attempt 预算为该
profile 的能力数量加两次有界恢复机会。显式预算始终优先；旧 manifest 继续使用其中保存的
计数和限制，不会被自动改写。
attempt 可以继承 session 的 profile，也可以省略 profile；但不能把已有范围的 session
悄悄改成另一个 profile。
如果调用方需要展示允许的下一步，应使用 `SessionController.allowed_targets(source)`，
不要直接读取 recipe 或 profile 的内部结构。
如果启用了 profile，组合适配器应使用固定名称 `research_brief`、`experiment` 和
`report_audit`，其中 `analysis` 是结果分析 capability 的规范名称，`analyze` 继续作为
旧调用方的兼容别名；任意自定义名称只适合未设置范围的旧 session 或兼容场景。
内置 profile 的有序 `capabilities` 元组同时作为文档化的最短直线路径，供 fixture
和希望按常规顺序组合的调用方参考；它不是隐式执行计划。调用方仍需显式构造
`SessionStep`、提供 capability 输入，并在需要时选择白名单内的回退路径。

最小的端到端参考实现位于 `examples/capability_package_minimal/`。可以运行
`uv run simple-ar-checks core` 做离线验收。新的能力开发应先遵循这组边界，
具体的 request/result schema 放在对应领域模块中，不要继续膨胀 core。

每个由 controller 管理的 attempt 会在 manifest 中记录 capability，并保存一个
`capability_result.json`，其中只包含
`CapabilityResult` 的状态、输出引用、诊断、usage 和 provenance。它用于进程结束后恢复
失败原因和结果边界，不复制全文或原始日志；旧八阶段不会因此改变产物布局。

旧的巨型 CLI 和 stage handler 模块已经从 `src/simple_ar/_legacy/` 迁出。
该包现在只保留兼容旧 import path 的别名。新的行为应优先实现到
`core/`、`research/`、`experiment/`、`report/` 和 `code_task/`
这些领域模块中。

CLI 代码按职责拆分：

```text
src/simple_ar/cli/
  parser.py  argparse 命令与参数声明
  main.py    命令分发与用户可见输出
```

Pipeline stage 编排按工作流区域拆分：

```text
src/simple_ar/pipeline_stages/
  research.py    01-04 阶段：plan、search、read、synthesize
  experiment.py  05-07 阶段：design、code、run
  report.py      08 阶段：report packaging 与安全审查
  common.py      LLM access、artifact reads 等共享 stage helpers
  registry.py    PipelineRunner 使用的 HANDLERS registry
  handlers.py    仅兼容聚合；不要在这里继续添加新逻辑
```

顶层实现模块已经收束到领域包中。新代码应直接从 `core/*`、`app/*`、
`integrations/*`、`research/*`、`experiment/*`、`report/*` 或 `code_task/*`
导入，不再重新引入宽泛的 compatibility facade。

Research 代码按 evidence 生命周期分组：

```text
src/simple_ar/research/
  planning/    research questions 和可执行 query plans
  sources/     source plan contracts 与 connector-neutral query objects
  connectors/  OpenAlex、Semantic Scholar、arXiv、本地文件 adapters
  documents/   document records、full-text hints、parser/extractor helpers
  store/       chunks 与本地 index backends
  evidence/    retrieval screening、coverage、可选 debug evidence cards
  outputs/     search-stage artifact writers
```

新的检索、全文、证据和 card 能力应放入这些包中，不再回到旧的 `research/*.py` 平铺结构。

### 替换检索 Provider

`research.sources.base` 定义了轻量 provider 接口：`SearchQuery -> SearchResponse`。
`research.sources.registry.SearchProviderRegistry` 只负责显式登记和构造 connector；
查询规划、去重、缓存和阶段产物投影仍由 search stage 负责。默认 pipeline 保持兼容，
库调用方可以向 `execute_search` 传入 `provider_registry=`，也可以登记新的 source 名称，
而不必改动这些策略。新的 provider 应只负责访问数据源并返回规范化的 `Paper` 对象，
不要写 run 产物，也不要自行决定研究覆盖范围。

### 使用独立 Search 边界

`research.sources.capability` 为库调用方提供最小的多来源检索入口：

```python
from simple_ar.research.sources import (
    SearchRequest,
    default_search_provider_registry,
    search_sources,
)

result = search_sources(
    SearchRequest(
        queries=("research topic",),
        providers=("openalex", "arxiv"),
        max_results_per_query=5,
    ),
    registry=default_search_provider_registry(),
)
```

结果会保留每个 provider/query 组合的一条响应，并用
`completed`、`partial`、`empty` 或 `failed` 区分可用结果、部分来源失败和成功但为空的检索。
这个边界不写 stage 文件，不负责候选选择、去重或全文下载；这些仍由现有 Search stage
及其调用方决定。
如果调用方需要 session handoff，可以使用 `run_search_capability()`，将相同的规范化论文
行以及 provider/query 响应元数据写成一次 attempt-local 的 `search_result.json`。部分结果或
空结果会保持为非完成状态；该适配器不会额外加入重试或候选选择策略。
之后可以使用 `SearchResult.from_handoff_dict()` 在不访问网络的情况下恢复
`search_handoff.v1`；如果响应引用了不存在的论文元数据，它会保留为诊断信息。

### 复用 Document Ingest

`research.documents.ingest.build_document_bundle()` 是当前文档元数据、受许可的全文处理、
section 和 chunk 之间的最小组合边界。它复用现有 research record，不调用 LLM，也不直接写
阶段产物。Search 仍负责索引持久化和旧 JSON/JSONL projection；下游可以通过
`research.service.load_search_document_bundle(ctx)` 从 state alias 或旧 Search 路径恢复同一个
typed bundle，因此 reader 不需要知道具体 provider 或目录布局。

`research.documents.ports` 提供 manifest 选出本地资源之后使用的轻量
`DocumentResolver` 和 `DocumentParser` 边界。`build_local_document_bundle()` 是直接从本地
文献进入 Read 的入口；它复用现有 bundle、section、chunk 和 Read 逻辑，不运行 Search。
默认 resolver 和 parser 保持现有本地/缓存行为，调用方可以注入其他存储或文档服务的实现。
`research.documents.LocalDocumentParser` 是现有纯文本、HTML、可选 PDF 和
`unstructured` 路径的可复用默认实现。旧的 extraction helper 仍委托给它，因此替换 parser
不需要改动 bundle 构造或旧 Search projection。
`DocumentBundle.to_handoff_dict()` 与 `from_handoff_dict()` 定义可恢复的
`document_bundle.v1` 表示。如果需要由 session 管理文档摄取，可以使用
`run_document_ingest_capability()`：它只在 attempt 目录写入一份 `document_bundle.json`，
并通过 attempt manifest 暴露；后续 Read 可以在另一个进程中显式恢复这份 bundle，不需要重新
下载，也不会复制到其他 stage 产物。

### 复用 Read 边界

`research.evidence.reader.ReadRequest` 接收 `DocumentBundle` 以及可选的文档或论文标识；
`read_documents()` 返回 typed evidence cards 和诊断信息，不调用 LLM，也不写入文件。现有的
`write_read_card_artifacts()` 仍作为该边界的兼容 projection，因此阶段产物路径和旧调用方保持不变。
如果需要由 session 持有这次结果，可以使用 `run_read_capability()`，它把相同的 cards 和来源位置
写成一次 `read_result.json` handoff；不会复制 chunk 原文、下载文档或扩大选择范围。
生成和恢复 Read 结果时都会校验 card 声明的 `evidence_refs` 是否能在同一份
`DocumentBundle` 的 chunks 中解析；缺失引用会留下诊断并将结果降为 `partial`。也可以直接调用
`validate_read_evidence()` 做同一项无副作用检查。该校验只检查显式引用，不扫描文件或判断
引用内容的语义正确性。

### 复用 Synthesis 边界

`research.synthesis.SynthesisRequest` 接收 research pipeline 已经组装好的 expanded evidence pack；
`synthesize_evidence()` 返回有上限的 `IdeaCandidate`、`NoveltyCheck`、可选的
`ExperimentContract` 以及证据缺口摘要。默认路径是确定性的且不写入文件；调用方显式提供
`SynthesisRequest(use_llm=True, llm_client=...)` 时，会保留结构化推导并增加有证据依据的模型文本。
阶段级 policy 仍负责持久化和更大范围的写作流程。现有 synthesis artifact writer 已通过这个 facade 进行结构化证据推导，
原有阶段产物路径保持不变。持久化的 compact pack 只保存 card 引用，因此调用这个边界前应先恢复
对应的 card rows。
`run_synthesis_capability()` 是 session 适配器：它接收调用方提供的 expanded pack，将完整的有界
方向 handoff 写成一次 `synthesis_result.json`，不会读取私有阶段路径，也不决定是否应该运行实验。
`SynthesisResult.from_handoff_dict()` 可以在不访问网络或调用 LLM 的情况下恢复这个
`synthesis_result.v1` handoff，包括 idea、novelty check 和可选的 research-level 实验契约。

需要注意，`research.contracts` 中的 research-level `ExperimentContract` 描述有证据依据的假设和
拟议改动；`experiment.contracts` 中历史上同名的执行契约则描述命令、指标、资源、依赖和实现设置。
两者职责不同，新代码应从与契约职责相符的模块导入，不能把两个类型混用。
`ResearchExperimentContract.from_row()` 可以恢复 research-level handoff，
`ExperimentRequest` 同时接受这个 typed 对象和历史上使用的 mapping；canonical execution
result 会保留该契约，但不会把它和旧的 execution contract 合并。
纵向 fixture 会把恢复出的契约显式传给 `ExperimentRequest`，因此执行结果记录的是真实的
research-to-experiment 交接，而不是从私有阶段目录重新猜测假设。
如果调用方传入 typed research contract 但没有 execution result schema，契约声明的 metric
名称会形成供下游分析使用的最小 expected-metric 视图；显式 execution schema 始终优先，
历史 mapping 输入保持原有行为。
如果要把这条 handoff 交给 standalone Experiment，使用
`experiment_request_from_synthesis()`。它只恢复 `synthesis_result.v1`、转移其中已有的
research-level contract，并要求调用方显式提供 `RunRequest`；不会因为 handoff 存在就批准
`needs_review`、选择命令、执行、重试或决定下一阶段。

### 组合 Research Brief

`research.brief.build_research_brief()` 是一个小型纵向组合入口：它先调用
Read 边界，再把返回的 evidence cards 交给 Synthesis 边界。它接受 Search 产出的
`DocumentBundle`、缓存文档或本地文献 bundle，返回 `ready`、`partial`、
`needs_review` 或 `empty`，不会把 metadata-only 输入伪装成充分证据。它不搜索、不写文件；
默认使用确定性 synthesis，调用方显式提供 `use_llm=True` 和共享 client 时才增加有证据依据的
模型文本。它只是验证能力之间的输入输出可以组合，不替代现有八阶段 pipeline。

`research.brief.run_research_brief_capability()` 是这条组合的可选 session 适配器。
调用方自行选择名称并注册 handler，再传入 typed 的 `ResearchBriefRequest`；适配器只写出
一个 `research_brief.json` handoff，其中包含结构化 cards、方向候选、实验契约和来源位置，
不会复制 source chunk 原文。空 brief 会映射为 `blocked`，部分 brief 会映射为 `partial`，
因此证据不足会被 controller 看见。它不会隐式注册，也不会修改内置 lifecycle profile。

对于多 attempt 的组合，使用 `ReadResult.from_handoff_dict(..., bundle=...)` 恢复持久化的
Read 结果，再用 `evidence_pack_from_read()` 形成最小的 Synthesis 输入。显式传入 bundle
是有意的：source text 只保留一个 owner，而论文选择和执行顺序仍由调用方决定。
下游也遵循同一规则：调用方先恢复持久化的 analysis handoff，从实际观测结果中组装报告段落，
再交给 standalone report assembler；assembler 不会根据输入引用自行推断或编造分析数值。

### 复用 Experiment 边界

`research.experiment.ExperimentRequest` 在现有执行 `RunRequest` 外增加可选的结果 schema、
contract、artifact、comparison 和 guard 元数据。`run_experiment()` 使用已有的
`ExecutionBackend` protocol，默认采用 `LocalExecutionBackend`，返回现有的 `RunResult` 以及
统一规范化后的 canonical results。它不写文件，也不决定实验如何分析。需要一次完成“执行后
分析”时可使用 `run_and_analyze()`：它复制 `AnalysisContext`，加入实际观测到的指标和
canonical 执行记录，再委托现有的 result-analysis service。失败和超时仍会作为分析输入保留；
该组合入口不负责重试、repair 或阶段决策，只有明确传入输出目录时才持久化分析产物。因此
code-task 仍然是一个 backend，而不是又一套实验 API。
如果 request 带有 primary 或 required metrics，组合入口会把这些要求转换为分析所需的最小
视图，调用方不必在 context 中重复填写。

`research.experiment.run_experiment_capability()` 是执行边界的可选 session 适配器。
调用方自行注册名称后，它会把现有 canonical result 写成 `results.json`，并把捕获到的
stdout/stderr 以同一 attempt 下的 `execution/stdout.txt` 和 `execution/stderr.txt` 产物保存。
这些日志通过 attempt manifest 显式声明，可供诊断或其他下游 capability 读取。所有非 passed
的执行仍映射为 failed capability result；结果中保留 `passed`、`failed` 或 `timed_out` 的
真实状态，因此 session 层不会把超时误认为实验成功。分析仍是独立 capability，
`research.analysis.analyze_experiment_capability()` 可以显式读取这个结果引用，并写出带有
execution ref 的单个 `analysis.json` handoff。它只在分析状态为 `passed` 时返回
`completed`；缺少执行/指标证据返回 `partial`，明确失败或阻塞则保留对应状态，避免
session 层把“写出了分析文件”误认为“分析已经通过”。下游需要跨进程恢复时可使用
`AnalysisHandoff.from_handoff_dict()`；它只恢复 execution ref、原始执行状态和
`AnalysisResult`，不会重新运行实验或复制执行产物。
该适配器还复用现有的 result guard 和 diagnosis 实现，在同一 attempt 中写出
`guard_report.json`、`diagnosis.json` 和精简的 `diagnosis.md`；guard 出错时 capability
会失败，但 canonical result 仍保留底层真实执行状态。它不会隐式 retry、repair 或选择研究转移。

### 复用 Result Analysis 边界

`research.analysis.AnalysisRequest` 和 `analyze_results()` 提供独立的结果分析入口。
它们复用现有的 metric normalization、claim grounding 和 audit 实现；默认使用确定性分析，
只有显式传入 `output_dir` 才会写入产物。这个边界不自行创造指标、不运行代码，也不决定研究流程转移。

如果调用方已经有两个 canonical execution result，可以使用
`research.analysis.compare_experiment_results()` 生成精简的
`experiment_comparison.v1` mapping。它只比较两次结果共有的数值指标，使用显式提供的方向
或结果 schema 中的方向，保留执行状态变化；证据缺失或方向无法确定时返回
`inconclusive`。这个 mapping 可以放入 `ExperimentRequest.comparisons`，但该 helper 不会
重试、选择所谓最佳结果，也不会决定 session 的下一步转移。

`AnalysisResult.status` 是与之配套的证据状态摘要。它只根据显式的 canonical execution
记录、guard、required metrics 和显式 comparison verdict 确定，取值为 `passed`、`failed`、
`blocked`、`incomplete` 或 `metric_below_target`。没有 execution record 的独立分析仍保持
`incomplete`；这个状态不会替调用方选择 retry 或研究阶段转移。请求持久化时，同一份精简
状态也会写入 `analysis_status.json`。

如果上层需要把结果交给 session policy，可使用
`research.decisions.transition_request_from_synthesis()`、
`transition_request_from_analysis()` 或 `transition_request_from_report_audit()`。
这些函数只生成已有的 `TransitionRequest`，不执行 handler、不自动重试，也不替调用方选择
下一个 capability。它们同时接受 typed result 和持久化 mapping，便于跨进程恢复而不复制
另一套决策 schema；Synthesis 的 `needs_review` 只会被标为证据不足，不会自动决定回到
Search、Read 还是重新综合。

### 复用 Report Figure 边界

`report.ports.FigureRenderer` 是报告视觉输出的最小替换点。
`DeterministicFigureRenderer` 包装现有 SVG 实现，并继续作为 report service 的默认实现。
需要自行编排报告的调用方可以向 `execute_report(..., figure_renderer=...)` 传入其他 renderer；
pipeline 入口不传该参数，因此保持原有行为。
未来的图像或图表 backend 可以实现同一个 render 方法，消费已有的 `ReportDocumentPlan`、
`ReportFigureConfig` 和 `ReportFigureResult`，无需改动 writer、citation audit 或报告组装逻辑。

### 使用 Report 组装边界

`report.capability.ReportAssemblyRequest` 和 `run_report_capability()` 为已经得到 section
draft 的调用方提供下游报告边界。该适配器复用 `assemble_report_sections()`、现有标题编号策略
和 `FigureRenderer` port，在 attempt 目录写出一个 `report.md`；renderer 报告的每个图文件也会
登记为同一 attempt 的 `figure` 输出引用，figure manifest 仍只是便于阅读的索引。如果 renderer
报告了但实际没有生成文件，能力会返回 `partial` 和 `missing` 引用，不会伪装成完成。它不选择
大纲、不调用 LLM、不修改正文，也不做 citation audit；这些仍由独立的上层编排或审计能力负责。
这样可以明确连接 report 到 audit，同时不复制 writer，也不改变旧 report stage。

`report.audit.ReportAuditRequest` 和 `audit_report()` 提供对应的无副作用审查边界。
它们复用现有 citation、metric 和 claim 检查；报告写作与修订仍由上层编排负责。
`ReportAuditCapabilityRequest` 和 `run_report_audit_capability()` 是可选的 session 适配器：
调用方显式传入报告/正文 artifact 引用、typed report context 和 memory，适配器在当前 attempt
中写出一个 `report_audit.json`。warning 映射为 partial，failed 映射为 failed；它不会隐式
重试、改写报告，也不会自行寻找所谓的“最新”产物。

## 添加 Pipeline Stage

向默认 research pipeline 添加 stage 时，需要一起更新：

1. 在 `src/simple_ar/core/stages.py` 中添加 enum value。
2. 在 `src/simple_ar/app/state.py` 和 `src/simple_ar/core/contracts.py`
   中添加或扩展 typed state/contract models。
3. 在对应领域 service 中实现阶段行为，例如
   `src/simple_ar/research/service.py` 或 `src/simple_ar/experiment/service.py`。
4. 在 `src/simple_ar/pipeline_stages/` 的对应模块中添加 stage controller。
   这里应负责阶段编排和产物衔接，不要重新变成新的大杂烩实现层。
5. 在 `HANDLERS` 中注册 handler。
6. 添加聚焦测试，检查 state update 和 declared outputs。

新的 stage 应优先使用显式 `ctx.state.<stage>` 指针和紧凑 stage contract，
而不是反向扫描 run 目录。`ctx.find_artifact(...)` 仅作为 legacy fallback 保留。

## 添加 Experiment Template

固定脚本模板主要位于 `src/simple_ar/experiment/templates.py`。内嵌 8 阶段
code-task templates 位于 `src/simple_ar/experiment/code_task_bridge/`，
因为它们会在写 run harness 前准备已有 workspace。旧的
`src/simple_ar/experiment/code_task_experiment.py` 只作为兼容 facade 保留；
新代码应直接从 `code_task_bridge` 导入。

`src/simple_ar/experiment/runner.py` 用于固定模板生成脚本的 subprocess 运行。
`src/simple_ar/code_task/` 则负责 LLM-guided 项目编辑、workspace 隔离、patch、
validation 和 benchmark comparison。

`experiment.execution.backend.RunResult` 是统一的 subprocess 结果模型。
`experiment.runner.ExperimentRunResult` 仅作为兼容别名保留；新的执行和分析代码应依赖
`RunResult`，避免维护两套相同结果结构。

新生成的 LLM usage 记录还会保存一次成功 `ask()` 请求实际使用的 provider 调用次数，
汇总中会给出由此计算的重试次数；没有该字段的旧记录仍可读取，并按一次调用处理。

顶层 run config 解析位于 `src/simple_ar/app/run_config.py`。它应该保持为薄的 TOML-to-runtime-options 层；code-task 专属 config 语义应继续放在 `src/simple_ar/code_task/runtime/config.py`，避免 standalone 和 embedded code-task 行为漂移。

新的 template 应满足：

- 添加到 `SUPPORTED_TEMPLATES`；
- 生成完整 standalone `experiment.py`；
- 只使用 `pyproject.toml` 中声明的依赖；
- 打印机器可解析指标行，例如 `metric_name: 0.123`，由 `src/simple_ar/experiment/metrics.py` 解析；
- 避免网络访问和不受控下载；
- 在 `tests/test_experiment_runner.py` 中有测试。

当前 template system 故意不做自由形式 code generation。这个边界能保证教学 pipeline 可复现，同时把更强 coding workflow 放在 `code-task` 下逐步发展。

对内嵌 code-task template，应保持 automatic approval boundary 显式：它们应准备 workspace，使用 controlled old/new edits，写紧凑阶段产物，例如 `code_task_experiment.json`，并通过 `07-run` 运行 benchmark，而不是在 reporting 时悄悄修改源码。通用 `code_task_project` template 应保持为 standalone code-task modules 的薄桥接层，而不是另一套 coding 实现。

## 扩展 Code Task

Code-task workflow 现在按生命周期分包，而不是把所有文件平铺在同一层：

```text
src/simple_ar/code_task/
  runtime/        config、path 和 manifest helpers
  workspace/      copy、git worktree、sparse-copy 准备
  analysis/       codebase index、repo map、locate、context packs
  editing/        work plan、patch plan、edit budgets、patch proposal/application
  execution/      environment probe、validation、benchmark、comparison、repair
  orchestration/  init 和 execute 这类组合下层模块的流程
```

新增 code-task 能力时，应放入拥有该行为的生命周期包中。除非是 public facade 或真正跨切面的边界，不要继续在 `code_task/` 根目录新增平铺文件。

新增 code-task 功能时：

- 原始 source directory 必须保持只读；
- artifacts 写入 `code_task/meta`、`code_task/run` 或 `code_task/repairs`；
- 底层行为稳定前，CLI 步骤应保持显式；
- 适合时同时测试 library function 和 CLI path；
- 优先使用小而可组合的函数，不要过早塞进单个 agent loop。

Metric comparison 应保持保守。未知数值指标可以记录 delta，但除非 manifest 显式配置方向或命中简单本地 heuristic，否则不应用于 improved/regressed verdict。只有当指标命名约定足够常见、不令人意外时，才添加新的默认 heuristic。

## 扩展 Report 与 Audit

Report system 是 V2.4 用来承接 research-only survey、experiment report 和
embedded code-task result 的出口。它应该保持 template-driven 和 evidence-aware，
不要退回到单个大 prompt 或单个大 service 文件。

```text
src/simple_ar/report/
  schema.py        context、memory、tools、draft、review 的 Pydantic models
  context.py       收集 papers、synthesis、metrics 和 code-task comparison
  templates.py     加载 Markdown 模板和 reviewer criteria
  memory.py        紧凑 section plan、evidence handles、claims、limitations
  tools.py         report tool schema definitions
  tool_gateway.py  有边界的只读 tool execution
  retrieval.py     source-handle backtracking
  agent.py         Writer/Reviewer orchestration
  citations.py     citation key 映射、显示标签和 citation cleanup
  audit.py         citation、metric、claim 和 reviewer audit 汇总
  assembler.py     section drafts 组装为最终 Markdown
  quality.py       deterministic report quality checks
  service.py       stage entrypoint 和 artifact packaging
```

新增 report 行为时：

- schema 放在 `schema.py`，不要在 `service.py` 里继续堆自由 dict；
- source lookup / backtracking 放到 `context.py`、`retrieval.py` 或
  `tool_gateway.py`；
- Writer/Reviewer loop 行为放到 `agent.py`；
- citation 映射、显示转换和 cleanup 放到 `citations.py`；
- 机械一致性检查放到 `audit.py` 或 `quality.py`；
- 模板和审查标准放在 `templates/report/`，不要硬编码成长 prompt；
- `service.py` 只保留 stage-level coordinator 和 artifact writer 的职责。

`report/service.py`、`pipeline_stages/research.py` 和 `cli/main.py` 仍然偏大，
需要视为黄灯。不要继续往这些文件里添加无关行为；新的工作应该迁移到对应领域模块，
或者顺手减少这些文件的职责。这是维护规则，不是要求把每个小 helper 都拆成独立文件。

## 扩展 Tools 和外部 Agent Backend

V2.6 新增 common tool 与 handoff 层，但不替换已有领域实现：

```text
src/simple_ar/tools/
  specs.py        CommonToolSpec、ToolCall、ToolResult、permission/risk enums
  registry.py     把 report 和 experiment tools 组合成统一 registry
  gateway.py      带权限检查的本地 dispatch 和紧凑 trace 写入
  permissions.py  read/write/execution/network policy checks
  openai_schema.py / mcp_schema.py
                  只导出 schema；默认不启动 server
  mcp_server.py   显式启动的 stdio MCP server，只暴露 run-local 只读 tools

src/simple_ar/agent_backends/
  base.py         AgentBackend protocol 和 run result models
  policy.py       写入 handoff 的外部 agent permission policy
  handoff.py      workspace-scoped handoff package 和不可信输出收集
  factory.py      fake/local_llm/Codex/Claude/OpenCode 的 provider selection
  fake.py         deterministic backend，用于集成测试和 dry-run
  local_llm.py    LLM-backed bounded reviewer/planner backend
  external_cli.py subprocess wrapper，包含 cwd、timeout、env allowlist 和日志
  profiles/       Codex / Claude Code / OpenCode profile Markdown
```

这个 common layer 刻意保持很薄。`experiment/tools/` 和
`report/tool_gateway.py` 仍然拥有具体业务逻辑；`tools/` 只负责给未来
OpenAI tool calling、MCP adapter 和外部 agent backend 提供统一、可审计的出口。

新增 tool/backend 时：

- 只注册真实可用的工具；不要为了展示 MCP/OpenAI schema 添加 stub tool；
- write、shell、network、secret access 默认关闭，除非配置和审批路径明确开启；
- 外部 agent 上下文写入 `agent_handoff/<name>/`，默认不要写用户全局工具目录；
- 外部 agent 输出一律视为不可信。先收集到 `agent_outputs/<name>/`，再交给已有
  patch、result guard、report audit 或 code-task validation 路径；
- 外部 CLI provider 必须保持 opt-in。`fake` 和 `local_llm` 可用于测试与本地 review；
  `codex`、`claude_code`、`opencode` 和 `external_cli` 必须等配置显式允许后才能启动；
- trace 默认保持紧凑。raw prompt、raw output 或大 payload 只能在 debug 设置下保留。

### Code-Task Environment Policy

当前 code-task runner 通过 `copy`、`git_worktree` 或实验性 `sparse_copy` 提供 workspace isolation，并支持 command timeout、可选 benchmark output streaming、stdout/stderr 捕获、受限 environment map 和显式 execution interpreter policy。它支持 `current` 和 `external`，但还不会创建或安装到单独 Python environment。除非未来功能明确改变这一点，否则不要默认把用户项目依赖安装到 SimpleAutoResearch 自己的 `.venv`。

环境支持应分层演进：

- `current`：使用当前 SimpleAutoResearch Python。简单，适合 demo，但不是依赖隔离。已支持。
- `external`：使用用户提供的 Python 或 Conda interpreter。是真实项目已有环境的第一逃生口。已支持。
- `project-venv`：在 `code_task/.venv/` 下创建 per-run 环境。隔离好，但可能浪费磁盘。计划中。
- `shared-env-cache`：在 `.simple_ar_cache/envs/<env-hash>/` 之类的缓存目录下创建或复用环境，按 OS、Python version 和 dependency files 哈希。长期推荐方向。计划中。
- `docker`：在容器内运行以获得更强隔离。应与 Python runner 分离，因为 Windows、GPU 和 image build 行为需要谨慎处理。计划中。

未来任何环境创建或依赖安装都必须显式、可审计，并记录到 artifacts。安全实现应记录 selected mode、interpreter path、dependency files、install commands、exit codes 和 warnings 到 `code_task/meta/environment_report.json` 或专门 environment artifact。

## 文档规则

文档分工：

- `README.md` / `README_zh.md`：项目入口、安装、quickstart、workflow 概览和链接。
- `docs/USAGE.md` / `docs/USAGE_zh.md`：安装、环境变量和 workflow walkthrough。
- `docs/CLI_REFERENCE.md` / `docs/CLI_REFERENCE_zh.md`：命令组和参数表。
- `docs/CONFIG_REFERENCE.md` / `docs/CONFIG_REFERENCE_zh.md`：TOML schema 和配置示例。
- `docs/WORKFLOWS.md` / `docs/WORKFLOWS_zh.md`：每个 workflow/stage 做什么，以及产物结构。
- `docs/DEVELOPMENT.md` / `docs/DEVELOPMENT_zh.md`：贡献者指南。
- `CHANGELOG.md`：按时间记录开发进展。
- `MDfiles/`：私有或学习型规划笔记，通常不提交 GitHub。

英文文档应链接到对应中文版本；中文文档内部链接应优先指向中文版本。

## 测试

开发时优先使用分层 checks：

```bash
uv run simple-ar-checks --list
uv run simple-ar-checks quick
uv run simple-ar-checks code-task
uv run simple-ar-checks pipeline
uv run simple-ar-checks research
uv run simple-ar-checks code-task-examples
uv run simple-ar-checks core
```

也可以不用 console script，直接运行脚本入口：

```bash
uv run python scripts/run_checks.py code-task
```

推荐验证层级：

| 修改范围 | 建议检查 |
| --- | --- |
| 仅文档 | `git diff --check` 加人工检查链接。 |
| 小型 parser、prompt、config、metric 或 CLI 改动 | `uv run simple-ar-checks quick`。 |
| Code-task 内部、workspace、repo-map、patching、validation、runner、repair | `uv run simple-ar-checks code-task`。 |
| 内置 code-task 示例或 benchmark 示例 | `uv run simple-ar-checks code-task-examples`。 |
| Pipeline、stages、experiment templates、run config | `uv run simple-ar-checks pipeline`。 |
| Literature、retrieval、evidence ledger、report generation、LLM adapter | `uv run simple-ar-checks research`。 |
| Core capability boundary、registry、attempt store 和能力包示例 | `uv run simple-ar-checks core`。 |
| 提交/推送前或大范围重构 | `uv run simple-ar-checks all`。 |

必要时仍可直接运行完整测试：

```bash
uv run python -m unittest discover -s tests
```

运行真实 code-task 示例测试：

```bash
uv run python -m unittest tests.test_code_task_examples
```

运行 experiment runner tests：

```bash
uv run python -m unittest tests.test_experiment_runner
```

运行配置解析和公开 example 配置加载测试：

```bash
uv run python -m unittest tests.test_run_config
```

## Git 卫生

- 保持 feature commit 聚焦，避免混入无关重构。
- 不提交 `.env`、run outputs、caches 或私有学习笔记。
- README 保持简洁；详细行为放到 docs。
- 用户可见命令、artifact 或 workflow 行为变化时，更新 `CHANGELOG.md`。
