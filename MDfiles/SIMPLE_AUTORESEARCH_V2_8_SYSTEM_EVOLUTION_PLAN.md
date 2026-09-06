# SimpleAutoResearch V2.8 主计划与架构收口方案

> 文档级别：**V2.8 唯一有效的施工计划**。
>
> 更新时间：2026-09-06。代码验证基线：`4a19b5e`（`feat/v2.8-system-evolution`）。
>
> 本文描述 V2.8 要做什么、当前真实差距、最终结构、能力迁移规则和退出条件。不表示所有目标已经实现。

## 0. 文档职责：只保留一个 V2.8 主计划

为了避免多个 V2.8 文档各自维护路线，今后按以下规则阅读：

| 文档 | 唯一职责 | 是否包含当前施工计划 |
|---|---|---:|
| `SIMPLE_AUTORESEARCH_V2_8_SYSTEM_EVOLUTION_PLAN.md` | V2.8 目标、架构、阶段、验收和删留决策 | **是，唯一来源** |
| `SIMPLE_AUTORESEARCH_V2_8_SESSION_HANDOFF.md` | 当前会话快照、已完成事项、下一步入口 | 否，只引用本文 |
| `SIMPLE_AUTORESEARCH_V2_8_PHASE0_BASELINE.md` | 冻结的代码/产物/消费者基线和审计证据 | 否，不改路线 |
| `SIMPLE_AUTORESEARCH_STAGE_DECOUPLING_AUDIT.md` | 阶段职责和历史清洗记录 | 否，只记录证据 |
| `SIMPLE_AUTORESEARCH_V2_8_UNDERGRADUATE_WORK_PLAN.md` | 协作者任务手册 | 否，必须服从本文 |
| `SIMPLE_AUTORESEARCH_LONG_TERM_VISION_AND_ARCHITECTURE.md` | 全项目长期愿景、V2.9+ 方向 | 否，V2.8 只保留摘要 |

如果其他文档与本文的当前 V2.8 计划冲突，以本文为准；旧文档中的 Phase 0–8、候选调度和
未来平台设计只作为历史记录，不能自动转化为当前任务。

## 1. 一句话决策

现在适合做一次**有安全网的架构切换**：

```text
先固化已有能力和行为
  -> 让 research-session 成为唯一正式主线
  -> 把旧路径中验证过的细节择优合并到 canonical 模块
  -> 让 simple-ar run 变成薄兼容入口
  -> 通过 example、集成测试和真实 smoke 后删除重复实现
```

这不是继续长期保留新旧两套业务，也不是不加验证地重写所有代码。目标架构现在就确定，
实现可以拆成几个可回退的提交，但每个提交都服务于同一次收口，不能再向旧路径增加新功能。

## 2. V2.8 理想目标与完成定义

V2.8 的目标是一个可以正式试用、受边界约束、结果可审计的 research-to-report 闭环：

```text
研究问题 + 网络/本地资料 + 准备好的 baseline/data/code
  -> plan
  -> search
  -> document_ingest
  -> read
  -> synthesize
  -> research_design
  -> code_task（有代码任务时）
  -> experiment
  -> analysis
  -> report
  -> report_audit
```

LLM 可以参与计划、阅读辅助、综合、设计、分析和写作；但每一阶段都必须经过明确的输入、
输出、权限、预算和状态检查。实验命令、baseline、数据集、资源限制和指标规则在 V2.8 中
仍由用户或配置明确提供。

### 2.1 V2.8 完成条件

V2.8 的验收分成两个层级，避免把“代码链路已经能跑”误写成“已经达到正常用户的研究
规模”。两个层级都必须有持久化 artifact；第二层通过后，才可以把 V2.8 的基础闭环标记为
正式可用。这里的“正式可用”限定为准备好 baseline/data/code、资源和指标的有界任务，不等于
任意主题上的完整自主 AutoResearch。

**第一层：工程闭环（当前已通过）**

至少要有一个可复现的受控 session，能够：

- 使用真实网络来源或本地资料完成检索和文档摄取；
- 让配置的 LLM 参与计划、阅读、综合、设计或写作，而不是只执行 deterministic fixture；
- 将研究方向转换为明确的实验/代码任务契约；
- 对准备好的 baseline 做受限代码修改或执行明确实验；
- 留下代码变更、执行命令、stdout/stderr、指标和分析产物；
- 生成包含来源、实验结果、限制和审计信息的报告；
- 中途失败时保留原始错误，并能在预算内显式继续；
- 新旧入口和历史 artifact 的必要兼容回归通过。

当前已有本地聚焦回归、AutoDL 全量测试、真实网络/LLM session 和真实 CodeTask session
作为第一层证据；第二层另有独立的正常用户规模验收记录。

**第二层：正常用户规模的正式试用闭环（已通过，2026-09-06）**

