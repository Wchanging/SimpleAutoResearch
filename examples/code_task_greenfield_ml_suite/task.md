# Task: Greenfield ML Experiment Workbench

Build a from-scratch Python project under `generated_project/` that can run a
resource-aware local machine-learning experiment suite. The goal is to test
whether SimpleAutoResearch can plan, generate, review, validate, run, and repair
a non-trivial greenfield codebase without relying on a pre-existing repository.

## Scope

Implement a reusable experiment workbench, not a single script. Treat this as a
small open-source project with a clean package layout, CLI, configuration
system, tests, reports, and extension points. The generated project should be
modular enough that a future contributor can inspect, extend, and replace
datasets/models without rewriting the whole codebase. Prefer real open datasets
when they are already available from installed libraries or local files; do not
require network access or runtime downloads. Deterministic synthetic data is
allowed only as a fallback path and must be reported clearly.

Target implementation scale: medium-sized, roughly a few thousand lines of
well-structured Python when implemented fully. Do not pad the code with
duplicates or boilerplate solely to increase line count; the scale should come
from real modules, tests, configuration, reports, and reusable abstractions.

The benchmark command used by SimpleAutoResearch is:

```bash
python generated_project/main.py --preset smoke --data-source auto
```

The command must finish within the configured timeout and print parseable metric
lines in the form `metric_name: value`. It may also write JSON/Markdown reports
inside the generated project, but stdout metrics are mandatory.

## Functional Requirements

1. Project architecture:
   - Create a proper Python package under `generated_project/`, with clear
     modules for data loading, feature processing, model registry, training,
     evaluation, experiment orchestration, reporting, configuration, utilities,
     and tests/self-checks.
   - Provide a single CLI entrypoint at `generated_project/main.py`.
   - Support subcommands or equivalent modes such as `run`, `list-datasets`,
     `list-models`, `self-check`, and `report`, while keeping the benchmark
     command above as the default smoke path.
   - Include a small documented configuration layer. It may use dataclasses,
     Pydantic when available, JSON, or a compact in-project parser. It should
     support presets such as `smoke`, `standard`, and `server`.
   - Implement registries or factory functions for datasets, feature pipelines,
     models, metrics, and experiment conditions. Avoid hard-coding all behavior
     into `main.py`.
   - Include docstrings and a short generated `README.md` inside
     `generated_project/` explaining how to run the project and how to add a new
     dataset/model.

2. Data layer:
   - Implement a tiered dataset loader:
     1. Prefer packaged open datasets when dependencies are available, such as
        scikit-learn `load_digits`, `load_breast_cancer`, `load_wine`, or other
        locally bundled public datasets.
     2. Support a user-provided local data directory via CLI/config, for example
        CSV/JSONL files under `data/`, without downloading anything at runtime.
     3. Fall back to deterministic synthetic datasets only when packaged/local
        datasets are unavailable.
   - Include at least two classification tasks. At least one task should use a
     real packaged or local open dataset when available.
   - Include one tabular/numeric task and one additional task that is either
     image-like, text-like, or another meaningfully different feature shape.
   - Support train/validation/test splits with fixed seeds.
   - Support controlled label noise or distribution shift for robustness checks.
   - Record dataset provenance, source type, row count, feature count, class
     count, and fallback status in `artifacts/results.json`.
   - Add dataset cards in the generated report that summarize provenance,
     license/source hints when known, preprocessing, split policy, and known
     limitations.

3. Feature and model layer:
   - Implement at least three model or baseline families.
   - Include a trivial baseline, a stronger classical baseline, and a neural or
     neural-like model when the environment supports it.
   - Use a model registry/factory so new models can be added without changing
     the experiment loop.
   - Include at least one feature pipeline abstraction, for example numeric
     normalization, simple bag-of-features, PCA-like dimensionality reduction
     when dependencies allow it, or image-like flattening.
   - If `torch` is installed, use it for a small MLP or compact text model and
     automatically select CUDA when available.
   - If `torch` is not installed, fall back to a deterministic NumPy or standard
     library implementation and state that fallback in the report.
   - Do not install dependencies.

