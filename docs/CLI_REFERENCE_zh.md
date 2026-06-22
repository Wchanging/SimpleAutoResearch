# CLI 参考

[English version](CLI_REFERENCE.md)

本文是 SimpleAutoResearch 的命令速查手册，只关注命令语法、参数、产物和少量边界说明。

- 安装和实践流程：[使用与配置](USAGE_zh.md)
- 工作流概念和产物结构：[工作流与产物](WORKFLOWS_zh.md)
- TOML 配置规范和示例：[配置参考](CONFIG_REFERENCE_zh.md)

## 命令总览

| 命令 | 用途 |
| --- | --- |
| `simple-ar run` | 启动新的 8 阶段 research pipeline。 |
| `simple-ar resume` | 继续已有 research pipeline run。 |
| `simple-ar status` | 查看 research run 或 code-task run 状态。 |
| `simple-ar tools ...` | 导出 tool schema、调用 run-local tool，或通过 MCP stdio 暴露只读 tools。 |
| `simple-ar inspect` | 为某次 run 构建本地 artifact index。 |
| `simple-ar search-artifacts` | 搜索已经索引的 run artifacts。 |
| `simple-ar clean` | 预览并清理某次 run 的可重建缓存。 |
| `simple-ar code-task ...` | 在隔离可编辑 workspace 中处理已有代码项目。 |

## Research Pipeline

### `simple-ar run`

**一句话说明**：启动一次新的 8 阶段科研流程。

**语法用法**：

```bash
uv run simple-ar run --topic "agent simulation" --to-stage report
uv run simple-ar run --config examples/research_report/configs/research_report.toml
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `--config PATH` | path | 可复现 run 的 TOML 配置；显式 CLI 参数会覆盖配置值。 |
| `--topic TEXT` | string | 研究主题。除非 `[run].topic` 已设置，否则必填。 |
| `--output-root DIR` | path | 时间戳 run 目录创建位置。 |
| `--from-stage NAME` | stage | 起始阶段，默认 `plan`。 |
| `--to-stage NAME` | stage | 结束阶段，默认 `report`。 |
| `--model NAME` | string | LLM 模型覆盖。 |
| `--llm-workers N` | int | 支持阶段的并发 LLM worker 数。 |
| `--max-papers N` | int | 文献 metadata 数量上限。 |
| `--search-query TEXT` | string | 覆盖生成的搜索 query。 |
| `--experiment-template NAME` | string | 实验模板，例如 `code_task_project`。 |
| `--experiment-timeout N` | int | 实验子进程 timeout。 |
| `--report-mode MODE` | enum | `auto`、`research_only` 或 `experiment`。 |
| `--no-llm` | flag | 尽可能使用 deterministic fallback，不调用 LLM。 |
| `--offline-search` | flag | 跳过 live literature providers。 |
| `--allow-fixture-fallback` | flag | live/cache 失败后允许 fixture metadata。 |
| `--strict-search` | flag | 搜索失败时直接失败，不使用 cache/fixture fallback。 |
| `--no-retrieval` | flag | 禁用本地 artifact retrieval 上下文。 |
| `--retrieval-top-k N` | int | 本地 artifact chunk 检索数量。 |
| `--quiet` | flag | 减少进度日志输出。 |
| `--overwrite-stage-artifacts` | flag | 关闭 `06-code` / `07-run` 重跑时的默认归档保护。只有旧代码/运行产物可丢弃时才使用。 |

**内嵌 code-task 参数**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `--code-task-config PATH` | path | `--experiment-template code_task_project` 使用的 code-task TOML。 |
| `--code-root DIR` | path | 准备到内嵌 code-task workspace 的源项目。 |
| `--task-file PATH` | path | 任务文件。内嵌 run 可省略；省略时 `05-design` 会生成任务。 |
| `--benchmark-command TEXT` | string | patch 前后运行的 benchmark command。 |
| `--code-task-name TEXT` | string | 内嵌 code-task 实验展示名。 |
| `--code-task-max-file-bytes N` | int | 内嵌 copy/sparse 模式最大复制文件大小。 |
| `--code-task-workspace-mode MODE` | enum | `auto`、`copy`、`git_worktree`、`sparse_copy`，或 greenfield code-task 使用的 `empty`。`auto` 优先 git worktree，失败时降级 copy。 |
| `--code-task-workspace-reuse-source-venv` | flag | 使用检测到的 source `.venv` Python。 |
| `--code-task-workspace-setup-hook TEXT` | string | 为未来 managed environment 记录 setup command。 |
| `--code-task-env-mode MODE` | enum | `current` 或 `external`。 |
| `--code-task-python PATH` | path | external env mode 的 Python 路径。 |
| `--primary-metric NAME` | string | 对比使用的主指标。 |
| `--metric-direction NAME=DIRECTION` | repeatable | 指标方向：`higher`、`lower`、`resource` 或 `ignore`。 |

**生成产物**：

- `runs/<run-id>/manifest.json`
- `runs/<run-id>/config_snapshot.json`
- `01-plan/`、`02-search/`、`08-report/` 等阶段目录

**注意**：

真实运行参数较多时，优先使用 TOML。完整字段见
[配置参考](CONFIG_REFERENCE_zh.md#完整-pipeline-config)。

### `simple-ar resume`

**一句话说明**：继续已有 research pipeline run。

**语法用法**：

```bash
uv run simple-ar resume runs/<run-id>
uv run simple-ar resume runs/<run-id> --from-stage report --report-mode research_only
uv run simple-ar resume runs/<run-id> --from-stage report --to-stage report --report-output-mode variant --report-output-label survey-v2
```

**参数表**：

`resume` 接收 `RUN_DIR`，并支持大多数 `run` 参数作为覆盖，包括
`--config`、阶段范围、LLM/search/report 参数、报告写入策略和内嵌 code-task 参数。

常用报告写入参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `--report-output-mode` | choice | `overwrite`、`archive` 或 `variant`。`variant` 会写入 `08-report/variants/<label>/`，不替换当前主报告。 |
| `--report-output-label` | string | report archive/variant 的可选目录标签。 |
| `--overwrite-stage-artifacts` | flag | 关闭 `06-code` / `07-run` 重跑时的默认归档保护。 |

**生成产物**：

- 更新已有 run 目录
- 在 `manifest.json` 中追加阶段执行状态

**注意**：

如果存在 `config_snapshot.json`，未传入的值会尽量沿用原 run 配置。

### `simple-ar status`

**一句话说明**：查看 research run 或 code-task run 的紧凑状态。

**语法用法**：

```bash
uv run simple-ar status runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | research run 或 code-task run 目录。 |