至少完成一次受控、可复现的中等规模验收，目标边界如下：

- 网络检索得到约 30–50 条 raw papers，并在 artifact 中保留 provider、查询、去重和筛选
  记录；
- 对约 10–20 篇候选完成 bounded Read/screening/notes，实际数量和回填原因可审计；
- 使用真实 `gpt-5.4-mini + chat` 完成 plan、Read、synthesis、design 和 report 所需的
  结构化 LLM 步骤，保留有限重试、超时和失败状态；
- 使用用户准备好的 baseline、dataset、code 和 benchmark，完成一次中等复杂度但有界的
  实验；在 AutoDL/3090 上必须限制 batch、epoch、超时和磁盘输出，不启动并行候选或无限
  repair；
- 完成 baseline/modified（或多条件实验）的真实执行、指标比较、结果分析和 provenance；
- 生成具有 Abstract、Introduction/Motivation、Related Work、Method、Experimental
  Setup、Results、Discussion、Limitations、References 等完整章节的 Markdown 报告；
- 报告引用必须能回到选定来源，正文数字和实验指标必须能回到 artifact，`report_audit`
  的 citation、metric、claim 检查通过或明确记录可解释的 warning；
- 保留完整 session 目录，使另一位协作者可以从 manifest、handoff 和报告复核每一步。

本次验收使用 `full_pipeline_tiny_mlp` 和 OpenAlex：实际保留 60 条 raw、10 篇 selected
Read/document，真实 `gpt-5.4-mini + chat` 完成计划、阅读、综合、设计和 Writer，CodeTask
在隔离 workspace 中完成 baseline/modified benchmark、指标比较和 analysis，最终 session
为 `completed`。`experiment` Markdown 报告包含摘要、问题/动机、相关工作、方法、实验设置、
结果、讨论、局限、验证指标和参考文献；citation、metric、claim audit 均为 `passed`。
60 条 raw 是 provider 在 `--max-results 10` 下的实际返回，略高于 30–50 的目标区间，已在
验收记录中保留而没有静默裁剪。该 run 使用 writer-only 基础报告闸门；同一代码也有 reviewer
通过和 reviewer warning 的对照记录，后者作为报告质量改进项而不是闭环事实失败。

这一层验证的是“正常用户可用的基础闭环”，不是 publication-ready 论文，也不是任意主题
都能自动研究。LaTeX/Overleaf 模板、复杂 repair、多候选和外部 Agent 仍属于后续版本。

### 2.2 V2.8 明确不做

- 无限自主研究循环；
- 自动发明完整实验矩阵、资源计划和晋级策略；
- 多候选并行调度、实验树和复杂 scheduler；
- 现在就接入 Claude Code、Codex、OpenCode 等原生 Harness；
- 现在就实现所有会议/期刊的 LaTeX 模板自适应；
- GUI、dashboard、远程队列和生产级 GPU 编排；
- 为了“结构统一”机械搬迁所有目录或重写仍有真实消费者的大型子系统。

## 3. 当前真实状态

### 3.1 两条路径的含义

```text
正式新路径（未来唯一主线）
research-session
  -> plan -> search -> document_ingest -> read -> synthesize
  -> research_design -> experiment -> analysis
  -> report -> report_audit（模型驱动 CLI 默认执行；库层仍是显式 continuation）

兼容旧路径
simple-ar run
  -> 旧八阶段 PipelineRunner / pipeline_stages
```

新路径已经有 session、attempt、artifact、typed handoff、有限 transition 和多个 capability。
当前状态已经完成了一次边界收口，但还没有完成旧实现的最终清除：

- 提供 `CodeTaskExperimentSpec` 时，`code_task` 已作为明确的实现模式进入 canonical
  `experiment` attempt；它不是没有代码任务时的默认步骤；
- CLI 的模型驱动 `research-session` 现在默认继续执行 `report -> report_audit`，`--no-report`
  只作为调试/快速模式；无模型的 deterministic session 仍停在分析结果；
- 旧 Search/Read/Synthesis 实现已归档到私有 `simple_ar/_legacy/research_stages.py`，
  `pipeline_stages/research.py` 现在只是供旧 `simple-ar run/resume` 使用的兼容 alias；
  这份 legacy 实现仍有重复业务，尚未满足最终删除门槛；
- canonical `search` 已吸收旧检索中确定性且可复用的去重、相关性/预算筛选和 facet coverage
  行为；原始 provider 响应、`selected_paper_ids`、selection rows 和 coverage 均保存在同一个
  `search_handoff.v1` 中，canonical ingest 使用筛选后的论文；
- 已有受控测试覆盖同一 session 的 `code_task -> experiment -> analysis -> report/audit`；
  新增的集成 fixture 还实际调用了 production CodeTask bridge、隔离工作区、baseline、
  受限 patch、validation 和指标比较，而不是替换 capability handler；
