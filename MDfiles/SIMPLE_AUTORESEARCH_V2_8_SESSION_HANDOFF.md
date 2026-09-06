# SimpleAutoResearch V2.8 新会话交接文档

> 文档职责：记录当前会话的真实状态和下一步入口，不维护第二套 V2.8 计划。
>
> **唯一施工计划**：`MDfiles/SIMPLE_AUTORESEARCH_V2_8_SYSTEM_EVOLUTION_PLAN.md`
>
> 状态快照：2026-09-06。Phase 3 清理起点：`29f212f`；当前 checkpoint：`3c575d4`
>（`feat/v2.8-system-evolution`）；当前本地
> `.env` 使用 `gpt-5.4-mini` 和网关可用的 `chat` 模式。V2.8 有界基础闭环已完成，完整
> AutoResearch 仍按长期路线推进。

## 0. 新会话阅读顺序

1. 本文：当前进度、真实入口和已知缺口；
2. `MDfiles/SIMPLE_AUTORESEARCH_V2_8_SYSTEM_EVOLUTION_PLAN.md`：唯一有效的 V2.8 目标和施工路线；
3. `MDfiles/SIMPLE_AUTORESEARCH_LONG_TERM_VISION_AND_ARCHITECTURE.md`：全项目长期方向；
4. `docs/WORKFLOWS_zh.md`：capability、session、attempt、artifact 的实际关系；
5. `docs/CLI_REFERENCE_zh.md`：命令和参数；
6. 改代码时，再读取对应的 `src/simple_ar` 模块和测试。

以下文档不是当前施工计划：

- `SIMPLE_AUTORESEARCH_V2_8_PHASE0_BASELINE.md`：冻结基线和审计证据；
- `SIMPLE_AUTORESEARCH_STAGE_DECOUPLING_AUDIT.md`：阶段职责和历史清洗记录；
- `SIMPLE_AUTORESEARCH_V2_8_UNDERGRADUATE_WORK_PLAN.md`：协作者手册，服从主计划。

## 1. 当前一句话结论

当前项目已经有一条显式的 capability/session 路径和一条旧八阶段兼容路径。最近的清理已经
删除无消费者的超前层，并完成了同一 session 中 `code_task -> experiment -> analysis ->
report/audit` 的真实 AutoDL 受控验收。v13 又在真实网络、`gpt-5.4-mini + chat` 和准备好的
轻量项目上完成了 `60 raw -> 10 selected -> 10 documents -> LLM plan/read/synthesis/design
-> CodeTask -> experiment -> analysis -> report -> report_audit`，session 为 `completed`，
报告和三项 audit 均通过。因此 V2.8 的“正常用户可用基础闭环”已经通过；这不等于任意主题、
任意代码库上的完整自主 AutoResearch，也不等于 publication-ready 论文。

旧入口仍有冻结兼容门面，但 Search、Read、Synthesis 的业务规则已不再在旧路径重复实现：
`_legacy/research_stages.py` 现在委托 canonical capability，只保留旧 Context/artifact 投影、
bounded retrieval trace、一次 coverage follow-up 和历史注入点。它暂时不能删除，因为旧
`run/resume`、SurveyBench、历史 reader 和旧测试仍有消费者；这属于有证据的兼容尾项，不是
另一条正在演进的研究主线。

当前工作目标是一次有安全网的架构切换：新路径成为唯一演进方向，旧路径冻结为兼容入口，
旧实现中的成熟行为择优合并后，删除重复实现。

## 2. 当前 Git 和验证状态

- 分支：`feat/v2.8-system-evolution`；
- checkpoint：`29f212f docs(v2.8): record scale acceptance and next phase` 是本轮 Phase 3 的
  清理起点；该提交之前已将权威的
  baseline/candidate/comparison 投影同时交给 Writer 和 Reviewer，避免真实实验已存在却被
  报告审阅误判为缺失；
- 远端：AutoDL 工作树已切换到 `3c575d4`，与 `origin/feat/v2.8-system-evolution` 一致且
  干净；远端仓库仍是浅 clone，但不影响当前代码和分支同步；
- 工作树：本轮 canonical research facade、planning query handoff、bounded source stop 和
  架构回归修改已提交并推送；旧 research 实现已收缩为冻结兼容 facade；
