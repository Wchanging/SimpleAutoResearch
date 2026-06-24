# Changelog

[English version](CHANGELOG.md)

本文按倒序记录用户可见的项目变化。规划笔记和设计理由主要放在 `docs/` 和 `MDfiles/`；这里尽量保持为普通 changelog，而不是长期计划文档。

## 2026-06-23

### Changed

- Greenfield dependency advice 从固定候选清单改为“当前 Python 环境动态扫描 + 任务相关筛选”。
  `code_task/meta/dependency_advice.json` 会保留完整 installed-package snapshot，终端和模型上下文
  只展示紧凑的任务相关库；内置 catalog 现在只是语义提示，不再限制可用库范围。
- Greenfield review repair 现在可以在 validation 前针对通用可修复问题做有限 LLM 重写，
  例如核心文件仍是 fallback、缺少 artifact writer 或缺少本地 API。修复后会同步
  `code_task/meta/code_artifacts.json`，避免旧 metadata 继续把已修文件判为 fallback；
  即使只有部分文件修复成功，也会刷新 `review_report.json`，让后续执行聚焦真正剩余的问题。
  通用资源探测 support module 现在有确定性修复路径，避免因为临时 LLM 连接错误卡在
  非任务领域逻辑的 fallback 文件上。
- LiteLLM provider 调用现在会对连接中断、超时、限流和 5xx 等临时错误做有限指数退避重试。
  可通过 `SIMPLE_AR_LLM_RETRY_ATTEMPTS`、`SIMPLE_AR_LLM_RETRY_BASE_DELAY_SEC`
  和 `SIMPLE_AR_LLM_RETRY_MAX_DELAY_SEC` 调整。
- Greenfield run repair 现在会在确定性 runtime 修复无法处理 benchmark 失败时，退到有边界的
  LLM 文件级重写。修复 prompt 会包含 stderr、failure analysis、当前文件内容、项目 API、
  dependency advice 和 metric schema，并在下一轮 validation/run 前先编译检查生成项目。
- ARC-Bench adapter 生成的 `benchmark.metric_directions` 现在只来自每个 topic manifest
  明确声明的实验指标，并额外保留 `runtime_sec = "resource"`。`condition_count`、
  `dataset_count`、`hypothesis_coverage` 等结构完整性信号仍可出现在结果 artifact 中，
  但不再作为所有任务的预设 benchmark 目标。

## 2026-06-22

### Added

- 新增 V2.6 收尾验收记录：真实运行了 `examples/code_task_medium_review/`、
  `examples/full_pipeline_tiny_mlp/`、`examples/research_report/`、
  `examples/greenfield_lightweight_training/` 和 `examples/tool_mcp_codex_agent/`
  的 LLM-backed 路径；大型 `code_task_greenfield_ml_suite` 保留为服务器端压力验收示例。
- 新增 `METRIC name=value` 指标输出格式支持；实验 runner 现在同时解析
  `name: value` 和 `METRIC name=value`，方便外部 agent 或自定义实验脚本用更明确的
  machine-readable 输出格式。
- 新增 greenfield deterministic entrypoint 的非数值字段过滤。`run_experiment()` 可以返回
  `best_condition` 这类描述字段，入口脚本只打印数值指标，避免实验因说明字段无法
  `float()` 而失败。

### Changed

- Code-task 在 `git_worktree` / monorepo 子目录场景下的 planning、patch planning 和
  edit proposal 现在使用实际 project root，而不是误把 workspace 根目录当成项目根目录；
  模型可见上下文会同时包含可编辑目标文件和只读依赖/引用片段。
- 内嵌 8 阶段 code-task bridge 现在会把 `allow_large_edits` 传入 proposal/apply 路径；
  `full_pipeline_tiny_mlp` 示例因此可以在隔离 workspace、review 和 benchmark guard
  保护下应用一次较大的但受控的 old/new replacement。
- Report agent 现在按 section 做 retry/recovery。单个 section 的 JSON 或审查输出失败时，
  不再导致整篇报告退回 evidence-limited fallback；失败 section 会单独重试并在必要时局部兜底。
- Greenfield generation 现在保证 `main.py` 入口不会因为文件预算/依赖排序被截断；
  review repair 也能补齐缺失入口，减少“项目主体已生成但入口缺失”的半成品状态。
- 外部 agent greenfield ingestion 现在只把可交付项目文件计入 `code_artifacts` 和文件预算，
  会忽略 `__pycache__`、`.pyc`、agent metadata 和 review notes；候选项目仍需继续通过
  SimpleAutoResearch 的 code review、run guard 和 metric schema 检查。
- `examples/tool_mcp_codex_agent/` 的文件预算调整为 12，并将 `agent_model` 恢复为空字符串，
  让 Codex CLI 使用用户账号当前默认模型；只有确认账号支持某个模型名时才建议显式填写。

### Fixed

- 修复 standalone medium review 在 worktree 模式下因为上下文定位错误而产生 0 edits 的问题。
- 修复 8 阶段 tiny MLP 在 code 阶段因大编辑配置未传递而被误拦截的问题。
- 修复 report writer 某个 section 返回非预期 JSON 时整篇报告降级的问题。
- 修复外部 Codex handoff 产物因缓存文件被计入预算而误判 `too_many_files` 的问题。
- 修复外部 Codex 项目使用 `METRIC key=value` 输出时，`07-run` 无法解析指标并误判 guard failed 的问题。

## 2026-06-18

### Added

- 新增的 greenfield code-task 模式。`code-task init` 现在可以使用
  `--kind greenfield`，并且不再要求 `code_root`；系统会创建 `empty`
  workspace，并把生成项目放在 `code_task/workspace/generated_project/`。
- 新增 code-task resource artifacts：`code_task/meta/resource_probe.json`
  记录紧凑机器信号，`code_task/meta/resource_decision.json` 将其归纳为
  greenfield generation 和外部 agent handoff 可使用的资源 profile。
- 新增 code-task memory artifacts，位于 `code_task/memory/`，包括
  `task_memory.json`、`task_memory.md`、`edit_history.jsonl`、
  `review_findings.jsonl` 和 `repair_memory.jsonl`。这些 memory 是对现有
  artifacts 的紧凑索引，不替代 canonical logs、patches 或 benchmark 输出。
- 新增自动 memory compaction。当前 active memory 过长时，旧事件会被压缩到
  `compressed_memory.json` / `compressed_memory.md`，近期事件仍保留在
  `task_memory.*`。
- 新增只读 code-task tools：`read_code_task_memory`、`list_code_task_files`、
  `search_code_task_code`、`read_code_task_file_range`、
  `find_code_task_symbol`、`find_code_task_related_files` 和
  `list_code_task_recent_edits`。
- 新增 code-task 级 greenfield 外部 agent handoff。Standalone
  `kind = "greenfield"` 任务现在可以通过 `[implementation]` 选择
  `fake`、`local_llm`、`codex`、`claude_code`、`opencode` 或
  `external_cli` backend；外部产物仍会先作为候选文件 ingest，再走原有
  review、validation、benchmark、memory 和 repair 路径。
- 新增 `examples/code_task_greenfield_ml_suite/`：一个更大的 standalone
  greenfield code-task 验收示例，适合服务器或较强本地机器测试。它要求生成模块化
  ML experiment workbench，优先使用本地可用的开源/打包数据集，必要时才退回
  deterministic synthetic fallback，并包含多模型/基线、ablation、可解析指标和
  resource-aware execution。
- 新增 greenfield dependency advice artifacts。Standalone greenfield code-task
  在 implementation planning 前会写出 `code_task/meta/dependency_advice.*`，
  并在终端提示已安装/缺失的推荐库和可选安装命令；该功能只给建议，不会自动安装依赖。

### Changed

- Code-task work planning、patch planning、edit proposal 和 repair prompts
  现在会读取紧凑 task memory，让重跑和外部 agent handoff 可以延续已有决策、
  失败尝试、validation 结果和 repair context。