- 真实网络 + 真实 LLM + 用户准备项目的低预算正式 smoke 已完成；上面 2.1 所定义的正常
  用户规模验收也已通过一次：60 条 raw、10 篇 selected/document、中等但受控实验和完整
  Markdown report/audit 均有持久化证据；
- 不能把现有接口数量等同于任意主题上的完整 AutoResearch 能力。

### 3.2 已确认有价值的基础

最近清理已保留并验证以下部分：

- `ArtifactStore`、`ArtifactRef`、`CapabilityResult`；
- session/attempt/parent/decision/budget 和有限 transition；
- search provider、文档摄取、全文解析、证据卡片和 evidence pack；
- code-task 的仓库分析、受限编辑、快照、review、repair 和验证；
- experiment execution、结果规范化、指标分析和失败诊断；
- report 的引用、数字审计、图表和质量检查；
- 旧 CLI、历史 handoff、benchmark 和必要的兼容 projection。

最近已删除的内容包括无消费者的 session plan、多候选调度、独立 iteration policy、
没有执行者的 Tool/MCP 设计草案等。这些删除不应被重新引入。

## 4. 最终架构：一条依赖方向，多个可替换能力

目标不是增加更多层，而是固定以下依赖方向：

```text
cli / benchmark adapter
          ↓
app：固定的用户用例编排
          ↓
core：session、artifact、capability、transition、budget
          ↓
research / code_task / experiment / result_analysis / report
          ↓
integrations：LLM、网络、文件、subprocess、外部 Agent
```

概念目录如下。除非实际迁移需要，不要求为了名称好看进行大规模机械重命名：

```text
simple_ar/
├─ core/             # 运行时和生命周期，不放具体 provider 业务
├─ app/              # research-session、brief、experiment、report 等用例
├─ research/         # plan、sources、documents、evidence、synthesis、design
├─ code_task/        # 代码分析、编辑、review、repair、验证
├─ experiment/       # 实验契约、执行、结果规范化
├─ result_analysis/  # 指标、比较、分析结果
├─ report/           # 报告、引用、图表、审计
├─ integrations/     # LLM 和外部系统适配
├─ cli/              # 参数解析、调用和展示
└─ compatibility/    # 旧 pipeline、旧 schema、旧 projection 的薄适配
```

当前的 `pipeline_stages/` 可以暂时承担 `compatibility/` 的物理位置，但不得继续成为新业务
逻辑的归属地。`literature`、`retrieval`、多个 tools 目录等是否移动或删除，必须按下面的
能力审计执行，不能仅按目录名称判断。

## 5. 能力迁移规则：保留行为，不保留重复实现

迁移不是“新代码全盘替换旧代码”，而是建立能力保留矩阵：

```text
能力
  -> 旧实现的真实行为
  -> 新实现的输入/输出契约
  -> 旧实现中更成熟的细节
  -> 新实现中更清晰的边界
  -> 合并后的 canonical owner
  -> 正常/失败 fixture 和回归测试
  -> 旧代码删除条件
```

### 5.1 重点择优合并

| 能力 | 旧路径中优先检查 | 新路径中保留/合并的位置 |
|---|---|---|
| Search | provider fallback、缓存、去重、coverage、下载/解析降级 | `research/sources`、`documents`、`evidence` |
| Read | LLM screening、摘要、重点定位、异常处理 | `research/evidence` 的 read policy 和显式 result |
| Synthesis | 成熟 prompt、上下文选择、gap/idea 规则 | `research/synthesis.py`，保留 evidence refs |
| Design | baseline、dataset、metrics、expected outcome 传递 | `research/design.py` 与实验契约 |
| CodeTask | repo map、作用域、快照、测试、repair budget | `code_task`，通过明确 bridge 接入 |
| Experiment | timeout、stdout/stderr、baseline comparison、失败诊断 | `experiment` 与 `result_analysis` |
| Report | citation、numeric audit、figure、quality checks | `report`，先稳定 Markdown profile |
| Runtime | lineage、恢复、预算、状态和兼容读取 | `core`，旧格式集中在 compatibility |

需要保留的是用户可见行为、研究可追溯性、失败透明度和安全限制；不需要保留的是历史类名、
重复 schema、重复 writer、无消费者的配置和偶然形成的隐式路径。

### 5.2 删除门槛

某个旧文件或旧模块满足以下条件后，可以直接删除：

1. 生产代码、CLI、benchmark、旧 reader 和测试均完成引用审计；
2. 其中有价值的行为已经进入唯一 canonical owner；
3. 新实现有正常、失败和降级 fixture；
4. 关键 artifact/schema 有 snapshot 或语义等价回归；
5. 至少有一次真实或受控 smoke；
6. 删除后全量测试和用户 example 通过。

满足条件就删，不因“可能以后有用”继续保留整套重复实现。

