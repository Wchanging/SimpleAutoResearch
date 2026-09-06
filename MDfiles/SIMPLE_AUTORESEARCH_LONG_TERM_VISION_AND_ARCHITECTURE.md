# SimpleAutoResearch 长期愿景与架构总图

> 文档性质：项目长期方向、产品边界和架构原则，不是某个版本的施工清单。
>
> 更新时间：2026-09-06。代码验证基线：`4a19b5e`。
>
> V2.8 的唯一施工计划见 `SIMPLE_AUTORESEARCH_V2_8_SYSTEM_EVOLUTION_PLAN.md`；本文只保留长期视角和版本边界。

## 0. 文档关系

当前文档职责已经收束：

| 文档 | 职责 |
|---|---|
| `SIMPLE_AUTORESEARCH_LONG_TERM_VISION_AND_ARCHITECTURE.md` | 长期愿景、版本边界、最终架构原则 |
| `SIMPLE_AUTORESEARCH_V2_8_SYSTEM_EVOLUTION_PLAN.md` | V2.8 唯一施工计划 |
| `SIMPLE_AUTORESEARCH_V2_8_SESSION_HANDOFF.md` | 当前会话状态和下一步入口 |
| `SIMPLE_AUTORESEARCH_V2_8_PHASE0_BASELINE.md` | 冻结基线、消费者和产物审计 |
| `SIMPLE_AUTORESEARCH_STAGE_DECOUPLING_AUDIT.md` | 阶段职责与历史治理证据 |
| `SIMPLE_AUTORESEARCH_V2_8_UNDERGRADUATE_WORK_PLAN.md` | 协作者任务手册 |

如果版本计划、协作手册或历史审计之间存在路线冲突，以 V2.8 主计划或更高版本的当前计划为准。

## 1. 产品愿景

SimpleAutoResearch 的长期目标不是“自己实现一个最大的编程 Agent”，而是提供一个可审计、
可恢复、可替换执行器的研究自动化底座：

```text
研究问题
  -> 资料与证据
  -> 研究方向/假设
  -> 研究设计与代码任务
  -> 实验执行
  -> 指标和结果分析
  -> 报告、论文材料和审计
```

用户可以替换 LLM、检索 provider、代码 Agent 和实验执行环境，但以下内容必须始终可控：

- 研究问题和证据来源；
- 实验契约、命令、数据和资源限制；
- 代码变更范围与权限；
- 指标、比较和结论门槛；
- artifact、attempt、lineage、失败和恢复记录。

系统的成熟度不以 Agent 类数量、工具数量或代码行数衡量，而以用户能否回答以下问题衡量：

1. 当前研究进行到哪一步；
2. 每一步使用了什么输入并产生了什么输出；
3. 结果是否来自真实资料和实验；
4. 失败后能否恢复或明确停止；
5. 换一个执行器是否不会破坏研究审计链路。

## 2. 版本边界

### 2.1 V2.8：有界端到端闭环

V2.8 先完成一个正式可试用的 research-to-report slice：

```text
网络/本地资料 + 准备好的 baseline/data/code
  -> plan -> search -> ingest -> read -> synthesize
  -> design -> code_task（可选） -> experiment -> analysis
  -> report -> report_audit
```

它允许配置 LLM 参与计划、阅读辅助、综合、设计、分析和写作，但不允许模型自由决定任意
路径、任意文件或无限循环。实验命令、baseline、数据集、资源和指标由用户或配置明确提供。

V2.8 的完成分成两层：第一层是网络/LLM/代码任务/实验/分析/report/audit 的工程闭环，已经
由本地回归和 AutoDL 真实 CodeTask session 证明；第二层是正常用户规模的正式试用。该层已
在准备好的 `full_pipeline_tiny_mlp` 项目上通过一次：实际为 60 条 raw、10 篇 selected/read
documents、一个有资源边界的 baseline/modified 实验，以及包含 Abstract、Problem/Motivation、
Related Work、Method、Experimental Setup、Results、Discussion、Limitations、Verified
Experiment Metrics、References 的完整 Markdown 报告，session 和三项 report audit 均通过。
这表示 V2.8 的有界基础闭环已完成；不表示任意主题、任意代码库上的完整自主 AutoResearch。

