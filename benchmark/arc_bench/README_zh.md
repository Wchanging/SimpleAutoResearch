# ARC-Bench 适配器使用说明

这个目录是 SimpleAutoResearch 的本地 benchmark 适配层，主要用于把
AutoResearchClaw 的 ARC-Bench 任务转换成可运行的 `code-task`，并在运行结束后
整理成 ARC-Bench 风格的 submission 和 leaf-level 分数。

适配器放在 `benchmark/arc_bench/`，不放进 `src/simple_ar/`，目的是让
benchmark 逻辑和核心框架解耦。

## 功能概览

```text
ARC manifest/rubric
  -> prepared SimpleAutoResearch code-task
  -> code-task run
  -> finalized ARC-style submission
  -> ARC-compatible leaf-level score
```

常用目录：

```text
benchmark/arc_bench/
  adapter.py              # 单个任务 prepare/finalize/score
  batch_runner.py         # 批量运行任务组
  prepared/ml/            # 已转换好的 ML 任务包
  runs/ml/                # SimpleAutoResearch 原始运行结果
  submissions/ml/         # finalize 后的 submission 与 judge 输出
  batch_state/            # 可恢复的批跑状态
  batch_logs/             # 命令日志
```

`benchmark/` 默认被 gitignore；只跟踪适配脚本和轻量文档。

## 一次性准备

如果 prepared 包不存在，或者服务器上的 AutoResearchClaw 路径不同：

```bash
uv run python benchmark/arc_bench/adapter.py prepare-ml \
  --arc-root /path/to/AutoResearchClaw/experiments/arc_bench
```

只准备部分任务：

```bash
uv run python benchmark/arc_bench/adapter.py prepare-ml \
  --arc-root /path/to/AutoResearchClaw/experiments/arc_bench \
  --topics ML02 ML04 ML10
```

推荐测试顺序写在：

```text
benchmark/arc_bench/prepared/ml/INDEX.md
```

默认 prepared task 会保持 vanilla ARC-Bench 输入形式：manifest 内容加可读 Markdown
rubric leaves。若要专门做 SimpleAutoResearch task-contract 机制的消融实验，可以显式开启额外的机器可读 contract，并建议写到单独 prepared root：

```bash
uv run python benchmark/arc_bench/adapter.py prepare-ml \
  --arc-root /path/to/AutoResearchClaw/experiments/arc_bench \
  --prepared-root benchmark/arc_bench/prepared/ml_contract \
  --include-contract
```

不要把 contract-enhanced prepared 包和 vanilla ARC-Bench 对比结果混在一起汇报，除非明确说明输入差异。

## 可选 ML 依赖

在运行 breadth 或 all 这类覆盖面更广的 ML 任务组之前，建议先安装常用科学计算库。这样 code-task 在环境探测和规划时能看到可用库，减少不必要的重复造轮子：

```bash
uv pip install numpy scipy scikit-learn pandas matplotlib statsmodels networkx imbalanced-learn umap-learn scikit-optimize cma seaborn pytest
```

快速检查：

```bash
uv run python -c "import numpy, scipy, sklearn, pandas, matplotlib, statsmodels, networkx, imblearn, umap, skopt, cma; print('arc deps ok')"
```

## 推荐批量运行

快速链路测试或论文主表，优先使用 AutoResearchClaw 原生 `judge.py`：

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topic-set quick \
  --analyze \
  --native-score \
  --native-score-model gpt-4o
```

如果需要按 ARC manual strict audit prompt 做双 reviewer 复核，再使用
`manual-strict`。可以指定两个 reviewer 模型，例如一个 Claude、一个 Codex/GPT：

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topic-set quick \
  --analyze \
  --score \
  --score-profile manual-strict \
  --strict-reviewer-models claude-opus-4-6,gpt-5.4 \
  --strict-reviewer-apis chat,responses \
  --strict-adjudicator-model gpt-5.4
```