- Code-task execute 现在会记录 probe、baseline、work-plan、patch-plan、
  proposal、apply、validation、patched run、failure analysis 和 repair proposal
  等步骤的 memory event；patch validation block 和静态 validation findings
  也会写成 review finding。
- 8 阶段 pipeline 中的 greenfield experiment generation 现在会委托给
  `06-code/code_task_run/` 下的统一 code-task greenfield run，再把兼容产物投影回
  `06-code/generated_project/` 供 `07-run` 使用。standalone greenfield code-task
  和 research-to-code greenfield 由此共享 workspace、memory、review、validation
  和 run 路径。
- Greenfield code review 现在会读取 implementation memory、architecture 和
  resource context，并保留兼容旧 `code_review.v1` 的同时，暴露更稳定的
  `review_contract`。
- 外部 agent handoff package 现在会包含 task memory context 和 code-task-only
  tool schemas；greenfield handoff 则暴露 experiment-only tools，避免 agent 看到
  与当前 workflow 无关的工具面。
- `simple_ar.code_task` 包 facade 改为 lazy-load 公共导出，避免导入小型子模块时
  顺带加载整个 code-task 和 agent-backend 图。

## 2026-06-14

### Added

- 新增 V2.6 common tool harness foundation：`src/simple_ar/tools/` 现在包含 shared tool specs、permission/risk levels、组合现有 report / experiment tools 的 registry、带权限检查的本地 dispatch、紧凑 `tool_trace.jsonl` 写入，以及 OpenAI/MCP-style schema export。
- 新增 V2.6 外部 agent handoff foundation：`src/simple_ar/agent_backends/` 现在可以写出 workspace-scoped `agent_handoff/<name>/` package，包含 instructions、真实 tool schema、permission policy、artifact handles、expected outputs、context files 和 workspace manifest。
- 新增外部 agent 输出收集路径：backend 产物会先进入 `agent_outputs/<name>/`，在通过已有 validation、result guard、report audit 或 code-task patch checks 之前，不会被当作可信结果。
- 新增 Codex、Claude Code 和 OpenCode profile Markdown，用于未来可选 backend。这些 profile 是 workspace-scoped guidance assets，不会默认安装到用户全局环境。
- 新增可运行的 V2.6 backend wrappers：deterministic `fake`、`local_llm`、generic `external_cli`，以及 Codex / Claude Code / OpenCode CLI wrappers。这些 wrapper 会记录 cwd、timeout、env allowlist、stdout/stderr 和 `agent_run.json` provenance。
- 新增 `simple-ar tools schema`、`simple-ar tools call` 和 `simple-ar tools serve-mcp`。MCP server 使用 stdio，并通过 `tools/list` 和 `tools/call` 暴露真实的 run-local 只读 experiment tools。
- 新增 `examples/tool_mcp_codex_agent/`：一个受控的 Codex 外部 agent 示例，包含 MCP server 模板；示例默认保持 `[implementation].agent_model` 为空，让 Codex CLI 使用当前账号配置的模型。
- 新增 `[implementation].agent_mode`，作为 V2.6 外部 agent 层唯一模式开关：`model`、`handoff`，以及预留的 `delegated_workspace` 契约。

### Changed

- 开发和使用文档现在说明 V2.6 的 tool/agent 边界：外部工具是可选 strong-path adapter，本地 research、report、experiment 和 code-task workflow 不依赖 MCP 或外部 agent CLI。
- Greenfield generation 和 greenfield repair 现在可以在 `[implementation].provider` 选择 agent backend 时走 handoff 边界。外部输出仍然是不可信 candidate files，必须继续通过已有 code review、result guard、rerun 和 validation gates。
- 预留的 code-task `external_agent` adapter 现在可以在显式 enabled 时通过 common handoff/ingestion 路径启动 backend；默认行为仍然只是输出可审查 invocation plan，不执行外部工具。
- 外部 agent wrapper 现在支持 `[implementation].agent_model`，但示例默认留空；Codex、Claude Code 和 OpenCode 测试只有在确认 CLI/账号支持某个模型名时才建议显式填写。
- 外部 agent 失败时，主流程错误现在会带上精简的 stderr/stdout 尾部，并对不支持的模型名、找不到 CLI binary 等常见问题给出提示。
- Agent-backed greenfield/code-task 路径现在会归一化并校验 `agent_mode`，把它写入 backend artifacts，并且对预留的 delegated-workspace 强路径显式失败，避免静默降级成普通 handoff。
- 外部 CLI backend 现在会在启动 subprocess 前解析 Windows command shim，例如 `codex.cmd`；Codex wrapper 也会使用绝对 handoff root，并加上 `--skip-git-repo-check`。
- 外部 agent handoff package 现在会在重跑前自动归档旧目录，避免旧的 `stdout.txt`、`stderr.txt` 或 `agent_result.json` 污染下一次 Codex/Claude/OpenCode 尝试。旧 handoff 会移动到 git 忽略的本地 cache，而不是放在 sibling `agent_handoff/archives/` 里，避免下一次外部 agent 读到旧失败日志；旧版本已生成的 sibling archives 也会在新 handoff 创建前迁移到同一个 cache。
- `simple-ar clean --shared-cache` 现在也会清理 `.simple_ar_cache/agent_handoff_archives`，因此现有 clean 命令可以完整移除跨 run 的外部 agent handoff transcripts。
- Agent-backed greenfield generation 现在要求 `generated_files/` 非空后才会复制到 `06-code/generated_project`，空目录提案会在 handoff 边界失败，不再延后变成难懂的缺失 `main.py` review error。

## 2026-06-13

### Added

- 新增 `07-run/diagnosis.json` 和 `diagnosis.md`。实验运行诊断会把 result guard issue、code review warning、缺失指标、stdout/stderr tail 和受控修复建议汇总成统一的 repair/report context。
- 新增只读 experiment tool：`read_experiment_diagnosis`，并让 `inspect_execution_failure` 同时返回 diagnosis。

### Changed

- Greenfield repair 现在除了 guard issue，也会读取 diagnosis context；缺失 required metrics 有了稳定契约，即使后续 guard 内部实现调整也不影响修复入口。
- Experiment report context 现在会把 `artifact:experiment_diagnosis` 和 canonical results、result guard 一起暴露给报告 writer/reviewer。
- Pipeline stage 输出摘要现在显示该阶段的真实产物，不再只显示内部 `contract.json` / `report.md` 摘要；例如 `07-run` 会直接显示 `results.json`、`guard_report.json`、`diagnosis.json`、stdout 和 stderr。
- Greenfield schema repair 现在会改写生成项目实际执行的 `main.py` 入口，并在后续 rerun 中保留 repaired-result provenance，不再把补丁写到未被执行的 fallback 模块里。
- Greenfield 训练示例现在从 tiny smoke project 升级为中等偏轻量 experiment-suite 任务，包含更多 condition-level metrics 和更大的文件/行数预算。
- Greenfield architecture planner 现在会把 8+ 文件预算视为中等偏轻量项目，引导模型规划 data、features、models、metrics、evaluation、reporting 和 self-checks 等有意义模块。
- Developer quick checks 现在也覆盖 run-config 和公开 example config loading tests，避免 example 路径或统一配置字段漂移后才在更重的 pipeline tests 中暴露。

## 2026-06-12

### Added

