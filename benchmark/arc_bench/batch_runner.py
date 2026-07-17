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

    refresh_parser = subparsers.add_parser(
        "refresh",
        help="Reuse completed run dirs and rerun finalize/score into a separate output variant.",
    )
    _add_common_options(refresh_parser)
    refresh_parser.add_argument(
        "--source-state-file",
        default=None,
        help=(
            "State file that contains completed run_dir entries to reuse. "
            "Defaults to the latest state file."
        ),
    )
    refresh_parser.add_argument(
        "--variant",
        default=None,
        help=(
            "Suffix for refreshed submission directories. "
            "Defaults to a timestamped variant derived from the new state file."
        ),
    )
    refresh_parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate the variant finalize/score artifacts even if they already look complete.",
    )
    refresh_parser.set_defaults(func=_refresh_command)

    status_parser = subparsers.add_parser("status", help="Print batch state summary.")
    _add_path_options(status_parser)
    status_parser.set_defaults(func=_status_command)

    summarize_parser = subparsers.add_parser(
        "summarize",
        help="Aggregate batch scores, durations, and LLM usage without rerunning any topic.",
    )
    _add_summary_options(summarize_parser)
    summarize_parser.set_defaults(func=_summarize_command)

    evidence_parser = subparsers.add_parser(
        "evidence",
        help="Build a paper-oriented ARC fidelity/repair evidence summary without rerunning any topic.",
    )
    _add_summary_options(evidence_parser)
    evidence_parser.set_defaults(func=_evidence_command)

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
            "For 'run' and 'refresh', omitting this creates a new timestamped state and marks it latest. "
            "For 'retry-unfinished', 'status', and 'summarize', omitting this reads the latest state."
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
        default=None,
        help=(
            "Named topic set to run when --topics/--topic-range are not provided. "
            "Use quick, breadth/next, specialized/high-risk, or all."
        ),
    )
    parser.add_argument("--topics", nargs="+", help="Explicit topic list, e.g. --topics ML04 ML02.")
    parser.add_argument(
        "--topic-range",
        action="append",
        default=[],
        metavar="ML01-ML10",
        help="Inclusive topic range. May be repeated; combined with --topics.",
    )
    parser.add_argument(
        "--exclude-topics",
        nargs="+",
        default=[],
        help="Topics to remove from the resolved set, e.g. --exclude-topics ML02 ML17.",
    )
    parser.add_argument("--analyze", action="store_true", help="Run finalize with LLM result analysis.")
    parser.add_argument("--analysis-model", help="Override SIMPLE_AR_MODEL for finalize --analyze.")
    parser.add_argument("--score", action="store_true", help="Run the built-in LLM leaf-level scorer after finalize.")
    parser.add_argument("--score-model", help="Override SIMPLE_AR_MODEL for adapter score.")
    parser.add_argument(
        "--native-score",
        action="store_true",
        help="Run AutoResearchClaw's native ARC-Bench judge.py after finalize; writes judge_native/.",
    )
    parser.add_argument("--native-score-model", help="Override ARC_JUDGE_MODEL for native ARC-Bench judge.")
    parser.add_argument(
        "--arc-root",
        help=(
            "Path to AutoResearchClaw/experiments/arc_bench for --native-score. "
            "Defaults to AutoResearchClaw/experiments/arc_bench under the repo root."
        ),
    )
    parser.add_argument(
        "--score-profile",
        choices=("proxy", "manual-strict"),
        default="proxy",
        help=(
            "Scoring profile passed to adapter.py score. Use manual-strict for two-reviewer strict auditing."
        ),
    )
    parser.add_argument(
        "--strict-reviewer-models",
        help="Comma-separated reviewer models for --score-profile manual-strict.",
    )
    parser.add_argument(
        "--strict-reviewer-apis",
        help="Optional comma-separated reviewer API modes for --score-profile manual-strict, e.g. chat,responses.",
    )
    parser.add_argument(
        "--strict-adjudicator-model",
        help="Optional adjudicator model for --score-profile manual-strict.",
    )
    parser.add_argument(
        "--strict-adjudicator-api",
        help="Optional adjudicator API mode for --score-profile manual-strict.",
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
    parser.add_argument(
        "--repair-rounds",
        type=int,
        default=None,
        help="Override code-task --repair-rounds for every execute call; omit to keep each TOML default.",
    )
    parser.add_argument(
        "--planning-review-rounds",
        type=int,
        default=None,
        help="Override code-task --planning-review-rounds for every execute call; omit to keep each TOML default.",
    )
    parser.add_argument(
        "--planning-mode",
        choices=("tool_agent", "compact"),
        default=None,
        help="Override code-task --planning-mode for every execute call; omit to keep each TOML default.",
    )
    parser.add_argument(
        "--repair-context",
        choices=("full", "raw_logs_only"),
        default=None,
        help="Ablation passthrough for code-task execute repair context.",
    )
    parser.add_argument(
        "--no-repair-memory",
        action="store_true",
        help="Ablation passthrough: omit previous repair memory from code-task repair prompts.",
    )
    parser.add_argument(
        "--contract-context",
        choices=("full", "minimal"),
        default=None,
        help="Ablation passthrough for code-task prompt contract context.",
    )
    parser.add_argument(
        "--review-gate",
        choices=("strict", "runtime"),
        default=None,
        help=(
            "Ablation passthrough for greenfield review gating. `runtime` lets "
            "non-runtime review blockers proceed to validation/run."
        ),
    )
    parser.add_argument(
        "--skip-any-result",
        action="store_true",
        help=(
            "Skip topics that already have a terminal state, including failures. "
            "Use this for one-shot ablations where failed topics count as results "
            "and only interrupted/pending topics should continue."
        ),
    )


def _add_summary_options(parser: argparse.ArgumentParser) -> None:
    _add_path_options(parser)
    parser.add_argument(
        "--topic-set",
        choices=sorted(TOPIC_SETS),
        help="Optional named topic set filter; omit to summarize all topics in the state file.",
    )
    parser.add_argument("--topics", nargs="+", help="Optional explicit topic filter, e.g. --topics ML04 ML02.")
    parser.add_argument("--topic-range", action="append", default=[], metavar="ML01-ML10")
    parser.add_argument("--exclude-topics", nargs="+", default=[])
    parser.add_argument(
        "--output-prefix",
        help=(
            "Optional output path prefix. Defaults to '<state-file>.summary' and writes "
            "both .json and .md."
        ),
    )
    parser.add_argument(
        "--judge-source",
        choices=("auto", "native", "manual-strict", "adapter"),
        default="auto",
        help=(
            "Which judge output to summarize. auto prefers native, then manual-strict, then legacy adapter judge."
        ),
    )
    parser.add_argument(
        "--failed-as-zero",
        action="store_true",
        help=(
            "Include failed/unscored topics in score means as zero for Code Dev, "
            "Code Exec, Result Analysis, and Overall. Useful for one-shot ablations."
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
        if args.skip_any_result and not args.force and _state_has_terminal_result(current):
            _print(f"[skip] {topic}: existing terminal result ({current.status})")
            continue
        if current.status == "completed" and not args.force:
            if _completed_state_still_valid(
                ctx,
                current,
                require_analysis=args.analyze,
                require_score=args.score,
                require_native_score=args.native_score,
                score_profile=args.score_profile,
            ):
                _print(f"[skip] {topic}: already completed at {current.output_dir}")
                continue
            if (args.score or args.native_score) and _completed_state_still_valid(
                ctx,
                current,
                require_analysis=args.analyze,
                require_score=False,
                require_native_score=False,
                score_profile=args.score_profile,
            ):
                _print(f"[score] {topic}: finalized output exists; scoring without rerunning experiment.")
                result = _score_existing_topic(
                    ctx,
                    topic,
                    current,
                    score=args.score,
                    score_model=args.score_model,
                    score_profile=args.score_profile,
                    native_score=args.native_score,
                    native_score_model=args.native_score_model,
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
            native_score=args.native_score,
            native_score_model=args.native_score_model,
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
    topics: list[str] = []
    for topic in candidate_topics:
        current = _topic_state(state, topic)
        if getattr(args, "skip_any_result", False) and _state_has_terminal_result(current):
            _print(f"[skip] {topic}: existing terminal result ({current.status})")
            continue
        if _unfinished_or_stale_completed(
            ctx,
            current,
            require_analysis=args.analyze,
            require_score=args.score,
            require_native_score=args.native_score,
            score_profile=args.score_profile,
        ):
            topics.append(topic)

    if not topics:
        _print("No unfinished topics found.")
        return 0

    exit_code = 0
    for topic in topics:
        current = _topic_state(state, topic)
        if (
            (args.score or args.native_score)
            and current.status in {"score_failed", "native_score_failed"}
            and _scorable_state_exists(ctx, current)
        ):
            _print(f"[score-retry] {topic}: finalized output exists; retrying failed judge only.")
            result = _score_existing_topic(
                ctx,
                topic,
                current,
                score=args.score,
                score_model=args.score_model,
                score_profile=args.score_profile,
                native_score=args.native_score,
                native_score_model=args.native_score_model,
            )
            state[topic] = result
            _save_state(ctx.state_file, state)
            if result.status != "completed":
                exit_code = 1
            continue
        if (args.score or args.native_score) and current.status in {"score_failed", "native_score_failed"}:
            _print(f"[skip] {topic}: previous judge failed, but no finalized submission is available to rescore.")
            exit_code = 1
            continue
        if (args.score or args.native_score) and current.status == "completed" and _completed_state_still_valid(
            ctx,
            current,
            require_analysis=args.analyze,
            require_score=False,
            require_native_score=False,
            score_profile=args.score_profile,
        ):
            _print(f"[score] {topic}: finalized output exists; scoring without rerunning experiment.")
            result = _score_existing_topic(
                ctx,
                topic,
                current,
                score=args.score,
                score_model=args.score_model,
                score_profile=args.score_profile,
                native_score=args.native_score,
                native_score_model=args.native_score_model,
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
                native_score=args.native_score,
                native_score_model=args.native_score_model,
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
            native_score=args.native_score,
            native_score_model=args.native_score_model,
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


def _refresh_command(args: argparse.Namespace) -> int:
    ctx = _context_from_args(args)
    source_state_file = _resolve_source_state_file(ctx.repo_root, args)
    source_state = _load_state(source_state_file)
    _print(f"[source-state] {source_state_file}")
    if not source_state:
        if source_state_file.exists():
            _print(f"Source state file has no topic entries: {source_state_file}.")
        else:
            _print(f"No source state file found at {source_state_file}.")
        return 1

    _remember_latest_state(ctx)
    refreshed_state = _load_state(ctx.state_file)
    if not refreshed_state:
        _save_state(ctx.state_file, refreshed_state)
    _print(f"[state] {ctx.state_file}")

    topics = _resolve_topics(args, ctx.prepared_root)
    variant = _refresh_variant(args, ctx)
    _print(f"[variant] {variant}")

    exit_code = 0
    for topic in topics:
        current = _topic_state(source_state, topic)
        if current.status != "completed":
            result = TopicState(
                topic=topic,
                status="source_not_completed",
                attempts=current.attempts,
                run_dir=current.run_dir,
                output_dir=current.output_dir,
                last_error=f"source state status is {current.status}",
                logs=list(current.logs),
                commands=list(current.commands),
                command_results=list(current.command_results),
                updated_at=_now(),
            )
            _print(f"[skip] {topic}: source state status is {current.status}; cannot refresh finalize/score.")
            refreshed_state[topic] = result
            _save_state(ctx.state_file, refreshed_state)
            exit_code = 1
            continue

        result = _refresh_completed_topic(
            ctx,
            topic,
            current,
            analyze=args.analyze,
            analysis_model=args.analysis_model,
            score=args.score,
            score_model=args.score_model,
            score_profile=args.score_profile,
            native_score=args.native_score,
            native_score_model=args.native_score_model,
            variant=variant,
            force=args.force,
            incremental_stats=True,
        )
        refreshed_state[topic] = result
        _save_state(ctx.state_file, refreshed_state)
        if result.status != "completed":
            exit_code = 1

    _print_summary(refreshed_state, topics)
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
    summary = _build_batch_summary(
        ctx,
        state,
        topics,
        judge_source=getattr(args, "judge_source", "auto"),
        failed_as_zero=bool(getattr(args, "failed_as_zero", False)),
    )
    json_path, md_path = _write_batch_summary(ctx, summary, output_prefix=getattr(args, "output_prefix", None))
    _print(render_batch_summary_markdown(summary))
    _print(f"\n[summary] wrote {_rel(ctx.repo_root, json_path)}")
    _print(f"[summary] wrote {_rel(ctx.repo_root, md_path)}")
    return 0


def _evidence_command(args: argparse.Namespace) -> int:
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
        _print("No topics selected for evidence summary.")
        return 0
    summary = _build_batch_summary(ctx, state, topics, judge_source=getattr(args, "judge_source", "auto"))
    evidence = _build_evidence_summary(ctx, summary)
    json_path, md_path = _write_evidence_summary(ctx, evidence, output_prefix=getattr(args, "output_prefix", None))
    _print(render_evidence_summary_markdown(evidence))
    _print(f"\n[evidence] wrote {_rel(ctx.repo_root, json_path)}")
    _print(f"[evidence] wrote {_rel(ctx.repo_root, md_path)}")
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
    strict_reviewer_models: str | None = None
    strict_reviewer_apis: str | None = None
    strict_adjudicator_model: str | None = None
    strict_adjudicator_api: str | None = None
    repair_rounds: int | None = None
    planning_review_rounds: int | None = None
    planning_mode: str | None = None
    repair_context: str | None = None
    use_repair_memory: bool = True
    contract_context: str | None = None
    review_gate: str | None = None
    arc_root: Path | None = None


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
        strict_reviewer_models=getattr(args, "strict_reviewer_models", None),
        strict_reviewer_apis=getattr(args, "strict_reviewer_apis", None),
        strict_adjudicator_model=getattr(args, "strict_adjudicator_model", None),
        strict_adjudicator_api=getattr(args, "strict_adjudicator_api", None),
        repair_rounds=getattr(args, "repair_rounds", None),
        planning_review_rounds=getattr(args, "planning_review_rounds", None),
        planning_mode=getattr(args, "planning_mode", None),
        repair_context=getattr(args, "repair_context", None),
        use_repair_memory=not bool(getattr(args, "no_repair_memory", False)),
        contract_context=getattr(args, "contract_context", None),
        review_gate=getattr(args, "review_gate", None),
        arc_root=(_abs(repo_root, args.arc_root) if getattr(args, "arc_root", None) else None),
    )


def _normalize_score_profile(value: str | None) -> str:
    profile = (value or "proxy").strip().lower()
    return profile


def _adapter_judge_dir(output_dir: Path, score_profile: str) -> Path:
    return output_dir / ("judge_manual_strict" if _normalize_score_profile(score_profile) == "manual-strict" else "judge")


def _run_topic(
    ctx: RunnerContext,
    topic: str,
    *,
    analyze: bool,
    analysis_model: str | None,
    score: bool,
    score_model: str | None,
    score_profile: str,
    native_score: bool,
    native_score_model: str | None,
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
    repair_rounds = repair_rounds_override if repair_rounds_override is not None else ctx.repair_rounds
    if repair_rounds is not None:
        execute_cmd.extend(["--repair-rounds", str(repair_rounds)])
    if ctx.planning_review_rounds is not None:
        execute_cmd.extend(["--planning-review-rounds", str(ctx.planning_review_rounds)])
    if ctx.planning_mode:
        execute_cmd.extend(["--planning-mode", ctx.planning_mode])
    if ctx.llm_retry_attempts > 0:
        execute_cmd.extend(["--llm-retry-attempts", str(ctx.llm_retry_attempts)])
    if ctx.repair_context:
        execute_cmd.extend(["--repair-context", ctx.repair_context])
    if not ctx.use_repair_memory:
        execute_cmd.append("--no-repair-memory")
    if ctx.contract_context:
        execute_cmd.extend(["--contract-context", ctx.contract_context])
    if ctx.review_gate:
        execute_cmd.extend(["--review-gate", ctx.review_gate])
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
            _rel(ctx.repo_root, _adapter_judge_dir(output_dir, score_profile)),
            "--score-profile",
            score_profile,
        ]
        if score_model:
            score_cmd.extend(["--model", score_model])
        if ctx.strict_reviewer_models:
            score_cmd.extend(["--strict-reviewer-models", ctx.strict_reviewer_models])
        if ctx.strict_reviewer_apis:
            score_cmd.extend(["--strict-reviewer-apis", ctx.strict_reviewer_apis])
        if ctx.strict_adjudicator_model:
            score_cmd.extend(["--strict-adjudicator-model", ctx.strict_adjudicator_model])
        if ctx.strict_adjudicator_api:
            score_cmd.extend(["--strict-adjudicator-api", ctx.strict_adjudicator_api])
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

    if native_score:
        native_result = _run_native_score(
            ctx,
            topic=topic,
            prepared_dir=prepared_dir,
            output_dir=output_dir,
            topic_log_root=topic_log_root,
            model=native_score_model,
        )
        _record_command(topic_state, native_result)
        if native_result.returncode != 0:
            return _finalize_topic_state(
                ctx,
                _fail(topic_state, "native_score_failed", f"native score exited with {native_result.returncode}"),
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
    score: bool,
    score_model: str | None,
    score_profile: str,
    native_score: bool,
    native_score_model: str | None,
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
            _rel(ctx.repo_root, _adapter_judge_dir(output_dir, score_profile)),
            "--score-profile",
            score_profile,
        ]
        if score_model:
            score_cmd.extend(["--model", score_model])
        if ctx.strict_reviewer_models:
            score_cmd.extend(["--strict-reviewer-models", ctx.strict_reviewer_models])
        if ctx.strict_reviewer_apis:
            score_cmd.extend(["--strict-reviewer-apis", ctx.strict_reviewer_apis])
        if ctx.strict_adjudicator_model:
            score_cmd.extend(["--strict-adjudicator-model", ctx.strict_adjudicator_model])
        if ctx.strict_adjudicator_api:
            score_cmd.extend(["--strict-adjudicator-api", ctx.strict_adjudicator_api])
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
    if native_score:
        native_result = _run_native_score(
            ctx,
            topic=topic,
            prepared_dir=prepared_dir,
            output_dir=output_dir,
            topic_log_root=topic_log_root,
            model=native_score_model,
        )
        _record_command(topic_state, native_result)
        if native_result.returncode != 0:
            return _finalize_topic_state(
                ctx,
                _fail(topic_state, "native_score_failed", f"native score exited with {native_result.returncode}"),
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
    native_score: bool,
    native_score_model: str | None,
    variant: str | None = None,
    force: bool = False,
    incremental_stats: bool = False,
) -> TopicState:
    started_monotonic = time.monotonic()
    started_at = _now()
    logs = [] if incremental_stats else list(current.logs)
    commands = [] if incremental_stats else list(current.commands)
    command_results = [] if incremental_stats else list(current.command_results)
    topic_state = TopicState(
        topic=topic,
        status="running",
        attempts=current.attempts,
        run_dir=current.run_dir,
        output_dir=current.output_dir,
        started_at=started_at,
        logs=logs,
        commands=commands,
        command_results=command_results,
        updated_at=_now(),
    )
    topic_state.stats["skip_run_stats_write"] = True
    if incremental_stats:
        topic_state.stats["skip_code_task_usage"] = True
        topic_state.stats["source_run_dir"] = current.run_dir
        topic_state.stats["source_output_dir"] = current.output_dir
        topic_state.stats["variant"] = variant or ""
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

    output_dir = _variant_output_dir(ctx, topic, run_dir, variant) if variant else (
        _abs(ctx.repo_root, current.output_dir) if current.output_dir else ctx.submissions_root / topic / run_dir.name
    )
    topic_state.output_dir = _rel(ctx.repo_root, output_dir)
    topic_log_root = ctx.log_root / topic / _timestamp()

    finalize_complete = False if force else _finalized_output_complete(
        output_dir,
        require_analysis=analyze,
        require_score=False,
        require_native_score=False,
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
        score_complete = False if force else _finalized_output_complete(
            output_dir,
            require_analysis=analyze,
            require_score=True,
            require_native_score=False,
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
                _rel(ctx.repo_root, _adapter_judge_dir(output_dir, score_profile)),
                "--score-profile",
                score_profile,
            ]
            if score_model:
                score_cmd.extend(["--model", score_model])
            if ctx.strict_reviewer_models:
                score_cmd.extend(["--strict-reviewer-models", ctx.strict_reviewer_models])
            if ctx.strict_reviewer_apis:
                score_cmd.extend(["--strict-reviewer-apis", ctx.strict_reviewer_apis])
            if ctx.strict_adjudicator_model:
                score_cmd.extend(["--strict-adjudicator-model", ctx.strict_adjudicator_model])
            if ctx.strict_adjudicator_api:
                score_cmd.extend(["--strict-adjudicator-api", ctx.strict_adjudicator_api])
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

    if native_score:
        native_complete = False if force else _finalized_output_complete(
            output_dir,
            require_analysis=analyze,
            require_score=False,
            require_native_score=True,
            score_profile=score_profile,
        )
        if native_complete:
            _print(f"[refresh] {topic}: native judge already exists; skipping native score.")
        else:
            native_result = _run_native_score(
                ctx,
                topic=topic,
                prepared_dir=prepared_dir,
                output_dir=output_dir,
                topic_log_root=topic_log_root,
                model=native_score_model,
            )
            _record_command(topic_state, native_result)
            if native_result.returncode != 0:
                return _finalize_topic_state(
                    ctx,
                    _fail(topic_state, "native_score_failed", f"native score exited with {native_result.returncode}"),
                    started_monotonic=started_monotonic,
                )

    if not _completed_state_still_valid(
        ctx,
        topic_state,
        require_analysis=analyze,
        require_score=score,
        require_native_score=native_score,
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
    env.setdefault("PYTHONUNBUFFERED", "1")
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
            for line in proc.stdout:
                if line:
                    output_queue.put(line)
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
    usage = _collect_topic_llm_usage(
        ctx,
        run_dir=run_dir,
        output_dir=output_dir,
        include_code_task=topic_state.stats.get("skip_code_task_usage") is not True,
    )
    metadata = {
        key: topic_state.stats.get(key)
        for key in ("source_run_dir", "source_output_dir", "variant")
        if topic_state.stats.get(key)
    }
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
        "write_run_stats": topic_state.stats.get("skip_run_stats_write") is not True,
        "metadata": metadata,
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
    if topic_state.run_dir and stats.get("write_run_stats") is not False:
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
    include_code_task: bool = True,
) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    if include_code_task and run_dir is not None:
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
        sources.append(
            _usage_source(
                ctx,
                stage="manual_strict_score",
                summary_path=output_dir / "judge_manual_strict" / "llm_usage_summary.json",
                jsonl_path=output_dir / "judge_manual_strict" / "llm_usage.jsonl",
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
    ranges = getattr(args, "topic_range", []) or []
    if getattr(args, "topics", None):
        raw_topics = list(args.topics)
    elif ranges:
        raw_topics = []
    elif getattr(args, "topic_set", None):
        raw_topics = list(TOPIC_SETS[str(args.topic_set)])
    else:
        raw_topics = list(TOPIC_SETS["quick"])
    for raw_range in ranges:
        raw_topics.extend(_expand_topic_range(str(raw_range)))
    excluded = {_normalize_topic_id(topic) for topic in getattr(args, "exclude_topics", []) or []}
    topics = [_normalize_topic_id(topic) for topic in raw_topics]
    seen: set[str] = set()
    unique_topics: list[str] = []
    for topic in topics:
        if topic in excluded:
            continue
        if topic in seen:
            continue
        seen.add(topic)
        unique_topics.append(topic)
    missing = [topic for topic in unique_topics if not (prepared_root / topic / "code_task.toml").exists()]
    if missing:
        raise SystemExit(f"Missing prepared topic(s): {', '.join(missing)} under {prepared_root}")
    return unique_topics


def _normalize_topic_id(value: object) -> str:
    text = str(value).strip().upper().replace("_", "")
    if text.startswith("ML") and text[2:].isdigit():
        return f"ML{int(text[2:]):02d}"
    if text.isdigit():
        return f"ML{int(text):02d}"
    return text


def _expand_topic_range(value: str) -> list[str]:
    text = value.strip().upper().replace("_", "")
    if not text:
        return []
    if "-" not in text:
        return [_normalize_topic_id(text)]
    left, right = [part.strip() for part in text.split("-", 1)]
    start = _topic_number(left)
    end = _topic_number(right)
    if start is None or end is None:
        raise SystemExit(f"Invalid --topic-range `{value}`. Expected format like ML01-ML10.")
    step = 1 if end >= start else -1
    return [f"ML{number:02d}" for number in range(start, end + step, step)]


def _topic_number(value: str) -> int | None:
    topic = _normalize_topic_id(value)
    if topic.startswith("ML") and topic[2:].isdigit():
        return int(topic[2:])
    return None


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
    require_native_score: bool = False,
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
        require_native_score=require_native_score,
        score_profile=score_profile,
    )


def _run_native_score(
    ctx: RunnerContext,
    *,
    topic: str,
    prepared_dir: Path,
    output_dir: Path,
    topic_log_root: Path,
    model: str | None,
) -> CommandResult:
    native_cmd = [
        "uv",
        "run",
        "python",
        "benchmark/arc_bench/adapter.py",
        "native-score",
        "--prepared-dir",
        _rel(ctx.repo_root, prepared_dir),
        "--run-dir",
        _rel(ctx.repo_root, output_dir),
        "--output-dir",
        _rel(ctx.repo_root, output_dir / "judge_native"),
        "--topic",
        topic,
        "--full",
        "--debug",
    ]
    if ctx.arc_root is not None:
        native_cmd.extend(["--arc-root", _rel(ctx.repo_root, ctx.arc_root)])
    if model:
        native_cmd.extend(["--model", model])
    return _run_logged(
        native_cmd,
        ctx.repo_root,
        topic_log_root / "native_score.log",
        timeout=ctx.score_timeout,
    )


def _unfinished_or_stale_completed(
    ctx: RunnerContext,
    state: TopicState,
    *,
    require_analysis: bool,
    require_score: bool,
    require_native_score: bool = False,
    score_profile: str = "proxy",
) -> bool:
    if state.status != "completed":
        return True
    return not _completed_state_still_valid(
        ctx,
        state,
        require_analysis=require_analysis,
        require_score=require_score,
        require_native_score=require_native_score,
        score_profile=score_profile,
    )


def _state_has_terminal_result(state: TopicState) -> bool:
    status = (state.status or "").strip().lower()
    if status in {"", "pending", "running"}:
        return False
    if status == "completed":
        return True
    return bool(state.run_dir or state.output_dir or state.last_error or state.logs or state.commands)


def _scorable_state_exists(ctx: RunnerContext, state: TopicState) -> bool:
    if not state.output_dir:
        return False
    output_dir = _abs(ctx.repo_root, state.output_dir)
    required_files = [
        output_dir / "submission" / "README.md",
        output_dir / "submission" / "claims.json",
        output_dir / "submission" / "results" / "metrics.json",
    ]
    return all(path.is_file() for path in required_files) and (output_dir / "submission" / "code").exists()


def _finalized_output_complete(
    output_dir: Path,
    *,
    require_analysis: bool,
    require_score: bool,
    require_native_score: bool = False,
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
        judge_dir = _adapter_judge_dir(output_dir, score_profile)
        for path in [
            judge_dir / "judge_result.json",
            judge_dir / "scorecard.md",
        ]:
            if not path.is_file():
                return False
        try:
            judge = json.loads((judge_dir / "judge_result.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(judge.get("overall_score"), (int, float)):
            return False
        if _normalize_score_profile(str(judge.get("scoring_profile") or "proxy")) != _normalize_score_profile(score_profile):
            return False
    if require_native_score:
        native_result = output_dir / "judge_native" / "judge_result.json"
        if not native_result.is_file():
            return False
        try:
            judge = json.loads(native_result.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(judge.get("overall_score"), (int, float)):
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
    if command in {"run", "refresh"}:
        return _new_batch_state_file(repo_root, args)
    return _latest_state_file(repo_root)


def _resolve_source_state_file(repo_root: Path, args: argparse.Namespace) -> Path:
    raw_state_file = getattr(args, "source_state_file", None)
    if raw_state_file:
        if str(raw_state_file).strip().lower() == "latest":
            return _latest_state_file(repo_root)
        return _abs(repo_root, raw_state_file)
    return _latest_state_file(repo_root)


def _refresh_variant(args: argparse.Namespace, ctx: RunnerContext) -> str:
    raw = getattr(args, "variant", None)
    if raw and str(raw).strip():
        return _safe_filename(str(raw))
    return _safe_filename(f"refresh-{ctx.state_file.stem}")


def _variant_output_dir(ctx: RunnerContext, topic: str, run_dir: Path, variant: str | None) -> Path:
    if not variant:
        return ctx.submissions_root / topic / run_dir.name
    return ctx.submissions_root / topic / f"{run_dir.name}--{_safe_filename(variant)}"


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
    ranges = getattr(args, "topic_range", []) or []
    if getattr(args, "topics", None):
        raw_topics = [_normalize_topic_id(topic) for topic in args.topics]
    elif ranges:
        raw_topics = []
    elif getattr(args, "topic_set", None):
        raw_topics = list(TOPIC_SETS[str(args.topic_set)])
    else:
        raw_topics = sorted(state)
    for raw_range in ranges:
        raw_topics.extend(_expand_topic_range(str(raw_range)))
    excluded = {_normalize_topic_id(topic) for topic in getattr(args, "exclude_topics", []) or []}
    seen: set[str] = set()
    topics: list[str] = []
    for topic in raw_topics:
        topic = _normalize_topic_id(topic)
        if topic in excluded:
            continue
        if topic in seen:
            continue
        seen.add(topic)
        if topic in state:
            topics.append(topic)
    return topics


def _build_batch_summary(
    ctx: RunnerContext,
    state: dict[str, TopicState],
    topics: list[str],
    *,
    judge_source: str = "auto",
    failed_as_zero: bool = False,
) -> dict[str, Any]:
    rows = [_build_summary_row(ctx, _topic_state(state, topic), judge_source=judge_source) for topic in topics]
    if failed_as_zero:
        rows = [_with_failed_zero_scores(row) for row in rows]
    scored_rows = [row for row in rows if _is_number(row.get("overall_score"))]
    usage_rows = [row for row in rows if _is_number(row.get("llm_total_tokens"))]
    duration_rows = [row for row in rows if _is_number(row.get("duration_sec"))]
    postprocess_duration_rows = [row for row in rows if _is_number(row.get("postprocess_duration_sec"))]
    postprocess_usage_rows = [row for row in rows if _is_number(row.get("postprocess_llm_total_tokens"))]
    repair_rows = [row for row in rows if _is_number(row.get("total_repair_count"))]
    execution_attempt_rows = [row for row in rows if _is_number(row.get("execution_attempts"))]
    command_names = ("init", "execute", "finalize", "score")
    aggregate = {
        "topic_count": len(rows),
        "completed_count": sum(1 for row in rows if row.get("status") == "completed"),
        "scored_count": len(scored_rows),
        "zero_imputed_count": sum(1 for row in rows if row.get("score_imputed_zero") is True),
        "failed_count": sum(1 for row in rows if row.get("status") not in {"completed", "pending"}),
        "score_means": {
            "code_development": _mean(_score_values(scored_rows, "Code Development")),
            "code_execution": _mean(_score_values(scored_rows, "Code Execution")),
            "result_analysis": _mean(_score_values(scored_rows, "Result Analysis")),
            "overall": _mean([row.get("overall_score") for row in scored_rows]),
        },
        "runtime_means_sec": {
            "total": _mean([row.get("duration_sec") for row in duration_rows]),
            "postprocess": _mean([row.get("postprocess_duration_sec") for row in postprocess_duration_rows]),
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
        "postprocess_llm_usage_means": {
            "requests": _mean([row.get("postprocess_llm_request_count") for row in postprocess_usage_rows]),
            "input_tokens": _mean([row.get("postprocess_llm_input_tokens") for row in postprocess_usage_rows]),
            "output_tokens": _mean([row.get("postprocess_llm_output_tokens") for row in postprocess_usage_rows]),
            "total_tokens": _mean([row.get("postprocess_llm_total_tokens") for row in postprocess_usage_rows]),
        },
        "postprocess_llm_usage_totals": {
            "requests": sum(_num(row.get("postprocess_llm_request_count")) for row in rows),
            "input_tokens": sum(_num(row.get("postprocess_llm_input_tokens")) for row in rows),
            "output_tokens": sum(_num(row.get("postprocess_llm_output_tokens")) for row in rows),
            "total_tokens": sum(_num(row.get("postprocess_llm_total_tokens")) for row in rows),
        },
        "repair_means": {
            "total_repair_count": _mean([row.get("total_repair_count") for row in repair_rows]),
            "review_repair_count": _mean([row.get("review_repair_count") for row in repair_rows]),
            "run_repair_count": _mean([row.get("run_repair_count") for row in repair_rows]),
            "existing_project_repair_count": _mean([row.get("repair_count") for row in repair_rows]),
            "execution_attempts": _mean([row.get("execution_attempts") for row in execution_attempt_rows]),
            "failed_execution_attempts": _mean([row.get("failed_execution_attempts") for row in execution_attempt_rows]),
            "repair_memory_entries": _mean([row.get("repair_memory_entries") for row in rows]),
            "review_findings_entries": _mean([row.get("review_findings_entries") for row in rows]),
        },
        "repair_totals": {
            "total_repair_count": sum(_num(row.get("total_repair_count")) for row in rows),
            "review_repair_count": sum(_num(row.get("review_repair_count")) for row in rows),
            "run_repair_count": sum(_num(row.get("run_repair_count")) for row in rows),
            "existing_project_repair_count": sum(_num(row.get("repair_count")) for row in rows),
            "execution_attempts": sum(_num(row.get("execution_attempts")) for row in rows),
            "failed_execution_attempts": sum(_num(row.get("failed_execution_attempts")) for row in rows),
            "repair_memory_entries": sum(_num(row.get("repair_memory_entries")) for row in rows),
            "review_findings_entries": sum(_num(row.get("review_findings_entries")) for row in rows),
        },
        "failure_type_counts": _count_values(
            row.get("failure_type")
            for row in rows
            if isinstance(row.get("failure_type"), str) and row.get("failure_type")
        ),
    }
    return {
        "schema_version": "simple_ar_arc_batch_summary.v1",
        "generated_at": _now(),
        "state_file": _rel(ctx.repo_root, ctx.state_file),
        "judge_source": judge_source,
        "failed_as_zero": failed_as_zero,
        "topics": topics,
        "aggregate": aggregate,
        "rows": rows,
    }


def _build_summary_row(ctx: RunnerContext, topic_state: TopicState, *, judge_source: str = "auto") -> dict[str, Any]:
    stats = _read_topic_stats(ctx, topic_state)
    source_stats = _read_source_code_task_stats(ctx, stats)
    source_run_dir = _source_run_dir_for_summary(ctx, topic_state, stats)
    repair_stats = _repair_stats_from_run_dir(ctx, source_run_dir) if source_run_dir else {}
    judge = _read_judge_result(ctx, topic_state, judge_source=judge_source)
    usage = stats.get("llm_usage") if isinstance(stats.get("llm_usage"), dict) else {}
    usage_totals = usage.get("totals") if isinstance(usage.get("totals"), dict) else {}
    if not usage_totals:
        usage_totals = topic_state.stats if isinstance(topic_state.stats, dict) else {}
    postprocess_usage_totals = usage_totals if isinstance(usage_totals, dict) else {}
    source_code_task_usage = _code_task_usage_from_stats(source_stats)
    if source_code_task_usage:
        usage_totals = _merge_usage_totals([source_code_task_usage, postprocess_usage_totals])
    command_durations = _command_durations(stats.get("commands"), topic_state.command_results)
    source_command_durations = _source_code_task_command_durations(source_stats)
    if source_command_durations:
        command_durations = {
            **source_command_durations,
            **command_durations,
        }
    postprocess_duration = _sum_duration_keys(command_durations, ("finalize", "score"))
    total_duration = _sum_duration_keys(command_durations, ("init", "execute", "finalize", "score"))
    if total_duration is None:
        total_duration = _first_number(stats.get("duration_sec"), topic_state.duration_sec)
    category_scores = judge.get("category_scores") if isinstance(judge.get("category_scores"), dict) else {}
    if not category_scores:
        category_scores = _category_scores_from_leaf_grades(judge.get("leaf_grades"))
    row = {
        "topic": topic_state.topic,
        "status": topic_state.status,
        "attempts": topic_state.attempts,
        "run_dir": topic_state.run_dir,
        "output_dir": topic_state.output_dir,
        "last_error": topic_state.last_error,
        "duration_sec": total_duration,
        "postprocess_duration_sec": _first_number(postprocess_duration),
        "command_duration_sec": _first_number(
            stats.get("command_duration_sec"),
            topic_state.stats.get("command_duration_sec") if isinstance(topic_state.stats, dict) else None,
        ),
        "command_durations_sec": command_durations,
        "judge_source": judge.get("judge_source", ""),
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
        "postprocess_llm_request_count": _first_number(postprocess_usage_totals.get("request_count")),
        "postprocess_llm_input_tokens": _first_number(postprocess_usage_totals.get("input_tokens")),
        "postprocess_llm_output_tokens": _first_number(postprocess_usage_totals.get("output_tokens")),
        "postprocess_llm_total_tokens": _first_number(postprocess_usage_totals.get("total_tokens")),
        **repair_stats,
    }
    if not row.get("failure_type"):
        diagnostic_signal = " ".join(
            str(row.get(key) or "")
            for key in ("failure_signal", "failure_cause", "review_blockers", "stderr_tail", "last_error", "status")
        )
        row["failure_type"] = _classify_failure_signal(diagnostic_signal)
    if not row.get("failure_type"):
        row["failure_type"] = _fallback_failure_type(row)
    return row


def _with_failed_zero_scores(row: dict[str, Any]) -> dict[str, Any]:
    if _is_number(row.get("overall_score")):
        return row
    status = str(row.get("status") or "").strip()
    if status in {"", "pending"}:
        return row
    out = dict(row)
    out["code_development"] = 0.0
    out["code_execution"] = 0.0
    out["result_analysis"] = 0.0
    out["overall_score"] = 0.0
    out["score_imputed_zero"] = True
    if not out.get("failure_type"):
        out["failure_type"] = _fallback_failure_type(out)
    return out


def _fallback_failure_type(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "").strip().lower()
    error = str(row.get("last_error") or "").strip()
    signal = str(row.get("failure_signal") or error or status)
    classified = _classify_failure_signal(signal)
    if classified:
        return classified
    if status in {"execute_failed", "run_failed", "repair_failed"}:
        return "execute_failed"
    if status in {"score_failed", "native_score_failed"}:
        return "score_failed"
    if status in {"finalize_failed", "analysis_failed"}:
        return "finalize_failed"
    if status and status not in {"completed", "pending"}:
        return status
    return ""


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


def _read_source_code_task_stats(ctx: RunnerContext, stats: dict[str, Any]) -> dict[str, Any]:
    metadata = stats.get("metadata") if isinstance(stats.get("metadata"), dict) else {}
    source_run_dir = metadata.get("source_run_dir")
    if not isinstance(source_run_dir, str) or not source_run_dir.strip():
        return {}
    return _read_json_dict(_abs(ctx.repo_root, source_run_dir) / "arc_task_stats.json")


def _source_run_dir_for_summary(ctx: RunnerContext, topic_state: TopicState, stats: dict[str, Any]) -> Path | None:
    metadata = stats.get("metadata") if isinstance(stats.get("metadata"), dict) else {}
    source_run_dir = metadata.get("source_run_dir")
    if isinstance(source_run_dir, str) and source_run_dir.strip():
        path = _abs(ctx.repo_root, source_run_dir)
        if path.exists():
            return path
    if topic_state.run_dir:
        path = _abs(ctx.repo_root, topic_state.run_dir)
        if path.exists():
            return path
    run_dir = stats.get("run_dir")
    if isinstance(run_dir, str) and run_dir.strip():
        path = _abs(ctx.repo_root, run_dir)
        if path.exists():
            return path
    return None


def _repair_stats_from_run_dir(ctx: RunnerContext, run_dir: Path) -> dict[str, Any]:
    manifest = _read_json_dict(run_dir / "manifest.json")
    repair = manifest.get("repair") if isinstance(manifest.get("repair"), dict) else {}
    repair_count = _int_or_none(repair.get("repair_count")) or 0
    review_repair_count = _int_or_none(repair.get("review_repair_count")) or 0
    run_repair_count = _int_or_none(repair.get("run_repair_count")) or 0
    execution_attempts, failed_execution_attempts, latest_failure_graph = _execution_attempt_stats(manifest)
    failure_graph_path = _resolve_failure_graph_path(run_dir, manifest, latest_failure_graph)
    failure_graph = _read_json_dict(failure_graph_path) if failure_graph_path else {}
    failure_signal = _failure_signal_from_graph(failure_graph)
    failure_type = _classify_failure_signal(failure_signal)
    diagnostics = _failure_diagnostics_from_run_dir(run_dir, manifest, failure_signal=failure_signal)
    return {
        "source_run_dir": _rel(ctx.repo_root, run_dir),
        "repair_status": repair.get("status", "") if isinstance(repair.get("status"), str) else "",
        "repair_count": repair_count,
        "review_repair_count": review_repair_count,
        "run_repair_count": run_repair_count,
        "total_repair_count": repair_count + review_repair_count + run_repair_count,
        "latest_review_repair": repair.get("latest_review_repair", "") if isinstance(repair.get("latest_review_repair"), str) else "",
        "latest_run_repair": repair.get("latest_run_repair", "") if isinstance(repair.get("latest_run_repair"), str) else "",
        "repair_memory_entries": _count_nonblank_lines(run_dir / "code_task" / "memory" / "repair_memory.jsonl"),
        "review_findings_entries": _count_nonblank_lines(run_dir / "code_task" / "memory" / "review_findings.jsonl"),
        "execution_attempts": execution_attempts,
        "failed_execution_attempts": failed_execution_attempts,
        "failure_type": failure_type,
        "failure_signal": failure_signal,
        "failure_graph": _rel(ctx.repo_root, failure_graph_path) if failure_graph_path else "",
        **diagnostics,
    }


def _failure_diagnostics_from_run_dir(run_dir: Path, manifest: dict[str, Any], *, failure_signal: str) -> dict[str, Any]:
    manifest_status = str(manifest.get("status") or "")
    benchmark = manifest.get("benchmark") if isinstance(manifest.get("benchmark"), dict) else {}
    benchmark_status = str(benchmark.get("last_status") or "")
    review_blockers = _review_blockers_from_run_dir(run_dir)
    stderr_tail = _stderr_tail_from_run_dir(run_dir)
    cause = (
        _likely_cause_from_text(_read_text_file(run_dir / "code_task" / "run" / "patched" / "failure_analysis.md"))
        or _likely_cause_from_text(_read_text_file(run_dir / "code_task" / "summary.md"))
        or failure_signal
        or stderr_tail
        or review_blockers
    )
    stage = ""
    if manifest_status == "review_failed":
        stage = "review"
    elif benchmark_status == "failed" or manifest_status == "failure_analyzed":
        stage = "run"
    elif manifest_status:
        stage = manifest_status
    return {
        "failure_stage": stage,
        "failure_cause": _compact_text(cause, limit=260) if cause else "",
        "review_blockers": _compact_text(review_blockers, limit=260) if review_blockers else "",
        "stderr_tail": _compact_text(stderr_tail, limit=260) if stderr_tail else "",
    }


def _likely_cause_from_text(text: str) -> str:
    if not text:
        return ""
    marker = "## Likely Cause"
    if marker in text:
        tail = text.split(marker, 1)[1]
        section = tail.split("\n## ", 1)[0]
        return _compact_text(section.strip(), limit=260)
    for line in text.splitlines():
        stripped = line.strip()
        if "strongest execution signal" in stripped.lower():
            return _compact_text(stripped, limit=260)
    return ""


def _review_blockers_from_run_dir(run_dir: Path) -> str:
    report = _read_json_dict(run_dir / "code_task" / "meta" / "review_report.json")
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    blockers: list[str] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        severity = str(finding.get("severity") or finding.get("level") or "").lower()
        if severity != "blocking":
            continue
        category = str(finding.get("category") or "").strip()
        summary = str(finding.get("summary") or finding.get("message") or finding.get("description") or "").strip()
        if summary:
            blockers.append(f"{category}: {summary}" if category else summary)
        if len(blockers) >= 3:
            break
    if blockers:
        return " | ".join(blockers)
    summary = _read_text_file(run_dir / "code_task" / "summary.md")
    lines = [line.strip("- ").strip() for line in summary.splitlines() if "`blocking`" in line]
    return " | ".join(lines[:3])


def _stderr_tail_from_run_dir(run_dir: Path) -> str:
    candidates = [
        run_dir / "code_task" / "run" / "patched" / "stderr.txt",
        run_dir / "code_task" / "run" / "patched" / "attempts" / "attempt-001" / "stderr.txt",
    ]
    for path in candidates:
        text = _read_text_file(path)
        if not text:
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            return _compact_text(" ".join(lines[-8:]), limit=260)
    return ""


def _execution_attempt_stats(manifest: dict[str, Any]) -> tuple[int, int, str]:
    attempts = _execution_attempts_from_manifest(manifest)
    if attempts:
        failed = sum(1 for attempt in attempts if _attempt_failed(attempt))
        latest_failure_graph = ""
        for attempt in reversed(attempts):
            graph = attempt.get("failure_graph") if isinstance(attempt, dict) else None
            if isinstance(graph, str) and graph:
                latest_failure_graph = graph
                break
        return len(attempts), failed, latest_failure_graph
    benchmark = manifest.get("benchmark") if isinstance(manifest.get("benchmark"), dict) else {}
    executed = bool(benchmark.get("executed"))
    return (1 if executed else 0), (0 if executed and benchmark.get("last_status") == "passed" else 0), ""


def _execution_attempts_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    benchmark = manifest.get("benchmark") if isinstance(manifest.get("benchmark"), dict) else {}
    attempts = _find_attempt_lists(benchmark)
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for attempt in attempts:
        key = str(attempt.get("id") or attempt.get("execution_report") or len(unique))
        if key in seen:
            continue
        seen.add(key)
        unique.append(attempt)
    return unique


def _find_attempt_lists(value: Any) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    if isinstance(value, dict):
        direct = value.get("attempts")
        if isinstance(direct, list):
            attempts.extend(row for row in direct if isinstance(row, dict))
        for child_key, child in value.items():
            if child_key == "attempts":
                continue
            attempts.extend(_find_attempt_lists(child))
    elif isinstance(value, list):
        for child in value:
            attempts.extend(_find_attempt_lists(child))
    return attempts


def _attempt_failed(attempt: dict[str, Any]) -> bool:
    status = str(attempt.get("status") or "").lower()
    if status and status != "passed":
        return True
    returncode = _int_or_none(attempt.get("returncode"))
    return returncode is not None and returncode != 0


def _resolve_failure_graph_path(run_dir: Path, manifest: dict[str, Any], latest_failure_graph: str) -> Path | None:
    candidates: list[str] = []
    if latest_failure_graph:
        candidates.append(latest_failure_graph)
    failure_analysis = manifest.get("failure_analysis") if isinstance(manifest.get("failure_analysis"), dict) else {}
    for key in ("history_failure_graph", "failure_graph"):
        value = failure_analysis.get(key)
        if isinstance(value, str) and value:
            candidates.append(value)
    layout = manifest.get("layout") if isinstance(manifest.get("layout"), dict) else {}
    value = layout.get("failure_graph")
    if isinstance(value, str) and value:
        candidates.append(value)
    candidates.append("code_task/run/patched/failure_graph.json")
    for candidate in candidates:
        path = run_dir / candidate
        if path.is_file():
            return path
    return None


def _failure_signal_from_graph(graph: dict[str, Any]) -> str:
    primary = graph.get("primary_signal")
    if isinstance(primary, str) and primary.strip():
        return _compact_text(primary)
    for key in ("runtime_signals", "validation_signals", "signal_terms"):
        value = graph.get(key)
        if isinstance(value, list):
            text = " ".join(str(item) for item in value[:12] if item)
            if text.strip():
                return _compact_text(text)
    traceback = graph.get("traceback")
    if isinstance(traceback, str) and traceback.strip():
        return _compact_text(traceback)
    return ""


def _classify_failure_signal(signal: str) -> str:
    text = signal.lower()
    if not text:
        return ""
    if "convergencewarning" in text or "warning" in text or "total no. of iterations" in text:
        return "warning/noise"
    if "has no attribute" in text or "attributeerror" in text:
        return "attribute/interface mismatch"
    if "keyerror" in text or "not found" in text or "missing" in text:
        return "key/schema mismatch"
    if "typeerror" in text or "unexpected keyword" in text or "positional argument" in text:
        return "type/interface mismatch"
    if "timeout" in text or "timed out" in text or "killed" in text or "memory" in text:
        return "timeout/resource"
    if "valueerror" in text or "invalid" in text or "cannot" in text or "failed" in text:
        return "value/schema mismatch"
    return "runtime/other"


def _count_nonblank_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
    except OSError:
        return 0


def _source_code_task_command_durations(source_stats: dict[str, Any]) -> dict[str, float]:
    if not source_stats:
        return {}
    durations = _command_durations(source_stats.get("commands"), [])
    return {
        key: value
        for key, value in durations.items()
        if key in {"init", "execute"}
    }


def _code_task_usage_from_stats(source_stats: dict[str, Any]) -> dict[str, Any]:
    if not source_stats:
        return {}
    usage = source_stats.get("llm_usage")
    if not isinstance(usage, dict):
        return {}
    sources = usage.get("sources")
    if not isinstance(sources, list):
        return {}
    summaries: list[dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, dict) or source.get("stage") != "code_task":
            continue
        summary = source.get("summary")
        if isinstance(summary, dict):
            summaries.append(_normalize_usage_summary(summary))
    return _merge_usage_totals(summaries) if summaries else {}


def _sum_duration_keys(durations: dict[str, float], keys: Sequence[str]) -> float | None:
    values = [durations.get(key) for key in keys if _is_number(durations.get(key))]
    if not values:
        return None
    return round(sum(float(value) for value in values), 3)


def _read_judge_result(ctx: RunnerContext, topic_state: TopicState, *, judge_source: str = "auto") -> dict[str, Any]:
    if not topic_state.output_dir:
        return {}
    output_dir = _abs(ctx.repo_root, topic_state.output_dir)
    candidates_by_source = {
        "native": [(output_dir / "judge_native" / "judge_result.json", "native")],
        "manual-strict": [(output_dir / "judge_manual_strict" / "judge_result.json", "manual-strict")],
        "adapter": [(output_dir / "judge" / "judge_result.json", "adapter")],
        "auto": [
            (output_dir / "judge_native" / "judge_result.json", "native"),
            (output_dir / "judge_manual_strict" / "judge_result.json", "manual-strict"),
            (output_dir / "judge" / "judge_result.json", "adapter"),
        ],
    }
    for path, source in candidates_by_source.get(judge_source, candidates_by_source["auto"]):
        data = _read_json_dict(path)
        if not data:
            continue
        data = dict(data)
        data.setdefault("judge_source", source)
        if source == "native":
            data.setdefault("scoring_profile", "native")
        elif source == "manual-strict":
            data.setdefault("scoring_profile", "manual-strict")
        return data
    return {}


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_text_file(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


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
    if "adapter.py native-score" in text:
        return "score"
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


def _category_scores_from_leaf_grades(leaf_grades: Any) -> dict[str, float]:
    if not isinstance(leaf_grades, list):
        return {}
    weighted: dict[str, dict[str, float]] = {}
    for leaf in leaf_grades:
        if not isinstance(leaf, dict):
            continue
        category = str(leaf.get("category") or leaf.get("task_category") or "").strip()
        score = _first_number(leaf.get("score"))
        if not category or score is None:
            continue
        weight = _first_number(leaf.get("weight")) or 1.0
        bucket = weighted.setdefault(category, {"weighted_sum": 0.0, "weight_sum": 0.0})
        bucket["weighted_sum"] += float(score) * float(weight)
        bucket["weight_sum"] += float(weight)
    out: dict[str, float] = {}
    for category, bucket in weighted.items():
        if bucket["weight_sum"] <= 0:
            continue
        out[category] = round(bucket["weighted_sum"] / bucket["weight_sum"], 4)
    return out


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


def _count_values(values: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        key = value.strip()
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _compact_text(value: str, *, limit: int = 220) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


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


def _write_evidence_summary(
    ctx: RunnerContext,
    evidence: dict[str, Any],
    *,
    output_prefix: str | None,
) -> tuple[Path, Path]:
    if output_prefix:
        prefix = _abs(ctx.repo_root, output_prefix)
    else:
        prefix = ctx.state_file.with_name(f"{ctx.state_file.stem}.evidence")
    json_path = prefix.parent / f"{prefix.name}.json"
    md_path = prefix.parent / f"{prefix.name}.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_evidence_summary_markdown(evidence), encoding="utf-8")
    return json_path, md_path


def render_batch_summary_markdown(summary: dict[str, Any]) -> str:
    aggregate = summary.get("aggregate") if isinstance(summary.get("aggregate"), dict) else {}
    score = aggregate.get("score_means") if isinstance(aggregate.get("score_means"), dict) else {}
    runtime = aggregate.get("runtime_means_sec") if isinstance(aggregate.get("runtime_means_sec"), dict) else {}
    usage_means = aggregate.get("llm_usage_means") if isinstance(aggregate.get("llm_usage_means"), dict) else {}
    usage_totals = aggregate.get("llm_usage_totals") if isinstance(aggregate.get("llm_usage_totals"), dict) else {}
    repair_means = aggregate.get("repair_means") if isinstance(aggregate.get("repair_means"), dict) else {}
    repair_totals = aggregate.get("repair_totals") if isinstance(aggregate.get("repair_totals"), dict) else {}
    failure_counts = aggregate.get("failure_type_counts") if isinstance(aggregate.get("failure_type_counts"), dict) else {}
    postprocess_usage_means = (
        aggregate.get("postprocess_llm_usage_means")
        if isinstance(aggregate.get("postprocess_llm_usage_means"), dict)
        else {}
    )
    postprocess_usage_totals = (
        aggregate.get("postprocess_llm_usage_totals")
        if isinstance(aggregate.get("postprocess_llm_usage_totals"), dict)
        else {}
    )
    rows = summary.get("rows") if isinstance(summary.get("rows"), list) else []
    lines = [
        "# ARC-Bench Batch Summary",
        "",
        f"- State file: `{summary.get('state_file', '')}`",
        f"- Generated at: `{summary.get('generated_at', '')}`",
        f"- Judge source: `{summary.get('judge_source', 'auto')}`",
        f"- Failed-as-zero: `{bool(summary.get('failed_as_zero'))}`",
        f"- Topics: `{aggregate.get('topic_count', 0)}` total, `{aggregate.get('completed_count', 0)}` completed, `{aggregate.get('scored_count', 0)}` scored, `{aggregate.get('failed_count', 0)}` failed",
        f"- Zero-imputed rows: `{aggregate.get('zero_imputed_count', 0)}`",
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
        "| Total Time | Postprocess | Execute | Finalize | Score | LLM Calls | Input Tokens | Output Tokens | Total Tokens | Post Tokens | Cost |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {_fmt_seconds(runtime.get('total'))} | {_fmt_seconds(runtime.get('postprocess'))} | "
            f"{_fmt_seconds(runtime.get('execute'))} | "
            f"{_fmt_seconds(runtime.get('finalize'))} | {_fmt_seconds(runtime.get('score'))} | "
            f"{_fmt_number(usage_means.get('requests'))} | {_fmt_int(usage_means.get('input_tokens'))} | "
            f"{_fmt_int(usage_means.get('output_tokens'))} | {_fmt_int(usage_means.get('total_tokens'))} | "
            f"{_fmt_int(postprocess_usage_means.get('total_tokens'))} | "
            f"{_fmt_cost(usage_means.get('estimated_cost_usd'))} |"
        ),
        "",
        "## API Totals",
        "",
        "| LLM Calls | Input Tokens | Output Tokens | Total Tokens | Postprocess Tokens | Cost |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {_fmt_int(usage_totals.get('requests'))} | {_fmt_int(usage_totals.get('input_tokens'))} | "
            f"{_fmt_int(usage_totals.get('output_tokens'))} | {_fmt_int(usage_totals.get('total_tokens'))} | "
            f"{_fmt_int(postprocess_usage_totals.get('total_tokens'))} | "
            f"{_fmt_cost(usage_totals.get('estimated_cost_usd'))} |"
        ),
        "",
        "## Repair And Execution Signals",
        "",
        "| Repair Total | Review Repair | Run Repair | Existing Repair | Exec Attempts | Failed Attempts | Repair Memory | Review Findings |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {_fmt_int(repair_totals.get('total_repair_count'))} | "
            f"{_fmt_int(repair_totals.get('review_repair_count'))} | "
            f"{_fmt_int(repair_totals.get('run_repair_count'))} | "
            f"{_fmt_int(repair_totals.get('existing_project_repair_count'))} | "
            f"{_fmt_int(repair_totals.get('execution_attempts'))} | "
            f"{_fmt_int(repair_totals.get('failed_execution_attempts'))} | "
            f"{_fmt_int(repair_totals.get('repair_memory_entries'))} | "
            f"{_fmt_int(repair_totals.get('review_findings_entries'))} |"
        ),
        "",
        "| Mean Repair | Mean Review Repair | Mean Run Repair | Mean Exec Attempts | Mean Failed Attempts | Mean Repair Memory |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {_fmt_number(repair_means.get('total_repair_count'))} | "
            f"{_fmt_number(repair_means.get('review_repair_count'))} | "
            f"{_fmt_number(repair_means.get('run_repair_count'))} | "
            f"{_fmt_number(repair_means.get('execution_attempts'))} | "
            f"{_fmt_number(repair_means.get('failed_execution_attempts'))} | "
            f"{_fmt_number(repair_means.get('repair_memory_entries'))} |"
        ),
        "",
    ]
    if failure_counts:
        lines.extend(
            [
                "## Failure Type Counts",
                "",
                "| Failure Type | Count |",
                "| --- | ---: |",
            ]
        )
        for name, count in failure_counts.items():
            lines.append(f"| {name} | {_fmt_int(count)} |")
        lines.append("")
    diagnostic_rows = [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("status") != "completed"
        and (row.get("failure_cause") or row.get("review_blockers") or row.get("stderr_tail"))
    ]
    if diagnostic_rows:
        lines.extend(
            [
                "## Failure Diagnostics",
                "",
                "| Topic | Status | Stage | Failure Type | Likely Cause | Review Blockers |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in diagnostic_rows:
            cause = row.get("failure_cause") or row.get("stderr_tail") or row.get("last_error") or ""
            lines.append(
                "| "
                f"{_md_cell(row.get('topic', ''))} | {_md_cell(row.get('status', ''))} | "
                f"{_md_cell(row.get('failure_stage', '') or '-')} | {_md_cell(row.get('failure_type', '') or '-')} | "
                f"{_md_cell(cause)} | {_md_cell(row.get('review_blockers', '') or '-')} |"
            )
        lines.append("")
    lines.extend(
        [
        "## Topic Details",
        "",
        "| Topic | Status | Judge | Profile | Code Dev | Code Exec | Result Analysis | Overall | Time | Postprocess | Repairs | Exec Attempts | Failure Type | Calls | Input | Output | Total |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in rows:
        if not isinstance(row, dict):
            continue
        lines.append(
            "| "
            f"{row.get('topic', '')} | {row.get('status', '')} | {row.get('judge_source', '') or '-'} | "
            f"{row.get('scoring_profile', '') or '-'} | "
            f"{_fmt_score(row.get('code_development'))} | {_fmt_score(row.get('code_execution'))} | "
            f"{_fmt_score(row.get('result_analysis'))} | {_fmt_score(row.get('overall_score'))} | "
            f"{_fmt_seconds(row.get('duration_sec'))} | {_fmt_seconds(row.get('postprocess_duration_sec'))} | "
            f"{_fmt_int(row.get('total_repair_count'))} | {_fmt_int(row.get('execution_attempts'))} | "
            f"{row.get('failure_type', '') or '-'} | "
            f"{_fmt_int(row.get('llm_request_count'))} | "
            f"{_fmt_int(row.get('llm_input_tokens'))} | {_fmt_int(row.get('llm_output_tokens'))} | "
            f"{_fmt_int(row.get('llm_total_tokens'))} |"
        )
    lines.append("")
    return "\n".join(lines)


def _build_evidence_summary(ctx: RunnerContext, summary: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in summary.get("rows", []) if isinstance(row, dict)]
    failure_examples: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        failure_type = row.get("failure_type")
        if not isinstance(failure_type, str) or not failure_type:
            continue
        bucket = failure_examples.setdefault(failure_type, [])
        if len(bucket) >= 5:
            continue
        bucket.append(
            {
                "topic": row.get("topic"),
                "failure_signal": row.get("failure_signal", ""),
                "failure_graph": row.get("failure_graph", ""),
                "source_run_dir": row.get("source_run_dir", ""),
            }
        )
    repair_heavy = sorted(
        rows,
        key=lambda row: (_num(row.get("total_repair_count")), _num(row.get("failed_execution_attempts"))),
        reverse=True,
    )[:10]
    high_score_low_repair = sorted(
        [
            row
            for row in rows
            if _is_number(row.get("overall_score")) and _num(row.get("total_repair_count")) <= 1
        ],
        key=lambda row: _num(row.get("overall_score")),
        reverse=True,
    )[:10]
    partial_or_failed = [
        row
        for row in rows
        if row.get("status") != "completed" or not _is_number(row.get("overall_score")) or _num(row.get("overall_score")) < 0.6
    ][:10]
    aggregate = summary.get("aggregate") if isinstance(summary.get("aggregate"), dict) else {}
    return {
        "schema_version": "simple_ar_arc_fidelity_evidence.v1",
        "generated_at": _now(),
        "state_file": summary.get("state_file", _rel(ctx.repo_root, ctx.state_file)),
        "judge_source": summary.get("judge_source", "auto"),
        "topic_count": len(rows),
        "repair_totals": aggregate.get("repair_totals", {}),
        "repair_means": aggregate.get("repair_means", {}),
        "failure_type_counts": aggregate.get("failure_type_counts", {}),
        "failure_examples": failure_examples,
        "trace_candidates": {
            "high_score_low_repair": [_evidence_topic_row(row) for row in high_score_low_repair],
            "repair_heavy": [_evidence_topic_row(row) for row in repair_heavy],
            "partial_or_failed": [_evidence_topic_row(row) for row in partial_or_failed],
        },
        "rows": [_evidence_topic_row(row) for row in rows],
    }


def _evidence_topic_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "topic": row.get("topic"),
        "status": row.get("status"),
        "overall_score": row.get("overall_score"),
        "code_development": row.get("code_development"),
        "code_execution": row.get("code_execution"),
        "result_analysis": row.get("result_analysis"),
        "total_repair_count": row.get("total_repair_count"),
        "review_repair_count": row.get("review_repair_count"),
        "run_repair_count": row.get("run_repair_count"),
        "execution_attempts": row.get("execution_attempts"),
        "failed_execution_attempts": row.get("failed_execution_attempts"),
        "repair_memory_entries": row.get("repair_memory_entries"),
        "failure_type": row.get("failure_type", ""),
        "failure_signal": row.get("failure_signal", ""),
        "source_run_dir": row.get("source_run_dir", ""),
        "output_dir": row.get("output_dir", ""),
    }


def render_evidence_summary_markdown(evidence: dict[str, Any]) -> str:
    repair_totals = evidence.get("repair_totals") if isinstance(evidence.get("repair_totals"), dict) else {}
    failure_counts = evidence.get("failure_type_counts") if isinstance(evidence.get("failure_type_counts"), dict) else {}
    trace_candidates = evidence.get("trace_candidates") if isinstance(evidence.get("trace_candidates"), dict) else {}
    lines = [
        "# ARC-Bench Fidelity Evidence Summary",
        "",
        f"- State file: `{evidence.get('state_file', '')}`",
        f"- Generated at: `{evidence.get('generated_at', '')}`",
        f"- Judge source: `{evidence.get('judge_source', 'auto')}`",
        f"- Topics: `{evidence.get('topic_count', 0)}`",
        "",
        "## Repair Totals",
        "",
        "| Total Repair | Review Repair | Run Repair | Existing Repair | Exec Attempts | Failed Attempts | Repair Memory |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {_fmt_int(repair_totals.get('total_repair_count'))} | "
            f"{_fmt_int(repair_totals.get('review_repair_count'))} | "
            f"{_fmt_int(repair_totals.get('run_repair_count'))} | "
            f"{_fmt_int(repair_totals.get('existing_project_repair_count'))} | "
            f"{_fmt_int(repair_totals.get('execution_attempts'))} | "
            f"{_fmt_int(repair_totals.get('failed_execution_attempts'))} | "
            f"{_fmt_int(repair_totals.get('repair_memory_entries'))} |"
        ),
        "",
    ]
    if failure_counts:
        lines.extend(["## Failure Taxonomy", "", "| Failure Type | Count |", "| --- | ---: |"])
        for name, count in failure_counts.items():
            lines.append(f"| {name} | {_fmt_int(count)} |")
        lines.append("")
    for key, title in (
        ("repair_heavy", "Repair-Heavy Trace Candidates"),
        ("high_score_low_repair", "High-Score Low-Repair Trace Candidates"),
        ("partial_or_failed", "Partial/Failed Trace Candidates"),
    ):
        rows = trace_candidates.get(key) if isinstance(trace_candidates.get(key), list) else []
        if not rows:
            continue
        lines.extend(["## " + title, "", "| Topic | Overall | Repairs | Attempts | Failure Type | Source Run |", "| --- | ---: | ---: | ---: | --- | --- |"])
        for row in rows:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"| {row.get('topic', '')} | {_fmt_score(row.get('overall_score'))} | "
                f"{_fmt_int(row.get('total_repair_count'))} | {_fmt_int(row.get('execution_attempts'))} | "
                f"{row.get('failure_type', '') or '-'} | `{row.get('source_run_dir', '')}` |"
            )
        lines.append("")
    failure_examples = evidence.get("failure_examples")
    if isinstance(failure_examples, dict) and failure_examples:
        lines.extend(["## Failure Examples", ""])
        for failure_type, examples in failure_examples.items():
            lines.extend([f"### {failure_type}", "", "| Topic | Signal | Failure Graph |", "| --- | --- | --- |"])
            for example in examples if isinstance(examples, list) else []:
                if not isinstance(example, dict):
                    continue
                lines.append(
                    f"| {example.get('topic', '')} | {example.get('failure_signal', '') or '-'} | "
                    f"`{example.get('failure_graph', '')}` |"
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


def _md_cell(value: Any) -> str:
    text = _compact_text(str(value or ""), limit=220)
    return text.replace("|", "\\|").replace("\n", " ").strip() or "-"


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