如果某个 reviewer 模型不支持当前 provider 的 Responses API（常见表现是
`local:convert_request_failed`），可以用 `--strict-reviewer-apis` 为每个 reviewer
单独指定 `chat` 或 `responses`。

每次 `run` 都会在 `benchmark/arc_bench/batch_state/` 下创建独立状态文件，例如：

```text
benchmark/arc_bench/batch_state/20260627-153607-quick.json
```

`batch_state/latest_state.json` 会记录最近一次批跑使用的状态文件。这样不同批次不会互相覆盖，
但默认重试和查看状态时仍然不用手动记路径。

任务组：

```bash
uv run python benchmark/arc_bench/batch_runner.py run --topic-set quick --analyze --native-score --native-score-model gpt-4o
uv run python benchmark/arc_bench/batch_runner.py run --topic-set breadth --analyze --native-score --native-score-model gpt-4o
uv run python benchmark/arc_bench/batch_runner.py run --topic-set specialized --analyze --native-score --native-score-model gpt-4o
uv run python benchmark/arc_bench/batch_runner.py run --topic-set all --analyze --native-score --native-score-model gpt-4o
```

显式指定任务：

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topics ML04 ML02 ML06 \
  --analyze \
  --native-score \
  --native-score-model gpt-4o
```

每个任务结束或失败后，都会写出一份轻量运行/API 统计：

也可以用范围和排除项来跑一部分任务：

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topic-range ML01-ML10 \
  --exclude-topics ML02 ML06 \
  --analyze \
  --native-score \
  --native-score-model gpt-4o
```

低成本消融建议每组都写入独立 state file 或 refresh variant，避免和主实验混在一起。下面这些开关会透传给 `simple-ar code-task execute`：

```bash
# 不给 repair prompt 结构化 failure-graph 上下文。
uv run python benchmark/arc_bench/batch_runner.py run \
  --topics ML06 ML09 ML10 \
  --repair-context raw_logs_only \
  --analyze \
  --native-score \
  --native-score-model gpt-4o \
  --state-file benchmark/arc_bench/batch_state/ablation-no-failure-graph.json

# 不给 repair prompt 之前的 repair memory。
uv run python benchmark/arc_bench/batch_runner.py run \
  --topics ML06 ML09 ML10 \
  --no-repair-memory \
  --analyze \
  --native-score \
  --native-score-model gpt-4o \
  --state-file benchmark/arc_bench/batch_state/ablation-no-repair-memory.json

# 使用最小 task-contract prompt 视图，可作为 Plan-then-Code 风格近似 baseline。
uv run python benchmark/arc_bench/batch_runner.py run \
  --topics ML06 ML09 ML10 \
  --contract-context minimal \
  --repair-rounds 0 \
  --analyze \
  --native-score \
  --native-score-model gpt-4o \
  --state-file benchmark/arc_bench/batch_state/ablation-minimal-contract.json
```

```text
benchmark/arc_bench/runs/ml/ML04/<run-id>/arc_task_stats.json
benchmark/arc_bench/submissions/ml/ML04/<run-id>/arc_task_stats.json
```

其中包含总耗时、每条命令的耗时、日志路径、退出码，以及 code-task、result-analysis、score 三部分汇总后的 LLM 请求次数、输入/输出/总 token 和估算费用。批跑 state 中也会保存紧凑摘要，因此 `batch_runner.py status` 可以直接显示耗时和 token 总量。

批跑脚本不会只看命令退出码。`execute` 后会读取 run 的 `manifest.json`，
只有业务状态达到 `benchmark_passed` 才会继续 `finalize`。如果后续补加
`--score`，并且某个任务已经有有效 submission，它会直接补 `judge/` 或
`judge_manual_strict/`，不会重跑实验。不同 score profile 的 judge 不会互相当作已完成产物；
后续补加 `--score-profile manual-strict` 时，会单独生成/刷新 manual strict judge。
如果服务器网络不稳定，可以加 `--llm-retry-attempts 5`，临时覆盖所有
`code-task execute` 调用的阶段级 LLM 重试次数。
默认情况下，LLM 调用不会向 provider 传客户端超时和输出上限。如果你需要为了费用控制
或企业网关策略重新加硬限制，可以在批跑前设置正数：