- 新增 V2.5 experiment/code reliability foundation：顶层 pipeline 配置现在可以使用统一的 `[task]`、`[implementation]`、`[execution]`、`[resource]`、`[evaluation]` 和 `[generation]` sections。
- `05-design` 现在会写出紧凑 experiment contract 包：`experiment_contract.json/.md`、`result_schema.json`、`resource_plan.json`、`dependency_plan.json`、`domain_profile.json` 和 `contract_validation.json`。
- 新增 domain profiles：generic experiment、existing-code experiment、ML experiment 和 code-agent evaluation task。
- 新增 V2.5 execution foundation：`src/simple_ar/experiment/execution/` 现在包含 `RunRequest` / `RunResult`、local execution backend、canonical result normalization 和 result guard checks。
- 新增受控 greenfield 实现路径：没有现成源码项目的任务可以在 `06-code` 写出 `architecture_plan.json/.md`、`file_plan.json`、`generated_project/`、`code_artifacts.json`、`implementation_memory.json`、`code_review.json`、`code_backend.json` 和可运行的 `experiment.py` harness。
- 新增 experiment tool contract 层：`src/simple_ar/experiment/tools/` 现在包含只读 local gateway tools，以及面向未来 MCP / external-agent adapter 的 OpenAI tool schema export。
- 新增轻量 greenfield 训练示例：`examples/greenfield_lightweight_training/configs/greenfield_training.toml`。

### Changed

- 统一任务设置会被归一到 `task_config`，并在需要时映射回旧 code-task keys；现有 `code_task_project` 仍可运行，新配置则可以使用更一致的参数结构。
- 当 `05-design/contract_validation.json` 报告 experiment contract 失败时，`06-code` 会在代码生成前停止。
- `07-run/results.json` 现在使用 canonical schema，同时保留旧顶层 `metrics`、`returncode` 和 `timed_out` 字段作为兼容入口。`07-run/guard_report.json` 会记录 timeout、非零退出、缺失指标和 NaN/Inf 检查。
- 内嵌 code-task pipeline run 现在会把嵌套 baseline-vs-patched comparison 投影到 canonical `07-run/results.json.comparisons`。
- `07-run` 现在会在 greenfield 执行证据显示缺少 schema required metrics 时，尝试一次受控修复、重新运行，并记录 `repair_summary.json`。
- 内嵌 code-task pipeline run 现在会在 `06-code` 内先验证 patched benchmark，再交给 `07-run`；如果 benchmark 失败，会基于 failure evidence 尝试一次受控 repair，而不是把坏补丁继续传给报告阶段。
- 从 `06-code` 或 `07-run` 重跑时，默认会把已有已审核产物保存在 `archives/<timestamp>/` 下。只有明确不需要旧代码/运行证据时，才使用 `--overwrite-stage-artifacts` 或 `[run].overwrite_stage_artifacts = true`。
- canonical `07-run/results.json` 现在会携带紧凑的 resource plan、code review 和 guard 信号；`08-report` 会把这些作为实验依据暴露给 writer/reviewer，使 code review 或 guard 有 warning 时报告能自动收紧结论。
- 内嵌 code-task bridge 已从旧 experiment facade 拆到 `src/simple_ar/experiment/code_task_bridge/`；pipeline experiment stage 逻辑也拆成 design/code/run 模块，让 `pipeline_stages/experiment.py` 保持薄接线层。
- full-pipeline tiny MLP 示例现在包含新的统一 task/config sections，同时保留旧 code-task sections 作为兼容路径。
- 配置参考文档现在说明 V2.5 统一 sections，并且不再把单独的 `[workspace]` 当作 embedded code-task config 信号。
- greenfield experiment contract 现在会包含 `[task].task_file` 的受限摘录，让从零代码生成能读取详细任务 Markdown，而不是只看到文件路径。
- greenfield code review 默认不再把 LLM 生成项目静默替换成 deterministic scaffold。确定性审查失败时会保留产物供检查；只有显式设置 `[generation].allow_fallback_scaffold = true` 才会降级，LLM reviewer findings 则作为 warning 保留。

## 2026-06-05

### Added

- 新增 V2.4 report foundation：`src/simple_ar/report/` 现在包含 Markdown 报告模板、reviewer criteria、紧凑 report memory、只读 source-backtracking tools 和本地 report audit 产物。
- `08-report` 现在除了 `report.md`、`references.bib`、`manifest.json` 和 `report_quality.json`，还会写出 `report_memory.json` 和 `report_audit.json`。
- Pipeline config 现在支持 report template / reviewer 设置、source backtracking 预算，以及 `[report.audit]` 开关。
- 新增受控 report Writer/Reviewer loop。LLM 模式下，`08-report` 会按模板分节起草、按 criteria 审查每节、执行有限修订，并把 reviewer findings 写入 `report_audit.json`。
- 新增报告重跑写入策略：`overwrite`、`archive` 和 `variant`。其中
  `variant` 会把额外报告包写入 `08-report/variants/<label>/`，不替换当前主 `report.md`。

### Changed

- 内嵌 `code_task_project` 现在默认只执行第一个较小的 work-plan batch，不再自动把串行依赖项合并成大补丁；standalone `code-task execute` 仍保留面向人工审核工作流的合并行为。
- Report context 现在会把嵌套 code-task 的 `run/comparison.json` 当作一等实验/指标证据。`08-report` 可以看到 baseline、patched 和 delta 指标，fallback report 也会写出 Code Task Evidence 前后对比表。
- 新增严格的内嵌 code-task pipeline 检查，覆盖 `06-code` 到 `08-report` 的完整路径；bundled tiny-digits MLP 测试中，accuracy 从 `0.766667` 提升到 `0.913333`，macro F1 从 `0.756898` 提升到 `0.913254`。
- 移除了 V2.4 report service 里隐藏的 legacy 单 prompt 报告生成分支。LLM 报告现在统一走 Writer/Reviewer agent loop；失败时回退到结构化 deterministic report。
- Report citation 映射、显示和 cleanup helper 已迁移到 `src/simple_ar/report/citations.py`；report tool gateway 现在也能用 source handle 解析 paper brief，让 reviewer tool request 和公开 schema 保持一致。
- 公开 examples 现在收束为四个维护入口：`examples/research_report/`、`examples/code_task_medium_review/`、`examples/full_pipeline_tiny_mlp/` 和 V2.5 的 `examples/greenfield_lightweight_training/`。旧的窄范围/过渡配置已从 `examples/` 移除，toy spam 项目迁移到 `tests/fixtures`。
- `code-task execute` 现在默认连续运行到真正的审核门，并使用 Rich 展示步骤状态。
  `--interactive` 只用于 primitive step 调试；`--yes` 现在会在普通 execute 模式下明确自动批准 inline 审核门，并在 interactive 模式下自动继续 primitive prompts。
- LLM work-plan / patch-plan 在配置次数内重试后仍失败时，现在会停在
  `llm_planning_failed`，不会静默写入 offline fallback plan。只有明确接受较弱的
  deterministic fallback 时，才使用 `--allow-planning-fallback`。
- `code-task init` 现在也使用 Rich 风格输出，和 execute 的展示方式保持一致。
- `simple-ar clean --shared-cache` 现在会同时清空共享 research index 和共享 literature provider cache，并显示更强的清理警告。
- medium review pipeline 示例现在允许 edit scope 修改 `configs/experiment.json`，这样模型实现 phrase feature 后可以启用新 feature family，benchmark 才能测到实际提升。
- 内嵌 code-task experiment 转发日志时，现在使用 patched benchmark stdout/stderr 标签，避免和 standalone baseline/patched artifacts 混淆。
- 报告生成现在使用 `P1` 这类模型侧短 citation key，并在 citation audit
  前映射回真实 provider id，减少长 OpenAlex / Semantic Scholar id 拷贝错误。
- Report section planning 现在使用可配置的 `max_section_sources` 预算，不再硬编码每节只给 4 个 source handles。

- `max_section_sources = 0` 现在表示每个报告 section 都可以看到全部已选论文级 handles，全文 chunks 仍通过有界 backtracking 按需回查，适合大上下文模型下的 survey 生成。
- Survey 报告现在区分“起草顺序”和“最终展示顺序”：起草顺序由模板里的 `Draft order:` 指令控制，最终 Markdown 仍按模板顺序展示。
- 报告起草新增可选 `batch_refine` source strategy，用于把更大的论文集合分批增量整合到同一 section 中。
- `batch_refine` 可通过 `review_source_batches = true` 在每个 source batch 后增加 reviewer 检查。

