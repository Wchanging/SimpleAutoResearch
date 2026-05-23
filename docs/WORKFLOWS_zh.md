# 工作流与产物

[English version](WORKFLOWS.md)

本文说明 SimpleAutoResearch 内部在做什么：工作流预设、pipeline 阶段、阶段输出和 run artifact 布局。具体命令见 [CLI 参考](CLI_REFERENCE_zh.md)，安装和 walkthrough 见 [使用与配置](USAGE_zh.md)。

## 工作流预设

当前 8 阶段 pipeline 只是一个预设，不是整个架构本身。SimpleAutoResearch 保持 module-first，这样 literature review、code improvement、experiment execution 和 report writing 可以重新组合。

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
-> run baseline -> plan patch -> approve -> propose edits -> apply edits
-> validate -> run patched benchmark -> compare results
-> analyze failure -> repair proposal
```

关键边界：

- 源项目会准备到 `code_task/workspace`。默认 `copy` 会建立受保护物理复制；`git_worktree` 会为 repo-root git 项目创建 detached worktree；实验性 `sparse_copy` 只复制配置的 include patterns，并始终排除 data/model/cache/secret-like 路径。原始代码不会被修改。
- Patch application 必须经过显式人工 approval gate。
- Edit proposal 是保守 old/new replacement，不是自由形式重写。
- 默认 editor backend 是 `controlled_patch`；backend interface 现在已经显式存在，后续外部 agent 可以接到同一套安全和审核 gate 后面。
- 同一个文件可以有多个有序 edit，但每个 `old` block 必须保持唯一匹配；无效 proposal 会在写文件前停止。
- `code-task execute` 可以推进下一步，但会在 plan approval 和 proposal review 处停下，除非用户显式继续。
- Work-plan item 应该是可执行的 implementation batch。executor 在选择第一个 active batch 时会跳过明显的纯分析 item，因此 LLM 生成的“先 inspect 项目”不会意外限制后续 edit 阶段。
- 如果多个已审核 work-plan item 形成小型串行依赖链，且必须一起落地才可运行，比如 feature producer、model consumer 和 config switch，active batch 可以把它们合并。拆分后的计划仍然可见，`batch_state.json.work_item.source_work_item_ids` 和合并后的 `target_files` 会记录实际执行范围。
- benchmark 通过的 repair 不自动等于任务成功。最终是否 improved 要看 `code_task/run/comparison.json`；如果 patched 指标仍低于 baseline，只能说明流程恢复到可运行或超过 benchmark floor，还没有真正完成“提升”目标。
- 当前执行有 workspace isolation 和明确 interpreter policy。支持 `current` 和 `external`；自动创建环境留到后续。`workspace.reuse_source_venv` 可以把 worktree/copy/sparse run 指向 source 项目已有 `.venv` Python，但不会安装依赖。

内置示例：

- `toy_spam_project`：极小规则分类器，适合 patch 和 failure-analysis smoke test。
- `tiny_digits_mlp_project`：基于 NumPy 和 scikit-learn bundled digits dataset 的轻量 MLP，适合无需 GPU/下载的本地 ML benchmark。
- `medium_review_pipeline_project`：多模块 review classifier，入口是 `main.py`，使用 JSON config，运行时有进度输出，任务自然涉及 feature extraction、model scoring 和配置文件之间的联动。

### 3. Research With Experiment：研究衔接实验

适合希望从研究想法走到可执行实验和有结果支撑的报告。

概念流程：

```text
plan -> search -> read -> synthesize -> design experiment
-> template codegen or embedded code-task -> run benchmark -> report
```

当前状态：

- `06-code` 默认生成白名单 template experiment。
- `--experiment-template code_task_project` 是通用内嵌 handoff，会接入 code-task workflow。它接受 `--code-task-config`，也接受显式 `--code-root`、可选 `--task-file` 和 `--benchmark-command`。如果没有 task file，`05-design` 会基于前面研究产物和紧凑代码摘要生成 `generated_code_task.md`。
- `simple-ar run --config ...` 是保持多参数 research/code-task run 可读、可复现的推荐方式。
- `--experiment-template llm_code_task_toy_spam` 仍保留为 bundled smoke-test template。
- 内嵌路径是端到端的：它会构建和 standalone code-task 一致的 repo map / context pack、work plan、attempt/batch 证据，然后在准备好的 workspace 内自动批准 patch plan。standalone code-task 仍是更安全的人工审核路径。
- Report generation 有保护：只有 citation、metric visibility、fixture disclosure 和 toy-demo boundary 检查通过时，才接受 LLM draft。

## 默认 8 阶段 Pipeline

```text
01 plan        限定主题和研究问题
02 search      收集论文元数据
03 read        生成文献笔记
04 synthesize  总结主题并提出假设
05 design      创建实验计划
06 code        生成实验代码或准备内嵌 code task
07 run         执行实验并解析指标
08 report      写带引用的 Markdown 报告
```

| 阶段 | 主要输出 | 目的 |
| --- | --- | --- |
| `plan` | `goal.md`, `problem.md` | 把主题收束成具体研究问题；启用 LLM 时由 LLM 支持。 |
| `search` | `papers.jsonl`, `search_meta.json` | 收集 OpenAlex/arXiv metadata 或 fixture rows。 |
| `read` | `paper_notes.json`, `notes.md` | 把论文 metadata 转成结构化 notes；启用 LLM 时由 LLM 支持。 |
| `synthesize` | `synthesis.md`, `hypothesis.md` | 产出有边界的 synthesis 和可测试假设。 |
| `design` | `experiment_plan.json` | 选择安全实验模板和参数。 |
| `code` | `experiment.py` | 根据模板生成代码，或准备内嵌 code-task harness。 |
| `run` | `results.json`, `stdout.txt`, `stderr.txt` | 执行实验并解析数值指标。 |
| `report` | `report.md`, `references.bib`, `manifest.json`, `report_quality.json` | 写带 citation 的论文式报告；启用 LLM 时由 LLM 支持。 |

## Search 与 LLM 边界

- Live search 先用 OpenAlex，再用 arXiv。未设置 `--strict-search` 时，live 失败后会使用缓存 metadata。
- `--offline-search` 会跳过 live provider，直接使用 fixture metadata。
- `--allow-fixture-fallback` 只在 live 和 cache 都失败后允许 fixture metadata。
- `--no-llm` 会让 plan/read/synthesize/report 使用 deterministic fallback。
- Report drafting 默认 `auto`：有 `results.json` 时使用 experiment sections，否则使用 literature-only structure。

## Research Run Artifacts

一次完整 research run 可能包含这些文件，取决于启用的选项：

```text
runs/<run-id>/
  manifest.json
  pipeline_state.json
  config_snapshot.json
  topic.txt
  llm_usage.jsonl
  llm_usage_summary.json
  artifact_index.json
  artifact_chunks.jsonl
  artifact_search_results.json  # 只有显式 search-artifacts 后才生成
  source_plan.json
  activity_log.jsonl
  evidence_ledger.jsonl
  01-plan/
  02-search/
  03-read/
  04-synthesize/
  05-design/
  06-code/
    code_task_experiment.json
    code_task_run/
  07-run/
  08-report/