```bash
export SIMPLE_AR_LLM_TIMEOUT_SEC=300
export SIMPLE_AR_MAX_OUTPUT_TOKENS=4096
```

## 只重跑 Analyze / Score

如果一批 ML01-ML25 的 `code-task execute` 已经跑完，只想用新的
result-analysis 或 judge 逻辑重新生成后处理结果，可以使用 `refresh`。它会读取已有
state 中的 `run_dir`，不会重新 init/execute，也不会覆盖原 submission；新的输出会写入
带 variant 后缀的目录。

```bash
uv run python benchmark/arc_bench/batch_runner.py refresh \
  --source-state-file benchmark/arc_bench/batch_state/20260706-011743-all.json \
  --topic-set all \
  --analyze \
  --score \
  --score-profile manual-strict \
  --variant manual-strict-rerun-01
```

输出示例：

```text
benchmark/arc_bench/submissions/ml/ML02/<run-id>--manual-strict-rerun-01/
benchmark/arc_bench/batch_state/<new-refresh-state>.json
```

后续汇总这组新结果时，使用新生成的 state 文件：

```bash
uv run python benchmark/arc_bench/batch_runner.py summarize \
  --state-file benchmark/arc_bench/batch_state/<new-refresh-state>.json \
  --judge-source manual-strict
```

这份 refresh state 自身只记录本次 finalize/result-analysis/score 的新增命令；但
`summarize` 会在发现 variant 输出时，自动读取 source run 的 `arc_task_stats.json`，
把源 code-task 的 init/execute 成本和本次后处理成本合并为 `Total Time` / 总 token。
表格中的 `Postprocess` / `Postprocess Tokens` 则保留本次后处理重算的增量口径。

如果不传 `--variant`，runner 会用新 state 名自动生成一个唯一 variant。若 variant
目录已存在且你希望重新覆盖这组后处理结果，再加 `--force`。

### 原生 AutoResearchClaw Judge 重评

如果结果要用于论文式对比，建议在 finalize 之后再跑 AutoResearchClaw 自带的
`experiments/arc_bench/scripts/judge.py --full`。适配器提供了一个很薄的
`native-score` 包装命令：它会读取本项目根目录 `.env`，必要时把 `SIMPLE_AR_MODEL`
映射为 `OPENAI_MODEL`，并允许通过 `--native-score-model` 覆盖原生 judge 使用的
`ARC_JUDGE_MODEL`。
如果 AutoResearchClaw 没有放在本仓库的 `AutoResearchClaw/experiments/arc_bench`
位置，请显式传入 `--arc-root`。批量 runner 的 `run`、`refresh` 和
`retry-unfinished` 在启用 `--native-score` 时也支持同一个参数。
原生 judge 建议默认使用 `gpt-4o`，除非你是在刻意做评测模型消融。AutoResearchClaw
原生解析器要求模型只返回单个 JSON 对象，对新模型夹带额外文本的容错较弱。

单个 ML04 已有 finalized 输出的示例：

```bash
uv run python benchmark/arc_bench/adapter.py native-score \
  --arc-root /path/to/AutoResearchClaw/experiments/arc_bench \
  --prepared-dir benchmark/arc_bench/prepared/ml/ML04 \
  --run-dir benchmark/arc_bench/submissions/ml/ML04/20260706-011752-arc-bench-ml04 \
  --output-dir benchmark/arc_bench/submissions/ml/ML04/20260706-011752-arc-bench-ml04/judge_native \
  --topic ML04 \
  --model gpt-4o \
  --full \
  --debug
```

如果要复用已有 all 批次，只重新跑原生 judge，并写入新的 variant 目录：

