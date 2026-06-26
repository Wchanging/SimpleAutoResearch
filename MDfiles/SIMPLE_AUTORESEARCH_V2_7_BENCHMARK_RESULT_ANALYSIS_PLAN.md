# SimpleAutoResearch V2.7 Benchmark Adaptation & Result Analysis 计划

> V2.7 的目标不是把 SimpleAutoResearch 变成某个 benchmark 的专用提交脚本，也不是在核心流程里继续堆叠更多硬编码评测逻辑。
>
> V2.7 要做的是：在 V2.6 已经形成的 code-task、greenfield、external agent、memory、review、repair 和 tool/MCP 边界之上，建立一套 **benchmark-ready 的实验结果分析与适配层**。ARC-Bench 是第一批重点目标，但实现方式必须能自然扩展到未来的 ML、coding、reproduction、scientific workflow 等不同 benchmark。
>
> 核心原则继续沿用 V2.6：**轻量高效、鲁棒可用、结构清晰、运行稳定、维护轻松、拓展简单、开源好用**。

## 1. V2.7 定位

V2.3 到 V2.6 已经分别补强了检索证据、报告审计、实验代码可靠性、工具调用与外部 agent 协作。现在最需要解决的问题是：系统如何在真实 benchmark 中被稳定评估，并把评测结果反向用于 code-task、repair、report 和长期演进。

V2.7 的主线是 **Benchmark Adaptation + Result Analysis + Feedback Loop**：

```text
code-task / 8-stage pipeline / external agent handoff
-> generated or patched project
-> validation / benchmark run
-> generic result analysis
-> benchmark adapter projection
-> external judge or benchmark scorer
-> feedback to memory / repair / report
```

换句话说，V2.7 不是新增一个“ARC-Bench 模式”，而是把“如何解释一次实验结果、如何形成可审计结论、如何投影到不同 benchmark 的提交格式”变成稳定能力。

## 2. 不做什么

V2.7 明确不做以下事情：

- 不把 ARC-Bench 的 rubric、目录结构、字段名硬编码进 `src/simple_ar`；
- 不把 benchmark adapter 当作主项目的默认运行路径；
- 不让报告生成阶段承担 benchmark 结果分析的全部职责；
- 不默认要求完整 8 阶段流程才能运行 benchmark；
- 不为了刷某个 benchmark 写任务专用 prompt、fallback 文件或固定文件名优先级；
- 不把 benchmark 输出包装成漂亮文本后就当作可靠分析；
- 不在缺少证据时让 LLM 编造 claim、结论或指标解释。

## 3. 设计原则

### 3.1 Adapter 在外，通用能力在内

Benchmark adapter 应优先放在 `benchmark/<suite>/` 这类与 `src/` 平级的位置，默认进入 `.gitignore`。它负责读取外部 benchmark 的任务、rubric、提交格式和 judge 工具，并把 SimpleAutoResearch 的产物转换成该 benchmark 需要的结构。

只有当某个能力对多个 benchmark、code-task 和 8 阶段流程都通用时，才进入核心包。例如：metric table 归一化、claim grounding、result audit、artifact reference、run failure diagnosis。

### 3.2 先分析结果，再适配格式

ARC-Bench、PaperBench、Kaggle-like benchmark 或未来自建 benchmark 的提交格式都不一样，但它们共同需要回答：

- 任务做了什么；
- 运行是否成功；
- 输出了哪些指标；
- 指标是否覆盖任务要求；
- 结果是否支持声明；
- 哪些失败、缺口或风险还存在；
- 是否存在 benchmark-specific shortcut 或无效实现。

因此 V2.7 应先生成通用的 result analysis artifact，再由 adapter 投影成 benchmark-specific submission。

### 3.3 数值结论确定性计算，解释交给 Agent

数值聚合、metric direction、seed coverage、dataset coverage、best/worst case、missing cell、runtime/resource 等应由规则代码确定性计算。LLM 负责解释这些结果、归纳失败模式、写 claim 和审查报告，但不能直接创造指标。

### 3.4 Benchmark 不应污染正常研究报告

研究报告、survey、实验报告和 benchmark analysis 是相关但不同的文本任务。V2.7 不应让 ARC-Bench 的短声明、rubric verdict 或 judge prompt 影响正常的 `08-report` survey/report 生成。

可行边界是：

- `07-run` 产出 run artifacts；
- result analysis 读取 run artifacts，产出 analysis artifacts；
- `08-report` 可以选择消费 analysis artifacts；
- benchmark adapter 可以单独消费 analysis artifacts 并生成提交文件。

