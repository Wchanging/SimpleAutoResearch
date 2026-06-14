# Greenfield Task: Lightweight Text Classifier Experiment Suite

Build a medium-light local Python project from scratch that trains, evaluates,
and compares several deterministic text-classification conditions. The goal is
to exercise the V2.5 greenfield path with enough engineering structure to test
planning, multi-file implementation, metric export, and review, while still
remaining CPU-only and laptop-friendly.

## Required Behavior

- Generate a deterministic local dataset in code. Use a multi-class text
  classification problem with at least four classes, controlled lexical noise,
  and some distractor tokens so the task is not perfectly separable.
- Split the data into train/validation/test sets with fixed seeds. Keep the
  split logic reusable and avoid mutating caller-owned data in place.
- Implement at least four conditions under the same split:
  - majority-class baseline
  - keyword-rule baseline
  - unigram bag-of-words multinomial Naive Bayes
  - unigram + bigram multinomial Naive Bayes
  - optional character n-gram or smoothing ablation if budget allows
- Evaluate all conditions on the same deterministic test split.
- Report the best condition, the margin over the strongest baseline, and a
  condition-level ablation table in code or structured output.
- Print parseable metrics as `metric_name: number` lines from `main.py`.
- Keep all code local and auditable. Do not access the network, user home
  directory, shell commands, credentials, or external datasets.
- Keep exactly one authoritative experiment orchestration path. Prefer
  `main.py` as a thin CLI wrapper, `generated_experiment/runner.py` as the only
  `run_experiment` owner, and helper modules for data, features, models,
  metrics, evaluation, and reporting.
- Target 8-10 purposeful files. Do not collapse all domain logic into one large
  runner unless the file budget forces it.

## Required Metrics

The generated project must emit at least:

- `accuracy`
- `macro_f1`
- `majority_accuracy`
- `keyword_accuracy`
- `unigram_accuracy`
- `bigram_accuracy`
- `char_ngram_accuracy`
- `ablation_gain`
- `best_model_margin`
- `condition_count`
- `data_size`
- `train_time_sec`
- `inference_time_ms`
- `parameter_count`

## Resource Limits

- CPU only.
- Target runtime under 60 seconds on a normal laptop.
- Keep the project compact but non-trivial, ideally 8-10 purposeful files and
  under roughly 1,800 generated lines.
- Avoid large generated data or model artifacts.
- Do not install dependencies. Prefer the Python standard library.

## Quality Expectations

- The best model condition should usually outperform the strongest baseline on
  the deterministic test split.
- The task should not be trivially perfect: if all trained models reach 1.0
  accuracy, the dataset is probably too easy and the implementation should add
  deterministic noise or distractors.
- The implementation should be readable enough for a user to inspect quickly.
- The code should separate data generation, feature extraction, model logic,
  metrics, evaluation orchestration, and CLI output.
- The reportable conclusion should be grounded in the generated metrics, not in
  hard-coded claims.
