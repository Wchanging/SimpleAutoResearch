"""Batch runner for ARC-Bench prepared SimpleAutoResearch code-task packages.

This script intentionally stays outside ``src/simple_ar``. It orchestrates the
existing public commands instead of importing internal code-task modules:

1. ``simple-ar code-task init``
2. ``simple-ar code-task execute``
3. ``benchmark/arc_bench/adapter.py finalize``
4. optional ``benchmark/arc_bench/adapter.py score``

Each topic receives its own log files and a JSON state record so interrupted
server runs can be resumed or failed topics can be retried.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import subprocess
import sys
import time
import tomllib
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
    "next": BREADTH_TOPICS,
    "specialized": SPECIALIZED_TOPICS,
    "high-risk": SPECIALIZED_TOPICS,
    "higher-risk": SPECIALIZED_TOPICS,
    "all": QUICK_TOPICS + BREADTH_TOPICS + SPECIALIZED_TOPICS,
}
SUCCESSFUL_RUN_STATUSES = {"benchmark_passed"}
DEFAULT_STATE_ROOT = Path("benchmark/arc_bench/batch_state")
LATEST_STATE_POINTER = DEFAULT_STATE_ROOT / "latest_state.json"
LEGACY_STATE_FILE = DEFAULT_STATE_ROOT / "ml_batch_state.json"


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
    retry_parser.add_argument(
        "--extend-repair-rounds",
        type=int,
        default=0,
        help=(
            "When used with --resume-existing, temporarily raise code-task --repair-rounds "
            "to used repair count plus this many extra rounds."
        ),
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
        default=None,
        help=(
            "JSON state file used for resume/retry bookkeeping. "
            "For 'run', omitting this creates a new timestamped state and marks it latest. "
            "For 'retry-unfinished' and 'status', omitting this reads the latest state."
        ),
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
        help=(
            "Named topic set to run when --topics is not provided. "
            "Use quick, breadth/next, specialized/high-risk, or all."
        ),
    )
    parser.add_argument("--topics", nargs="+", help="Explicit topic list, e.g. --topics ML04 ML02.")
    parser.add_argument("--analyze", action="store_true", help="Run finalize with LLM result analysis.")
    parser.add_argument("--analysis-model", help="Override SIMPLE_AR_MODEL for finalize --analyze.")
    parser.add_argument("--score", action="store_true", help="Run the built-in LLM leaf-level scorer after finalize.")
    parser.add_argument("--score-model", help="Override SIMPLE_AR_MODEL for adapter score.")
    parser.add_argument(
        "--score-profile",
        choices=("proxy", "arc-auto", "strict"),
        default="proxy",
        help="Scoring profile passed to adapter.py score.",
    )
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
    parser.add_argument(
        "--score-timeout",
        type=int,
        default=0,
        help="Optional timeout in seconds for scoring; 0 means no runner-level timeout.",
    )
    parser.add_argument(
        "--llm-retry-attempts",
        type=int,
        default=0,
        help="Override code-task --llm-retry-attempts for every execute call; 0 keeps each TOML default.",
    )


def _run_command(args: argparse.Namespace) -> int:
    ctx = _context_from_args(args)
    topics = _resolve_topics(args, ctx.prepared_root)
    _remember_latest_state(ctx)
    state = _load_state(ctx.state_file)
    if not state:
        _save_state(ctx.state_file, state)
    _print(f"[state] {ctx.state_file}")
    exit_code = 0

    for topic in topics:
        current = _topic_state(state, topic)
        if current.status == "completed" and not args.force:
            if _completed_state_still_valid(
                ctx,
                current,
                require_analysis=args.analyze,
                require_score=args.score,
                score_profile=args.score_profile,
            ):
                _print(f"[skip] {topic}: already completed at {current.output_dir}")
                continue
            if args.score and _completed_state_still_valid(
                ctx,
                current,
                require_analysis=args.analyze,
                require_score=False,
                score_profile=args.score_profile,
            ):
                _print(f"[score] {topic}: finalized output exists; scoring without rerunning experiment.")
                result = _score_existing_topic(
                    ctx,
                    topic,
                    current,
                    score_model=args.score_model,
                    score_profile=args.score_profile,
                )
                state[topic] = result
                _save_state(ctx.state_file, state)
                if result.status != "completed":
                    exit_code = 1
                continue
            _print(f"[stale] {topic}: previous completed state is missing passed run or finalized artifacts; rerunning.")
        result = _run_topic(
            ctx,
            topic,
            analyze=args.analyze,
            analysis_model=args.analysis_model,
            score=args.score,
            score_model=args.score_model,
            score_profile=args.score_profile,
            previous_state=current,
        )
        state[topic] = result
        _save_state(ctx.state_file, state)
        if result.status != "completed":
            exit_code = 1

    _print_summary(state, topics)
    return exit_code


def _retry_unfinished_command(args: argparse.Namespace) -> int:
    ctx = _context_from_args(args)
    state = _load_state(ctx.state_file)
    _print(f"[state] {ctx.state_file}")
    candidate_topics = _resolve_topics(args, ctx.prepared_root)
    topics = [
        topic
        for topic in candidate_topics
        if _unfinished_or_stale_completed(
            ctx,
            _topic_state(state, topic),
            require_analysis=args.analyze,
            require_score=args.score,
            score_profile=args.score_profile,
        )
    ]

    if not topics:
        _print("No unfinished topics found.")
        return 0

    exit_code = 0
    for topic in topics:
        current = _topic_state(state, topic)
        if args.score and current.status == "completed" and _completed_state_still_valid(
            ctx,
            current,
            require_analysis=args.analyze,
            require_score=False,
            score_profile=args.score_profile,
        ):
            _print(f"[score] {topic}: finalized output exists; scoring without rerunning experiment.")
            result = _score_existing_topic(
                ctx,
                topic,
                current,
                score_model=args.score_model,
                score_profile=args.score_profile,
            )
            state[topic] = result
            _save_state(ctx.state_file, state)
            if result.status != "completed":
                exit_code = 1
            continue
        resume_run_dir = _abs(ctx.repo_root, current.run_dir) if args.resume_existing and current.run_dir else None
        config_path = ctx.prepared_root / topic / "code_task.toml"
        repair_rounds_override = None
        if resume_run_dir is not None:
            used_repairs = _repair_usage(resume_run_dir)
            configured_repairs = _configured_repair_rounds(config_path)
            if args.extend_repair_rounds > 0:
                repair_rounds_override = max(configured_repairs, used_repairs + args.extend_repair_rounds)
                _print(
                    f"[resume] {topic}: extending repair budget for this execute call "
                    f"to {repair_rounds_override} round(s) "
                    f"(configured={configured_repairs}, used={used_repairs}, extra={args.extend_repair_rounds})."
                )
            elif _repair_budget_exhausted(resume_run_dir, config_path):
                _print(
                    f"[fresh] {topic}: previous run appears to have exhausted its configured repair budget; "
                    "creating a fresh run instead of resuming it. Use --resume-existing "
                    "--extend-repair-rounds N to continue repairing the same run."
                )
                resume_run_dir = None
        result = _run_topic(
            ctx,
            topic,
            analyze=args.analyze,
            analysis_model=args.analysis_model,
            score=args.score,
            score_model=args.score_model,
            score_profile=args.score_profile,
            resume_run_dir=resume_run_dir,
            previous_state=current,
            repair_rounds_override=repair_rounds_override,
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
        if ctx.state_file.exists():
            _print(f"State file has no topic entries: {ctx.state_file}.")
        else:
            _print(f"No state file found at {ctx.state_file}.")
        return 0
    _print(f"[state] {ctx.state_file}")
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
    score_timeout: int = 0
    llm_retry_attempts: int = 0


def _context_from_args(args: argparse.Namespace) -> RunnerContext:
    repo_root = Path(__file__).resolve().parents[2]
    state_file = _resolve_state_file(repo_root, args)
    return RunnerContext(
        repo_root=repo_root,
        prepared_root=_abs(repo_root, args.prepared_root),
        runs_root=_abs(repo_root, args.runs_root),
        submissions_root=_abs(repo_root, args.submissions_root),
        state_file=state_file,
        log_root=_abs(repo_root, args.log_root),
        execute_timeout=getattr(args, "execute_timeout", 0),
        finalize_timeout=getattr(args, "finalize_timeout", 0),
        score_timeout=getattr(args, "score_timeout", 0),
        llm_retry_attempts=getattr(args, "llm_retry_attempts", 0),
    )


def _run_topic(
    ctx: RunnerContext,
    topic: str,
    *,
    analyze: bool,
    analysis_model: str | None,
    score: bool,
    score_model: str | None,
    score_profile: str,
    resume_run_dir: Path | None = None,
    previous_state: TopicState | None = None,
    repair_rounds_override: int | None = None,
) -> TopicState:
    topic_state = TopicState(
        topic=topic,
        attempts=(previous_state.attempts + 1 if previous_state is not None else 1),
        status="running",
        updated_at=_now(),
    )
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
    execute_cmd = [
        "uv",
        "run",
        "simple-ar",
        "code-task",
        "execute",
        _rel(ctx.repo_root, run_dir),
        "--config",
        _rel(ctx.repo_root, config_path),
    ]
    if repair_rounds_override is not None and repair_rounds_override > 0:
        execute_cmd.extend(["--repair-rounds", str(repair_rounds_override)])
    if ctx.llm_retry_attempts > 0:
        execute_cmd.extend(["--llm-retry-attempts", str(ctx.llm_retry_attempts)])
    execute_cmd.append("--yes")
    execute_result = _run_logged(
        execute_cmd,
        ctx.repo_root,
        topic_log_root / "execute.log",
        timeout=ctx.execute_timeout,
    )
    _record_command(topic_state, execute_result)
    if execute_result.returncode != 0:
        return _fail(topic_state, "execute_failed", f"execute exited with {execute_result.returncode}")
    run_ok, run_detail = _run_business_success(run_dir)
    if not run_ok:
        return _fail(topic_state, "execute_incomplete", run_detail)

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

    if score:
        score_cmd = [
            "uv",
            "run",
            "python",
            "benchmark/arc_bench/adapter.py",
            "score",
            "--prepared-dir",
            _rel(ctx.repo_root, prepared_dir),
            "--submission-dir",
            _rel(ctx.repo_root, output_dir / "submission"),
            "--output-dir",
            _rel(ctx.repo_root, output_dir / "judge"),
            "--score-profile",
            score_profile,
        ]
        if score_model:
            score_cmd.extend(["--model", score_model])
        score_result = _run_logged(
            score_cmd,
            ctx.repo_root,
            topic_log_root / "score.log",
            timeout=ctx.score_timeout,
        )
        _record_command(topic_state, score_result)
        if score_result.returncode != 0:
            return _fail(topic_state, "score_failed", f"score exited with {score_result.returncode}")

    topic_state.status = "completed"
    topic_state.last_error = None
    topic_state.updated_at = _now()
    _print(f"[done] {topic}: {topic_state.output_dir}")
    return topic_state


def _score_existing_topic(
    ctx: RunnerContext,
    topic: str,
    current: TopicState,
    *,
    score_model: str | None,
    score_profile: str,
) -> TopicState:
    topic_state = TopicState(
        topic=topic,
        status="running",
        attempts=current.attempts,
        run_dir=current.run_dir,
        output_dir=current.output_dir,
        logs=list(current.logs),
        commands=list(current.commands),
        updated_at=_now(),
    )
    if not current.output_dir:
        return _fail(topic_state, "score_failed", "completed state has no output_dir")
    prepared_dir = ctx.prepared_root / topic
    output_dir = _abs(ctx.repo_root, current.output_dir)
    topic_log_root = ctx.log_root / topic / _timestamp()
    score_cmd = [
        "uv",
        "run",
        "python",
        "benchmark/arc_bench/adapter.py",
        "score",
        "--prepared-dir",
        _rel(ctx.repo_root, prepared_dir),
        "--submission-dir",
        _rel(ctx.repo_root, output_dir / "submission"),
        "--output-dir",
        _rel(ctx.repo_root, output_dir / "judge"),
        "--score-profile",
        score_profile,
    ]
    if score_model:
        score_cmd.extend(["--model", score_model])
    score_result = _run_logged(
        score_cmd,
        ctx.repo_root,
        topic_log_root / "score.log",
        timeout=ctx.score_timeout,
    )
    _record_command(topic_state, score_result)
    if score_result.returncode != 0:
        return _fail(topic_state, "score_failed", f"score exited with {score_result.returncode}")
    topic_state.status = "completed"
    topic_state.last_error = None
    topic_state.updated_at = _now()
    _print(f"[done] {topic}: scored existing output at {topic_state.output_dir}")
    return topic_state


def _run_logged(command: list[str], cwd: Path, log_path: Path, timeout: int = 0) -> CommandResult:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _print("$ " + " ".join(command))
    with log_path.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(command) + "\n\n")
        log.flush()
        env = _subprocess_env()
        try:
            if _should_use_pty():
                returncode = _run_logged_pty(command, cwd, log, env, timeout=timeout)
            else:
                returncode = _run_logged_pipe(command, cwd, log, env, timeout=timeout)
        except FileNotFoundError as exc:
            message = f"\nCommand failed to start: {exc}\n"
            print(message, end="")
            log.write(message)
            returncode = 127
        log.write(f"\n[exit] {returncode}\n")
    return CommandResult(returncode=returncode, log_path=_rel(cwd, log_path), command=command)


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("FORCE_COLOR", "1")
    env.setdefault("PY_COLORS", "1")
    env.setdefault("CLICOLOR_FORCE", "1")
    env.setdefault("TERM", "xterm-256color")
    return env


def _should_use_pty() -> bool:
    return os.name != "nt" and sys.stdout.isatty()


def _run_logged_pipe(
    command: list[str],
    cwd: Path,
    log: Any,
    env: dict[str, str],
    *,
    timeout: int = 0,
) -> int:
    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="")
        log.write(line)
    try:
        return proc.wait(timeout=timeout if timeout > 0 else None)
    except subprocess.TimeoutExpired:
        proc.kill()
        message = f"\nCommand timed out after {timeout}s.\n"
        print(message, end="")
        log.write(message)
        return 124


def _run_logged_pty(
    command: list[str],
    cwd: Path,
    log: Any,
    env: dict[str, str],
    *,
    timeout: int = 0,
) -> int:
    # On POSIX terminals this keeps Rich/Click/etc. color output enabled while
    # still teeing the child process stream into a log file.
    import pty
    import select

    master_fd: int | None = None
    slave_fd: int | None = None
    proc: subprocess.Popen[bytes] | None = None
    deadline = time.monotonic() + timeout if timeout > 0 else None
    timed_out = False
    try:
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            close_fds=True,
        )
        os.close(slave_fd)
        slave_fd = None

        while True:
            if deadline is not None and time.monotonic() >= deadline:
                proc.kill()
                timed_out = True
                message = f"\nCommand timed out after {timeout}s.\n"
                print(message, end="")
                log.write(message)
                break

            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if master_fd in ready:
                chunk = _read_pty_chunk(master_fd)
                if chunk:
                    text = chunk.decode("utf-8", errors="replace")
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    log.write(text)
                    log.flush()
                elif proc.poll() is not None:
                    break

            if proc.poll() is not None:
                while True:
                    chunk = _read_pty_chunk(master_fd)
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", errors="replace")
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    log.write(text)
                    log.flush()
                break

        if timed_out:
            proc.wait()
            return 124
        return proc.wait()
    finally:
        if slave_fd is not None:
            os.close(slave_fd)
        if master_fd is not None:
            os.close(master_fd)


def _read_pty_chunk(fd: int) -> bytes:
    try:
        return os.read(fd, 4096)
    except OSError as exc:
        if exc.errno == errno.EIO:
            return b""
        raise


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


def _repair_budget_exhausted(run_dir: Path, config_path: Path) -> bool:
    repair_rounds = _configured_repair_rounds(config_path)
    if repair_rounds <= 0:
        return False
    used = _repair_usage(run_dir)
    return used > 0 and used >= repair_rounds


def _repair_usage(run_dir: Path) -> int:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        return 0
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    repair = manifest.get("repair", {})
    if not isinstance(repair, dict):
        return 0
    used_counts: list[int] = []
    for key in ("repair_count", "review_repair_count", "run_repair_count"):
        try:
            used = int(repair.get(key, 0) or 0)
        except (TypeError, ValueError):
            used = 0
        used_counts.append(max(0, used))
    return max(used_counts, default=0)


def _completed_state_still_valid(
    ctx: RunnerContext,
    state: TopicState,
    *,
    require_analysis: bool,
    require_score: bool,
    score_profile: str = "proxy",
) -> bool:
    if not state.run_dir or not state.output_dir:
        return False
    run_dir = _abs(ctx.repo_root, state.run_dir)
    output_dir = _abs(ctx.repo_root, state.output_dir)
    run_ok, _ = _run_business_success(run_dir)
    if not run_ok:
        return False
    return _finalized_output_complete(
        output_dir,
        require_analysis=require_analysis,
        require_score=require_score,
        score_profile=score_profile,
    )


def _unfinished_or_stale_completed(
    ctx: RunnerContext,
    state: TopicState,
    *,
    require_analysis: bool,
    require_score: bool,
    score_profile: str = "proxy",
) -> bool:
    if state.status != "completed":
        return True
    return not _completed_state_still_valid(
        ctx,
        state,
        require_analysis=require_analysis,
        require_score=require_score,
        score_profile=score_profile,
    )


def _finalized_output_complete(
    output_dir: Path,
    *,
    require_analysis: bool,
    require_score: bool,
    score_profile: str = "proxy",
) -> bool:
    required_files = [
        output_dir / "arc_adapter_meta.json",
        output_dir / "submission" / "README.md",
        output_dir / "submission" / "claims.json",
        output_dir / "submission" / "results" / "metrics.json",
        output_dir / "result_analysis" / "metric_summary.json",
        output_dir / "result_analysis" / "analysis_audit.json",
        output_dir / "result_analysis" / "analysis_report.md",
    ]
    if any(not path.is_file() for path in required_files):
        return False
    if not (output_dir / "submission" / "code").exists():
        return False
    if require_analysis:
        if not (output_dir / "result_analysis" / "analysis_response.json").is_file():
            return False
        meta_path = output_dir / "arc_adapter_meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        analysis = meta.get("analysis", {})
        if not isinstance(analysis, dict) or analysis.get("llm_used") is not True:
            return False
    if require_score:
        for path in [
            output_dir / "judge" / "judge_result.json",
            output_dir / "judge" / "scorecard.md",
        ]:
            if not path.is_file():
                return False
        try:
            judge = json.loads((output_dir / "judge" / "judge_result.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(judge.get("overall_score"), (int, float)):
            return False
        if str(judge.get("scoring_profile") or "proxy") != score_profile:
            return False
    return True


def _run_business_success(run_dir: Path) -> tuple[bool, str]:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        return False, f"missing run manifest: {manifest_path}"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"could not read run manifest: {exc}"
    status = str(manifest.get("status", "unknown"))
    if status in SUCCESSFUL_RUN_STATUSES:
        return True, f"manifest status {status}"
    benchmark = manifest.get("benchmark", {})
    if isinstance(benchmark, dict):
        patched = benchmark.get("patched_execution")
        if isinstance(patched, dict):
            patched_status = patched.get("status", "unknown")
            return False, f"manifest status {status}; patched benchmark status {patched_status}"
        last_status = benchmark.get("last_status")
        if last_status:
            return False, f"manifest status {status}; benchmark last_status {last_status}"
    return False, f"manifest status {status}; expected one of {sorted(SUCCESSFUL_RUN_STATUSES)}"


def _configured_repair_rounds(config_path: Path) -> int:
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return 0
    execute = config.get("execute", {})
    if not isinstance(execute, dict):
        return 0
    try:
        return max(0, int(execute.get("repair_rounds", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _resolve_state_file(repo_root: Path, args: argparse.Namespace) -> Path:
    raw_state_file = getattr(args, "state_file", None)
    if raw_state_file:
        if str(raw_state_file).strip().lower() == "latest":
            return _latest_state_file(repo_root)
        return _abs(repo_root, raw_state_file)

    command = getattr(args, "command", "")
    if command == "run":
        return _new_batch_state_file(repo_root, args)
    return _latest_state_file(repo_root)


def _new_batch_state_file(repo_root: Path, args: argparse.Namespace) -> Path:
    state_root = _abs(repo_root, DEFAULT_STATE_ROOT)
    topic_label = _state_topic_label(args)
    timestamp = _timestamp()
    candidate = state_root / f"{timestamp}-{topic_label}.json"
    suffix = 2
    while candidate.exists():
        candidate = state_root / f"{timestamp}-{topic_label}-{suffix}.json"
        suffix += 1
    return candidate


def _state_topic_label(args: argparse.Namespace) -> str:
    topics = getattr(args, "topics", None)
    if topics:
        label = "topics-" + "-".join(str(topic) for topic in topics[:4])
        if len(topics) > 4:
            label += f"-plus{len(topics) - 4}"
    else:
        label = str(getattr(args, "topic_set", "batch") or "batch")
    return _safe_filename(label)


def _safe_filename(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned[:80] or "batch"


def _latest_state_file(repo_root: Path) -> Path:
    pointer_path = _abs(repo_root, LATEST_STATE_POINTER)
    try:
        data = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        legacy_path = _abs(repo_root, LEGACY_STATE_FILE)
        return legacy_path

    state_file = data.get("state_file") if isinstance(data, dict) else None
    if not isinstance(state_file, str) or not state_file.strip():
        return _abs(repo_root, LEGACY_STATE_FILE)
    return _abs(repo_root, state_file)


def _remember_latest_state(ctx: RunnerContext) -> None:
    pointer_path = _abs(ctx.repo_root, LATEST_STATE_POINTER)
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": _now(),
        "state_file": _rel(ctx.repo_root, ctx.state_file),
    }
    pointer_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
