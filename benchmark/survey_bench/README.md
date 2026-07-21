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

For final high-budget runs, add `--thorough`. This uses a separate config set
and result namespace, so balanced smoke-test outputs are not overwritten:

```powershell
uv run python benchmark\survey_bench\adapter.py run-topic --topic-id topic11 --thorough
uv run python benchmark\survey_bench\adapter.py resume-latest --topic-id topic11 --thorough --from-stage report
uv run python benchmark\survey_bench\adapter.py finalize-latest --topic-id topic11 --thorough --eval-content --model gpt-4o
uv run python benchmark\survey_bench\adapter.py summarize-batch --thorough
```

### Ablation: w/o Review-Guided Revision

This ablation removes only the report-stage reviewer/revision loop. Retrieval,
paper selection, adaptive outline planning, writer budget, deterministic figures,
post-draft citation/metric/claim audits, and the native SurveyBench evaluator are
unchanged. When a completed full-system thorough run already exists, reuse its
upstream artifacts and rerun only `report`; the original `report.md` is
preserved and the ablation report is written as a sibling variant:

```powershell
uv run python benchmark\survey_bench\adapter.py resume-latest --topic-id topic01 --thorough --without-review-guided-revision --reuse-full-run
uv run python benchmark\survey_bench\adapter.py finalize-latest --topic-id topic01 --thorough --variant w-o-review-guided-revision --reuse-full-run --eval-content --model gpt-4o
uv run python benchmark\survey_bench\adapter.py summarize-batch --thorough --variant w-o-review-guided-revision
```

For a sequential 20-topic report-only ablation in PowerShell:

```powershell
1..20 | ForEach-Object {
  uv run python benchmark\survey_bench\adapter.py resume-latest --topic-id ("topic{0:D2}" -f $_) --thorough --without-review-guided-revision --reuse-full-run
}
```

The reused report package is stored under
`<full-run>/08-report/variants/w-o-review-guided-revision/`; the exported method
and score output remain isolated under `SurveyBench/data/<topic-key>-thorough-w-o-review-guided-revision/`
and `benchmark/survey_bench/results/ablations/w-o-review-guided-revision/`.
Use `run-topic --without-review-guided-revision` only when no compatible full
run exists and a clean end-to-end ablation is required.

Use `resume-latest` when a run already completed search/read but failed in a
later stage. It reuses the latest topic run directory and passes the matching
balanced or thorough config to `simple-ar resume`, avoiding a fresh literature
search. The default is `--from-stage report --to-stage report`; override the
stage range when you need to rerun read or search.

The thorough profile reads configs from
`benchmark/survey_bench/configs/topics-thorough/`, writes generation runs under
`benchmark/survey_bench/results/topics-thorough/<topic-key>/`, exports to
`SurveyBench/data/<topic-key>-thorough/`, and writes scores under
`benchmark/survey_bench/results/score-thorough/<topic-key>/`.

Quiz-based evaluation is optional and significantly more expensive than content
evaluation. Run it only when you explicitly need SurveyBench's quiz protocol.

Run a bounded single-topic SimpleAutoResearch survey generation:

```bash
uv run simple-ar run \
  --config benchmark/survey_bench/configs/topics/topic11-llm-based-multi-agent.toml
```

The topic configs live under `benchmark/survey_bench/configs/topics/`, one TOML
per SurveyBench topic key. Both `topics/` and `topics-thorough/` use `gpt-4o`
for SimpleAutoResearch survey generation. Native SurveyBench evaluation examples
also use `gpt-4o`, giving new runs a single-model generation-and-evaluation
protocol that is easier to compare with the reported SurveyBench setting.
Historical results generated with another model remain valid development
artifacts, but must not be labelled as this `gpt-4o` protocol without rerunning.
The default `topics/` configs are balanced for iteration. The
`topics-thorough/` configs increase retrieval, read, and report budgets for
longer surveys with more cited papers, figures, and tables.

The config uses the built-in `survey_long` report template, which is intended
for longer reader-oriented academic surveys rather than compact technical
briefs. It also raises retrieval and read-stage budgets so a single-topic
smoke run can draw on roughly 20-30 papers instead of a tiny source set. The
same config enables deterministic SVG figure generation for long surveys. Its
report stage uses `cost_profile = "balanced"` and `outline_strategy =
"adaptive"` so sections receive a topic-specific outline, topic-specific goals,
and bounded source batches by default; switch to `cost_profile = "thorough"`
only for final high-budget runs.

The same configs also set `[report.longform]` targets for paper count, report
length, citation density, and tables. These are generic SimpleAutoResearch
long-form synthesis controls rather than SurveyBench-only prompts: each run
writes `08-report/longform/` artifacts for paper selection, taxonomy, outline
planning, citation coverage, and visual planning so quality problems can be
audited without re-running the judge. The older `[report.survey]` section is
still accepted as a compatibility alias, but new configs should use
`[report.longform]`.

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