- 本地全量回归：`uv run --no-sync python -m unittest discover -s tests -p 'test_*.py'`，600 项
  通过（204.764 秒）；本轮边界聚焦回归 40 项通过。AutoDL 同一 checkpoint 的 40 项聚焦回归、
  fixture smoke 和 600 项全量回归也均通过（全量 29.476 秒）；
- 入口修复后补跑的边界/CLI、session/public API/search、report 和 synthesis 聚焦回归均通过；
- 本轮已通过 code-task/report、report/audit、CLI 默认行为和 LLM fallback 的聚焦回归；
- AutoDL 真实 CodeTask v6 已通过：同一 session 中完成 LLM plan/read/synthesis/design、隔离 workspace、baseline、受限 patch、validation、patched benchmark、comparison、analysis、report/audit；comparison 为 1 组、7 个指标，report audit 三项通过；
- 已完成低负载 CUDA smoke：RTX 3090 24 GiB、Torch 2.8.0+cu128、CUDA 可用；结束后 GPU 空闲，没有启动长训练；LLM client 默认对临时 provider 错误最多尝试 3 次并采用有上限的指数退避，且显式覆盖 Cloudflare 524/origin timeout；canonical Read 已加入 bounded screening/rerank、paper notes 和 source snippets；
- 语法检查：`uv run --no-sync python -m compileall -q src tests examples` 通过；
- `git diff --check`：通过。

仓库现在只追踪本 handoff、V2.8 主计划和长期愿景三份活动文档；`MDfiles/` 中其余历史/临时
规划笔记仍保持忽略。这样协作者可以从 Git 获取唯一有效的当前计划，同时不会把所有过程笔记
重新变成并行路线。

## 3. 当前两条路径

### 3.1 新路径：未来唯一正式主线

```text
research-session
  -> plan
  -> search
  -> document_ingest
  -> read
  -> synthesize
  -> research_design
  -> experiment
  -> analysis
  -> report
  -> report_audit
```

其中 `code_task` 作为显式配置接入时，会在 canonical `experiment` attempt 中执行；没有代码
任务时不会凭空增加这一步。模型驱动的 `research-session` 默认继续执行 report/audit，
`--no-report` 是调试和快速模式；无模型的 deterministic session 仍停在分析结果。

新路径的核心基础位于：

- `src/simple_ar/core/`：artifact、capability、session、attempt、transition、budget；
- `src/simple_ar/research/`：planning、sources、documents、evidence、synthesis、design；
- `src/simple_ar/app/`：研究用例的固定组合；
- `src/simple_ar/experiment/`、`result_analysis/`、`report/`：实验、分析和报告能力。

### 3.2 旧路径：冻结兼容入口

```text
simple-ar run
  -> PipelineRunner / pipeline_stages（兼容 registry）
  -> pipeline_stages/research.py（alias） -> simple_ar/_legacy/research_stages.py（兼容 facade）
  -> Plan -> Search -> Read -> Synthesize -> Design -> Code -> Run -> Report
```

ARC-Bench、SurveyBench、历史 run reader 和部分旧 artifact projection 仍依赖它。旧入口不
再新增业务能力；research prefix 的 provider/search/read/synthesis 规则已经委托 canonical，
剩余代码只负责旧输入输出转换、历史 trace/ledger 和兼容测试。待这些消费者迁移并通过旧格式
回归后，再删除 facade。

## 4. 最近清理保留和删除了什么

已保留并作为后续合并基础：

- artifact/store、typed handoff、session/attempt/parent/decision/budget；
- provider、文档摄取、证据卡片和 synthesis 产物；
- CodeTask 的仓库分析、作用域、快照、review、repair 和验证；
- Experiment/Analysis 的执行、指标和失败诊断；
- Report 的引用、数字审计、图表和质量检查；
- 旧 CLI、历史格式、benchmark 和必要兼容层。

已删除或冻结：

- 无消费者的 `core/session_plan.py`；
- 无生产消费者的 `experiment/code_task_experiment.py` 兼容 facade；
- CodeTask 多候选调度和比较入口；
- 独立 research iteration policy；
- 没有执行者的 research Tool/MCP 设计、evaluation/retention 草案；
- 默认复合 `research_brief` 作为新 session 隐藏阶段。
- 旧 `pipeline_stages/research.py` 的公开文件已收缩为 alias，`simple_ar/_legacy/research_stages.py`
  已收缩为兼容 facade；旧 Search/Pipeline/Document Ingest 回归 31 项通过，本轮又补了
  canonical source stop 和 canonical import boundary 回归。

