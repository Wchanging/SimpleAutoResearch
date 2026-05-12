from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import Sequence

from simple_ar.artifacts import read_json, read_text
from simple_ar.code_task import (
    analyze_code_task_failure,
    apply_patch_edits,
    generate_patch_plan,
    initialize_code_task,
    propose_patch_edits,
    propose_repair_edits,
    record_plan_decision,
    run_code_task_benchmark,
    validate_code_task,
)
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
    resume_parser.add_argument("--llm-workers", type=int, default=None)
    resume_parser.add_argument("--max-papers", type=int, default=None)
    resume_parser.add_argument("--search-query", default=None)
    resume_parser.add_argument("--experiment-template", default=None)
    resume_parser.add_argument("--experiment-timeout", type=int, default=None)
    resume_parser.add_argument("--no-llm", action="store_true")
    resume_parser.add_argument("--offline-search", action="store_true")
    resume_parser.add_argument("--allow-fixture-fallback", action="store_true")
    resume_parser.add_argument("--strict-search", action="store_true")
    resume_parser.add_argument("--no-retrieval", action="store_true")
    resume_parser.add_argument("--retrieval-top-k", type=int, default=None)
    resume_parser.add_argument("--quiet", action="store_true")

    status_parser = subparsers.add_parser("status", help="Show run status.")
    status_parser.add_argument("run_dir")

    code_task_parser = subparsers.add_parser(
        "code-task",
        help="Work with an existing codebase in an isolated run workspace.",
    )
    code_task_subparsers = code_task_parser.add_subparsers(
        dest="code_task_command",
        required=True,
    )
    code_task_init = code_task_subparsers.add_parser(
        "init",
        help="Copy a codebase into a code-task workspace and build a code index.",
    )
    code_task_init.add_argument("--code-root", required=True)
    code_task_init.add_argument("--task-file", required=True)
    code_task_init.add_argument("--output-root", default="runs")
    code_task_init.add_argument("--name", default=None)
    code_task_init.add_argument("--benchmark-command", default=None)
    code_task_init.add_argument(
        "--max-file-bytes",
        type=int,
        default=2_000_000,
        help="Maximum file size copied into the workspace. Use 0 to disable.",
    )
    code_task_plan = code_task_subparsers.add_parser(
        "plan",
        help="Generate a human-reviewable patch plan for a code-task run.",
    )
    code_task_plan.add_argument("run_dir")
    code_task_plan.add_argument("--model", default=None)
    code_task_plan.add_argument("--no-llm", action="store_true")
    code_task_plan.add_argument("--force", action="store_true")
    code_task_plan.add_argument("--max-files", type=int, default=8)
    code_task_plan.add_argument("--max-source-chars-per-file", type=int, default=2500)

    code_task_decide = code_task_subparsers.add_parser(
        "decide-plan",
        help="Record a human decision for the current code-task patch plan.",
    )
    code_task_decide.add_argument("run_dir")
    code_task_decide.add_argument(
        "--decision",
        required=True,
        choices=("approve", "reject", "revise"),
    )
    code_task_decide.add_argument("--note", default="")
    code_task_decide.add_argument("--reviewer", default="user")

    code_task_propose = code_task_subparsers.add_parser(
        "propose-edits",
        help="Ask the model to propose controlled old/new text edits.",
    )
    code_task_propose.add_argument("run_dir")
    code_task_propose.add_argument("--model", default=None)
    code_task_propose.add_argument("--no-llm", action="store_true")
    code_task_propose.add_argument("--force", action="store_true")
    code_task_propose.add_argument("--max-files", type=int, default=8)
    code_task_propose.add_argument("--max-source-chars-per-file", type=int, default=4000)

    code_task_apply = code_task_subparsers.add_parser(
        "apply-edits",
        help="Safely apply controlled old/new text edits to the workspace.",
    )
    code_task_apply.add_argument("run_dir")
    code_task_apply.add_argument("--edits-file", default=None)
    code_task_apply.add_argument(
        "--allow-unapproved-plan",
        action="store_true",
        help="Bypass the human approval gate. Intended only for local experiments/tests.",
    )

    code_task_validate = code_task_subparsers.add_parser(
        "validate",
        help="Run lightweight static validation over the code-task workspace.",
    )
    code_task_validate.add_argument("run_dir")
    code_task_validate.add_argument("--strict", action="store_true")
    code_task_validate.add_argument("--max-file-bytes", type=int, default=500_000)

    code_task_run = code_task_subparsers.add_parser(
        "run",
        help="Run the recorded benchmark command in the code-task workspace.",
    )
    code_task_run.add_argument("run_dir")
    code_task_run.add_argument("--command", dest="benchmark_command", default=None)
    code_task_run.add_argument("--timeout", type=int, default=60)
    code_task_run.add_argument("--skip-validation", action="store_true")

    code_task_analyze = code_task_subparsers.add_parser(
        "analyze-failure",
        help="Write a deterministic failure analysis from the latest benchmark run.",
    )
    code_task_analyze.add_argument("run_dir")

    code_task_repair = code_task_subparsers.add_parser(
        "repair",
        help="Propose bounded repair edits from the latest failure analysis.",
    )
    code_task_repair.add_argument("run_dir")
    code_task_repair.add_argument("--model", default=None)
    code_task_repair.add_argument("--no-llm", action="store_true")
    code_task_repair.add_argument("--max-files", type=int, default=8)
    code_task_repair.add_argument("--max-source-chars-per-file", type=int, default=4000)

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
            config=_resume_config(run_dir, args, from_stage),
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

    if args.command == "code-task":
        if args.code_task_command == "init":
            _print_code_task_init(args)
            return
        if args.code_task_command == "plan":
            _print_code_task_plan(args)
            return
        if args.code_task_command == "decide-plan":
            _print_code_task_decision(args)
            return
        if args.code_task_command == "propose-edits":
            _print_code_task_propose_edits(args)
            return
        if args.code_task_command == "apply-edits":
            _print_code_task_apply_edits(args)
            return
        if args.code_task_command == "validate":
            _print_code_task_validate(args)
            return
        if args.code_task_command == "run":
            _print_code_task_run(args)
            return
        if args.code_task_command == "analyze-failure":
            _print_code_task_analyze_failure(args)
            return
        if args.code_task_command == "repair":
            _print_code_task_repair(args)
            return
        parser.error(f"Unknown code-task command: {args.code_task_command}")

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


