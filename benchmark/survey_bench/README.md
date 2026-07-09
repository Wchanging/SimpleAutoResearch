# SurveyBench Adapter

This folder contains a lightweight adapter from SimpleAutoResearch outputs to an external `SurveyBench/` checkout. The adapter does not reimplement or modify SurveyBench judge prompts. Native evaluation is run through `SurveyBench/src/run_content_eval.py` and `SurveyBench/src/run_quiz_eval.py`.

## Layout

Keep the external benchmark checkout at the repository root by default:

```text
SurveyBench/
  data/HumanSurvey/
  src/run_content_eval.py
  src/run_quiz_eval.py
```

Generated survey markdown files should be exported to a method directory:

```text
SurveyBench/data/SimpleAutoResearch/
  Graph Neural Networks.md
  Large Language Models for Recommendation.md
  ...
```

Filenames must match `SurveyBench/data/HumanSurvey/*.md`. Do not use `HumanSurvey` during generation; it is evaluation-only reference data.

## Common Commands

List topics:

```bash
uv run python benchmark/survey_bench/adapter.py topics
```

Run a bounded single-topic SimpleAutoResearch survey generation:

```bash
uv run simple-ar run \
  --config benchmark/survey_bench/configs/llm_based_multi_agent.toml
```

The config uses the built-in `survey_long` report template, which is intended
for longer reader-oriented academic surveys rather than compact technical
briefs. It also raises retrieval and read-stage budgets so a single-topic
smoke run can draw on roughly 20-30 papers instead of a tiny source set. The
same config enables deterministic SVG figure generation for long surveys. Its
report stage uses `cost_profile = "balanced"` and `outline_strategy =
"adaptive"` so sections receive topic-specific goals and bounded source
batches by default; switch to `cost_profile = "thorough"` only for final
high-budget runs.

This writes the run under:

```text
benchmark/survey_bench/results/LLM-based-Multi-Agent/<timestamp-topic>/
```

Export generated surveys:

```bash
uv run python benchmark/survey_bench/adapter.py export \
  --source-dir benchmark/survey_bench/results/LLM-based-Multi-Agent/<timestamp-topic>/08-report \
  --method SimpleAutoResearch \
  --allow-subset \
  --normalize-headings \
  --force
```

If `08-report` only contains `report.md`, copy or rename it to the SurveyBench
topic filename first, for example `LLM-based Multi-Agent.md`. Relative Markdown
image assets such as `figures/*.svg` are copied during export.

Validate format:

```bash
uv run python benchmark/survey_bench/adapter.py validate \
  --survey-dir SurveyBench/data/SimpleAutoResearch \
  --output benchmark/survey_bench/results/SimpleAutoResearch/validation.json
```

Run native content/outline/richness evaluation:

```bash
uv run python benchmark/survey_bench/adapter.py eval-content \
  --method SimpleAutoResearch \
  --model gpt-4o-mini \
  --api-key "$OPENAI_API_KEY" \
  --api-url "$OPENAI_BASE_URL"
```

Run native quiz-based evaluation:

```bash
uv run python benchmark/survey_bench/adapter.py eval-quiz \
  --method SimpleAutoResearch \
  --model gpt-4o-mini \
  --api-key "$OPENAI_API_KEY" \
  --api-url "$OPENAI_BASE_URL" \
  --emb-model text-embedding-3-small \
  --emb-dimension 1536 \
  --emb-api-key "$OPENAI_API_KEY" \
  --emb-api-url "$OPENAI_BASE_URL"
```

Summarize native result artifacts:

```bash
uv run python benchmark/survey_bench/adapter.py summarize \
  --method SimpleAutoResearch
```

The summary includes a paper-style grouped table for outline quality, content quality, richness, and per-group averages.

Outputs are written under:

```text
benchmark/survey_bench/results/SimpleAutoResearch/
```

## Notes

`--normalize-headings` only adapts markdown numbering for SurveyBench outline parsing. It does not add content. If the generator already emits numbered headings such as `## 1 Introduction` and `### 1.1 Background`, omit it.

The adapter currently handles benchmark-side preparation and native evaluation. High-scoring surveys still require stronger SimpleAutoResearch survey generation: topic-specific outline planning, section-level evidence synthesis, reader-need-oriented self-review, tables/figures, and citation organization.
