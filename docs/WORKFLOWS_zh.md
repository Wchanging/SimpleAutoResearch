# 工作流与产物

[English version](WORKFLOWS.md)

本文说明 SimpleAutoResearch 内部在做什么：工作流预设、pipeline 阶段、artifact 归属和模块边界。它不重复完整文件手册；具体命令和文件树见 [使用与配置](USAGE_zh.md)，命令参数见 [CLI 参考](CLI_REFERENCE_zh.md)，TOML 字段见 [配置参考](CONFIG_REFERENCE_zh.md)。

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
| `search` | `planning/`、`traces/`、`review/`、`papers.jsonl`、`search_meta.json` | 拆解主题、执行检索、去重/筛选 metadata、检查覆盖度，并从配置源收集文献记录。 |
| `read` | `paper_notes.json`, `notes.md` | 把论文 metadata 转成结构化 notes；启用 LLM 时由 LLM 支持。 |
| `synthesize` | `synthesis.md`, `hypothesis.md` | 产出有边界的 synthesis 和可测试假设。 |
| `design` | `experiment_plan.json` | 选择安全实验模板和参数。 |
| `code` | `experiment.py` | 根据模板生成代码，或准备内嵌 code-task harness。 |
| `run` | `results.json`, `stdout.txt`, `stderr.txt` | 执行实验并解析数值指标。 |
| `report` | `report.md`, `references.bib`, `manifest.json`, `report_quality.json` | 写带 citation 的论文式报告；启用 LLM 时由 LLM 支持。 |

## Search 与 LLM 边界

Search 是证据引擎，不只是 metadata lookup。它会收束研究问题、选择 source 顺序、检索和筛选候选文献、检查 coverage、记录 document/full-text 状态、构建本地 chunks/cards，并向后续 report 或 code-task 交付保守的 evidence bridge。

普通运行默认只保留紧凑产物：

```text
02-search/
  papers.jsonl / search_meta.json
  documents/       # 标准化 document records，以及 full-text/cache manifests
  research_index/  # 可迁移 chunks 与本地索引 metadata
  cards/           # paper、claim、method、dataset、code-link cards
  evidence/        # evidence pack、gap、idea、novelty hint、experiment contract
```

当 `[run].debug_artifacts = true` 时，search 还会保留 planning 文件、retrieval traces、screening decisions、coverage-review reports、section tables、只读 tool-context 草案、adapter/governance notes，以及其他诊断产物。

共享加速索引默认放在 run 目录之外的 `.simple_ar_cache/research_index`，按 run/source metadata 组织。run-local 的 PDF 下载缓存和 extracted text 属于可重建内容，可以用 `simple-ar clean` 预览和清理。

LLM 参与是有边界的。research planner 可以使用 deterministic、`auto` 或 LLM 模式；coverage check 和本地 novelty check 只是风险信号，不是原创性证明。`--no-llm` 会让 plan/read/synthesize/report 使用 deterministic fallback 文本。

完整 search-stage 文件树和逐文件说明见 [使用与配置](USAGE_zh.md)。search、cache、parser 和 debug artifact 配置见 [配置参考](CONFIG_REFERENCE_zh.md)。

## Artifact 归属概览

WORKFLOWS 只保留产物归属层面的说明；完整文件树放在 [使用与配置](USAGE_zh.md)。概括来说：

- run 根目录文件（`state.json`、`manifest.json`、`config_snapshot.json`、usage logs，以及可选 artifact indexes）负责 resume state、配置快照和可观测性。
- 阶段目录（`01-plan` 到 `08-report`）各自拥有自己的 contract、report 和稳定 handoff artifact。
- `02-search` 负责 retrieval、document/full-text 状态、本地 chunks、evidence cards 和紧凑 evidence bridge。
- 当 research pipeline 衔接代码执行时，`06-code/code_task_run` 会嵌入与 standalone code task 相同形态的 artifact。
- `08-report` 负责最终报告包：报告正文、references、manifest 和质量检查。

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