## 6. 架构切换计划

### Phase 0：冻结基线和行为安全网（已完成）

目标：把当前代码当作可回退基线，同时建立黑盒能力基线。

工作内容：

- 以 `09df140` 作为历史清理基线，并以 `21eac85` 作为当前可回退基线；
- 不再向旧 `simple-ar run` 增加新功能；
- 为一个小型真实项目准备 baseline、dataset、code task 和实验命令；
- 固化 example、artifact 树、状态变化和失败/恢复行为；
- 建立“旧行为—新契约—合并位置—删除条件”矩阵。

退出条件：新会话可以从一个 example 看懂主链，并能判断每个阶段实际产生了什么。

### Phase 1：打通正式黄金路径（工程范围已完成）

目标：让 `research-session` 成为可正式试用的单一入口。

工作内容：

- 固定 `plan -> search -> ingest -> read -> synthesize -> design`；
- 在提供代码任务时接入 `code_task -> experiment -> analysis`；
- 将 `report -> report_audit` 纳入正式完整 profile；
- 保留 `--no-report` 或等价调试方式，但不让报告成为隐藏的可选分支；
- 记录每个 attempt 的输入、输出、错误、预算和 lineage；
- 增加一次真实网络 + LLM + 代码/实验 + 报告的受控验收。

退出条件：一条命令或一份明确配置能得到完整 session 目录，报告中的引用、代码版本和指标
都能回溯到实际 artifact。当前已由 AutoDL 的真实 CodeTask session 和 report/audit 产物
证明；正常用户规模的正式试用仍属于 Phase 5/6.9 的剩余闸门。

### Phase 2：canonical 能力择优合并（核心行为已完成，兼容尾项保留）

目标：把旧路径中仍有价值的策略合并到新模块，而不是复制整个旧阶段。

顺序：

1. Search：连接器、fallback、缓存、去重和 coverage；
2. Read/Evidence：筛选、笔记、证据定位和失败降级；
3. Synthesis：prompt、上下文裁剪、gap/idea/hypothesis 规则；
4. Design/Experiment：契约、代码任务、执行和分析 handoff；
5. Report：引用、图表和审计的稳定输入输出。

每一项都必须先有消费方和 fixture，再修改实现。迁移期间禁止在旧实现和新实现两边各加一套
新策略。

退出条件：新主线使用 canonical 实现；`simple-ar run` 只负责旧输入/输出转换和兼容调用。

### Phase 3：清除旧重复实现（核心主线已收口，最终尾项待审计）

目标：完成一次有终点的清理，而不是无限期“以后再迁移”。

- 将 `pipeline_stages/research.py` 收缩为适配器后删除重复业务逻辑；
- 统一 `literature` 与 `research/sources` 的真实消费者；
- 统一 `retrieval` 与 `research/store/evidence` 的真实消费者；
- 清理没有独特消费者的 tools、projection、schema 和 wrapper；
- 保留 `_legacy`、历史 reader、benchmark 需要的最小兼容层；
- 更新文档、example、测试和 CLI 帮助。

退出条件：不存在两套同时演进的 Search/Read/Synthesis/Report 核心实现，新增功能只能进入
canonical 路径。

### 6.3 本轮 Phase 3 收口记录（2026-09-05）

已完成的切换动作：

- CLI 顶层不再加载旧 `pipeline_stages` registry；只有 `simple-ar run/resume` 访问时才懒加载；
- 新的 `experiment`、`report` 和 code-task bridge 不再从 `pipeline_stages.common` 获取通用
  artifact/LLM helper；这些行为已收归 `core.runtime`，旧模块只保留兼容别名和 legacy 检索辅助；
- 删除无生产消费者的 `experiment/code_task_experiment.py` 兼容 facade，保留实际使用的
  `experiment/code_task_bridge.py`；
- 增加 `examples/research_session_smoke.py`，在本地 fixture 上实际落地完整的
  research-session 到 report/audit 产物；
- 增加同一 session 的 production CodeTask bridge 到 report/audit 受控验收测试，验证
  canonical synthesis handoff、隔离工作区、baseline、受限 patch、validation、指标比较和
  引用/数字审计；同时修复 bridge 只接受历史外层 synthesis 包装、不能读取 canonical
  `synthesis_result.v1` 的边界问题；
- 修复内嵌 CodeTask bridge 只执行首个 work item 的边界：严格串行的依赖链现在合并为一个
  有界、可审查的 batch，仍不引入多候选或开放式迭代；
- 报告 numeric audit 已改为识别 selected source metadata、可读指标名和科学计数法，避免把
  `twelve papers`、`training time`、`5.4e-05 seconds` 误判为未绑定数字；
