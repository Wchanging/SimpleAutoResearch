# SimpleAutoResearch 长期愿景与架构演进总指引

> 本文档用于指导 SimpleAutoResearch 在 V2.2 之后的长期演进。它不是某个版本的详细任务清单，而是项目定位、能力差距、架构边界、路线顺序和取舍依据。
>
> 当前判断：V2.2 已经把 Code Workspace Engine 的底座跑通，但项目不能继续只沿着代码助手方向发展。更合理的长期拆分是三大块：**检索/证据、Coding/实验、论文/报告**。每一块都要吸收 AutoResearchClaw 和 ARIS 的经验，同时保留 SimpleAutoResearch 的核心风格：模块化、可审计、可复现、可逐步自动化。

## 1. 项目定位

SimpleAutoResearch 的目标不是“一键生成论文”，也不是“又一个通用代码修改助手”。

更合适的定位是：

**一个面向科研闭环的自动化工作台。**

它要把一个研究问题逐步变成：

```text
可检索的资料
-> 可审计的证据
-> 可实验的假设
-> 可运行的代码
-> 可比较的结果
-> 可追溯的 claim
-> 可发布/交流的报告或论文草稿
```

长期能力可以拆成三大块：

1. **检索 / 证据**
   - 搜什么；
   - 从哪里搜；
   - 如何读论文；
   - 如何抽取证据；
   - 如何做 gap analysis、idea generation、novelty check；
   - 如何形成可实验 hypothesis。

2. **Coding / 实验**
   - 如何理解已有代码；
   - 如何隔离环境；
   - 如何修改、复现、修复、消融；
   - 如何运行 benchmark；
   - 如何比较 baseline/patched/ablation；
   - 如何在必要时接入 Codex、Claude Code、MCP、Docker、远程机器。

3. **论文 / 报告**
   - 如何把文献和实验转成可读结构；
   - 如何生成 related work、method、experiment、result；
   - 如何做 claim audit、citation audit、metric audit；
   - 如何输出 Markdown、LaTeX、PDF 或更适合用户场景的报告。

核心原则：

**先证据，后结论；先可控，后自动；先可复现，后智能化。**

### 1.1 新阶段判断：本地轻量路径 + 外部强路径

经过 V2.3 的库替换和结构重构后，项目需要明确转向：

**SimpleAutoResearch 不应该和 Codex、Claude Code、MCP 生态、成熟 PDF parser、成熟 vector store 竞争，而应该拥抱它们，把自己定位成科研工作流、artifact contract、权限边界和审计层。**

长期上应保留两条路径：

| 路径 | 目标强度 | 适用场景 | SimpleAutoResearch 的职责 |
| --- | --- | --- | --- |
| 本地轻量路径 | 70-80% | 普通本机、教学、低成本开源用户 | 检索、缓存、轻量全文解析、SQLite/BM25、本地 cards、受控 code-task、报告审计 |
| 外部强路径 | 90-100% | 用户可用 Codex/Claude Code/MCP/服务器/GPU/强 parser | Tool/agent contract、workspace/env sandbox、权限审批、artifact 审计、结果回填 |

这不是放弃本地能力，而是重新分工：

- 本地必须可用、稳定、轻量，承担基础科研闭环；
- strong mode 不再意味着“我们自己造全部强能力”，而是通过 adapter 调用更强工具；
- 复杂代码修改、复杂文档解析、浏览器检索、向量检索、多 agent reviewer 可以交给外部工具；
- 但外部工具不能绕过 SimpleAutoResearch 的 workspace、permission、artifact、review、rollback 和 report audit；
- 沙盒、环境隔离、权限策略、artifact schema、review gate 仍然必须由我们自己掌握。

## 2. 当前状态与总体差距

### 2.1 当前已有基础

V1/V2/V2.1/V2.2 已经让项目有了一个能跑通的最小闭环：

- 8 阶段 pipeline：`plan -> search -> read -> synthesize -> design -> code -> run -> report`；
- arXiv / OpenAlex 等基础检索；
- research-only survey 的初步 prompt；
- artifact index / retrieval chunk；
- code-task 独立流程；
- embedded code-task 接入 8 阶段；
- workspace mode：`copy` / `git_worktree` / `sparse_copy`；
- repo map / context pack / locate；
- work plan / attempt / batch；
- controlled patch / edit budget / large edit approval；
- baseline/patched benchmark；
- medium review pipeline 示例；
- README、Usage、CLI、Workflow、Development 等开源文档。

这说明项目已经不是纯 demo，而是有了工程化底座。

### 2.2 离“可用开源工具”的差距

要成为稳定可用的开源工具，还缺：

- 检索侧：更可靠的多源检索、全文解析、证据卡片、引用追踪；
- Coding 侧：更好的环境隔离、依赖管理、真实 repo 复现、消融实验；
- 报告侧：living paper、claim/citation/metric audit，而不是最后总结式报告；
- 评测侧：AutoEval，用固定任务衡量每次改动是否真的变好；
- 交互侧：更顺手的 HITL review，减少用户翻 JSON 的成本；
- 工具侧：Tool/MCP adapter，避免所有外部能力都手写在主流程里。