### 3.5 Feedback Loop 必须可追踪

Benchmark failure 不应只是终端里的一段 stderr。系统应把失败分析写入 memory、repair context 和 audit artifact，使下一轮修复知道：

- 哪个命令失败；
- 失败类型是语法、运行时、结果 schema、metric 缺失、质量不足，还是 benchmark judge 不通过；
- 哪些文件/模块最可能相关；
- 上一轮修复已经尝试了什么；
- 哪些假设不能再重复。

这条要求应作为 SimpleAutoResearch code-task / repair 的通用增强，而不是 ARC-Bench 专用逻辑。每轮修复后，系统都应形成一份轻量的 previous repair context，至少包含：

- 本轮失败诊断：错误类型、关键 stderr、失败命令、失败阶段、最可能的根因；
- 本轮修改摘要：修改了哪些文件、涉及哪些关键函数/接口、修复策略是什么；
- 修复后证据：静态检查、运行命令、返回码、指标变化、schema/guard 变化；
- 重复失败判断：如果错误信息相同或高度相似，需要明确记录“上一轮定位假设可能不成立”，避免下一轮继续改同一处；
- 下一轮建议：建议优先查看的文件、需要验证的数据流/调用链、不要重复尝试的方向。

这样 repair prompt 看到的不是孤立的最后一行 stderr，而是“刚刚改过哪里、为什么仍失败、哪些判断已经被证伪”。这能减少模型在多轮修复中反复猜同一个位置的问题，也能让 greenfield 与已有代码任务共享同一套调试记忆机制。

## 4. 目标架构

```text
Task / Contract / Rubric
        |
        v
Implementation Engine
  - existing-code patch
  - greenfield generation
  - external agent handoff
        |
        v
Validation / Benchmark Runner
  - static validation
  - smoke test
  - benchmark command
  - result schema guard
        |
        v
Generic Result Analysis
  - normalize metrics
  - build evidence table
  - generate claims
  - audit grounding
  - write analysis report
        |
        v
Benchmark Adapter
  - ARC-Bench projection
  - future benchmark projection
  - judge command wrapper
  - submission manifest
        |
        v
Feedback
  - memory update
  - repair plan
  - report context
  - comparison dashboard
```

核心包只应该知道“通用实验结果分析”是什么，不应该知道某个外部 benchmark 的所有目录细节。

## 5. Generic Result Analysis Layer

### 5.1 输入

通用结果分析层应该接受以下信息：

- 任务描述：用户 task、experiment contract、benchmark rubric 或 acceptance criteria；
- 运行结果：stdout/stderr、return code、timeout、resource usage；
- 结构化指标：`metrics.json`、per-dataset/per-seed/per-condition records、summary tables；
- 生成/修改摘要：实现策略、关键文件、依赖、入口命令；
- 审查结果：static validation、LLM review、post-run review、guard verdict；
- 可选证据：README、实验日志、plots、tables、submission files。

### 5.2 输出

建议产物：

```text
result_analysis/
  analysis_context.json      # 归一化后的任务、指标、证据、运行状态
  metric_summary.json        # 确定性指标聚合与覆盖情况
  claims.json                # claim/verdict/evidence/limitations/confidence
  analysis_report.md         # 面向用户或 benchmark 的解释性报告
  analysis_audit.json        # grounding、缺失项、矛盾、风险
```

### 5.3 Claim 模型

每条 claim 至少包含：

- `claim_id`：稳定 ID；
- `claim`：结论文本；
- `verdict`：supported / partially_supported / unsupported / not_evaluated；
- `evidence_refs`：引用到 metric、log、artifact 或 task criterion；
- `metric_refs`：关联指标及其方向；
- `limitations`：适用范围和缺失证据；
- `confidence`：high / medium / low；
- `required_follow_up`：如果不能支持结论，需要补什么实验或修什么输出。

这样做的目的不是让输出更复杂，而是避免“写了一段像结论的文字，但无法回溯到任何实际结果”。

### 5.4 确定性分析

规则代码应负责：

- 检查 metrics 是否存在、是否非空；
- 检查 metric direction 是否可解释；
- 统计 dataset / seed / condition 覆盖率；
- 识别全 0、全 NaN、全空表、单 seed 冒充多 seed；
- 识别 benchmark command 成功但结果文件缺失；
- 比较 baseline / patched / generated project 的指标；
- 标记 runtime/resource 指标不参与“越大越好/越小越好”的简单排序。

LLM 只在这些结构化事实之上进行解释。

### 5.5 LLM Analyzer 与 Reviewer

Analyzer 负责：