- `research-session` 的报告默认行为已经写入 CLI 帮助、example 和测试；
- `pipeline_stages/research.py` 已收缩为 alias，冻结实现归档到 `_legacy`；旧 Search/Pipeline/
  Document Ingest 回归 31 项通过；
- canonical `read` 已接入共享的 bounded LLM screening/rerank 和 paper-note policy；模型阅读
  会接收有界 source chunks，并把 screening decisions、paper notes、notes markdown 和来源定位
  写入 `read_result.v1`，再由 `evidence_pack_from_read` 交给 synthesis；deterministic Read
  仍保持无模型调用的兼容模式；
- 清理根目录 `.tmp_tests/`、`.pytest_cache/` 等确认无价值的临时产物；历史 `runs/` 和
  provider 缓存因仍可能用于复现而保留。

尚未宣称完成的部分：

- `simple_ar/_legacy/research_stages.py` 仍需按 Search/Synthesis 的能力矩阵拆出并合并剩余
  成熟行为，随后才能删除；Read 的 screening、paper notes 和 evidence 定位已进入 canonical
  owner，但旧八阶段 projection 仍需保留到兼容消费者退出；
- `literature`、`retrieval`、多个 tools 和 projection 的最终 canonical owner 仍需逐项确认；
- 旧 legacy 实现的最终删除、AutoDL 用户项目回归和 CodeTask 依赖链修复后的稳定真实复跑仍是
  后续退出条件；本轮已经取得真实 provider/LLM 和一次真实 CodeTask 证据，但不能把一次
  `objective_inconclusive` 或网关超时记为研究改进成功。

### 6.4 当前目录消费者审计（2026-09-05）

本轮按真实 import/CLI/test 消费者确认了以下暂留边界；它们不是因为“目录看起来有用”而
保留，也不是同一能力的两套主实现：

| 目录 | 当前唯一职责/消费者 | 当前结论 |
|---|---|---|
| `literature/` | 论文模型、provider client、BibTeX 和 citation 基础；被 `research/sources`、`documents`、`report` 直接使用 | 保留为底层 provider/metadata 层 |
| `research/connectors/` | 将 literature client 转成 source-agnostic `SearchQuery/SearchResponse`；被 `research/sources/registry.py` 使用 | 保留为 canonical source adapter |
| `research/store/` | canonical 文档 chunk 和研究索引；被 document ingest、artifact writer 和 cleanup 使用 | 保留为 canonical document store |
| `retrieval/` | 旧 run artifact 的本地索引/搜索、CLI inspect/search，以及 CodeTask 分析的路径分类 | 暂不删除；它是兼容/运维检索，不替代论文 source search |
| `tools/` | 通用工具权限、trace、CLI/MCP schema 和 agent handoff；仍有公开 CLI/外部执行器消费者 | 冻结，不在 V2.8 扩展为 Harness |
| `experiment/tools/` | 实验命令工具 gateway 和 OpenAI schema；与通用工具权限生命周期不同 | 保留，不与 `tools/` 强行合并 |
| `pipeline_stages/` + `_legacy/` | 旧 `run/resume`、历史 artifact projection、旧测试和外部 adapter | 只作兼容；`research_stages.py` 待消费者替代后删除 |

因此本轮没有删除 `literature`、`retrieval` 或 tools：它们均有可定位消费者。下一次删除只
针对 `_legacy/research_stages.py` 中已由 canonical capability 替代、且满足本计划 5.2
删除门槛的函数和产物；新功能不得回流这些目录。

### 6.5 真实 provider/LLM、CodeTask 与 AutoDL 记录（2026-09-06）

- 单 provider arXiv/OpenAlex 网络 smoke 已成功返回论文元数据；此前真实 online
  `research-session` 还完成过 42 条 raw、6 篇 selected、LLM plan/Read/synthesis/design、
  实验、analysis、report 和 report-audit，报告约 19k 字符，citation/metric/claim 均为
  `passed`。这证明主链可以接真实网络和网关，但仍属于低预算规模。
- 用户将模型切换为 `gpt-5.4-mini` 后，当前网关使用 `chat` 模式成功；本地 `.env` 通过
  `SIMPLE_AR_LLM_API=chat` 固定协议，不再让网关猜测。provider 层对超时、连接中断、限流、
  5xx、Cloudflare 524/origin timeout 使用有上限的指数退避；它只负责同一请求的有限重试，
  不会无限重跑整个 session。
- AutoDL 分支 `feat/v2.8-system-evolution` 在 `21eac85` 上完成低负载 CUDA smoke：系统
  可见 RTX 3090 24 GiB、Torch 2.8.0+cu128、CUDA 可用；smoke 结束后 GPU 回到空闲状态。
  项目本身的依赖环境不强制安装 Torch，避免把 GPU 依赖带入基础闭环。