### 2.3 离“企业级/产品级鲁棒”的差距

这里的“企业级”不是说马上做商业产品，而是指可靠、可观测、可审计、可恢复、可治理。

还缺：

- 明确 schema version 和 artifact migration；
- 完整日志、trace、cost、token、resource observability；
- 失败恢复与断点续跑；
- 环境与依赖的隔离、缓存、销毁策略；
- 权限模型：网络、文件、命令、secret、外部 agent；
- 安全边界不能依赖 prompt；
- 大项目 context / repo map 的分层检索；
- 多模型、多工具、多 agent 的路由策略；
- 自动评测集和回归测试；
- 更完整的报告审计和 provenance。

## 3. 参考项目的有效经验

### 3.1 AutoResearchClaw

值得吸收：

- 科研流程应当有多个质量门，而不是一条线跑完；
- 文献筛选、实验设计、最终报告都需要 gate；
- 实验失败后可以 pivot/refine，而不是只报错；
- coding agent 可以作为执行后端，但必须在 sandbox 中工作；
- claim/citation/quality gate 是科研系统的核心；
- co-pilot 模式比全自动更现实。

不直接照搬：

- 不照搬 20+ stage 的复杂度；
- 不把外部 agent 作为最终裁判；
- 不承诺“一键顶会论文”；
- 不让 pipeline 复杂到用户无法局部运行。

### 3.2 ARIS / Auto-claude-code-research-in-sleep

值得吸收：

- artifact contract 串 workflow；
- executor / reviewer 分离；
- idea discovery、novelty check、experiment bridge；
- experiment log、experiment audit、result-to-claim；
- paper claim audit、citation audit；
- research wiki / memory；
- effort、budget、human checkpoint 作为公共控制轴。

不直接照搬：

- 不把系统变成散落的一堆 skill；
- 不依赖外部 assistant runtime 作为唯一运行环境；
- 不急着做 rebuttal、poster、slides 等后期功能；
- 核心仍然由 Python 框架、CLI/config、artifact contract 保证可复现。

### 3.3 对 SimpleAutoResearch 的启发

AutoResearchClaw 更像“大科研工作流系统”，ARIS 更像“研究技能与 artifact 契约系统”。

SimpleAutoResearch 应该取中间路线：

- 比普通 DeepResearch 更强，因为能跑代码和实验；
- 比通用 coding agent 更科研，因为有文献、假设、报告审计；
- 比 AutoResearchClaw 更轻，因为阶段更少、模块更清晰；
- 比 ARIS 更工程化，因为核心流程由代码、配置、测试和 artifact 管理。

## 4. 三大块之一：检索 / 证据

### 4.1 目标

检索模块不能只做“搜几篇论文 metadata”。它应该逐步发展为 Research Evidence Engine。

长期目标：

```text
research goal
-> query plan
-> multi-source retrieval
-> paper/fulltext/code/dataset collection
-> paper cards
-> claim/method/dataset cards
-> gap analysis
-> idea candidates
-> novelty check
-> experiment contract
```

### 4.2 当前状态

已有：

- arXiv / OpenAlex 基础检索；
- rate limit fallback 和本地 cache 的雏形；
- `papers.jsonl`；
- 文献 note / synthesis prompt；
- report 中已有引用和 references 的初步能力；
- artifact chunk/index 作为后续检索基础。

不足：

- 主要还是 metadata 和摘要级信息；
- 全文解析能力弱；
- paper card / claim card / method card 还不成熟；
- citation trace 不够强；
- novelty check 还没有真正形成流程；
- research-only 更像 survey，不够像 evidence engine；
- Research Wiki / memory 还没有沉淀。

### 4.3 离可用版本还差什么

可用版本至少要做到：

- 研究问题拆解和 query expansion，避免用户给什么关键词就只搜什么；
- 多源检索：arXiv、OpenAlex、Semantic Scholar 或可扩展 adapter；
- 多轮检索循环：根据已发现证据补 query、查缺口、再检索；
- 去重和版本合并；
- 稳定缓存和 rate limit 策略；
- coverage check：判断子问题、方法、数据集、代码链接和实验线索是否覆盖足够；
- 论文结构化卡片；
- claim/method/dataset 抽取；
- evidence ledger 统一 schema；
- citation trace；
- gap analysis；
- idea candidates；
- lightweight novelty check；
- 输出 `experiment_contract.md/json`。

### 4.4 离企业级鲁棒还差什么

更高标准需要：

- 检索源质量评分；
- source provenance；
- schema version；
- 检索失败重试和降级策略；
- PDF/HTML/LaTeX 多格式解析；
- local BM25 / SQLite / later vector index；
- Research Wiki 跨 run 复用；
- citation audit；
- claim contradiction detection；
- retrieval AutoEval；
- tool trace 和 permission policy；
- 可插拔 MCP/tool adapter。

