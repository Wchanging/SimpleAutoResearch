# ARC-Bench 适配器使用说明

这个目录是 SimpleAutoResearch 的本地 benchmark 适配层，主要用于把
AutoResearchClaw 的 ARC-Bench 任务转换成 SimpleAutoResearch 可以直接执行的
`code-task`，并在运行结束后整理成 ARC-Bench 风格的 `submission/` 结果包。

适配器刻意放在 `benchmark/arc_bench/` 下，而不是 `src/simple_ar/` 下。这样可以把
benchmark 逻辑和项目源码解耦，后续需要接入别的 benchmark 时，也可以新增独立目录，
而不是污染核心框架。

## 常用目录

```text
benchmark/arc_bench/
  adapter.py             # 独立适配脚本
  config.example.toml    # 本地路径配置示例
  prepared/ml/           # 已转换好的 ML 任务包
  runs/ml/               # SimpleAutoResearch 原始运行结果
  submissions/ml/        # finalize 后的 ARC-Bench submission
```

## 推荐测试顺序

ML 任务的建议运行顺序写在：

```text
benchmark/arc_bench/prepared/ml/INDEX.md
```

当前建议先跑 `ML04 -> ML02 -> ML06 -> ML10 -> ML08`，确认基础链路稳定后，
再按 `INDEX.md` 里的 breadth pass 和 higher-risk pass 继续扩展。

## 初始化一个任务

以 `ML02` 为例：

```bash
uv run simple-ar code-task init \
  --config benchmark/arc_bench/prepared/ml/ML02/code_task.toml
```

这一步会在 `benchmark/arc_bench/runs/ml/ML02/` 下创建一个新的 run 目录。

## 获取最新 Run 目录

Linux / Ubuntu：

```bash
RUN_DIR=$(ls -td benchmark/arc_bench/runs/ml/ML02/* | head -n 1)
```

PowerShell：

```powershell
$RUN_DIR = (Get-ChildItem benchmark/arc_bench/runs/ml/ML02 -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1).FullName
```

## 执行 Code Task

```bash
uv run simple-ar code-task execute "$RUN_DIR" \
  --config benchmark/arc_bench/prepared/ml/ML02/code_task.toml \
  --yes
```

这一步会完成代码生成、静态检查、benchmark 执行、失败诊断和有限轮自动修复。
如果中途失败，可以继续使用同一个 `RUN_DIR` 重新执行，系统会尽量复用已有阶段产物。

## Finalize 成 Submission

普通 finalize：

```bash
OUT_DIR=benchmark/arc_bench/submissions/ml/ML02/$(basename "$RUN_DIR")

uv run python benchmark/arc_bench/adapter.py finalize \
  --prepared-dir benchmark/arc_bench/prepared/ml/ML02 \
  --run-dir "$RUN_DIR" \
  --output-dir "$OUT_DIR" \
  --force
```

如果希望调用 LLM 重新分析结果，并生成 benchmark-facing README 与 claims：

```bash
OUT_DIR=benchmark/arc_bench/submissions/ml/ML02/$(basename "$RUN_DIR")

uv run python benchmark/arc_bench/adapter.py finalize \
  --prepared-dir benchmark/arc_bench/prepared/ml/ML02 \
  --run-dir "$RUN_DIR" \
  --output-dir "$OUT_DIR" \
  --force \
  --analyze
```

`--force` 会覆盖已有输出目录，只在你明确想重建 submission 时使用。

## 查看结果

重点看这些文件：

```text
benchmark/arc_bench/submissions/ml/ML02/<run-id>/
  submission/
    README.md            # 面向 benchmark 的结果说明
    claims.json          # 结构化结论与证据
    results/metrics.json # benchmark 指标
    code/                # 提交代码
  result_analysis/
    metric_summary.json
    rubric_coverage.json
    analysis_report.md
    analysis_audit.json
```

如果 `finalize --analyze` 因模型输出格式失败，会保留：

```text
result_analysis/analysis_prompt.txt
result_analysis/analysis_raw_response.txt
```

这两个文件用于诊断模型到底收到了什么提示、返回了什么内容。

## 批量准备 ML 任务

如果服务器上的 AutoResearchClaw 路径不同，可以显式传入 `--arc-root`：

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

## 外部 Judge

如果你已经准备好 ARC-Bench 的 judge 命令，可以通过适配器包装执行：

```bash
uv run python benchmark/arc_bench/adapter.py judge \
  --submission-dir benchmark/arc_bench/submissions/ml/ML02/<run-id> \
  --judge-command "python /path/to/arc_judge.py --submission {submission_dir} --output {output_dir}"
```

适配器会保存：

```text
judge/
  stdout.txt
  stderr.txt
  judge_result.json
```

`{submission_dir}` / `{submission}` 会替换成 submission 目录，
`{output_dir}` / `{output}` 会替换成 judge 输出目录。

