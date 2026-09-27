# TabM California Housing research case

The supplied paper is *TabM: Advancing Tabular Deep Learning with Parameter-Efficient Ensembling* (ICLR 2025). The source and its California Housing data/split are engineering-provided assets. The system should read and reason from them; neither the paper choice nor the initial evaluation protocol is an autonomous discovery. Do not mistake a cheap compatibility probe for a paper reproduction.

Study the official TabM implementation and relevant prior work. Select a feasible, testable candidate rather than implementing a prescribed algorithm. The baseline and candidate must use the same fixed train/validation split, seed, primary metric, and hardware environment. Limit edits to the authorized method files. Do not change dataset files, metric definitions, evaluator adapter, experimental configuration, or the supplied paper. No extra download or package installation during the research session.

The adapter reports independently recomputed validation RMSE and training time. A negative result, implementation failure, or single favorable seed should be described honestly. A supplemental seed or method revision requires an explicit reason, comparable baseline/candidate conditions, and available process budget; it is permitted, not mandatory. A report should distinguish paper claims, measured results, resource costs, and uncertainty. Do not select candidates by held-out test performance.

The official training script computes test metrics and leaves them in raw outputs. The current framework does not enforce a sealed holdout, so a validation-only stdout metric is **not** proof of blinding. Treat test numbers as off-limits for selection and state this limitation in the final analysis.