### 4.5 参考 AutoResearchClaw / ARIS 的实现方向

吸收 AutoResearchClaw：

- literature screen gate；
- gap analysis；
- hypothesis 形成；
- 多 agent/reviewer 审查 idea；
- claim verification。

吸收 ARIS：

- `IDEA_REPORT.md`；
- `EXPERIMENT_PLAN.md`；
- novelty check；
- research wiki；
- reviewer 不由 executor 自己担任。

SimpleAutoResearch 的实现方式：

- 不做重型多 agent 开局；
- 先做稳定 schema 和 artifact；
- 让 human gate 选择 hypothesis；
- 再把 hypothesis 转成 experiment contract。

### 4.6 近期建设目标

V2.3 应该重点推进这一块：

- `research_questions.json`
- `query_plan.json`
- `retrieval_rounds.jsonl`
- `screening_decisions.jsonl`
- `coverage_report.md`
- `research_contract.md`
- `paper_cards.jsonl`
- `claim_cards.jsonl`
- `method_cards.jsonl`
- `dataset_cards.jsonl`
- `code_links.jsonl`
- `gap_analysis.md`
- `idea_candidates.jsonl`
- `novelty_checks.jsonl`
- `experiment_contract.md/json`
- research-only evidence-aware report
- minimal Research Wiki
- retrieval / evidence AutoEval

验收标准：

- 用户给一个研究主题，系统能自动拆成子问题、扩展 query、进行多轮检索，并输出 evidence pack；
- 检索不被初始关键词锁死：系统能从检索结果和 gap 中补充术语与 follow-up query；
- 系统能说明哪些方向已经覆盖、哪些方向证据不足；
- report 中关键引用可追溯到 paper/claim card；
- 至少形成 2-3 个可实验 hypothesis；
- 用户能选择一个 hypothesis 生成 experiment contract；
- 这套输出能被后续 coding workflow 使用。

## 5. 三大块之二：Coding / 实验

### 5.1 目标

Coding 模块不能只是“让模型改代码”。它应该是实验执行与复现引擎。

长期目标：

```text
experiment contract / user task / paper repo
-> workspace isolation
-> environment setup
-> repo understanding
-> baseline reproduction
-> code/config modification
-> ablation matrix
-> benchmark run
-> failure diagnosis
-> repair loop
-> result comparison
-> experiment evidence
```

### 5.2 当前状态

V2.2 已经具备较强基础：

- code-task 独立命令；
- config-first + execute；
- workspace mode：`copy` / `git_worktree` / `sparse_copy`；
- environment probe；
- repo map / codebase index；
- locate / context pack；
- work plan / attempts / batches；
- patch plan review；
- controlled patch；
- edit budget / large edit approval；
- protected scope；
- 默认 edit scope；
- baseline/patched benchmark；
- repair proposal；
- medium review pipeline 示例；
- embedded code-task 接入 8 阶段。

不足：

- 环境隔离仍不够；
- edit scope 还主要是内置默认规则，缺少用户可配置的 allowed/protected patterns；
- 依赖安装和版本选择还弱；
- Docker / GPU / remote backend 未成型；
- 大型项目上下文仍可能爆炸；
- 外部 coding agent adapter 还只是规划；
- 多轮修改和 repair 还需要更多真实任务验证；
- ablation matrix 还弱；
- 从论文 repo 自动复现还没有打通；
- apply-back / PR 生成还没有。

### 5.3 离可用版本还差什么

可用版本至少要做到：

- 可配置 edit scope：用户能声明哪些目录可改、哪些文件只读；
- 项目级 venv 或 shared env cache；
- dependency setup plan；
- 明确 approval；
- baseline command 识别与建议；
- repo intake；
- benchmark stdout/stderr streaming；
- multi-file 修改稳定性；
- run matrix；
- ablation config；
- failure taxonomy；
- result comparison table；
- 可复用示例项目集。

### 5.4 离企业级鲁棒还差什么

更高标准需要：

- Docker backend；
- GPU/hardware profile；
- resource budget；
- command sandbox；
- network/file permission policy；
- secret read block；
- state machine / resume / rollback；
- distributed/remote execution；
- external agent isolated backend；
- reproducibility hash；
- run artifact retention policy；
- security review；
- code-task AutoEval；
- benchmark suite。

### 5.5 参考 AutoResearchClaw / ARIS 的实现方向

吸收 AutoResearchClaw：

- sandbox / remote / SSH / Colab / Slurm 这类执行后端抽象；
- code search 和 repo analysis；
- experiment execution loop；
- git branch / commit 作为实验版本管理；
- 外部 coding agent 作为强后端；
- 失败后 refine/pivot。

吸收 ARIS：

- experiment bridge；
- experiment log；
- experiment audit；
- ablation planner；
- result-to-claim；
- executor/reviewer 分离。

SimpleAutoResearch 的实现方式：