### Internal

- 公共 CLI 和 pipeline stage 入口拆分到 `src/simple_ar/cli/` 和
  `src/simple_ar/pipeline_stages/` 下的小模块；旧的大文件仅作为私有兼容 shim 保留。

## 2026-06-03

### Changed

- 真实 online full-text check 现在能把 `.../article/download/...` 这类常见学术下载链接识别为 PDF candidate，即使 URL 本身不以 `.pdf` 结尾。
- Compact `search_meta.json` 现在会保留一份小型 `source_plan`，让后续 read/synthesize/design 阶段在 verbose planning traces 被清理后，仍能知道实际 sources、全文意图、index backend 和预算。
- Retrieval 现在会先收集一个有边界的 overfetch 候选集合，再做最终筛选。这样当某个 provider 返回的候选后续被丢弃时，系统仍有机会用其他 provider 补足文献预算。
- Synthesis limitations 现在会区分全局全文解析成功和 shortlisted paper 的证据缺口，避免“被丢弃论文解析成功”让保留论文的 brief 看起来比实际更强。
- 精简 research run 的默认主链：`papers.jsonl` -> `paper_notes.json`
  Paper Brief -> `synthesis_brief.json` -> `experiment_contract.json`。
  旧的 `03-read/cards/*` 和 `04-synthesize/evidence/*` 诊断产物现在只在
  `[run].debug_artifacts = true` 时保留。
- 解耦 research 阶段产物归属。`02-search` 现在只负责 retrieval/document/full-text/index
  产物；`03-read` 负责 reading review/shortlist 以及 paper/claim/method/dataset/code-link cards；
  `04-synthesize` 负责 evidence pack、gap、ideas 和 novelty hints；
  `05-design` 负责 experiment contract 和可选 tool handoff 草案。
- 将 search 阶段 metadata screening trace 改为
  `02-search/traces/retrieval_selection.jsonl`；语义 keep/drop/priority
  决策现在归属 `03-read/review/`。
- Read 阶段的 LLM screening 现在可以丢弃或重排检索到的论文；`paper_notes.json`
  和 read cards 会基于 shortlist 生成，而不是无条件读取所有 retrieved rows。
- Read 阶段的 LLM review 现在改为可扩展的两步式路径：先并发粗筛 title/abstract 小批次，再对保留集合做重排，并记录阅读优先级、证据角色和 synthesis hint。
- Pipeline Rich 进度输出现在会展示每个阶段的职责说明，用户运行时可以直接看出当前阶段在做什么。
- Artifact retrieval 现在会忽略 cards、evidence packs、idea candidates、
  experiment contracts 和 tool handoff drafts 等结构化中间产物，避免系统生成的证据表被当成新的来源材料再次检索。

## 2026-06-02

### Added

- 新增 V2.3 Week 3 research-bridge 产物。精简运行会在 `03-read/cards/`
  保留 reading cards，在 `04-synthesize/evidence/` 保留 synthesis evidence，
  在 `05-design/evidence/` 保留 experiment contract；debug 运行还可以保留
  `tool_context.json/md`、`evidence_review.md`、`decision_log.jsonl` 和
  `eval_report.json/md` 等 design handoff 草案。
- 新增 `[research.budget].novelty_backend`，当前稳定支持 `local`，
  用于基于当前 evidence pack 生成词面重合 novelty-risk hints。

- 新增 V2.3 Day 12 section-aware document extraction。默认精简运行会用 section
  spans 构建 chunks/cards；debug 运行可以额外保留
  `02-search/documents/sections.jsonl` 供检查。
- 当 section records 存在时，research chunks 会带上 `section`、`heading` 和
  `section_id` provenance，让 `02-search/research_index/chunks.jsonl` 不再只是扁平文本切块。
- 新增 V2.3 Day 13 扩展 evidence cards：
  `03-read/cards/method_cards.jsonl`、`dataset_cards.jsonl` 和
  `code_links.jsonl`。
- 报告阶段新增紧凑 structured evidence summary。LLM report 和 fallback report
  都可以使用 read 阶段的 paper cards、claim cards、section records 和扩展 cards 作为有边界的证据。
- 新增可配置的 code-task `[edit_scope]` allowlist 和额外 protected patterns。
  该 scope 会写入 `code_task/manifest.json`，并在 repo map、context、work-plan、
  edit proposal、repair 和 apply-time patch validation 中重复生效。
- 新增 debug-only 只读 Tool/MCP handoff 产物，位于 `05-design/tools/`：
  `tool_adapter_contract.json/md`、`tool_trace.jsonl` 和
  `external_agent_backend.md`。
- 新增 debug-only `05-design/governance/artifact_retention_policy.json/md`，
  用于把 search artifacts 区分为稳定 run outputs、evidence tables、cache、
  trace、debug 诊断和可重建文件。
- 新增 `simple-ar clean RUN_DIR`：先用 Rich tree 预览、再确认删除的缓存清理命令，
  用于清理可重建 run-local 缓存和该 run 在共享 SQLite research index 中的 rows。

### Changed

- Paper cards 和 claim cards 现在会优先使用 section-aware 的 method、experiment、
  result 和 limitation chunks，而不是把所有文本都当成扁平摘要处理。
- Usage 文档已更新精简 `02-search/` 产物树，并区分默认 evidence artifacts、
  debug-only 诊断文件和 tool handoff 草案。
- Code-task 示例现在显式声明 edit scope：实现文件可编辑，tests、benchmark 和锁定配置仍作为只读证据。
- LLM research planning 现在会在 query expansion 关闭时继续生效。`[research].max_queries`
  仍然控制最终 query 数量，但 `[research].planner = "llm"` 不会再静默退回 deterministic planning。
- Retrieval selection 现在会先尽量保留 required facets 的多样性，再用普通 rank 填满剩余文档预算，减少某一类高分 query 把 overview/benchmark/dataset 证据挤掉的情况。
- V2.3 online check 配置现在默认使用 compact artifacts，并避免把本地 demo notes
  混入在线 evidence check。需要 planning/traces/coverage/tool 草案时再设置
  `[run].debug_artifacts = true`。
- Evidence pack 现在保存 artifact refs 和 card ids，不再重复复制 `cards/*.jsonl`，
  减少 synthesis 阶段产物膨胀。
- V2.3 release hardening 覆盖了分层检查、内置 code-task 示例、compact search
  CLI run、medium code-task baseline CLI run，以及全量 unittest discovery。

- Pipeline 进度输出现在使用克制的 Rich panel、阶段分隔线和状态/消息颜色分类，
  让用户更容易看清当前阶段和关键事件，同时不改变 pipeline 执行逻辑。

## 2026-05-31

### Added

- 将 Pydantic、Rich、LiteLLM、pyalex 加入直接依赖，作为第一批基础设施替换。
- 新增基于 `WorkspaceState` 的 pipeline reboot core。运行目录现在会写入顶层
  `state.json`，已完成阶段可以写出紧凑的 `contract.json` / `report.md`，
  用于机器可读交接和人工审查。
- 新增可选 `unstructured` 全文解析后端，可通过 `[research].parser_backend = "unstructured"` 启用；如果未安装该可选包，只会在 manifest 中记录失败，不会让 search 阶段失败。
- 新增可选 LanceDB research-index 后端状态，可通过 `[research].index_backend = "lancedb"` 或 `"hybrid_lancedb"` 启用；`chunks.jsonl` 仍然是可移植 source of truth。

### Changed