- 根据任务和 rubric 生成 claim；
- 解释指标支持或不支持哪些结论；
- 总结失败模式和后续实验建议；
- 生成面向 benchmark 的 concise writeup。

Reviewer 负责：

- 查找 claim 与 metrics 的不一致；
- 查找没有证据支撑的结论；
- 查找 task/rubric 中没有覆盖的要求；
- 查找可能的 shortcut、hard-code、leakage、过拟合；
- 要求 analyzer 降级或删除不可靠 claim。

Analyzer 和 Reviewer 都可以使用模板，但模板必须服务于任务类型，而不是服务于某个固定文件结构。

## 6. Benchmark Adapter Boundary

### 6.1 Adapter 负责什么

每个 benchmark adapter 负责：

- 读取 benchmark 原始任务、rubric、输入数据和 judge 配置；
- 生成 SimpleAutoResearch 可运行的 `task.md` 和 `code_task.toml`；
- 运行或提示用户运行 code-task；
- 从 run artifacts 中提取 submission 所需文件；
- 调用通用 result analysis 或 adapter-local analyzer；
- 生成 benchmark-specific submission；
- 可选地调用 benchmark judge；
- 保存 adapter manifest，记录来源、命令、版本、路径和限制。

### 6.2 Core 负责什么

核心包负责：

- 统一 code-task execution；
- workspace / worktree / output root / memory；
- LLM retry、review、repair；
- result schema guard；
- 通用 result analysis；
- tool/MCP schema；
- artifact contract。

如果某段代码只知道 ARC-Bench 的 `stage-14`、`claims.json` 或某个 rubric 字段，它就不应该进入核心包。

## 7. ARC-Bench 适配方向

ARC-Bench 是 V2.7 的第一批重点验收对象。它的价值在于任务足够真实，且评分不仅看代码是否运行，还看结果分析是否可信。

### 7.1 短期实现

短期保持在 `benchmark/arc_bench/` 下独立实现：

```text
benchmark/arc_bench/
  adapter.py
  config.example.toml
  templates/
    result_analysis.md
    claim_review.md
  prepared/
  runs/
  submissions/
```

adapter 可以调用 SimpleAutoResearch 的通用能力，但不反向要求主 CLI 增加 ARC-Bench 专属命令。

### 7.2 需要补强的点

- 生成任务时保留 rubric、metric direction、submission expectation，但不要塞入过多固定指标；
- 对不同 ML task 自动推断主要 metric 和必要 outputs；
- finalization 时不仅复制代码和 metrics，还要生成非模板化 claim 和 README；
- 分析结果应说明哪些 claim 已被指标支持，哪些只是实现说明；
- judge 失败后应将失败原因进入 code-task memory，供下一轮修复使用。

### 7.3 不应做的点

- 不为 ML02、ML04 等单个任务写专用 fallback；
- 不把 ARC-Bench 的 task 名称或 dataset 名称写进核心 repair 逻辑；
- 不用“通过 review”替代 benchmark judge；
- 不让 adapter 自动覆盖用户已有 submission，除非显式指定。

## 8. 与现有流程的结合

### 8.1 Code-Task

Standalone code-task 是 V2.7 benchmark 适配的主要入口。无论是 existing-code 还是 greenfield，都应走同一条主干：

```text
task -> workspace -> plan -> implementation -> review -> validation -> run -> repair -> result analysis
```

V2.7 需要避免两套逻辑再次分裂。benchmark adapter 只提供任务输入和提交投影，不应绕过 code-task 的 memory、review、repair 和 run guard。

### 8.2 8 阶段 Pipeline

8 阶段流程中的 `07-run` 可以选择产出 result analysis，`08-report` 可以选择消费它。这样科研报告能从结构化实验结果中获益，但不会被 benchmark 格式绑架。

### 8.3 External Agent

Codex / Claude Code / OpenCode 可以负责实现和修复，但外部 agent 产物仍需经过：

- workspace 边界；
- artifact ingest；
- review；
- benchmark run；
- result analysis；
- adapter finalization。

外部 agent 不能直接声明 benchmark 通过，也不能绕过 SimpleAutoResearch 的审计。

## 9. 实施计划

### Week 0.5：Benchmark 边界审计

| Day | 任务 | 目标 | 验收 |
| --- | --- | --- | --- |
| Day 1 | 梳理 ARC-Bench 输入/输出 | 明确 task、rubric、metrics、submission、judge 的真实契约 | 写出 adapter contract，不进入核心源码 |
| Day 2 | 检查现有 benchmark adapter | 删除测试残留和任务专用硬编码 | `benchmark/arc_bench/` 可热插拔、可 gitignore |
| Day 3 | 明确 result analysis 与 report 边界 | 避免把 benchmark analysis 混进 survey/report | 文档写清 `07-run -> analysis -> 08-report` 的关系 |