**生成产物**：

- 无文件写入；只打印状态

**注意**：

对 code-task run，会显示环境、计划、补丁、验证、benchmark、指标对比和 repair 状态。

## Tool 与 MCP

### `simple-ar tools schema`

**一句话说明**：导出真实已注册 tool 的 MCP 或 OpenAI function-tool schema。

**语法用法**：

```bash
uv run simple-ar tools schema --format mcp
uv run simple-ar tools schema --format openai --output tool_schema.json
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `--format` | enum | `mcp` 或 `openai`，默认 `mcp`。 |
| `--output PATH` | path | 可选输出文件；省略时打印到 stdout。 |

### `simple-ar tools call`

**一句话说明**：调用一个 run-local 只读 tool，并写入紧凑 trace。

**语法用法**：

```bash
uv run simple-ar tools call runs/<run-id> list_experiment_artifacts
uv run simple-ar tools call runs/<run-id> search_generated_code --args-json '{"query":"run_experiment","max_matches":10}'
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | 已有 run 目录。 |
| `TOOL_NAME` | string | 已注册 tool 名称。 |
| `--args-json JSON` | object | JSON object 形式的 tool 参数。 |
| `--args-file PATH` | path | 从 JSON 文件读取 tool 参数。PowerShell 等 shell 中 inline JSON 不好转义时建议使用。 |
| `--debug-payloads` | flag | 保留更大的 trace payload；默认 trace 保持紧凑。 |

**生成产物**：

