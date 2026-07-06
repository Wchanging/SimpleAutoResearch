# ARC-Bench ML Prepared Packages

- ARC root: `AutoResearchClaw\experiments\arc_bench`
- Run root: `benchmark\arc_bench\runs\ml`
- Topic count: `25`

## Recommended Test Order

This order is for SimpleAutoResearch hardening, not an ARC-Bench official
difficulty ranking. The idea is to first cover the known good path, then add
classical ML breadth, and finally move into more specialized or potentially
resource-sensitive topics.

Batch runner shortcuts:

- `--topic-set quick`: Quick Confidence Pass.
- `--topic-set breadth` or `--topic-set next`: Next Breadth Pass.
- `--topic-set specialized`, `--topic-set high-risk`, or `--topic-set higher-risk`: Specialized / Higher-Risk Pass.
- `--topic-set all`: all groups in the order above.

### Quick Confidence Pass

Run these first when checking a new branch or server environment:

1. `ML04` - KNN scaling classification. Already useful as a smoke test for
   greenfield generation, benchmark execution, repair, finalize, and result
   analysis.
2. `ML02` - noisy non-linear regression ensembles. Good for checking metric
   tables, hypothesis coverage, and rubric-backed README/claims generation.
3. `ML06` - logistic-regression learning-rate schedules. A small supervised
   learning task that should be fast and deterministic enough for regression
   checks.
4. `ML10` - cross-validation reliability on small samples. Useful for testing
   repeated splits, variance reporting, and analysis quality.
5. `ML08` - imbalance-handling strategies. Good for validating class-imbalance
   metrics and optional `imbalanced-learn` usage.

### Next Breadth Pass

After the confidence pass works, continue in this order:

1. `ML15` - feature selection with injected noise features.
2. `ML18` - post-hoc calibration on tabular classifiers.
3. `ML01` - dropout regularization on shallow tabular MLPs.
4. `ML05` - dimensionality reduction and cluster preservation.
5. `ML11` - outlier detection under controlled anomaly injection.
6. `ML12` - clustering on non-convex and anisotropic shapes.
7. `ML13` - Gaussian Process kernel choices.

### Specialized / Higher-Risk Pass

These are better after the framework is stable, because they are more likely to
stress dependencies, runtime, stochastic evaluation, or task interpretation:

1. `ML03` - derivative-free optimization comparison.
2. `ML09` - Bayesian vs grid/random hyperparameter search.
3. `ML16` - bandit robustness under drifting rewards.
4. `ML20` - classical forecaster robustness on seasonal time series.
5. `ML22` - active learning strategies.
6. `ML24` - online binary classification under concept drift.
7. `ML25` - Lorenz-63 short-horizon forecasting.
8. `ML14` - conformal intervals on heteroscedastic regression.
9. `ML19` - semi-supervised learning on small tabular datasets.
10. `ML21` - causal discovery for small Gaussian linear-SEM DAGs.
11. `ML23` - learning-to-rank on synthetic query-document benchmarks.
12. `ML07` - text feature extraction. Run later unless the server environment
    already has the needed text data path or network/cache assumptions settled.
13. `ML17` - topic-model comparison on 20newsgroups subsets. Run after the
    text-data/cache assumptions are settled because it may need the same
    sklearn text dataset availability as ML07.

| Topic | Config | Task | Run root |
| --- | --- | --- | --- |
| `ML01` | `benchmark\arc_bench\prepared\ml\ML01\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML01\task.md` | `benchmark\arc_bench\runs\ml\ML01` |
| `ML02` | `benchmark\arc_bench\prepared\ml\ML02\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML02\task.md` | `benchmark\arc_bench\runs\ml\ML02` |
| `ML03` | `benchmark\arc_bench\prepared\ml\ML03\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML03\task.md` | `benchmark\arc_bench\runs\ml\ML03` |
| `ML04` | `benchmark\arc_bench\prepared\ml\ML04\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML04\task.md` | `benchmark\arc_bench\runs\ml\ML04` |
| `ML05` | `benchmark\arc_bench\prepared\ml\ML05\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML05\task.md` | `benchmark\arc_bench\runs\ml\ML05` |
| `ML06` | `benchmark\arc_bench\prepared\ml\ML06\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML06\task.md` | `benchmark\arc_bench\runs\ml\ML06` |
| `ML07` | `benchmark\arc_bench\prepared\ml\ML07\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML07\task.md` | `benchmark\arc_bench\runs\ml\ML07` |
| `ML08` | `benchmark\arc_bench\prepared\ml\ML08\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML08\task.md` | `benchmark\arc_bench\runs\ml\ML08` |
| `ML09` | `benchmark\arc_bench\prepared\ml\ML09\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML09\task.md` | `benchmark\arc_bench\runs\ml\ML09` |
| `ML10` | `benchmark\arc_bench\prepared\ml\ML10\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML10\task.md` | `benchmark\arc_bench\runs\ml\ML10` |
| `ML11` | `benchmark\arc_bench\prepared\ml\ML11\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML11\task.md` | `benchmark\arc_bench\runs\ml\ML11` |
| `ML12` | `benchmark\arc_bench\prepared\ml\ML12\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML12\task.md` | `benchmark\arc_bench\runs\ml\ML12` |
| `ML13` | `benchmark\arc_bench\prepared\ml\ML13\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML13\task.md` | `benchmark\arc_bench\runs\ml\ML13` |
| `ML14` | `benchmark\arc_bench\prepared\ml\ML14\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML14\task.md` | `benchmark\arc_bench\runs\ml\ML14` |
| `ML15` | `benchmark\arc_bench\prepared\ml\ML15\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML15\task.md` | `benchmark\arc_bench\runs\ml\ML15` |
| `ML16` | `benchmark\arc_bench\prepared\ml\ML16\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML16\task.md` | `benchmark\arc_bench\runs\ml\ML16` |
| `ML17` | `benchmark\arc_bench\prepared\ml\ML17\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML17\task.md` | `benchmark\arc_bench\runs\ml\ML17` |
| `ML18` | `benchmark\arc_bench\prepared\ml\ML18\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML18\task.md` | `benchmark\arc_bench\runs\ml\ML18` |
| `ML19` | `benchmark\arc_bench\prepared\ml\ML19\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML19\task.md` | `benchmark\arc_bench\runs\ml\ML19` |
| `ML20` | `benchmark\arc_bench\prepared\ml\ML20\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML20\task.md` | `benchmark\arc_bench\runs\ml\ML20` |
| `ML21` | `benchmark\arc_bench\prepared\ml\ML21\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML21\task.md` | `benchmark\arc_bench\runs\ml\ML21` |
| `ML22` | `benchmark\arc_bench\prepared\ml\ML22\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML22\task.md` | `benchmark\arc_bench\runs\ml\ML22` |
| `ML23` | `benchmark\arc_bench\prepared\ml\ML23\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML23\task.md` | `benchmark\arc_bench\runs\ml\ML23` |
| `ML24` | `benchmark\arc_bench\prepared\ml\ML24\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML24\task.md` | `benchmark\arc_bench\runs\ml\ML24` |
| `ML25` | `benchmark\arc_bench\prepared\ml\ML25\code_task.toml` | `benchmark\arc_bench\prepared\ml\ML25\task.md` | `benchmark\arc_bench\runs\ml\ML25` |
