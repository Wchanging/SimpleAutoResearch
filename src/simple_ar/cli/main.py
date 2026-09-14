from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

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
from simple_ar.code_task.runtime.config import EXECUTE_STEPS
from simple_ar.core.console import print_line
from simple_ar.integrations.llm import LLMClient, LLMError
from simple_ar.retrieval.index import build_artifact_index
from simple_ar.retrieval.search import search_artifacts
from simple_ar.cli.parser import build_parser
from simple_ar.tools.cli import call_tool, print_tool_schema, serve_mcp


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in {"run", "resume", "research-code-task", "research-experiment"}:
        raise SystemExit(
            f"The legacy command {arguments[0]} has been retired. "
            "Use research-session for new research and research-session-continue "
            "for canonical sessions. Historical outputs remain readable with status; "
            "legacy stage options are not silently translated or executed."
        )
    from simple_ar.cli.research_config import research_defaults
    try:
        parser = build_parser(research_defaults=research_defaults(arguments))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Invalid research configuration: {exc}") from exc
    args = parser.parse_args(arguments)


    if args.command == "research-brief":
        _print_research_brief(args)
        return

    if args.command == "research-session":
        _print_research_session(args)
        return

    if args.command == "research-session-continue":
        _print_research_session_continue(args)
        return

    if args.command == "research-session-migrate":
        _print_research_session_migrate(args)
        return

    if args.command == "research-report":
        _print_research_report(args)
        return



    if args.command == "status":
        _print_status(Path(args.run_dir))
        return

    if args.command == "tools":
        if args.tools_command == "schema":
            print_tool_schema(schema_format=args.format, output=args.output)
            return
        if args.tools_command == "call":
            call_tool(
                Path(args.run_dir),
                args.tool_name,
                args_json=args.args_json,
                args_file=args.args_file,
                debug_payloads=args.debug_payloads,
            )
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


def _print_research_brief(args: argparse.Namespace) -> None:
    """Run the small capability-oriented topic-to-brief application path."""

    from simple_ar.app.research_brief import (
        ResearchBriefSessionError,
        ResearchBriefSessionRequest,
        new_research_brief_root,
        run_research_brief_session,
    )

    if args.max_results < 1 or args.max_chunks < 1 or args.idea_limit < 1:
        raise SystemExit("--max-results, --max-chunks, and --idea-limit must be positive.")
    llm_client = _optional_research_llm_client(args.model, "research brief")
    session_root = new_research_brief_root(args.output_root, args.topic)
    request = ResearchBriefSessionRequest(
        topic=args.topic,
        session_root=session_root,
        local_documents=tuple(Path(path) for path in args.local_document),
        queries=tuple(args.queries),
        providers=tuple(args.providers),
        max_results=args.max_results,
        max_chunks=args.max_chunks,
        idea_limit=args.idea_limit,
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        use_llm=llm_client is not None,
        llm_client=llm_client,
    )
    try:
        result = run_research_brief_session(request)
    except ResearchBriefSessionError as exc:
        raise SystemExit(str(exc)) from exc
    print_line(f"Research brief session: {result.session_root}")
    print_line(f"Status: {result.status}")
    print_line(f"Mode: {'llm' if llm_client is not None else 'deterministic'}")
    print_line(f"Planner: {result.plan.query_plan.planner}")
    print_line(
        f"Synthesis: {result.brief.synthesis.generation_mode if result.brief.synthesis else 'none'}"
    )
    print_line(f"Papers: {len(result.search.papers)}")
    print_line(f"Documents: {len(result.documents.records)}")
    print_line(f"Ideas: {len(result.brief.synthesis.ideas) if result.brief.synthesis else 0}")
    print_line(f"Synthesis handoff: {result.brief_path}")




def _experiment_result_schema(args: argparse.Namespace) -> dict[str, object]:
    """Normalize the few metric controls exposed by the small CLI."""

    primary = str(args.primary_metric or "").strip()
    metrics = [str(item).strip() for item in args.metric if str(item).strip()]
    required = list(dict.fromkeys(([primary] if primary else []) + metrics))
    directions: dict[str, str] = {}
    for item in args.metric_direction:
        name, separator, direction = str(item).partition("=")
        if not separator or not name.strip() or not direction.strip():
            raise SystemExit(f"Invalid --metric-direction value: {item!r}")
        directions[name.strip()] = direction.strip()
    schema: dict[str, object] = {"required_metrics": required}
    if primary:
        schema["primary_metric"] = primary
    if directions:
        schema["metric_directions"] = directions
    return schema