V2.8 不承诺：完整自主研究、实验树、多候选调度、远程资源治理、原生外部 Harness、全模板
LaTeX 自适应或 publication-ready 论文。

### 2.2 V2.9：报告工程与有限恢复

V2.8 主链稳定后，优先做：

- 一个固定论文/实验报告 profile；
- 稳定的 Markdown 和 Overleaf-ready `.tex/.bib/figures` 导出；
- 引用、数字、图表和报告完整性审计；
- 有预算、有停止条件的补证据、repair、重跑和报告修订；
- 记录 Overleaf 网页端编译反馈，而不是在本地假装完成 LaTeX 编译。

暂时不伪装成自动适配所有会议和期刊模板。先把一个模板做稳，再根据真实需求扩展。

### 2.3 后续：外部 Agent Harness

Claude Code、Codex、OpenCode、Cursor 或类似 openresearch-cli 的执行器应作为适配器接入：

```text
外部 Agent Harness
        ↓
统一任务、权限、工作区和 artifact 接口
        ↓
SimpleAutoResearch 研究流程和实验底座
```

外部 Harness 可以负责代码阅读、修改、测试和交互，但不能接管 SimpleAutoResearch 的研究
契约、实验结果、指标判定、权限、lineage 和报告审计。skill 是行为注入和操作说明，不替代
进程、事件流、权限和结果协议的适配。

### 2.4 最终 AutoResearch

在 V2.8/V2.9 基础稳定后，才考虑：

- Agent 提出多个假设和实验候选；
- 系统按明确规则选择下一候选；
- 多轮代码修改、实验、比较和晋级；
- Git-native 实验树、分支工作区和并行执行；
- 跨实验记忆、停止判断和人工接管。

完整 AutoResearch 是多个已验证底座的组合，不是再增加一个总控 Agent 类就能得到的功能。

## 3. 最终架构原则

### 3.1 一个概念只有一个 owner

每个研究事实、状态、契约、连接器和报告产物都必须有唯一 canonical owner。旧格式、旧 CLI
或 benchmark 需要的内容只能通过集中式 compatibility reader/writer 转换，不能把旧判断散落
到新模块中。

### 3.2 固定依赖方向

```text
CLI / benchmark adapter
          ↓
Application use case
          ↓
Core runtime：session、artifact、capability、transition、budget
          ↓
Research / CodeTask / Experiment / Analysis / Report
          ↓
Integrations：LLM、网络、文件、subprocess、外部 Agent
```

领域能力不能依赖 CLI；core 不能依赖具体 provider；报告不能偷偷决定重跑实验；外部 Agent
不能绕过权限和 artifact 边界。

### 3.3 目标目录的概念归属

```text
simple_ar/
├─ core/             # 生命周期、artifact、能力、转换和预算
├─ app/              # research-session 等固定用例编排
├─ research/         # planning、sources、documents、evidence、synthesis、design
├─ code_task/        # 代码分析、编辑、review、repair、验证
├─ experiment/       # 实验契约、执行和结果规范化
├─ result_analysis/  # 指标、比较和分析
├─ report/           # 报告、引用、图表和审计
├─ integrations/     # LLM 与外部系统适配
├─ cli/              # 参数解析和展示
└─ compatibility/    # 旧入口和旧 artifact 的薄适配
```

这是一张责任图，不要求为了目录名称机械重命名。当前 `pipeline_stages/` 可以暂时作为
compatibility 的物理实现，但不能继续接收新的核心业务逻辑。

## 4. 当前代码的长期处理方向

### 保留并强化

- `core` 的 session、attempt、artifact、lineage、budget 和有限 transition；
- `research` 的显式来源、文档、证据和 typed handoff；
- CodeTask 中已经实际承担代码分析、受限修改、review、repair 和验证的能力；
- Experiment/Analysis 的真实执行、指标和失败诊断；
- Report 的 citation、numeric audit、figure 和 quality 检查。