- 不急着让外部 agent 全权操作；
- 先把 workspace、env、benchmark、artifact、approval 稳住；
- 外部 agent 只能作为 editor backend；
- 所有 diff 必须回到本系统审计；
- 优先打通真实轻量 repo 的复现和消融。

### 5.6 近期建设目标

Coding/实验块在 V2.1/V2.2 已经打下了 code-task 基础，但还缺环境、外部工具和真实复现消融。考虑报告块仍停留在较早版本，Coding/实验块的后续强化从 V2.5 开始推进：V2.5 做受控环境与沙盒，V2.6 做外部工具 / Agent Harness，V2.7 做复现与消融。

V2.5：Managed Execution & Environment

- project venv；
- shared env cache；
- dependency setup plan；
- dependency install approval；
- Docker backend 最小版本；
- hardware profile；
- resource budget；
- environment failure diagnosis。

V2.6：Tool / Agent / MCP Harness

- Codex / Claude Code / OpenCode backend；
- MCP / Skills adapter；
- external editor permission policy；
- isolated workspace handoff；
- diff / artifact approval gate；
- tool trace；
- fallback local editor。

V2.7：Reproduction & Ablation Engine

- curated mini repo suite；
- repo intake；
- baseline command 建议；
- reproduction contract；
- ablation plan；
- run matrix；
- multi-seed / multi-config；
- comparison table；
- experiment report。

验收标准：

- 不污染 SimpleAutoResearch 自己的 `.venv`；
- 至少 3 个真实小型项目可跑通；
- 至少一个项目能自动设计并运行 ablation；
- 失败时能区分环境、数据、配置、代码、指标问题；
- comparison table 能进入报告。

## 6. 三大块之三：论文 / 报告

### 6.1 目标

报告模块不能只是最后一步总结。它应该是 Report & Audit Engine。

长期目标：

```text
paper cards + claim cards
-> related work
experiment contract
-> method / setup
run results
-> result tables
comparison / audit
-> supported claims
-> report / paper draft
-> citation / metric / claim audit
```

### 6.2 当前状态

已有：

- `report.md`；
- `references.bib`；
- report prompt 优化过；
- research-only survey 风格初步支持；
- code-task summary；
- baseline/patched 指标对比；
- README/USAGE/WORKFLOWS 中说明了能力边界。

不足：

- report 仍偏末端汇总；
- related work、method、experiments、results 还没有作为 living sections 逐步形成；
- claim audit 弱；
- metric audit 弱；
- citation audit 弱；
- 表格/图表与源文件绑定弱；
- LaTeX/PDF 导出弱；
- 不同报告类型和模板不成熟；
- reviewer 角色不够明确。

### 6.3 离可用版本还差什么

可用版本至少要做到：

- living paper sections；
- result-to-claim map；
- citation trace；
- metric trace；
- claim strength 标记；
- report quality check；
- research-only survey 和 experiment report 分开模板；
- report 中清楚区分 evidence、interpretation、limitation；
- 支持用户在最终报告前审查核心 claim。

### 6.4 离企业级鲁棒还差什么

更高标准需要：

- citation audit；
- metric audit；
- contradiction check；
- unsupported claim detection；
- template system；
- Markdown -> LaTeX/PDF；
- venue/style adaptation；
- figure/table source binding；
- reviewer model / external reviewer；
- report regression eval；
- plagiarism / novelty 风险提示；
- traceable provenance。

### 6.5 参考 AutoResearchClaw / ARIS 的实现方向

吸收 AutoResearchClaw：

- finalization gate；
- quality gate；
- paper writing stages；
- citation integrity；
- claim verification。

吸收 ARIS：

- paper plan；
- paper write；
- result-to-claim；
- paper claim audit；
- citation audit；
- experiment audit；
- reviewer/executor 分离。

SimpleAutoResearch 的实现方式：

- 先做 Markdown living paper；
- 先把引用、数字、claim 追溯做稳；
- 再做 LaTeX/PDF；
- 不急着生成完整正式论文；
- 报告必须显式展示限制和未验证 claim。

### 6.6 近期建设目标

报告不能等到很后面才升级。V2.3 把 evidence pack 做稳之后，V2.4 应优先强化本体报告能力，让本地路径至少达到 70-80% 的科研报告水准，再去推进更复杂的环境和外部 agent。

V2.4：Report & Audit Engine

- `report_sections/related_work.md`
- `report_sections/method.md`
- `report_sections/experiments.md`
- `report_sections/results.md`
- `result_to_claim.json`
- `claim_audit.json`
- `citation_audit.json`
- `metric_audit.json`
- Markdown -> LaTeX/PDF 最小导出。

V2.8：Research-to-Code Integrated Writing

- experiment contract 进入 code workflow；
- results 回填 claim；
- Literature Rescue；
- report sections 从中间阶段逐步生成；
- 报告能同时引用 evidence pack 和 experiment artifacts。

验收标准：

- 报告关键数字能追溯到实验结果；
- citation 能追溯到文献证据；
- unsupported claim 会被标记、降级或删除；
- reproduction/ablation report 能生成清晰对比表；
- 用户能审查最终 claim。