- AutoDL 真实 CodeTask v6 已完成同一 session 的 LLM plan/read/synthesis/design、隔离 workspace、
  baseline、受限 patch、validation、patched benchmark、comparison、analysis、report 和
  audit。`results.json` 为 `passed`，comparison 为 1 组、7 个指标；report-audit 的
  citation/metric/claim 均通过，产物保存在远程 `runs/autodl-code-task-v6/` 下。
- v4 的长请求曾偶发超时，v5 暴露了 comparison artifact 路径解析问题；`21eac85` 修复后 v6
  成功，说明当前 retry、artifact handoff 和 report evidence appendix 已经通过一次真实远程
  验收。该结果证明“工程闭环”成立，不证明模型一定带来指标提升，也不证明正常用户规模已达标。
- AutoDL `598 tests` 全量测试通过；测试和验收均采用受控命令，没有启动长训练、并行候选或
  高 GPU 占用任务。
- AutoDL v13 规模验收已通过：`60 raw -> 10 selected -> 10 documents -> LLM
  plan/read/synthesis/design -> CodeTask -> baseline/modified experiment -> analysis
  -> report -> report_audit`；report 状态为 `completed`，audit 的 citation/metric/claim
  均为 `passed`。v12 的 reviewer warning 和 v11 的 reviewer-passed 结果也保留在远程 runs
  中，作为报告质量随机性和审阅策略的对照证据。

### 6.6 canonical Search 筛选迁移记录（2026-09-05）

本轮把旧 Search 中已有测试支撑、且不依赖旧 stage artifact 布局的最小策略迁移到
`research.sources.capability`：

- `SearchSelectionPolicy` 接收已完成的 research plan、问题 facet 和显式论文预算；
- `select_search_result` 通过已有 `research.evidence.retrieval` 完成 identity 去重、相关性排序、
  facet 优先保留和预算截断；
- coverage 通过已有 `research.evidence.coverage` 生成，provider 失败仍保留在 responses；
- `SearchResult` 的旧 `papers` 仍表示原始扁平 provider 输出，新增 `selected_papers` 供 canonical
  ingest 使用，因此未改变旧 API 的原始结果语义；
- 当调用方显式提供 `cache_dir` 且 source plan 允许缓存时，canonical source 会缓存成功的元数据，
  provider 失败时才读取同一 query/source/limit 的缓存；命中状态为 `cached`，原始失败原因仍在
  response message 中，不会把缓存伪装成 fresh provider 成功；
- 该 handoff 已有 capability 单测和 brief/session 回归，旧 legacy Search 的 provider/cache/多轮
  兼容逻辑仍未删除，后续必须继续按保留矩阵审计。

### 6.7 Search/Read/Synthesis 保留矩阵（2026-09-05）

| 旧行为 | canonical 状态 | 当前决定 | 删除旧实现的前提 |
|---|---|---|---|
| provider 调用、失败响应归一化 | `research/sources/capability.py` 已覆盖 | 已迁移 | 旧 CLI projection 完成回归后删除重复调用 |
| 去重、相关性排序、论文预算、facet coverage | `research/evidence/retrieval.py` + `evidence/coverage.py` 已接入 Search handoff | 已迁移 | 旧 Search 不再是唯一消费者后删除重复编排 |
| 成功元数据缓存、失败后的显式 cached recovery | `SearchRequest.cache_dir` 可选支持 | 已迁移 | 完成真实 provider/cache smoke 后删除旧专用分支 |
| 多轮 coverage follow-up 查询 | 仍在 `_legacy/research_stages.py` | 暂留兼容；不是 V2.8 单轮主线验收条件 | 证明 canonical 单轮预算不足以满足目标，或将其作为明确 bounded policy 迁移 |
| LLM coarse screening/rerank | canonical `research.evidence.screening` 被 Read 调用；旧 facade 复用同一 policy | 已迁移；旧 artifact 仍是 projection | CLI projection 完成回归后删除旧编排 |
| LLM paper notes / `notes.md` | canonical Read 的 `paper_notes`、`notes_markdown`；旧 facade 继续落地历史文件 | 已迁移 typed 行为；保留旧 projection | 历史 reader/CLI 退出后删除旧写出 |
| source chunks、coverage、cards 到 synthesis 的证据定位 | canonical Read handoff + `evidence_pack_from_read` 提供 bounded snippets/refs/notes | 已迁移 | 保持 snippet 上限、notes 和引用回归 |

这张矩阵的原则是：V2.8 主线只吸收能改善正式闭环且不引入额外调度复杂度的行为；旧 CLI
仍需要的输出先留在冻结 compatibility 层，不再作为新能力的落点。

### 6.8 AutoDL/3090 低资源验收边界（已完成，2026-09-06）

GPU 服务器是 V2.8 的真实环境验收手段，不是新的编排层。AutoDL helper、低负载 CUDA smoke
和真实服务器规模验收均已按低消耗顺序完成：