4. Experiment layer:
   - Run multiple conditions under the same split.
   - Include ablations for at least two feature families or training choices.
   - Implement an experiment runner that can run a matrix of dataset x model x
     feature condition x seed, but keep the smoke preset bounded.
   - Support at least two seeds in non-smoke presets. The smoke preset may use
     one seed for speed.
   - Produce condition-level records, not just aggregate metrics.
   - Measure runtime and parameter count.
   - Compute accuracy and macro F1 at minimum.
   - Add at least one robustness or calibration metric, such as robustness drop,
     expected calibration error, or confidence margin.
   - Include a result selector that chooses the best non-trivial condition by
     the configured primary metric while retaining baseline comparisons.

5. Reporting layer:
   - Print a concise Rich-style terminal summary if `rich` is installed, with a
     plain-text fallback otherwise.
   - Write `artifacts/results.json`, `artifacts/report.md`, and
     `artifacts/condition_results.jsonl`.
   - Include at least two Markdown tables in the report: a condition comparison
     table and a dataset/provenance table.
   - Explain which backend path was used: CPU, CUDA, torch, NumPy, or fallback.
   - Explain which dataset source path was used: packaged open dataset, local
     user dataset, or synthetic fallback.
   - Include a short "reproducibility checklist" in the report covering seed,
     dataset source, dependency fallbacks, resource profile, and command.

6. Testing and quality gates:
   - Keep clear module boundaries such as data, features, models, evaluation,
     reporting, configuration, and CLI entrypoint.
   - Include lightweight self-checks or tests that can run without network. The
     `self-check` mode should validate dataset loading, metric math, registry
     entries, config parsing, and report writing.
   - Include at least one negative or edge-case test, such as missing optional
     dependencies, tiny class counts, unknown model names, or local data parse
     errors.
   - Add structured error messages for unsupported dataset/model/preset names.
   - Avoid hidden global state, nondeterministic randomness, or machine-specific
     absolute paths.
   - Keep generated files inside `generated_project/`.
   - Do not rely on notebook-only workflows. The project must run from CLI.

## Required Metrics

The benchmark should emit at least these metric names:

- `best_score`
- `accuracy`
- `macro_f1`
- `baseline_accuracy`
- `neural_accuracy`
- `ablation_gain`
- `robustness_drop`
- `condition_count`
- `task_count`
- `data_size`
- `open_dataset_count`
- `synthetic_fallback_used`
- `test_count`
- `config_preset_count`
- `train_time_sec`
- `inference_time_ms`
- `parameter_count`

If a metric is not applicable because a dependency is unavailable, emit a
numeric fallback value and explain the fallback in `artifacts/report.md`.
For `synthetic_fallback_used`, emit `0` when the smoke run used packaged/local
open data for all primary tasks, and `1` when any primary task had to fall back
to synthetic data.

## Resource Behavior

The project should inspect local resources at runtime and choose a safe profile:

- Laptop/CPU path: small packaged/local dataset slice, short training, no heavy
  dependencies.
- Server/GPU path: still bounded, but may use a slightly larger synthetic
  dataset/local dataset slice or more epochs when CUDA is available.
- The `standard` and `server` presets may scale dataset sizes, seeds, and model
  conditions, but must remain bounded by command-line/config limits.

Do not assume a 3090 or any particular GPU is present. Detect it.
Do not download datasets during benchmark execution; if optional network-backed
dataset helpers exist, they must be disabled by default.

## Acceptance Criteria

- The benchmark command exits with code 0.
- All required metrics are printed and parseable.
- The best non-trivial condition beats the trivial baseline on the deterministic
  smoke preset.
- `condition_count`, `task_count`, `test_count`, and `config_preset_count` are
  greater than zero and reflect real generated functionality.
- The smoke preset uses at least one packaged or local open dataset when such a
  dataset is available in the current Python environment.
- `python generated_project/main.py self-check` exits with code 0 when the
  generated project is valid.
- The implementation is modular enough to support later external-agent repair
  or reviewer passes.
- No network access, dependency installation, or writes outside
  `generated_project/` are required.
