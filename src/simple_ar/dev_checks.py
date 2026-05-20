from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class CheckGroup:
    """A named unittest target group used by the developer check runner.

    Args:
        description: Human-readable guidance shown by ``--list``.
        targets: Arguments passed after ``python -m unittest``.
    """

    description: str
    targets: tuple[str, ...]


CHECK_GROUPS: dict[str, CheckGroup] = {
    "quick": CheckGroup(
        description="Fast sanity checks for contracts, metrics, prompts, and CLI parsing.",
        targets=(
            "tests.test_contracts",
            "tests.test_metrics",
            "tests.test_prompts",
            "tests.test_cli",
        ),
    ),
    "code-task": CheckGroup(
        description="Core code-task workflow tests, including workspace, mapping, patching, validation, run, and repair.",
        targets=("tests.test_code_task",),
    ),
    "code-task-examples": CheckGroup(
        description="Realistic bundled code-task example tests. Run after changing examples or benchmark behavior.",
        targets=("tests.test_code_task_examples",),
    ),
    "pipeline": CheckGroup(
        description="Pipeline, stage contracts, and experiment runner tests.",
        targets=(
            "tests.test_pipeline",
            "tests.test_experiment_runner",
            "tests.test_search_stage",
        ),
    ),
    "research": CheckGroup(
        description="Literature, retrieval, evidence, LLM adapter, and report tests.",
        targets=(
            "tests.test_literature",
            "tests.test_retrieval",
            "tests.test_evidence",
            "tests.test_llm",
            "tests.test_report",
        ),
    ),
    "all": CheckGroup(
        description="Full unittest discovery. Run before commits, pushes, or broad refactors.",
        targets=("discover", "-s", "tests"),
    ),
}


def build_unittest_command(
    group_name: str,
    *,
    verbose: bool = False,
    failfast: bool = False,
) -> list[str]:
    """Build the subprocess command for one named check group."""

    group = CHECK_GROUPS[group_name]
    targets = list(group.targets)
    if group_name == "all":
        if verbose:
            targets.append("-v")
        if failfast:
            targets.append("-f")
    else:
        prefix: list[str] = []
        if verbose:
            prefix.append("-v")
        if failfast:
            prefix.append("-f")
        targets = prefix + targets
    return [sys.executable, "-m", "unittest", *targets]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments for the developer check runner."""

    parser = argparse.ArgumentParser(
        prog="simple-ar-checks",
        description="Run layered SimpleAutoResearch developer test groups.",
    )
    parser.add_argument(
        "groups",
        nargs="*",
        choices=tuple(CHECK_GROUPS),
        help="Check group(s) to run. Use --list to see guidance.",
    )
    parser.add_argument("--list", action="store_true", help="List available check groups.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    parser.add_argument("--verbose", action="store_true", help="Pass -v to unittest.")
    parser.add_argument("--failfast", action="store_true", help="Pass -f to unittest.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one or more layered unittest groups."""

    args = parse_args(argv)
    if args.list:
        _print_groups()
        return 0
    groups = list(args.groups or ["quick"])
    for group_name in groups:
        command = build_unittest_command(
            group_name,
            verbose=bool(args.verbose),
            failfast=bool(args.failfast),
        )
        print(f"[{group_name}] {' '.join(command)}")
        if args.dry_run:
            continue
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            return int(completed.returncode)
    return 0


def _print_groups() -> None:
    print("Available check groups:")
    for name, group in CHECK_GROUPS.items():
        print(f"- {name}: {group.description}")


if __name__ == "__main__":
    raise SystemExit(main())
