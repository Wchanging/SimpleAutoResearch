# ARC-Bench Adapter

Chinese version: [README_zh.md](README_zh.md)

This folder is a local benchmark harness for testing SimpleAutoResearch against
AutoResearchClaw's ARC-Bench tasks. It stays outside `src/simple_ar/` on
purpose: ARC-specific conversion, submission packaging, and scoring should not
become part of the core package.

## What It Does

The adapter supports the workflow we use for server-side benchmark testing:

```text
ARC manifest/rubric
  -> prepared SimpleAutoResearch code-task
  -> code-task run
  -> finalized ARC-style submission
  -> ARC-compatible leaf-level score
```

Important folders:

```text
benchmark/arc_bench/
  adapter.py              # prepare/finalize/score one topic
  batch_runner.py         # run topic sets end-to-end
  prepared/ml/            # generated ML task packages
  runs/ml/                # raw SimpleAutoResearch runs
  submissions/ml/         # finalized submissions + judge outputs
  batch_state/            # resumable batch status
  batch_logs/             # command logs
```

`benchmark/` is gitignored. Only the adapter scripts and lightweight docs are
tracked.

## One-Time Prepare

If prepared packages are missing or your AutoResearchClaw checkout lives in a
different path:

```bash
uv run python benchmark/arc_bench/adapter.py prepare-ml \
  --arc-root /path/to/AutoResearchClaw/experiments/arc_bench
```

Prepare a subset:

```bash
uv run python benchmark/arc_bench/adapter.py prepare-ml \
  --arc-root /path/to/AutoResearchClaw/experiments/arc_bench \
  --topics ML02 ML04 ML10
```

The recommended ML test order is maintained in:

```text
benchmark/arc_bench/prepared/ml/INDEX.md
```

By default, prepared tasks preserve the vanilla ARC-Bench input text: manifest
content plus rubric leaves in readable Markdown. For ablations of
SimpleAutoResearch's task-contract mechanism, you may opt in to an additional
machine-readable contract block:

```bash
uv run python benchmark/arc_bench/adapter.py prepare-ml \
  --arc-root /path/to/AutoResearchClaw/experiments/arc_bench \
  --prepared-root benchmark/arc_bench/prepared/ml_contract \
  --include-contract
```

Do not mix contract-enhanced prepared packages with vanilla ARC-Bench comparison
runs unless the difference is reported explicitly.

## Optional ML Dependencies

Before running the breadth or full ML topic sets, install the common scientific
Python packages so the code-task planner can see useful local libraries instead
of reinventing basic ML utilities:

```bash
uv pip install numpy scipy scikit-learn pandas matplotlib statsmodels networkx imbalanced-learn umap-learn scikit-optimize cma seaborn pytest
```

Quick environment check:

```bash
uv run python -c "import numpy, scipy, sklearn, pandas, matplotlib, statsmodels, networkx, imblearn, umap, skopt, cma; print('arc deps ok')"
```

## Recommended Batch Run

Run the quick confidence pass and continue even if individual topics fail:

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topic-set quick \
  --analyze \
  --score
```

The command above uses the default `proxy` score profile, which is intended for
fast development regression. For paper-facing or AutoResearchClaw-protocol
comparisons, run the same batch with the strict judge profile:

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topic-set quick \
  --analyze \
  --score \
  --score-profile strict
```

Each `run` command creates a separate state file under
`benchmark/arc_bench/batch_state/`, for example:

```text
benchmark/arc_bench/batch_state/20260627-153607-quick.json
```

`batch_state/latest_state.json` records the latest state file. This avoids
mixing multiple benchmark batches while keeping retry commands short.

Topic sets:

```bash
uv run python benchmark/arc_bench/batch_runner.py run --topic-set quick --analyze --score
uv run python benchmark/arc_bench/batch_runner.py run --topic-set breadth --analyze --score
uv run python benchmark/arc_bench/batch_runner.py run --topic-set specialized --analyze --score
uv run python benchmark/arc_bench/batch_runner.py run --topic-set all --analyze --score
```

Strict scoring variants:

```bash
uv run python benchmark/arc_bench/batch_runner.py run --topic-set quick --analyze --score --score-profile strict
uv run python benchmark/arc_bench/batch_runner.py run --topic-set breadth --analyze --score --score-profile strict
uv run python benchmark/arc_bench/batch_runner.py run --topic-set specialized --analyze --score --score-profile strict
uv run python benchmark/arc_bench/batch_runner.py run --topic-set all --analyze --score --score-profile strict
```

