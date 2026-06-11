from __future__ import annotations

import argparse
from pathlib import Path

from review_pipeline.experiment import format_metric_lines, run_experiment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the medium review pipeline example.")
    parser.add_argument(
        "--config",
        default="configs/experiment.json",
        help="Path to an experiment JSON config.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output and print metrics only.",
    )
    parser.add_argument(
        "--show-progress",
        action="store_true",
        help="Force progress output even when another wrapper sets quiet defaults.",
    )
    args = parser.parse_args(argv)

    show_progress = args.show_progress or not args.quiet
    metrics = run_experiment(Path(args.config), show_progress=show_progress)
    print(format_metric_lines(metrics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
