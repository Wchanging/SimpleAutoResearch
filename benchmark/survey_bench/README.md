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

The legacy `run-topic` / `resume-latest` generation commands are retired with the eight-stage CLI. Generate new reports through `research-session`, then use `export-report --report-file` explicitly. Historical reports, variant namespaces, validation and native evaluation remain supported; this adapter no longer launches or resumes research workflows.

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

Aggregate all completed topic summaries into one cross-topic table:

```bash
uv run python benchmark/survey_bench/adapter.py summarize-batch
```

Aggregate a topic range, for example topic10 through topic20:

```bash
uv run python benchmark/survey_bench/adapter.py summarize-batch --from-topic 10 --to-topic 20
```

The batch summary scans `benchmark/survey_bench/results/score/topic*/summary.json` and writes macro means to:

```text
benchmark/survey_bench/results/score/batch_summary/batch_summary.json
benchmark/survey_bench/results/score/batch_summary/batch_summary.md
```

Outputs are written under:

```text
benchmark/survey_bench/results/score/topic11-llm-based-multi-agent/
```

Recommended local results layout:

- `results/topics/<topic-key>/<timestamp-topic>/`: SimpleAutoResearch generation runs, where `topic-key` can be listed with `topics --with-ids`.
- `results/topics-thorough/<topic-key>/<timestamp-topic>/`: high-budget generation runs created with `--thorough`.
- `results/score/<method>/`: native SurveyBench evaluation summaries for an exported method. For single-topic runs, prefer the stable topic key such as `topic11-llm-based-multi-agent` as the method name.
- `results/score-thorough/<topic-key>/`: native SurveyBench evaluation summaries for `--thorough` exports.
- `results/_native_commands/`: sanitized records of native judge subprocess calls.
- `results/_logs/`: optional batch logs.
- `results/_exports/`: temporary scratch exports from old/manual workflows; safe to delete after the corresponding method directory exists.

## Notes

`--normalize-headings` only adapts markdown numbering for SurveyBench outline parsing. It does not add content. If the generator already emits numbered headings such as `## 1 Introduction` and `### 1.1 Background`, omit it.

The adapter currently handles benchmark-side preparation and native evaluation. High-scoring surveys still require stronger SimpleAutoResearch survey generation: topic-specific outline planning, section-level evidence synthesis, reader-need-oriented self-review, tables/figures, and citation organization.