Explicit topics:

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topics ML04 ML02 ML06 \
  --analyze \
  --score \
  --score-profile strict
```

Each topic writes a compact runtime/API summary after it finishes or fails:

```text
benchmark/arc_bench/runs/ml/ML04/<run-id>/arc_task_stats.json
benchmark/arc_bench/submissions/ml/ML04/<run-id>/arc_task_stats.json
```

The stats file records wall-clock duration, per-command duration, command logs,
return codes, and aggregated LLM request/token/cost information from code-task,
result analysis, and scoring. The batch state also stores a compact summary, so
`batch_runner.py status` can show duration and token totals without opening the
artifact.

Aggregate one batch into paper-style score/runtime/API means:

```bash
uv run python benchmark/arc_bench/batch_runner.py summarize
```

`summarize` reads the latest batch by default and writes
`<state-file>.summary.json` plus `<state-file>.summary.md`. It reports Code
Development, Code Execution, Result Analysis, Overall, average wall time,
average command time, average LLM calls, and average input/output/total tokens.
Use `--state-file <path>` for an older batch or `--topic-set quick`/`--topics
ML04 ML02` to filter the rows.

The batch runner only finalizes runs whose business status is
`benchmark_passed`. If `--score` is added later and a topic already has a valid
finalized submission, it fills in `judge/` without rerunning the experiment. A
judge generated by one score profile does not satisfy another profile, so adding
`--score-profile strict` later will create/refresh the strict judge output.
For unstable server networks, add `--llm-retry-attempts 5` to override the
prepared TOML retry budget for every `code-task execute` call.
LLM calls omit client-side timeout and provider output-limit parameters by
default. If you need to reintroduce hard bounds for cost control or an
enterprise gateway policy, set positive values before running the batch:

```bash
export SIMPLE_AR_LLM_TIMEOUT_SEC=300
export SIMPLE_AR_MAX_OUTPUT_TOKENS=4096
```

## Refresh Analyze / Score Only

If ML01-ML25 already have completed `code-task execute` runs and you only want
to regenerate result analysis or judge outputs with newer adapter logic, use
`refresh`. It reads `run_dir` entries from an existing state file, skips
init/execute, and writes a separate submission variant instead of overwriting
the previous output.

```bash
uv run python benchmark/arc_bench/batch_runner.py refresh \
  --source-state-file benchmark/arc_bench/batch_state/20260706-011743-all.json \
  --topic-set all \
  --analyze \
  --score \
  --score-profile strict \
  --variant strict-rerun-01
```

Example output layout:

```text
benchmark/arc_bench/submissions/ml/ML02/<run-id>--strict-rerun-01/
benchmark/arc_bench/batch_state/<new-refresh-state>.json
```

Summarize the refreshed result set with the new state file:

```bash
uv run python benchmark/arc_bench/batch_runner.py summarize \
  --state-file benchmark/arc_bench/batch_state/<new-refresh-state>.json
```

The refreshed state records only the new finalize/result-analysis/score
commands. During `summarize`, variant outputs automatically read the source
run's `arc_task_stats.json` and combine source init/execute cost with the new
post-processing cost for `Total Time` and total tokens. The `Postprocess` /
`Postprocess Tokens` columns keep the incremental rerun-only view.

When `--variant` is omitted, the runner derives a unique timestamped variant
from the new state file. Add `--force` only when you intentionally want to
regenerate an existing variant directory.

## Retry

Fresh retry for unfinished topics:

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --topic-set quick \
  --analyze \
  --score \
  --score-profile strict \
  --llm-retry-attempts 5
```

By default, `retry-unfinished` reads `latest_state.json`. To retry a specific
older batch, pass its state file explicitly:

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --state-file benchmark/arc_bench/batch_state/20260627-153607-quick.json \
  --topic-set quick \
  --analyze \
  --score \
  --score-profile strict
```

Continue from the previous run and grant more repair attempts:

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --topic-set quick \
  --analyze \
  --score \
  --score-profile strict \
  --resume-existing \
  --extend-repair-rounds 2
```

Without `--extend-repair-rounds`, exhausted repair budgets are treated as stale
and the runner starts a fresh run.

Check state:

```bash
uv run python benchmark/arc_bench/batch_runner.py status
```