- stdout 上的 tool result JSON；
- `RUN_DIR/tools/tool_trace.jsonl`。

### `simple-ar tools serve-mcp`

**一句话说明**：通过 MCP stdio 暴露 run-local 只读 tools。

**语法用法**：

```bash
uv run simple-ar tools serve-mcp runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | tools 可检查的已有 run 目录。 |
| `--debug-payloads` | flag | 保留更大的 trace payload。 |

**注意**：

- 当前 server methods：`initialize`、`ping`、`tools/list`、`tools/call`；
- 默认只暴露真实注册的只读 experiment tools；
- 这个命令不会启用写文件、shell、network 或 dependency-install tool。

## Artifact Tools

### `simple-ar inspect`

**一句话说明**：索引并总结本地 run artifacts。

**语法用法**：

```bash
uv run simple-ar inspect runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | 要索引的 run 目录。 |

**生成产物**：

- `artifact_index.json`
- `artifact_chunks.jsonl`

**注意**：

用户可读产物和运行管理 metadata 会区分索引。

### `simple-ar search-artifacts`

**一句话说明**：使用 lexical retrieval 搜索已经索引的 run artifacts。

**语法用法**：

```bash
uv run simple-ar search-artifacts runs/<run-id> "accuracy" --top-k 5
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | run 目录。 |
| `QUERY` | string | 搜索 query。 |
| `--top-k N` | int | 返回结果数量，默认 `8`。 |
| `--include-operational` | flag | 同时搜索 manifest、runner metadata 等运行管理文件。 |

**生成产物**：

- 无文件写入；打印匹配片段和来源路径

**注意**：

如果 index 不存在或过期，先运行 `inspect`。

### `simple-ar clean`

**一句话说明**：预览并清理某个 run 的可重建缓存，同时保留报告、manifest、paper metadata、read 阶段 Paper Brief、coverage report 和 `research_index/chunks.jsonl` 等审计产物。

**语法用法**：
```bash
uv run simple-ar clean runs/<run-id>
uv run simple-ar clean runs/<run-id> --yes
uv run simple-ar clean runs/<run-id> --all-caches
uv run simple-ar clean --shared-index
uv run simple-ar clean --shared-cache
```

**参数表**：
| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | 要清理的 run 目录。使用 `--shared-index` 或 `--shared-cache` 时可省略。 |
| `--yes` | flag | 跳过交互式 `yes` 确认，直接删除预览中列出的目标。 |
| `--all-caches` | flag | 在更强警告后，删除该 run 下所有已知可重建缓存、索引和 context artifacts。 |
| `--shared-index` | flag | 强清理：清空跨 run/test 共享的 research index store。 |
| `--shared-cache` | flag | 最强共享清理：同时清空共享 research index、literature provider cache 和外部 agent handoff archives。 |
| `--index-root PATH` | path | `--shared-index` / `--shared-cache` 使用的共享索引根目录；默认 `SIMPLE_AR_RESEARCH_INDEX_ROOT` 或 `.simple_ar_cache/research_index`。 |
| `--literature-cache-root PATH` | path | `--shared-cache` 使用的 literature cache 根目录；默认 `.simple_ar_cache/literature`。 |
| `--allow-external-index-root` | flag | 允许 shared cleanup 清理当前 workspace 外的路径。 |

**生成产物**：
- 先打印 Rich tree 预览：红色为将删除的缓存，绿色为会保留的审计产物。
- 删除 `02-search/documents/fulltext_cache/`、`02-search/documents/extracted_text/`、`artifact_search_results.json` 等可重建缓存。
- 如果 `index_meta.json` 指向当前 workspace 下的共享 SQLite research index，会删除该 run 对应的 SQLite rows。
- 使用 `--all-caches` 时，还会删除可重建的 research index、artifact search index/chunks、code-task repo map、locate outputs 和 context packs。

**注意**：
`clean` 不会删除 run 目录本身，也不会删除报告、manifest、papers、`fulltext_extraction.json` 等解析审计文件、read 阶段 Paper Brief、synthesis brief、已保留的 debug coverage 和 portable chunks。`--all-caches` 会把 portable chunks 也视为可重建索引缓存删除，但仍会保留最终报告、metadata、manifest 和 benchmark outputs。

`--shared-index` 比 `--all-caches` 更强：它会清空跨 run 共享的 SQLite/LanceDB 加速索引，后续运行需要重新构建索引状态。它不触碰 run 目录，因此 run-local 审计产物仍会保留。

`--shared-cache` 更强：它会同时清空共享 research index、`.simple_ar_cache/literature` 和 `.simple_ar_cache/agent_handoff_archives`。后续运行可能需要重新请求 literature provider、重新构建本地索引，并且不再保留旧的外部 agent handoff transcripts。

## Code Task Commands

Code-task 命令会把代码任务准备到 `runs/<run-id>/code_task/workspace`。已有项目默认 `auto`：优先创建 detached git worktree，Git 条件不满足时降级为受保护 copy；显式 `git_worktree` 失败时会给出可操作 checklist，而不是静默降级。greenfield 任务会从 empty workspace 开始生成项目。后续修改或生成只发生在隔离 workspace 的 project root 中，不会直接修改原始项目。

正常用户优先看“高级编排命令”。“底层原语命令”通常由 `execute` 自动调用，主要用于调试、学习或细粒度人工介入。

### 高级编排命令

#### `simple-ar code-task init`

**一句话说明**：创建 code-task run，准备可编辑 workspace，并构建初始代码索引。

**语法用法**：

```bash
uv run simple-ar code-task init --config examples/code_task_medium_review/configs/code_task.toml
uv run simple-ar code-task init --code-root path/to/project --task-file task.md --benchmark-command "python main.py"
uv run simple-ar code-task init --kind greenfield --task-file task.md --benchmark-command "python generated_project/main.py"
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `--config PATH` | path | init 设置 TOML；CLI 参数覆盖配置值。 |
| `--kind MODE` | enum | `existing_project` 表示已有项目 patch；`greenfield` 表示从零生成项目。 |
| `--code-root DIR` | path | 源项目。`existing_project` 必填；`greenfield` 仅在需要 scaffold/source root 时填写。 |
| `--task-file PATH` | path | Markdown/text 任务描述。除非配置中已设置，否则必填。 |
| `--output-root DIR` | path | code-task run 创建位置。 |
| `--name TEXT` | string | run 名称后缀。 |
| `--benchmark-command TEXT` | string | 在 workspace 中 patch 前后运行的命令。 |
| `--primary-metric NAME` | string | before/after verdict 使用的主指标。 |
| `--metric-direction NAME=DIRECTION` | repeatable | 指标方向：`higher`、`lower`、`resource` 或 `ignore`。 |
| `--env-mode MODE` | enum | `current` 或 `external`。 |
| `--python PATH` | path | `--env-mode external` 的 Python。 |
| `--workspace-mode MODE` | enum | `auto`、`copy`、`git_worktree`、`sparse_copy` 或 `empty`。`greenfield` 默认 `empty`，已有项目默认 `auto`。 |
| `--workspace-include GLOB` | repeatable | `sparse_copy` include pattern。 |
| `--workspace-exclude GLOB` | repeatable | `sparse_copy` 额外 exclude pattern。 |
| `--workspace-reuse-source-venv` | flag | 检测并复用 source `.venv` Python。 |
| `--workspace-setup-hook TEXT` | string | 记录 setup command；init 不执行它。 |
| `--max-file-bytes N` | int | copy/sparse 模式最大复制文件大小，`0` 表示禁用。 |

