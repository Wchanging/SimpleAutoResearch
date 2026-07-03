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

快速链路测试，单个任务失败后继续后续任务：

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topic-set quick \
  --analyze \
  --score
```

上面的命令使用默认 `proxy` 评分，适合快速开发回归。如果要做论文实验或对齐
AutoResearchClaw strict evaluation protocol，建议显式使用 strict profile：

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topic-set quick \
  --analyze \
  --score \
  --score-profile strict
```

每次 `run` 都会在 `benchmark/arc_bench/batch_state/` 下创建独立状态文件，例如：

```text
benchmark/arc_bench/batch_state/20260627-153607-quick.json
```

`batch_state/latest_state.json` 会记录最近一次批跑使用的状态文件。这样不同批次不会互相覆盖，
但默认重试和查看状态时仍然不用手动记路径。

任务组：

```bash
uv run python benchmark/arc_bench/batch_runner.py run --topic-set quick --analyze --score
uv run python benchmark/arc_bench/batch_runner.py run --topic-set breadth --analyze --score
uv run python benchmark/arc_bench/batch_runner.py run --topic-set specialized --analyze --score
uv run python benchmark/arc_bench/batch_runner.py run --topic-set all --analyze --score
```

正式评分版本：

```bash
uv run python benchmark/arc_bench/batch_runner.py run --topic-set quick --analyze --score --score-profile strict
uv run python benchmark/arc_bench/batch_runner.py run --topic-set breadth --analyze --score --score-profile strict
uv run python benchmark/arc_bench/batch_runner.py run --topic-set specialized --analyze --score --score-profile strict
uv run python benchmark/arc_bench/batch_runner.py run --topic-set all --analyze --score --score-profile strict
```

显式指定任务：

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topics ML04 ML02 ML06 \
  --analyze \
  --score \
  --score-profile strict
```

每个任务结束或失败后，都会写出一份轻量运行/API 统计：

```text
benchmark/arc_bench/runs/ml/ML04/<run-id>/arc_task_stats.json
benchmark/arc_bench/submissions/ml/ML04/<run-id>/arc_task_stats.json
```

其中包含总耗时、每条命令的耗时、日志路径、退出码，以及 code-task、result-analysis、score 三部分汇总后的 LLM 请求次数、输入/输出/总 token 和估算费用。批跑 state 中也会保存紧凑摘要，因此 `batch_runner.py status` 可以直接显示耗时和 token 总量。

批跑脚本不会只看命令退出码。`execute` 后会读取 run 的 `manifest.json`，
只有业务状态达到 `benchmark_passed` 才会继续 `finalize`。如果后续补加
`--score`，并且某个任务已经有有效 submission，它会直接补 `judge/`，不会重跑实验。
不同 score profile 的 judge 不会互相当作已完成产物；后续补加 `--score-profile strict`
时，会单独生成/刷新 strict judge。
如果服务器网络不稳定，可以加 `--llm-retry-attempts 5`，临时覆盖所有
`code-task execute` 调用的阶段级 LLM 重试次数。
默认情况下，LLM 调用不会向 provider 传客户端超时和输出上限。如果你需要为了费用控制
或企业网关策略重新加硬限制，可以在批跑前设置正数：

```bash
export SIMPLE_AR_LLM_TIMEOUT_SEC=300
export SIMPLE_AR_MAX_OUTPUT_TOKENS=4096
```

## 重跑与续修

为所有未完成任务重新开新 run：

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --topic-set quick \
  --analyze \
  --score \
  --score-profile strict \
  --llm-retry-attempts 5
```

默认情况下，`retry-unfinished` 会读取 `latest_state.json` 指向的最近批次。如果要重试某一次
历史批次，可以显式传入对应 state：

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --state-file benchmark/arc_bench/batch_state/20260627-153607-quick.json \
  --topic-set quick \
  --analyze \
  --score \
  --score-profile strict
```

复用上一次失败 run，并额外给 repair 预算：

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --topic-set quick \
  --analyze \
  --score \
  --score-profile strict \
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

如果要做论文或正式对比，使用 strict profile：

```bash
uv run python benchmark/arc_bench/adapter.py score \
  --prepared-dir benchmark/arc_bench/prepared/ml/ML02 \
  --submission-dir "$OUT_DIR/submission" \
  --output-dir "$OUT_DIR/judge_strict" \
  --score-profile strict \
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
    evidence_bundle.json         # scorer 使用的紧凑 benchmark 证据包
    judge_result.json            # leaf_grades + scoring_summary
    scorecard.md
    score_round_code_response.json
    score_round_code_prompt.txt              # 仅在 scoring 失败时生成
    score_round_code_response_attempt_*.json # 仅记录 schema retry 响应
    score_round_results_response.json
    score_round_results_prompt.txt              # 仅在 scoring 失败时生成
    score_round_results_response_attempt_*.json # 仅记录 schema retry 响应
    strict_reviewer_*.json       # 仅 strict profile 生成
    strict_disagreements.json    # 仅 strict profile 生成
    strict_adjudication.json     # 仅 strict profile 且存在分歧时生成
```

`finalize --analyze` 负责根据实测结果生成 benchmark-facing README 和 claims。
`score` 现在有三档 profile：

- `proxy`：默认轻量 two-round LLM scorer，用于开发回归、quick/breadth 烟测和趋势判断；不要直接当作 AutoResearchClaw strict 分数汇报。
- `arc-auto`：尽量贴近 `scripts/judge.py` 的自动 two-round judge 行为，保留可恢复的缺 leaf 处理。
- `strict`：运行独立 reviewer、对超过阈值的 per-leaf 分歧复审，记录 analysis source，并输出 CD/CE/RA 与 overall 汇总；正式论文对比优先使用这一档。

如果某轮评分返回了合法 JSON 但顶层 schema 不对，adapter 会带着更严格的 `grades`
契约重试一次，并保存每次 raw response。若重试后仍不能恢复 `grades` 数组，评分才会失败。
如果合法响应遗漏单个 leaf，会记录 warning，并按 AutoResearchClaw `judge.py` 的行为给该
leaf 默认 `0.5`；这一缺省只用于非 strict 的自动评分 profile。

## 外部 Judge

通常使用内置 `score` 即可。只有当你明确要调用外部 ARC-Bench judge 时，才使用包装器：

```bash
uv run python benchmark/arc_bench/adapter.py judge \
  --submission-dir benchmark/arc_bench/submissions/ml/ML02/<run-id>/submission \
  --judge-command "python /path/to/arc_judge.py --submission {submission_dir} --output {output_dir}"
```
