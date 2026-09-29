# TabM real-paper research case

This one-folder case tests the V2.9 research loop against the official [TabM code](https://github.com/yandex-research/tabm) and [ICLR 2025 paper](https://arxiv.org/pdf/2410.24210), rather than the older Mammoth demonstration. It is a bounded California Housing *research improvement* task, not a claim that TabM itself is reproduced or that any new method is effective. The separate Adult data probe is not silently pooled with California's regression metric.

The case files define the goal (`research.toml`), edit/evaluation boundary (`code_task.toml`), researcher requirements (`task.md`), and external measurement adapter (`run_tabm.py`). The project, paper PDF, data and Python environment are machine assets under `runs/assets`, not tracked example outputs or credentials in `.env`. A missing asset is a preflight failure. `runs/tabm-research` holds actual session records.

The supplied PDF is a local reading input even with remote full-text fetching disabled. This case permits extraction of up to 40 pages and 140 text chunks so the available 37-page paper is not cut off by the generic 20-page parser default. Extraction is not the same as model review of every page; source coverage and appendix-only claims still need checking.

## Prepared-server run

The server's `runs/assets/tabm/paper` is a trimmed copy of upstream commit `28e47ae3` with the official California and Adult arrays from the [TabM data archive](https://huggingface.co/datasets/rototoHF/tabm-data/blob/main/data.tar). The prepared Python is `runs/assets/tabm-venv/bin/python`. These assets are ignored by Git: syncing the repository alone does not create them on a fresh machine. Do not run this configuration on an unprepared host or repoint it at arbitrary data and call the results comparable.

From the repository root, after checking that the preflight assets and API configuration exist:

```bash
uv run --no-sync simple-ar research-session --config examples/tabm_research/research.toml
```

The model chooses whether and how to revise the method inside `bin/model.py` and `lib/deep.py`; no algorithmic change is preselected. The framework bounds the session to two research follow-up rounds, eight measured processes and 3600 process seconds. Each training command has a 330-second adapter timeout inside a 420-second process limit. Those are ceilings, not completion estimates. Model token/request budget is not preset. The configured first condition is only seed 0. `seeds = [0]` with the declared `seed_flag` records a paired seed protocol that permits an evidence-driven *new* seed later; it does not run or require that supplement by default. Any supplement still needs an analysis reason, a fresh baseline/candidate pair, remaining process budget, and an accepted research decision.

The example sets a 32768-token per-call output ceiling because reasoning models can consume the output allowance before producing final structured text. This is not a session API-spend cap. A provider with a smaller per-call limit must use a compatible value; a `finish_reason=length` pause can be resumed with `--max-output-tokens` after checking the provider limit, without repeating completed reading or training.

## Evidence and limitations

The adapter runs the official training entrypoint with a fresh output directory, then scores `predictions.npz` against the fixed validation labels independently of the edited model's JSON report. It rejects missing/misaligned/non-finite predictions or a conflicting official validation metric. It emits validation RMSE and training seconds as framework metrics. Both baseline and candidate must pass the same adapter. The raw official log and report can include test metrics, so this is **not a strict blind held-out evaluation**; do not use those values to select a candidate or overstate generalization. The current external environment reuses Torch 2.8 rather than the paper's Torch 2.0.1 lock; a scoped PyTorch checkpoint compatibility setting is used only for this isolated subprocess. This run is a functional and scientific-reasoning acceptance case, not a faithful original-environment replication.

Server compatibility probes completed the unmodified official California evaluation in about 120 seconds (validation RMSE 0.4443464341) and Adult in about 71 seconds (validation accuracy 0.8610471365). These pilot values only bound cost and verify setup; they are **not** automatically reusable research-session baseline artifacts. A completed session and a generated report still require review of method provenance, baseline comparability, decisions, measured effects, and claim quality.