**生成产物**：

- `code_task/manifest.json`
- `code_task/task.md`
- `code_task/workspace/`
- `code_task/meta/codebase_index.json`
- `code_task/meta/repo_map.json`
- `code_task/meta/repo_map_summary.md`

**注意**：

可复用设置建议写入 TOML，见 [配置参考](CONFIG_REFERENCE_zh.md#standalone-code-task-config)。

#### `simple-ar code-task execute`

**一句话说明**：根据当前 run 产物推进到下一个安全停止点。

**语法用法**：

```bash
uv run simple-ar code-task execute runs/<run-id> --config examples/code_task_medium_review/configs/code_task.toml
uv run simple-ar code-task execute runs/<run-id> --to-step propose-edits
uv run simple-ar code-task execute runs/<run-id> --apply-proposed-edits --timeout 60
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--config PATH` | path | 可选 TOML，用于模型路由、预算和运行设置。 |
| `--to-step STEP` | enum | 最多运行到 `probe`、`baseline`、`work-plan`、`batch`、`plan`、`propose-edits`、`apply-edits`、`review`、`validate`、`run`、`analyze-failure` 或 `repair`。 |
| `--dry-run` | flag | 只打印下一步动作，不写产物。 |
| `--model NAME` | string | LLM 步骤模型覆盖。 |
| `--no-llm` | flag | 尽可能使用 deterministic fallback。 |
| `--timeout N` | int | benchmark timeout。 |
| `--baseline-policy MODE` | enum | 已有项目 baseline 策略：`auto`、`run`、`skip`、`provided` 或 `none`。昂贵 baseline 可用 `skip`/`none` 跳过，或用 `provided` 记录已有指标。 |
| `--baseline-metrics-file PATH` | path | `--baseline-policy provided` 时读取的 JSON 或 `metric=0.82` 文本指标文件。 |
| `--yes` | flag | 普通 execute 模式下自动批准 inline 审核门；与 `--interactive` 一起使用时，自动继续 primitive prompts。只有明确接受审核风险、想自动化跑通时才使用。 |
| `--interactive` | flag | 调试模式：逐个 primitive step 确认，而不是连续运行到下一个审核门。 |
| `--no-review-inline` | flag | 禁用 inline 审核提示，在审核门直接停止。 |
| `--skip-validation` | flag | 静态验证未通过时仍运行 benchmark。 |
| `--strict-validation` | flag | 将较高风险 validation warning 视为 error。 |
| `--validation-max-file-bytes N` | int | 静态验证扫描文件大小上限。 |
| `--apply-proposed-edits` | flag | plan 批准后应用已审核的 `proposed_edits.json`。 |
| `--allow-large-edits` | flag | 允许已审核、超过 normal 预算的较大 proposal。 |
| `--allow-planning-fallback` | flag | LLM 规划重试失败后，允许写入 deterministic fallback work/patch plan。 |
| `--llm-retry-attempts N` | int | work-plan / patch-plan 的 LLM 尝试次数。 |
| `--repair-rounds N` | int | 失败后的 bounded repair proposal 轮数。 |
| `--max-files N` | int | LLM 步骤上下文文件预算。 |
| `--max-source-chars-per-file N` | int | 单文件 source 上下文预算。 |
| `--env-mode MODE` | enum | `current` 或 `external`。 |
| `--python PATH` | path | external env mode 的 Python。 |

**生成产物**：

- `code_task/work_plan.md` 和 `work_plan.json`
- `code_task/attempts/attempt-*/batches/batch-*/batch_state.json`
- `code_task/patch_plan.md`
- `code_task/meta/proposed_edits.json`
- `code_task/meta/applied_edits.json`
- `code_task/meta/review_report.json` 和 `review_report_post_run.json`
- `code_task/meta/validation_report.json`
- `probe` 后的 `code_task/meta/resource_probe.json` 和 `resource_decision.json`
- `code_task/memory/task_memory.md`、`compressed_memory.md` 和 `review_findings.jsonl`
- `code_task/run/baseline/`、`code_task/run/patched/`、`code_task/run/comparison.json`
- `code_task/summary.md`

**注意**：

`execute` 会保留审核点，但不强制每个审核门都另开一条命令。真实终端里，它会对
`patch_plan.md`、`proposed_edits.json` 或 large-edit approval 打印黄色 Rich 审核面板，
并询问是否继续；非交互 shell 中会干净停在审核门，除非显式传入 `--yes`。中断后重跑时，
已完成步骤会显示为 skipped。只有调试 primitive step 时才建议使用 `--interactive`，
并可搭配 `--yes` 自动继续这些 primitive prompts。普通 execute 模式下的 `--yes`
会自动批准审核门，只应在你明确想自动审批 plan/proposal 时使用。使用
`--no-review-inline` 可恢复“停住、下次再跑”的旧行为。完整运行流程见
[使用与配置](USAGE_zh.md#推荐路径toml--execute)。

如果 LLM work-plan 或 patch-plan 返回了无法解析的 JSON，`execute` 会停在
`llm_planning_failed`，并且不会写入 offline fallback plan。此时直接重跑同一条
`execute` 命令即可重新尝试模型调用；如果你明确接受 deterministic plan，再使用
`--no-llm` 或 `--allow-planning-fallback`。

补丁应用后，`execute` 会先运行结构化 reviewer，再进入静态验证；patched
benchmark 完成后还会再运行一次 post-run reviewer。阻塞性发现会写入
`code_task/memory/`，后续 repair 可以直接利用这些失败证据。

传入 `--config PATH` 时，`execute` 也会读取 standalone code-task 的
`[implementation]` 和 `[resource]`。Greenfield 任务正是通过这里选择本地
backend 或显式的 Codex / Claude Code / OpenCode handoff，而不是新增一套
provider-specific CLI 参数。

#### `simple-ar code-task decide-plan`

**一句话说明**：记录当前 patch plan 的人工审核决定。

**语法用法**：

```bash
uv run simple-ar code-task decide-plan runs/<run-id> --decision approve
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--decision VALUE` | enum | `approve`、`reject` 或 `revise`，必填。 |
| `--note TEXT` | string | 可选审核备注。 |
| `--reviewer TEXT` | string | 审核人标签，默认 `user`。 |

**生成产物**：

- 更新 `manifest.json` 中的 plan decision 状态

**注意**：

当 patch plan 不应继续生成 proposal 时，使用 `reject` 或 `revise`。

### 底层原语命令

以下命令通常由 `execute` 自动调用。需要手动控制或排查某一步时再直接运行。

#### `simple-ar code-task map`

**一句话说明**：从 editable workspace 重建 repo-map 产物。

**语法用法**：

```bash
uv run simple-ar code-task map runs/<run-id> --show-summary
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--no-refresh-index` | flag | 复用已有 `codebase_index.json`。 |
| `--show-summary` | flag | 打印 `repo_map_summary.md`。 |

**生成产物**：

- `code_task/meta/codebase_index.json`
- `code_task/meta/repo_map.json`
- `code_task/meta/repo_map_summary.md`

**注意**：

确定性命令，不调用 LLM。

#### `simple-ar code-task locate`

**一句话说明**：从 repo map 中排序可能相关的可编辑文件和只读证据文件。

**语法用法**：

```bash
uv run simple-ar code-task locate runs/<run-id> --query "improve classifier"
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--query TEXT` | string | locate query，默认使用 `code_task/task.md`。 |
| `--top-k N` | int | 每组候选数量，默认 `8`。 |
| `--refresh-map` | flag | 排序前重建 index 和 repo map。 |
| `--no-read-only` | flag | 省略受保护的只读证据文件。 |
| `--show-summary` | flag | 打印 `locate_results.md`。 |

**生成产物**：

- `code_task/meta/locate_results.json`
- `code_task/meta/locate_results.md`

**注意**：

tests 和 benchmark 可作为计划证据，但默认不可编辑。

#### `simple-ar code-task context`

**一句话说明**：构建受限的 prompt-ready context pack。

**语法用法**：

```bash
uv run simple-ar code-task context runs/<run-id> --max-files 8 --max-total-chars 20000
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--query TEXT` | string | locate query，默认使用任务文件。 |
| `--top-k N` | int | locate 候选预算。 |
| `--max-files N` | int | 纳入 snippet 的文件上限。 |
| `--max-source-chars-per-file N` | int | 单文件 snippet 字符预算。 |
| `--max-total-chars N` | int | 总 snippet 字符预算。 |
| `--refresh-map` | flag | 打包前重建 repo map。 |
| `--show-prompt` | flag | 打印 `prompt_context.md`。 |

**生成产物**：

- `code_task/context_packs/context-NNN/context_pack.json`
- `code_task/context_packs/context-NNN/prompt_context.md`
- `code_task/context_packs/context-NNN/selected_snippets.jsonl`

**注意**：

不调用 LLM，也不修改文件。

#### `simple-ar code-task probe`

**一句话说明**：检查 workspace runtime 和项目环境信号。

**语法用法**：

```bash
uv run simple-ar code-task probe runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--env-mode MODE` | enum | `current` 或 `external`。 |
| `--python PATH` | path | external env mode 的 Python。 |

**生成产物**：

- `code_task/meta/environment_report.json`

**注意**：

`probe` 不安装依赖，也不运行项目 benchmark。

#### `simple-ar code-task baseline`

**一句话说明**：patch 前运行记录的 benchmark。

**语法用法**：

```bash
uv run simple-ar code-task baseline runs/<run-id> --timeout 60
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--command TEXT` | string | 覆盖本次 benchmark command。 |
| `--timeout N` | int | benchmark timeout。 |
| `--skip-validation` | flag | 静态验证未通过时仍运行。 |
| `--env-mode MODE` | enum | `current` 或 `external`。 |
| `--python PATH` | path | external env mode 的 Python。 |

**生成产物**：

- `code_task/run/baseline/execution_report.json`
- `code_task/run/baseline/stdout.txt`
- `code_task/run/baseline/stderr.txt`
- `code_task/run/baseline/metrics.json`

**注意**：

benchmark command 在 `code_task/workspace` 中运行。

#### `simple-ar code-task work-plan`

**一句话说明**：生成面向批次执行的 implementation work plan。

**语法用法**：

```bash
uv run simple-ar code-task work-plan runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--model NAME` | string | 模型覆盖。 |
| `--no-llm` | flag | 使用 fallback planning。 |
| `--force` | flag | 重新生成已有 work plan。 |
| `--allow-planning-fallback` | flag | LLM work planning 失败时允许 deterministic fallback。 |
| `--llm-retry-attempts N` | int | work planning 的 LLM 尝试次数。 |
| `--max-files N` | int | planning 上下文文件预算。 |
| `--max-source-chars-per-file N` | int | 单文件 source 上下文预算。 |

**生成产物**：

- `code_task/work_plan.json`
- `code_task/work_plan.md`

**注意**：

work-plan 中的 target files 会进入后续 edit-scope 检查。

#### `simple-ar code-task batch`

**一句话说明**：为某个 work-plan item 创建 attempt/batch 状态目录。

**语法用法**：

```bash
uv run simple-ar code-task batch runs/<run-id> --work-item W1
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--work-item ID` | string | work-plan item id，例如 `W1`，必填。 |
| `--attempt-id ID` | string | 可选 attempt id，例如 `attempt-001`。 |
| `--force` | flag | 即使已有 batch，也创建新 batch。 |

**生成产物**：

- `code_task/attempts/attempt-NNN/attempt_state.json`
- `code_task/attempts/attempt-NNN/batches/batch-NNN/batch_state.json`

**注意**：

`batch` 不调用 LLM，也不修改文件。

#### `simple-ar code-task plan`

**一句话说明**：为 active batch 生成可人工审核的 patch plan。

**语法用法**：

```bash
uv run simple-ar code-task plan runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--model NAME` | string | 模型覆盖。 |
| `--no-llm` | flag | 使用 fallback plan。 |
| `--force` | flag | 重新生成已有 plan。 |
| `--allow-planning-fallback` | flag | LLM patch planning 失败时允许 deterministic fallback。 |
| `--llm-retry-attempts N` | int | patch planning 的 LLM 尝试次数。 |
| `--max-files N` | int | 上下文文件预算。 |
| `--max-source-chars-per-file N` | int | 单文件 source 上下文预算。 |

**生成产物**：

- `code_task/patch_plan.md`

**注意**：

生成 edit proposal 前应先运行 `decide-plan`。

#### `simple-ar code-task propose-edits`

**一句话说明**：请求模型生成受控 old/new text edits。

**语法用法**：

```bash
uv run simple-ar code-task propose-edits runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--model NAME` | string | 模型覆盖。 |
| `--no-llm` | flag | 写入 deterministic empty proposal。 |
| `--force` | flag | 重新生成已有 proposal。 |
| `--max-files N` | int | 可编辑上下文文件预算。 |
| `--max-source-chars-per-file N` | int | 单文件 source 上下文预算。 |
| `--allow-large-edits` | flag | 人工审核后接受较大但仍受限的 proposal。 |

**生成产物**：

- `code_task/meta/proposed_edits.json`
- `code_task/meta/proposal_warnings.json`
- 如果存在 active batch，也会写入 batch 级 proposal 产物

**注意**：

proposal 是结构化 JSON，不是 unified diff。

#### `simple-ar code-task apply-edits`

**一句话说明**：在 workspace 中安全应用受控 old/new edits。

**语法用法**：

```bash
uv run simple-ar code-task apply-edits runs/<run-id>
uv run simple-ar code-task apply-edits runs/<run-id> --edits-file runs/<run-id>/code_task/repairs/repair-001/proposed_edits.json
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--edits-file PATH` | path | 指定要应用的 proposal 文件。 |
| `--allow-unapproved-plan` | flag | 本地测试/demo 时绕过 plan approval。 |
| `--allow-large-edits` | flag | 应用已审核、需要 large-edit approval 的 proposal。 |

**生成产物**：

- 修改 `code_task/workspace` 下的文件
- `code_task/meta/applied_edits.json`
- `code_task/patch.diff`

**注意**：

写文件前会检查路径、edit scope、old text 匹配和 large-edit 限制。

#### `simple-ar code-task validate`

**一句话说明**：对 workspace 运行轻量静态验证。

**语法用法**：

```bash
uv run simple-ar code-task validate runs/<run-id> --strict
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--strict` | flag | 将较高风险 warning 视为 error。 |
| `--max-file-bytes N` | int | 扫描文件大小上限。 |

**生成产物**：

- `code_task/meta/validation_report.json`

**注意**：

静态验证是保守检查，不替代 benchmark。

#### `simple-ar code-task run`

**一句话说明**：patch 后运行 benchmark。

**语法用法**：

```bash
uv run simple-ar code-task run runs/<run-id> --timeout 60
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--command TEXT` | string | 覆盖记录的 benchmark command。 |
| `--timeout N` | int | benchmark timeout。 |
| `--skip-validation` | flag | 静态验证未通过时仍运行。 |
| `--env-mode MODE` | enum | `current` 或 `external`。 |
| `--python PATH` | path | external env mode 的 Python。 |

**生成产物**：

- `code_task/run/patched/execution_report.json`
- `code_task/run/patched/stdout.txt`
- `code_task/run/patched/stderr.txt`
- `code_task/run/patched/metrics.json`
- baseline metrics 存在时写入 `code_task/run/comparison.json`

**注意**：

指标比较会区分“benchmark 跑通”和“目标确实提升”。

#### `simple-ar code-task analyze-failure`

**一句话说明**：总结最近失败的 validation 或 benchmark。

**语法用法**：

```bash
uv run simple-ar code-task analyze-failure runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |

**生成产物**：

- `code_task/run/patched/failure_analysis.md` 或 `code_task/meta/failure_analysis.md`

**注意**：

确定性命令，不修改源文件。

#### `simple-ar code-task repair`

**一句话说明**：根据最近失败上下文提出受限 repair edits。

**语法用法**：

```bash
uv run simple-ar code-task repair runs/<run-id>
```

**参数表**：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `RUN_DIR` | path | code-task run 目录。 |
| `--model NAME` | string | 模型覆盖。 |
| `--no-llm` | flag | 写入 deterministic empty repair proposal。 |
| `--max-files N` | int | repair 上下文文件预算。 |
| `--max-source-chars-per-file N` | int | 单文件 source 上下文预算。 |

**生成产物**：

- `code_task/repairs/repair-NNN/proposed_edits.json`
- 更新 `code_task/summary.md`

**注意**：

repair proposal 不会自动应用。审核后使用 `apply-edits --edits-file ...`。
