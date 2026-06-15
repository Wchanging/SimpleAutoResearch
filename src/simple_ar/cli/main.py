from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence

from simple_ar.core.artifacts import read_json, read_text
from simple_ar.cli.code_task_view import (
    confirm_next_step,
    confirm_review_gate,
    render_execute_header,
    render_execute_message,
    render_execute_result,
    render_init_result,
    render_review_gate,
    render_step_preview,
)
from simple_ar.code_task import (
    analyze_code_task_failure,
    apply_patch_edits,
    build_code_task_context_pack,
    build_code_task_repo_map,
    create_code_task_batch,
    execute_code_task,
    generate_code_task_work_plan,
    generate_patch_plan,
    initialize_code_task,
    locate_code_task_context,
    probe_code_task_environment,
    propose_patch_edits,
    propose_repair_edits,
    record_plan_decision,
    run_code_task_baseline,
    run_code_task_benchmark,
    validate_code_task,
    PatchValidationError,
    WorkspaceModeError,
)
from simple_ar.code_task.runtime.config import (
    CodeTaskConfigError,
    load_code_task_init_options,
    load_code_task_execute_options,
)
from simple_ar.app.cleanup import (
    CleanError,
    apply_clean_plan,
    build_clean_plan,
    build_shared_cache_clean_plan,
    build_shared_index_clean_plan,
    confirm_clean_plan,
    render_clean_plan,
)
from simple_ar.code_task.orchestration.execute import EXECUTE_STEPS
from simple_ar.core.console import print_line
from simple_ar.core.pipeline import Context, PipelineRunner
from simple_ar.core.reporting import ConsoleReporter
from simple_ar.integrations.llm import LLMError
from simple_ar.retrieval.index import build_artifact_index
from simple_ar.retrieval.search import search_artifacts
from simple_ar.app.run_config import RunConfigError, load_pipeline_run_config
from simple_ar.pipeline_stages.registry import HANDLERS
from simple_ar.core.stages import Stage, parse_stage
from simple_ar.cli.parser import build_parser
from simple_ar.tools.cli import call_tool, print_tool_schema, serve_mcp


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        settings = _resolve_run_settings(args)
        topic = str(settings["topic"])
        from_stage = str(settings["from_stage"])
        to_stage = str(settings["to_stage"])
        run_dir = _new_run_dir(Path(str(settings["output_root"])), topic)
        reporter = ConsoleReporter(enabled=not bool(settings["quiet"]))
        ctx = Context(
            run_dir=run_dir,
            topic=topic,
            config=dict(settings["config"]),
        )
        executions = _run_pipeline_for_cli(ctx, reporter, from_stage=from_stage, to_stage=to_stage)
        if settings["quiet"]:
            print_line(f"Run directory: {run_dir}")
        print_line(f"Stages completed: {len(executions)}")
        return

    if args.command == "resume":
        run_dir = Path(args.run_dir)
        topic = _read_topic(run_dir)
        from_stage = args.from_stage or _next_stage_from_state(run_dir) or "plan"
        config = _resume_config(run_dir, args, from_stage)
        quiet = bool(config.pop("_quiet", False))
        to_stage = str(config.get("to_stage") or "report")
        reporter = ConsoleReporter(enabled=not quiet)
        ctx = Context(
            run_dir=run_dir,
            topic=topic,
            config=config,
        )
        executions = _run_pipeline_for_cli(ctx, reporter, from_stage=from_stage, to_stage=to_stage)
        if quiet:
            print_line(f"Run directory: {run_dir}")
        print_line(f"Resumed from: {from_stage}")
        print_line(f"Stages completed: {len(executions)}")
        return

    if args.command == "status":
        _print_status(Path(args.run_dir))
        return

    if args.command == "tools":
        if args.tools_command == "schema":
            print_tool_schema(schema_format=args.format, output=args.output)
            return
        if args.tools_command == "call":
            call_tool(Path(args.run_dir), args.tool_name, args_json=args.args_json, debug_payloads=args.debug_payloads)
            return
        if args.tools_command == "serve-mcp":
            serve_mcp(Path(args.run_dir), debug_payloads=args.debug_payloads)
            return
        parser.error(f"Unknown tools command: {args.tools_command}")

    if args.command == "code-task":
        if args.code_task_command == "init":
            _print_code_task_init(args)
            return
        if args.code_task_command == "probe":
            _print_code_task_probe(args)
            return
        if args.code_task_command == "map":
            _print_code_task_map(args)
            return
        if args.code_task_command == "locate":
            _print_code_task_locate(args)
            return
        if args.code_task_command == "context":
            _print_code_task_context(args)
            return
        if args.code_task_command == "work-plan":
            _print_code_task_work_plan(args)
            return
        if args.code_task_command == "batch":
            _print_code_task_batch(args)
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
        if args.code_task_command == "baseline":
            _print_code_task_baseline(args)
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
        if args.code_task_command == "execute":
            _print_code_task_execute(args)
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

    if args.command == "clean":
        _print_clean(args)
        return

    parser.error(f"Unknown command: {args.command}")


def _print_clean(args: argparse.Namespace) -> None:
    """Preview and clean rebuildable run caches after confirmation."""
    try:
        if args.shared_cache:
            plan = build_shared_cache_clean_plan(
                index_root=args.index_root,
                literature_cache_root=args.literature_cache_root,
                allow_external_index_root=bool(args.allow_external_index_root),
            )
        elif args.shared_index:
            plan = build_shared_index_clean_plan(
                index_root=args.index_root,
                allow_external_index_root=bool(args.allow_external_index_root),
            )
        else:
            if not args.run_dir:
                raise CleanError("Missing run directory. Pass RUN_DIR or use --shared-index.")
            plan = build_clean_plan(Path(args.run_dir), all_caches=bool(args.all_caches))
    except CleanError as exc:
        raise SystemExit(str(exc)) from exc
    render_clean_plan(plan)
    if not plan.targets:
        print_line("Nothing to clean.")
        return
    if not confirm_clean_plan(plan, assume_yes=args.yes):
        print_line("Clean cancelled.")
        return
    result = apply_clean_plan(plan)
    print_line(f"Cleaned targets: {result.deleted_targets}")
    print_line(f"Deleted bytes: {_format_bytes(result.deleted_bytes)}")
    if result.deleted_sqlite_rows:
        print_line(f"Deleted shared SQLite index rows: {result.deleted_sqlite_rows}")
    if result.deleted_lancedb_rows:
        print_line(f"Deleted shared LanceDB index rows: {result.deleted_lancedb_rows}")


