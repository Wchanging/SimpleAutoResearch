"""Fresh, author-code-assisted fixed RCP comparison, not a whole-paper replication.

The adapter is standard-library-only. The worker runs in the user-specified
prepared author environment; no downloads, installation or model editing occur.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

REVISION = "0caefc117d7e933227deecb4814fd1bc7f4478e5"
METRICS = ("coverage", "cond_cov_x_error", "wsc", "exact_region_size")


def resolve_python(value):
    """Make argv absolute without dereferencing a virtualenv's Python symlink."""
    found = shutil.which(value) if not any(separator in value for separator in ("/", "\\")) else value
    return os.path.abspath(os.path.expanduser(found)) if found else None


def summarize(rows, seeds):
    """Check complete paired observations before computing descriptive summaries."""
    paired = {}
    for row in rows:
        identity = (row["seed"], row["method"])
        if identity in paired or identity[0] not in seeds or identity[1] not in {"Ball", "RCP-Ball"}:
            raise ValueError("Duplicate or unexpected seed/method observation")
        if row["rows"] != 21613 or row["splits"] != [10123, 3639, 2048, 5803] or row["output_dim"] != 2:
            raise ValueError("The measured House dataset/split differs from this declared case")
        for name in METRICS:
            value = row["metrics"].get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Missing or non-finite author metric: {name}")
        paired[identity] = row["metrics"]
    if set(paired) != {(seed, method) for seed in seeds for method in ("Ball", "RCP-Ball")}:
        raise ValueError("Incomplete paired measurements")
    summary = {}
    for name in METRICS:
        baseline = [paired[(seed, "Ball")][name] for seed in seeds]
        candidate = [paired[(seed, "RCP-Ball")][name] for seed in seeds]
        delta = [b - a for a, b in zip(baseline, candidate)]
        summary[name] = {
            "Ball_mean": statistics.mean(baseline), "RCP_Ball_mean": statistics.mean(candidate),
            "paired_delta": statistics.mean(delta),
            "paired_delta_seed_se": statistics.stdev(delta) / math.sqrt(len(delta)) if len(delta) > 1 else None,
        }
    return summary


def worker(args):
    # Import only in the explicitly selected scientific runtime.
    from omegaconf import OmegaConf
    from rcp.configs.config import get_config
    from rcp.models.tuning import get_tuning
    from rcp.run_experiment import run
    from rcp.utils import configure_logging
    from rcp.utils.run_config import RunConfig
    from rcp.datamodules import load_datamodule
    import torch

    configure_logging()
    output = args.output_root.resolve()
    config = get_config(OmegaConf.create(dict(
        name="fixed-house-pair", log_base_dir=str(output),
        data_dir=str(args.author_path.resolve() / "data"), datasets="single-feldman-house",
        tuning_type="rcp_all", selected_models=["Mean"], device="cpu", manager="sequential",
        nb_workers=1, fast=False, max_epochs=args.max_epochs, repeat_tuning=len(args.seeds),
        precomputation_level=0, progress_bar=False, print_config=False,
    )))
    OmegaConf.save(config, output / "author_config.yaml")
    hparams = dict(next(iter(get_tuning(config))))
    grid = list(hparams["conformal_grid"])
    baseline = next(row for row in grid if row["method"] == "Ball")
    candidate = next(row for row in grid if row["method"] == "RCP-Ball"
        and row.get("qestimator") == "kernel" and row.get("qestimator_params") == {"bandwidth_x": 0.1}
        and row.get("qestimator_dataset") == "calib" and row.get("adjustment_func") == "difference")
    hparams["conformal_grid"] = [baseline, candidate]
    measurements = []
    for seed in args.seeds:
        rc = RunConfig(config=config, dataset_group="feldman", dataset="house", run_id=seed,
                       hparams=copy.deepcopy(hparams))
        dm = load_datamodule(rc)
        for row in run(rc, 0):
            values = {key: float(value) if math.isfinite(float(value)) else None for key, value in row.metrics.items()}
            measurements.append({"seed": seed, "method": row.hparams["posthoc_method"], "metrics": values,
                "rows": dm.total_size, "splits": [len(dm.data_train), len(dm.data_val), len(dm.data_calib), len(dm.data_test)],
                "output_dim": dm.output_dim})
        (output / "measurements.json").write_text(json.dumps(measurements, indent=2), encoding="utf-8")
        print(f"Completed paired seed {seed}", flush=True)
    summary = summarize(measurements, args.seeds)
    (output / "summary.json").write_text(json.dumps({"summary": summary,
        "selected_methods": [baseline, candidate], "seeds": args.seeds,
        "protocol": OmegaConf.to_container(config, resolve=True),
        "scope": "fixed author-code subset on House; not exact published-table or whole-paper reproduction",
        "uncertainty": "seed variability conditional on this dataset, not a population confidence interval",
        "independent_metric_reimplementation": False,
        "runtime": {"python": sys.version, "torch": torch.__version__, "device": "cpu"},
    }, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--author-path", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable, help="Prepared author Python; defaults to the current Python.")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--seeds", nargs="+", type=int, default=list(range(10)))
    parser.add_argument("--max-epochs", type=int, default=5000)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.max_epochs < 1 or len(set(args.seeds)) != len(args.seeds) or any(seed < 0 for seed in args.seeds):
        parser.error("Use a positive epoch cap and distinct non-negative seeds")
    if not (args.author_path / "rcp" / "run_experiment.py").is_file() or not (args.author_path / "data" / "feldman" / "house.csv").is_file():
        parser.error("Prepare the fixed author source and full House data first; see this case's README")
    if args.worker:
        worker(args)
        return
    revision = subprocess.run(["git", "-C", str(args.author_path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    if revision != REVISION:
        parser.error(f"This fixed case requires author revision {REVISION}; got {revision}")
    dirty = subprocess.run(["git", "-C", str(args.author_path), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True, text=True, check=True).stdout
    if dirty.strip():
        parser.error("This fixed case requires unmodified tracked author source and data")
    python = resolve_python(args.python)
    if not python or not Path(python).is_file():
        parser.error("The selected scientific Python does not exist; prepare it or omit --python")
    args.output_root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="observations-", dir=args.output_root.resolve()))
    env = dict(os.environ)
    env["PYTHONPATH"] = str(args.author_path.resolve()) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    command = [python, str(Path(__file__).resolve()), "--worker", "--author-path", str(args.author_path.resolve()),
               "--output-root", str(output), "--max-epochs", str(args.max_epochs), "--seeds", *map(str, args.seeds)]
    started = time.monotonic()
    subprocess.run(command, cwd=args.author_path.resolve(), env=env, check=True)
    measured = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    measured.update(source_revision=revision, fresh_output=True, elapsed_sec=time.monotonic() - started,
                    runtime_python=python, baseline_candidate_share_predictor=True)
    (output / "summary.json").write_text(json.dumps(measured, indent=2), encoding="utf-8")
    if os.environ.get("SIMPLE_AR_OUTPUT_DIR"):
        delivery = Path(os.environ["SIMPLE_AR_OUTPUT_DIR"])
        delivery.mkdir(parents=True, exist_ok=True)
        for filename in ("measurements.json", "summary.json"):
            shutil.copyfile(output / filename, delivery / filename)
    print(f"RAW_MEASUREMENTS {output / 'measurements.json'}", flush=True)
    for name, values in measured["summary"].items():
        for statistic, value in values.items():
            if value is not None:
                print(f"METRIC {name}_{statistic}={value:.12g}", flush=True)


if __name__ == "__main__":
    main()