- 顶层 pipeline TOML 现在先经过 Pydantic schema 校验，再 flatten 成现有运行时配置 dict，配置类型错误会更早、更明确地暴露。
- Code-task TOML 现在也通过 Pydantic section schema 解析 init 和 execute options，替换之前的自由 dict 解析路径。
- LLM client 现在通过 LiteLLM 调用 provider，不再直接构造 OpenAI SDK client；OpenAI-compatible `OPENAI_BASE_URL` 仍然通过 LiteLLM 的 OpenAI provider 路径支持。
- OpenAlex 检索现在使用 pyalex，而不是手写 urllib request client；项目内仍保留 `Paper` normalization 层。
- Pipeline 进度和 developer-check 输出现在通过一个轻量 Rich console wrapper，为后续更清晰的人工审核输出打基础，同时保持现有 CLI 兼容。
- Research SQLite FTS / LanceDB 加速索引默认共享在 `.simple_ar_cache/research_index`，run 目录只保留可审计的 `chunks.jsonl` 和 `index_meta.json`；共享数据库按 `run_id` 更新，避免每个 run 重复创建一份索引。
- Pipeline 阶段依赖现在优先使用显式 `WorkspaceState` 指针，而不是通过
  `find_artifact` 反向扫描 run 目录；旧 helper 仅作为 legacy compatibility 保留。
- 默认 pipeline run 会在 search 阶段 contract 写出后只压缩 `02-search` 的诊断目录。
  search-owned `documents/` 和 `research_index/` 会保留在 run 目录；后续阶段会各自保留
  cards/evidence/contracts。需要额外保留 planning、trace、screening 和 coverage-review
  诊断产物时，可设置 `[run].debug_artifacts = true`。
- 紧凑 search run 现在会同步清理 `search_meta.json` 中指向已删除诊断产物的路径，
  避免 metadata 指向不存在的 planning/trace/review 文件。
- 原本巨大的 `stage_handlers.py` 和 `cli.py` 已移动到私有 `src/simple_ar/_legacy/`，
  对外 import path 只保留小型 compatibility wrapper，便于后续逐步拆掉巨石实现。
- Experiment runner/template helpers 现在直接放在 `src/simple_ar/experiment/`；
  冗余的 `src/simple_ar/coding/` 包已移除，避免模板实验和 code-task 自动化同时抢占
  “coding” 这个领域名。
- Research 模块现在按生命周期分组到 `planning/`、`sources/`、`documents/`、
  `store/`、`evidence/` 和 `outputs/`，不再把所有检索/证据文件平铺在同一目录。
- Code-task 模块现在按生命周期分组到 `runtime/`、`workspace/`、`analysis/`、
  `editing/`、`execution/` 和 `orchestration/`，收缩原先 25 个左右文件平铺的包表面。
- 顶层实现文件已收束到明确的领域包：`core/` 放 pipeline primitives，
  `app/` 放 config/state/usage/dev checks，`integrations/` 放 LLM provider，
  `experiment/` 放模板实验与 metrics，`report/` 放报告审计工具。此前宽泛的
  `simple_ar.pipeline`、`simple_ar.artifacts`、`simple_ar.prompts` 等 facade 文件已移除。
- README、Usage、Workflow 和 Config Reference 已说明 `unstructured` 与 LanceDB 是可选后端，而不是基础安装强依赖。

## 2026-05-27

### Added

- 新增 V2.3 Day 10 failure-safe full-text caching：被选中的本地全文会标记为 cached，
  受控远程获取失败会记录到 `fulltext_manifest.json`，search 阶段继续使用 metadata/abstract evidence。
- 新增 V2.3 Day 11 full-text extraction：
  `02-search/documents/fulltext_extraction.json` 现在会记录已缓存/本地全文资源的 parser 结果，
  并在 read 阶段生成 evidence cards 前把解析文本送入 `research_index/chunks.jsonl`。

### Changed

- Search 阶段 contract 现在声明 `documents/fulltext_extraction.json`，命令行完成输出和
  `search_meta.json` 都能看到全文解析产物。
- 本地 research 示例默认启用 `use_fulltext = true`，便于检查本地 Markdown/text 的
  parser -> chunk -> card 链路。
- README、Usage、Config Reference 和 Workflows 文档已更新当前边界：Markdown/text
  和基础 HTML 可解析，PDF 解析是 best-effort，向量检索还未启用。

## 2026-05-26

### Added

- 新增 V2.3 Day 3 research-question 和 query-plan sections，统一放在
  `02-search/planning/research_plan.json` 中。
- 新增可选 LLM-backed research planning，可通过 `[research].planner` 控制；
  离线或 provider 失败时仍保留 deterministic planning 兜底。
- research plan 中新增结构化 `query_specs`，让 LLM research planner 保留
  title/abstract keyword 意图，而不是只输出偏浏览器搜索风格的 query 字符串。
- 新增 V2.3 Day 4 检索 trace 产物：
  `02-search/traces/retrieval_rounds.jsonl` 和
  `02-search/traces/retrieval_selection.jsonl`，记录实际执行的 source/query 尝试、
  简洁 query 意图 trace、去重和轻量 retrieval-selection 决策，再写入 `papers.jsonl`。
- 新增 V2.3 Day 5 coverage 产物：
  `02-search/review/coverage_report.json` 和 `02-search/review/coverage_report.md`，
  记录 required facets 覆盖情况、缺失 facets、问题覆盖度和预算内 follow-up query 建议。
- 新增 V2.3 Day 6 document-store 产物：
  `02-search/documents/documents.jsonl` 和
  `02-search/documents/cache_manifest.json`，记录已选 metadata、配置的本地文件、
  extraction status、source counts，以及 cache/full-text 意图，不下载受限全文。
- 新增 V2.3 Day 7 本地 research-index 产物：
  `02-search/research_index/chunks.jsonl` 和
  `02-search/research_index/index_meta.json`；在 `sqlite_fts` / `hybrid` 模式下可选生成 SQLite FTS。
- 新增 V2.3 Day 8 deterministic evidence-card 产物：
  `03-read/cards/paper_cards.jsonl` 和
  `03-read/cards/claim_cards.jsonl`，基于 document chunks 生成，并带 evidence refs 供后续 audit 使用。
- 新增 V2.3 Day 9 full-text planning 产物：
  `02-search/documents/fulltext_manifest.json`，记录 arXiv/OpenAlex/local-file 全文 hints、
  fetch 预算决策，以及 blocked/skipped 原因；默认不会下载远程 PDF。
- 新增 Semantic Scholar 在线 metadata connector，默认位于 OpenAlex 和 arXiv
  之间，让 V2.3 research search 不依赖 fixture metadata 时也有更广的论文来源。
- 新增本地研究源示例 `examples/run_configs/local_research_report.toml`，
  并在 `examples/research/` 下提供配套 Markdown notes。

### Changed

- 搜索执行现在通过 research connector wrapper 调用 OpenAlex、Semantic Scholar、
  arXiv 和本地 Markdown/text 文件源，同时保留已有 fixture 与 cache fallback 行为。
- search 阶段现在会先把 seed queries 扩展成受限的 facet-driven follow-up queries，
  再构建 `research_plan.json`；LLM 开启时，`planner = "auto"` 可以让模型参与
  更强的 question decomposition 和 query 术语扩展。
- 当 coverage 仍有缺口且 retrieval-round 预算允许时，search 会继续跑第二轮
  ordered-fallback follow-up retrieval，并重新筛选候选文献。
- Search 阶段的 planning、trace 和 review 产物现在分别放在
  `02-search/planning/`、`02-search/traces/` 和 `02-search/review/` 下，
  避免较大的 research run 把所有中间文件平铺在阶段目录根部。
- research planning 现在合并为一个 `planning/research_plan.json`，避免把研究问题、
  query plan 和 source plan 拆成三个小 JSON，同时保留可审查的内部 sections。
- 本地 Markdown/text 搜索现在使用轻量 keyword-overlap 匹配，而不是要求
  query 字符串逐字出现，使规范化后的 paper-search query 更适合短笔记源。
- 本地 Markdown/text 文档现在会生成带 content hash 的 parsed document record；
  PDF 输入会先记录为 skipped 或 failed，除非明确启用 full-text 意图且可选 parser 可用。