def _stage_handlers():
    """Return the mapping of stages to their respective handler functions."""
    return {Stage(number): handler for number, handler in HANDLERS.items()}


def _run_pipeline_for_cli(
    ctx: Context,
    reporter: ConsoleReporter,
    *,
    from_stage: str,
    to_stage: str,
) -> list[object]:
    """Run the pipeline, with research-only report shortcut support."""
    if not _should_jump_research_only_report(ctx.config, from_stage=from_stage, to_stage=to_stage):
        return PipelineRunner(_stage_handlers(), reporter=reporter).run(
            ctx,
            from_stage=from_stage,
            to_stage=to_stage,
        )

    start = parse_stage(from_stage)
    executions: list[object] = []
    if int(start) <= int(Stage.SYNTHESIZE):
        executions.extend(
            PipelineRunner(_stage_handlers(), reporter=reporter).run(
                ctx,
                from_stage=start,
                to_stage=Stage.SYNTHESIZE,
            )
        )
    executions.extend(
        PipelineRunner(_stage_handlers(), reporter=reporter).run(
            ctx,
            from_stage=Stage.REPORT,
            to_stage=Stage.REPORT,
        )
    )
    return executions


def _should_jump_research_only_report(
    config: dict[str, object],
    *,
    from_stage: str,
    to_stage: str,
) -> bool:
    """Return true when report-only survey should skip design/code/run stages."""
    try:
        target = parse_stage(to_stage)
        start = parse_stage(from_stage)
    except ValueError:
        return False
    mode = str(config.get("report_mode") or "auto").strip().lower()
    return mode == "research_only" and target == Stage.REPORT and start != Stage.REPORT


def _resolve_run_settings(args: argparse.Namespace) -> dict[str, object]:
    """Merge run defaults, TOML config, and explicit CLI overrides."""
    file_config = _load_run_config_or_exit(getattr(args, "config", None))
    topic = _first_string(args.topic, file_config.get("topic"))
    if not topic:
        raise SystemExit("Missing research topic. Pass --topic or set [run].topic in --config.")
    output_root = _first_string(args.output_root, file_config.get("output_root"), "runs")
    from_stage = _first_string(args.from_stage, file_config.get("from_stage"), "plan")
    to_stage = _first_string(args.to_stage, file_config.get("to_stage"), "report")
    args_quiet = getattr(args, "quiet", None)
    quiet = bool(args_quiet) if args_quiet is not None else bool(file_config.get("quiet", False))

    config = _default_run_context_config()
    config.update(_context_config_values(file_config))
    _apply_run_cli_overrides(config, args)
    config["from_stage"] = from_stage
    config["to_stage"] = to_stage
    return {
        "topic": topic,
        "output_root": output_root,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "quiet": quiet,
        "config": config,
    }


def _resume_config(run_dir: Path, args: argparse.Namespace, from_stage: str) -> dict[str, object]:
    """Merge resume-time overrides into the original run configuration.

    Resuming a run should not silently replace the original template, timeout,
    retrieval, or search settings with parser defaults. Only explicitly supplied
    resume flags should override the saved ``config_snapshot.json``.
    """
    config = _base_resume_config(run_dir)
    file_config = _load_run_config_or_exit(getattr(args, "config", None))
    config.update(_context_config_values(file_config))
    config["from_stage"] = from_stage
    config["to_stage"] = _first_string(args.to_stage, file_config.get("to_stage"), "report")
    args_quiet = getattr(args, "quiet", None)
    config["_quiet"] = bool(args_quiet) if args_quiet is not None else bool(file_config.get("quiet", False))

    _apply_run_cli_overrides(config, args)
    return config


def _apply_run_cli_overrides(config: dict[str, object], args: argparse.Namespace) -> None:
    """Apply explicit run/resume CLI values over defaults or config files."""
    _set_if_not_none(config, "model", args.model)
    _set_if_not_none(config, "llm_max_workers", args.llm_workers)
    _set_if_not_none(config, "max_papers", args.max_papers)
    _set_if_not_none(config, "search_query", args.search_query)
    _set_if_not_none(config, "experiment_template", args.experiment_template)
    _set_if_not_none(config, "experiment_timeout_sec", args.experiment_timeout)
    _set_if_not_none(config, "retrieval_top_k", args.retrieval_top_k)
    _set_if_not_none(config, "report_mode", args.report_mode)
    _set_if_not_none(config, "report_output_mode", getattr(args, "report_output_mode", None))
    _set_if_not_none(config, "report_output_label", getattr(args, "report_output_label", None))
    _set_if_not_none(config, "overwrite_stage_artifacts", getattr(args, "overwrite_stage_artifacts", None))
    config.update(_pipeline_code_task_config(args))

    if args.no_llm is True:
        config["use_llm"] = False
        config["mode"] = "offline"
    else:
        config["use_llm"] = bool(config.get("use_llm", True))
        config["mode"] = "llm" if config["use_llm"] else "offline"
    if args.offline_search is True:
        config["use_arxiv"] = False
    else:
        config["use_arxiv"] = bool(config.get("use_arxiv", True))
    if args.allow_fixture_fallback is True:
        config["allow_fixture_fallback"] = True
    else:
        config["allow_fixture_fallback"] = bool(config.get("allow_fixture_fallback", False))
    if args.strict_search is True:
        config["strict_search"] = True
    else:
        config["strict_search"] = bool(config.get("strict_search", False))
    if args.no_retrieval is True:
        config["use_retrieval"] = False
    else:
        config["use_retrieval"] = bool(config.get("use_retrieval", True))


def _load_run_config_or_exit(config_path: str | None) -> dict[str, object]:
    try:
        return load_pipeline_run_config(config_path)
    except RunConfigError as exc:
        raise SystemExit(str(exc)) from exc


def _context_config_values(data: dict[str, object]) -> dict[str, object]:
    meta_keys = {"topic", "output_root", "quiet"}
    return {key: value for key, value in data.items() if key not in meta_keys}


def _first_string(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _default_run_context_config() -> dict[str, object]:
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
        "report_mode": "auto",
    }


