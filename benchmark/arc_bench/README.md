# ARC-Bench Adapter for SimpleAutoResearch

This directory is a local, ignored benchmark harness. It is intentionally kept
outside `src/simple_ar/` so ARC-Bench experiments do not become part of the
open-source package surface.

The adapter is a hot-pluggable bridge:

1. Read an ARC-Bench topic manifest and rubric from a configurable ARC-Bench
   location.
2. Generate a SimpleAutoResearch greenfield `code-task` task and TOML config.
3. After the SimpleAutoResearch run finishes, project its artifacts into an
   ARC-Bench-style `submission/` layout.
4. Optionally run ARC-Bench's own judge command and save raw stdout/stderr plus
   a machine-readable judge result.

It does not add a `simple-ar` command and does not import project internals.

## Layout

```text
benchmark/arc_bench/
  adapter.py             # standalone CLI
  config.example.toml    # local defaults; copy/edit as needed
  prepared/              # generated task/config packages
  runs/                  # raw SimpleAutoResearch benchmark runs
  submissions/           # finalized ARC-style submissions
```

The whole `benchmark/` tree is gitignored.

## Requirements

The adapter uses the Python standard library plus `PyYAML` for ARC manifests.
If your environment does not have it:

```bash
uv add pyyaml
```

or install it in the server environment you use for benchmark runs.

## Prepare One Topic

```bash
uv run python benchmark/arc_bench/adapter.py prepare \
  --arc-root AutoResearchClaw/experiments/arc_bench \
  --topic ML02 \
  --output-dir benchmark/arc_bench/prepared/ML02 \
  --simple-ar-output-root benchmark/arc_bench/runs/ML02
```

This writes:

```text
benchmark/arc_bench/prepared/ML02/
  task.md
  code_task.toml
  manifest.json
  rubric.json
  arc_meta.json
  commands.md
```

Run the generated task like this. `code-task execute` needs the run manifest created
by `code-task init`, so initialize first and pass the printed run directory into
execute:

```bash
uv run simple-ar code-task init --config benchmark/arc_bench/prepared/ML02/code_task.toml
uv run simple-ar code-task execute <RUN_DIR> \
  --config benchmark/arc_bench/prepared/ML02/code_task.toml --yes
```

The adapter config writes raw runs under `simple_ar_output_root`, usually
`benchmark/arc_bench/runs/<topic>/`.

## Prepare All ML Topics

```bash
uv run python benchmark/arc_bench/adapter.py prepare-ml \
  --arc-root AutoResearchClaw/experiments/arc_bench
```

By default this discovers every `config/ml/manifests/ML*.yaml` topic and writes:

```text
benchmark/arc_bench/prepared/ml/
  INDEX.md
  ML01/
    task.md
    code_task.toml
    manifest.json
    rubric.json
    arc_meta.json
    commands.md
  ...
  ML25/
```

Raw SimpleAutoResearch runs are configured under:

```text
benchmark/arc_bench/runs/ml/<topic>/
```

Each generated `code_task.toml` keeps `benchmark.metric_directions` scoped to
the metrics declared by that topic manifest, plus `runtime_sec = "resource"`.
Structural completeness signals such as dataset counts or hypothesis coverage
may still be produced by a submission, but they are not predeclared as required
benchmark objectives for every topic.

To generate only a subset:

```bash
uv run python benchmark/arc_bench/adapter.py prepare-ml \
  --arc-root /path/to/AutoResearchClaw/experiments/arc_bench \
  --topics ML02 ML04 ML10
```

## Finalize A Run

After SimpleAutoResearch finishes, convert the run into an ARC-style submission:

```bash
uv run python benchmark/arc_bench/adapter.py finalize \
  --prepared-dir benchmark/arc_bench/prepared/ML02 \
  --run-dir benchmark/arc_bench/runs/ML02/<run-id> \
  --output-dir benchmark/arc_bench/submissions/ML02/<run-id>
```

`finalize` will not overwrite a non-empty output directory by default. Use a new
`--output-dir`, or pass `--force` only when you intentionally want to replace
the previous submission package.