- 摘要和已解析本地文件现在会切成可移植本地 index chunks，为后续 evidence cards
  和 RAG-style retrieval 提供稳定输入，不需要现在就引入 embedding。
- OpenAlex metadata 现在会尽量保留 open-access URL hints；arXiv records 也能推导 provider
  PDF hints，供后续受控全文获取/解析阶段使用。
- read 阶段现在会在 `03-read` stage contract 中记录 paper/claim card 数量，方便检查 evidence layer 是否生成完整，同时不再让 `search_meta.json` 承担下游阶段职责。
- 拆分公开命令和配置文档：`CLI_REFERENCE_zh.md` 现在只聚焦命令语法、参数表和产物；
  新增 `CONFIG_REFERENCE_zh.md` 集中说明 TOML schema、完整配置和 workspace 模式变体。
- 配置文档补充了更完整的 inline comments 和关键字段说明，便于理解 `max_papers`、
  research budgets、workspace modes 和 execute budgets 等不够自解释的设置。

## 2026-05-24

### Added

- 添加 V2.3 research source-planning 基础：`02-search` 现在会写出
  `planning/research_plan.json`，记录研究问题、计划 query、source 顺序、
  research mode、本地文档、cache/index 偏好和轻量预算。
- 顶层 run config 现在支持 `[research]`，包括 `sources`、`queries`、
  `local_documents`、`cache`、`index_backend` 和 `[research.budget]`。

### Changed

- 搜索执行开始通过 research connector wrapper 调用 OpenAlex、arXiv 和本地
  Markdown/text 文件源，同时保留已有 fixture 与 cache fallback 行为。
- README、Usage、CLI Reference 和 Workflows 文档开始补充
  `02-search/planning/research_plan.json`、`[research]` 配置和本地文件源说明。

## 2026-05-23

### Changed

- 内嵌 8 阶段 `code_task_project` run 现在在 `06-code` 使用 V2.2 的
  code-task 执行形态：repo map / context pack、LLM work plan、attempt/batch
  state、patch plan、受控 edit proposal、apply 和 validation。最终报告证据
  除了 summary、diff、comparison，也会指向 work-plan 和 batch artifacts。
- 新增 medium review pipeline code-task 示例，包含 `main.py` 入口、JSON config、
  多模块 feature/model/metric 结构、可见进度输出，以及启用 streamed benchmark
  output 的 TOML task config。
- `code-task execute --config` 现在可通过 `[execute].stream_benchmark_output = true`
  在 baseline 和 patched benchmark 运行时转发 stdout/stderr。
- Benchmark streaming 现在支持 `"auto"`、`"line"` 和 `"summary"` 等字符串模式；
  `"auto"` 同时兼容普通逐行日志和 `tqdm` 这类 carriage-return 进度输出。
- 文档已把内嵌 research-to-code 路径从旧的直接 patch-plan 流程更新为
  work-plan / batch based flow。
- Work-plan batch 创建现在会把小型串行依赖链合并成一个受控执行批次，例如
  feature producer -> model consumer -> config switch。拆分后的审核项仍然保留，
  `batch_state.json.work_item.source_work_item_ids` 会记录合并范围；如果合并批次
  触发 large budget，应用前仍需要显式审核和 `--allow-large-edits`。
- 应用已审核 large proposal 时，现在会把 apply-time approval 记录到
  `applied_edits.json` 和 `manifest.json.patch.budget`；executor benchmark
  路径也会避免写入重复的 validation history。
- 公开文档现在把 README 保持为更简洁的项目入口，把完整 code-task executor
  链路下沉到 Usage，移除冗长的 PowerShell run 目录选择脚本，统一使用
  `runs/<run-id>` 占位符，并把 `copy`、`git_worktree`、`sparse_copy` 作为一等
  workspace 策略展示。

## 2026-05-22

### Changed

- Code-task patched run 现在会把 benchmark 是否通过和任务目标是否达成分开记录：`manifest.json.objective.status` 来自 baseline-vs-patched comparison，因此 benchmark 通过时仍可能被标记为 `regressed`、`mixed` 或 `inconclusive`。
- `code-task execute` 现在会优先选择第一个真正可执行的实现型 work item，而不是盲目把纯检查、纯分析的第一个 work-plan item 当作编辑批次。
- 手动运行 `code-task validate` 和 `code-task run` 时，如果补丁已经应用，也会同步 latest batch、attempt 和 work-plan 状态，使手动路径更接近 executor 路径。
- 应用 repair proposal 时，现在会记录实际使用的 repair proposal path 作为 latest applied proposal；后续 patched benchmark 通过后，旧的 failure/repair section 会在 status 和 summary 中标记为 resolved。
- Failure analysis 现在会捕获 metric floor 和 timing budget 信号，例如 `accuracy below benchmark floor`、`macro_f1` 和 `train_time_sec`。
- 文档补充说明了 objective verdict、implementation-batch selection、repair application state，以及 benchmark pass 但指标退化时应该如何判断。
- `README.md` / `README_zh.md` 和 `docs/USAGE.md` / `docs/USAGE_zh.md` 现在把 TOML + `code-task execute` 作为主要 code-task 使用路径，primitive commands 下沉为高级调试步骤。
- 新增 V2.2 editor backend interface，并把默认 controlled old/new patch 路径迁移到 `controlled_patch` backend 后面，同时保持现有 CLI/API 兼容。proposal、batch、apply、manifest 和 status 产物现在会暴露 backend metadata。
- 新增预留的 `external_agent` editor 边界，为后续 Codex / Claude / OpenCode adapter 做准备。当前包含 provider 规范化、保守权限策略、secret/home 读取阻断规则和可审查 invocation-plan artifact，但默认仍不可执行。

## 2026-05-21

### Added

- 添加 Day 17-20 V2.2 batch-level edit budget enforcement：code-task proposal 现在有 normal / large / absolute 三档预算，并通过 `--allow-large-edits` 做显式大编辑审核。
- 在 `code_task/attempts/attempt-NNN/batches/batch-NNN/` 下补充 batch context、proposal warnings、usage summaries、validation links、benchmark links 和 repair proposal links。
- 添加 `code-task execute --config`，支持读取 `[execute]`、`[models.code_task]` 和 `[budget]`，让模型路由和预算控制可以放进 TOML，而不是堆很长的 CLI 参数。

### Changed

- `code-task execute` 现在可以按配置把 work-plan / patch-plan、edit proposal、repair 分别路由到 planner/editor/repair 模型。
- Active work-item batch 会限制 LLM edit proposal 只能修改该 batch 的 target files；tests 和 benchmark 文件仍然作为只读证据。
- Validation、benchmark 和 repair 步骤现在会更新 active batch state，便于检查中断、失败和修复尝试。
- 超大或超预算模型输出会被 normalizer 转成 warnings / rejected edits，而不是隐式应用。
- 文档现在明确展示正确的 reviewed executor 顺序：批准 plan 后使用 `execute --to-step propose-edits` 生成 proposal，并补充 missing proposal、benchmark regression、repair proposal、exact-text patch failure、large-edit approval 和本地 `uv` cache 权限问题的排错说明。
- Repair 和 edit proposal 现在会拒绝把 unified-diff 片段误写进结构化 `old` / `new` JSON 字段的 edit；`apply-edits` 遇到 patch validation failure 时会输出可读错误，而不是直接打印 Python traceback。

## 2026-05-20

### Added

