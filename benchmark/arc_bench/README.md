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

## Recommended Batch Run

Run the quick confidence pass and continue even if individual topics fail:

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topic-set quick \
  --analyze \
  --score
```

Topic sets:

```bash
uv run python benchmark/arc_bench/batch_runner.py run --topic-set quick --analyze --score
uv run python benchmark/arc_bench/batch_runner.py run --topic-set breadth --analyze --score
uv run python benchmark/arc_bench/batch_runner.py run --topic-set specialized --analyze --score
uv run python benchmark/arc_bench/batch_runner.py run --topic-set all --analyze --score
```

Explicit topics:

```bash
uv run python benchmark/arc_bench/batch_runner.py run \
  --topics ML04 ML02 ML06 \
  --analyze \
  --score
```

The batch runner only finalizes runs whose business status is
`benchmark_passed`. If `--score` is added later and a topic already has a valid
finalized submission, it fills in `judge/` without rerunning the experiment.

## Retry

Fresh retry for unfinished topics:

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --topic-set quick \
  --analyze \
  --score
```

Continue from the previous run and grant more repair attempts:

```bash
uv run python benchmark/arc_bench/batch_runner.py retry-unfinished \
  --topic-set quick \
  --analyze \
  --score \
  --resume-existing \
  --extend-repair-rounds 2
```

Without `--extend-repair-rounds`, exhausted repair budgets are treated as stale
and the runner starts a fresh run.

Check state:

```bash
uv run python benchmark/arc_bench/batch_runner.py status
```

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
    analysis_prompt.txt          # when --analyze is used
    analysis_raw_response.txt    # when --analyze fails
  judge/
    judge_result.json            # leaf_grades + scoring_summary
    scorecard.md
    score_round_code_prompt.txt
    score_round_code_response.json
    score_round_results_prompt.txt
    score_round_results_response.json
```

`finalize --analyze` builds the benchmark-facing README/claims from measured
results. `score` is the ARC-compatible two-round LLM judge:

- Code Development leaves are graded from code.
- Code Execution and Result Analysis leaves are graded from summary, metrics,
  claims, and writeup.
- `overall_strict` and `results_only` are deterministic weighted aggregates of
  the model's leaf scores.

If a scoring round returns invalid JSON, scoring fails. If a valid response
omits one leaf, that leaf is recorded with warning and default score `0.5`,
matching AutoResearchClaw's `judge.py` behavior.

## External Judge

Normally use the built-in `score`. If you specifically want to run an external
ARC-Bench judge, the wrapper is still available:

```bash
uv run python benchmark/arc_bench/adapter.py judge \
  --submission-dir benchmark/arc_bench/submissions/ml/ML02/<run-id>/submission \
  --judge-command "python /path/to/arc_judge.py --submission {submission_dir} --output {output_dir}"
```