## 7. 项目代码结构演进

长期上，SimpleAutoResearch 不能让功能随着版本增长散落在一堆顶层 Python 文件里。

当前结构已经有一些值得保留的边界：

```text
src/simple_ar/
  code_task/      # V2.1/V2.2 已经相对成型
  literature/     # arXiv/OpenAlex/BibTeX 等文献源能力
  retrieval/      # artifact chunk/index/evidence 的早期能力
  experiment/     # 旧 experiment/template/demo 逻辑，后续应收敛
  pipeline.py
  stage_handlers.py
  reporting.py
  prompts.py
  cli.py
```

主要问题：

- `stage_handlers.py` 承担了过多跨阶段业务逻辑；
- `literature/` 更像 source client，不应承载完整 Research Evidence Engine；
- `retrieval/` 同时服务 artifact search 和未来 research index，边界不清；
- `reporting.py` 是报告入口，但未来 claim/citation/metric audit 应形成独立模块；
- `experiment/` 中旧模板实验和新的 code-task/reproduction 能力存在重叠；
- `prompts.py` 会越来越大，需要按领域拆分。

长期目标结构应接近：

```text
src/simple_ar/
  app/
    cli.py
    commands/
    config.py

  core/
    artifacts.py
    contracts.py
    pipeline.py
    stages.py
    policy.py
    budgets.py
    logging.py

  research/
    contracts.py
    sources.py
    connectors/
      arxiv.py
      openalex.py
      local_files.py
      semantic_scholar.py
    documents.py
    cache.py
    extractors.py
    chunking.py
    index.py
    evidence.py
    cards.py
    ideas.py
    novelty.py
    experiment_contract.py
    review.py
    wiki.py
    prompts.py

  code_task/
    workspace.py
    repo_map.py
    context.py
    work_plan.py
    attempts.py
    editor.py
    patching.py
    runner.py
    comparison.py
    repair.py
    summary.py

  execution/
    environment.py
    managed_env.py
    docker_backend.py
    resources.py
    commands.py

  reproduction/
    intake.py
    baseline.py
    ablation.py
    run_matrix.py
    reports.py

  report/
    sections.py
    assemble.py
    claim_audit.py
    citation_audit.py
    metric_audit.py
    latex.py
    prompts.py

  tools/
    adapters.py
    mcp.py
    permissions.py
    trace.py

  eval/
    research_eval.py
    code_task_eval.py
    report_eval.py
    suites.py
```

这不是要求一次性大迁移，而是给后续版本提供稳定方向。

### 7.1 V2.3 应落实的结构调整

V2.3 不应只新增功能，还应该把检索/证据相关代码正式收敛到 `research/` 包中。

应优先完成：

- 新建 `src/simple_ar/research/`；
- 把 source connector、document store、extractor、index、cards、ideas、novelty、experiment contract 放入该包；
- 现有 `literature/` 可在 Day1/Day2 先作为 connector 的底层实现被复用，但 V2.3 内应完成迁移到 `research/connectors/`、`research/cache.py` 或相关 research 模块；旧 `literature/` 只保留兼容 wrapper；
- 现有 `retrieval/` 中与 artifact chunk/index 相关的能力保留，但 research 文档索引应迁移到 `research/index.py`；
- `stage_handlers.py` 只做编排调用，不再放大量 evidence/carding/index 业务逻辑；
- research prompt 应在 V2.3 内拆到 `research/prompts.py`；顶层 `prompts.py` 只保留兼容 re-export；
- 新增的 schema 和 artifact 写入逻辑不应散落在多个 stage handler 中。

### 7.1.1 `stage_handlers.py` 治理方案

`stage_handlers.py` 仍然是当前最明显的结构风险。短期可以允许它保留“8 阶段入口”，但不能继续承载具体业务实现。

目标边界：

- `stage_handlers.py`：只负责读取阶段输入、调用领域 workflow、写少量阶段级 metadata；
- `research/workflow.py`：负责 `02-search` 的 research plan、retrieval rounds、screening、coverage、document/index/cards 写入；
- `research/artifacts.py`：只放 artifact path、artifact bundle 写入、schema 组装；
- `research/connectors/`：只做 provider/local source adapter；
- `research/cards.py`、`research/ideas.py`、`research/experiment_contract.py`：负责证据卡片、idea、experiment contract；
- `report/` 或 `reporting/` 后续负责 report assembly 与 audit，不再把报告逻辑压在 `stage_handlers.py`。

治理节奏：