1. 记录 GPU、Python/uv、commit、依赖和数据/项目路径；
2. 先跑本地 fixture 完整 smoke，确认环境和 artifact 写入；
3. 再跑单 provider、单结果、有限 chunks/idea、默认有界重试和短超时的 online smoke；
4. 使用准备好的单个 CodeTask 项目验证 baseline、受限 patch、validation、experiment、
   analysis、report/audit；
5. 规模验收从小 batch、少量 epoch 和单候选开始，并保留完整 session 目录。

V2.8 不引入 GPU 自动申请、训练队列、并行候选或自动资源调度。GPU 实验失败必须保留失败
   attempt 和诊断；online/LLM 失败不能用 fixture 结果静默替代。具体命令和记录项见
   `examples/README.md` 的 AutoDL/3090 小节。

### 6.9 V2.8 正常用户规模验收（已通过，2026-09-06）

这是 V2.8 宣布“正式基础闭环完成”的最后一个业务验收闸门，不是新的框架层。验收固定为
一条受控命令和一个准备好的项目，本次已完成：

```text
30–50 raw papers
  -> 10–20 bounded Read candidates/notes
  -> real LLM plan/synthesis/design
  -> one bounded baseline/modified experiment
  -> metric comparison + analysis
  -> full Markdown experiment report + report_audit
```

执行规则：

- `--max-results`、selected paper budget、`--max-chunks`、`--idea-limit`、请求超时和重试
  次数显式固定；raw provider 返回量可能有小幅偏差，但必须完整记录，不因长上下文而无限扩大预算；
- 先使用现有准备好的项目和真实数据/代码，实验只允许一个受控方向、一个 benchmark 和
  明确的 batch/epoch/时间上限；
- 第一轮关闭 reviewer，先验证 Writer、证据附录和 audit 的事实完整性；若基础报告通过，
  再单独尝试最多一轮 reviewer，不能把 reviewer 失败混入主闭环失败；
- 验收记录 raw/selected 数量、Read notes、LLM attempt、实验命令、资源峰值、报告章节、
  引用/指标/claim audit 和完整 output root；
- 若网络、模型或实验失败，保留 session 作为失败证据，检查后再决定一次有界 continuation，
  不用 fixture 结果覆盖真实失败，也不连续重跑消耗资源。

退出条件已满足：v13 session 为 `completed`，报告章节齐全，引用和数字可以回溯，audit
三项均为 `passed`，且 AutoDL 运行采用单候选、受限 benchmark、短实验和无并行设置。V2.8
现在进入冻结和小修阶段；后续只修复真实阻塞，不新增顶层抽象。

本次实际记录：`60 raw -> 10 selected -> 10 documents -> LLM plan/read/synthesis/design
-> CodeTask -> baseline/modified experiment -> analysis -> report -> report_audit`。v13
使用 `report-reviewer=disabled` 验证基础报告的确定性证据链；v11 的一轮 reviewer 结果为
`completed`，v12 的 reviewer 结果为可解释的 `partial/warning`，两者都保留为报告质量回归
样本，而不改变 v13 的闭环结论。

#### 6.9.1 本次选定的验收方向

本次不选择需要下载大模型或长时间训练的任务，先用“轻量图像分类中模型容量/训练策略对
小样本泛化的影响”作为正常用户规模验收方向：

- 数据使用现有 `examples/full_pipeline_tiny_mlp/` 的 `sklearn.datasets.load_digits`，约
  1,800 条 8×8 图像样本，优先走已安装的公开数据集，不在 benchmark 中隐式下载；
- 代码保留为一个可读的多模块项目，包含 data split、MLP model、training、metrics、
  benchmark、tests 和 CLI 输出，研究修改可以落在训练策略、特征/模型设置和配置接线上；
- 实验先固定一个 baseline，再运行一个受限 modified condition，保留 accuracy、macro F1、
  训练/推理时间、参数量和配置 provenance；需要多 seed 时只增加有限重复，不引入实验树；
- 3090 作为可用的资源上限和后续 Torch/CUDA 复核环境，而不是强制启动 GPU。第一轮优先让
  CPU/packaged-data 路径稳定，若该方向的真实代码任务暴露出 GPU 分支的必要问题，再用小
  batch、少量 epoch 接入 CUDA；
- 方向足够接近真实研究任务，但数据、代码和 benchmark 都能提前审查。公开代码、数据下载、
  依赖安装和模型选择仍作为显式任务输入，不由 LLM 静默扩大权限；如果真实任务缺少资源，
  先补齐准备好的项目/configuration，再从已有 session 边界继续，不为此新增人工接力子系统。

#### 6.9.2 真实任务边界观察（不单独扩展）

资源准备、代码作用域和报告 continuation 是真实任务可能遇到的边界，但不是 V2.8 要单独
建设的人工接力子系统。规模验收只复用现有入口和状态，不另造总控状态机：

