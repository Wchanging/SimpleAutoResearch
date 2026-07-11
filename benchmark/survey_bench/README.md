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

Generated survey markdown files should be exported to a method directory. For
single-topic tests, prefer the stable topic key as the method directory:

```text
SurveyBench/data/topic11-llm-based-multi-agent/
  LLM-based Multi-Agent.md
```

Filenames must match `SurveyBench/data/HumanSurvey/*.md`. Do not use `HumanSurvey` during generation; it is evaluation-only reference data.

## Common Commands

List topics:

```bash
uv run python benchmark/survey_bench/adapter.py topics
uv run python benchmark/survey_bench/adapter.py topics --with-ids
```

End-to-end single-topic test on PowerShell:

```powershell
# 1. Generate the survey with SimpleAutoResearch.
uv run python benchmark\survey_bench\adapter.py run-topic --topic-id topic11

# 2. Export the latest report, validate it, run the native content judge, and summarize.
uv run python benchmark\survey_bench\adapter.py finalize-latest --topic-id topic11 --eval-content --model gpt-4o
```

The final summary is written to:

```text
benchmark/survey_bench/results/score/<topic-key>/summary.md
```

Quiz-based evaluation is optional and significantly more expensive than content
evaluation. Run it only when you explicitly need SurveyBench's quiz protocol.

Run a bounded single-topic SimpleAutoResearch survey generation:

```bash
uv run simple-ar run \
  --config benchmark/survey_bench/configs/topics/topic11-llm-based-multi-agent.toml
```

The topic configs live under `benchmark/survey_bench/configs/topics/`, one TOML
per SurveyBench topic key. They use `gpt-5.1` for SimpleAutoResearch survey
generation. Native SurveyBench evaluation examples below use `gpt-4o`, matching
the paper-comparison setting more closely than smaller judge models.

The config uses the built-in `survey_long` report template, which is intended
for longer reader-oriented academic surveys rather than compact technical
briefs. It also raises retrieval and read-stage budgets so a single-topic
smoke run can draw on roughly 20-30 papers instead of a tiny source set. The
same config enables deterministic SVG figure generation for long surveys. Its
report stage uses `cost_profile = "balanced"` and `outline_strategy =
"adaptive"` so sections receive a topic-specific outline, topic-specific goals,
and bounded source batches by default; switch to `cost_profile = "thorough"`
only for final high-budget runs.

This writes the run under:

```text
benchmark/survey_bench/results/topics/topic11-llm-based-multi-agent/<timestamp-topic>/
```

Export generated surveys:

```bash
uv run python benchmark/survey_bench/adapter.py export-report \
  --report-file benchmark/survey_bench/results/topics/topic11-llm-based-multi-agent/<timestamp-topic>/08-report/report.md \
  --topic "LLM-based Multi-Agent" \
  --method topic11-llm-based-multi-agent \
  --normalize-headings \
  --force
```

`export-report` writes the report to the SurveyBench topic filename and copies
relative Markdown image assets such as `figures/*.svg`. Use the older `export`
command only when a source directory already contains one or more topic-named
Markdown files.

Validate format:

```bash
uv run python benchmark/survey_bench/adapter.py validate \
  --survey-dir SurveyBench/data/topic11-llm-based-multi-agent \
  --allow-subset \
  --output benchmark/survey_bench/results/score/topic11-llm-based-multi-agent/validation.json
```

Run native content/outline/richness evaluation:

```bash
uv run python benchmark/survey_bench/adapter.py eval-content \
  --method topic11-llm-based-multi-agent \
  --model gpt-4o \
  --api-key "$OPENAI_API_KEY" \
  --api-url "$OPENAI_BASE_URL"
```

Run native quiz-based evaluation:

```bash
uv run python benchmark/survey_bench/adapter.py eval-quiz \
  --method topic11-llm-based-multi-agent \
  --model gpt-4o \
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
  --method topic11-llm-based-multi-agent
```

The summary includes a paper-style grouped table for outline quality, content quality, richness, and per-group averages.

Outputs are written under:

```text
benchmark/survey_bench/results/score/topic11-llm-based-multi-agent/
```

Recommended local results layout:

- `results/topics/<topic-key>/<timestamp-topic>/`: SimpleAutoResearch generation runs, where `topic-key` can be listed with `topics --with-ids`.
- `results/score/<method>/`: native SurveyBench evaluation summaries for an exported method. For single-topic runs, prefer the stable topic key such as `topic11-llm-based-multi-agent` as the method name.
- `results/_native_commands/`: sanitized records of native judge subprocess calls.
- `results/_logs/`: optional batch logs.
- `results/_exports/`: temporary scratch exports from old/manual workflows; safe to delete after the corresponding method directory exists.

## Notes

`--normalize-headings` only adapts markdown numbering for SurveyBench outline parsing. It does not add content. If the generator already emits numbered headings such as `## 1 Introduction` and `### 1.1 Background`, omit it.

The adapter currently handles benchmark-side preparation and native evaluation. High-scoring surveys still require stronger SimpleAutoResearch survey generation: topic-specific outline planning, section-level evidence synthesis, reader-need-oriented self-review, tables/figures, and citation organization.