### 择优合并

- 旧搜索中的 provider fallback、缓存、去重和 coverage；
- 旧阅读中的 screening、摘要、重点定位和异常处理；
- 旧综合中的成熟 prompt、上下文裁剪和 gap/idea 规则；
- 旧实验和报告中的运行记录、审计和兼容输出。

这些行为要进入唯一 canonical owner，而不是把整个旧大文件搬到新目录。

### 冻结或删除

- 无消费者的 scheduler、候选调度和通用 iteration policy；
- 无执行者的 Tool/MCP 设计层；
- 重复的 literature/retrieval/provider/reader/writer；
- 仅凭目录存在判断状态的隐式 projection；
- 没有真实消费者的 registry、schema、wrapper 和配置。

删除前必须有引用审计、替代实现、fixture、snapshot/语义回归和必要的真实 smoke。

## 5. 项目维护协议

- V2.8 的路线只在主计划中维护；
- 长期愿景只记录版本边界，不复制施工步骤；
- handoff 只记录当前事实，不把设想写成已实现；
- baseline 和 audit 记录证据，不决定新路线；
- 新功能先写清 canonical owner、输入输出、失败状态和消费者；
- 先用小 example 验证，再扩展到真实 provider、LLM 和 AutoDL；
- 任何外部 Agent、GPU、远程执行和模板功能都必须以实际消费者为前提；
- 进度以可复现闭环和清晰删除结果衡量，不以新增文件数衡量。

## 6. 当前判断

`09df140` 是一次有效的瘦身 checkpoint，`4a19b5e` 是本次代码验证基线。随后已经完成一次主线
边界收口和工程闭环验收：

- 模型驱动的 `research-session` 默认到达 report/audit，`--no-report` 只用于调试；
- code-task 已以明确 bridge 进入同一 session 的 canonical experiment attempt；
- 本地低资源 smoke 已实际生成完整 session、report 和 audit；受控纵向回归已实际调用
  production CodeTask bridge，在隔离副本中完成 baseline、受限 patch、validation 和指标比较；
- 当前网关使用 `gpt-5.4-mini + chat` 已完成一次真实网络/LLM 到 report/audit 的完整 session；
  AutoDL 真实 CodeTask v6 也完成了隔离修改、baseline/patched comparison、analysis、report
  和 audit，1 组 comparison 的 7 个指标均保留在产物中；指标没有提升时仍正确记录
  `objective_inconclusive`，不把“流程完成”冒充为“研究改进成功”；
- AutoDL 低负载 CUDA smoke 和 `598 tests` 全量回归已通过；当前没有启动长训练、并行候选或
  GPU 自动编排；
- 无生产消费者的 code-task 兼容 facade 和确认无价值的根目录临时产物已删除；
- 旧八阶段搜索/阅读/综合实现已私有归档并冻结，公开 `pipeline_stages/research.py` 只剩
  alias；legacy 业务本体仍按消费者矩阵保留最后的 compatibility 尾项；正常用户规模的
  检索/阅读/中等实验/完整论文式 Markdown 报告验收已由 AutoDL v13 完成。

当前主线是 V2.8 冻结和最后的兼容尾项审计：

```text
V2.8 基础闭环（已通过）
  -> canonical 主线冻结
  -> 按消费者证据删除最后的 legacy 重复实现
  -> V2.9 固定 Markdown/Overleaf-ready 模板与有限恢复
  -> 最后接入外部 Agent Harness
```

V2.8 通过后仍不能跳过 Phase 3 的最后删除审计，但这一步只处理已经确认无消费者且有
canonical 替代的重复实现；旧 CLI、benchmark 或历史 reader 仍需的内容继续作为薄兼容层。
完成这次收口后，再进入 V2.9 的 Report/Overleaf 工程化和有限恢复，最后才接入外部 Harness。