`simple_ar.cli` 已改为惰性导出，消除了 `python -m simple_ar.cli.main` 的重复加载 warning，同时保持
`from simple_ar.cli import main` 兼容。

删除的是无真实消费者的设计，不代表旧代码中所有成熟细节都被放弃。后续必须按主计划的
能力保留矩阵，将旧路径中更好的 fallback、缓存、筛选、修复、审计等行为合并到 canonical owner。
旧 `pipeline_stages.common` 中的通用 artifact/LLM helper 已迁入 `core.runtime`，该文件现在只
保留旧 source-plan/evidence projection 辅助；`pipeline_stages.experiment/report` 只是旧 registry
转发到共享的 experiment/report 实现，不是第二套业务核心。

## 5. 已有证据与未证明事项

已有证据：

- deterministic fixture 已验证 `plan -> search -> ingest -> read -> synthesize -> design -> experiment -> analysis`；
- 已完成单 provider arXiv 网络 smoke；旧的 `gpt-5.4` 配置曾在 `plan` 阶段返回 503
  `model_not_found`，用户改为 `gpt-5.4-mini` 后确认当前网关应使用 `chat` 模式，本地 `.env`
  已记录 `SIMPLE_AR_LLM_API=chat`；
- 另一次低资源 deterministic arXiv `research-session` 已实际走通真实网络到 analysis（1 个 selected / 6 个 raw），但无 LLM，按设计未自动生成 report；
- 一次严格限额的真实 `research-session` 已完成网络检索、LLM plan/synthesis/design、实验、
  analysis、report 和 report/audit：42 个 raw、6 篇 selected，报告约 19k 字符，citation、
  metric、claim audit 全部 `passed`，session 状态为 `completed`；
- 受控 fixture 已覆盖同一 session 的 `code_task -> experiment -> analysis -> report/audit`，并验证报告审计会检查正文中的指标和引用；
- canonical Search 已把旧检索中有独立测试支撑的去重、相关性/预算筛选和 facet coverage 接入同一
  `search_handoff.v1`，原始 provider 响应与筛选后论文均可追溯；显式提供 `cache_dir` 时，成功
  元数据缓存和失败后的 cached recovery 也保留可审计状态；
- `examples/research_session_smoke.py` 已实际生成完整的本地 session、report 和 audit；
- `examples/autodl_low_resource_smoke.sh` 已准备好；AutoDL 上另行完成了低负载 CUDA smoke，
  记录 RTX 3090 24 GiB、Torch 2.8.0+cu128 和 CUDA 可用性，结束后 GPU 空闲，没有启动长
  训练或并行候选；
- 新增的纵向回归实际调用 production CodeTask bridge，在隔离副本中完成 baseline、LLM 结构化计划/patch、受限编辑、validation、指标比较，再进入 report/audit；此前一个小型真实样例只修改了 `features.py`，baseline 与 patched 的 `accuracy` 均为 `0.642857`，正确记录为 `objective_inconclusive`；随后 AutoDL v6 在 `21eac85` 上完成了修复后的真实 CodeTask 到 report/audit，comparison 为 1 组、7 个指标，三项 report audit 均通过；
- canonical Read 接入后的第一次受限 online smoke 在 `synthesize` 处遇到网关请求超时；随后最小
  health-check 成功，并将每请求重试上限设为 2 后重新执行，同样的单 provider/单结果低预算路径
  已完成 Search、LLM plan、Read screening/notes、synthesis、design、实验、analysis、report
  和 report-audit，session 为 `completed`，citation/metric/claim audit 均为 `passed`；两次临时
  运行产物均已检查并清理；
- 已修正 report audit 对 selected source metadata、可读指标名和科学计数法的识别，旧真实报告在不改正文的情况下重审为 `passed`；已修正 embedded CodeTask bridge 将严格串行依赖链合并为一个有界 batch，聚焦回归通过；
- 已验证实验失败会保留错误和 attempt，并可显式 continuation；
- AutoDL v13 规模验收已完成：60 raw、10 selected、10 documents，实验结果 `passed` 且 comparison
  verdict 为 `improved`；报告含 10 个主要章节，`report_audit.json` 的 citation/metric/claim
  均为 `passed`，session manifest 为 `completed`。v11（reviewer passed）和 v12（reviewer
  warning）也保留在 AutoDL `runs/`，用于后续报告质量回归；