- 添加 Day 8 V2.2 分层 repo-map 产物：`code_task/meta/repo_map.json` 和 `code_task/meta/repo_map_summary.md`。
- Repo-map schema 包含 project、directory、file、symbol、entrypoint、test、benchmark、config 和 prompt-budget 层，同时保留 `codebase_index.json` 兼容旧流程。
- 添加 `simple-ar code-task map`，可以作为独立步骤从当前 workspace 重建 repo-map 产物。
- 添加 `simple-ar code-task locate`，可以从 repo map 中排序 likely editable targets 和 protected read-only evidence。
- 添加 `simple-ar code-task context`，可以在 `code_task/context_packs/context-NNN/` 下生成受预算限制的 prompt context pack。
- 添加 Day 15-16 V2.2 work-plan artifacts：`code_task/work_plan.json` 和 `code_task/work_plan.md`，以及 `simple-ar code-task work-plan`。
- 添加初始 attempt/batch 状态目录：`code_task/attempts/attempt-NNN/batches/batch-NNN/`，以及 `simple-ar code-task batch --work-item W1`。
- 添加 `simple-ar-checks` 和 `scripts/run_checks.py`，支持 `quick`、`code-task`、`pipeline`、`research`、`all` 等分层开发验证组。

### Changed

- Code-task 初始化现在同时写旧 codebase index 和新 repo map；补丁应用后也会同步重建两个产物。
- Code-task 文档现在说明 `map -> locate -> context` 路径，便于大项目在规划/编辑前先缩小上下文。
- Patch planning 现在会在存在 latest context pack 时优先使用它；controlled edit proposal 只读取其中 editable snippets，并继续把保护文件作为 read-only evidence。
- Code-task planning 现在增加更高一层的 work-plan，用于先把宽泛任务拆成小批次，再进入 patch proposal。
- `code-task execute` 现在会在正常路径中包含 work-plan 和 batch setup；除非传 `--no-llm`，否则 work-plan 使用配置好的 LLM。
- 开发文档现在推荐迭代时使用目标测试组，把完整测试发现保留给提交、推送或大范围重构前。
- V2.2 计划和 workspace 文档现在记录 Day 14 真实 LLM smoke 暴露的问题：普通 JSON patch proposal 可能触发很长 completion，因此后续 editor-backend 工作需要加入 bounded proposal contract、context request artifact、多轮 attempt 和 future external coding-agent routing。

## 2026-05-19

### Added

- 添加 V2.2 code-task workspace modes：`copy` 仍是默认模式，`git_worktree` 可为 repo-root git 项目在 `code_task/workspace` 创建 detached worktree。
- 添加实验性 `sparse_copy` workspace mode，支持 include/exclude patterns、data/model/cache/secret-like 路径内置排除，并在 manifest 中记录 pattern/risk。
- 添加 `[workspace]` 配置，以及 standalone 和 embedded code-task 的 workspace mode、source virtualenv reuse、setup hook 记录相关 CLI flags。
- 在 code-task `manifest.json` 中新增结构化 `workspace` section，同时保留旧 `copy` section 以兼容。

### Changed

- Code-task initialization 改为通过 workspace dispatcher，而不是直接调用 copy routine。
- `code-task init` 遇到 workspace 或 task-file 设置问题时，改为输出用户可读的下一步检查清单，而不是原始 Python traceback。
- Codebase indexing 会跳过 `.git`、`.env`、virtualenv 和 cache metadata，避免 worktree mode 把 git metadata 或 secret-like 文件放进模型上下文。
- 默认 edit scope 现在也保护 `.env` 和 secret/credential-looking paths。

## 2026-05-18

### Added

- 为内嵌 `code_task_project` run 添加 research-first task generation：如果没有 task file，`05-design` 会从前面研究产物和紧凑代码摘要生成 `generated_code_task.md`，再由 `06-code` 作为 code-task prompt 使用。

### Changed

- 对 8 阶段内嵌 code-task run，`[code_task].task_file` 变为可选；standalone `simple-ar code-task init` 仍要求 task file。
- 更新 README、usage、workflow 和 CLI reference，区分用户显式 task file 与 research-first generated task file。

## 2026-05-17

### Added

- 添加默认 code-task edit-scope policy，并在 `manifest.json` 中记录受保护 test/benchmark path patterns。
- 添加通用 `code_task_project` experiment template，使 8 阶段 `simple-ar run --to-stage report` 可以复制用户项目、运行 baseline benchmark、应用 LLM-controlled patch、运行 patched benchmark，并把 nested code-task evidence 写入最终报告。
- 添加顶层 `simple-ar run --config` / `resume --config`，支持可复现 TOML-configured research 和 embedded code-task runs。
- 添加 `examples/run_configs/tiny_digits_mlp_pipeline.toml`，作为 config-driven end-to-end code-task pipeline 示例。
- 添加 pipeline code-task options：`--code-task-config`、`--code-root`、`--task-file`、`--benchmark-command`、`--primary-metric`、重复 `--metric-direction` 和 environment policy overrides。
- 添加 Phase 5 failure analysis 对 validation-only failures 的支持。
- 添加 bounded repair proposal metadata，包括 source analysis paths、selected repair context files 和 explicit repair constraints。
- 添加 `simple-ar code-task execute`，作为保守 state-aware orchestrator，在 plan approval 和 proposal review gates 停止。
- 添加 code-task metric comparison 配置：`--primary-metric` 和重复 `--metric-direction METRIC=DIRECTION`。
- 添加 `code-task init --config`，支持 TOML 初始化，包括 metric direction 设置。
- 添加 tiny-digits MLP code-task config 示例。
- 添加 `docs/CLI_REFERENCE.md` 作为专门命令和参数参考。

### Changed

- 泛化内嵌 code-task experiment 准备流程，使旧 toy-spam demo 和用户项目共享 baseline/plan/proposal/apply/validate harness。
- 将内嵌 code-task bridge module 从 `code_task_demo.py` 重命名为 `code_task_experiment.py`。
- Final reports 在 LLM draft 缺少 Code Task Evidence 时，会追加 deterministic Code Task Evidence section。
- Code-task proposals、repairs 和 patch application 默认把 tests 与 benchmark files 作为只读证据。
- Comparison artifacts 记录配置的 metric directions，并把未知 metrics 作为 delta 记录，但不用于 improved/regressed verdict。
- 将 code-task init config parsing 从 `cli.py` 移到 `code_task/config.py`。
- Repair context selection 优先考虑当前 patch 修改的文件，再考虑 traceback/test files，减少误改 tests 的风险。
- `code_task/summary.md` 在 repair proposal 生成后加入 Repair section。
- Patch application 支持同一文件多个有序 edit。
- `code-task execute` 遇到无效 edit proposal 时报告 `patch_apply_failed`，不直接暴露 Python traceback。
- 文档明确 `code-task execute` 是 primitive code-task commands 的便捷层，不取代可审核步骤。
- 将共享 metric parsing 从 `experiment.metrics` 移到顶层 `simple_ar.metrics`。
- 移除未使用的 code-task environment config helper 和旧 unlabelled benchmark artifact fallback。
- 简化 `docs/USAGE.md`，把详细命令/配置表移到 CLI reference。
- Code-task summaries 现在从 outcome 和 next-step section 开始，`simple-ar status` 也展示 summary、metric config 和 comparison deltas。

## 2026-05-16

### Added

- 添加 `code-task probe`，用于 V2.1 environment inspection。
- 添加 `code_task/meta/environment_report.json`，包含 OS、Python、tool、GPU、dependency-file 和 test-directory signals。
- 在 code-task summaries 和 `simple-ar status` 中加入 environment status。
- 添加 `code-task baseline`，在 `code_task/run/baseline/` 捕获 pre-patch benchmark results。
- 添加 code-task `--env-mode current|external` 和 `--python`，用于选择 benchmark command 的解释器。
- 添加轻量 `tiny_digits_mlp_project` 示例，支持无需下载和 GPU 的本地 ML-style benchmark。
- 添加 code-task baseline-vs-patched comparison artifacts。

### Changed

- 在 usage 和 workflow docs 中记录 code-task environment probe。
- 记录 V2.1 code-task environment isolation 方向：current interpreter、explicit external interpreter、per-run venv、shared environment cache 和 Docker。
- 更新 README 和 development docs，反映当前 V2.1 code-task baseline、comparison 和 module structure。
- Code-task benchmark runs 使用 labelled artifact directories（`baseline` 和 `patched`）。
- Benchmark execution reports 记录 selected environment mode 和 Python executable。
- Patch planning 会包含已记录 environment、validation 和 baseline metric context。