```bash
uv run python benchmark/arc_bench/batch_runner.py refresh \
  --arc-root /path/to/AutoResearchClaw/experiments/arc_bench \
  --source-state-file benchmark/arc_bench/batch_state/20260706-011743-all.json \
  --topic-set all \
  --native-score \
  --native-score-model gpt-4o \
  --variant native-full-rerun-01 \
  --score-timeout 3600
```

输出示例：

```text
benchmark/arc_bench/submissions/ml/ML04/<run-id>--native-full-rerun-01/judge_native/judge_result.json
benchmark/arc_bench/batch_state/<new-refresh-state>.json
```

`adapter.py native-score --backend local` 只建议用于路径冒烟测试；正式对比请使用默认
LLM backend。

## 重跑与续修

为所有未完成任务重新开新 run：

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --topic-set quick \
  --analyze \
  --native-score \
  --native-score-model gpt-4o \
  --llm-retry-attempts 5
```

默认情况下，`retry-unfinished` 会读取 `latest_state.json` 指向的最近批次。如果要重试某一次
历史批次，可以显式传入对应 state：

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --state-file benchmark/arc_bench/batch_state/20260627-153607-quick.json \
  --topic-set quick \
  --analyze \
  --native-score \
  --native-score-model gpt-4o
```

复用上一次失败 run，并额外给 repair 预算：

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --topic-set quick \
  --analyze \
  --native-score \
  --native-score-model gpt-4o \
  --resume-existing \
  --extend-repair-rounds 2
```

如果不加 `--extend-repair-rounds`，而旧 run 的 repair 预算已经耗尽，批跑器会自动新建
fresh run，避免重复进入无效 repair 循环。

查看状态：

```bash
uv run python benchmark/arc_bench/batch_runner.py status
```

`status` 默认也读取最近批次；需要查看历史批次时使用 `--state-file <path>`，或者显式传
`--state-file latest`。

## 单任务手动流程

调试单个 topic 时使用。

```bash
uv run simple-ar code-task init \
  --config benchmark/arc_bench/prepared/ml/ML02/code_task.toml