def _base_resume_config(run_dir: Path) -> dict[str, object]:
    config_path = run_dir / "config_snapshot.json"
    if config_path.exists():
        data = read_json(config_path)
        if isinstance(data, dict):
            return dict(data)
    return _default_run_context_config()


def _pipeline_code_task_config(args: argparse.Namespace) -> dict[str, object]:
    """Return non-empty top-level code-task experiment config overrides."""
    config: dict[str, object] = {}
    mapping = {
        "code_task_config": "code_task_config",
        "code_task_code_root": "code_task_code_root",
        "code_task_task_file": "code_task_task_file",
        "code_task_benchmark_command": "code_task_benchmark_command",
        "code_task_name": "code_task_name",
        "code_task_max_file_bytes": "code_task_max_file_bytes",
        "code_task_workspace_mode": "code_task_workspace_mode",
        "code_task_workspace_reuse_source_venv": "code_task_workspace_reuse_source_venv",
        "code_task_workspace_setup_hook": "code_task_workspace_setup_hook",
        "code_task_env_mode": "code_task_env_mode",
        "code_task_python_executable": "code_task_python_executable",
        "code_task_primary_metric": "code_task_primary_metric",
    }
    for attr, key in mapping.items():
        value = getattr(args, attr, None)
        if value is not None:
            config[key] = value
    metric_directions = getattr(args, "code_task_metric_direction", None)
    if metric_directions:
        config["code_task_metric_directions"] = dict(metric_directions)
    return config


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
        state_path = run_dir / "state.json"
        if state_path.exists():
            data = read_json(state_path)
            topic = data.get("topic")
            if isinstance(topic, str) and topic.strip():
                return topic.strip()
        raise SystemExit(f"Missing topic.txt in {run_dir}")
    return read_text(topic_path).strip()


def _next_stage_from_state(run_dir: Path) -> str | None:
    """Read the pipeline_state.json to determine which stage needs to run next."""
    state_path = run_dir / "pipeline_state.json"
    if not state_path.exists():
        workspace_state_path = run_dir / "state.json"
        if not workspace_state_path.exists():
            return None
        data = read_json(workspace_state_path)
        stages = data if isinstance(data, dict) else {}
        completed = {
            "plan": stages.get("plan", {}),
            "search": stages.get("search", {}),
            "read": stages.get("read", {}),
            "synthesize": stages.get("synthesize", {}),
            "design": stages.get("design", {}),
            "code": stages.get("code", {}),
            "run": stages.get("run", {}),
            "report": stages.get("report", {}),
        }
        for stage_name in ("plan", "search", "read", "synthesize", "design", "code", "run", "report"):
            stage_state = completed.get(stage_name, {})
            if not isinstance(stage_state, dict) or stage_state.get("status") != "completed":
                return stage_name
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

    print_line(f"Run: {run_dir}")
    print_line(f"Topic: {manifest.get('topic', '')}")
    if state:
        print_line(
            "Pipeline: "
            f"{state.get('status', 'unknown')} "
            f"(last={state.get('last_stage', 'none')}, next={state.get('next_stage', 'none')})"
        )

    print_line("Stages:")
    for item in manifest.get("stages", []):
        marker = _stage_status(item)
        outputs = item.get("outputs", [])
        output_text = ", ".join(str(name) for name in outputs) if isinstance(outputs, list) else ""
        suffix = f" -> {output_text}" if output_text else ""
        print_line(f"- {item['stage_number']:02d} {item['stage']}: {marker}{suffix}")

    report_dir = run_dir / "08-report"
    report_path = report_dir / "report.md"
    report_manifest_path = report_dir / "manifest.json"
    if report_path.exists() or report_manifest_path.exists():
        print_line("Report:")
        if report_path.exists():
            print_line(f"- report.md: {report_path}")
        if report_manifest_path.exists():
            print_line(f"- manifest.json: {report_manifest_path}")