| 阶段 | 目标 | 操作 |
| --- | --- | --- |
| V2.3 Day5.5-Day7 | 收敛 search artifacts | 已将 planning 合并为 `research_plan.json`，并把 documents/chunks/index 写入逻辑迁入 `research/` |
| V2.3 Day8-Day10 | 收敛 cards/gap | 新增 `research/cards.py`、`research/gaps.py`，`stage_handlers.py` 只调用 artifact bundle |
| V2.3 Day11-Day14 | 收敛 idea/contract/report evidence | 新增 `research/ideas.py`、`research/novelty.py`、`research/experiment_contract.py` |
| V2.3 Release 前 | Search workflow 外迁 | 把 `_live_literature_search`、`_collect_retrieval_round`、`_search_*_once` 迁入 `research/workflow.py` |
| V2.4 | Report 拆包 | 建立 `report/` 或强化 `reporting/` 分层，stage handler 不再承载报告组装和审计逻辑 |
| V2.5+ | Execution/reproduction/tools 拆包 | `execution/`、`tools/`、`reproduction/`、`bridge/` 逐步落地，stage handler 只保留 orchestration |

治理原则：

- 每次拆分都必须保持测试可过，不做一次性大重写；
- 先迁移新增能力，再迁移旧逻辑；
- 每个新模块必须有清晰输入/输出 artifact；
- 不为了“目录好看”拆分，只有当拆分能降低后续修改成本时才做；
- `stage_handlers.py` 每新增一个大功能，都应优先考虑放入领域 workflow。

### 7.2 后续版本结构调整

V2.4 应逐步建立 `report/`：

- living paper sections；
- result-to-claim；
- claim audit；
- citation audit；
- metric audit；
- report templates；
- quality review。

V2.5 应逐步建立 `execution/`：

- managed env；
- Docker backend；
- resource profile；
- setup plan；
- dependency policy。

V2.6 应逐步建立 `tools/` 或 `agent_backends/`：

- ToolAdapter；
- MCP / Skills adapter；
- external editor backend；
- permission policy；
- tool trace；
- fallback policy。

V2.7 应逐步建立 `reproduction/`：

- repo intake；
- baseline reproduction；
- ablation planner；
- run matrix；
- experiment report。

V2.8 应逐步建立 `bridge/` 或 `research_to_code/`：

- hypothesis to experiment contract；
- evidence to task file；
- code task handoff；
- result feedback；
- literature rescue；
- result-to-report feedback。

原则：

- 不为目录漂亮而重构；
- 但每个新阶段必须减少旧的混杂逻辑；
- 新能力默认进入对应 domain package；
- 旧接口可以通过 wrapper 保持兼容；
- 每次迁移都要有测试覆盖和文档说明。

## 8. 横向基础设施

三大块之外，还需要一组横向能力支撑。

### 8.1 Artifact Store

要求：

- schema version；
- provenance；
- lifecycle；
- resume；
- debug；
- report evidence；
- AutoEval input。

原则：

**artifact 少而有用。**

如果某个文件不能用于 resume、debug、审查、报告、模型输入、成本追踪或评测，就不要默认生成。

### 8.2 AutoEval

AutoEval 是项目能否长期进化的关键。

需要覆盖：

- retrieval quality；
- evidence extraction；
- citation trace；
- context pack hit rate；
- patch apply；
- benchmark improvement；
- repair success；
- protected scope violation；
- report claim consistency；
- token/runtime/cost。

验证层级：

- quick：开发时快速检查；
- standard：常规提交前；
- full：版本发布前；
- realistic：定期 LLM + 真实示例项目。

### 8.3 HITL Review UX

用户不能一直手翻 JSON。

需要：

- plan summary；
- evidence summary；
- diff summary；
- risk summary；
- approve/reject/refine；
- decision artifact；
- 未来 TUI/GUI approval inbox。

### 8.4 Tool / MCP / Agent Adapter

早期支持只读，后期支持完整能力。

需要：

- ToolAdapter；
- permission policy；
- tool trace；
- network approval；
- write approval；
- external agent sandbox；
- fallback backend。

原则：

**工具可以增强能力，但不能绕过审计。**

## 9. 调整后的路线图

这一路线图回归三大块，但保持版本节奏。

```text
V2.3：检索/证据与 Tool-ready Contract
  Local Evidence Engine / Evidence Pack / Experiment Contract / Read-only Tool-MCP Foundation

V2.4：论文/报告增强
  Report & Audit Engine / Living Sections / Claim-Citation-Metric Audit

V2.5：受控执行环境与沙盒
  Managed Execution & Environment / Permission Policy / Resource Profile

V2.6：外部工具与 Agent Harness
  Codex / Claude Code / OpenCode / MCP / Skills Adapter, with SimpleAR audit gates

V2.7：复现与消融实验
  Reproduction & Ablation Engine using local or external editor backend

V2.8：检索、Coding 与报告闭环
  Research-to-Code Bridge / Literature Rescue / Result-to-Claim

V2.9+：产品化交互
  TUI / GUI / Collaboration Workspace
```

### V2.3：Local Evidence Engine / Evidence Pack / Experiment Contract / Read-only Tool-MCP Foundation

主线：检索/证据。

目标：