## 2026-05-14

### Added

- 添加 README capability boundaries，说明当前 research、report 和 code-task 能力边界。
- 强化 research-only survey report 和 embedded code-task demo report 的 prompt rules。
- 添加 report-bound checks，限制 toy-demo 对 accuracy、effectiveness、feasibility、generalization 的过度声称。
- 添加 fixture/code-task fallback discussion，把 offline fixture synthesis 视为 traceability context，而不是真实文献证据。

### Changed

- 更新 `report_quality.json` 文案，使 metric-table checks 明确只在 parsed metrics 存在时适用。
- 更新文档，解释 guarded LLM report drafting，以及 standalone `code-task` 与 embedded 8-stage demo 的当前边界。

### Verified

- 运行真实 LLM-backed literature-only flow：`synthesize -> report`。
- 运行真实 LLM-backed 8-stage `llm_code_task_toy_spam` demo，包括 patch planning、controlled edit proposal、benchmark execution 和 report generation。

## 2026-05-13

### Added

- 添加 automatic report mode selection：缺少 `results.json` 时生成 literature-only narrative，有结果时使用 experiment sections。
- 为 `simple-ar run` 和 `simple-ar resume` 添加 `--report-mode {auto,research_only,experiment}`。
- 添加 research-only report fallback sections：`Search Scope`、`Thematic Synthesis`、`Approach Patterns`、`Open Questions`、`Limitations`、`Conclusion`。
- 在 `08-report/manifest.json` 中记录 report mode。

### Changed

- 放宽 report stage contract，不再要求 `results.json`，支持 `synthesize -> report`。
- 更新 report LLM prompt，根据 research-only 和 experiment mode 切换结构和规则。
- 在 `docs/USAGE.md` 和 `docs/WORKFLOWS.md` 中记录 report-mode 行为和 synthesize-to-report flow。

## 2026-05-12

### Added

- 添加 `08-report/report_quality.json`，检查 citation provenance、body-cited papers、metric visibility 和 runtime/fallback disclosure。
- 添加 code-task benchmark runs 和 failure analysis 后的 `code_task/summary.md` 自动生成。
- 添加实验性 `llm_code_task_toy_spam` 8-stage experiment template，把更安全的 code-task patch workflow 嵌入 plan/search/read/synthesize/design/code/run/report pipeline。

### Changed

- 重写 `README.md`，整理为更清晰的开源项目入口，包含 setup、environment configuration、quickstart、workflow presets、docs links、reference 和 community。
- 减少 code-task patch IO，移除完整 pre/post workspace manifest artifacts；`applied_edits.json` 只保留修改文件的 before/after hashes。
- 将详细文档整合为 `docs/USAGE.md`、`docs/WORKFLOWS.md` 和 `docs/DEVELOPMENT.md`。
- 移除重叠文档：`docs/CODE_TASK.md`、`docs/RUN_ARTIFACTS.md`、`docs/CLI_AND_CONFIG.md`、`docs/EXTENDING.md`。

### Notes

- `CHANGELOG.md` 保持为按时间记录的开发日志，而不是版本规划文档。
- 规划重的笔记留在 `MDfiles/`，与公开文档分离。

## 2026-05-11

### Added

- 添加 `code-task validate`，提供轻量 Python syntax checks、risky import/call warnings、missing import warnings 和 strict-mode hazard errors。
- 添加 `code-task run`，在 copied workspace 中执行 benchmark command，包含 timeout、stdout/stderr、return code 和 parsed metrics。
- 添加 `code-task analyze-failure`，把最近失败的 benchmark run 转成紧凑 Markdown diagnosis。
- 添加 `code-task repair`，从 failure analysis 生成 bounded repair edit proposal，但不自动应用。
- 添加 `src/simple_ar/code_task/state.py`，集中管理 code-task path、manifest 和 safe workspace path helpers。
- 添加 realistic code-task smoke example：`examples/code_tasks/toy_spam_project`。
- 添加 `tests/test_code_task_examples.py`，验证示例 benchmark 先失败、workspace patch 后通过，并且不修改原始项目。
- 添加初始 code-task usage、artifact layout、CLI/config direction、workflow composition 和 extension guidance 文档。

### Changed

- 将长命令和 artifact 说明从 `README.md` 移入专门 docs。
- Code-task artifacts 保持在 `code_task/workspace` 和 `code_task/meta` 下，避免 run root 膨胀。
- 将 code-task execution artifacts 分组到 `code_task/run`，repair attempts 分组到 `code_task/repairs`。
- 记录未来 CLI/config 方向：底层步骤稳定前保留 primitive commands，再添加 config-driven convenience workflows。

### Verified

- `uv run python -m unittest tests.test_code_task_examples`
- `uv run python -m unittest discover -s tests`

## 2026-05-10

### Added

- 添加 controlled `code-task propose-edits`，用于模型生成 JSON old/new replacements。
- 添加 controlled `code-task apply-edits`，在 `code_task/workspace` 中安全应用 approved replacements。
- 添加 patch artifacts：`code_task/patch.diff`、`code_task/meta/proposed_edits.json`、`code_task/meta/applied_edits.json`。

### Changed

- 更新 code-task status output，展示 patch state 和 changed files。
- 在详细 docs 拆分前更新 README code-task quick usage。

### Verified

- `uv run python -m unittest discover -s tests`

## 2026-05-09

### Added

- 添加初始 `code-task init` workflow，将已有代码库复制到隔离 run workspace。
- 添加 Python-aware `codebase_index.json`，记录 file hashes、role tags、imports、functions、classes、tests 和 entrypoint candidates。
- 添加 `code-task plan`，根据 task、codebase index 和 selected snippets 生成 human-reviewable patch plan。
- 添加 `code-task decide-plan`，记录人工 approval、rejection 或 revision request。

### Changed

- Code-task artifacts 放在 `code_task/workspace` 和 `code_task/meta` 下，而不是向 run root 增加更多文件。

## 2026-05-08

### Added

- 添加本地 artifact inspection：`simple-ar inspect`。
- 添加本地 lexical artifact search：`simple-ar search-artifacts`。
- 默认只做 source-only chunking，`--include-operational` 用于调试 runner metadata。
- 添加 evidence-aware run artifacts：`source_plan.json`、`activity_log.jsonl`、`evidence_ledger.jsonl`。
- 将 retrieval snippets 接入 `read`、`synthesize` 和 `report` 阶段。
- 添加 retrieval controls：`--retrieval-top-k`、`--no-retrieval`。

### Changed

- 改进 report citation checks，使 body citations 和生成的 `references.bib` 保持一致。
- 使用 `--allow-fixture-fallback` 显式控制 fixture fallback。
- Live literature search 顺序更新为 OpenAlex、arXiv，然后 provider-specific cache。

## 2026-05-06

### Added

- 发布 V1 teaching pipeline baseline：
  - `01 plan`
  - `02 search`
  - `03 read`
  - `04 synthesize`
  - `05 design`
  - `06 code`
  - `07 run`
  - `08 report`
- 添加 file-based stage contracts 和 resumable runs。
- 添加 OpenAI-compatible LLM calls，并显示进度和 usage logging。
- 添加 `.env` 配置：API key、base URL、model name 和可选 cost estimates。
- 添加 OpenAlex/arXiv-backed paper metadata 和 local cache support。
- 添加 offline fixture mode，支持 deterministic tests 和 demos。
- 添加基于模板的 `toy_text_classification` 实验生成。
- 添加 subprocess experiment execution，包含 timeout、stdout、stderr、return code 和 parsed metrics。
- 根据已知 paper metadata 生成 deterministic BibTeX。

### Verified

- 覆盖 contracts、pipeline behavior、literature parsing、LLM adapters、report packaging 和 experiment execution 的单元测试。