`finalize` always writes a generic `result_analysis/` folder with deterministic
metric and claim-audit artifacts. By default it does not spend LLM tokens and it
keeps the submission README/claims close to the original run artifacts. If you
want the adapter to call the configured LLM and regenerate the benchmark-facing
README and claims from measured metrics, add `--analyze`:

```bash
uv run python benchmark/arc_bench/adapter.py finalize \
  --prepared-dir benchmark/arc_bench/prepared/ML02 \
  --run-dir benchmark/arc_bench/runs/ML02/<run-id> \
  --output-dir benchmark/arc_bench/submissions/ML02/<run-id> \
  --analyze
```

Use `--analysis-model <model>` only when you want to override
`SIMPLE_AR_MODEL`. The analysis artifacts are saved under:

```text
result_analysis/
  analysis_context.json
  metric_summary.json
  claims.json
  analysis_report.md
  analysis_audit.json
  analysis_prompt.txt        # only when --analyze is used
  analysis_raw_response.txt  # only when --analyze is used
  analysis_response.json      # only when --analyze is used
  llm_usage.jsonl             # only when --analyze is used
  llm_usage_summary.json      # only when --analyze is used
```

If the model does not return valid JSON, `finalize --analyze` stops and keeps
`analysis_raw_response.txt` for diagnosis. This is intentional: invalid analysis
should be inspected instead of silently replacing the submission with a weak
fallback.

The finalizer writes:

```text
submission/
  code/
  results/metrics.json
  README.md
  claims.json
  reproduce.sh
stage-14/
  experiment_summary.json
arc_adapter_meta.json
```

You can then run ARC-Bench's own judge against the finalized output, or copy
the `submission/` package into ARC-Bench's expected `results/<framework>/<topic>`
layout.

## Run An External Judge

The adapter does not vendor ARC-Bench's judge and does not assume where it lives.
If you have a judge command available, run it through the explicit wrapper:

```bash
uv run python benchmark/arc_bench/adapter.py judge \
  --submission-dir benchmark/arc_bench/submissions/ML02/<run-id> \
  --judge-command "python /path/to/arc_judge.py --submission {submission_dir} --output {output_dir}"
```

The wrapper saves:

```text
judge/
  stdout.txt
  stderr.txt
  judge_result.json
```

Supported placeholders are `{submission_dir}`, `{submission}`, `{output_dir}`,
and `{output}`. The command is intentionally configured outside
SimpleAutoResearch because different ARC-Bench checkouts or server environments
may use different judge entrypoints.

## Config File

All CLI flags can be provided through a TOML file:

```bash
uv run python benchmark/arc_bench/adapter.py prepare \
  --config benchmark/arc_bench/config.example.toml \
  --topic ML02
```

Important paths:

- `arc_root`: path to `experiments/arc_bench`, wherever it lives on the target
  machine.
- `output_dir`: where prepared task/config packages are written.
- `simple_ar_output_root`: where raw SimpleAutoResearch benchmark runs should
  be written. The default is under `benchmark/arc_bench/runs/<topic>` so raw
  runs stay separate from finalized `submissions/<topic>/<run-id>` packages.

This keeps server paths flexible; `AutoResearchClaw/` does not need to live
inside this repository.

## Adapter Boundary

This adapter is deliberately separate from `src/simple_ar/`.

- Adapter responsibilities: read ARC manifests/rubrics, prepare SimpleAR
  code-task packages, finalize submissions, and optionally invoke external ARC
  judge commands.
- SimpleAutoResearch responsibilities: generate or modify code, run validation
  and benchmark commands, repair failures, record memory/review artifacts, and
  produce generic run artifacts.
- Shared future boundary: V2.7 may introduce a generic result-analysis layer
  that this adapter can call, but ARC-specific rubric projection should remain
  here unless it becomes useful for multiple benchmark suites.

If a future benchmark needs a different submission shape, add a sibling adapter
under `benchmark/<suite>/` instead of adding suite-specific branches to the core
pipeline.
