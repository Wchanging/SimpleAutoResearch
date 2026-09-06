# SimpleAutoResearch V2.8 新会话交接文档

> 文档职责：记录当前会话的真实状态和下一步入口，不维护第二套 V2.8 计划。
>
> **唯一施工计划**：`MDfiles/SIMPLE_AUTORESEARCH_V2_8_SYSTEM_EVOLUTION_PLAN.md`
>
> 状态快照：2026-09-06。当前 Git 基线：`21eac85`，已推送到
> `origin/feat/v2.8-system-evolution`；当前工作树已清洁。当前本地
> `.env` 使用 `gpt-5.4-mini` 和网关可用的 `chat` 模式。该文档和完整 AutoResearch 都不代表已经完成。

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
report/audit` 的真实 AutoDL 受控验收。真实网络 + `gpt-5.4-mini` 也已完成一次从检索、LLM
计划/阅读/综合、实验、分析到 report/audit 的完整落地。由此可以确认第一层“工程闭环”成立；
但 30–50 条检索、10–20 篇阅读、中等实验和完整学术结构 Markdown 报告的正常用户规模验收
仍在进行，不能把低预算 smoke 直接当作 V2.8 最终完成。

旧搜索、阅读、综合逻辑仍有冻结兼容实现，尚未完成最终删除；这属于 Phase 3 的兼容尾项，
不再阻塞 canonical 主线的规模验收，但删除前仍需按消费者矩阵补齐回归。

当前工作目标是一次有安全网的架构切换：新路径成为唯一演进方向，旧路径冻结为兼容入口，
旧实现中的成熟行为择优合并后，删除重复实现。

## 2. 当前 Git 和验证状态

- 分支：`feat/v2.8-system-evolution`；
- checkpoint：`21eac85 fix(research-code-task): preserve comparison artifact`；该提交之前已
  完成 CodeTask comparison handoff、报告确定性实验附录和 report audit 修复；
- 远端：已推送；
- 工作树：已清洁；canonical Read 接入和其回归测试均已推送；
- AutoDL 全量回归：`CUDA_VISIBLE_DEVICES='' uv run --no-sync python -m unittest discover -s tests -p 'test_*.py'`，588 项通过（27.358 秒）；本地最新 CodeTask/report 聚焦回归 14 项通过；
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
  -> PipelineRunner / pipeline_stages
  -> pipeline_stages/research.py（薄 alias） -> simple_ar/_legacy/research_stages.py
  -> Plan -> Search -> Read -> Synthesize -> Design -> Code -> Run -> Report
```

ARC-Bench、SurveyBench、历史 run reader 和部分旧 artifact projection 仍可能依赖它。旧入口不
再新增业务能力；迁移完成后只保留薄适配，或在有明确替代和回归证据后删除。

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
- 旧 `pipeline_stages/research.py` 的公开文件已收缩为 alias，冻结实现归档到
  `simple_ar/_legacy/research_stages.py`；旧 Search/Pipeline/Document Ingest 回归 31 项通过。

`simple_ar.cli` 已改为惰性导出，消除了 `python -m simple_ar.cli.main` 的重复加载 warning，同时保持
`from simple_ar.cli import main` 兼容。

删除的是无真实消费者的设计，不代表旧代码中所有成熟细节都被放弃。后续必须按主计划的
能力保留矩阵，将旧路径中更好的 fallback、缓存、筛选、修复、审计等行为合并到 canonical owner。
旧 `pipeline_stages.common` 中的通用 artifact/LLM helper 已迁入 `core.runtime`，该文件现在只
保留兼容别名和旧检索投影辅助。

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
- AutoDL 全量测试通过：`588 tests in 27.358s — OK`；本地最新 CodeTask/report 聚焦回归
  14 项通过，compileall 和 `git diff --check` 通过。

仍未证明：

- 在正常用户规模下稳定完成一次 30–50 条 raw、10–20 篇 bounded Read、中等实验和完整报告；
- 真实用户项目和真实 LLM 下 CodeTask 能稳定产生有效改进；当前证据证明的是隔离修改、验证、比较和“无提升时如实记录”，不证明自动得到更优指标；
- 完整的代码错误迭代、实验重跑、结果比较和报告更新在规模 session 中的稳定性；
- 多 provider 稳定性和任意主题上的研究质量；
- 完整 reviewer 修订循环；
- AutoDL/3090 上真实中等数据/模型实验的资源边界和复现性；
- publication-ready 论文、LaTeX 编译和模板自适应；
- 外部 Claude Code/Codex/OpenCode Harness 的成熟接入。

## 6. 当前下一步

当前工程闭环已完成，正在进入主计划 6.9 的正常用户规模验收；不再新增顶层能力：

1. 在 AutoDL 当前分支上用 `gpt-5.4-mini + chat`、一个准备好的真实数据/代码项目，执行
   30–50 条 raw、10–20 篇 bounded Read、一个中等但受控实验和完整 Markdown report；
2. 先以 `--no-report` 前缀确认 search/read/design/code-task/experiment/analysis 的资源和
   artifact，再在同一 session 上执行 report continuation；
3. 检查 raw/selected、paper notes、LLM attempts、实验命令、指标 provenance、报告章节、
   citation/metric/claim audit 以及 GPU/CPU/磁盘占用；失败时保留 session，不用 fixture 覆盖；
4. 规模 run 若暴露真实阻塞，只修对应的预算、重试、artifact handoff、配置或报告事实问题，
   补聚焦回归后再继续；通过后冻结 V2.8 主结构，再清理最后满足门槛的 legacy 重复实现；
5. 每次真实验收后同步主计划、本 handoff、长期愿景和中英文 changelog，并提交推送 checkpoint。

Search/Read/Synthesis 的逐项状态见主计划 6.7：Search 的筛选、coverage、可选缓存和 Read 的
bounded screening/notes/snippets 已进入 canonical；旧多轮 follow-up 和历史 artifact projection
仍是冻结兼容行为，尚未宣称已删除。

在正常用户规模验收通过前，不新增 scheduler、任意 DAG、外部 Harness、通用论文模板适配或
新的顶层抽象。V2.8 通过后才进入 V2.9 的 Markdown/Overleaf 工程化与有限恢复设计。

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
