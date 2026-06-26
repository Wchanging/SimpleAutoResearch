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

## 批量运行任务

如果想按顺序自动完成 `init -> execute -> finalize`，并且某个任务失败后继续跑下一个，
可以使用批跑脚本：

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topic-set quick \
  --analyze
```

`quick` 当前等价于：

```text
ML04 -> ML02 -> ML06 -> ML10 -> ML08
```

`INDEX.md` 里的三组顺序都可以直接用参数覆盖：

```bash
# 第一组：快速确认链路
uv run python benchmark/arc_bench/batch_runner.py run --topic-set quick --analyze

# 第二组：扩展覆盖
uv run python benchmark/arc_bench/batch_runner.py run --topic-set breadth --analyze
# breadth 也可以写成 next
uv run python benchmark/arc_bench/batch_runner.py run --topic-set next --analyze

# 第三组：更高风险 / 更专项的任务
uv run python benchmark/arc_bench/batch_runner.py run --topic-set specialized --analyze
# specialized 也可以写成 high-risk 或 higher-risk
uv run python benchmark/arc_bench/batch_runner.py run --topic-set high-risk --analyze

# 三组全部按顺序跑
uv run python benchmark/arc_bench/batch_runner.py run --topic-set all --analyze
```

也可以显式指定任务：

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topics ML04 ML02 ML06 \
  --analyze
```

批跑状态会写到：

```text
benchmark/arc_bench/batch_state/ml_batch_state.json
```

批跑脚本不会只看命令退出码。`execute` 后会读取 run 的 `manifest.json`，
只有业务状态达到 `benchmark_passed` 才会继续 `finalize`。标记为 completed 前，
还会检查 submission 和 `result_analysis/` 的关键文件是否存在；如果本次命令带
`--analyze`，还会要求 LLM analysis 产物存在。旧 state 即使曾经把失败 run 误标成
completed，后续 `run` / `retry-unfinished` 也会重新检查并把它当成未完成任务继续跑。

每个任务的命令日志会写到：

```text
benchmark/arc_bench/batch_logs/<MLxx>/<timestamp>/
  init.log
  execute.log
  finalize.log
```

在 Linux / Ubuntu 终端中，批跑脚本会用伪终端保留 `simple-ar` 的 Rich 彩色输出。
对应日志也会保留 ANSI 颜色转义码，适合回放和排查问题。

只重跑未完成任务：

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --topic-set quick \
  --analyze
```

默认会为未完成任务创建新的 run。如果想复用上一次失败的 `run_dir` 继续尝试：

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --topic-set quick \
  --analyze \
  --resume-existing
```

注意：如果旧 run 的 manifest 记录显示 repair 次数已经达到 `code_task.toml` 里的
`[execute].repair_rounds`，批跑脚本会自动放弃 resume，改为新建 fresh run。这样可以避免
反复进入“repair 预算已耗尽，所以重新 execute 仍然不再 repair”的死循环。

如果你希望保留旧 run 的代码修改、失败记录和 repair 记忆，并在原基础上继续修，
可以显式延长本次 execute 的 repair 预算：

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --topic-set quick \
  --analyze \
  --resume-existing \
  --extend-repair-rounds 2
```

这不会修改 `code_task.toml`，只会在本次调用 `simple-ar code-task execute` 时临时传入
`--repair-rounds <已用 repair 次数 + 2>`。适合“同一个错误连续出现，需要让模型知道旧代码状态后继续修”的情况。

查看当前批跑状态：

```bash
uv run python benchmark/arc_bench/batch_runner.py status
```

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