RUN_DIR=$(ls -td benchmark/arc_bench/runs/ml/ML02/* | head -n 1)

uv run simple-ar code-task execute "$RUN_DIR" \
  --config benchmark/arc_bench/prepared/ml/ML02/code_task.toml \
  --yes
```

Finalize：

```bash
OUT_DIR=benchmark/arc_bench/submissions/ml/ML02/$(basename "$RUN_DIR")

uv run python benchmark/arc_bench/adapter.py finalize \
  --prepared-dir benchmark/arc_bench/prepared/ml/ML02 \
  --run-dir "$RUN_DIR" \
  --output-dir "$OUT_DIR" \
  --force \
  --analyze
```

Score：

```bash
uv run python benchmark/arc_bench/adapter.py score \
  --prepared-dir benchmark/arc_bench/prepared/ml/ML02 \
  --submission-dir "$OUT_DIR/submission" \
  --output-dir "$OUT_DIR/judge"
```

如果要做双 reviewer strict 复核，使用 `manual-strict`。它会加载
AutoResearchClaw 的 `manual_strict_audit_prompt.md`，并在当前 adapter 中执行
两个独立 reviewer、per-leaf 分歧检测和 adjudication：

```bash
uv run python benchmark/arc_bench/adapter.py score \
  --prepared-dir benchmark/arc_bench/prepared/ml/ML02 \
  --submission-dir "$OUT_DIR/submission" \
  --output-dir "$OUT_DIR/judge_manual_strict" \
  --score-profile manual-strict \
  --strict-reviewer-models claude-opus-4-6,gpt-5.4 \
  --strict-adjudicator-model gpt-5.4 \
  --strict-reviewers 2 \
  --disagreement-threshold 0.20
```

## 重点查看文件

```text
benchmark/arc_bench/submissions/ml/ML02/<run-id>/
  submission/
    code/
    results/metrics.json
    README.md
    claims.json
  result_analysis/
    metric_summary.json
    analysis_report.md
    analysis_audit.json
    analysis_response.json       # --analyze 成功时的结构化 LLM 响应
    analysis_prompt.txt          # 仅在 analyze JSON 解析失败时用于诊断
    analysis_raw_response.txt    # 仅在 analyze JSON 解析失败时用于诊断
  judge/
    evidence_bundle.json         # scorer 使用的紧凑 benchmark 证据包，包含 leaf 定向代码证据
    judge_result.json            # leaf_grades + scoring_summary
    scorecard.md
    score_round_code_response.json
    score_round_code_prompt.txt              # 仅在 scoring 失败时生成
    score_round_code_response_attempt_*.json # 仅记录 schema retry 响应
    score_round_results_response.json
    score_round_results_prompt.txt              # 仅在 scoring 失败时生成
    score_round_results_response_attempt_*.json # 仅记录 schema retry 响应
  judge_manual_strict/
    evidence_bundle.json
    judge_result.json
    scorecard.md
    reviewer_*.json
    disagreements.json
    adjudication.json
  judge_native/
    judge_result.json            # AutoResearchClaw scripts/judge.py --full 的原生结果副本
    judge_debug.json             # 启用 --debug 时的原生调试文件
    native_judge_meta.json       # wrapper 命令、模式、耗时与复制路径
    stdout.txt
    stderr.txt
```

`finalize --analyze` 负责根据实测结果生成 benchmark-facing README 和 claims。
正式推荐只使用两类 judge：

- `native-score`：调用 AutoResearchClaw 自带 `scripts/judge.py`，输出到 `judge_native/`；适合作为论文主表的原生 judge 路径。
- `score --score-profile manual-strict`：在当前 adapter 中复现 ARC manual strict audit 的双 reviewer + disagreement adjudication 流程，输出到 `judge_manual_strict/`；适合作为严格复核或 appendix。可用 `--strict-reviewer-models` 指定两个 reviewer 模型。

如果某轮评分返回了合法 JSON 但顶层 schema 不对，adapter 会带着更严格的 `grades`
契约重试一次，并保存每次 raw response。若重试后仍不能恢复 `grades` 数组，评分才会失败。
如果合法响应遗漏单个 leaf，会记录 warning，并按 AutoResearchClaw `judge.py` 的行为给该
leaf 默认 `0.5`；这一缺省只用于内部轻量自动评分，不用于 `manual-strict`。

## 外部 Judge

通常使用 `native-score` 调用 AutoResearchClaw 自带的 ARC-Bench judge。只有当你明确要调用其他外部 judge 命令时，才使用更底层的包装器：

```bash
uv run python benchmark/arc_bench/adapter.py judge \
  --submission-dir benchmark/arc_bench/submissions/ml/ML02/<run-id>/submission \
  --judge-command "python /path/to/arc_judge.py --submission {submission_dir} --output {output_dir}"
```

## 批次指标汇总

跑完一批任务后，可以直接汇总论文表格常用指标，不会重新执行实验、finalize 或 score：

```bash
uv run python benchmark/arc_bench/batch_runner.py summarize
```

默认读取最近一次 batch state，并写出：

```text
benchmark/arc_bench/batch_state/<batch>.summary.json
benchmark/arc_bench/batch_state/<batch>.summary.md
```

汇总内容包括 Code Development、Code Execution、Result Analysis、Overall 的均值，以及平均耗时、各命令耗时、平均 LLM 调用次数、输入 token、输出 token 和总 token。查看历史批次时使用：

```bash
uv run python benchmark/arc_bench/batch_runner.py summarize \
  --state-file benchmark/arc_bench/batch_state/<batch>.json
```

只汇总一组或几个任务：

```bash
uv run python benchmark/arc_bench/batch_runner.py summarize --topic-set quick
uv run python benchmark/arc_bench/batch_runner.py summarize --topics ML04 ML02
```