def _resume_config(run_dir: Path, args: argparse.Namespace, from_stage: str) -> dict[str, object]:
    """Merge resume-time overrides into the original run configuration.

    Resuming a run should not silently replace the original template, timeout,
    retrieval, or search settings with parser defaults. Only explicitly supplied
    resume flags should override the saved ``config_snapshot.json``.
    """
    config = _base_resume_config(run_dir)
    config["from_stage"] = from_stage
    config["to_stage"] = args.to_stage

    _set_if_not_none(config, "model", args.model)
    _set_if_not_none(config, "llm_max_workers", args.llm_workers)
    _set_if_not_none(config, "max_papers", args.max_papers)
    _set_if_not_none(config, "search_query", args.search_query)
    _set_if_not_none(config, "experiment_template", args.experiment_template)
    _set_if_not_none(config, "experiment_timeout_sec", args.experiment_timeout)
    _set_if_not_none(config, "retrieval_top_k", args.retrieval_top_k)

    if args.no_llm:
        config["use_llm"] = False
        config["mode"] = "offline"
    else:
        config["use_llm"] = bool(config.get("use_llm", True))
        config["mode"] = "llm" if config["use_llm"] else "offline"
    if args.offline_search:
        config["use_arxiv"] = False
    else:
        config["use_arxiv"] = bool(config.get("use_arxiv", True))
    if args.allow_fixture_fallback:
        config["allow_fixture_fallback"] = True
    else:
        config["allow_fixture_fallback"] = bool(config.get("allow_fixture_fallback", False))
    if args.strict_search:
        config["strict_search"] = True
    else:
        config["strict_search"] = bool(config.get("strict_search", False))
    if args.no_retrieval:
        config["use_retrieval"] = False
    else:
        config["use_retrieval"] = bool(config.get("use_retrieval", True))
    return config


def _base_resume_config(run_dir: Path) -> dict[str, object]:
    config_path = run_dir / "config_snapshot.json"
    if config_path.exists():
        data = read_json(config_path)
        if isinstance(data, dict):
            return dict(data)
    return {
        "mode": "llm",
        "model": None,
        "llm_max_workers": 4,
        "max_papers": 5,
        "search_query": None,
        "experiment_template": "toy_text_classification",
        "experiment_timeout_sec": 30,
        "use_llm": True,
        "use_arxiv": True,
        "allow_fixture_fallback": False,
        "strict_search": False,
        "use_retrieval": True,
        "retrieval_top_k": 4,
    }


def _set_if_not_none(data: dict[str, object], key: str, value: object) -> None:
    if value is not None:
        data[key] = value


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
    if manifest.get("workflow") == "code_task":
        _print_code_task_status(run_dir, manifest)
        return

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


