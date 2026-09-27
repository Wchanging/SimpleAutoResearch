"""Run the supplied TabM training code and expose validation-only metrics.

This is an evaluator adapter, not a proposed research method. The original
report and training log remain available as artifacts for independent audit.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from uuid import uuid4

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 in the paper environment
    import tomli as tomllib  # type: ignore[no-redef]

import numpy as np


def _validated_metric(metrics: dict[str, object], name: str) -> float:
    value = metrics.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Official validation report has no numeric {name!r} metric.")
    measured = float(value)
    if not math.isfinite(measured):
        raise ValueError(f"Official validation report has a non-finite {name!r} metric.")
    return measured


def _measured_validation(project: Path, output: Path, dataset: str) -> float:
    """Score fixed validation labels outside the editable training entrypoint."""
    with np.load(output / "predictions.npz") as predictions:
        if "val" not in predictions:
            raise ValueError("TabM did not save validation predictions.")
        predicted = np.asarray(predictions["val"]).reshape(-1)
    labels = np.asarray(np.load(project / "data" / dataset / "Y_val.npy")).reshape(-1)
    if predicted.shape != labels.shape or not np.isfinite(predicted).all():
        raise ValueError("Validation predictions are missing, non-finite or misaligned with fixed labels.")
    if dataset == "california":
        return float(np.sqrt(np.mean(np.square(predicted - labels))))
    return float(np.mean((predicted >= 0.5) == labels))


def main() -> None:
    import tomli_w

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=("california", "adult"), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--timeout", type=int, default=330)
    parser.add_argument("--output", type=Path, default=os.environ.get("SIMPLE_AR_OUTPUT_DIR"))
    args = parser.parse_args()
    if args.seed < 0 or args.timeout < 1 or args.output is None:
        parser.error("A non-negative seed, positive timeout and --output/SIMPLE_AR_OUTPUT_DIR are required.")

    project = Path.cwd().resolve()
    source_config = project / "exp" / "tabm" / args.dataset / "0-evaluation" / "0.toml"
    if not (project / "bin" / "model.py").is_file() or not source_config.is_file():
        parser.error("Run from the prepared TabM paper project with its official evaluation config.")
    if not (project / "data" / args.dataset / "READY").is_file():
        parser.error(f"The original {args.dataset} data split is not prepared.")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = tomllib.loads(source_config.read_text(encoding="utf-8"))
    config["seed"] = args.seed
    # A fresh config path prevents a prior attempt's checkpoint from being read
    # as evidence for this candidate or seed.
    generated_config = output / f"official_{uuid4().hex}" / "0.toml"
    generated_config.parent.mkdir(parents=True)
    generated_config.write_text(tomli_w.dumps(config), encoding="utf-8")
    official_output = generated_config.with_suffix("")
    log_path = output / "training.log"
    environment = os.environ.copy()
    environment.setdefault("OMP_NUM_THREADS", "4")
    # The old paper script reloads a checkpoint it just wrote without passing
    # weights_only. PyTorch >=2.6 changed that default. Scope the compatibility
    # switch to this fresh, isolated subprocess; never load a supplied checkpoint.
    import torch

    torch_version = tuple(int(part) for part in torch.__version__.split("+", 1)[0].split(".")[:2])
    if torch_version >= (2, 6):
        environment["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

    started = time.monotonic()
    print(f"Running TabM {args.dataset} validation condition with seed {args.seed}.", flush=True)
    try:
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                [sys.executable, "bin/model.py", str(generated_config)],
                cwd=project, env=environment, stdout=log, stderr=subprocess.STDOUT,
                timeout=args.timeout, check=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"TabM training exceeded {args.timeout} seconds; see {log_path}.") from exc
    elapsed = time.monotonic() - started
    if completed.returncode:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
        raise RuntimeError(f"TabM training returned {completed.returncode}:\n{tail}")

    report_path = official_output / "report.json"
    if not report_path.is_file():
        raise RuntimeError("TabM completed without an official report.json artifact.")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    validation = report.get("metrics", {}).get("val", {})
    if not isinstance(validation, dict):
        raise ValueError("TabM official report has no validation metrics object.")
    metric_name = "rmse" if args.dataset == "california" else "accuracy"
    reported = _validated_metric(validation, metric_name)
    primary = _measured_validation(project, official_output, args.dataset)
    if not math.isclose(primary, reported, rel_tol=1e-5, abs_tol=1e-6):
        raise ValueError(f"Official validation {metric_name} differs from independently scored predictions.")
    shutil.copy2(report_path, output / "official_report.json")
    (output / "adapter_result.json").write_text(json.dumps({
        "dataset": args.dataset,
        "seed": args.seed,
        "upstream_config": str(source_config),
        "run_config": str(generated_config),
        "validation_metric": metric_name,
        "validation_value": primary,
        "train_seconds": elapsed,
        "torch_version": torch.__version__,
        "stdout_metric_scope": "validation_only",
        "raw_official_artifacts_contain_test_metrics": True,
    }, indent=2, allow_nan=False), encoding="utf-8")
    print(f"METRIC {metric_name}={primary}", flush=True)
    print(f"METRIC train_seconds={elapsed}", flush=True)


if __name__ == "__main__":
    main()
