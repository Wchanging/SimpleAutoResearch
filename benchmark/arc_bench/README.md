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

The historical SimpleAutoResearch profile injects both public manifest content
and readable rubric leaves into `task.md`. That is useful for internal
diagnostics, but it is a rubric-informed generation setting rather than a
rubric-hidden comparison setting. For ablations of SimpleAutoResearch's
task-contract mechanism, you may opt in to an additional machine-readable
contract block:

```bash
uv run python benchmark/arc_bench/adapter.py prepare-ml \
  --arc-root /path/to/AutoResearchClaw/experiments/arc_bench \
  --prepared-root benchmark/arc_bench/prepared/ml_contract \
  --include-contract
```

Do not mix contract-enhanced prepared packages with the default prepared
profile unless the difference is reported explicitly.

### Rubric-Hidden Generation Sensitivity

To test whether generation-time rubric exposure changes outcomes, prepare an
isolated `manifest_only` package. It retains the public research question,
background, hypotheses, conditions, datasets, metrics, and runtime interface,
but removes rubric leaves and the rubric-derived machine-readable contract from
the task passed to the generation, review, and repair agents. The rubric remains
available only to the evaluator during scoring.

```bash
uv run python benchmark/arc_bench/adapter.py prepare-ml \
  --arc-root /path/to/AutoResearchClaw/experiments/arc_bench \
  --topics ML01 ML04 ML06 ML08 ML11 ML14 ML17 ML19 ML22 ML25 \
  --prepared-root benchmark/arc_bench/prepared_manifest_only/ml \
  --run-root benchmark/arc_bench/runs_manifest_only/ml \
  --generation-input manifest_only
```

Run this package with separate run, submission, log, and state roots. Keep all
generation settings and the evaluator fixed relative to a fresh
`rubric_informed` counterpart. Do not reuse plans or code from a rubric-informed
run: those artifacts have already observed the rubric.

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

For quick checks or paper-facing main tables, prefer AutoResearchClaw's native
`judge.py` path:

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topic-set quick \
  --analyze \
  --native-score \
  --native-score-model gpt-4o
```

For a two-reviewer manual strict audit pass, use `manual-strict`. You can set
two reviewer models, for example one Claude model and one Codex/GPT model:

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topic-set quick \
  --analyze \
  --score \
  --score-profile manual-strict \
  --strict-reviewer-models claude-opus-4-6,gpt-5.4 \
  --strict-reviewer-apis chat,responses \
  --strict-adjudicator-model gpt-5.4
```

If a reviewer model does not support the provider's Responses API surface
(for example `local:convert_request_failed`), use `--strict-reviewer-apis` to
choose `chat` or `responses` per reviewer.

Each `run` command creates a separate state file under
`benchmark/arc_bench/batch_state/`, for example:

```text
benchmark/arc_bench/batch_state/20260627-153607-quick.json
```

`batch_state/latest_state.json` records the latest state file. This avoids
mixing multiple benchmark batches while keeping retry commands short.

Topic sets:

```bash
uv run python benchmark/arc_bench/batch_runner.py run --topic-set quick --analyze --native-score --native-score-model gpt-4o
uv run python benchmark/arc_bench/batch_runner.py run --topic-set breadth --analyze --native-score --native-score-model gpt-4o
uv run python benchmark/arc_bench/batch_runner.py run --topic-set specialized --analyze --native-score --native-score-model gpt-4o
uv run python benchmark/arc_bench/batch_runner.py run --topic-set all --analyze --native-score --native-score-model gpt-4o
```

Explicit topics:

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topics ML04 ML02 ML06 \
  --analyze \
  --native-score \
  --native-score-model gpt-4o
```

### Bounded Parallel Runs

`batch_runner.py run` can execute independent topics concurrently without
interleaving their detailed Rich/CLI output. The controller is the only writer
of the batch state file; each worker retains complete `init.log`,
`execute.log`, `finalize.log`, and `score.log` files under its own topic log
directory.

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topic-set all \
  --jobs 2 \
  --progress \
  --analyze \
  --score \
  --score-profile manual-strict \
  --strict-reviewer-models claude-opus-4-6,gpt-5.4 \
  --strict-reviewer-apis chat,responses \
  --strict-adjudicator-model gpt-5.4 \
  --state-file benchmark/arc_bench/batch_state/all-parallel.json
```

Use `--jobs 2` as the conservative starting point: each topic can make many
LLM calls and the manual-strict score itself invokes two reviewers. Parallel
runs are quiet by default; `--progress` displays one reusable `tqdm` row per
worker with the active topic, its real runner stage (`init`, `execute`,
`finalize`, or `score`), and elapsed time. It deliberately does not invent
percentages inside the variable-length `execute` stage. Use
`--quiet-subprocess` for the same log-only behavior in a serial run. To resume
an interrupted one-shot batch without revisiting terminal successes or
failures, repeat the same command with `--skip-any-result`.