### Week 1：Adapter 可用化

| Day | 任务 | 目标 | 验收 |
| --- | --- | --- | --- |
| Day 4 | ARC prepare 批处理 | 支持按 task id / split / family 批量生成 code-task config | 输出路径、task、config、commands 可复现 |
| Day 5 | ARC finalize | 从 code-task run 生成 submission scaffold | 不依赖固定 run 目录；不会覆盖已有 submission |
| Day 6 | ARC judge wrapper | 可选调用外部 judge，保存原始 judge output | judge 命令可配置，不强绑定本机路径 |
| Day 7 | Adapter README | 写清安装、准备、运行、提交、清理 | 用户能在服务器上按步骤跑 |

Week 0.5 / Week 1 的实现边界：

- ARC-Bench adapter 保持在 `benchmark/arc_bench/` 下，不进入 `src/simple_ar`；
- `prepare` / `prepare-ml` 负责生成 standalone code-task 输入；
- `finalize` 负责把 SimpleAutoResearch run 投影成 ARC-style submission，默认不覆盖已有输出；
- `judge` 只包装用户提供的外部 judge command，并保存 stdout/stderr/result，不解释 judge 语义；
- ARC-specific manifest、rubric、submission shape、judge path 都留在 adapter 层；
- 后续 result analysis 能力稳定后，再由 adapter 调用通用分析层，而不是反向让核心流程依赖 ARC-Bench。

### Week 2：Generic Result Analysis Schema

| Day | 任务 | 目标 | 验收 |
| --- | --- | --- | --- |
| Day 8 | `AnalysisContext` schema | 定义任务、metric、artifact、log、rubric 的通用结构 | Pydantic 校验和 JSON roundtrip |
| Day 9 | `Claim` schema | 定义 claim/verdict/evidence/confidence/limitations | 不允许无 evidence 的 supported claim |
| Day 10 | Metric normalizer | 归一化 metrics、directions、coverage、resource signals | 空指标、NaN、全 0 能被标记 |
| Day 11 | Analysis artifacts | 输出 context、summary、claims、audit、report | 文件结构稳定且紧凑 |

### Week 3：Analyzer / Reviewer Agent

| Day | 任务 | 目标 | 验收 |
| --- | --- | --- | --- |
| Day 12 | Analyzer prompt template | 根据 task/rubric/metrics 生成解释和 claim | 不编造未提供指标 |
| Day 13 | Reviewer prompt template | 检查 claim grounding、遗漏和矛盾 | unsupported claim 会被降级或要求修正 |
| Day 14 | Deterministic audit | 在 LLM 之外检查 claim/evidence/ref | LLM 输出错误不会直接写成最终结论 |
| Day 15 | Retry/backoff | 网络/限流错误指数退避重试 | 不因一次连接波动直接降级或退出 |

### Week 4：Code-Task 集成

| Day | 任务 | 目标 | 验收 |
| --- | --- | --- | --- |
| Day 16 | Post-run analysis hook | benchmark 成功后可选执行 result analysis | standalone code-task 可开启/关闭 |
| Day 17 | Failure analysis hook | benchmark 失败时把分析写入 memory | repair prompt 能看到失败类型和已尝试方案 |
| Day 18 | Previous repair context | repair prompt 显式注入上一轮 diagnosis、changed files、attempted fix 和仍失败证据 | 同类错误重复出现时，模型不会从零猜测或反复改同一处 |
| Day 19 | Generic repair context | runtime failure repair 使用完整上下文，不依赖固定文件名 | 自定义项目结构也能定位相关文件 |
| Day 20 | Result guard refinement | 区分“能跑”“指标有效”“结论可信” | 不再把空结果当成功 |

### Week 5：ARC-Bench ML 实测

| Day | 任务 | 目标 | 验收 |
| --- | --- | --- | --- |
| Day 20 | ML02 / ML04 smoke | 验证不同任务的 run、repair、analysis、submission | 结果非模板、claim 可回溯 |
| Day 21 | 选择 3-5 个 ML task | 覆盖回归、分类、资源约束、不同 metric | 不为单题写专用逻辑 |
| Day 22 | Judge feedback | 收集 judge 失败原因，形成修复清单 | 失败可进入下一轮 repair |
| Day 23 | Adapter hardening | 路径、覆盖、重复运行、清理 | 服务器和本机路径都能跑 |

Week 2-5 首轮实现状态：