- `600 tests in 204.764s — OK`；本轮 40 项边界聚焦回归、compileall 和 `git diff --check`
  均已通过；AutoDL 同一 checkpoint 的全量回归和 fixture smoke 已复核通过，3090 在受控
  检查结束后保持空闲。

仍未证明：

- 真实用户项目和真实 LLM 下 CodeTask 能稳定产生有效改进；当前证据证明的是隔离修改、验证、比较和“无提升时如实记录”，不证明自动得到更优指标；
- 完整的代码错误迭代、实验重跑、结果比较和报告更新在规模 session 中的稳定性；
- 多 provider 稳定性和任意主题上的研究质量；
- Reviewer 多轮修订在不同模型输出下的稳定性；v12 已记录一组可解释 warning，尚未把它
  当作主闭环失败；
- AutoDL/3090 上真实中等数据/模型实验的资源边界和复现性；
- publication-ready 论文、LaTeX 编译和模板自适应；
- 外部 Claude Code/Codex/OpenCode Harness 的成熟接入。
- 当前 checkpoint 的两次在线 provider smoke 未形成新的成功运行：一次在 plan 请求超时，
  一次完成 plan/search/document 后在 Read 批量模型请求阶段等待过久而人工中止。失败 session
  保存在 `runs/verification/phase3-online-timeout-3c575d4/` 和
  `runs/verification/phase3-online-retry-3c575d4/`，这属于网关可用性/批量耗时观察，不覆盖
  之前 v13 的真实闭环成功证据。

## 6. 当前下一步

V2.8 业务闭环和 Phase 3 代码清理均已验收；当前 checkpoint `3c575d4` 已提交、推送 GitHub，
并在 AutoDL 完成同 commit 同步。canonical 主线只有一条，旧路径只保留明确的 Context、旧
artifact、历史 reader 和 benchmark 兼容职责；旧 facade 不再承载新研究规则。

本轮验证已完成：本地/远端全量测试各 600 项通过，远端 40 项边界回归和 fixture smoke 通过，
compileall、`git diff --check`、canonical 导入边界也通过；3090 只做受控环境检查和低资源
验证，结束后保持空闲。当前在线网关的两次低预算复测失败证据已经保留；网关恢复后可重跑同一
命令，但不应扩大 V2.8 范围或启动长训练。

后续冻结 V2.8，进入 V2.9 的 Markdown/Overleaf-ready 报告工程化和有限 continuation；外部
Claude Code/Codex/OpenCode Harness 放到更后面。V2.8 冻结阶段只修复真实验收暴露的配置、预算、
重试、artifact handoff、兼容或报告事实问题，不新增 scheduler、任意 DAG、外部 Harness、
通用模板适配或新的顶层抽象。

Search/Read/Synthesis 的逐项状态见主计划 6.7：Search 的筛选、coverage、可选缓存和 Read 的
bounded screening/notes/snippets 已进入 canonical；旧多轮 follow-up、retrieval ledger 和
历史 artifact projection 仍是冻结兼容行为，待旧消费者退出后随 facade 删除。

V2.8 冻结阶段不新增 scheduler、任意 DAG、外部 Harness、通用论文模板适配或新的顶层抽象。
V2.9 再进入 Markdown/Overleaf 工程化与有限恢复设计。

## 7. 工作协议

- 先读主计划，再读代码；
- 任何“已完成”必须有代码、测试或持久化 artifact 证据；
- 修改前先说明 canonical owner、兼容策略和验证命令；
- 删除前完成生产引用、CLI、benchmark、历史 reader 和测试审计；
- 先跑聚焦测试，再跑 fixture，最后按风险决定真实 LLM/provider smoke；
- 保留失败原文和诊断，不用静默 fallback 把失败标成成功；
- 每个提交说明一个边界变化、行为影响和验证结果；
- 不把旧路径继续当作新功能的落点。

## 8. 常用检查命令

```powershell
uv run --no-sync python -m unittest discover -s tests
uv run --no-sync python -m compileall -q src tests examples
git diff --check
```

离线示例和已有能力示例见 `examples/README.md`。真实 provider/LLM 运行必须限制主题、预算、
输出目录和并发，并在结束后记录实际产物和失败边界。