def _print_code_task_status(run_dir: Path, manifest: dict[str, object]) -> None:
    """Print status for a code-task workflow manifest."""
    print_line(f"Run: {run_dir}")
    print_line(f"Workflow: {manifest.get('workflow', 'code_task')}")
    print_line(f"Status: {manifest.get('status', 'unknown')}")
    objective = manifest.get("objective", {})
    if isinstance(objective, dict) and objective:
        print_line(f"Objective: {objective.get('status', 'unknown')}")

    layout = manifest.get("layout", {})
    if isinstance(layout, dict):
        print_line("Layout:")
        for key in (
            "summary",
            "task",
            "workspace",
            "meta",
            "codebase_index",
            "work_plan",
            "attempts",
        ):
            value = layout.get(key)
            if value:
                print_line(f"- {key}: {run_dir / str(value)}")

    workspace = manifest.get("workspace", {})
    if isinstance(workspace, dict) and workspace:
        print_line("Workspace:")
        print_line(f"- mode: {workspace.get('mode', 'copy')}")
        cleanup = workspace.get("cleanup_hint")
        if cleanup:
            print_line(f"- cleanup: {cleanup}")

    codebase = manifest.get("codebase", {})
    if isinstance(codebase, dict):
        print_line("Codebase:")
        print_line(f"- files: {codebase.get('file_count', 0)}")
        print_line(f"- python files: {codebase.get('python_file_count', 0)}")
        print_line(f"- test files: {codebase.get('test_file_count', 0)}")

    plan = manifest.get("plan", {})
    if isinstance(plan, dict) and plan:
        print_line("Plan:")
        print_line(f"- status: {plan.get('status', 'unknown')}")
        print_line(f"- mode: {plan.get('mode', 'unknown')}")
        if plan.get("patch_plan"):
            print_line(f"- patch plan: {run_dir / str(plan.get('patch_plan'))}")

    work_plan = manifest.get("work_plan", {})
    if isinstance(work_plan, dict) and work_plan:
        print_line("Work Plan:")
        print_line(f"- status: {work_plan.get('status', 'unknown')}")
        print_line(f"- mode: {work_plan.get('mode', 'unknown')}")
        print_line(f"- items: {work_plan.get('item_count', 0)}")
        if work_plan.get("path"):
            print_line(f"- json: {run_dir / str(work_plan.get('path'))}")
        if work_plan.get("markdown"):
            print_line(f"- markdown: {run_dir / str(work_plan.get('markdown'))}")

    attempts = manifest.get("attempts", {})
    if isinstance(attempts, dict) and attempts:
        print_line("Attempts:")
        print_line(f"- active: {attempts.get('active', '')}")
        if attempts.get("latest_batch"):
            print_line(f"- latest batch: {run_dir / str(attempts.get('latest_batch'))}")

    environment = manifest.get("environment", {})
    if isinstance(environment, dict) and environment:
        print_line("Environment:")
        print_line(f"- status: {environment.get('status', 'unknown')}")
        policy = environment.get("policy", {})
        if isinstance(policy, dict):
            print_line(f"- mode: {policy.get('mode', 'current')}")
            if policy.get("python_executable"):
                print_line(f"- python: {policy.get('python_executable')}")
        report = environment.get("report")
        if report:
            print_line(f"- report: {run_dir / str(report)}")
        platform_data = environment.get("platform", {})
        if isinstance(platform_data, dict):
            system = platform_data.get("system")
            release = platform_data.get("release")
            if system:
                print_line(f"- platform: {system} {release or ''}".rstrip())
        gpu = environment.get("gpu", {})
        if isinstance(gpu, dict):
            print_line(f"- gpu: {gpu.get('count', 0)} device(s)")

    patch = manifest.get("patch", {})
    if isinstance(patch, dict) and patch:
        print_line("Patch:")
        print_line(f"- status: {patch.get('status', 'unknown')}")
        editor = patch.get("editor")
        backend = patch.get("editor_backend")
        if not backend and isinstance(editor, dict):
            backend = editor.get("backend")
        if backend:
            print_line(f"- editor backend: {backend}")
        if patch.get("proposed_edits"):
            print_line(f"- proposed edits: {run_dir / str(patch.get('proposed_edits'))}")
        if patch.get("patch_diff"):
            print_line(f"- patch diff: {run_dir / str(patch.get('patch_diff'))}")
        changed_files = patch.get("changed_files")
        if isinstance(changed_files, list) and changed_files:
            print_line(f"- changed files: {', '.join(str(path) for path in changed_files)}")

    validation = manifest.get("validation", {})
    if isinstance(validation, dict) and validation:
        print_line("Validation:")
        print_line(f"- status: {validation.get('status', 'unknown')}")
        print_line(f"- errors: {validation.get('error_count', 0)}")
        print_line(f"- warnings: {validation.get('warning_count', 0)}")
        if validation.get("report"):
            print_line(f"- report: {run_dir / str(validation.get('report'))}")

    benchmark = manifest.get("benchmark", {})
    if isinstance(benchmark, dict) and benchmark.get("command"):
        print_line("Benchmark:")
        print_line(f"- command: {benchmark.get('command')}")
        if benchmark.get("primary_metric"):
            print_line(f"- primary metric: {benchmark.get('primary_metric')}")
        metric_directions = benchmark.get("metric_directions", {})
        if isinstance(metric_directions, dict) and metric_directions:
            direction_text = ", ".join(
                f"{name}={direction}"
                for name, direction in sorted(metric_directions.items())
            )
            print_line(f"- metric directions: {direction_text}")
        print_line(f"- executed: {benchmark.get('executed', False)}")
        if benchmark.get("last_status"):
            print_line(f"- last status: {benchmark.get('last_status')}")
        if benchmark.get("latest_label"):
            print_line(f"- latest label: {benchmark.get('latest_label')}")
        runs = benchmark.get("runs", {})
        if isinstance(runs, dict) and runs:
            for label in ("baseline", "patched"):
                row = runs.get(label)
                if isinstance(row, dict):
                    print_line(f"- {label}: {row.get('status', 'unknown')}")
        comparison = benchmark.get("comparison", {})
        if isinstance(comparison, dict) and comparison:
            print_line(f"- comparison: {comparison.get('verdict', 'inconclusive')}")
            deltas = comparison.get("deltas", {})
            if isinstance(deltas, dict) and deltas:
                delta_text = ", ".join(
                    f"{name}={_format_status_number(value)}"
                    for name, value in sorted(deltas.items())[:5]
                )
                print_line(f"- comparison deltas: {delta_text}")
            if comparison.get("path"):
                print_line(f"- comparison report: {run_dir / str(comparison.get('path'))}")
        if benchmark.get("execution_report"):
            print_line(f"- execution report: {run_dir / str(benchmark.get('execution_report'))}")

    failure = manifest.get("failure_analysis", {})
    if isinstance(failure, dict) and failure:
        if failure.get("status") not in {"no_failure", "resolved"}:
            print_line("Failure Analysis:")
            print_line(f"- status: {failure.get('status', 'unknown')}")
            if failure.get("source"):
                print_line(f"- source: {failure.get('source')}")
            if failure.get("analysis"):
                print_line(f"- analysis: {run_dir / str(failure.get('analysis'))}")

    repair = manifest.get("repair", {})
    if isinstance(repair, dict) and repair:
        if repair.get("status") not in {"benchmark_passed", "resolved"}:
            print_line("Repair:")
            print_line(f"- status: {repair.get('status', 'unknown')}")
            print_line(f"- attempts: {repair.get('repair_count', 0)}")
            if repair.get("latest_proposed_edits"):
                print_line(f"- latest proposal: {run_dir / str(repair.get('latest_proposed_edits'))}")


