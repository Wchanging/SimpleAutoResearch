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
import queue
import subprocess
import sys
import threading
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
    "ML17",
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
    started_at: str | None = None
    ended_at: str | None = None
    duration_sec: float = 0.0


@dataclass
class TopicState:
    topic: str
    status: str = "pending"
    attempts: int = 0
    run_dir: str | None = None
    output_dir: str | None = None
    last_error: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_sec: float | None = None
    updated_at: str | None = None
    logs: list[str] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)
    command_results: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


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

    summarize_parser = subparsers.add_parser(
        "summarize",
        help="Aggregate batch scores, durations, and LLM usage without rerunning any topic.",
    )
    _add_summary_options(summarize_parser)
    summarize_parser.set_defaults(func=_summarize_command)

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


def _add_summary_options(parser: argparse.ArgumentParser) -> None:
    _add_path_options(parser)
    parser.add_argument(
        "--topic-set",
        choices=sorted(TOPIC_SETS),
        help="Optional named topic set filter; omit to summarize all topics in the state file.",
    )
    parser.add_argument("--topics", nargs="+", help="Optional explicit topic filter, e.g. --topics ML04 ML02.")
    parser.add_argument(
        "--output-prefix",
        help=(
            "Optional output path prefix. Defaults to '<state-file>.summary' and writes "
            "both .json and .md."
        ),
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
        if current.status == "completed":
            _print(f"[refresh] {topic}: completed run exists; refreshing finalize/score without rerunning execute.")
            result = _refresh_completed_topic(
                ctx,
                topic,
                current,
                analyze=args.analyze,
                analysis_model=args.analysis_model,
                score=args.score,
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


def _summarize_command(args: argparse.Namespace) -> int:
    ctx = _context_from_args(args)
    state = _load_state(ctx.state_file)
    if not state:
        if ctx.state_file.exists():
            _print(f"State file has no topic entries: {ctx.state_file}.")
        else:
            _print(f"No state file found at {ctx.state_file}.")
        return 0
    topics = _resolve_summary_topics(args, state)
    if not topics:
        _print("No topics selected for summary.")
        return 0
    summary = _build_batch_summary(ctx, state, topics)
    json_path, md_path = _write_batch_summary(ctx, summary, output_prefix=getattr(args, "output_prefix", None))
    _print(render_batch_summary_markdown(summary))
    _print(f"\n[summary] wrote {_rel(ctx.repo_root, json_path)}")
    _print(f"[summary] wrote {_rel(ctx.repo_root, md_path)}")
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
    started_monotonic = time.monotonic()
    started_at = _now()
    topic_state = TopicState(
        topic=topic,
        attempts=(previous_state.attempts + 1 if previous_state is not None else 1),
        status="running",
        started_at=started_at,
        updated_at=_now(),
    )
    config_path = ctx.prepared_root / topic / "code_task.toml"
    prepared_dir = ctx.prepared_root / topic
    topic_log_root = ctx.log_root / topic / _timestamp()
    topic_log_root.mkdir(parents=True, exist_ok=True)

    if not config_path.exists():
        return _finalize_topic_state(
            ctx,
            _fail(topic_state, "missing_config", f"Missing config: {config_path}"),
            started_monotonic=started_monotonic,
        )

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
            return _finalize_topic_state(
                ctx,
                _fail(topic_state, "init_failed", f"init exited with {init_result.returncode}"),
                started_monotonic=started_monotonic,
            )
        run_dir = _detect_new_run_dir(ctx.runs_root / topic, before)
        if run_dir is None:
            return _finalize_topic_state(
                ctx,
                _fail(topic_state, "init_failed", f"Could not detect new run under {ctx.runs_root / topic}"),
                started_monotonic=started_monotonic,
            )

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
        return _finalize_topic_state(
            ctx,
            _fail(topic_state, "execute_failed", f"execute exited with {execute_result.returncode}"),
            started_monotonic=started_monotonic,
        )
    run_ok, run_detail = _run_business_success(run_dir)
    if not run_ok:
        return _finalize_topic_state(
            ctx,
            _fail(topic_state, "execute_incomplete", run_detail),
            started_monotonic=started_monotonic,
        )

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
        return _finalize_topic_state(
            ctx,
            _fail(topic_state, "finalize_failed", f"finalize exited with {finalize_result.returncode}"),
            started_monotonic=started_monotonic,
        )

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
            return _finalize_topic_state(
                ctx,
                _fail(topic_state, "score_failed", f"score exited with {score_result.returncode}"),
                started_monotonic=started_monotonic,
            )

    topic_state.status = "completed"
    topic_state.last_error = None
    topic_state.updated_at = _now()
    topic_state = _finalize_topic_state(ctx, topic_state, started_monotonic=started_monotonic)
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
    started_monotonic = time.monotonic()
    started_at = _now()
    topic_state = TopicState(
        topic=topic,
        status="running",
        attempts=current.attempts,
        run_dir=current.run_dir,
        output_dir=current.output_dir,
        started_at=started_at,
        logs=list(current.logs),
        commands=list(current.commands),
        command_results=list(current.command_results),
        updated_at=_now(),
    )
    if not current.output_dir:
        return _finalize_topic_state(
            ctx,
            _fail(topic_state, "score_failed", "completed state has no output_dir"),
            started_monotonic=started_monotonic,
        )
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
        return _finalize_topic_state(
            ctx,
            _fail(topic_state, "score_failed", f"score exited with {score_result.returncode}"),
            started_monotonic=started_monotonic,
        )
    topic_state.status = "completed"
    topic_state.last_error = None
    topic_state.updated_at = _now()
    topic_state = _finalize_topic_state(ctx, topic_state, started_monotonic=started_monotonic)
    _print(f"[done] {topic}: scored existing output at {topic_state.output_dir}")
    return topic_state


def _refresh_completed_topic(
    ctx: RunnerContext,
    topic: str,
    current: TopicState,
    *,
    analyze: bool,
    analysis_model: str | None,
    score: bool,
    score_model: str | None,
    score_profile: str,
) -> TopicState:
    started_monotonic = time.monotonic()
    started_at = _now()
    topic_state = TopicState(
        topic=topic,
        status="running",
        attempts=current.attempts,
        run_dir=current.run_dir,
        output_dir=current.output_dir,
        started_at=started_at,
        logs=list(current.logs),
        commands=list(current.commands),
        command_results=list(current.command_results),
        updated_at=_now(),
    )
    if not current.run_dir:
        return _finalize_topic_state(
            ctx,
            _fail(topic_state, "refresh_failed", "completed state has no run_dir"),
            started_monotonic=started_monotonic,
        )

    prepared_dir = ctx.prepared_root / topic
    run_dir = _abs(ctx.repo_root, current.run_dir)
    run_ok, run_detail = _run_business_success(run_dir)
    if not run_ok:
        return _finalize_topic_state(
            ctx,
            _fail(topic_state, "execute_incomplete", run_detail),
            started_monotonic=started_monotonic,
        )

    output_dir = _abs(ctx.repo_root, current.output_dir) if current.output_dir else ctx.submissions_root / topic / run_dir.name
    topic_state.output_dir = _rel(ctx.repo_root, output_dir)
    topic_log_root = ctx.log_root / topic / _timestamp()

    finalize_complete = _finalized_output_complete(
        output_dir,
        require_analysis=analyze,
        require_score=False,
        score_profile=score_profile,
    )
    if finalize_complete:
        _print(f"[refresh] {topic}: finalized output already satisfies analysis={analyze}; skipping finalize.")
    else:
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
            return _finalize_topic_state(
                ctx,
                _fail(topic_state, "finalize_failed", f"finalize exited with {finalize_result.returncode}"),
                started_monotonic=started_monotonic,
            )

    if score:
        score_complete = _finalized_output_complete(
            output_dir,
            require_analysis=analyze,
            require_score=True,
            score_profile=score_profile,
        )
        if score_complete:
            _print(f"[refresh] {topic}: {score_profile} judge already exists; skipping score.")
        else:
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
                return _finalize_topic_state(
                    ctx,
                    _fail(topic_state, "score_failed", f"score exited with {score_result.returncode}"),
                    started_monotonic=started_monotonic,
                )

    if not _completed_state_still_valid(
        ctx,
        topic_state,
        require_analysis=analyze,
        require_score=score,
        score_profile=score_profile,
    ):
        return _finalize_topic_state(
            ctx,
            _fail(topic_state, "refresh_incomplete", "refreshed completed run still lacks required artifacts"),
            started_monotonic=started_monotonic,
        )

    topic_state.status = "completed"
    topic_state.last_error = None
    topic_state.updated_at = _now()
    topic_state = _finalize_topic_state(ctx, topic_state, started_monotonic=started_monotonic)
    _print(f"[done] {topic}: refreshed existing output at {topic_state.output_dir}")
    return topic_state


def _run_logged(command: list[str], cwd: Path, log_path: Path, timeout: int = 0) -> CommandResult:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _print("$ " + " ".join(command))
    started_at = _now()
    started = time.monotonic()
    returncode = 127
    with log_path.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(command) + "\n\n")
        log.write(f"[started] {started_at}\n\n")
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
        ended_at = _now()
        duration_sec = round(time.monotonic() - started, 3)
        log.write(f"\n[ended] {ended_at}\n")
        log.write(f"[duration_sec] {duration_sec}\n")
        log.write(f"[exit] {returncode}\n")
    return CommandResult(
        returncode=returncode,
        log_path=_rel(cwd, log_path),
        command=command,
        started_at=started_at,
        ended_at=ended_at,
        duration_sec=duration_sec,
    )


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

    output_queue: queue.Queue[str | None] = queue.Queue()

    def reader() -> None:
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                output_queue.put(chunk)
        finally:
            output_queue.put(None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout if timeout > 0 else None
    reader_done = False
    timed_out = False

    while True:
        if deadline is not None and time.monotonic() >= deadline and proc.poll() is None:
            timed_out = True
            break
        try:
            item = output_queue.get(timeout=0.1)
        except queue.Empty:
            item = None
            queue_empty = True
        else:
            queue_empty = False
        if item is None:
            if not queue_empty:
                reader_done = True
            if proc.poll() is not None and reader_done and output_queue.empty():
                break
            continue
        print(item, end="")
        log.write(item)
        log.flush()

    if timed_out:
        proc.kill()
        message = f"\nCommand timed out after {timeout}s.\n"
        print(message, end="")
        log.write(message)
        log.flush()
        thread.join(timeout=2)
        return 124
    thread.join(timeout=2)
    return proc.wait()


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
    topic_state.command_results.append(asdict(result))
    topic_state.updated_at = _now()


def _fail(topic_state: TopicState, status: str, message: str) -> TopicState:
    topic_state.status = status
    topic_state.last_error = message
    topic_state.updated_at = _now()
    _print(f"[fail] {topic_state.topic}: {status} - {message}")
    return topic_state


def _finalize_topic_state(ctx: RunnerContext, topic_state: TopicState, *, started_monotonic: float) -> TopicState:
    topic_state.ended_at = _now()
    topic_state.duration_sec = round(time.monotonic() - started_monotonic, 3)
    stats = _build_topic_stats(ctx, topic_state)
    topic_state.stats = _compact_topic_stats_for_state(stats)
    written = _write_topic_stats(ctx, topic_state, stats)
    if written:
        topic_state.stats["artifact_paths"] = written
        _print(f"[stats] {topic_state.topic}: wrote {', '.join(written)}")
    return topic_state


def _build_topic_stats(ctx: RunnerContext, topic_state: TopicState) -> dict[str, Any]:
    command_results = [row for row in topic_state.command_results if isinstance(row, dict)]
    command_duration_sec = round(
        sum(float(row.get("duration_sec") or 0.0) for row in command_results),
        3,
    )
    run_dir = _abs(ctx.repo_root, topic_state.run_dir) if topic_state.run_dir else None
    output_dir = _abs(ctx.repo_root, topic_state.output_dir) if topic_state.output_dir else None
    usage = _collect_topic_llm_usage(ctx, run_dir=run_dir, output_dir=output_dir)
    return {
        "schema_version": "simple_ar_arc_task_stats.v1",
        "topic": topic_state.topic,
        "status": topic_state.status,
        "attempts": topic_state.attempts,
        "started_at": topic_state.started_at,
        "ended_at": topic_state.ended_at,
        "duration_sec": topic_state.duration_sec,
        "command_duration_sec": command_duration_sec,
        "run_dir": topic_state.run_dir,
        "output_dir": topic_state.output_dir,
        "last_error": topic_state.last_error,
        "commands": command_results,
        "llm_usage": usage,
    }


def _compact_topic_stats_for_state(stats: dict[str, Any]) -> dict[str, Any]:
    usage = stats.get("llm_usage") if isinstance(stats.get("llm_usage"), dict) else {}
    totals = usage.get("totals") if isinstance(usage, dict) else {}
    return {
        "duration_sec": stats.get("duration_sec"),
        "command_duration_sec": stats.get("command_duration_sec"),
        "llm_request_count": totals.get("request_count") if isinstance(totals, dict) else 0,
        "llm_input_tokens": totals.get("input_tokens") if isinstance(totals, dict) else 0,
        "llm_output_tokens": totals.get("output_tokens") if isinstance(totals, dict) else 0,
        "llm_total_tokens": totals.get("total_tokens") if isinstance(totals, dict) else 0,
        "estimated_cost_usd": totals.get("estimated_cost_usd") if isinstance(totals, dict) else 0.0,
    }


def _write_topic_stats(ctx: RunnerContext, topic_state: TopicState, stats: dict[str, Any]) -> list[str]:
    written: list[str] = []
    if topic_state.run_dir:
        run_dir = _abs(ctx.repo_root, topic_state.run_dir)
        if run_dir.exists():
            path = run_dir / "arc_task_stats.json"
            path.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            written.append(_rel(ctx.repo_root, path))
    if topic_state.output_dir:
        output_dir = _abs(ctx.repo_root, topic_state.output_dir)
        if output_dir.exists():
            path = output_dir / "arc_task_stats.json"
            path.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            written.append(_rel(ctx.repo_root, path))
    return written


def _collect_topic_llm_usage(
    ctx: RunnerContext,
    *,
    run_dir: Path | None,
    output_dir: Path | None,
) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    if run_dir is not None:
        sources.append(
            _usage_source(
                ctx,
                stage="code_task",
                summary_path=run_dir / "code_task" / "meta" / "llm_usage_summary.json",
                jsonl_path=run_dir / "code_task" / "meta" / "llm_usage.jsonl",
            )
        )
    if output_dir is not None:
        sources.append(
            _usage_source(
                ctx,
                stage="result_analysis",
                summary_path=output_dir / "result_analysis" / "llm_usage_summary.json",
                jsonl_path=output_dir / "result_analysis" / "llm_usage.jsonl",
            )
        )
        sources.append(
            _usage_source(
                ctx,
                stage="score",
                summary_path=output_dir / "judge" / "llm_usage_summary.json",
                jsonl_path=output_dir / "judge" / "llm_usage.jsonl",
            )
        )
    sources = [row for row in sources if row.get("found")]
    totals = _merge_usage_totals([row.get("summary", {}) for row in sources if isinstance(row.get("summary"), dict)])
    return {
        "totals": totals,
        "sources": sources,
    }


def _usage_source(ctx: RunnerContext, *, stage: str, summary_path: Path, jsonl_path: Path) -> dict[str, Any]:
    summary = _read_usage_summary(summary_path)
    source_path = summary_path
    source_kind = "summary"
    if not summary:
        rows = _read_usage_jsonl(jsonl_path)
        if rows:
            summary = _summarize_usage_rows(rows)
            source_path = jsonl_path
            source_kind = "jsonl"
    return {
        "stage": stage,
        "found": bool(summary),
        "source_kind": source_kind if summary else "",
        "path": _rel(ctx.repo_root, source_path) if summary else "",
        "summary": summary,
    }


def _read_usage_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return _normalize_usage_summary(data if isinstance(data, dict) else {})


def _read_usage_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    except (OSError, json.JSONDecodeError):
        return rows
    return rows


def _summarize_usage_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels = [str(row.get("label")) for row in rows if row.get("label")]
    return {
        "request_count": len(rows),
        "input_tokens": sum(_usage_int(row, "input_tokens", "prompt_tokens") for row in rows),
        "output_tokens": sum(_usage_int(row, "output_tokens", "completion_tokens") for row in rows),
        "total_tokens": sum(_usage_int(row, "total_tokens") for row in rows),
        "estimated_cost_usd": round(sum(_usage_float(row, "estimated_cost_usd") for row in rows), 6),
        "labels": labels,
    }


def _normalize_usage_summary(summary: dict[str, Any]) -> dict[str, Any]:
    labels = summary.get("labels")
    if not isinstance(labels, list):
        labels = []
    return {
        "request_count": _usage_int(summary, "request_count", "requests"),
        "input_tokens": _usage_int(summary, "input_tokens", "prompt_tokens"),
        "output_tokens": _usage_int(summary, "output_tokens", "completion_tokens"),
        "total_tokens": _usage_int(summary, "total_tokens"),
        "estimated_cost_usd": _usage_float(summary, "estimated_cost_usd"),
        "labels": [str(label) for label in labels if label],
    }


def _merge_usage_totals(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    labels: list[str] = []
    for summary in summaries:
        raw_labels = summary.get("labels")
        if isinstance(raw_labels, list):
            labels.extend(str(label) for label in raw_labels if label)
    return {
        "request_count": sum(_usage_int(row, "request_count") for row in summaries),
        "input_tokens": sum(_usage_int(row, "input_tokens") for row in summaries),
        "output_tokens": sum(_usage_int(row, "output_tokens") for row in summaries),
        "total_tokens": sum(_usage_int(row, "total_tokens") for row in summaries),
        "estimated_cost_usd": round(sum(_usage_float(row, "estimated_cost_usd") for row in summaries), 6),
        "labels": labels,
    }


def _usage_int(row: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if key not in row or row.get(key) is None:
            continue
        try:
            return int(row.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return 0


def _usage_float(row: dict[str, Any], *keys: str) -> float:
    for key in keys:
        if key not in row or row.get(key) is None:
            continue
        try:
            return float(row.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
    return 0.0


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
        analysis = meta.get("analysis")
        if not isinstance(analysis, dict):
            metadata = meta.get("metadata")
            analysis = metadata.get("analysis") if isinstance(metadata, dict) else None
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
        stats = row.stats if isinstance(row.stats, dict) else {}
        duration = stats.get("duration_sec")
        total_tokens = stats.get("llm_total_tokens")
        cost = stats.get("estimated_cost_usd")
        suffix_parts: list[str] = []
        if isinstance(duration, (int, float)):
            suffix_parts.append(f"{duration:.1f}s")
        if isinstance(total_tokens, int) and total_tokens:
            suffix_parts.append(f"{total_tokens} tokens")
        if isinstance(cost, (int, float)) and cost:
            suffix_parts.append(f"${cost:.4f}")
        suffix = f" ({', '.join(suffix_parts)})" if suffix_parts else ""
        _print(f"{topic:>4}  {row.status:<16} {detail}{suffix}")


def _resolve_summary_topics(args: argparse.Namespace, state: dict[str, TopicState]) -> list[str]:
    if getattr(args, "topics", None):
        raw_topics = [str(topic).upper() for topic in args.topics]
    elif getattr(args, "topic_set", None):
        raw_topics = TOPIC_SETS[str(args.topic_set)]
    else:
        raw_topics = sorted(state)
    seen: set[str] = set()
    topics: list[str] = []
    for topic in raw_topics:
        if topic in seen:
            continue
        seen.add(topic)
        if topic in state:
            topics.append(topic)
    return topics


def _build_batch_summary(ctx: RunnerContext, state: dict[str, TopicState], topics: list[str]) -> dict[str, Any]:
    rows = [_build_summary_row(ctx, _topic_state(state, topic)) for topic in topics]
    scored_rows = [row for row in rows if _is_number(row.get("overall_score"))]
    usage_rows = [row for row in rows if _is_number(row.get("llm_total_tokens"))]
    duration_rows = [row for row in rows if _is_number(row.get("duration_sec"))]
    command_names = ("init", "execute", "finalize", "score")
    aggregate = {
        "topic_count": len(rows),
        "completed_count": sum(1 for row in rows if row.get("status") == "completed"),
        "scored_count": len(scored_rows),
        "failed_count": sum(1 for row in rows if row.get("status") not in {"completed", "pending"}),
        "score_means": {
            "code_development": _mean(_score_values(scored_rows, "Code Development")),
            "code_execution": _mean(_score_values(scored_rows, "Code Execution")),
            "result_analysis": _mean(_score_values(scored_rows, "Result Analysis")),
            "overall": _mean([row.get("overall_score") for row in scored_rows]),
        },
        "runtime_means_sec": {
            "total": _mean([row.get("duration_sec") for row in duration_rows]),
            **{
                name: _mean(
                    [
                        row.get("command_durations_sec", {}).get(name)
                        for row in rows
                        if isinstance(row.get("command_durations_sec"), dict)
                    ]
                )
                for name in command_names
            },
        },
        "llm_usage_means": {
            "requests": _mean([row.get("llm_request_count") for row in usage_rows]),
            "input_tokens": _mean([row.get("llm_input_tokens") for row in usage_rows]),
            "output_tokens": _mean([row.get("llm_output_tokens") for row in usage_rows]),
            "total_tokens": _mean([row.get("llm_total_tokens") for row in usage_rows]),
            "estimated_cost_usd": _mean([row.get("estimated_cost_usd") for row in usage_rows]),
        },
        "llm_usage_totals": {
            "requests": sum(_num(row.get("llm_request_count")) for row in rows),
            "input_tokens": sum(_num(row.get("llm_input_tokens")) for row in rows),
            "output_tokens": sum(_num(row.get("llm_output_tokens")) for row in rows),
            "total_tokens": sum(_num(row.get("llm_total_tokens")) for row in rows),
            "estimated_cost_usd": round(sum(_num(row.get("estimated_cost_usd")) for row in rows), 6),
        },
    }
    return {
        "schema_version": "simple_ar_arc_batch_summary.v1",
        "generated_at": _now(),
        "state_file": _rel(ctx.repo_root, ctx.state_file),
        "topics": topics,
        "aggregate": aggregate,
        "rows": rows,
    }


def _build_summary_row(ctx: RunnerContext, topic_state: TopicState) -> dict[str, Any]:
    stats = _read_topic_stats(ctx, topic_state)
    judge = _read_judge_result(ctx, topic_state)
    usage = stats.get("llm_usage") if isinstance(stats.get("llm_usage"), dict) else {}
    usage_totals = usage.get("totals") if isinstance(usage.get("totals"), dict) else {}
    if not usage_totals:
        usage_totals = topic_state.stats if isinstance(topic_state.stats, dict) else {}
    command_durations = _command_durations(stats.get("commands"), topic_state.command_results)
    category_scores = judge.get("category_scores") if isinstance(judge.get("category_scores"), dict) else {}
    row = {
        "topic": topic_state.topic,
        "status": topic_state.status,
        "attempts": topic_state.attempts,
        "run_dir": topic_state.run_dir,
        "output_dir": topic_state.output_dir,
        "last_error": topic_state.last_error,
        "duration_sec": _first_number(stats.get("duration_sec"), topic_state.duration_sec),
        "command_duration_sec": _first_number(
            stats.get("command_duration_sec"),
            topic_state.stats.get("command_duration_sec") if isinstance(topic_state.stats, dict) else None,
        ),
        "command_durations_sec": command_durations,
        "scoring_profile": judge.get("scoring_profile", ""),
        "code_development": _category_score(category_scores, "Code Development"),
        "code_execution": _category_score(category_scores, "Code Execution"),
        "result_analysis": _category_score(category_scores, "Result Analysis"),
        "overall_score": _first_number(judge.get("overall_score"), judge.get("overall_strict")),
        "results_only": _first_number(judge.get("results_only")),
        "llm_request_count": _first_number(usage_totals.get("request_count"), usage_totals.get("llm_request_count")),
        "llm_input_tokens": _first_number(usage_totals.get("input_tokens"), usage_totals.get("llm_input_tokens")),
        "llm_output_tokens": _first_number(usage_totals.get("output_tokens"), usage_totals.get("llm_output_tokens")),
        "llm_total_tokens": _first_number(usage_totals.get("total_tokens"), usage_totals.get("llm_total_tokens")),
        "estimated_cost_usd": _first_number(usage_totals.get("estimated_cost_usd")),
    }
    return row


def _read_topic_stats(ctx: RunnerContext, topic_state: TopicState) -> dict[str, Any]:
    candidates: list[Path] = []
    if topic_state.output_dir:
        candidates.append(_abs(ctx.repo_root, topic_state.output_dir) / "arc_task_stats.json")
    if topic_state.run_dir:
        candidates.append(_abs(ctx.repo_root, topic_state.run_dir) / "arc_task_stats.json")
    for path in candidates:
        data = _read_json_dict(path)
        if data:
            return data
    stats = topic_state.stats if isinstance(topic_state.stats, dict) else {}
    return {
        "duration_sec": topic_state.duration_sec or stats.get("duration_sec"),
        "command_duration_sec": stats.get("command_duration_sec"),
        "commands": topic_state.command_results,
        "llm_usage": {
            "totals": {
                "request_count": stats.get("llm_request_count"),
                "input_tokens": stats.get("llm_input_tokens"),
                "output_tokens": stats.get("llm_output_tokens"),
                "total_tokens": stats.get("llm_total_tokens"),
                "estimated_cost_usd": stats.get("estimated_cost_usd"),
            }
        },
    }


def _read_judge_result(ctx: RunnerContext, topic_state: TopicState) -> dict[str, Any]:
    if not topic_state.output_dir:
        return {}
    return _read_json_dict(_abs(ctx.repo_root, topic_state.output_dir) / "judge" / "judge_result.json")


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _command_durations(primary: Any, fallback: Any) -> dict[str, float]:
    rows = primary if isinstance(primary, list) else fallback
    if not isinstance(rows, list):
        return {}
    durations: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _command_name(row.get("command"))
        if not name:
            continue
        value = _first_number(row.get("duration_sec"))
        if value is None:
            continue
        durations[name] = round(durations.get(name, 0.0) + value, 3)
    return durations


def _command_name(command: Any) -> str:
    if not isinstance(command, list):
        return ""
    parts = [str(part) for part in command]
    text = " ".join(parts)
    if "code-task init" in text:
        return "init"
    if "code-task execute" in text:
        return "execute"
    if "adapter.py finalize" in text:
        return "finalize"
    if "adapter.py score" in text:
        return "score"
    return ""


def _category_score(category_scores: dict[str, Any], expected: str) -> float | None:
    expected_keys = _score_keys(expected)
    for category, value in category_scores.items():
        if _score_key(str(category)) not in expected_keys:
            continue
        if isinstance(value, dict):
            return _first_number(value.get("score"))
        return _first_number(value)
    return None


def _score_keys(value: str) -> set[str]:
    key = _score_key(value)
    aliases = {
        "codedevelopment": {"codedevelopment", "codedev", "cd"},
        "codeexecution": {"codeexecution", "codeexec", "ce"},
        "resultanalysis": {"resultanalysis", "resultsanalysis", "ra"},
    }
    return aliases.get(key, {key})


def _score_key(value: str) -> str:
    value = value.lower().replace("&", "and")
    return "".join(char for char in value if char.isalnum())


def _score_values(rows: list[dict[str, Any]], category: str) -> list[Any]:
    key_map = {
        "Code Development": "code_development",
        "Code Execution": "code_execution",
        "Result Analysis": "result_analysis",
    }
    key = key_map[category]
    return [row.get(key) for row in rows]


def _mean(values: Iterable[Any]) -> float | None:
    numbers = [_num(value) for value in values if _is_number(value)]
    if not numbers:
        return None
    return round(sum(numbers) / len(numbers), 4)


def _first_number(*values: Any) -> float | None:
    for value in values:
        if _is_number(value):
            return _num(value)
    return None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _num(value: Any) -> float:
    return float(value) if _is_number(value) else 0.0


def _write_batch_summary(
    ctx: RunnerContext,
    summary: dict[str, Any],
    *,
    output_prefix: str | None,
) -> tuple[Path, Path]:
    if output_prefix:
        prefix = _abs(ctx.repo_root, output_prefix)
    else:
        prefix = ctx.state_file.with_name(f"{ctx.state_file.stem}.summary")
    json_path = prefix.parent / f"{prefix.name}.json"
    md_path = prefix.parent / f"{prefix.name}.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_batch_summary_markdown(summary), encoding="utf-8")
    return json_path, md_path


def render_batch_summary_markdown(summary: dict[str, Any]) -> str:
    aggregate = summary.get("aggregate") if isinstance(summary.get("aggregate"), dict) else {}
    score = aggregate.get("score_means") if isinstance(aggregate.get("score_means"), dict) else {}
    runtime = aggregate.get("runtime_means_sec") if isinstance(aggregate.get("runtime_means_sec"), dict) else {}
    usage_means = aggregate.get("llm_usage_means") if isinstance(aggregate.get("llm_usage_means"), dict) else {}
    usage_totals = aggregate.get("llm_usage_totals") if isinstance(aggregate.get("llm_usage_totals"), dict) else {}
    rows = summary.get("rows") if isinstance(summary.get("rows"), list) else []
    lines = [
        "# ARC-Bench Batch Summary",
        "",
        f"- State file: `{summary.get('state_file', '')}`",
        f"- Generated at: `{summary.get('generated_at', '')}`",
        f"- Topics: `{aggregate.get('topic_count', 0)}` total, `{aggregate.get('completed_count', 0)}` completed, `{aggregate.get('scored_count', 0)}` scored, `{aggregate.get('failed_count', 0)}` failed",
        "",
        "## Score Means",
        "",
        "| Code Dev | Code Exec | Result Analysis | Overall |",
        "| ---: | ---: | ---: | ---: |",
        (
            f"| {_fmt_score(score.get('code_development'))} | {_fmt_score(score.get('code_execution'))} | "
            f"{_fmt_score(score.get('result_analysis'))} | {_fmt_score(score.get('overall'))} |"
        ),
        "",
        "## Runtime And API Means",
        "",
        "| Total Time | Execute | Finalize | Score | LLM Calls | Input Tokens | Output Tokens | Total Tokens | Cost |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {_fmt_seconds(runtime.get('total'))} | {_fmt_seconds(runtime.get('execute'))} | "
            f"{_fmt_seconds(runtime.get('finalize'))} | {_fmt_seconds(runtime.get('score'))} | "
            f"{_fmt_number(usage_means.get('requests'))} | {_fmt_int(usage_means.get('input_tokens'))} | "
            f"{_fmt_int(usage_means.get('output_tokens'))} | {_fmt_int(usage_means.get('total_tokens'))} | "
            f"{_fmt_cost(usage_means.get('estimated_cost_usd'))} |"
        ),
        "",
        "## API Totals",
        "",
        "| LLM Calls | Input Tokens | Output Tokens | Total Tokens | Cost |",
        "| ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {_fmt_int(usage_totals.get('requests'))} | {_fmt_int(usage_totals.get('input_tokens'))} | "
            f"{_fmt_int(usage_totals.get('output_tokens'))} | {_fmt_int(usage_totals.get('total_tokens'))} | "
            f"{_fmt_cost(usage_totals.get('estimated_cost_usd'))} |"
        ),
        "",
        "## Topic Details",
        "",
        "| Topic | Status | Profile | Code Dev | Code Exec | Result Analysis | Overall | Time | Calls | Input | Output | Total |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        lines.append(
            "| "
            f"{row.get('topic', '')} | {row.get('status', '')} | {row.get('scoring_profile', '') or '-'} | "
            f"{_fmt_score(row.get('code_development'))} | {_fmt_score(row.get('code_execution'))} | "
            f"{_fmt_score(row.get('result_analysis'))} | {_fmt_score(row.get('overall_score'))} | "
            f"{_fmt_seconds(row.get('duration_sec'))} | {_fmt_int(row.get('llm_request_count'))} | "
            f"{_fmt_int(row.get('llm_input_tokens'))} | {_fmt_int(row.get('llm_output_tokens'))} | "
            f"{_fmt_int(row.get('llm_total_tokens'))} |"
        )
    lines.append("")
    return "\n".join(lines)


def _fmt_score(value: Any) -> str:
    return f"{float(value):.3f}" if _is_number(value) else "-"


def _fmt_seconds(value: Any) -> str:
    if not _is_number(value):
        return "-"
    seconds = float(value)
    if seconds >= 3600:
        return f"{seconds / 3600:.2f}h"
    if seconds >= 60:
        return f"{seconds / 60:.1f}m"
    return f"{seconds:.1f}s"


def _fmt_number(value: Any) -> str:
    return f"{float(value):.1f}" if _is_number(value) else "-"


def _fmt_int(value: Any) -> str:
    return f"{int(round(float(value))):,}" if _is_number(value) else "-"


def _fmt_cost(value: Any) -> str:
    return f"${float(value):.4f}" if _is_number(value) and float(value) else "-"


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