```

根目录文件：

- `manifest.json`：stage status 和声明输出。
- `pipeline_state.json`：已完成阶段和 resume 的下一阶段。
- `config_snapshot.json`：本次运行配置快照。
- `llm_usage.jsonl`：每次成功 LLM request 一行。
- `llm_usage_summary.json`：token 聚合和可选费用估算。
- `artifact_index.json`：由 `inspect` 或 `search-artifacts` 生成的本地 artifact index。
- `artifact_chunks.jsonl`：用于本地 retrieval 的 line-addressable chunks。
- `artifact_search_results.json`：最近一次 artifact search 结果，只由 artifact search 命令生成。
- `source_plan.json`：描述每个阶段应该参考哪些 artifacts 的 source plan。
- `activity_log.jsonl`：source planning 和 retrieval actions 的结构化日志。
- `evidence_ledger.jsonl`：阶段使用的 snippets，包含 path 和 line range。
- `05-design/generated_code_task.md`：仅当内嵌 `code_task_project` 没有 task file 时生成。
- `05-design/generated_code_task_meta.json`：生成 task file 的 provenance。
- `06-code/code_task_experiment.json`：内嵌 code-task templates 的阶段产物。

嵌套 code-task 文件：

- `06-code/code_task_run/code_task/summary.md`：嵌套 code-task outcome 汇总。
- `06-code/code_task_run/code_task/meta/repo_map.json`：准备后 workspace 的分层 repo map。
- `06-code/code_task_run/code_task/context_packs/context-001/`：planning/editing 使用的 prompt-ready context pack。
- `06-code/code_task_run/code_task/work_plan.md`：批次式 implementation plan。
- `06-code/code_task_run/code_task/attempts/attempt-001/batches/batch-001/batch_state.json`：当前内嵌 batch 状态。
- `06-code/code_task_run/code_task/patch_plan.md`：由 pipeline 自动批准的 LLM patch plan。
- `06-code/code_task_run/code_task/meta/proposed_edits.json`：受控 old/new edit proposal。
- `06-code/code_task_run/code_task/patch.diff`：在准备好的 workspace 中应用的补丁。
- `06-code/code_task_run/code_task/run/baseline/`：pre-patch benchmark artifacts。
- `06-code/code_task_run/code_task/run/patched/`：patched benchmark artifacts。
- `06-code/code_task_run/code_task/run/comparison.json`：baseline 和 patched 都存在时的 before/after comparison。

Report-stage 文件：

- `08-report/report.md`：最终 Markdown 报告。
- `08-report/references.bib`：报告正文引用论文的 BibTeX。
- `08-report/manifest.json`：报告包和可复现 metadata。
- `08-report/report_quality.json`：citation、metric 和 runtime limits 的规则检查。

## Code Task Artifacts

Code-task artifacts 都放在 `code_task/` 下：

```text
runs/<run-id>/
  manifest.json
  code_task/
    task.md
    summary.md
    work_plan.md
    patch_plan.md
    patch.diff
    workspace/
    meta/
      environment_report.json
      codebase_index.json
      repo_map.json
      repo_map_summary.md
      locate_results.json
      locate_results.md
      hitl_decisions.jsonl
      proposed_edits.json
      applied_edits.json
      validation_report.json
      failure_analysis.md
      llm_usage.jsonl
      llm_usage_summary.json
    context_packs/
      context-001/
        context_pack.json
        prompt_context.md
        selected_snippets.jsonl
    attempts/
      attempt-001/
        attempt_state.json
        batches/
          batch-001/
            batch_state.json
            batch_context.json
            proposed_edits.json
            proposal_warnings.json
    run/
      comparison.json
      baseline/
        execution_report.json
        stdout.txt
        stderr.txt
        metrics.json
      patched/
        execution_report.json
        stdout.txt
        stderr.txt
        metrics.json
        failure_analysis.md
    repairs/
      repair-001/
        proposed_edits.json