def _print_code_task_status(run_dir: Path, manifest: dict[str, object]) -> None:
    """Print status for a code-task workflow manifest."""
    print(f"Run: {run_dir}")
    print(f"Workflow: {manifest.get('workflow', 'code_task')}")
    print(f"Status: {manifest.get('status', 'unknown')}")

    layout = manifest.get("layout", {})
    if isinstance(layout, dict):
        print("Layout:")
        for key in ("task", "workspace", "meta", "codebase_index"):
            value = layout.get(key)
            if value:
                print(f"- {key}: {run_dir / str(value)}")

    codebase = manifest.get("codebase", {})
    if isinstance(codebase, dict):
        print("Codebase:")
        print(f"- files: {codebase.get('file_count', 0)}")
        print(f"- python files: {codebase.get('python_file_count', 0)}")
        print(f"- test files: {codebase.get('test_file_count', 0)}")

    plan = manifest.get("plan", {})
    if isinstance(plan, dict) and plan:
        print("Plan:")
        print(f"- status: {plan.get('status', 'unknown')}")
        print(f"- mode: {plan.get('mode', 'unknown')}")
        if plan.get("patch_plan"):
            print(f"- patch plan: {run_dir / str(plan.get('patch_plan'))}")

    patch = manifest.get("patch", {})
    if isinstance(patch, dict) and patch:
        print("Patch:")
        print(f"- status: {patch.get('status', 'unknown')}")
        if patch.get("proposed_edits"):
            print(f"- proposed edits: {run_dir / str(patch.get('proposed_edits'))}")
        if patch.get("patch_diff"):
            print(f"- patch diff: {run_dir / str(patch.get('patch_diff'))}")
        changed_files = patch.get("changed_files")
        if isinstance(changed_files, list) and changed_files:
            print(f"- changed files: {', '.join(str(path) for path in changed_files)}")

    validation = manifest.get("validation", {})
    if isinstance(validation, dict) and validation:
        print("Validation:")
        print(f"- status: {validation.get('status', 'unknown')}")
        print(f"- errors: {validation.get('error_count', 0)}")
        print(f"- warnings: {validation.get('warning_count', 0)}")
        if validation.get("report"):
            print(f"- report: {run_dir / str(validation.get('report'))}")

    benchmark = manifest.get("benchmark", {})
    if isinstance(benchmark, dict) and benchmark.get("command"):
        print("Benchmark:")
        print(f"- command: {benchmark.get('command')}")
        print(f"- executed: {benchmark.get('executed', False)}")
        if benchmark.get("last_status"):
            print(f"- last status: {benchmark.get('last_status')}")
        if benchmark.get("execution_report"):
            print(f"- execution report: {run_dir / str(benchmark.get('execution_report'))}")

    failure = manifest.get("failure_analysis", {})
    if isinstance(failure, dict) and failure:
        print("Failure Analysis:")
        print(f"- status: {failure.get('status', 'unknown')}")
        if failure.get("analysis"):
            print(f"- analysis: {run_dir / str(failure.get('analysis'))}")

    repair = manifest.get("repair", {})
    if isinstance(repair, dict) and repair:
        print("Repair:")
        print(f"- status: {repair.get('status', 'unknown')}")
        print(f"- attempts: {repair.get('repair_count', 0)}")
        if repair.get("latest_proposed_edits"):
            print(f"- latest proposal: {run_dir / str(repair.get('latest_proposed_edits'))}")


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


def _print_code_task_init(args: argparse.Namespace) -> None:
    """Initialize a code-task run and print the resulting workspace summary."""
    code_root = Path(args.code_root)
    task_file = Path(args.task_file)
    name = args.name or f"code-task-{code_root.resolve().name}"
    run_dir = _new_run_dir(Path(args.output_root), name)
    result = initialize_code_task(
        run_dir=run_dir,
        code_root=code_root,
        task_file=task_file,
        benchmark_command=args.benchmark_command,
        max_file_bytes=args.max_file_bytes,
    )
    project = result.codebase_index.get("project", {})
    print(f"Code task run: {result.run_dir}")
    print(f"Workspace: {result.workspace_dir}")
    print(f"Task: {result.task_dir / 'task.md'}")
    print(f"Index: {result.codebase_index_path}")
    print(
        "Files copied: "
        f"{result.copy_report.files_copied} "
        f"({result.copy_report.skipped_count} skipped)"
    )
    print(
        "Indexed: "
        f"{project.get('file_count', 0)} file(s), "
        f"{project.get('python_file_count', 0)} Python file(s), "
        f"{project.get('test_file_count', 0)} test file(s)"
    )
    if args.benchmark_command:
        print(f"Benchmark command recorded: {args.benchmark_command}")