def _print_inspect(run_dir: Path) -> None:
    """Build an artifact index and print a compact run summary."""
    index = build_artifact_index(run_dir)
    artifacts = _artifact_rows(index)
    print_line(f"Run: {run_dir}")
    print_line(f"Artifacts: {len(artifacts)}")
    print_line(f"Index: {run_dir / 'artifact_index.json'}")

    by_kind = _count_by(artifacts, "kind")
    if by_kind:
        print_line("Kinds:")
        for name, count in by_kind.items():
            print_line(f"- {name}: {count}")

    by_stage = _count_by(artifacts, "stage")
    if by_stage:
        print_line("Stages:")
        for name, count in by_stage.items():
            print_line(f"- {name}: {count}")

    if artifacts:
        print_line("Largest artifacts:")
        for artifact in sorted(artifacts, key=lambda item: int(item.get("bytes", 0)), reverse=True)[:5]:
            size = _format_bytes(int(artifact.get("bytes", 0)))
            print_line(
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
    print_line(f"Run: {run_dir}")
    print_line(f"Query: {query}")
    print_line(f"Chunks searched: {results.get('chunk_count', 0)}")
    print_line(f"Matches: {len(matches)}")
    print_line(f"Operational metadata included: {include_operational}")
    print_line(f"Results: {run_dir / 'artifact_search_results.json'}")
    for match in matches:
        path = match.get("path", "")
        line_start = match.get("line_start", "")
        line_end = match.get("line_end", "")
        score = match.get("score", "")
        snippet = str(match.get("snippet", "")).strip()
        print_line(f"- {path}:{line_start}-{line_end} score={score}")
        if snippet:
            print_line(f"  {snippet}")


def _print_code_task_init(args: argparse.Namespace) -> None:
    """Initialize a code-task run and print the resulting workspace summary."""
    try:
        options = load_code_task_init_options(
            config_path=args.config,
            code_root=args.code_root,
            task_file=args.task_file,
            output_root=args.output_root,
            name=args.name,
            benchmark_command=args.benchmark_command,
            max_file_bytes=args.max_file_bytes,
            workspace_mode=args.workspace_mode,
            workspace_include=args.workspace_include,
            workspace_exclude=args.workspace_exclude,
            workspace_reuse_source_venv=args.workspace_reuse_source_venv,
            workspace_setup_hook=args.workspace_setup_hook,
            env_mode=args.env_mode,
            python_executable=args.python_executable,
            primary_metric=args.primary_metric,
            metric_directions=args.metric_direction or [],
        )
    except CodeTaskConfigError as exc:
        raise SystemExit(str(exc)) from exc
    code_root = Path(options.code_root)
    if options.task_file is None:
        raise SystemExit("Missing task file. Pass --task-file or set [code_task].task_file.")
    task_file = Path(options.task_file)
    name = options.name or f"code-task-{code_root.resolve().name}"
    run_dir = _new_run_dir(Path(options.output_root), name)
    try:
        result = initialize_code_task(
            run_dir=run_dir,
            code_root=code_root,
            task_file=task_file,
            benchmark_command=options.benchmark_command,
            max_file_bytes=options.max_file_bytes,
            workspace_mode=options.workspace_mode,
            workspace_include=options.workspace_include,
            workspace_exclude=options.workspace_exclude,
            workspace_reuse_source_venv=options.workspace_reuse_source_venv,
            workspace_setup_hook=options.workspace_setup_hook,
            env_mode=options.env_mode,
            python_executable=options.python_executable,
            primary_metric=options.primary_metric,
            metric_directions=options.metric_directions,
            edit_scope_mode=options.edit_scope_mode,
            edit_scope_allowed_patterns=options.edit_scope_allowed_patterns,
            edit_scope_protected_patterns=options.edit_scope_protected_patterns,
        )
    except (
        FileExistsError,
        FileNotFoundError,
        NotADirectoryError,
        ValueError,
        WorkspaceModeError,
    ) as exc:
        raise SystemExit(_code_task_init_error_message(exc, options=options)) from exc
    render_init_result(
        result,
        config_path=options.config_path,
        benchmark_command=options.benchmark_command,
        primary_metric=options.primary_metric,
        metric_directions=options.metric_directions,
    )


def _code_task_init_error_message(
    exc: Exception,
    *,
    options: object,
) -> str:
    """Return a user-facing init error with likely next steps."""
    workspace_mode = getattr(options, "workspace_mode", "copy")
    code_root = getattr(options, "code_root", "")
    task_file = getattr(options, "task_file", "")
    lines = [f"Could not initialize code task: {exc}"]
    if isinstance(exc, FileNotFoundError) and "Task file" in str(exc):
        lines.extend(
            [
                "",
                "Check the task file path:",
                f"- configured task_file: {task_file or '(missing)'}",
                "- Pass --task-file path\\to\\task.md, or set [code_task].task_file in TOML.",
                "- For embedded 8-stage code_task_project runs, omit task_file only if you want 05-design to generate one.",
            ]
        )
    elif isinstance(exc, (FileNotFoundError, NotADirectoryError)):
        lines.extend(
            [
                "",
                "Check the code root path:",
                f"- configured code_root: {code_root or '(missing)'}",
                "- It should point to the baseline project directory, not the task file.",
                "- If you use git_worktree, code_root must be the baseline git repository root.",
            ]
        )
    elif isinstance(exc, WorkspaceModeError) and workspace_mode == "git_worktree":
        lines.extend(
            [
                "",
                "git_worktree quick checklist:",
                "- code_root should be the baseline repository root.",
                "- The repository needs at least one local commit.",
                "- GitHub or any remote is not required.",
                "- Use --workspace-mode copy when the baseline is not a git repository.",
            ]
        )
    return "\n".join(lines)


def _print_code_task_probe(args: argparse.Namespace) -> None:
    """Probe the workspace environment and print a compact summary."""
    result = probe_code_task_environment(
        Path(args.run_dir),
        env_mode=args.env_mode,
        python_executable=args.python_executable,
    )
    print_line(f"Code task run: {result.run_dir}")
    print_line(f"Environment report: {result.report_path}")
    print_line(f"Status: {result.status}")
    gpu_count = result.gpu.get("count", 0) if isinstance(result.gpu, dict) else 0
    print_line(f"GPU devices: {gpu_count}")
    available_tools = [
        name
        for name, data in result.tools.items()
        if isinstance(data, dict) and data.get("available") is True
    ]
    if available_tools:
        print_line("Available tools: " + ", ".join(sorted(available_tools)))
    if result.warnings:
        print_line("Warnings:")
        for warning in result.warnings:
            print_line(f"- {warning}")


def _print_code_task_map(args: argparse.Namespace) -> None:
    """Build repo-map artifacts and print the resulting project summary."""
    result = build_code_task_repo_map(
        Path(args.run_dir),
        refresh_index=not args.no_refresh_index,
    )
    project = result.repo_map.get("project", {})
    if not isinstance(project, dict):
        project = {}
    print_line(f"Code task run: {result.run_dir}")
    print_line(f"Repo map: {result.repo_map_path}")
    print_line(f"Summary: {result.summary_path}")
    print_line(f"Index: {result.codebase_index_path}")
    print_line(f"Index refreshed: {result.refreshed_index}")
    print_line(
        "Mapped: "
        f"{project.get('file_count', 0)} file(s), "
        f"{project.get('directory_count', 0)} directory group(s), "
        f"{project.get('symbol_count', 0)} symbol(s)"
    )
    print_line(
        "Roles: "
        f"{project.get('test_file_count', 0)} test file(s), "
        f"{project.get('benchmark_file_count', 0)} benchmark file(s), "
        f"{project.get('config_file_count', 0)} config file(s)"
    )
    entrypoints = result.repo_map.get("entrypoints", [])
    if isinstance(entrypoints, list) and entrypoints:
        print_line("Entrypoints:")
        for item in entrypoints[:8]:
            if isinstance(item, dict):
                symbol = item.get("symbol")
                suffix = f"::{symbol}" if symbol else ""
                print_line(f"- {item.get('path', '')}{suffix}")
    if args.show_summary:
        print_line("")
        print_line(read_text(result.summary_path))


def _print_code_task_locate(args: argparse.Namespace) -> None:
    """Rank likely code-task context files and print a compact summary."""
    result = locate_code_task_context(
        Path(args.run_dir),
        query=args.query,
        top_k=args.top_k,
        refresh_map=args.refresh_map,
        include_read_only=not args.no_read_only,
    )
    print_line(f"Code task run: {result.run_dir}")
    print_line(f"Locate results: {result.results_path}")
    print_line(f"Summary: {result.summary_path}")
    print_line(f"Editable targets: {len(result.editable_targets)}")
    for row in result.editable_targets[:8]:
        print_line(f"- {row.get('path', '')} (score {row.get('score', 0)})")
    print_line(f"Read-only evidence: {len(result.read_only_evidence)}")
    for row in result.read_only_evidence[:8]:
        print_line(f"- {row.get('path', '')} (score {row.get('score', 0)})")
    if args.show_summary:
        print_line("")
        print_line(read_text(result.summary_path))


def _print_code_task_context(args: argparse.Namespace) -> None:
    """Build a bounded code-task context pack and print artifact paths."""
    result = build_code_task_context_pack(
        Path(args.run_dir),
        query=args.query,
        top_k=args.top_k,
        max_files=args.max_files,
        max_source_chars_per_file=args.max_source_chars_per_file,
        max_total_chars=args.max_total_chars,
        refresh_map=args.refresh_map,
    )
    print_line(f"Code task run: {result.run_dir}")
    print_line(f"Context directory: {result.context_dir}")
    print_line(f"Context pack: {result.context_pack_path}")
    print_line(f"Prompt context: {result.prompt_context_path}")
    print_line(f"Snippets: {result.snippets_path}")
    print_line(f"Locate results: {result.locate_results_path}")
    print_line(f"Selected files: {len(result.selected_files)}")
    for path in result.selected_files[:12]:
        print_line(f"- {path}")
    if args.show_prompt:
        print_line("")
        print_line(read_text(result.prompt_context_path))


def _print_code_task_work_plan(args: argparse.Namespace) -> None:
    """Generate a batch-oriented work plan and print artifact paths."""
    try:
        result = generate_code_task_work_plan(
            Path(args.run_dir),
            model=args.model,
            use_llm=not args.no_llm,
            allow_llm_fallback=args.allow_planning_fallback,
            llm_retry_attempts=args.llm_retry_attempts,
            force=args.force,
            max_files=args.max_files,
            max_source_chars_per_file=args.max_source_chars_per_file,
            message_callback=lambda message: print_line(f"  - {message}"),
        )
    except LLMError as exc:
        raise SystemExit(str(exc)) from exc
    print_line(f"Code task run: {result.run_dir}")
    print_line(f"Work plan: {result.work_plan_path}")
    print_line(f"Work plan markdown: {result.work_plan_markdown_path}")
    print_line(f"Mode: {result.mode}")
    print_line(f"Items: {result.item_count}")
    print_line(f"Pending approval: {result.pending_approval}")
    print_line(f"Context files: {len(result.selected_files)}")
    for path in result.selected_files:
        print_line(f"- {path}")


def _print_code_task_batch(args: argparse.Namespace) -> None:
    """Create attempt/batch state for a reviewed work item."""
    result = create_code_task_batch(
        Path(args.run_dir),
        work_item_id=args.work_item,
        attempt_id=args.attempt_id,
        force=args.force,
    )
    print_line(f"Code task run: {result.run_dir}")
    print_line(f"Attempt: {result.attempt_id}")
    print_line(f"Batch: {result.batch_id}")
    print_line(f"Work item: {result.work_item_id}")
    print_line(f"State: {result.state}")
    print_line(f"Attempt state: {result.attempt_state_path}")
    print_line(f"Batch state: {result.batch_state_path}")


def _print_code_task_plan(args: argparse.Namespace) -> None:
    """Generate a code-task patch plan and print a compact summary."""
    try:
        result = generate_patch_plan(
            Path(args.run_dir),
            model=args.model,
            use_llm=not args.no_llm,
            allow_llm_fallback=args.allow_planning_fallback,
            llm_retry_attempts=args.llm_retry_attempts,
            force=args.force,
            max_files=args.max_files,
            max_source_chars_per_file=args.max_source_chars_per_file,
            message_callback=lambda message: print_line(f"  - {message}"),
        )
    except LLMError as exc:
        raise SystemExit(str(exc)) from exc
    print_line(f"Code task run: {result.run_dir}")
    print_line(f"Patch plan: {result.patch_plan_path}")
    print_line(f"Mode: {result.mode}")
    print_line(f"Pending approval: {result.pending_approval}")
    print_line(f"Context files: {len(result.selected_files)}")
    for path in result.selected_files:
        print_line(f"- {path}")


def _print_code_task_decision(args: argparse.Namespace) -> None:
    """Record and print the human decision for a patch plan."""
    row = record_plan_decision(
        Path(args.run_dir),
        decision=args.decision,
        note=args.note,
        reviewer=args.reviewer,
    )
    print_line(f"Code task run: {args.run_dir}")
    print_line(f"Decision: {row['decision']}")
    print_line("Decision log: code_task/meta/hitl_decisions.jsonl")


def _print_code_task_propose_edits(args: argparse.Namespace) -> None:
    """Generate controlled edits and print a compact proposal summary."""
    result = propose_patch_edits(
        Path(args.run_dir),
        model=args.model,
        use_llm=not args.no_llm,
        force=args.force,
        max_files=args.max_files,
        max_source_chars_per_file=args.max_source_chars_per_file,
        allow_large_edits=args.allow_large_edits,
        message_callback=lambda message: print_line(f"  - {message}"),
    )
    print_line(f"Code task run: {result.run_dir}")
    print_line(f"Proposed edits: {result.proposal_path}")
    print_line(f"Mode: {result.mode}")
    print_line(f"Edit count: {result.edit_count}")
    print_line(f"Context files: {len(result.selected_files)}")
    for path in result.selected_files:
        print_line(f"- {path}")


def _print_code_task_apply_edits(args: argparse.Namespace) -> None:
    """Safely apply controlled edits and print changed files."""
    try:
        result = apply_patch_edits(
            Path(args.run_dir),
            edits_file=Path(args.edits_file) if args.edits_file else None,
            allow_unapproved_plan=args.allow_unapproved_plan,
            allow_large_edits=args.allow_large_edits,
        )
    except PatchValidationError as exc:
        print_line("Patch validation failed; no workspace files were changed.")
        print_line(str(exc))
        raise SystemExit(1) from exc
    print_line(f"Code task run: {result.run_dir}")
    print_line(f"Patch diff: {result.patch_diff_path}")
    print_line(f"Applied edits: {result.applied_edits_path}")
    print_line(f"Changed files: {len(result.changed_files)}")
    for path in result.changed_files:
        print_line(f"- {path}")


def _print_code_task_validate(args: argparse.Namespace) -> None:
    """Run static validation and print a compact issue summary."""
    result = validate_code_task(
        Path(args.run_dir),
        strict=args.strict,
        max_file_bytes=args.max_file_bytes,
    )
    print_line(f"Code task run: {result.run_dir}")
    print_line(f"Validation report: {result.report_path}")
    print_line(f"Status: {result.status}")
    print_line(f"Errors: {result.error_count}")
    print_line(f"Warnings: {result.warning_count}")


def _print_code_task_run(args: argparse.Namespace) -> None:
    """Run a code-task benchmark and print execution artifacts."""
    result = run_code_task_benchmark(
        Path(args.run_dir),
        command=args.benchmark_command,
        timeout_sec=args.timeout,
        skip_validation=args.skip_validation,
        env_mode=args.env_mode,
        python_executable=args.python_executable,
    )
    print_line(f"Code task run: {result.run_dir}")
    print_line(f"Run label: {result.label}")
    print_line(f"Execution report: {result.report_path}")
    print_line(f"Status: {result.status}")
    print_line(f"Return code: {result.returncode}")
    print_line(f"Timed out: {result.timed_out}")
    print_line(f"Stdout: {result.stdout_path}")
    print_line(f"Stderr: {result.stderr_path}")
    if result.metrics:
        print_line("Metrics:")
        for key, value in result.metrics.items():
            print_line(f"- {key}: {value}")


def _print_code_task_baseline(args: argparse.Namespace) -> None:
    """Run the pre-patch benchmark and print execution artifacts."""
    result = run_code_task_baseline(
        Path(args.run_dir),
        command=args.benchmark_command,
        timeout_sec=args.timeout,
        skip_validation=args.skip_validation,
        env_mode=args.env_mode,
        python_executable=args.python_executable,
    )
    print_line(f"Code task run: {result.run_dir}")
    print_line(f"Baseline report: {result.report_path}")
    print_line(f"Status: {result.status}")
    print_line(f"Return code: {result.returncode}")
    print_line(f"Timed out: {result.timed_out}")
    print_line(f"Stdout: {result.stdout_path}")
    print_line(f"Stderr: {result.stderr_path}")
    if result.metrics:
        print_line("Metrics:")
        for key, value in result.metrics.items():
            print_line(f"- {key}: {value}")


def _print_code_task_analyze_failure(args: argparse.Namespace) -> None:
    """Write failure analysis and print the implicated files."""
    result = analyze_code_task_failure(Path(args.run_dir))
    print_line(f"Code task run: {result.run_dir}")
    print_line(f"Failure analysis: {result.analysis_path}")
    print_line(f"Status: {result.status}")
    print_line(f"Source: {result.source}")
    print_line(f"Implicated files: {len(result.implicated_files)}")
    for path in result.implicated_files:
        print_line(f"- {path}")


def _print_code_task_repair(args: argparse.Namespace) -> None:
    """Generate a bounded repair proposal and print the review path."""
    result = propose_repair_edits(
        Path(args.run_dir),
        model=args.model,
        use_llm=not args.no_llm,
        max_files=args.max_files,
        max_source_chars_per_file=args.max_source_chars_per_file,
        message_callback=lambda message: print_line(f"  - {message}"),
    )
    print_line(f"Code task run: {result.run_dir}")
    print_line(f"Repair directory: {result.repair_dir}")
    print_line(f"Proposed edits: {result.proposal_path}")
    print_line(f"Mode: {result.mode}")
    print_line(f"Edit count: {result.edit_count}")
    print_line(f"Context files: {len(result.selected_files)}")
    for path in result.selected_files:
        print_line(f"- {path}")


def _print_code_task_execute(args: argparse.Namespace) -> None:
    """Run the state-aware code-task orchestrator and render progress."""
    try:
        options = load_code_task_execute_options(config_path=args.config)
    except CodeTaskConfigError as exc:
        raise SystemExit(str(exc)) from exc
    model = args.model or options.model
    use_llm = False if args.no_llm else options.use_llm
    timeout = args.timeout if args.timeout != 60 else options.timeout_sec
    to_step = args.to_step or options.to_step
    repair_rounds = args.repair_rounds if args.repair_rounds != 0 else options.repair_rounds
    max_files = args.max_files if args.max_files != 8 else options.max_files
    max_source_chars = (
        args.max_source_chars_per_file
        if args.max_source_chars_per_file != 4000
        else options.max_source_chars_per_file
    )
    validation_max_file_bytes = (
        args.validation_max_file_bytes
        if args.validation_max_file_bytes != 500_000
        else options.validation_max_file_bytes
    )
    env_mode = args.env_mode or options.env_mode
    python_executable = args.python_executable or options.python_executable
    allow_planning_fallback = args.allow_planning_fallback or options.allow_planning_fallback
    llm_retry_attempts = args.llm_retry_attempts or options.llm_retry_attempts
    inline_apply_proposed_edits = False
    inline_allow_large_edits = False

    run_dir = Path(args.run_dir)
    render_execute_header(
        run_dir,
        to_step=to_step,
        use_llm=use_llm,
        timeout_sec=timeout,
        dry_run=args.dry_run,
    )

    def run_execute(target_step: str, *, dry_run_override: bool | None = None):
        return execute_code_task(
            run_dir,
            to_step=target_step,
            dry_run=args.dry_run if dry_run_override is None else dry_run_override,
            model=model,
            planner_model=options.planner_model,
            editor_model=options.editor_model,
            repair_model=options.repair_model,
            use_llm=use_llm,
            timeout_sec=timeout,
            skip_validation=args.skip_validation or options.skip_validation,
            env_mode=env_mode,
            python_executable=python_executable,
            strict_validation=args.strict_validation or options.strict_validation,
            validation_max_file_bytes=validation_max_file_bytes,
            stream_benchmark_output=options.stream_benchmark_output,
            apply_proposed_edits=(
                args.apply_proposed_edits
                or options.apply_proposed_edits
                or inline_apply_proposed_edits
            ),
            allow_large_edits=(
                args.allow_large_edits
                or options.allow_large_edits
                or inline_allow_large_edits
            ),
            allow_planning_fallback=allow_planning_fallback,
            llm_retry_attempts=llm_retry_attempts,
            repair_rounds=repair_rounds,
            budget_profile=options.budget_profile,
            edit_budget_overrides=options.edit_budget_overrides,
            max_batches=options.max_batches,
            cost_cap_usd=options.cost_cap_usd,
            max_files=max_files,
            max_source_chars_per_file=max_source_chars,
            message_callback=render_execute_message,
        )

    staged_mode = bool(args.interactive)
    if not staged_mode:
        result = run_execute(to_step)
        while True:
            render_execute_result(result)
            if not _inline_review_enabled(args):
                return
            action = _inline_review_action(result, to_step)
            if action == "approve-plan":
                render_review_gate(
                    title="Patch Plan Review",
                    artifact=run_dir / "code_task" / "patch_plan.md",
                    action="approve this patch plan and continue to edit proposal generation",
                    warning=(
                        "Only approve if the plan matches the task, edit scope, "
                        "benchmark, and expected files."
                    ),
                )
                if not confirm_review_gate(
                    "Approve the patch plan and continue?",
                    assume_yes=bool(args.yes),
                ):
                    print_line("Execute stopped at patch-plan review.")
                    return
                record_plan_decision(
                    run_dir,
                    decision="approve",
                    note="Approved from inline execute review.",
                    reviewer="execute",
                )
                result = run_execute(to_step)
                continue
            if action == "apply-proposal":
                render_review_gate(
                    title="Edit Proposal Review",
                    artifact=run_dir / "code_task" / "meta" / "proposed_edits.json",
                    action="apply the reviewed proposal, validate, and run the patched benchmark",
                    warning=(
                        "Only continue after checking every old/new replacement, "
                        "target path, and proposal warning."
                    ),
                )
                if not confirm_review_gate(
                    "Apply the reviewed edit proposal and continue?",
                    assume_yes=bool(args.yes),
                ):
                    print_line("Execute stopped at edit-proposal review.")
                    return
                inline_apply_proposed_edits = True
                result = run_execute(to_step)
                continue
            if action == "allow-large-edits":
                render_review_gate(
                    title="Large Edit Review",
                    artifact=run_dir / "code_task" / "meta" / "proposed_edits.json",
                    action="allow the large reviewed proposal and continue applying it",
                    warning=(
                        "Large edits have wider blast radius. Continue only if "
                        "the larger patch is intentional and fully reviewed."
                    ),
                )
                if not confirm_review_gate(
                    "Allow this large edit proposal and continue?",
                    assume_yes=bool(args.yes),
                ):
                    print_line("Execute stopped at large-edit review.")
                    return
                inline_apply_proposed_edits = True
                inline_allow_large_edits = True
                result = run_execute(to_step)
                continue
            return
        return

    target_index = EXECUTE_STEPS.index(to_step)
    rendered_steps: set[str] = set()
    for step in EXECUTE_STEPS[: target_index + 1]:
        preview = run_execute(step, dry_run_override=True)
        current_preview = next((record for record in reversed(preview.steps) if record.step == step), None)
        if current_preview is not None and current_preview.status == "skipped":
            render_execute_result(preview, steps=(current_preview,))
            rendered_steps.add(current_preview.step)
            if preview.stop_reason != "stop_point":
                return
            continue
        if preview.stop_reason != "dry_run":
            unseen_steps = tuple(record for record in preview.steps if record.step not in rendered_steps)
            render_execute_result(preview, steps=unseen_steps)
            return
        if not args.yes:
            render_step_preview(step)
            if not confirm_next_step(step, assume_yes=False):
                print_line(f"Execute stopped before step: {step}")
                return
        result = run_execute(step)
        current = next((record for record in reversed(result.steps) if record.step == step), None)
        render_execute_result(result, steps=(current,) if current is not None else ())
        if current is not None:
            rendered_steps.add(current.step)
        if result.stop_reason not in {"stop_point", "completed"}:
            return


def _inline_review_enabled(args: argparse.Namespace) -> bool:
    """Return true when execute may ask inline review-gate questions."""

    return (
        not bool(args.dry_run)
        and not bool(args.no_review_inline)
        and not bool(args.interactive)
        and (bool(args.yes) or sys.stdin.isatty())
    )


def _inline_review_action(result: object, to_step: str) -> str | None:
    """Map an execute stop reason to an optional inline review action."""

    target_index = EXECUTE_STEPS.index(to_step)
    stop_reason = getattr(result, "stop_reason", "")
    if stop_reason == "approval_required" and target_index > EXECUTE_STEPS.index("plan"):
        return "approve-plan"
    if stop_reason == "proposal_review_required" and target_index > EXECUTE_STEPS.index("propose-edits"):
        return "apply-proposal"
    if stop_reason == "large_edit_approval_required" and target_index > EXECUTE_STEPS.index("apply-edits"):
        return "allow-large-edits"
    return None


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


def _format_status_number(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        sign = "+" if number > 0 else ""
        return f"{sign}{number:.6g}"
    return str(value)


def _stage_status(item: dict[str, object]) -> str:
    """Return a readable stage status for old and new manifests."""
    value = item.get("status")
    if isinstance(value, str) and value:
        return value
    return "done" if item.get("completed") else "pending"


if __name__ == "__main__":
    main()