- research-only 不再只是摘要；
- 支持 topic -> research questions -> query plan -> retrieval rounds -> coverage check 的完整调研循环；
- 支持自动 query expansion 和 follow-up search，避免被用户初始关键词限制；
- 建立可扩展 source connector；
- 建立 document store / cache / provenance；
- 支持 metadata、abstract、本地文档和 basic PDF/text ingestion；
- 建立本地 searchable index，至少支持 keyword/BM25/SQLite FTS 这类轻量但可用的检索；
- 预留 embedding/vector/parser/tool adapter，但不绑定具体重型后端；
- 文献检索形成 evidence pack；
- evidence pack 能解释筛选、去重、排序和覆盖度；
- evidence 能生成保守 hypothesis / idea candidates；
- hypothesis 能生成 experiment contract，并可转成给 code-task 或外部 coding agent 的 `tool_context`；
- AutoEval 能衡量检索、文档摄取、证据抽取和报告引用质量；
- HITL review 更顺手；
- Tool/MCP 有只读接入基础；
- 不再把 strong mode 的所有能力压成本地自研，而是把外部强工具纳入受控 backend。
- 同步补齐 code-task 的正式 `[edit_scope]` 配置，因为 V2.3 开始要把 evidence/experiment contract 交给 coding workflow，用户必须能声明哪些路径可改、哪些路径只读。

重点产物：

- `source_plan.json`
- `research_questions.json`
- `query_plan.json`
- `retrieval_rounds.jsonl`
- `screening_decisions.jsonl`
- `coverage_report.md`
- `documents.jsonl`
- `cache_manifest.json`
- `chunks.jsonl`
- `research_index/`
- `paper_cards.jsonl`
- `claim_cards.jsonl`
- `method_cards.jsonl`
- `dataset_cards.jsonl`
- `evidence_pack.json/md`
- `gap_summary.md`
- `idea_candidates.jsonl`
- `novelty_checks.jsonl`
- `experiment_contract.md/json`
- `tool_context.md/json`
- `research_wiki/`
- `eval_report.md`
- `tool_trace.jsonl`
- `manifest.json.edit_scope`

能力模式：

- `lite`：metadata + abstract + cards + keyword/BM25 检索，适合低成本 survey；
- `standard`：metadata + 本地文档/basic PDF/text extraction + chunks + SQLite/BM25/FTS，适合正常研究任务；
- `strong`：standard 本地路径 + 外部 parser/vector/tool/MCP/Codex/Claude backend，适合更大项目和服务器/强工具可用场景。

V2.3 的重点不是继续本地堆轮子，而是把 evidence pack、experiment contract、tool context、permission/review/audit 这些强工具也必须遵守的契约做稳。lite/standard 是本地可运行路径，strong 是 adapter path。

### V2.4：Report & Audit Engine

主线：论文/报告。

目标：

- 把 V1 式“末端总结”升级为 evidence-aware living report；
- 从 `evidence_pack`、paper cards、claim cards、coverage report 生成 `related_work`；
- 从 `experiment_contract` 和 code-task/reproduction 结果生成 `method`、`experiments`、`results`；
- 建立 result-to-claim、citation trace、metric trace；
- unsupported claim 会被标记、降级或删除；
- research-only survey 和 experiment report 分开模板；
- Markdown 报告结构更接近科研论文；
- 保留 LaTeX/PDF adapter 预留，但 V2.4 不强行追求复杂排版。

重点产物：

- `report_sections/related_work.md`
- `report_sections/method.md`
- `report_sections/experiments.md`
- `report_sections/results.md`
- `result_to_claim.json`
- `citation_audit.json`
- `metric_audit.json`
- `claim_audit.json`
- `report_quality.md/json`

### V2.5：Managed Execution & Environment

主线：Coding/实验基础设施。

目标：

- 不污染用户环境；
- 支持 project venv / shared env cache；
- 支持 Docker 最小后端；
- 依赖安装可审批、可记录、可复现；
- 硬件和资源可探测；
- 失败能定位到环境层。

### V2.6：Tool / Agent / MCP Harness

横向增强。

目标：

- MCP adapter；
- Codex / Claude Code / OpenCode backend；
- Skills/tool registry；
- complexity router：简单任务走本地，复杂任务走外部强 backend；
- executor/reviewer 分离；
- external agent isolated workspace；
- tool permission / risk policy；
- 完整 trace、fallback、diff review 和 artifact 回填。

原则：外部 agent 可以更强，但不能绕过 SimpleAutoResearch 的 sandbox、permission、artifact、approval 和 report audit。

### V2.7：Benchmark Adaptation & Result Analysis Engine

主线：Coding/实验、benchmark 适配、结果分析。

目标：

- 以 ARC-Bench 为第一批真实外部验收目标，但不把 ARC-Bench 专用逻辑写入核心源码；
- 建立 `benchmark/<suite>/` 热插拔 adapter 边界，支持 prepare、finalize、analyze、judge 四类动作；
- 建立通用 result analysis layer，将 metrics、logs、rubric、claims 和 limitations 结构化；
- 让 code-task / greenfield / external agent 的运行结果能够被统一解释、审计和回填到 memory / repair；
- 支持 benchmark failure diagnosis，使下一轮修复看到完整失败上下文，而不是只看到最后一行 stderr；
- 在保持轻量边界的前提下，为后续 reproduction、ablation matrix、multi-seed run matrix 和 cross-run comparison 打底。

