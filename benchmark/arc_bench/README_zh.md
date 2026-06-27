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

## 推荐批量运行

快速链路测试，单个任务失败后继续后续任务：

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topic-set quick \
  --analyze \
  --score
```

任务组：

```bash
uv run python benchmark/arc_bench/batch_runner.py run --topic-set quick --analyze --score
uv run python benchmark/arc_bench/batch_runner.py run --topic-set breadth --analyze --score
uv run python benchmark/arc_bench/batch_runner.py run --topic-set specialized --analyze --score
uv run python benchmark/arc_bench/batch_runner.py run --topic-set all --analyze --score
```

显式指定任务：

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topics ML04 ML02 ML06 \
  --analyze \
  --score
```

批跑脚本不会只看命令退出码。`execute` 后会读取 run 的 `manifest.json`，
只有业务状态达到 `benchmark_passed` 才会继续 `finalize`。如果后续补加
`--score`，并且某个任务已经有有效 submission，它会直接补 `judge/`，不会重跑实验。

## 重跑与续修

为所有未完成任务重新开新 run：

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --topic-set quick \
  --analyze \
  --score
```

复用上一次失败 run，并额外给 repair 预算：

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --topic-set quick \
  --analyze \
  --score \
  --resume-existing \
  --extend-repair-rounds 2
```

如果不加 `--extend-repair-rounds`，而旧 run 的 repair 预算已经耗尽，批跑器会自动新建
fresh run，避免重复进入无效 repair 循环。

查看状态：

```bash
uv run python benchmark/arc_bench/batch_runner.py status
```

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
    analysis_prompt.txt          # 使用 --analyze 时生成
    analysis_raw_response.txt    # analyze 失败时用于诊断
  judge/
    judge_result.json            # leaf_grades + scoring_summary
    scorecard.md
    score_round_code_prompt.txt
    score_round_code_response.json
    score_round_results_prompt.txt
    score_round_results_response.json
```

`finalize --analyze` 负责根据实测结果生成 benchmark-facing README 和 claims。
`score` 是 ARC-compatible two-round LLM judge：

- Code Development leaf 从代码评分。
- Code Execution / Result Analysis leaf 从 summary、metrics、claims、writeup 评分。
- `overall_strict` 和 `results_only` 由 leaf 分数按权重确定性汇总。

如果某轮评分没有返回合法 JSON，会直接失败；如果合法响应遗漏单个 leaf，会记录 warning，
并按 AutoResearchClaw `judge.py` 的行为给该 leaf 默认 `0.5`。

## 外部 Judge

通常使用内置 `score` 即可。只有当你明确要调用外部 ARC-Bench judge 时，才使用包装器：

```bash
uv run python benchmark/arc_bench/adapter.py judge \
  --submission-dir benchmark/arc_bench/submissions/ml/ML02/<run-id>/submission \
  --judge-command "python /path/to/arc_judge.py --submission {submission_dir} --output {output_dir}"
```