- 新增通用 `simple_ar.result_analysis` 层，包含 `AnalysisContext`、metric summary、claim schema、audit 和 artifact 写出；
- result analysis 默认可确定性运行，不需要 LLM，也会标记 missing metrics、全 0 指标、无 claim 等弱证据；
- LLM analyzer 作为可选增强，通过统一 schema 输出 README、claims 和 audit；无证据的 supported claim 会被自动降级；
- ARC adapter 的 `finalize` 会始终写出 `result_analysis/`，并在 `--analyze` 时调用通用 result analysis 层重写 `submission/README.md` 与 `submission/claims.json`；
- ARC adapter 仍保持在 `benchmark/arc_bench/`，ARC manifest/rubric/submission/judge 细节没有进入核心 pipeline；
- Week5 的真实 ML02/ML04 judge-level 验收仍需要在服务器上结合 ARC-Bench judge 继续跑，当前实现先保证 adapter 与通用分析层具备可测入口。

### Week 6：General Benchmark Interface

| Day | 任务 | 目标 | 验收 |
| --- | --- | --- | --- |
| Day 24 | Benchmark adapter contract | 抽象 prepare/finalize/analyze/judge 四个动作 | 新 benchmark 不需要改核心流程 |
| Day 25 | Template registry | result analysis 模板按任务类型选择 | ML、coding、scientific workflow 可区分 |
| Day 26 | Documentation | 写清 adapter 与 core 边界 | 用户知道如何新增 benchmark |
| Day 27 | Regression tests | 加 adapter dry-run、schema、metric normalizer 测试 | 不依赖外部 benchmark 数据也能 CI |

### Week 7：Feedback Loop 与长期化

| Day | 任务 | 目标 | 验收 |
| --- | --- | --- | --- |
| Day 28 | Memory integration | 把 judge/analysis/repair lessons 写入 run memory | 下一轮不会重复同类错误 |
| Day 29 | Cross-run comparison | 同一 task 多次 run 的指标对比 | 能判断模型/backend/配置变化影响 |
| Day 30 | Benchmark dashboard seed | 生成轻量 summary index | 不引入重型服务 |
| Day 31 | V2.7 release audit | 检查结构、文档、测试和废弃代码 | 不留下 benchmark-specific core 污染 |

## 10. 验收标准

V2.7 完成时至少应满足：

- `benchmark/arc_bench/` 可以作为热插拔适配工具存在，不污染核心包；
- ARC-Bench ML 任务可以批量 prepare、run、finalize、analyze；
- submission 不再只有自动模板 summary，而是包含可回溯的 result claim；
- result analysis 能区分指标缺失、运行失败、弱证据和真实支持；
- code-task repair 能消费 benchmark failure、previous repair context 和指标证据，而不是只看最后一行 stderr；
- benchmark-specific 逻辑不进入通用 generation/review/repair 代码；
- `08-report` 能选择使用 result analysis，但 survey/report 不被 benchmark 格式污染；
- 新 benchmark adapter 可以复用同一套 result analysis schema。

## 11. 风险与取舍

| 风险 | 取舍 |
| --- | --- |
| 为 ARC-Bench 过拟合 | adapter 外置；核心只吸收通用 analysis/claim/audit |
| result analysis 与 report 重叠 | analysis 负责指标和 claim grounding；report 负责完整叙事 |
| LLM 解释过度 | 确定性 metric summary + reviewer audit + unsupported 降级 |
| 配置继续膨胀 | adapter 自动推断默认路径和 metric；用户只覆盖必要项 |
| benchmark 数据/路径不稳定 | benchmark root、task root、judge command 全部可配置 |
| repair 继续补丁化 | repair context 使用 failure analysis、artifact map、代码检索、previous repair context 和 memory，而非固定文件名 |
| 外部 agent 绕过验收 | external output 必须回到 SimpleAutoResearch 的 validation/run/analysis |

## 12. 最终判断

V2.7 的关键不是“让 SimpleAutoResearch 能跑某个 benchmark”，而是让项目第一次拥有比较稳定的外部评测闭环：

- benchmark 任务能进入 code-task；
- 实现能被验证和修复；
- 结果能被结构化解释；
- claim 能被审计；
- submission 能被 adapter 投影；
- judge 反馈能回到下一轮 memory 和 repair。

这会把 SimpleAutoResearch 从“能生成/修改代码并写报告”进一步推进到“能被外部 benchmark 持续检验和改进”的阶段。只要保持 adapter 边界清晰，V2.7 可以强化项目能力，而不会再次把核心结构拖回一大坨不可维护的专用逻辑。