```

重要目录：

- `workspace/`：源项目的可编辑副本、worktree 或 sparse subset。
- `meta/`：环境报告、索引、locate results、决策、proposal、applied edit summary、validation report、failure analysis 和 LLM usage。
- `context_packs/`：从 locate results 和 workspace snippets 派生的受限 prompt context pack。
- `attempts/`：用于 bounded implementation / repair loop 的 work-plan attempt 和 batch state。
- `run/`：带 label 的 benchmark stdout/stderr、execution report、parsed metrics、before/after comparison 和 benchmark failure analysis。
- `repairs/`：按 attempt 分组的 bounded repair proposal。

重要用户可见文件：

- `summary.md`：紧凑 outcome、下一步建议、task、patch、validation、benchmark、comparison 和 failure-analysis summary。
- tests 和 benchmark 文件默认受 edit scope 保护。它们可以作为只读证据被索引，但 `propose-edits`、`repair` 和 `apply-edits` 不应修改它们。
- `meta/environment_report.json`：OS/Python/tool/GPU/project probe。
- `meta/repo_map.json`：从 `codebase_index.json` 派生的 project/directory/file/symbol/entrypoint/test/benchmark/config 分层 map。
- `meta/repo_map_summary.md`：人类可读的紧凑 repo-map summary 和 prompt-budget 说明。
- `meta/locate_results.json`：确定性排序后的 editable targets 和 protected read-only evidence。
- `meta/locate_results.md`：便于人工检查的 locate summary。
- `context_packs/context-NNN/context_pack.json`：选择文件、预算、来源 artifact 和省略文件记录。
- `context_packs/context-NNN/prompt_context.md`：按 editable targets / read-only evidence 分组的 prompt-ready Markdown。
- `context_packs/context-NNN/selected_snippets.jsonl`：实际截断后的源码片段，每个文件一行。
- `work_plan.md`：位于 patch plan 之上的批次式 implementation plan。
- `attempts/attempt-NNN/attempt_state.json`：由 work-plan 和 batch outcome 推导的 attempt 生命周期状态。
- `attempts/attempt-NNN/batches/batch-NNN/batch_state.json`：active work item、允许修改的 target files、batch artifacts 和最终 batch state。
- `run/baseline/execution_report.json`：pre-patch benchmark result。
- `run/patched/execution_report.json`：post-patch benchmark result。
- `run/comparison.json`：baseline/patched metric deltas 和保守 verdict。
- `manifest.json.objective`：当 patched benchmark artifact 存在时，从 comparison 派生出的当前任务目标 verdict。它用于区分“代码跑通了”和“指标目标真的提升了”。
- `patch_plan.md`：编辑前供人审核的计划，包含已记录环境、validation 和 baseline context。
- 如果存在 latest context pack，`patch_plan.md` 会记录其路径，并优先使用其中的 selected snippets，而不是旧的 index-only 文件选择。
- `patch.diff`：应用后的补丁，便于 review。
- `meta/proposed_edits.json`：可审核 edit proposal，并记录 editor backend metadata。
- `meta/applied_edits.json`：被修改文件及 before/after hash，并记录实际应用的 proposal path 和 editor backend。对于 repair proposal，这里会指向 `code_task/repairs/repair-NNN/proposed_edits.json`。

## Code-Task 环境策略

环境处理和源码隔离是两件事：

- 源码隔离：用户代码会先准备到 `code_task/workspace`，再应用任何补丁。默认 `copy` 是物理复制；`git_worktree` 是 detached worktree；`sparse_copy` 是实验性 allowlist copy。
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