`status` also reads the latest state by default. Use `--state-file <path>` or
`--state-file latest` when you want to inspect a particular batch.

## Manual Single-Topic Flow

Use this when debugging one topic.

```bash
uv run simple-ar code-task init \
  --config benchmark/arc_bench/prepared/ml/ML02/code_task.toml

RUN_DIR=$(ls -td benchmark/arc_bench/runs/ml/ML02/* | head -n 1)

uv run simple-ar code-task execute "$RUN_DIR" \
  --config benchmark/arc_bench/prepared/ml/ML02/code_task.toml \
  --yes
```

Finalize:

```bash
OUT_DIR=benchmark/arc_bench/submissions/ml/ML02/$(basename "$RUN_DIR")

uv run python benchmark/arc_bench/adapter.py finalize \
  --prepared-dir benchmark/arc_bench/prepared/ml/ML02 \
  --run-dir "$RUN_DIR" \
  --output-dir "$OUT_DIR" \
  --force \
  --analyze
```

Score:

```bash
uv run python benchmark/arc_bench/adapter.py score \
  --prepared-dir benchmark/arc_bench/prepared/ml/ML02 \
  --submission-dir "$OUT_DIR/submission" \
  --output-dir "$OUT_DIR/judge"
```

For paper-facing comparisons, use the strict profile, or `arc-native` when you
want the built-in scorer to use the canonical ARC-style strict audit prompt and
leaf-targeted evidence bundle:

```bash
uv run python benchmark/arc_bench/adapter.py score \
  --prepared-dir benchmark/arc_bench/prepared/ml/ML02 \
  --submission-dir "$OUT_DIR/submission" \
  --output-dir "$OUT_DIR/judge_strict" \
  --score-profile strict \
  --strict-reviewers 2 \
  --disagreement-threshold 0.20
```

## Outputs To Inspect

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
    analysis_response.json       # parsed LLM response when --analyze succeeds
    analysis_prompt.txt          # only when --analyze fails JSON parsing
    analysis_raw_response.txt    # only when --analyze fails JSON parsing
  judge/
    evidence_bundle.json         # compact benchmark evidence passed to scorer, including leaf-targeted code evidence
    judge_result.json            # leaf_grades + scoring_summary
    scorecard.md
    score_round_code_response.json
    score_round_code_prompt.txt              # only when scoring fails
    score_round_code_response_attempt_*.json # schema retry attempts only
    score_round_results_response.json
    score_round_results_prompt.txt              # only when scoring fails
    score_round_results_response_attempt_*.json # schema retry attempts only
    reviewer_*.json              # strict / arc-native profile only
    disagreements.json           # strict / arc-native profile only
    adjudication.json            # strict / arc-native profile only, when needed
```

`finalize --analyze` builds the benchmark-facing README/claims from measured
results. `score` has four profiles:

- `proxy` (default): lightweight two-round LLM scorer for development
  regression. It is useful for quick/breadth smoke checks, but should not be
  reported as an AutoResearchClaw strict score.
- `arc-auto`: keeps the ARC-style automatic two-round scorer behavior closer to
  `scripts/judge.py`, including recoverable missing-leaf handling.
- `strict`: runs independent reviewer passes, re-adjudicates per-leaf
  disagreements above the threshold, records the analysis source, and reports
  CD/CE/RA plus overall aggregates. Use this for paper-facing comparisons.
- `arc-native`: uses the same strict two-reviewer/adjudication protocol with a
  canonical ARC-style audit prompt and leaf-targeted evidence bundle. Use the
  separate `judge --judge-command` wrapper only when calling an external native
  AutoResearchClaw judge binary/script.

If a scoring round returns valid JSON with the wrong top-level schema, the
adapter retries once with a stricter `grades` contract and saves retry raw
responses. If the retry still cannot produce a recoverable `grades` array,
scoring fails. If a valid response omits one leaf, that leaf is recorded with
warning and default score `0.5` for the non-strict automatic profiles.

## External Judge

Normally use the built-in `score`. If you specifically want to run an external
ARC-Bench judge, the wrapper is still available:

```bash
uv run python benchmark/arc_bench/adapter.py judge \
  --submission-dir benchmark/arc_bench/submissions/ml/ML02/<run-id>/submission \
  --judge-command "python /path/to/arc_judge.py --submission {submission_dir} --output {output_dir}"
```