Subset ranges and exclusions are also supported:

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topic-range ML01-ML10 \
  --exclude-topics ML02 ML06 \
  --analyze \
  --native-score \
  --native-score-model gpt-4o
```

For low-cost ablations, keep each result in a separate state file or variant.
The following switches are passed through to `simple-ar code-task execute`:

```bash
# No structured failure-graph context in repair prompts.
uv run python benchmark/arc_bench/batch_runner.py run \
  --topics ML06 ML09 ML10 \
  --repair-context raw_logs_only \
  --analyze \
  --native-score \
  --native-score-model gpt-4o \
  --state-file benchmark/arc_bench/batch_state/ablation-no-failure-graph.json

# No previous repair memory in repair prompts.
uv run python benchmark/arc_bench/batch_runner.py run \
  --topics ML06 ML09 ML10 \
  --no-repair-memory \
  --analyze \
  --native-score \
  --native-score-model gpt-4o \
  --state-file benchmark/arc_bench/batch_state/ablation-no-repair-memory.json

# Minimal task-contract prompt view, useful as a Plan-then-Code style baseline.
uv run python benchmark/arc_bench/batch_runner.py run \
  --topics ML06 ML09 ML10 \
  --contract-context minimal \
  --repair-rounds 0 \
  --analyze \
  --native-score \
  --native-score-model gpt-4o \
  --state-file benchmark/arc_bench/batch_state/ablation-minimal-contract.json

# Fewer greenfield planning-review iterations.
uv run python benchmark/arc_bench/batch_runner.py run \
  --topics ML06 ML09 ML10 \
  --planning-review-rounds 0 \
  --analyze \
  --native-score \
  --native-score-model gpt-4o \
  --state-file benchmark/arc_bench/batch_state/ablation-no-plan-review.json
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
average command time, repair counts, execution attempts, failure-type counts,
average LLM calls, and average input/output/total tokens.
Use `--state-file <path>` for an older batch or `--topic-set quick`/`--topics
ML04 ML02` to filter the rows.

For paper evidence tables, export a compact repair/fidelity summary without
rerunning experiments:

```bash
uv run python benchmark/arc_bench/batch_runner.py evidence \
  --state-file benchmark/arc_bench/batch_state/20260711-native-gpt4o-bundle-all.json \
  --topic-set all \
  --judge-source native
```

This writes `<state-file>.evidence.json` and `<state-file>.evidence.md` with
repair totals, failure taxonomy, failure graph examples, and candidate runs for
requirement-trace inspection.

The batch runner only finalizes runs whose business status is
`benchmark_passed`. If `--score` is added later and a topic already has a valid
finalized submission, it fills in `judge/` or `judge_manual_strict/` without
rerunning the experiment. A judge generated by one score profile does not
satisfy another profile, so adding `--score-profile manual-strict` later will
create/refresh the manual strict judge output.
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
  --score-profile manual-strict \
  --variant manual-strict-rerun-01
```

Example output layout:

```text
benchmark/arc_bench/submissions/ml/ML02/<run-id>--manual-strict-rerun-01/
benchmark/arc_bench/batch_state/<new-refresh-state>.json
```

Summarize the refreshed result set with the new state file:

```bash
uv run python benchmark/arc_bench/batch_runner.py summarize \
  --state-file benchmark/arc_bench/batch_state/<new-refresh-state>.json \
  --judge-source manual-strict
```

The refreshed state records only the new finalize/result-analysis/score
commands. During `summarize`, variant outputs automatically read the source
run's `arc_task_stats.json` and combine source init/execute cost with the new
post-processing cost for `Total Time` and total tokens. The `Postprocess` /
`Postprocess Tokens` columns keep the incremental rerun-only view.

When `--variant` is omitted, the runner derives a unique timestamped variant
from the new state file. Add `--force` only when you intentionally want to
regenerate an existing variant directory.

### Native AutoResearchClaw Judge Rerun

For paper-facing comparisons, prefer running AutoResearchClaw's own
`experiments/arc_bench/scripts/judge.py` in `--full` mode after finalization.
The adapter provides a thin wrapper named `native-score`: it loads this
project's `.env`, maps `SIMPLE_AR_MODEL` to `OPENAI_MODEL` when needed, and
lets `--native-score-model` override `ARC_JUDGE_MODEL`.
If AutoResearchClaw is not checked out under this repository as
`AutoResearchClaw/experiments/arc_bench`, pass `--arc-root` explicitly. The
same option is available on `batch_runner.py run`, `refresh`, and
`retry-unfinished` when `--native-score` is enabled.
For native judge runs, `gpt-4o` is the recommended default unless you are
intentionally running a model ablation. AutoResearchClaw's native parser expects
a single JSON object and is less tolerant of extra text from newer models.

Single-topic example using an existing finalized ML04 output:

```bash
uv run python benchmark/arc_bench/adapter.py native-score \
  --arc-root /path/to/AutoResearchClaw/experiments/arc_bench \
  --prepared-dir benchmark/arc_bench/prepared/ml/ML04 \
  --run-dir benchmark/arc_bench/submissions/ml/ML04/20260706-011752-arc-bench-ml04 \
  --output-dir benchmark/arc_bench/submissions/ml/ML04/20260706-011752-arc-bench-ml04/judge_native \
  --topic ML04 \
  --model gpt-4o \
  --full \
  --debug
