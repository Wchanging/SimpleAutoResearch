from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import Sequence

from simple_ar.artifacts import read_json, read_text
from simple_ar.pipeline import Context, PipelineRunner
from simple_ar.reporting import ConsoleReporter
from simple_ar.stage_handlers import HANDLERS
from simple_ar.stages import Stage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="simple-ar")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Start a new research run.")
    run_parser.add_argument("--topic", required=True)
    run_parser.add_argument("--output-root", default="runs")
    run_parser.add_argument("--from-stage", default="plan")
    run_parser.add_argument("--to-stage", default="report")
    run_parser.add_argument("--model", default=None)
    run_parser.add_argument("--llm-workers", type=int, default=4)
    run_parser.add_argument("--max-papers", type=int, default=5)
    run_parser.add_argument("--search-query", default=None)
    run_parser.add_argument("--no-llm", action="store_true")
    run_parser.add_argument("--offline-search", action="store_true")
    run_parser.add_argument("--strict-search", action="store_true")
    run_parser.add_argument("--quiet", action="store_true")

    resume_parser = subparsers.add_parser("resume", help="Resume an existing run.")
    resume_parser.add_argument("run_dir")
    resume_parser.add_argument("--from-stage", default=None)
    resume_parser.add_argument("--to-stage", default="report")
    resume_parser.add_argument("--model", default=None)
    resume_parser.add_argument("--llm-workers", type=int, default=4)
    resume_parser.add_argument("--max-papers", type=int, default=5)
    resume_parser.add_argument("--search-query", default=None)
    resume_parser.add_argument("--no-llm", action="store_true")
    resume_parser.add_argument("--offline-search", action="store_true")
    resume_parser.add_argument("--strict-search", action="store_true")
    resume_parser.add_argument("--quiet", action="store_true")

    status_parser = subparsers.add_parser("status", help="Show run status.")
    status_parser.add_argument("run_dir")

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        run_dir = _new_run_dir(Path(args.output_root), args.topic)
        reporter = ConsoleReporter(enabled=not args.quiet)
        ctx = Context(
            run_dir=run_dir,
            topic=args.topic,
            config={
                "from_stage": args.from_stage,
                "to_stage": args.to_stage,
                "mode": "offline" if args.no_llm else "llm",
                "model": args.model,
                "llm_max_workers": args.llm_workers,
                "max_papers": args.max_papers,
                "search_query": args.search_query,
                "use_llm": not args.no_llm,
                "use_arxiv": not args.offline_search,
                "strict_search": args.strict_search,
            },
        )
        executions = PipelineRunner(_stage_handlers(), reporter=reporter).run(
            ctx,
            from_stage=args.from_stage,
            to_stage=args.to_stage,
        )
        if args.quiet:
            print(f"Run directory: {run_dir}")
        print(f"Stages completed: {len(executions)}")
        return

    if args.command == "resume":
        run_dir = Path(args.run_dir)
        topic = _read_topic(run_dir)
        from_stage = args.from_stage or _next_stage_from_state(run_dir) or "plan"
        reporter = ConsoleReporter(enabled=not args.quiet)
        ctx = Context(
            run_dir=run_dir,
            topic=topic,
            config={
                "from_stage": from_stage,
                "to_stage": args.to_stage,
                "mode": "offline" if args.no_llm else "llm",
                "model": args.model,
                "llm_max_workers": args.llm_workers,
                "max_papers": args.max_papers,
                "search_query": args.search_query,
                "use_llm": not args.no_llm,
                "use_arxiv": not args.offline_search,
                "strict_search": args.strict_search,
            },
        )
        executions = PipelineRunner(_stage_handlers(), reporter=reporter).run(
            ctx,
            from_stage=from_stage,
            to_stage=args.to_stage,
        )
        if args.quiet:
            print(f"Run directory: {run_dir}")
        print(f"Resumed from: {from_stage}")
        print(f"Stages completed: {len(executions)}")
        return

    if args.command == "status":
        _print_status(Path(args.run_dir))
        return

    parser.error(f"Unknown command: {args.command}")


def _stage_handlers():
    """Return the mapping of stages to their respective handler functions."""
    return {Stage(number): handler for number, handler in HANDLERS.items()}


def _new_run_dir(output_root: Path, topic: str) -> Path:
    """Generate a unique timestamped directory path for a new run."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = _slugify(topic)
    return output_root / f"{timestamp}-{slug}"


def _slugify(text: str) -> str:
    """Convert text into a URL and folder-friendly slug string."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return slug[:50] or "research"


def _read_topic(run_dir: Path) -> str:
    """Read the original research topic from the run directory."""
    topic_path = run_dir / "topic.txt"
    if not topic_path.exists():
        raise SystemExit(f"Missing topic.txt in {run_dir}")
    return read_text(topic_path).strip()


def _next_stage_from_state(run_dir: Path) -> str | None:
    """Read the pipeline_state.json to determine which stage needs to run next."""
    state_path = run_dir / "pipeline_state.json"
    if not state_path.exists():
        return None
    data = read_json(state_path)
    value = data.get("next_stage")
    return str(value) if value else None


def _print_status(run_dir: Path) -> None:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"Missing manifest.json in {run_dir}")
    manifest = read_json(manifest_path)
    print(f"Run: {run_dir}")
    print(f"Topic: {manifest.get('topic', '')}")
    for item in manifest.get("stages", []):
        marker = "done" if item.get("completed") else "pending"
        print(f"- {item['stage_number']:02d} {item['stage']}: {marker}")


if __name__ == "__main__":
    main()