```text
research-session
  -> prepared project / explicit task scope
  -> CodeTask validation
  -> experiment -> analysis -> report
```

如果用户确实需要补充资源或确认较大代码变更，现有 CodeTask approval、`ready_for_report`
和 `research-report` continuation 已提供边界；只有真实运行暴露出无法表达的阻塞，才补带
reason、required_inputs、resume command 和审计 artifact 的最小能力。原则是观察并修复实际
阻塞，不把人工步骤本身发展成新的主线。

### Phase 4：V2.8 报告输出稳定化

先完成一个稳定的 Markdown/结构化报告 profile：

- 研究问题、方法、证据、实验设置、结果、限制；
- 引用、数字和指标审计；
- 结构化图表和 figure manifest；
- Markdown 报告的章节、引用、数字、指标和限制稳定可审计。

本阶段以 Markdown experiment profile 为 V2.8 边界；可交给 Overleaf 的 `.tex/.bib/figures`
输出包、网页端编译反馈记录和固定模板工程化顺延到 V2.9。本地不部署 LaTeX 编译器，也不
在 V2.8 内承诺自动适配所有会议和期刊模板。

### Phase 5：AutoDL 真实实验验证（6.9 已完成，后续按需复现）

低资源 smoke 和 6.9 规模验收已经用 3090 服务器和真实环境验证了：

- 实际模型、数据集和训练/推理命令；
- GPU 资源、超时和依赖；
- 代码错误迭代和有限 repair；
- 失败后的恢复和实验结果可复现；
- 报告是否真实反映实验，而不是 fixture 文本；
- 规模验收是否在明确的 GPU/CPU/磁盘预算内完成。

### Phase 6：V2.8 之后的外部 Harness

最后再接入 Claude Code、Codex、OpenCode 等。它们作为可替换的代码执行/交互适配器，
SimpleAutoResearch 继续掌握研究契约、实验、指标、权限、artifact 和审计。外部 Harness
不进入 V2.8 的核心验收，也不反向决定项目的主状态模型。

## 7. 测试和验收分层

每个迁移点至少执行：

1. domain/capability contract tests；
2. application/session/artifact tests；
3. CLI 和历史入口回归；
4. 正常、失败、降级和恢复 fixture；
5. 必要时受控真实 provider/LLM smoke；
6. `compileall`、`git diff --check` 和全量测试。

V2.8 的正式验收报告必须同时记录：

- 阶段是否执行；
- artifact 是否产生；
- LLM 是否真实调用；
- 代码和实验是否真实执行；
- 报告和审计是否完成；
- 失败是否透明；
- 哪些仍是 deferred 或 partial。

## 8. 后续开发纪律

- 新能力只有一个 canonical owner；
- application 负责固定流程，domain 负责规则，adapter 负责外部副作用；
- 不新增万能 `ResearchTask`、任意 DAG 或无消费者 registry；
- 不在旧大文件中继续堆新分支；
- 不用目录里“存在某文件”代替明确的结果状态；
- 不让 LLM 返回任意 stage、路径或命令直接驱动系统；
- 失败、重试、repair 和恢复必须创建可追踪的 attempt；
- 每个提交只解决一个可说明的边界，提交信息说明行为变化和验证结果；
- 文档中的“已完成”必须能回到代码、测试或持久化 artifact。

## 9. 当前下一步

V2.8 的基础闭环已经通过，当前不再新增顶层能力。后续按以下顺序推进：

1. 冻结 `research-session` 的 canonical 主线和 `full_pipeline_tiny_mlp` 验收基线；只有真实
   运行暴露出配置、预算、重试、artifact handoff 或报告事实问题时才做小修；
2. 完成 Phase 3 最后一次消费者审计：对 `_legacy`、旧 projection、`literature`、`retrieval`
   和 tools 逐项确认。已被 canonical 行为替代且无真实消费者的实现直接删除；仍被旧 CLI、
   benchmark 或历史 reader 使用的部分只保留薄 compatibility 层；
3. 把 v12 这类 Reviewer warning 作为报告质量回归样本，继续改善 Writer/Reviewer 对本地
   实验事实、文献动机和因果边界的区分，但不把 Reviewer 随机输出变成闭环状态机；
4. V2.8 冻结后进入 V2.9：先稳定一个 Markdown/Overleaf-ready 模板和有限 continuation，再
   评估多模板、复杂 repair；Claude Code、Codex、OpenCode 等外部 Harness 最后接入；
5. 每次修改继续更新本计划、handoff、长期愿景和中英文 changelog，并提交可复核 checkpoint。

V2.8 冻结阶段不新增 scheduler、任意 DAG、外部 Harness、通用模板适配或新的顶层抽象；
V2.9 再进入 Markdown/Overleaf 工程化与有限恢复设计。