def _print_research_session(args: argparse.Namespace) -> None:
    """Run the canonical bounded research-to-report application."""

    from simple_ar.app.research_application import (
        ResearchApplicationError,
        ResearchApplicationServices,
        create_session,
    )
    from simple_ar.app.research_intake import ResearchInputError
    from simple_ar.app.session_roots import new_research_session_root
    from simple_ar.research.workflow_contracts import ResearchBrief

    if args.max_results < 1 or args.max_chunks < 1 or args.idea_limit < 1:
        raise SystemExit(
            "--max-results, --max-chunks, and --idea-limit must be positive."
        )
    if args.timeout_sec is not None and args.timeout_sec < 1:
        raise SystemExit("--timeout-sec must be positive when provided.")
    if args.max_review_iterations < 0:
        raise SystemExit("--max-review-iterations cannot be negative.")
    command = tuple(args.command_argv or ())
    execution_details = getattr(args, "execution_details", {})
    if command and execution_details.get("pairs"):
        raise SystemExit("Use execution.pairs or a single command, not both; paired argv must be explicit.")
    outputs = getattr(args, "outputs", None)
    if outputs and "experiments" not in outputs and (command or execution_details or getattr(args, "code_task_config", None)):
        raise SystemExit("Execution configuration requires experiments in --outputs/task.outputs.")
    if outputs and (args.with_report or args.no_report):
        raise SystemExit("Use explicit outputs or --with-report/--no-report, not both.")
    for field in ("total_tokens", "llm_requests", "max_output_tokens", "process_invocations", "process_wall_seconds"):
        value = getattr(args, field, None)
        if value is not None and value < (0 if field.startswith("process_") else 1):
            raise SystemExit(f"Invalid {field}: {value}")
    code_task_spec = None
    code_task_baseline_policy = "auto"
    code_task_config = getattr(args, "code_task_config", None)
    if code_task_config:
        if command:
            raise SystemExit(
                "Use either --command or --code-task-config for research-session, not both."
            )
        if not args.model:
            raise SystemExit("--code-task-config requires --model for Code-Task generation.")
        try:
            code_task_spec, execute_options = _load_code_task_spec_for_cli(
                _resolve_cli_path(code_task_config)
            )
        except (CodeTaskConfigError, RuntimeError, TypeError, ValueError) as exc:
            raise SystemExit(f"Invalid research Code-Task configuration: {exc}") from exc
        if not code_task_spec.code_root.exists():
            raise SystemExit(f"Code-Task project root not found: {code_task_spec.code_root}")
        if execute_options.use_llm is not True:
            raise SystemExit(
                "--code-task-config requires [execute].use_llm = true because "
                "the existing Code-Task backend generates the implementation."
            )
        code_task_baseline_policy = execute_options.baseline_policy
        timeout_sec = (
            args.timeout_sec
            if args.timeout_sec is not None
            else execute_options.timeout_sec
        )
    else:
        # No command is the explicit literature-only shape of the canonical
        # application.  It must not manufacture an execution request: the
        # application will stop at its evidence summary, or continue to a
        # research-only report when a model is supplied.
        timeout_sec = args.timeout_sec if args.timeout_sec is not None else 300
    if args.with_report and args.no_report:
        raise SystemExit("Use either --with-report or --no-report for research-session, not both.")
    llm_client = _optional_research_llm_client(args.model, "research session", max_output_tokens=getattr(args, "max_output_tokens", None))
    report_requested = bool(args.with_report or (llm_client is not None and not args.no_report))
    if outputs is not None:
        report_requested = "report" in outputs
    if report_requested and llm_client is None:
        raise SystemExit("--with-report requires --model for report generation.")
    resume_root = getattr(args, "session_root", None)
    session_root = resume_root or new_research_session_root(args.output_root, args.topic)
    execution: dict[str, object] | None = None
    task_text = ""
    if code_task_spec is not None:
        if not code_task_spec.benchmark_command:
            raise SystemExit(
                "--code-task-config requires [benchmark].command for the canonical research-session."
            )
        command = _split_cli_command(code_task_spec.benchmark_command)
        if not command:
            raise SystemExit("The configured Code-Task benchmark command is empty.")
        if code_task_spec.task_file is not None:
            try:
                task_text = code_task_spec.task_file.read_text(encoding="utf-8")
            except OSError as exc:
                raise SystemExit(f"Could not read Code-Task task file: {exc}") from exc
        task = {
            "code_root": str(code_task_spec.code_root.resolve()),
            "workspace_mode": code_task_spec.workspace_mode,
            "approval_note": code_task_spec.approval_note,
            "max_repairs": execute_options.repair_rounds,
            "allowed_patterns": list(code_task_spec.edit_scope_allowed_patterns),
            "protected_patterns": list(code_task_spec.edit_scope_protected_patterns),
            "budget_profile": execute_options.budget_profile,
            "allow_large_edits": bool(
                execute_options.allow_large_edits or code_task_spec.allow_large_edits
            ),
        }
        execution = {
            "command": command,
            "cwd": str(code_task_spec.code_root.resolve()),
            "timeout_sec": timeout_sec,
            "label": args.label,
            "result_schema": _merge_result_schemas(
                code_task_spec.result_schema(), _experiment_result_schema(args)
            ),
            "code_task": task,
        }
        if code_task_baseline_policy in {"auto", "run"}:
            execution["baseline"] = {"command": command, "label": "baseline"}
        elif code_task_baseline_policy == "provided":
            raise SystemExit(
                "The canonical research-session does not import provided baseline metrics; "
                "use baseline_policy=run/auto or skip for this entrypoint."
            )
    elif command or execution_details.get("pairs"):
        execution = {
            "command": list(command),
            "cwd": str(Path(args.cwd).resolve()),
            "timeout_sec": timeout_sec,
            "label": args.label,
            "result_schema": _experiment_result_schema(args),
        }
    if execution_details:
        if execution is None:
            raise SystemExit("execution.protocol requires a command, pairs or code_task_config.")
        if execution_details.get("pairs") and code_task_spec is not None and code_task_baseline_policy not in {"auto", "run"}:
            raise SystemExit("Paired experiments require CodeTask baseline_policy=auto/run.")
        execution.update(execution_details)
        if execution_details.get("pairs"):
            execution.pop("baseline", None)  # Pair rows own both commands.
        from simple_ar.app.research_execution import execution_request
        execution_request(execution)
    request_text = args.topic.strip()
    experiment_requested = execution is not None or bool(outputs and "experiments" in outputs)
    if task_text.strip():
        request_text += "\n\n## Implementation task\n\n" + task_text.strip()
    config: dict[str, object] = {
        "research_max_documents": args.max_results,
        "report": {
            "mode": "experiment" if experiment_requested else "research_only",
            "template": args.report_template if experiment_requested else (
                "survey" if args.report_template == "experiment" else args.report_template
            ),
            "reviewer": args.report_reviewer,
            "max_review_iterations": args.max_review_iterations,
        },
    }
    if execution is not None:
        config["execution"] = execution
    if args.queries:
        config["research_queries"] = list(args.queries)
    if args.providers:
        config["research_sources"] = list(args.providers)
    for name in ("research_use_fulltext", "research_allow_pdf_download", "research_keep_raw_pdf"):
        value = getattr(args, name, None)
        if value is not None:
            config[name] = value
    assets = tuple(
        {
            "locator": str(Path(path)),
            "kind": "file",
            "role": "paper",
            "mutability": "read_only",
            "allowed_uses": ["read", "reference"],
        }
        for path in args.local_document
    )
    services = ResearchApplicationServices(
        llm_client=llm_client,
        max_results=args.max_results,
        max_chunks=args.max_chunks,
        idea_limit=args.idea_limit,
        cache_dir=Path(args.cache_dir).resolve() if args.cache_dir else None,
        input_base_dir=Path.cwd(),
        config=config,
        budget_limits={
            "llm_requests": getattr(args, "llm_requests", 40),
            "total_tokens": getattr(args, "total_tokens", 160_000),
            "process_invocations": args.process_invocations if getattr(args, "process_invocations", None) is not None else (8 if execution is not None else 0),
            "process_wall_seconds": args.process_wall_seconds if getattr(args, "process_wall_seconds", None) is not None else (max(60, timeout_sec * 8) if execution is not None else 0),
        },
        max_attempts=32 if code_task_spec is not None else 20,
    )
    brief = ResearchBrief(
        request_text=request_text,
        objective=args.topic.strip(),
        intents=("research", "experiment") if experiment_requested else ("research",),
        requested_outputs=tuple(outputs) if outputs is not None else ("experiments", "report") if execution is not None and report_requested
        else ("experiments",) if execution is not None
        else ("report",) if report_requested
        else ("summary",),
        asset_requests=assets,
    )
    try:
        if resume_root is None:
            app = create_session(brief, root=session_root, services=services)
        else:
            from simple_ar.app.research_application import load_session
            from dataclasses import replace
            app = load_session(session_root, services=replace(services, config={}))
            if app.brief.objective != brief.objective or app.brief.requested_outputs != brief.requested_outputs:
                raise ResearchApplicationError("Resume requires the same goal and outputs; it does not revise the research brief.")
            existing = app.services.config.get("execution")
            if execution is not None and existing is None:
                app.supply_execution(execution, task_text=task_text)
            elif execution is not None and execution != existing:
                raise ResearchApplicationError("Cannot replace an existing experiment configuration while resuming.")
            elif app.view().status != "completed":
                app.continue_session(reason="Resume from research-session; reuse persisted evidence and budgets.")
        view = app.view()
        for _ in range(services.max_attempts + 8):
            if view.next_action is None:
                view = app.advance(max_actions=1)
                break
            print_line(f"Action: {view.next_action}")
            view = app.advance(max_actions=1)
            print_line(
                f"Status: {view.status}; next: {view.next_action or 'none'}"
                + (f"; {view.status_reason}" if view.status_reason else "")
            )
            if view.status in {"completed", "paused", "blocked", "failed"}:
                break
        app.export_session()
    except (ResearchApplicationError, ResearchInputError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print_line(f"Research session: {view.session_root}")
    print_line(f"Status: {view.status}")
    print_line(f"Mode: {'llm' if llm_client is not None else 'deterministic'}")
    print_line(f"Next action: {view.next_action or 'none'}")
    print_line(f"Artifacts: {len(view.state_refs)}; attempts: {len(view.attempts)}")
    print_line(
        "Implementation: "
        + ("existing Code-Task backend" if code_task_spec is not None
           else "explicit command" if execution is not None
           else "preparation required" if outputs and "experiments" in outputs
           else "not requested (literature-only)")
    )
    for name in ("summary", "experiment", "matrix_results", "analysis", "report", "report_audit"):
        if name in view.state_refs:
            print_line(f"{name}: {view.session_root / view.state_refs[name].path}")
    _ensure_research_cli_success(
        view.status,
        operation="Research session",
        root=view.session_root,
    )


def _split_cli_command(value: str) -> list[str]:
    """Parse the legacy Code-Task command string into explicit argv."""

    parts = shlex.split(value, posix=os.name != "nt")
    if os.name == "nt":
        parts = [
            item[1:-1] if len(item) >= 2 and item[0] == item[-1] and item[0] in "\"'" else item
            for item in parts
        ]
    return parts


def _merge_result_schemas(
    configured: dict[str, object], cli_schema: dict[str, object]
) -> dict[str, object]:
    """Let explicit CLI metric flags override configured Code-Task defaults."""

    merged = dict(configured)
    if cli_schema.get("primary_metric"):
        merged["primary_metric"] = cli_schema["primary_metric"]
    if cli_schema.get("required_metrics"):
        merged["required_metrics"] = cli_schema["required_metrics"]
    if cli_schema.get("metric_directions"):
        merged["metric_directions"] = cli_schema["metric_directions"]
    return merged


def _print_research_session_continue(args: argparse.Namespace) -> None:
    """Retry an experiment through the canonical application lifecycle."""

    session_root = Path(args.session_root)
    from simple_ar.app.research_application import (
        ResearchApplicationError,
        load_session,
    )

    try:
        view = load_session(session_root).retry_experiment(
            command=tuple(args.command_argv or ()),
            cwd=Path(args.cwd),
            timeout_sec=args.timeout_sec,
            result_schema=_experiment_result_schema(args),
            label=args.label,
            parent_attempt_id=args.parent_attempt_id,
            reason="Retry the failed canonical experiment from research-session-continue.",
        )
    except (
        ResearchApplicationError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        raise SystemExit(str(exc)) from exc
    print_line(f"Research session: {view.session_root}")
    print_line(f"Status: {view.status}")
    print_line("Mode: deterministic")
    print_line(f"Recovery parent: {args.parent_attempt_id or 'latest experiment'}")
    if "experiment" in view.state_refs:
        print_line(f"Execution: {view.session_root / view.state_refs['experiment'].path}")
    print_line(f"Next action: {view.next_action or 'none'}")
    _ensure_research_cli_success(
        view.status,
        operation="Research session recovery",
        root=view.session_root,
        accepted={"completed", "running"},
    )
    return



def _print_research_session_migrate(args: argparse.Namespace) -> None:
    """Create and report a canonical successor for a legacy v1 session."""

    from simple_ar.app.session_migration import (
        SessionMigrationError,
        import_legacy_session,
    )

    if args.max_artifact_bytes < 1:
        raise SystemExit("--max-artifact-bytes must be positive.")
    try:
        result = import_legacy_session(
            args.source_root,
            args.destination_root,
            artifact_names=args.artifact_names,
            requested_outputs=args.requested_outputs or None,
            max_artifact_bytes=args.max_artifact_bytes,
        )
    except (
        FileExistsError,
        OSError,
        SessionMigrationError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        raise SystemExit(str(exc)) from exc
    print_line(f"Legacy session: {result.source_root}")
    print_line(f"Successor session: {result.destination_root}")
    print_line(f"Parent session: {result.parent_session}")
    print_line(f"Migration: {result.destination_root / result.migration_ref.path}")
    print_line(f"Imported artifacts: {len(result.imported)}")
    print_line(f"Skipped artifacts: {len(result.skipped)}")
    print_line(f"Historical budget: {result.budget_status}")


def _optional_research_llm_client(
    model: str | None,
    purpose: str,
    *, max_output_tokens: int | None = None,
) -> LLMClient | None:
    """Create the shared client only when a research command opts into LLMs."""

    if not model:
        return None
    try:
        return LLMClient.from_env(model=None if model == "env" else model, **({"max_output_tokens": max_output_tokens} if max_output_tokens is not None else {}))
    except LLMError as exc:
        raise SystemExit(f"Cannot enable LLM-backed {purpose}: {exc}") from exc


def _print_research_report(args: argparse.Namespace) -> None:
    """Generate a report from an existing research-session handoff."""

    if args.max_review_iterations < 0:
        raise SystemExit("--max-review-iterations cannot be negative.")
    session_root = Path(args.session_root)
    client = _optional_research_llm_client(args.model, "research report")
    if client is None:
        raise SystemExit("--model is required for research-report.")

    # Report generation belongs to the canonical application lifecycle.
    from simple_ar.app.research_application import (
        ResearchApplicationError,
        ResearchApplicationServices,
        load_session,
    )

    try:
        app = load_session(
            session_root,
            services=ResearchApplicationServices(
                llm_client=client,
                config={
                    "report": {
                        "mode": "experiment",
                        "template": args.template,
                        "reviewer": args.reviewer,
                        "max_review_iterations": args.max_review_iterations,
                    }
                },
            ),
        )
    except ResearchApplicationError as exc:
        raise SystemExit(
            f"Cannot continue this report session: {exc}. Historical sessions remain readable; "
            "new research uses research-session. No legacy workflow was executed."
        ) from exc
    try:
        view = app.view()
        if not ({"report", "paper", "full_paper"} & {
            item.strip().lower() for item in app.brief.requested_outputs
        }):
            view = app.request_report()
        elif view.status == "paused" and view.next_action is not None:
            view = app.continue_session(reason="Resume the canonical report lifecycle.")
        for _ in range(app.services.max_attempts + 8):
            if view.next_action is None:
                break
            print_line(f"Action: {view.next_action}")
            view = app.advance(max_actions=1)
            print_line(
                f"Status: {view.status}; next: {view.next_action or 'none'}"
                + (f"; {view.status_reason}" if view.status_reason else "")
            )
            if view.status in {"completed", "paused", "blocked", "failed"}:
                break
        app.export_session()
    except (ResearchApplicationError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print_line(f"Research report session: {view.session_root}")
    print_line(f"Status: {view.status}")
    for name in ("report", "report_audit"):
        if name in view.state_refs:
            print_line(f"{name}: {view.session_root / view.state_refs[name].path}")
    _ensure_research_cli_success(
        view.status,
        operation="Research report",
        root=view.session_root,
    )
    return




def _resolve_cli_path(value: str | Path) -> Path:
    """Resolve a user-supplied path without changing artifact-relative paths."""

    path = Path(value)
    return path if path.is_absolute() else Path.cwd() / path


def _ensure_research_cli_success(
    status: str,
    *,
    operation: str,
    root: Path,
    accepted: set[str] | None = None,
) -> None:
    """Make non-success application results visible to shell callers."""

    allowed = accepted or {"completed"}
    if status not in allowed:
        raise SystemExit(
            f"{operation} ended with status {status!r}; inspect {root}."
        )


def _load_code_task_spec_for_cli(config_path: Path):
    """Load one Code-Task spec consistently across research CLI consumers."""

    from simple_ar.experiment.code_task_bridge.spec import code_task_project_spec

    execute_options = load_code_task_execute_options(config_path=str(config_path))
    spec = code_task_project_spec(
        {
            "code_task_config": str(config_path),
            "safety_allow_large_edits": execute_options.allow_large_edits,
        }
    )
    return spec, execute_options


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






def _new_run_dir(output_root: Path, topic: str) -> Path:
    """Generate a unique timestamped directory path for a new run."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = _slugify(topic)
    return output_root / f"{timestamp}-{slug}"


def _slugify(text: str) -> str:
    """Convert text into a URL and folder-friendly slug string."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return slug[:50] or "research"




def _print_status(run_dir: Path) -> None:
    session_manifest_path = run_dir / "session_manifest.json"
    if session_manifest_path.exists():
        _print_research_session_status(run_dir)
        return

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


def _print_research_session_status(run_dir: Path) -> None:
    """Print the persisted checkpoint for a capability-oriented session."""

    from simple_ar.core.capabilities import CapabilityRegistry
    from simple_ar.core.session import SessionController

    try:
        controller = SessionController.load(
            run_dir,
            registry=CapabilityRegistry(),
        )
        snapshot = controller.status_snapshot()
        attempts = controller.list_attempts()
    except (FileNotFoundError, KeyError, OSError, TypeError, ValueError) as exc:
        raise SystemExit(f"Could not read research session {run_dir}: {exc}") from exc

    print_line(f"Session: {run_dir}")
    print_line(f"Session ID: {snapshot['session_id']}")
    print_line(f"Topic: {snapshot['topic']}")
    print_line(f"Profile: {snapshot.get('profile') or 'none'}")
    print_line(
        "Status: "
        f"{snapshot['status']} "
        f"(current={snapshot.get('current_attempt') or 'none'}, "
        f"attempts={snapshot['attempt_count']})"
    )

    # Historical sessions recorded next-step decisions. Display those facts
    # without executing the retired workflow or rewriting its manifest.
    decision = snapshot.get("last_decision")
    if (
        snapshot.get("status") == "running"
        and snapshot.get("running_attempts") == 0
        and isinstance(decision, dict)
        and decision.get("action") == "accept"
        and decision.get("result_status") == "completed"
        and decision.get("next_capability") == "report"
    ):
        print_line("Handoff: ready_for_report (next=report)")
    elif (
        snapshot.get("status") == "running"
        and snapshot.get("running_attempts") == 0
        and isinstance(decision, dict)
        and decision.get("action") in {"repair", "revise"}
        and decision.get("next_capability")
    ):
        print_line(
            "Continuation: explicit "
            f"{decision['action']} -> {decision['next_capability']}"
        )

    budget = snapshot["budget"]
    print_line(
        "Budget: "
        f"{budget['attempts']}/{budget['max_attempts']} attempts, "
        f"{budget['no_progress']}/{budget['max_no_progress']} no-progress"
    )

    print_line(
        "Attempt summary: "
        f"{snapshot['completed_attempts']} completed, "
        f"{snapshot['failed_attempts']} failed, "
        f"{snapshot['blocked_attempts']} blocked, "
        f"{snapshot['running_attempts']} running"
    )
    print_line("Attempts:")
    if not attempts:
        print_line("- none")
    else:
        for attempt in attempts:
            capability = attempt.capability or "unknown"
            parent = f", parent={attempt.parent_attempt}" if attempt.parent_attempt else ""
            print_line(
                f"- {attempt.attempt_id}: {capability} {attempt.status}{parent}"
            )

    if isinstance(decision, dict):
        target = decision.get("next_capability") or "stop"
        print_line(
            "Last decision: "
            f"{decision.get('action', 'unknown')} -> {target} "
            f"({decision.get('result_status', 'unknown')})"
        )


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
            kind=args.kind,
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
            env_mode=args.env_mode,
            python_executable=args.python_executable,
            primary_metric=args.primary_metric,
            metric_directions=args.metric_direction or [],
        )
    except CodeTaskConfigError as exc:
        raise SystemExit(str(exc)) from exc
    code_root = Path(options.code_root) if options.code_root else None
    if options.task_file is None:
        raise SystemExit("Missing task file. Pass --task-file or set [code_task].task_file.")
    task_file = Path(options.task_file)
    name = options.name or (
        f"code-task-{code_root.resolve().name}" if code_root is not None else "greenfield-code-task"
    )
    run_dir = _new_run_dir(Path(options.output_root), name)
    try:
        result = initialize_code_task(
            run_dir=run_dir,
            code_root=code_root,
            task_file=task_file,
            kind=options.kind,
            benchmark_command=options.benchmark_command,
            max_file_bytes=options.max_file_bytes,
            workspace_mode=options.workspace_mode,
            workspace_include=options.workspace_include,
            workspace_exclude=options.workspace_exclude,
            workspace_reuse_source_venv=options.workspace_reuse_source_venv,
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
                "- Pass --task-file path/to/task.md, or set [code_task].task_file in TOML.",
                "- Windows-style backslashes are accepted, but forward slashes are portable across Linux/macOS/Windows.",
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
                "- If you use git_worktree, code_root may be the git repository root or a project subdirectory inside it.",
            ]
        )
    elif isinstance(exc, WorkspaceModeError) and workspace_mode == "git_worktree":
        lines.extend(
            [
                "",
                "git_worktree quick checklist:",
                "- code_root should be inside the baseline git repository.",
                "- The repository needs at least one local commit.",
                "- GitHub or any remote is not required.",
                "- If code_root is a monorepo subdirectory, SimpleAutoResearch will use the matching worktree subdirectory as project root.",
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
    repair_rounds = args.repair_rounds if args.repair_rounds is not None else options.repair_rounds
    planning_review_rounds = (
        args.planning_review_rounds
        if args.planning_review_rounds is not None
        else options.planning_review_rounds
    )
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
    planning_mode = args.planning_mode or options.planning_mode
    llm_retry_attempts = args.llm_retry_attempts or options.llm_retry_attempts
    baseline_policy = args.baseline_policy or options.baseline_policy
    baseline_metrics_file = args.baseline_metrics_file or options.baseline_metrics_file
    review_gate = args.review_gate or options.review_gate
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
            writer_model=options.writer_model,
            reviewer_model=options.reviewer_model,
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
            baseline_policy=baseline_policy,
            baseline_metrics_file=baseline_metrics_file,
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
            planning_mode=planning_mode,
            planning_review_rounds=planning_review_rounds,
            llm_retry_attempts=llm_retry_attempts,
            repair_rounds=repair_rounds,
            planning_snapshot_from=args.reuse_planning_from,
            review_gate=review_gate,
            budget_profile=options.budget_profile,
            edit_budget_overrides=options.edit_budget_overrides,
            max_batches=options.max_batches,
            cost_cap_usd=options.cost_cap_usd,
            max_files=max_files,
            max_source_chars_per_file=max_source_chars,
            max_generated_lines=options.max_generated_lines,
            implementation_provider=options.implementation_provider,
            implementation_agent_mode=options.implementation_agent_mode,
            implementation_allow_external_agent=options.implementation_allow_external_agent,
            implementation_agent_model=options.implementation_agent_model,
            implementation_agent_binary=options.implementation_agent_binary,
            implementation_agent_args=options.implementation_agent_args,
            implementation_agent_timeout_sec=options.implementation_agent_timeout_sec,
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
        if (step in {"work-plan", "batch"} and to_step not in {"work-plan", "batch"}
                and not (Path(args.run_dir) / "code_task/work_plan.json").is_file()):
            continue
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
