"""Batch runner for ARC-Bench prepared SimpleAutoResearch code-task packages.

This script intentionally stays outside ``src/simple_ar``. It orchestrates the
existing public commands instead of importing internal code-task modules:

1. ``simple-ar code-task init``
2. ``simple-ar code-task execute``
3. ``benchmark/arc_bench/adapter.py finalize``

Each topic receives its own log files and a JSON state record so interrupted
server runs can be resumed or failed topics can be retried.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


QUICK_TOPICS = ["ML04", "ML02", "ML06", "ML10", "ML08"]
BREADTH_TOPICS = ["ML15", "ML18", "ML01", "ML05", "ML11", "ML12", "ML13"]
SPECIALIZED_TOPICS = [
    "ML03",
    "ML09",
    "ML16",
    "ML20",
    "ML22",
    "ML24",
    "ML25",
    "ML14",
    "ML19",
    "ML21",
    "ML23",
    "ML07",
]
TOPIC_SETS = {
    "quick": QUICK_TOPICS,
    "breadth": BREADTH_TOPICS,
    "specialized": SPECIALIZED_TOPICS,
    "all": QUICK_TOPICS + BREADTH_TOPICS + SPECIALIZED_TOPICS,
}


@dataclass
class CommandResult:
    returncode: int
    log_path: str
    command: list[str]


@dataclass
class TopicState:
    topic: str
    status: str = "pending"
    attempts: int = 0
    run_dir: str | None = None
    output_dir: str | None = None
    last_error: str | None = None
    updated_at: str | None = None
    logs: list[str] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Batch run ARC-Bench ML code-task packages.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run selected topics; completed topics are skipped by default.")
    _add_common_options(run_parser)
    run_parser.add_argument("--force", action="store_true", help="Rerun topics even when the state says completed.")
    run_parser.set_defaults(func=_run_command)

    retry_parser = subparsers.add_parser("retry-unfinished", help="Run each unfinished topic once.")
    _add_common_options(retry_parser)
    retry_parser.add_argument(
        "--resume-existing",
        action="store_true",
        help="Reuse the previous run_dir for unfinished topics when available; otherwise create a fresh run.",
    )
    retry_parser.set_defaults(func=_retry_unfinished_command)

    status_parser = subparsers.add_parser("status", help="Print batch state summary.")
    _add_path_options(status_parser)
    status_parser.set_defaults(func=_status_command)

    args = parser.parse_args(argv)
    return int(args.func(args))


def _add_path_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--prepared-root",
        default="benchmark/arc_bench/prepared/ml",
        help="Root containing prepared ML topic folders.",
    )
    parser.add_argument(
        "--runs-root",
        default="benchmark/arc_bench/runs/ml",
        help="Root where SimpleAutoResearch run directories are created.",
    )
    parser.add_argument(
        "--submissions-root",
        default="benchmark/arc_bench/submissions/ml",
        help="Root where finalized submissions are written.",
    )
    parser.add_argument(
        "--state-file",
        default="benchmark/arc_bench/batch_state/ml_batch_state.json",
        help="JSON state file used for resume/retry bookkeeping.",
    )
    parser.add_argument(
        "--log-root",
        default="benchmark/arc_bench/batch_logs",
        help="Directory for per-topic command logs.",
    )


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    _add_path_options(parser)
    parser.add_argument(
        "--topic-set",
        choices=sorted(TOPIC_SETS),
        default="quick",
        help="Named topic set to run when --topics is not provided.",
    )
    parser.add_argument("--topics", nargs="+", help="Explicit topic list, e.g. --topics ML04 ML02.")
    parser.add_argument("--analyze", action="store_true", help="Run finalize with LLM result analysis.")
    parser.add_argument("--analysis-model", help="Override SIMPLE_AR_MODEL for finalize --analyze.")
    parser.add_argument(
        "--execute-timeout",
        type=int,
        default=0,
        help="Optional timeout in seconds for code-task execute; 0 means no runner-level timeout.",
    )
    parser.add_argument(
        "--finalize-timeout",
        type=int,
        default=0,
        help="Optional timeout in seconds for finalize; 0 means no runner-level timeout.",
    )


def _run_command(args: argparse.Namespace) -> int:
    ctx = _context_from_args(args)
    state = _load_state(ctx.state_file)
    topics = _resolve_topics(args, ctx.prepared_root)
    exit_code = 0

    for topic in topics:
        current = _topic_state(state, topic)
        if current.status == "completed" and not args.force:
            _print(f"[skip] {topic}: already completed at {current.output_dir}")
            continue
        result = _run_topic(ctx, topic, analyze=args.analyze, analysis_model=args.analysis_model)
        state[topic] = result
        _save_state(ctx.state_file, state)
        if result.status != "completed":
            exit_code = 1

    _print_summary(state, topics)
    return exit_code


def _retry_unfinished_command(args: argparse.Namespace) -> int:
    ctx = _context_from_args(args)
    state = _load_state(ctx.state_file)
    candidate_topics = _resolve_topics(args, ctx.prepared_root)
    topics = [topic for topic in candidate_topics if _topic_state(state, topic).status != "completed"]

    if not topics:
        _print("No unfinished topics found.")
        return 0

    exit_code = 0
    for topic in topics:
        current = _topic_state(state, topic)
        resume_run_dir = Path(current.run_dir) if args.resume_existing and current.run_dir else None
        result = _run_topic(
            ctx,
            topic,
            analyze=args.analyze,
            analysis_model=args.analysis_model,
            resume_run_dir=resume_run_dir,
        )
        state[topic] = result
        _save_state(ctx.state_file, state)
        if result.status != "completed":
            exit_code = 1

    _print_summary(state, topics)
    return exit_code


def _status_command(args: argparse.Namespace) -> int:
    ctx = _context_from_args(args)
    state = _load_state(ctx.state_file)
    if not state:
        _print(f"No state file found at {ctx.state_file}.")
        return 0
    _print_summary(state, sorted(state))
    return 0


@dataclass(frozen=True)
class RunnerContext:
    repo_root: Path
    prepared_root: Path
    runs_root: Path
    submissions_root: Path
    state_file: Path
    log_root: Path
    execute_timeout: int = 0
    finalize_timeout: int = 0


def _context_from_args(args: argparse.Namespace) -> RunnerContext:
    repo_root = Path(__file__).resolve().parents[2]
    return RunnerContext(
        repo_root=repo_root,
        prepared_root=_abs(repo_root, args.prepared_root),
        runs_root=_abs(repo_root, args.runs_root),
        submissions_root=_abs(repo_root, args.submissions_root),
        state_file=_abs(repo_root, args.state_file),
        log_root=_abs(repo_root, args.log_root),
        execute_timeout=getattr(args, "execute_timeout", 0),
        finalize_timeout=getattr(args, "finalize_timeout", 0),
    )


def _run_topic(
    ctx: RunnerContext,
    topic: str,
    *,
    analyze: bool,
    analysis_model: str | None,
    resume_run_dir: Path | None = None,
) -> TopicState:
    topic_state = TopicState(topic=topic, attempts=1, status="running", updated_at=_now())
    config_path = ctx.prepared_root / topic / "code_task.toml"
    prepared_dir = ctx.prepared_root / topic
    topic_log_root = ctx.log_root / topic / _timestamp()
    topic_log_root.mkdir(parents=True, exist_ok=True)

    if not config_path.exists():
        return _fail(topic_state, "missing_config", f"Missing config: {config_path}")

    _print(f"\n===== START {topic} =====")

    if resume_run_dir is not None:
        run_dir = resume_run_dir
        _print(f"[resume] {topic}: {run_dir}")
    else:
        before = _existing_run_dirs(ctx.runs_root / topic)
        init_result = _run_logged(
            ["uv", "run", "simple-ar", "code-task", "init", "--config", _rel(ctx.repo_root, config_path)],
            ctx.repo_root,
            topic_log_root / "init.log",
        )
        _record_command(topic_state, init_result)
        if init_result.returncode != 0:
            return _fail(topic_state, "init_failed", f"init exited with {init_result.returncode}")
        run_dir = _detect_new_run_dir(ctx.runs_root / topic, before)
        if run_dir is None:
            return _fail(topic_state, "init_failed", f"Could not detect new run under {ctx.runs_root / topic}")

    topic_state.run_dir = _rel(ctx.repo_root, run_dir)
    execute_result = _run_logged(
        [
            "uv",
            "run",
            "simple-ar",
            "code-task",
            "execute",
            _rel(ctx.repo_root, run_dir),
            "--config",
            _rel(ctx.repo_root, config_path),
            "--yes",
        ],
        ctx.repo_root,
        topic_log_root / "execute.log",
        timeout=ctx.execute_timeout,
    )
    _record_command(topic_state, execute_result)
    if execute_result.returncode != 0:
        return _fail(topic_state, "execute_failed", f"execute exited with {execute_result.returncode}")

    output_dir = ctx.submissions_root / topic / run_dir.name
    topic_state.output_dir = _rel(ctx.repo_root, output_dir)
    finalize_cmd = [
        "uv",
        "run",
        "python",
        "benchmark/arc_bench/adapter.py",
        "finalize",
        "--prepared-dir",
        _rel(ctx.repo_root, prepared_dir),
        "--run-dir",
        _rel(ctx.repo_root, run_dir),
        "--output-dir",
        _rel(ctx.repo_root, output_dir),
        "--force",
    ]
    if analyze:
        finalize_cmd.append("--analyze")
    if analysis_model:
        finalize_cmd.extend(["--analysis-model", analysis_model])

    finalize_result = _run_logged(
        finalize_cmd,
        ctx.repo_root,
        topic_log_root / "finalize.log",
        timeout=ctx.finalize_timeout,
    )
    _record_command(topic_state, finalize_result)
    if finalize_result.returncode != 0:
        return _fail(topic_state, "finalize_failed", f"finalize exited with {finalize_result.returncode}")

    topic_state.status = "completed"
    topic_state.last_error = None
    topic_state.updated_at = _now()
    _print(f"[done] {topic}: {topic_state.output_dir}")
    return topic_state


def _run_logged(command: list[str], cwd: Path, log_path: Path, timeout: int = 0) -> CommandResult:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _print("$ " + " ".join(command))
    with log_path.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(command) + "\n\n")
        log.flush()
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                print(line, end="")
                log.write(line)
            returncode = proc.wait(timeout=timeout if timeout > 0 else None)
        except subprocess.TimeoutExpired:
            proc.kill()
            message = f"\nCommand timed out after {timeout}s.\n"
            print(message, end="")
            log.write(message)
            returncode = 124
        except FileNotFoundError as exc:
            message = f"\nCommand failed to start: {exc}\n"
            print(message, end="")
            log.write(message)
            returncode = 127
        log.write(f"\n[exit] {returncode}\n")
    return CommandResult(returncode=returncode, log_path=_rel(cwd, log_path), command=command)


def _record_command(topic_state: TopicState, result: CommandResult) -> None:
    topic_state.logs.append(result.log_path)
    topic_state.commands.append(result.command)
    topic_state.updated_at = _now()


def _fail(topic_state: TopicState, status: str, message: str) -> TopicState:
    topic_state.status = status
    topic_state.last_error = message
    topic_state.updated_at = _now()
    _print(f"[fail] {topic_state.topic}: {status} - {message}")
    return topic_state


def _resolve_topics(args: argparse.Namespace, prepared_root: Path) -> list[str]:
    raw_topics = args.topics if getattr(args, "topics", None) else TOPIC_SETS[getattr(args, "topic_set", "quick")]
    topics = [topic.upper() for topic in raw_topics]
    seen: set[str] = set()
    unique_topics: list[str] = []
    for topic in topics:
        if topic in seen:
            continue
        seen.add(topic)
        unique_topics.append(topic)
    missing = [topic for topic in unique_topics if not (prepared_root / topic / "code_task.toml").exists()]
    if missing:
        raise SystemExit(f"Missing prepared topic(s): {', '.join(missing)} under {prepared_root}")
    return unique_topics


def _load_state(path: Path) -> dict[str, TopicState]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    topics = data.get("topics", data)
    if not isinstance(topics, dict):
        return {}
    state: dict[str, TopicState] = {}
    for topic, value in topics.items():
        if isinstance(value, dict):
            state[str(topic)] = TopicState(topic=str(topic), **{k: v for k, v in value.items() if k != "topic"})
    return state


def _save_state(path: Path, state: dict[str, TopicState]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": _now(),
        "topics": {topic: asdict(value) for topic, value in sorted(state.items())},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _topic_state(state: dict[str, TopicState], topic: str) -> TopicState:
    return state.get(topic, TopicState(topic=topic))


def _existing_run_dirs(root: Path) -> set[Path]:
    if not root.exists():
        return set()
    return {path.resolve() for path in root.iterdir() if path.is_dir()}


def _detect_new_run_dir(root: Path, before: set[Path]) -> Path | None:
    if not root.exists():
        return None
    candidates = [path for path in root.iterdir() if path.is_dir() and path.resolve() not in before]
    if not candidates:
        candidates = [path for path in root.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _print_summary(state: dict[str, TopicState], topics: Iterable[str]) -> None:
    _print("\n===== BATCH SUMMARY =====")
    for topic in topics:
        row = _topic_state(state, topic)
        detail = row.output_dir or row.run_dir or row.last_error or ""
        _print(f"{topic:>4}  {row.status:<16} {detail}")


def _abs(repo_root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def _rel(repo_root: Path, path: str | Path) -> str:
    path = Path(path)
    try:
        return str(path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _print(message: str) -> None:
    print(message, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
