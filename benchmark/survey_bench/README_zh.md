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
benchmark/survey_bench/results/<topic-slug>/<timestamp-topic>/
```

导出给 SurveyBench 原生评测的 Markdown 文件仍会复制到：

```text
SurveyBench/data/<method>/
```

文件名必须和 `SurveyBench/data/HumanSurvey/*.md` 对齐。生成阶段不要读取 `HumanSurvey`，它只应该作为评测 reference。

## 常用命令

查看 topics：

```bash
uv run python benchmark/survey_bench/adapter.py topics
```

跑一个有边界的单 topic 生成测试：

```bash
uv run simple-ar run \
  --config benchmark/survey_bench/configs/llm_based_multi_agent.toml
```

这个配置默认使用内置 `survey_long` 模板，目标是生成更长、更面向读者需求的学术综述，而不是紧凑技术报告；同时会提高检索和 read 阶段预算，让单 topic 测试大致基于 20-30 篇论文展开，并为长 survey 开启确定性的 SVG 图示生成。报告阶段默认使用 `cost_profile = "balanced"` 和 `outline_strategy = "adaptive"`，让章节获得 topic-specific 目标和有界 source batches；只有最终高预算评测时才建议切换到 `cost_profile = "thorough"`。

默认会生成到：

```text
benchmark/survey_bench/results/LLM-based-Multi-Agent/<timestamp-topic>/
```

把生成的 survey 导出到 SurveyBench 方法目录：

```bash
uv run python benchmark/survey_bench/adapter.py export \
  --source-dir benchmark/survey_bench/results/LLM-based-Multi-Agent/<timestamp-topic>/08-report \
  --method SimpleAutoResearch \
  --allow-subset \
  --normalize-headings \
  --force
```

如果 `08-report` 里只有 `report.md`，需要先把它复制或重命名成 topic 对应文件名，例如 `LLM-based Multi-Agent.md`。导出时会一并复制相对 Markdown 图片资产，例如 `figures/*.svg`。后续可以再把这一步做成 adapter 的便捷命令。

验证格式：

```bash
uv run python benchmark/survey_bench/adapter.py validate \
  --survey-dir SurveyBench/data/SimpleAutoResearch \
  --allow-subset \
  --output benchmark/survey_bench/results/SimpleAutoResearch/validation.json
```

运行原生 content/outline/richness 评测：

```bash
uv run python benchmark/survey_bench/adapter.py eval-content \
  --method SimpleAutoResearch \
  --model gpt-4o-mini
```

运行原生 quiz-based 评测：

```bash
uv run python benchmark/survey_bench/adapter.py eval-quiz \
  --method SimpleAutoResearch \
  --model gpt-4o-mini \
  --emb-model text-embedding-3-small \
  --emb-dimension 1536
```

汇总原生结果：

```bash
uv run python benchmark/survey_bench/adapter.py summarize \
  --method SimpleAutoResearch
```

汇总结果会写到：

```text
benchmark/survey_bench/results/SimpleAutoResearch/summary.json
benchmark/survey_bench/results/SimpleAutoResearch/summary.md
```

`summary.md` 会生成类似论文表格的分组指标，包括 Outline Quality、Content Quality、Richness 以及对应均值。

## 依赖提示

content 评测至少需要 `pandas`；quiz 评测还会使用 embedding、FAISS、json5、PyYAML 等依赖，调用量和资源消耗明显高于 content 评测。

## 当前适配状态

当前 adapter 解决的是评测侧对齐：格式校验、导出、调用原生 judge、汇总。生成高分 survey 还需要继续增强 SimpleAutoResearch 的 survey 写作流程，例如 topic-specific outline planning、section-wise evidence synthesis、面向读者需求的自审查、表格/图示生成和引用组织。
