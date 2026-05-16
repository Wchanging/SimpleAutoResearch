# Task: Improve The Tiny Digits MLP Baseline

Improve the small NumPy MLP classifier in this repository while keeping the benchmark local, deterministic, and lightweight.

## Goal

Increase validation accuracy and macro F1 on the bundled `sklearn.datasets.load_digits` benchmark.

The benchmark command is:

```bash
python benchmark.py
```

## Constraints

- Do not download data.
- Do not require a GPU.
- Do not add heavy dependencies.
- Keep the public helper functions importable from `digits_mlp`.
- Keep the benchmark deterministic.
- Keep CPU runtime small enough for repeated local code-task runs.
- Prefer clear, reviewable changes over large rewrites.

## Useful Directions

Potential improvements include:

- tuning hidden size, learning rate, epoch count, or batch size;
- improving the training loop while preserving readability;
- adding simple learning-rate scheduling or validation-aware stopping;
- improving numerical stability;
- adding a small test if the public API changes.

## Evidence To Report

After changes, run:

```bash
python benchmark.py
```

Use the printed `accuracy`, `macro_f1`, `train_time_sec`, and `inference_time_ms` lines as the main before/after evidence.
