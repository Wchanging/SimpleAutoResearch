"""Measurement/resource adapter for Mammoth tpami2023; no training loop here.

Run from the isolated Mammoth project directory. Pass --data-root (containing
CIFAR100/cifar-100-python), then the official model arguments. This adapter is
engineering-provided infrastructure, not an autonomously discovered method.
"""
import argparse
import importlib
import json
import math
import os
from pathlib import Path
import sys
import time


def summarize(rows):
    """Convert measured percentage accuracies to fractions; omit unseen tasks."""
    if not rows or any(len(row) < i + 1 for i, row in enumerate(rows)):
        raise ValueError("A measured accuracy row is required after every training task.")
    matrix = [[float(value) / 100 for value in row[:i + 1]] for i, row in enumerate(rows)]
    if any(not math.isfinite(value) or not 0 <= value <= 1 for row in matrix for value in row):
        raise ValueError("Measured accuracies must be finite percentages between 0 and 100.")
    means = [sum(row) / len(row) for row in matrix]
    metrics = {"accuracy": means[-1], "average_incremental_accuracy": sum(means) / len(means)}
    n = len(matrix)
    if n > 1:
        metrics["backward_transfer"] = sum(matrix[-1][j] - matrix[j][j] for j in range(n - 1)) / (n - 1)
        metrics["forgetting"] = sum(max(matrix[i][j] for i in range(j, n)) - matrix[-1][j] for j in range(n - 1)) / (n - 1)
    # Existing metric provenance/report tools can expose every measured cell.
    for stage, row in enumerate(matrix, start=1):
        for task, value in enumerate(row, start=1):
            metrics[f"accuracy_after_task_{stage}_on_task_{task}"] = value
    return matrix, metrics


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=os.environ.get("SIMPLE_AR_OUTPUT_DIR"))
    settings, model_args = parser.parse_known_args()
    if settings.output is None:
        parser.error("--output or SIMPLE_AR_OUTPUT_DIR is required")
    data_root, output = settings.data_root.resolve(), settings.output.resolve()
    if not (data_root / "CIFAR100/cifar-100-python").is_dir():
        parser.error("Prepare the official CIFAR-100 data before running an experiment.")
    sys.path.insert(0, str(Path.cwd()))
    import torch
    torch.set_num_threads(2)
    scheduler = torch.optim.lr_scheduler.MultiStepLR

    def compatible_scheduler(*positional, verbose=False, **kwargs):
        # tpami2023 passes verbose=False; Torch 2.8 removed this logging option.
        return scheduler(*positional, **kwargs)

    torch.optim.lr_scheduler.MultiStepLR = compatible_scheduler
    import utils.conf as conf
    conf.base_path_dataset = lambda: str(data_root) + os.sep
    from datasets import get_dataset
    from datasets.utils import continual_dataset
    from models import get_model
    from utils import training
    selector = argparse.ArgumentParser(add_help=False)
    selector.add_argument("--model", required=True)
    name = selector.parse_known_args(model_args)[0].model
    args = importlib.import_module("models." + name).get_parser().parse_args(model_args)
    if args.dataset != "seq-cifar100" or args.seed is None or args.disable_log or not args.nowand:
        parser.error("This example requires seq-cifar100, an explicit seed, logging enabled and --nowand 1.")
    conf.set_random_seed(args.seed)
    dataset = get_dataset(args)
    args.n_epochs = args.n_epochs if args.n_epochs is not None else dataset.get_epochs()
    args.batch_size = args.batch_size if args.batch_size is not None else dataset.get_batch_size()
    args.minibatch_size = args.minibatch_size if args.minibatch_size is not None else dataset.get_minibatch_size()
    loader = continual_dataset.DataLoader

    def limited_loader(*positional, **kwargs):
        kwargs["num_workers"] = 2
        return loader(*positional, **kwargs)

    continual_dataset.DataLoader = limited_loader
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    class MeasurementLogger(training.Logger):
        def write(self, configuration):
            if len(self.fullaccs) != dataset.N_TASKS:
                raise ValueError("The complete task sequence has not been measured.")
            matrix, metrics = summarize(self.fullaccs)
            record = {"accuracy_unit": "fraction", "row_semantics": "seen tasks after each training task",
                      "class_il_accuracy": matrix, "metrics": metrics, "configuration": configuration,
                      "duration_sec": time.monotonic() - started,
                      "limitations": ["Runtime dependencies differ from the original paper environment.",
                                      "Task logging is not an intra-training checkpoint or resume mechanism."]}
            (output / "continual_results.json").write_text(json.dumps(record, indent=2, allow_nan=False), encoding="utf-8")
            for key, value in metrics.items():
                print(f"METRIC {key}={value}", flush=True)

    training.Logger = MeasurementLogger
    model = get_model(args, dataset.get_backbone(), dataset.get_loss(), dataset.get_transform())
    training.train(model, dataset, args)


if __name__ == "__main__":
    main()