def _print_code_task_plan(args: argparse.Namespace) -> None:
    """Generate a code-task patch plan and print a compact summary."""
    result = generate_patch_plan(
        Path(args.run_dir),
        model=args.model,
        use_llm=not args.no_llm,
        force=args.force,
        max_files=args.max_files,
        max_source_chars_per_file=args.max_source_chars_per_file,
        message_callback=lambda message: print(f"  - {message}"),
    )
    print(f"Code task run: {result.run_dir}")
    print(f"Patch plan: {result.patch_plan_path}")
    print(f"Mode: {result.mode}")
    print(f"Pending approval: {result.pending_approval}")
    print(f"Context files: {len(result.selected_files)}")
    for path in result.selected_files:
        print(f"- {path}")


def _print_code_task_decision(args: argparse.Namespace) -> None:
    """Record and print the human decision for a patch plan."""
    row = record_plan_decision(
        Path(args.run_dir),
        decision=args.decision,
        note=args.note,
        reviewer=args.reviewer,
    )
    print(f"Code task run: {args.run_dir}")
    print(f"Decision: {row['decision']}")
    print("Decision log: code_task/meta/hitl_decisions.jsonl")


def _print_code_task_propose_edits(args: argparse.Namespace) -> None:
    """Generate controlled edits and print a compact proposal summary."""
    result = propose_patch_edits(
        Path(args.run_dir),
        model=args.model,
        use_llm=not args.no_llm,
        force=args.force,
        max_files=args.max_files,
        max_source_chars_per_file=args.max_source_chars_per_file,
        message_callback=lambda message: print(f"  - {message}"),
    )
    print(f"Code task run: {result.run_dir}")
    print(f"Proposed edits: {result.proposal_path}")
    print(f"Mode: {result.mode}")
    print(f"Edit count: {result.edit_count}")
    print(f"Context files: {len(result.selected_files)}")
    for path in result.selected_files:
        print(f"- {path}")


def _print_code_task_apply_edits(args: argparse.Namespace) -> None:
    """Safely apply controlled edits and print changed files."""
    result = apply_patch_edits(
        Path(args.run_dir),
        edits_file=Path(args.edits_file) if args.edits_file else None,
        allow_unapproved_plan=args.allow_unapproved_plan,
    )
    print(f"Code task run: {result.run_dir}")
    print(f"Patch diff: {result.patch_diff_path}")
    print(f"Applied edits: {result.applied_edits_path}")
    print(f"Changed files: {len(result.changed_files)}")
    for path in result.changed_files:
        print(f"- {path}")


def _print_code_task_validate(args: argparse.Namespace) -> None:
    """Run static validation and print a compact issue summary."""
    result = validate_code_task(
        Path(args.run_dir),
        strict=args.strict,
        max_file_bytes=args.max_file_bytes,
    )
    print(f"Code task run: {result.run_dir}")
    print(f"Validation report: {result.report_path}")
    print(f"Status: {result.status}")
    print(f"Errors: {result.error_count}")
    print(f"Warnings: {result.warning_count}")


def _print_code_task_run(args: argparse.Namespace) -> None:
    """Run a code-task benchmark and print execution artifacts."""
    result = run_code_task_benchmark(
        Path(args.run_dir),
        command=args.benchmark_command,
        timeout_sec=args.timeout,
        skip_validation=args.skip_validation,
    )
    print(f"Code task run: {result.run_dir}")
    print(f"Execution report: {result.report_path}")
    print(f"Status: {result.status}")
    print(f"Return code: {result.returncode}")
    print(f"Timed out: {result.timed_out}")
    print(f"Stdout: {result.stdout_path}")
    print(f"Stderr: {result.stderr_path}")
    if result.metrics:
        print("Metrics:")
        for key, value in result.metrics.items():
            print(f"- {key}: {value}")


def _print_code_task_analyze_failure(args: argparse.Namespace) -> None:
    """Write failure analysis and print the implicated files."""
    result = analyze_code_task_failure(Path(args.run_dir))
    print(f"Code task run: {result.run_dir}")
    print(f"Failure analysis: {result.analysis_path}")
    print(f"Status: {result.status}")
    print(f"Implicated files: {len(result.implicated_files)}")
    for path in result.implicated_files:
        print(f"- {path}")


def _print_code_task_repair(args: argparse.Namespace) -> None:
    """Generate a bounded repair proposal and print the review path."""
    result = propose_repair_edits(
        Path(args.run_dir),
        model=args.model,
        use_llm=not args.no_llm,
        max_files=args.max_files,
        max_source_chars_per_file=args.max_source_chars_per_file,
        message_callback=lambda message: print(f"  - {message}"),
    )
    print(f"Code task run: {result.run_dir}")
    print(f"Repair directory: {result.repair_dir}")
    print(f"Proposed edits: {result.proposal_path}")
    print(f"Mode: {result.mode}")
    print(f"Edit count: {result.edit_count}")
    print(f"Context files: {len(result.selected_files)}")
    for path in result.selected_files:
        print(f"- {path}")


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
