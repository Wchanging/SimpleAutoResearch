"""Run asset preparation and two CPU training steps through the shared executor.

This is environment verification, not a research session or scientific result.
Run using the framework environment; --python selects the separate Torch env.
"""

import argparse
import json
import os
from pathlib import Path

from simple_ar.core.budget import BudgetLedger
from simple_ar.core.process import ProcessSpec, run_process


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parent
    interpreter, data_root = str(args.python.resolve()), str(args.data_root.resolve())
    environment = dict(os.environ, CUDA_VISIBLE_DEVICES="", OMP_NUM_THREADS="2",
                       MKL_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2")
    ledger = BudgetLedger({"process_invocations": 2, "process_wall_seconds": 240},
                          storage_path=args.output / "budget.json")
    commands = [
        ("prepare", 180, [interpreter, str(root / "prepare.py"), "--data-root", data_root]
         + (["--download"] if args.download else [])),
        ("cpu-probe", 60, [interpreter, str(root / "train.py"), "--data-root", data_root,
            "--output", str((args.output / "training").resolve()), "--seed", "0",
            "--device", "cpu", "--batch-size", "2", "--probe-steps", "2"]),
    ]
    records = []
    for name, timeout, command in commands:
        print(f"Starting {name} (timeout {timeout}s)", flush=True)
        result = run_process(ProcessSpec(command, root, timeout, env=environment,
            output_dir=(args.output / name).resolve(), attempt_id=name), budget_ledger=ledger)
        records.append({"action": name, "returncode": result.returncode,
                        "stop_reason": result.stop_reason, "duration_sec": result.duration_sec})
        (args.output / "preflight.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        print(json.dumps(records[-1]), flush=True)
        if result.returncode != 0 or result.stop_reason is not None:
            print(f"Inspect logs in {args.output / name}; no automatic retry.", flush=True)
            return 1
    print("CPU preflight passed; no scientific acceptance or GPU authorization implied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
