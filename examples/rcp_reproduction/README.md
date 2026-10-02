# ICML 2025 RCP: prepared, finite reproduction

Reproduce a **declared author-code-assisted subset** of
[Rectifying Conformity Scores for Better Conditional Coverage](https://proceedings.mlr.press/v267/plassier25a.html):
full House data, a Mean predictor, Ball versus one fixed kernel RCP-Ball,
ten paired seeds. Conditional diagnostics and prediction-region size are reported
together. This is not an innovation task, exact paper-table reproduction,
whole-paper replication or a PaperBench score.

## Prepare the scientific environment

The research assistant and scientific environment may be separate. The adapter
uses current Python unless `--python` is provided; this Linux configuration
explicitly selects a prepared runtime. On Windows use `venv/Scripts/python.exe`
in the command, or omit `--python` if the current Python has the author dependencies.
Paths/interpreters belong in this case's TOML, not global `.env`.

From the repository root, obtain the fixed official source and paper:

```bash
mkdir -p runs/assets/rcp
git clone https://github.com/stat-ml/rcp.git runs/assets/rcp/author
git -C runs/assets/rcp/author switch --detach 0caefc117d7e933227deecb4814fd1bc7f4478e5
curl --fail --location https://raw.githubusercontent.com/mlresearch/v267/main/assets/plassier25a/plassier25a.pdf \
  --output runs/assets/rcp/paper.pdf
uv venv --python 3.13 runs/assets/rcp/venv
uv pip install --python runs/assets/rcp/venv/bin/python -r runs/assets/rcp/author/requirements.txt
```

Read the author's README/license before installing. The full pinned requirements
include CUDA packages; these commands are **not a minimal CPU lock**. For CPU-only
hosts, deliberately prepare a compatible CPU environment following the author
requirements and record omissions/versions. Do not blindly install several GiB
of accelerator dependencies on a small disk. The adapter does not install or
download anything, and missing source, data or dependencies fail before measurement.
House data is included in the author source at `data/feldman/house.csv`.

The acceptance environment uses Python 3.12.3 and Torch 2.7.1+cpu rather than the
author's Python 3.13.2. Its ~127-second historical probe only bounds that server's
cost, not every machine's runtime or this product workflow's success.

## Run and inspect

After checking data, environment, available disk/CPU and global API settings:

```bash
uv run simple-ar research-session --config examples/rcp_reproduction/research.toml
```

Each measured invocation creates a fresh observations directory under
`runs/rcp-reproduction`; it does not load old training outputs. The two methods
share each fitted predictor and split. Normal author training/metrics are retained
(`fast=false`, 5000-epoch cap with early stopping, `precomputation_level=0`).
The framework's 600-second process ceiling is a safety limit, not a cost estimate.
If a preflight shows it is insufficient, change that task limit before running;
do not shrink the data, metrics, seeds or epoch cap merely to pass a timeout.

Inspect `measurements.json`, `summary.json`, `author_config.yaml`, process logs,
and the canonical `results.json` attachment list. This case declares measurements
and runtime/summary as process-output attachments, so report review can reread
registered windows rather than assuming stdout contains every seed. The adapter
copies these two completed files into its invocation's `SIMPLE_AR_OUTPUT_DIR`;
this is not a global `.env` setting or independent metric verification. Inspect
analysis, report and audit. Seed SE describes split/fit variability conditional
on this dataset, not population uncertainty or significance. Author metrics are
not independently reimplemented. `baseline_policy=skip` means there is no second
framework baseline command: **both Ball and RCP-Ball are measured inside the same
explicit paired adapter**, not that Ball is omitted.

Use the printed session-root to resume; do not create another task. Completed
valid measurements should be reused when writing is retried. Changes to seeds,
method, data or environment require an honest revised protocol, not silent reuse.
An assembled report or passing audit is not proof of scientific accuracy.
