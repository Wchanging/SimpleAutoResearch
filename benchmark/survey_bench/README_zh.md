# SurveyBench 适配器

这个目录提供 SimpleAutoResearch 到外部 `SurveyBench/` 的轻量适配。适配器不复制、不改写 SurveyBench 的 judge prompt 和评分脚本；评测时通过 subprocess 调用 `SurveyBench/src/run_content_eval.py` 和 `SurveyBench/src/run_quiz_eval.py`，尽量保持原生评测路径。

## 目录约定

外部 benchmark 本体默认放在仓库根目录：

```text
SurveyBench/
  data/HumanSurvey/          # 人工 reference surveys，只用于评测
  src/run_content_eval.py    # 原生 content/outline/richness judge
  src/run_quiz_eval.py       # 原生 quiz-based judge
```

SimpleAutoResearch 的单 topic 运行建议放在：

```text
benchmark/survey_bench/results/topics/<topic-key>/<timestamp-topic>/
```

导出给 SurveyBench 原生评测的 Markdown 文件仍会复制到 method 目录。单 topic 测试建议直接使用稳定的 topic key 作为 method 名：

```text
SurveyBench/data/topic11-llm-based-multi-agent/
  LLM-based Multi-Agent.md
```

文件名必须和 `SurveyBench/data/HumanSurvey/*.md` 对齐。生成阶段不要读取 `HumanSurvey`，它只应该作为评测 reference。

## 常用命令

查看 topics：

```bash
uv run python benchmark/survey_bench/adapter.py topics
uv run python benchmark/survey_bench/adapter.py topics --with-ids
```

旧 `run-topic` / `resume-latest` 生成入口已随八阶段执行器退出，不再支持通过本适配器触发研究流程或消融重跑。新报告由 `research-session` 生成，再通过 `export-report --report-file` 显式导出。历史报告、变体命名空间、格式验证及原生评测仍可使用；不会自动重跑历史研究。

把生成的 survey 导出到 SurveyBench 方法目录：

```bash
uv run python benchmark/survey_bench/adapter.py export-report \
  --report-file benchmark/survey_bench/results/topics/topic11-llm-based-multi-agent/<timestamp-topic>/08-report/report.md \
  --topic "LLM-based Multi-Agent" \
  --method topic11-llm-based-multi-agent \
  --normalize-headings \
  --force
```

`export-report` 会直接写成 SurveyBench 需要的 topic 文件名，并复制相对 Markdown 图片资产，例如 `figures/*.svg`。只有当源目录本来已经包含一个或多个 topic 命名的 Markdown 文件时，才需要使用旧的 `export` 命令。

验证格式：

```bash
uv run python benchmark/survey_bench/adapter.py validate \
  --survey-dir SurveyBench/data/topic11-llm-based-multi-agent \
  --allow-subset \
  --output benchmark/survey_bench/results/score/topic11-llm-based-multi-agent/validation.json
```

运行原生 content/outline/richness 评测：

```bash
uv run python benchmark/survey_bench/adapter.py eval-content \
  --method topic11-llm-based-multi-agent \
  --model gpt-4o
```

运行原生 quiz-based 评测：

```bash
uv run python benchmark/survey_bench/adapter.py eval-quiz \
  --method topic11-llm-based-multi-agent \
  --model gpt-4o \
  --emb-model text-embedding-3-small \
  --emb-dimension 1536
```

汇总原生结果：

```bash
uv run python benchmark/survey_bench/adapter.py summarize \
  --method topic11-llm-based-multi-agent
```

汇总结果会写到：

```text
benchmark/survey_bench/results/score/topic11-llm-based-multi-agent/summary.json
benchmark/survey_bench/results/score/topic11-llm-based-multi-agent/summary.md
```

`summary.md` 会生成类似论文表格的分组指标，包括 Outline Quality、Content Quality、Richness 以及对应均值。

汇总所有已完成 topic，计算跨 topic 均值：

```bash
uv run python benchmark/survey_bench/adapter.py summarize-batch
```

只汇总某个 topic 范围，例如 topic10 到 topic20：

```bash
uv run python benchmark/survey_bench/adapter.py summarize-batch --from-topic 10 --to-topic 20
```

批量汇总会扫描 `benchmark/survey_bench/results/score/topic*/summary.json`，并输出：

```text
benchmark/survey_bench/results/score/batch_summary/batch_summary.json
benchmark/survey_bench/results/score/batch_summary/batch_summary.md
```

推荐的本地结果目录约定：

- `results/topics/<topic-key>/<timestamp-topic>/`：SimpleAutoResearch 生成 run，`topic-key` 可通过 `topics --with-ids` 查看。
- `results/topics-thorough/<topic-key>/<timestamp-topic>/`：使用 `--thorough` 生成的高预算 run。
- `results/score/<method>/`：某个导出方法的 SurveyBench 原生评测与汇总；单 topic 测试建议使用 `topic11-llm-based-multi-agent` 这类稳定 topic key。
- `results/score-thorough/<topic-key>/`：使用 `--thorough` 导出后的 SurveyBench 原生评测与汇总。
- `results/_native_commands/`：脱敏后的原生 judge 子进程调用记录。
- `results/_logs/`：可选批处理日志。
- `results/_exports/`：旧的手工导出临时目录；对应 method 目录存在后可以删除。

## 依赖提示

content 评测至少需要 `pandas`；quiz 评测还会使用 embedding、FAISS、json5、PyYAML 等依赖，调用量和资源消耗明显高于 content 评测。

## 当前适配状态

当前 adapter 解决的是评测侧对齐：格式校验、导出、调用原生 judge、汇总。生成高分 survey 还需要继续增强 SimpleAutoResearch 的 survey 写作流程，例如 topic-specific outline planning、section-wise evidence synthesis、面向读者需求的自审查、表格/图示生成和引用组织。