这是项目从“能生成/修改代码”走向“能被外部 benchmark 持续检验和改进”的关键阶段。V2.7 的重点不是刷单个 benchmark，而是把可迁移的结果分析、claim grounding、adapter boundary 和 judge feedback loop 做稳。

### V2.8：Research-to-Code Bridge

主线：检索、Coding 与报告的连接。

目标：

- 从 evidence/hypothesis 生成 experiment contract；
- 从 paper cards 提取 dataset、metric、baseline、repo link；
- 判断使用已有 repo、复现 repo，还是从零实现最小实验；
- 将结果回填 claim/report；
- 支持 Literature Rescue。

### V2.9+：TUI / GUI / Collaboration Workspace

产品化增强。

目标：

- artifact browser；
- approval inbox；
- diff viewer；
- run dashboard；
- research wiki browser；
- workflow editor；
- project memory。

## 10. 短中期产品突破口

不要用“自动写顶会论文”作为早期宣传和验收标准。

更实际的突破口是：

### 10.1 Research Evidence Pack

用户输入一个研究方向，系统输出：

- 领域脉络；
- paper cards；
- claim cards；
- gap analysis；
- idea candidates；
- novelty 风险；
- 可实验 hypothesis；
- experiment contract。

### 10.2 Reproduction Assistant

用户输入一个 repo 或论文代码，系统输出：

- repo 结构理解；
- 环境计划；
- baseline command；
- baseline run；
- failure diagnosis；
- 修复建议；
- reproduction report。

### 10.3 Ablation Assistant

用户输入一个 baseline 和假设，系统输出：

- ablation plan；
- code/config patch；
- run matrix；
- comparison table；
- result-to-claim；
- report section。

### 10.4 Living Research Report

用户完成调研和实验后，系统输出：

- related work；
- method；
- experiments；
- results；
- limitation；
- claim/citation/metric audit；
- final report。

## 11. 设计原则

### 11.1 三大块都要强，不能偏科

只强检索，会变成 DeepResearch。

只强 Coding，会变成 Aider/SWE-agent。

只强报告，会变成论文润色器。

SimpleAutoResearch 必须强在三者连接：

```text
证据提出假设
代码验证假设
报告审计结论
```

### 11.2 系统边界不能依赖 prompt

必须由代码兜底：

- path safety；
- edit scope；
- secret read block；
- artifact schema；
- timeout；
- dependency approval；
- network approval；
- benchmark/test protection；
- report audit；
- tool permission。

### 11.3 Human-in-the-loop 是能力，不是缺陷

科研自动化不是无人驾驶。

系统应该做繁琐工作，用户在关键节点决策。

### 11.4 AutoEval 是护城河

没有评测，项目会退化成 prompt 调参。

每个版本都要能回答：

- 检索是否更准？
- evidence 是否更可追溯？
- coding 是否更稳定？
- repair 是否更有效？
- 报告 claim 是否更可信？
- 成本是否可控？

### 11.5 先真实小任务，再大任务

发展顺序：

1. 小型 Python 项目；
2. 中等 Python repo；
3. 真实轻量 ML repo；
4. 论文 repo 复现；
5. 消融实验矩阵；
6. 文献到实验契约；
7. 文献到代码实现；
8. 多环境、多资源实验；
9. TUI/GUI 协作。

### 11.6 保持可重构

如果某个模块方向不对，可以推翻重写。

但要尽量保留：

- artifact contract；
- 用户可理解的 workflow；
- 文档说明；
- 迁移路径；
- 测试和 eval。

## 12. 最终判断

SimpleAutoResearch 最终有价值的形态是：

**一个能把研究问题变成证据、实验、结果和报告的科研工作台。**

不是单纯调研工具，不是单纯代码工具，也不是单纯报告工具。

下一阶段应该回归三大块建设：

- V2.3 把检索/证据做成可交接的 Evidence Pack 和 Tool-ready Contract；
- V2.4 把论文/报告从 V1 式末端汇总升级为 living report 与 claim/citation/metric audit；
- V2.5 把环境隔离、沙盒、资源画像和执行权限做稳；
- V2.6 接入 Codex / Claude Code / MCP / Skills 等外部强工具，但全部经过 SimpleAutoResearch 的权限、artifact、diff 和审计边界；
- V2.7 把复现与消融实验做成可靠产品场景；
- V2.8 把检索、Coding 和报告接起来，让 evidence-backed hypothesis 能变成 experiment contract、code task 和可审计结论。

这条路线更接近 AutoResearchClaw 的科研工作台方向，也保留 ARIS 的 artifact contract 优点，同时不会让 SimpleAutoResearch 过早变成大而空的黑箱 agent。