```

To reuse an existing all-topic batch and only rerun native judging into a new
variant directory:

```bash
uv run python benchmark/arc_bench/batch_runner.py refresh \
  --arc-root /path/to/AutoResearchClaw/experiments/arc_bench \
  --source-state-file benchmark/arc_bench/batch_state/20260706-011743-all.json \
  --topic-set all \
  --native-score \
  --native-score-model gpt-4o \
  --variant native-full-rerun-01 \
  --score-timeout 3600
```

This writes outputs like:

```text
benchmark/arc_bench/submissions/ml/ML04/<run-id>--native-full-rerun-01/judge_native/judge_result.json
benchmark/arc_bench/batch_state/<new-refresh-state>.json
```

Use `--backend local` with `adapter.py native-score` only for path smoke tests;
real comparisons should use the native LLM backend.

## Retry

Fresh retry for unfinished topics:

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --topic-set quick \
  --analyze \
  --native-score \
  --native-score-model gpt-4o \
  --llm-retry-attempts 5
```

By default, `retry-unfinished` reads `latest_state.json`. To retry a specific
older batch, pass its state file explicitly:

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --state-file benchmark/arc_bench/batch_state/20260627-153607-quick.json \
  --topic-set quick \
  --analyze \
  --native-score \
  --native-score-model gpt-4o
```

Continue from the previous run and grant more repair attempts:

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --topic-set quick \
  --analyze \
  --native-score \
  --native-score-model gpt-4o \
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

For a two-reviewer manual strict audit, use `manual-strict`. It loads
AutoResearchClaw's bundled `manual_strict_audit_prompt.md` and runs independent
reviewers, per-leaf disagreement detection, and adjudication inside this
adapter:

```bash
uv run python benchmark/arc_bench/adapter.py score \
  --prepared-dir benchmark/arc_bench/prepared/ml/ML02 \
  --submission-dir "$OUT_DIR/submission" \
  --output-dir "$OUT_DIR/judge_manual_strict" \
  --score-profile manual-strict \
  --strict-reviewer-models claude-opus-4-6,gpt-5.4 \
  --strict-adjudicator-model gpt-5.4 \
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
  judge_manual_strict/
    evidence_bundle.json
    judge_result.json
    scorecard.md
    reviewer_*.json
    disagreements.json
    adjudication.json
  judge_native/
    judge_result.json            # copied result from AutoResearchClaw scripts/judge.py --full
    judge_debug.json             # native debug artifact when --debug is enabled
    native_judge_meta.json       # wrapper command, mode, duration, and copied paths
    stdout.txt
    stderr.txt
```

`finalize --analyze` builds the benchmark-facing README/claims from measured
results. For formal reporting, use only two judge paths:

- `native-score`: calls AutoResearchClaw's bundled `scripts/judge.py` and writes
  `judge_native/`. Use this for the native judge path in paper tables.
- `score --score-profile manual-strict`: runs the adapter's two-reviewer manual
  strict audit using the bundled ARC manual prompt and writes
  `judge_manual_strict/`. Use this for strict review or appendix diagnostics.
  Use `--strict-reviewer-models` to choose separate reviewer models.

If a scoring round returns valid JSON with the wrong top-level schema, the
adapter retries once with a stricter `grades` contract and saves retry raw
responses. If the retry still cannot produce a recoverable `grades` array,
scoring fails. If a valid response omits one leaf, that leaf is recorded with
warning and default score `0.5` for the internal lightweight automatic scorer;
this fallback is not used by `manual-strict`.

## External Judge

Normally use `native-score` for AutoResearchClaw's bundled ARC-Bench judge. If
you specifically want to run a different external judge command, the lower-level
wrapper is still available:

```bash
uv run python benchmark/arc_bench/adapter.py judge \
  --submission-dir benchmark/arc_bench/submissions/ml/ML02/<run-id>/submission \
  --judge-command "python /path/to/arc_judge.py --submission {submission_dir} --output {output_dir}"
```
