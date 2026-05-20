# Changelog

[English version](CHANGELOG.md)

本文按倒序记录用户可见的项目变化。规划笔记和设计理由主要放在 `docs/` 和 `MDfiles/`；这里尽量保持为普通 changelog，而不是长期计划文档。

## 2026-05-20

### Added

- 添加 Day 8 V2.2 分层 repo-map 产物：`code_task/meta/repo_map.json` 和 `code_task/meta/repo_map_summary.md`。
- Repo-map schema 包含 project、directory、file、symbol、entrypoint、test、benchmark、config 和 prompt-budget 层，同时保留 `codebase_index.json` 兼容旧流程。
- 添加 `simple-ar code-task map`，可以作为独立步骤从当前 workspace 重建 repo-map 产物。
- 添加 `simple-ar code-task locate`，可以从 repo map 中排序 likely editable targets 和 protected read-only evidence。
- 添加 `simple-ar code-task context`，可以在 `code_task/context_packs/context-NNN/` 下生成受预算限制的 prompt context pack。
- 添加 `simple-ar-checks` 和 `scripts/run_checks.py`，支持 `quick`、`code-task`、`pipeline`、`research`、`all` 等分层开发验证组。

### Changed

- Code-task 初始化现在同时写旧 codebase index 和新 repo map；补丁应用后也会同步重建两个产物。
- Code-task 文档现在说明 `map -> locate -> context` 路径，便于大项目在规划/编辑前先缩小上下文。
- Patch planning 现在会在存在 latest context pack 时优先使用它；controlled edit proposal 只读取其中 editable snippets，并继续把保护文件作为 read-only evidence。
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
- 添加 `docs/CODE_TASK_WORKSPACE.md`，记录 V2.1 workspace/copy 数据流、隐含假设和 V2.2 workspace-mode 替换点。

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
