"""Guided input adapter; generated TOML enters the canonical session CLI.

This module does not run agents, infer execution authority, or own sessions.
Only implemented entry paths are offered. Advanced users can edit the generated
files and run the same research-session command directly.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import sys

from simple_ar.app.session_roots import new_research_session_root
from simple_ar.cli.research_config import data_options_supplied, data_settings, setup_option_contract
from simple_ar.core.console import print_line
from simple_ar.result_analysis.table import TABLE_CHOICES, TABLE_MODE_DESCRIPTIONS, TABLE_PLOT_DESCRIPTIONS, TableSpec


FUNCTION_LABELS = {
    "survey": "Direction survey / 方向调研 — compare sources and deliver a report",
    "bug_fix": "Code repair / 修改代码 — project, allowed edits and validation required",
    "reproduction": "Finite reproduction / 有限复现 — confirmed command and environment; optional task venv",
    "writing": "Material writing / 材料写作 — use notes, drafts or an analysis package",
    "data_analysis": "Data and plots / 分析绘图 — deterministic tables (no API needed); --scripted uses model and code execution",
    "figure": "Editable method figure / 方法图 — describe components and relationships; deliver code, SVG and PNG",
}


def _document_input(value: str) -> Path | str:
    """Keep URL spelling intact; retain the existing local Path contract."""
    return value if value.startswith(("https://", "http://")) else Path(value)


def add_data_options(parser: argparse.ArgumentParser) -> None:
    defaults = TableSpec.defaults()
    parser.add_argument("--data-file", type=Path, help="Data analysis: UTF-8 CSV/TSV or JSON records.")
    parser.add_argument("--data-attribution", default="", help="Optional user-declared data source/credit/version. Preserved in analysis and writing; not downloaded or independently verified.")
    parser.add_argument("--value-column", action="append", default=[], help="Explicit numeric column; repeat for separate metrics or selected matrix/coordinate columns.")
    parser.add_argument("--group-column", default="", help="Category column: unique row labels for values bars/heatmaps, or distinct coordinate series for line/scatter.")
    parser.add_argument("--observation-unit", default="", help="What one row represents, e.g. one independent run or one supplied summary.")
    parser.add_argument("--value-unit", default="", help="Unit shared by selected value columns; omitted is recorded as unknown.")
    parser.add_argument("--paired-baseline", default="", help="Explicit same-row paired comparison: selected baseline column; other selected observation columns are candidates with a common quantity/unit.")
    parser.add_argument("--data-association", choices=TABLE_CHOICES["association"], default=defaults["association"],
                        help="Opt-in descriptive Pearson r for supplied line/scatter x/y pairs in each group; not paired differences, significance or causal inference.")
    parser.add_argument("--data-mode", choices=TABLE_CHOICES["mode"], default=defaults["mode"],
                        help="; ".join(f"{mode}: {meaning}" for mode, meaning in TABLE_MODE_DESCRIPTIONS.items()))
    parser.add_argument("--data-missing", choices=TABLE_CHOICES["missing"], default=defaults["missing"])
    parser.add_argument("--figure-width", choices=TABLE_CHOICES["width"], default=defaults["width"], help="Generic 3.5/7-inch figure target, not a conference-specific size.")
    parser.add_argument("--data-max-mb", type=int, default=defaults["max_mb"], help="Physical input size limit, MiB.")
    parser.add_argument("--data-max-figures", type=int, default=defaults["max_figures"], help="Physical SVG page limit; overflow fails without dropping categories.")
    parser.add_argument("--data-plot", choices=TABLE_CHOICES["plot"], default=defaults["plot"],
                        help="; ".join(f"{plot}: {meaning}" for plot, meaning in TABLE_PLOT_DESCRIPTIONS.items()))
    parser.add_argument("--x-column", default="", help="line/scatter: explicit numeric x column, distinct from value columns.")
    parser.add_argument("--x-unit", default="", help="Unit of the x coordinate; omission is recorded as unknown.")
    parser.add_argument("--data-max-points", type=int, default=defaults["max_points"], help="Physical points per coordinate figure; excess fails without sampling.")
    parser.add_argument("--series-layout", choices=TABLE_CHOICES["series_layout"], default=defaults["series_layout"],
                        help="line/scatter: separate axes by default, or explicitly share axes for columns with a common declared unit.")


def add_start_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("start", help="Set up a survey, code fix, prepared reproduction, writing or descriptive data analysis without TOML.")
    parser.add_argument("--kind", choices=tuple(FUNCTION_LABELS))
    parser.add_argument("--from-session", type=Path, help="Reuse registered results or a validated project from an existing session.")
    parser.add_argument("--reuse", action="append", default=[], choices=("data_analysis", "report", "summary", "code_analysis", "code_project"),
                        help="Result to reuse as writing material; repeatable. code_project also supports isolated code/figure revision or repeating a saved reproduction protocol. Inspect with results SESSION.")
    parser.add_argument("--chat", action="store_true", help="Opt-in model-assisted natural-language setup; confirms semantic choices before ordinary task setup. Requires a terminal and model connection.")
    parser.add_argument("--resume-setup", type=Path, help="Resume a saved natural-language setup directory, retaining replies and setup model accounting; not execution recovery.")
    parser.add_argument("--with-report", action="store_true", help="Data analysis: also write and review a report from the saved analysis and figures in the same session; requires a model. Default: analysis only, no task API calls.")
    parser.add_argument("--scripted", action="store_true", help="Data analysis: generate and execute an isolated analysis/plot project from the goal, instead of using a preset. Requires model and ordinary execution confirmation; not an OS sandbox.")
    add_data_options(parser)
    parser.add_argument("--data-path", action="append", default=[], type=Path,
                        help="Reproduction/code fix: existing data file/directory, repeatable. Project inputs enter isolated code workspaces; external paths never rewrite execution commands.")
    parser.add_argument("--goal", help="Describe the question or desired fix in your own words.")
    parser.add_argument("--asset-max-mb", type=int,
                        help="Chat setup: explicit per-project/data download capacity in MiB (default 20); project ZIP expansion is limited to four times this. Does not grant download or execution permission.")
    parser.add_argument("--document", action="append", default=[], type=_document_input,
                        help="Literature tasks: local paper/reference; reproduction --chat also accepts a public HTTPS paper URL, acquired only after confirmation. Use --material for notes/drafts/analysis packages.")
    parser.add_argument("--material", action="append", default=[], type=Path, help="Writing: notes, drafts, ordinary JSON or a table_analysis.v1 analysis package. Ordinary materials are unverified; analysis packages recheck copied data. Not a bibliographic paper.")
    parser.add_argument("--template", help="Writing: built-in report template or Markdown template path; default material_report. Use experiment for an honest paper-style draft.")
    parser.add_argument("--sources", choices=setup_option_contract("survey")["sources"]["allowed_values"], help="Use only supplied documents, or allow online search.")
    parser.add_argument("--fulltext", action="store_true", help="Allow online survey full text, or PDF download/retention for explicitly confirmed reproduction --chat paper URLs; otherwise PDF permission is requested separately.")
    parser.add_argument("--max-cited-sources", type=int, help="Optional maximum number of distinct sources cited in the final report.")
    parser.add_argument("--project", type=Path, help="Existing project: bug_fix edits an isolated copy; reproduction inspects it, with optional --allow/--validate for isolated result-adapter preparation.")
    parser.add_argument("--validate", help="Explicit code validation command; reproduction with --allow uses this independent check before formal measurement.")
    parser.add_argument("--allow", action="append", default=[], help="Editable project-relative path/glob; repeat as needed.")
    parser.add_argument("--project-python", type=Path,
                        help="Existing Python for code repair/reproduction; preserves a venv entry. With reproduction --environment venv, selects the base Python for the explicitly authorized task environment.")
    parser.add_argument("--hypothesis", help="Prepared reproduction: the published conclusion to check.")
    parser.add_argument("--dataset", help="Prepared reproduction: data and any accepted adaptation.")
    parser.add_argument("--expected-outcome", help="Prepared reproduction: comparison criteria, not invented results.")
    parser.add_argument("--metric", action="append", default=[], help="Prepared reproduction: metric emitted by the command; repeat as needed.")
    parser.add_argument("--output-files", type=json.loads, default={}, help='Reproduction: JSON name-to-relative-file mapping written under SIMPLE_AR_OUTPUT_DIR, for example {"raw":"measurements.json"}. No directory scan or stdout path inference.')
    parser.add_argument("--metric-sources", type=json.loads, default={}, help='Reproduction: explicit JSON/CSV metric selectors for registered outputs; for example {"coverage":{"output":"raw","path":["coverage"]}}.')
    parser.add_argument("--cwd", type=Path, help="Prepared reproduction: existing execution directory; defaults to the current directory.")
    parser.add_argument("--timeout-sec", type=int, help="Prepared reproduction: one process limit in seconds; defaults to 300.")
    parser.add_argument("--environment", choices=setup_option_contract("reproduction")["environment"]["allowed_values"], default="current",
                        help="Reproduction: use the current environment, or explicitly create a task-local venv and install selected requirements before the declared command.")
    parser.add_argument("--requirements", action="append", default=[],
                        help="Venv: project-relative requirements file; repeatable. Defaults to requirements.txt when present, otherwise a bare venv. Installation can run build code; not an OS sandbox.")
    parser.add_argument("--install-project", action="store_true",
                        help="Reproduction venv: also install the execution project as a package, not editable. Requires a packaging declaration; may run build code/network and write source build metadata. Default: no project install.")
    parser.add_argument("--check-argv", type=json.loads, help="Reproduction: explicitly authorized short check argv as JSON, run before measurement. No inferred command or scientific metrics; uses the preparation timeout.")
    parser.add_argument("--model", default="env", help="Default: use the configured model catalog route, or legacy .env connection.")
    parser.add_argument("--interaction", choices=("assisted", "checkpoints", "autonomous"), default="checkpoints")
    parser.add_argument("--output-root", type=Path, default=Path("runs/assistant"))
    parser.add_argument("--prepare-only", action="store_true", help="Save inspectable input files without model or process calls.")
    parser.add_argument("--yes", action="store_true", help="Accept the displayed task summary; does not broaden execution authority.")
    parser.add_argument("--command", dest="run_argv", nargs=argparse.REMAINDER, help="Prepared reproduction: explicit argv. Put this option last; no shell interpretation.")


def _answer(prompt: str, current: str | None, *, interactive: bool) -> str:
    if current and current.strip():
        return current.strip()
    if not interactive:
        raise ValueError(f"Missing input: {prompt}. Supply the corresponding option or run start in a terminal.")
    answer = input(prompt + ": ").strip()
    if not answer:
        raise ValueError(f"A value is required: {prompt}")
    return answer


def _quote(value: str) -> str:
    # JSON strings are valid TOML basic strings for these ordinary text values.
    return json.dumps(value, ensure_ascii=False)


def _array(values: list[str]) -> str:
    return "[" + ", ".join(_quote(value) for value in values) + "]"


def _inline_value(value) -> str:
    """Serialize validated selectors (tables, lists and ordinary scalars)."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_quote(key)} = {_inline_value(item)}" for key, item in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_inline_value(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _command(arguments: list[str]) -> str:
    if sys.platform == "win32":
        import subprocess
        return subprocess.list2cmdline(arguments)
    return shlex.join(arguments)


def _literature_rows(args: argparse.Namespace, sources: str, documents: list[Path]) -> list[str]:
    return [f"materials_only = {'true' if sources == 'materials' else 'false'}",
            f"use_fulltext = {'true' if args.fulltext else 'false'}",
            f"allow_pdf_download = {'true' if args.fulltext else 'false'}",
            f"keep_raw_pdf = {'true' if args.fulltext else 'false'}",
            *(["max_fulltext_documents = 6", "max_pdf_mb = 20"] if args.fulltext else []),
            "", "[assets]", f"papers = {_array([str(path) for path in documents])}"]


def _code_task_files(goal: str, project: Path, validation: str, allowed: list[str],
                     python_executable: Path | None = None, *, generated_script: bool = False,
                     protected: list[str] | None = None, edit_policy: dict | None = None,
                     timeout_sec: int = 300) -> dict[str, str]:
    environment = ('mode = "current"\n' if python_executable is None else
                   'mode = "external"\n' + f'python = {_quote(str(python_executable))}\n')
    policy = {"budget_profile": "large", "allow_large_edits": True} if generated_script else (edit_policy or {})
    policy_rows = ''.join(f'{key} = {str(value).lower() if type(value) is bool else _quote(value)}\n'
                          for key, value in policy.items() if value is not None)
    return {
        "task.md": goal + "\n",
        "code_task.toml": "[code_task]\n" + f"code_root = {_quote(str(project))}\n"
        'task_file = "{config_dir}/task.md"\noutput_root = "{config_dir}/sessions"\n'
        '\n[workspace]\nmode = "auto"\n\n[environment]\n' + environment
        + "\n[edit_scope]\n" + f"allowed_patterns = {_array(allowed)}\n"
        + 'protected_patterns = ' + _array(list(dict.fromkeys([
            "tests/**", "**/tests/**", "test_*.py", "**/test_*.py", ".env", ".env.*", *(protected or [])]))) + '\n'
        + "\n[benchmark]\n" + f"command = {_quote(validation)}\n"
        + f'\n[execute]\nuse_llm = true\nbaseline_policy = "skip"\ntimeout_sec = {timeout_sec}\nrepair_rounds = 1\n'
        + policy_rows,
    }


def _reproduction_rows(args: argparse.Namespace, *, interactive: bool) -> list[str]:
    from simple_ar.experiment.execution.outputs import metric_sources, output_files
    attachments = output_files({"output_files": args.output_files})
    selectors = metric_sources({"output_files": attachments, "metric_sources": args.metric_sources})
    protocol = {key: _answer(prompt, getattr(args, key), interactive=interactive) for key, prompt in (
        ("hypothesis", "Published conclusion / 要检查的论文结论"),
        ("dataset", "Data and adaptation / 数据及条件偏离"),
        ("expected_outcome", "Comparison criteria / 预期及判断标准"),
    )}
    argv = args.run_argv
    if argv is None:
        raw = _answer('Command argv as JSON / 命令参数列表（如 ["python", "run.py"]）', None, interactive=interactive)
        argv = json.loads(raw)
    if not isinstance(argv, list) or not argv or any(not isinstance(item, str) or not item.strip() for item in argv):
        raise ValueError("Reproduction requires a nonempty command argument list of nonempty strings.")
    python = getattr(args, "project_python", None)
    if python is not None and args.environment == "current":
        from simple_ar.code_task.execution.environment import resolve_code_task_command
        argv = resolve_code_task_command(argv, env_mode="external", python_executable=python)
        if getattr(args, "check_argv", None):
            args.check_argv = resolve_code_task_command(args.check_argv, env_mode="external", python_executable=python)
    args.run_argv = argv
    metrics = args.metric or list(selectors) or [_answer("Measured metric / stdout 或登记文件中的指标名", None, interactive=interactive)]
    if any(not value.strip() for value in metrics) or len(set(metrics)) != len(metrics):
        raise ValueError("Metric names must be nonempty and distinct.")
    cwd = (args.cwd or args.project or Path.cwd()).expanduser().resolve()
    if not cwd.is_dir():
        raise ValueError(f"Execution directory not found: {cwd}")
    timeout = args.timeout_sec if args.timeout_sec is not None else 300
    if timeout < 1:
        raise ValueError("--timeout-sec must be positive.")
    environment_rows = []
    setup_steps = 0
    if getattr(args, "check_argv", None) is not None and not args.check_argv:
        raise ValueError("--check-argv must name a nonempty check command; omit it to skip the check.")
    if args.environment == "venv" or getattr(args, "check_argv", None) is not None:
        from simple_ar.research.project_environment import environment_profile
        requirements = (args.requirements or (["requirements.txt"] if (cwd / "requirements.txt").is_file() else [])) if args.environment == "venv" else []
        profile = environment_profile({"mode": args.environment, "requirements": requirements,
                                       **({"python_executable": str(python)} if python is not None and args.environment == "venv" else {}),
                                       "install_project": args.install_project, "timeout_sec": timeout,
                                       "check_command": getattr(args, "check_argv", None) or []}, project=cwd)
        if args.environment == "venv" and argv[0] not in {"python", "python3", "python.exe", "python3.exe"}:
            raise ValueError("Task venv requires a python/python3 command alias; do not silently replace an explicit interpreter or launcher.")
        setup_steps = (2 + bool(profile["requirements"] or profile["install_project"]) if args.environment == "venv" else 0) + bool(profile.get("check_command"))
        environment_rows = ["", "[execution.environment]", f'mode = "{args.environment}"',
                            f"requirements = {_array(profile['requirements'])}",
                            f"install_project = {'true' if profile['install_project'] else 'false'}", f"timeout_sec = {timeout}"]
        if profile.get("check_command"):
            environment_rows.append(f"check_command = {_array(profile['check_command'])}")
            print_line(f"Preparation check: {_command(profile['check_command'])}; failure or timeout stops measurement. Check outputs are not scientific results.")
        if args.environment == "venv":
            environment_rows.append(f"python_executable = {_quote(profile['python_executable'])}")
        print_line(f"Preparation: {args.environment}; requirements {profile['requirements']}; install project: {profile['install_project']}. {setup_steps} invocations, each <= {timeout}s. Venv installation may run build code/network; current mode does not install. Not an OS sandbox.")
    print_line("\n".join(f"{key}: {value}" for key, value in protocol.items()))
    print_line(f"Execution: {_command(argv)}\nDirectory: {cwd}\nOne invocation; timeout: {timeout}s; metrics: {', '.join(metrics)}")
    if not setup_steps:
        print_line("Uses the current environment, not an OS sandbox. No automatic installs, baseline training, or method changes are authorized.")
    adaptation = bool(args.validate or args.allow)
    initial_files = []
    state = getattr(args, "_setup_state", {})
    if state.get("status") == "accepted" and state.get("proposals"):
        proposal = state["proposals"][-1]
        creation = (proposal.get("execution_proposal") or {}).get("code_preparation")
        if creation:
            if (proposal.get("kind") != "reproduction" or not adaptation
                    or args.allow != creation["allowed_paths"]
                    or shlex.split(args.validate) != creation["validation_argv"]):
                raise ValueError("Confirmed adapter creation scope/checker no longer matches setup.")
            initial_files = list(creation["allowed_paths"])
            # Like a requested analysis/figure script, a confirmed new adapter
            # is whole-file creation, not a small patch to author algorithms.
            # Reuse the existing bounded script edit policy; do not broaden
            # editable paths or apply this to ordinary repository repairs.
            args._generated_script = True
            print_line("Authorized new adapter creation uses the bounded script edit policy; author files remain read-only.")
    invocations = (3 if adaptation else 1) + setup_steps
    return ["", "[execution]", f"command = {_array(argv)}", f"cwd = {_quote(str(cwd))}",
            *(['code_task_config = "code_task.toml"'] if adaptation else []),
            *([f"initial_files = {_array(initial_files)}"] if initial_files else []),
            f"timeout_sec = {timeout}", 'baseline_policy = "skip"', f"primary_metric = {_quote(metrics[0])}",
            f"metrics = {_array(metrics)}",
            *(["output_files = {" + ", ".join(f"{_quote(name)} = {_quote(path)}" for name, path in attachments.items()) + "}"] if attachments else []),
            *(["metric_sources = " + _inline_value(selectors)] if selectors else []),
            *environment_rows, "", "[execution.protocol]",
            *[f"{key} = {_quote(value)}" for key, value in protocol.items()],
            f"metrics = {_array(metrics)}", "", "[budget]", f"process_invocations = {invocations}",
            f"process_wall_seconds = {timeout * invocations}"]


def _session_project(session) -> tuple[Path, dict, dict] | None:
    """Resolve a validated owned workspace and its original scope/verification."""
    from simple_ar.code_task.runtime.state import code_task_paths, load_code_task_manifest
    ref = session.manifest.state_refs.get("implementation")
    if ref is None or ref.status != "available":
        return None
    result = session.store.read_json(ref)
    if result.get("status") != "validated" or not result.get("workspace_dir") or not result.get("code_task_run_dir"):
        return None
    workspace, run = Path(result["workspace_dir"]).resolve(), Path(result["code_task_run_dir"]).resolve()
    if (not workspace.is_relative_to(session.store.root) or not run.is_relative_to(session.store.root)
            or not workspace.is_dir() or code_task_paths(run).workspace_dir.resolve() != workspace):
        # A retired or relocated workspace is not an editable offering. Its
        # absence must not block independently registered portable deliveries.
        return None
    manifest = load_code_task_manifest(run)
    if not manifest.get("benchmark", {}).get("command") or not manifest.get("edit_scope", {}).get("allowed_patterns"):
        return None
    policy = {}
    for prepared in session.manifest.state_refs.values():
        if prepared.kind != "prepared_execution" or prepared.status != "available":
            continue
        execution = session.store.read_json(prepared).get("execution", {})
        if execution.get("cwd") and Path(execution["cwd"]).resolve() == workspace:
            task = execution.get("code_task", {})
            policy = {key: task[key] for key in ("budget_profile", "allow_large_edits") if key in task}
            break
    return workspace, manifest, policy


def session_materials(root: Path) -> dict[str, list[Path]]:
    """Resolve current registered deliveries, not arbitrary historical files."""
    from simple_ar.core.capabilities import CapabilityRegistry
    from simple_ar.core.session import SessionController
    session = SessionController.load(root, registry=CapabilityRegistry())
    available: dict[str, list[Path]] = {}
    project = _session_project(session)
    if project is not None:
        available["code_project"] = [project[0]]
    for name in ("data_analysis", "report", "summary"):
        ref = session.manifest.state_refs.get(name)
        if ref is None or ref.status != "available":
            continue
        path = session.store.resolve(ref).resolve()
        if not path.is_relative_to(session.store.root) or not path.is_file():
            raise ValueError(f"Registered {name} result is missing or outside its session.")
        if name == "report":
            body = path.parent / "report_body.md"
            if not body.is_file():
                continue
            # Keep the draft and its evidence together; the draft alone is not
            # independently verified literature or a measured result.
            paths = [body]
            paths.extend(p for p in (path.parent / "report_experiment_evidence.json",
                                    path.parent / "citation_map.json") if p.is_file())
            documents = session.manifest.state_refs.get("documents")
            # Reuse the evidence actually consumed by this draft, including
            # later reading/linked documents, not the initial ingest alone.
            writer = session.manifest.state_refs.get("writer")
            if writer is not None and writer.status == "available":
                payload = session.store.read_json(writer)
                snapshot = payload.get("input_snapshot", {}).get("path")
                if snapshot:
                    snapshot_ref = session.store.ref(Path(writer.path).parent / snapshot)
                    sources = session.store.read_json(snapshot_ref).get("sources", [])
                    bundles = [row for row in sources if row.get("schema") == "document_bundle.v1"]
                    if len(bundles) > 1:
                        raise ValueError("Registered report has ambiguous source bundles.")
                    if bundles:
                        from simple_ar.core.capabilities import ArtifactRef
                        documents = ArtifactRef.from_dict(bundles[0])
            if documents is not None and documents.status == "available":
                retained = session.store.resolve(documents).resolve()
                if not retained.is_relative_to(session.store.root) or not retained.is_file():
                    raise ValueError("Registered report sources are missing or outside their session.")
                paths.append(retained)
        else:
            paths = [path]
        available[name] = paths
    implementation = session.manifest.state_refs.get("implementation")
    if implementation is not None and implementation.status == "available":
        payload = session.store.read_json(implementation)
        delivery = payload.get("artifact_refs", {}).get("code_analysis")
        if payload.get("status") == "validated" and isinstance(delivery, dict):
            path = (session.store.resolve(implementation).parent / delivery["path"]).resolve()
            if not path.is_relative_to(session.store.root) or not path.is_file():
                raise ValueError("Registered code analysis package is missing or outside its session.")
            available["code_analysis"] = [path]
            return available
        if payload.get("status") == "validated" and payload.get("workspace_dir"):
            workspace = Path(payload["workspace_dir"]).resolve()
            files = [workspace / "outputs/report.md", workspace / "outputs/results.json"]
            if workspace.is_relative_to(session.store.root) and all(path.is_file() for path in files):
                available["code_analysis"] = files
    return available


def reuse_session_materials(args: argparse.Namespace) -> None:
    root = getattr(args, "from_session", None)
    selected = getattr(args, "reuse", [])
    if root is None:
        if selected:
            raise ValueError("--reuse requires --from-session.")
        return
    if not selected:
        raise ValueError("Select --reuse data_analysis, code_analysis, code_project, report or summary; inspect with results SESSION.")
    if "code_project" in selected:
        if selected != ["code_project"] or args.kind not in {None, "bug_fix", "figure", "data_analysis", "reproduction"}:
            raise ValueError("Reuse one code_project independently for code/figure revision; use code_analysis for writing.")
        if args.project or args.validate or args.allow:
            raise ValueError("Project reuse retains its registered scope and verification; do not override them in the same selection.")
        from simple_ar.core.capabilities import CapabilityRegistry
        from simple_ar.core.session import SessionController
        session = SessionController.load(root, registry=CapabilityRegistry())
        project = _session_project(session)
        if project is None:
            raise ValueError("No current reusable code_project; complete and validate the original project first.")
        workspace, manifest, policy = project
        reproduction = args.kind == "reproduction"
        if reproduction:
            if any((args.run_argv is not None, args.cwd, args.hypothesis, args.dataset,
                    args.expected_outcome, args.metric, args.output_files, args.metric_sources,
                    args.environment != "current", args.requirements, args.install_project,
                    args.check_argv is not None, args.project_python)):
                raise ValueError("Reusing reproduction retains its measured protocol, checker and interpreter; start a separate task to change execution conditions.")
            prepared = session.manifest.state_refs.get("preparation")
            execution = session.store.read_json(prepared).get("execution", {}) if prepared and prepared.status == "available" else {}
            if (not isinstance(execution, dict) or not execution.get("command")
                    or Path(execution.get("cwd", "")).resolve() != workspace
                    or not isinstance(execution.get("protocol"), dict)
                    or any(not execution["protocol"].get(key) for key in ("hypothesis", "dataset", "expected_outcome"))):
                raise ValueError("No prepared reproduction protocol for this validated project.")
            if (execution.get("baseline_policy", "skip") != "skip"
                    or any(execution.get(key) for key in ("pairs", "baseline", "seeds", "seed_count", "seed_flag"))
                    or set(execution["protocol"]) - {"hypothesis", "dataset", "expected_outcome", "metrics"}):
                raise ValueError("This entry repeats single-command finite protocols only; resume the original session for paired or extended protocols.")
            args.run_argv = list(execution["command"])
            args.cwd = workspace
            for key in ("hypothesis", "dataset", "expected_outcome"):
                setattr(args, key, execution["protocol"][key])
            from simple_ar.app.research_execution import execution_request
            schema = execution_request(execution).result_schema
            args.metric = list(schema.get("required_metrics", []))
            args.output_files = dict(schema.get("output_files", {}))
            args.metric_sources = dict(schema.get("metric_sources", {}))
            primary = schema.get("primary_metric")
            if primary in args.metric:
                args.metric = [primary, *[name for name in args.metric if name != primary]]
            if args.timeout_sec is None:
                args.timeout_sec = execution.get("timeout_sec", 300)
        args.kind, args.scripted, args.project = "reproduction" if reproduction else "bug_fix", False, workspace
        args.validate = manifest["benchmark"]["command"]
        args.allow = list(manifest["edit_scope"]["allowed_patterns"])
        args._reuse_protected = list(manifest["edit_scope"].get("protected_patterns", []))
        args._reuse_edit_policy = policy
        python = manifest.get("environment", {}).get("policy", {}).get("python_executable")
        if reproduction:
            python = execution.get("code_task", {}).get("python_executable") or python
        if python and not args.project_python:
            args.project_python = Path(python)
        if (workspace / "data").is_dir():
            args.data_path = list(dict.fromkeys([*args.data_path, workspace / "data"]))
        args.from_session, args.reuse = None, []
        print_line("Reuse a validated project in a new isolated task with its registered edit scope and checker." +
                   (" Repeat the saved reproduction protocol; no environment installation is requested." if reproduction else ""))
        return
    if args.kind not in {None, "writing"}:
        raise ValueError("Registered deliveries currently enter writing; use their source data for a new analysis.")
    available = session_materials(root)
    for name in selected:
        if name not in available:
            raise ValueError(f"No current reusable {name} delivery in {root}.")
        args.material.extend(available[name])
    args.material = list(dict.fromkeys(args.material))
    args.kind = "writing"
    # Idempotent when called both before chat and by the ordinary entry point.
    args.from_session = None
    args.reuse = []


def prepare_start(args: argparse.Namespace) -> Path | None:
    """Collect bounded user choices and write one canonical, editable input set."""
    if getattr(args, "asset_max_mb", None) is not None and not hasattr(args, "_setup_state"):
        raise ValueError("--asset-max-mb applies to confirmed --chat downloads, not ordinary task preparation.")
    reuse_session_materials(args)
    interactive = sys.stdin.isatty()
    print_line("Available now: survey, bug_fix, prepared reproduction, material-based writing, data_analysis, editable figure.")
    print_line("Concept images use the image command; autonomous reproduction preparation is not yet offered.")
    if interactive and args.kind is None:
        for index, (name, label) in enumerate(FUNCTION_LABELS.items(), 1):
            print_line(f"{index}. {label} ({name})")
    kind = _answer("Function number or name / 功能编号或名称", args.kind, interactive=interactive)
    numbers = {str(index): name for index, name in enumerate(FUNCTION_LABELS, 1)}
    kind = numbers.get(kind, kind)
    if kind not in FUNCTION_LABELS:
        raise ValueError("Choose a displayed function number or name.")
    if args.scripted or kind == "figure":
        if args.scripted and kind != "data_analysis":
            raise ValueError("--scripted requires --kind data_analysis.")
        if args.with_report or args.project or args.validate or args.allow or args.document or args.material:
            raise ValueError("Scripted analysis takes data and a goal; it delivers its own explanation. Reuse the resulting report for later writing.")
        from copy import copy
        from simple_ar.cli.parser import build_parser
        from simple_ar.cli.research_config import FIELDS
        from simple_ar.result_analysis.script_project import SCRIPT_EDIT_PATTERNS, prepare_script_project
        goal = _answer("Goal / 目标", args.goal, interactive=interactive)
        data = args.data_file
        if kind == "data_analysis" and data is None:
            data = Path(_answer("Data file / 数据路径", None, interactive=interactive))
        root = getattr(args, "_start_root", None) or new_research_session_root(args.output_root, goal)
        project, validation = prepare_script_project(Path(root) / "source", data=data, goal=goal, diagram=kind == "figure")
        forwarded = copy(args)
        defaults = build_parser().parse_args(["start"])
        for destination, _ in FIELDS["analysis"].values():
            setattr(forwarded, destination, getattr(defaults, destination))
        forwarded.kind = "bug_fix"
        forwarded.scripted = False
        forwarded.goal = goal
        forwarded.project = project
        forwarded.validate = _command(list(validation))
        forwarded.allow = list(SCRIPT_EDIT_PATTERNS)
        forwarded.data_path = [project / "data"]
        forwarded._code_task_text = (project / "README.md").read_text(encoding="utf-8")
        meanings = {name: getattr(args, name) for name in (
            "value_column", "group_column", "observation_unit", "value_unit",
            "data_attribution", "paired_baseline", "x_column", "x_unit") if getattr(args, name)}
        if meanings:
            forwarded._code_task_text += "\n## User-declared data meanings\n\n" + json.dumps(meanings, ensure_ascii=False) + "\n"
        forwarded._start_root = root
        forwarded._generated_script = True
        forwarded._reuse_protected = ["data/**"]
        print_line("Scripted analysis uses the existing CodeTask execution boundary; supplied data and the delivery checker are protected. File checks do not establish statistical correctness.")
        print_line("Generation uses the bounded large edit profile (up to 16,000 characters per new block), only in the newly created script project. Ordinary project-repair limits are unchanged.")
        return prepare_start(forwarded)
    project_python = getattr(args, "project_python", None)
    if project_python is not None:
        if kind not in {"bug_fix", "reproduction"}:
            raise ValueError("--project-python requires --kind bug_fix; reproduction uses its command/environment options.")
        from simple_ar.code_task.execution.environment import _resolve_external_python
        try:
            project_python = Path(_resolve_external_python(str(project_python)))
        except (FileNotFoundError, ValueError) as exc:
            raise ValueError(f"Project Python executable not found or invalid: {project_python}") from exc
    if args.environment != "current" or args.requirements or args.install_project:
        if kind != "reproduction" or args.environment != "venv":
            raise ValueError("Environment/requirements/project installation requires --kind reproduction --environment venv.")
    if getattr(args, "check_argv", None) is not None and kind != "reproduction":
        raise ValueError("--check-argv requires --kind reproduction.")
    data_paths = [path.expanduser().resolve() for path in getattr(args, "data_path", [])]
    if data_paths and kind not in {"reproduction", "bug_fix"}:
        raise ValueError("--data-path requires --kind reproduction or bug_fix; table analysis uses --data-file.")
    for path in data_paths:
        if not path.exists():
            raise ValueError(f"Reproduction data location not found: {path}")
    goal = _answer("Goal / 目标", args.goal, interactive=interactive)
    analysis = None
    if kind == "data_analysis":
        if interactive:
            if args.data_file is None:
                args.data_file = Path(_answer("Data file / 数据路径", None, interactive=True))
            if not args.value_column:
                from simple_ar.result_analysis.table import read_table_source
                _, rows = read_table_source(args.data_file.expanduser().resolve(), max_mb=args.data_max_mb)
                columns = list(rows[0])
                print_line(f"Data preview / 数据预览: {len(rows)} rows; values below are examples, not inferred semantics.")
                for number, column in enumerate(columns, 1):
                    samples = [str(row[column])[:70] for row in rows[:3]]
                    print_line(f"{number}. {column}: {samples}")
                selected = _answer("Columns to analyze: names or comma-separated numbers / 选择分析列", None, interactive=True)
                if selected in columns:
                    args.value_column = [selected]
                else:
                    tokens = [item.strip() for item in selected.split(",")]
                    args.value_column = [columns[int(item) - 1] if item.isdecimal() and 1 <= int(item) <= len(columns) else item
                                         for item in tokens]
            args.observation_unit = _answer("What one row represents / 每行代表什么", args.observation_unit, interactive=True)
        analysis = data_settings(args)
    elif data_options_supplied(args):
        raise ValueError("Data options require --kind data_analysis.")
    if any(isinstance(path, str) for path in args.document):
        raise ValueError("Paper URLs require reproduction --chat acquisition and confirmation before ordinary preparation; --prepare-only does not fetch.")
    documents = [path.expanduser().resolve() for path in args.document]
    materials = [path.expanduser().resolve() for path in args.material]
    if args.template and kind != "writing":
        raise ValueError("--template requires --kind writing.")
    if materials and kind != "writing" and not (kind == "data_analysis" and args.with_report):
        raise ValueError("--material requires writing or data_analysis --with-report.")
    if kind == "writing" and interactive and not (documents or materials):
        materials.append(Path(_answer("Draft, notes or results / 草稿、笔记或结果说明路径", None,
                                      interactive=interactive)).expanduser().resolve())
    if kind in {"survey", "reproduction"} and interactive and not documents and args.sources is None:
        prompt = "Local document path / 本地材料" + ("（必需）" if kind == "reproduction" else "（可留空在线检索）")
        supplied = input(prompt + ": ").strip()
        if supplied:
            documents.append(Path(supplied).expanduser().resolve())
    for path in [*documents, *materials]:
        if not path.is_file():
            raise ValueError(f"Document not found: {path}")
    if kind == "writing":
        if not (materials or documents):
            raise ValueError("Writing requires at least one --material or --document.")
        if len(set([*documents, *materials])) != len(documents) + len(materials):
            raise ValueError("Each writing file must have one role; do not repeat papers as material.")
        from simple_ar.research.documents.ports import SUPPORTED_DOCUMENT_SUFFIXES, SUPPORTED_MATERIAL_SUFFIXES
        if any(path.suffix.lower() not in SUPPORTED_DOCUMENT_SUFFIXES for path in documents) or any(
            path.suffix.lower() not in SUPPORTED_MATERIAL_SUFFIXES for path in materials):
            raise ValueError("Writing material must be text/HTML/PDF/JSON or a table_analysis.v1 package; use data_analysis to compute raw tables.")
        from simple_ar.research.documents.ingest import analysis_material_paths
        analysis_material_paths(materials)
    project = None
    validation = None
    allowed = list(args.allow)
    sources = args.sources
    reproduction_rows: list[str] = []
    from simple_ar.report.templates import BUILTIN_TEMPLATE_NAMES, MATERIAL_REPORT_TEMPLATE
    writing_template = args.template or MATERIAL_REPORT_TEMPLATE
    if kind == "writing":
        from simple_ar.report.schema import ReportRuntimeConfig
        from simple_ar.report.templates import ReportTemplateError, load_report_template_bundle
        if Path(writing_template).suffix.lower() in {".md", ".markdown"}:
            writing_template = str(Path(writing_template).expanduser().resolve())
        try:
            load_report_template_bundle(report_mode="supplied_materials",
                                        config=ReportRuntimeConfig(template=writing_template))
        except ReportTemplateError as exc:
            raise ValueError(f"Invalid writing template or review criteria: {exc}") from exc
    if kind != "reproduction" and any((args.run_argv is not None, args.hypothesis, args.dataset,
            args.expected_outcome, args.metric, args.output_files, args.metric_sources, args.cwd is not None, args.timeout_sec is not None)):
        raise ValueError("Execution/protocol options require --kind reproduction; use --validate for bug_fix.")
    if kind == "data_analysis":
        if any((args.fulltext, args.project, args.validate, allowed, args.max_cited_sources)) or sources not in {None, "materials"}:
            raise ValueError("Data analysis does not authorize online search, remote retrieval or project editing.")
        if (documents or materials or sources) and not args.with_report:
            raise ValueError("Data documentation and references require --with-report; analysis alone reads only its table.")
        if any(path.suffix.lower() not in {".md", ".markdown", ".txt", ".pdf"} for path in [*documents, *materials]):
            raise ValueError("Data report documentation must be local text/PDF; supply the table through --data-file.")
        if len(set([*documents, *materials])) != len(documents) + len(materials):
            raise ValueError("Each supplied file must have one role; use --material for data documentation and --document for references.")
        sources = "materials"
    elif kind in {"survey", "reproduction", "writing"}:
        if args.max_cited_sources is not None and args.max_cited_sources < 1:
            raise ValueError("--max-cited-sources must be positive when specified.")
        if (args.validate or allowed or args.project) and kind != "reproduction":
            raise ValueError(f"{kind} does not accept project editing or validation options; choose bug_fix.")
        if args.project and not args.project.expanduser().resolve().is_dir():
            raise ValueError(f"Project directory not found: {args.project}")
        if kind in {"reproduction", "writing"}:
            paper_url_prepared = kind == "reproduction" and any(
                entry.get("adopted") and entry.get("receipt", {}).get("status") == "completed"
                and entry.get("allow_pdf_download")
                and Path(entry["receipt"]["path"]).resolve() in documents
                for entry in getattr(args, "_setup_state", {}).get("paper_acquisitions", []))
            if sources not in {None, "materials"} or (args.fulltext and not paper_url_prepared):
                raise ValueError("Prepared reproduction/writing uses supplied local material only; online preparation is not supported by this entry.")
            sources = "materials"
        else:
            sources = _answer("Source scope / 来源 [materials / search]", sources, interactive=interactive)
        if sources not in {"materials", "search"}:
            raise ValueError("Source scope must be materials or search.")
        if sources == "materials" and not (documents or materials):
            raise ValueError("materials requires at least one --document; no online search will be inferred.")
        if args.fulltext and sources != "search" and not (kind == "reproduction" and paper_url_prepared):
            raise ValueError("--fulltext enables remote retrieval and requires --sources search; local documents are already read in materials mode.")
        if kind == "reproduction":
            if args.validate or allowed:
                if not (args.validate and allowed and args.project):
                    raise ValueError("Reproduction code preparation requires --project, explicit --allow paths and an independent --validate command.")
                if args.check_argv is not None:
                    raise ValueError("Code preparation's independent validation replaces --check-argv; environment installation is authorized separately.")
                if args.environment == "venv" and shlex.split(args.validate)[0] not in {"python", "python3"}:
                    raise ValueError("Code preparation venv requires a python/python3 checker alias.")
                project = args.project.expanduser().resolve()
                if args.cwd and args.cwd.expanduser().resolve() != project:
                    raise ValueError("Reproduction code preparation must measure in its isolated project; --cwd must match --project.")
                validation = args.validate
            reproduction_rows = _reproduction_rows(args, interactive=interactive)
    else:
        if documents or sources or args.fulltext or args.max_cited_sources is not None:
            raise ValueError("bug_fix does not consume literature inputs or a search scope.")
        project = Path(_answer("Project path / 项目路径", str(args.project) if args.project else None,
                               interactive=interactive)).expanduser().resolve()
        if not project.is_dir():
            raise ValueError(f"Project directory not found: {project}")
        validation = _answer("Validation command / 验证命令", args.validate, interactive=interactive)
        if not allowed:
            allowed = [_answer("Editable path/glob / 允许修改的路径（例如 src/**）", None, interactive=interactive)]
    if project:
        for pattern in allowed:
            if not pattern.strip() or Path(pattern).is_absolute() or ".." in pattern.replace("\\", "/").split("/"):
                raise ValueError("Editable paths must be nonempty project-relative patterns without '..'.")
    if args.with_report and kind != "data_analysis":
        raise ValueError("start --with-report requires --kind data_analysis; survey, reproduction and writing already request reports.")
    if args.with_report and not args.model:
        raise ValueError("--with-report requires a model connection; analysis alone needs no model.")
    print_line(f"Task: {kind}\nGoal: {goal}\nModel: {'not used (descriptive analysis)' if kind == 'data_analysis' and not args.with_report else args.model}\nInteraction: {args.interaction}")
    if project:
        print_line(f"Project: {project}\nEdit scope: {', '.join(allowed)}\nValidation: {validation}")
        print_line("The validation command is authorized in an isolated auto-selected worktree/copy using "
                   + ("the approved task venv after installation" if kind == "reproduction" and args.environment == "venv"
                      else f"project Python {project_python}" if project_python else "the current Python environment")
                   + "; workspace isolation is not an OS sandbox.")
        if data_paths:
            print_line("Declared data inputs: " + ", ".join(str(path) for path in dict.fromkeys(data_paths)))
            print_line("Project data is copied at the same relative location and protected from automated edits; external data paths are not copied or injected into commands.")
    elif kind == "data_analysis":
        print_line(f"Data: {analysis['file']}; values: {', '.join(args.value_column)}; mode: {args.data_mode}; missing: {args.data_missing}")
        print_line("The analysis itself makes no model/API calls or project-code execution and does not verify experiments. Descriptive statistics only; copied data may be sensitive. Optional chat setup has separate model usage.")
        if args.with_report:
            print_line("A model will write and review a report from this session's copied data, computed results and figures; no new experiments or online search.")
    else:
        print_line(f"Sources: {sources}; papers: {len(documents)}; materials: {len(materials)}. " +
                   ("One declared reproduction command requested." if kind == "reproduction" else "No code execution requested."))
        if data_paths:
            print_line("External read-only data locations (not copied or split-verified): " + ", ".join(str(path) for path in dict.fromkeys(data_paths)))
        if args.max_cited_sources is not None:
            print_line(f"Final report source limit: {args.max_cited_sources} distinct cited source(s).")
        if sources == "search":
            print_line("Reading: remote full-text/PDF retrieval enabled (best effort)." if args.fulltext else
                       "Reading: abstracts and supplied local materials only. Use --fulltext for remote full-text/PDF retrieval.")
        if kind == "writing":
            print_line("Writes from supplied material; analysis packages copy data and recheck arithmetic, not data collection or scientific validity. No experiment, synthesis, or online search is requested.")

    # Persist before the final confirmation: an EOF/decline here loses no inputs.
    root = getattr(args, "_start_root", None) or new_research_session_root(args.output_root, goal)
    config = root / "research.toml"
    outputs = {"survey": ["report"], "bug_fix": ["bug_fix"], "reproduction": ["experiments", "report"], "writing": ["report"], "data_analysis": ["data_analysis"]}
    if args.with_report:
        outputs[kind].append("report")
    model = "" if kind == "data_analysis" and not args.with_report else args.model
    rows = ["[task]", f"goal = {_quote(goal)}", f"kind = {_quote(kind)}",
            f"outputs = {_array(outputs[kind])}",
            'output_root = "sessions"', "", "[model]", f"name = {_quote(model)}", "",
            "[research]", f"interaction = {_quote(args.interaction)}"]
    if kind in {"survey", "reproduction", "writing"} or (kind == "data_analysis" and args.with_report):
        rows.extend(_literature_rows(args, sources, documents))
        if data_paths:
            rows.append(f"data = {_array([str(path) for path in dict.fromkeys(data_paths)])}")
        if kind == "reproduction" and (args.project or args.cwd):
            from simple_ar.research.preparation import inspect_project_preparation, project_preparation_markdown
            from simple_ar.core.artifacts import write_json
            facts = inspect_project_preparation(args.project or args.cwd, data_paths=tuple(data_paths),
                read_paths=tuple(getattr(args, "_setup_state", {}).get("project_read_paths", [])))
            write_json(root / "preparation.json", facts)
            (root / "preparation.md").write_text(project_preparation_markdown(facts), encoding="utf-8")
        if kind in {"writing", "data_analysis"}:
            rows.append(f"materials = {_array([str(path) for path in materials])}")
    if kind in {"survey", "reproduction", "writing"} or args.with_report:
        rows.extend(["", "[report]", "document_review = true"])
        if kind in {"survey", "reproduction", "data_analysis"} or (kind == "writing" and writing_template in {
            MATERIAL_REPORT_TEMPLATE, "analysis_report", "source_review",
        }):
            # Keep the established entry default until automatic granularity has
            # a real same-material comparison; auto remains an explicit choice.
            rows.extend(['draft_scope = "document"', 'review_scope = "document"'])
        if args.max_cited_sources is not None:
            rows.append(f"max_cited_sources = {args.max_cited_sources}")
        if kind == "reproduction":
            rows.append('template = "reproduction"')
        elif kind in {"writing", "data_analysis"}:
            rows.append(f"template = {_quote(writing_template)}")
        # New built-in tasks share evidence planning; custom templates and
        # existing TOML/checkpoints retain their author's structure.
        if kind in {"survey", "reproduction"} or writing_template in {"auto", *BUILTIN_TEMPLATE_NAMES}:
            rows.append('outline_strategy = "adaptive"')
        rows.extend(reproduction_rows)
    if kind == "data_analysis":
        rows.extend(["", "[analysis]", *[f"{key} = {_array(list(value)) if isinstance(value, tuple) else str(value) if type(value) is int else _quote(value)}"
                                             for key, value in analysis.items()]])
    if project:
        if data_paths and kind == "bug_fix":
            rows.extend(["", "[assets]", f"data = {_array([str(path) for path in dict.fromkeys(data_paths)])}"])
        implementation_goal = getattr(args, "_code_task_text", goal)
        if kind == "reproduction":
            implementation_goal += ("\n\nPrepare only the authorized result adapter or execution glue. "
                "Reuse the inspected author implementation; do not alter methods, splits, evaluation, "
                "data or declared measurement conditions. Preserve raw results and export the declared "
                "metrics with their identity and provenance. The independent validator is not a scientific measurement.\n"
                + "Formal measurement argv: " + json.dumps(args.run_argv, ensure_ascii=False)
                + "\nRegistered outputs: " + json.dumps(args.output_files, ensure_ascii=False)
                + "\nMetric selectors: " + json.dumps(args.metric_sources, ensure_ascii=False))
        for name, text in _code_task_files(implementation_goal, project, validation, allowed,
                                         None if kind == "reproduction" and args.environment == "venv" else project_python,
                                         generated_script=getattr(args, "_generated_script", False),
                                         protected=getattr(args, "_reuse_protected", None),
                                         edit_policy=getattr(args, "_reuse_edit_policy", None),
                                         timeout_sec=args.timeout_sec or 300).items():
            (root / name).write_text(text, encoding="utf-8")
        if kind == "bug_fix":
            rows.extend(["", "[execution]", 'code_task_config = "code_task.toml"'])
    config.write_text("\n".join(rows) + "\n", encoding="utf-8")
    # Validate through the existing parser, not an independent wizard schema.
    from simple_ar.cli.research_config import research_defaults
    research_defaults(["research-session", "--config", str(config)])
    if hasattr(args, "_setup_state"):
        from simple_ar.core.artifacts import write_json
        state = {**args._setup_state, "status": "configured", "configuration": str(config)}
        write_json(root / "setup.json", state)
    print_line(f"Saved task configuration: {config}")
    print_line("Run: " + _command(["simple-ar", "research-session", "--config", str(config)]))
    resume = ["simple-ar", "research-session", "--session-root", "PATH"]
    if model:
        resume.extend(["--model", model])
    print_line("After a session starts, resume its printed path with " + _command(resume) +
               "; replace PATH with that session path, and do not rerun this setup to resume.")
    if args.prepare_only:
        return config
    if not args.yes:
        if not interactive:
            raise ValueError(f"Task saved at {config}. Use --yes to start non-interactively, or --prepare-only to save only.")
        if input("Start now / 现在执行? [y/N]: ").strip().lower() not in {"y", "yes"}:
            print_line("Saved without execution. Edit the files or use the printed run command later.")
            return None
    return config
