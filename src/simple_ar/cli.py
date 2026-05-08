from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import Sequence

from simple_ar.artifacts import read_json, read_text
from simple_ar.pipeline import Context, PipelineRunner
from simple_ar.reporting import ConsoleReporter
from simple_ar.retrieval.index import build_artifact_index
from simple_ar.retrieval.search import search_artifacts
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
    run_parser.add_argument("--experiment-template", default="toy_text_classification")
    run_parser.add_argument("--experiment-timeout", type=int, default=30)
    run_parser.add_argument("--no-llm", action="store_true")
    run_parser.add_argument("--offline-search", action="store_true")
    run_parser.add_argument("--allow-fixture-fallback", action="store_true")
    run_parser.add_argument("--strict-search", action="store_true")
    run_parser.add_argument("--no-retrieval", action="store_true")
    run_parser.add_argument("--retrieval-top-k", type=int, default=4)
    run_parser.add_argument("--quiet", action="store_true")

    resume_parser = subparsers.add_parser("resume", help="Resume an existing run.")
    resume_parser.add_argument("run_dir")
    resume_parser.add_argument("--from-stage", default=None)
    resume_parser.add_argument("--to-stage", default="report")
    resume_parser.add_argument("--model", default=None)
    resume_parser.add_argument("--llm-workers", type=int, default=4)
    resume_parser.add_argument("--max-papers", type=int, default=5)
    resume_parser.add_argument("--search-query", default=None)
    resume_parser.add_argument("--experiment-template", default="toy_text_classification")
    resume_parser.add_argument("--experiment-timeout", type=int, default=30)
    resume_parser.add_argument("--no-llm", action="store_true")
    resume_parser.add_argument("--offline-search", action="store_true")
    resume_parser.add_argument("--allow-fixture-fallback", action="store_true")
    resume_parser.add_argument("--strict-search", action="store_true")
    resume_parser.add_argument("--no-retrieval", action="store_true")
    resume_parser.add_argument("--retrieval-top-k", type=int, default=4)
    resume_parser.add_argument("--quiet", action="store_true")

    status_parser = subparsers.add_parser("status", help="Show run status.")
    status_parser.add_argument("run_dir")

    inspect_parser = subparsers.add_parser("inspect", help="Index and summarize run artifacts.")
    inspect_parser.add_argument("run_dir")

    search_parser = subparsers.add_parser(
        "search-artifacts",
        help="Search indexed run artifacts with lexical retrieval.",
    )
    search_parser.add_argument("run_dir")
    search_parser.add_argument("query")
    search_parser.add_argument("--top-k", type=int, default=8)
    search_parser.add_argument(
        "--include-operational",
        action="store_true",
        help="Also search runner metadata such as manifests and stage_meta.json.",
    )

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
                "experiment_template": args.experiment_template,
                "experiment_timeout_sec": args.experiment_timeout,
                "use_llm": not args.no_llm,
                "use_arxiv": not args.offline_search,
                "allow_fixture_fallback": args.allow_fixture_fallback,
                "strict_search": args.strict_search,
                "use_retrieval": not args.no_retrieval,
                "retrieval_top_k": args.retrieval_top_k,
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
                "experiment_template": args.experiment_template,
                "experiment_timeout_sec": args.experiment_timeout,
                "use_llm": not args.no_llm,
                "use_arxiv": not args.offline_search,
                "allow_fixture_fallback": args.allow_fixture_fallback,
                "strict_search": args.strict_search,
                "use_retrieval": not args.no_retrieval,
                "retrieval_top_k": args.retrieval_top_k,
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

    if args.command == "inspect":
        _print_inspect(Path(args.run_dir))
        return

    if args.command == "search-artifacts":
        _print_artifact_search(
            Path(args.run_dir),
            args.query,
            top_k=args.top_k,
            include_operational=args.include_operational,
        )
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
    state_path = run_dir / "pipeline_state.json"
    state = read_json(state_path) if state_path.exists() else {}

    print(f"Run: {run_dir}")
    print(f"Topic: {manifest.get('topic', '')}")
    if state:
        print(
            "Pipeline: "
            f"{state.get('status', 'unknown')} "
            f"(last={state.get('last_stage', 'none')}, next={state.get('next_stage', 'none')})"
        )

    print("Stages:")
    for item in manifest.get("stages", []):
        marker = _stage_status(item)
        outputs = item.get("outputs", [])
        output_text = ", ".join(str(name) for name in outputs) if isinstance(outputs, list) else ""
        suffix = f" -> {output_text}" if output_text else ""
        print(f"- {item['stage_number']:02d} {item['stage']}: {marker}{suffix}")

    report_dir = run_dir / "08-report"
    report_path = report_dir / "report.md"
    report_manifest_path = report_dir / "manifest.json"
    if report_path.exists() or report_manifest_path.exists():
        print("Report:")
        if report_path.exists():
            print(f"- report.md: {report_path}")
        if report_manifest_path.exists():
            print(f"- manifest.json: {report_manifest_path}")


def _print_inspect(run_dir: Path) -> None:
    """Build an artifact index and print a compact run summary."""
    index = build_artifact_index(run_dir)
    artifacts = _artifact_rows(index)
    print(f"Run: {run_dir}")
    print(f"Artifacts: {len(artifacts)}")
    print(f"Index: {run_dir / 'artifact_index.json'}")

    by_kind = _count_by(artifacts, "kind")
    if by_kind:
        print("Kinds:")
        for name, count in by_kind.items():
            print(f"- {name}: {count}")

    by_stage = _count_by(artifacts, "stage")
    if by_stage:
        print("Stages:")
        for name, count in by_stage.items():
            print(f"- {name}: {count}")

    if artifacts:
        print("Largest artifacts:")
        for artifact in sorted(artifacts, key=lambda item: int(item.get("bytes", 0)), reverse=True)[:5]:
            size = _format_bytes(int(artifact.get("bytes", 0)))
            print(
                f"- {artifact.get('path', '')} "
                f"({artifact.get('kind', 'unknown')}, {size})"
            )


def _print_artifact_search(
    run_dir: Path,
    query: str,
    *,
    top_k: int,
    include_operational: bool = False,
) -> None:
    """Search run artifacts and print top snippets with source provenance."""
    results = search_artifacts(
        run_dir,
        query,
        top_k=top_k,
        include_operational=include_operational,
    )
    matches = results.get("matches", [])
    print(f"Run: {run_dir}")
    print(f"Query: {query}")
    print(f"Chunks searched: {results.get('chunk_count', 0)}")
    print(f"Matches: {len(matches)}")
    print(f"Operational metadata included: {include_operational}")
    print(f"Results: {run_dir / 'artifact_search_results.json'}")
    for match in matches:
        path = match.get("path", "")
        line_start = match.get("line_start", "")
        line_end = match.get("line_end", "")
        score = match.get("score", "")
        snippet = str(match.get("snippet", "")).strip()
        print(f"- {path}:{line_start}-{line_end} score={score}")
        if snippet:
            print(f"  {snippet}")


def _artifact_rows(index: dict[str, object]) -> list[dict[str, object]]:
    rows = index.get("artifacts", [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _count_by(rows: list[dict[str, object]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(field)
        key = str(value) if value else "root"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def _format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value / (1024 * 1024):.1f} MiB"


def _stage_status(item: dict[str, object]) -> str:
    """Return a readable stage status for old and new manifests."""
    value = item.get("status")
    if isinstance(value, str) and value:
        return value
    return "done" if item.get("completed") else "pending"


if __name__ == "__main__":
    main()
