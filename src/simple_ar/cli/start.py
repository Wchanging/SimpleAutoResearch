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
from simple_ar.cli.research_config import data_options_supplied, data_settings
from simple_ar.core.console import print_line
from simple_ar.result_analysis.table import TABLE_CHOICES, TABLE_MODE_DESCRIPTIONS, TABLE_PLOT_DESCRIPTIONS, TableSpec


FUNCTION_LABELS = {
    "survey": "Direction survey / 方向调研 — compare sources and deliver a report",
    "bug_fix": "Code repair / 修改代码 — project, allowed edits and validation required",
    "reproduction": "Finite reproduction / 有限复现 — confirmed command and environment; optional task venv",
    "writing": "Material writing / 材料写作 — use notes, drafts or an analysis package",
    "data_analysis": "Data and plots / 分析绘图 — table, numeric columns and row meaning; no API needed",
}


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
    parser.add_argument("--chat", action="store_true", help="Opt-in model-assisted natural-language setup; confirms semantic choices before ordinary task setup. Requires a terminal and model connection.")
    parser.add_argument("--resume-setup", type=Path, help="Resume a saved natural-language setup directory, retaining replies and setup model accounting; not execution recovery.")
    parser.add_argument("--with-report", action="store_true", help="Data analysis: also write and review a report from the saved analysis and figures in the same session; requires a model. Default: analysis only, no task API calls.")
    add_data_options(parser)
    parser.add_argument("--data-path", action="append", default=[], type=Path,
                        help="Reproduction/code fix: existing data file/directory, repeatable. Project inputs enter isolated code workspaces; external paths never rewrite execution commands.")
    parser.add_argument("--goal", help="Describe the question or desired fix in your own words.")
    parser.add_argument("--document", action="append", default=[], type=Path,
                        help="Literature tasks: a bibliographic paper/reference, with citation identity. Use --material for notes, drafts or analysis packages; file format alone does not determine its role.")
    parser.add_argument("--material", action="append", default=[], type=Path, help="Writing: notes, drafts, ordinary JSON or a table_analysis.v1 analysis package. Ordinary materials are unverified; analysis packages recheck copied data. Not a bibliographic paper.")
    parser.add_argument("--template", help="Writing: built-in report template or Markdown template path; default material_report. Use experiment for an honest paper-style draft.")
    parser.add_argument("--sources", choices=("materials", "search"), help="Use only supplied documents, or allow online search.")
    parser.add_argument("--fulltext", action="store_true", help="Allow remote full-text retrieval and PDF downloads for an online survey; otherwise read available abstracts/local materials.")
    parser.add_argument("--max-cited-sources", type=int, help="Optional maximum number of distinct sources cited in the final report.")
    parser.add_argument("--project", type=Path, help="Existing project: bug_fix edits an isolated copy; reproduction chat inspects it read-only and proposes a command for confirmation.")
    parser.add_argument("--validate", help="Explicit validation command, for example: python -m unittest discover -s tests.")
    parser.add_argument("--allow", action="append", default=[], help="Editable project-relative path/glob; repeat as needed.")
    parser.add_argument("--project-python", type=Path,
                        help="Code repair: existing target-project Python executable. Default: current environment. No installs or environment creation.")
    parser.add_argument("--hypothesis", help="Prepared reproduction: the published conclusion to check.")
    parser.add_argument("--dataset", help="Prepared reproduction: data and any accepted adaptation.")
    parser.add_argument("--expected-outcome", help="Prepared reproduction: comparison criteria, not invented results.")
    parser.add_argument("--metric", action="append", default=[], help="Prepared reproduction: metric emitted by the command; repeat as needed.")
    parser.add_argument("--output-files", type=json.loads, default={}, help='Reproduction: JSON name-to-relative-file mapping written under SIMPLE_AR_OUTPUT_DIR, for example {"raw":"measurements.json"}. No directory scan or stdout path inference.')
    parser.add_argument("--cwd", type=Path, help="Prepared reproduction: existing execution directory; defaults to the current directory.")
    parser.add_argument("--timeout-sec", type=int, help="Prepared reproduction: one process limit in seconds; defaults to 300.")
    parser.add_argument("--environment", choices=("current", "venv"), default="current",
                        help="Reproduction: use the current environment, or explicitly create a task-local venv and install selected requirements before the declared command.")
    parser.add_argument("--requirements", action="append", default=[],
                        help="Venv: project-relative requirements file; repeatable. Defaults to requirements.txt when present, otherwise a bare venv. Installation can run build code; not an OS sandbox.")
    parser.add_argument("--install-project", action="store_true",
                        help="Reproduction venv: also install the execution project as a package, not editable. Requires a packaging declaration; may run build code/network and write source build metadata. Default: no project install.")
    parser.add_argument("--model", default="env", help="Default: use the model connection from .env.")
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
            *(["max_fulltext_documents = 4", "max_pdf_mb = 20"] if args.fulltext else []),
            "", "[assets]", f"papers = {_array([str(path) for path in documents])}"]


def _code_task_files(goal: str, project: Path, validation: str, allowed: list[str],
                     python_executable: Path | None = None) -> dict[str, str]:
    environment = ('mode = "current"\n' if python_executable is None else
                   'mode = "external"\n' + f'python = {_quote(str(python_executable))}\n')
    return {
        "task.md": goal + "\n",
        "code_task.toml": "[code_task]\n" + f"code_root = {_quote(str(project))}\n"
        'task_file = "{config_dir}/task.md"\noutput_root = "{config_dir}/sessions"\n'
        '\n[workspace]\nmode = "auto"\n\n[environment]\n' + environment
        + "\n[edit_scope]\n" + f"allowed_patterns = {_array(allowed)}\n"
        + 'protected_patterns = ["tests/**", "**/tests/**", "test_*.py", "**/test_*.py", ".env", ".env.*"]\n'
        + "\n[benchmark]\n" + f"command = {_quote(validation)}\n"
        + '\n[execute]\nuse_llm = true\nbaseline_policy = "skip"\ntimeout_sec = 300\nrepair_rounds = 1\n',
    }


def _reproduction_rows(args: argparse.Namespace, *, interactive: bool) -> list[str]:
    from simple_ar.experiment.execution.outputs import output_files
    attachments = output_files({"output_files": args.output_files})
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
    metrics = args.metric or [_answer("Emitted metric / 命令实际输出的指标名", None, interactive=interactive)]
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
    if args.environment == "venv":
        from simple_ar.research.project_environment import environment_profile
        requirements = args.requirements or (["requirements.txt"] if (cwd / "requirements.txt").is_file() else [])
        profile = environment_profile({"mode": "venv", "requirements": requirements,
                                       "install_project": args.install_project, "timeout_sec": timeout}, project=cwd)
        if argv[0] not in {"python", "python3", "python.exe", "python3.exe"}:
            raise ValueError("Task venv requires a python/python3 command alias; do not silently replace an explicit interpreter or launcher.")
        setup_steps = 2 + bool(profile["requirements"] or profile["install_project"])
        environment_rows = ["", "[execution.environment]", 'mode = "venv"',
                            f"requirements = {_array(profile['requirements'])}",
                            f"install_project = {'true' if profile['install_project'] else 'false'}", f"timeout_sec = {timeout}"]
        print_line(f"Task-local venv: selected requirements {profile['requirements']}; install project package: {profile['install_project']}; then pip check. {setup_steps} preparation invocations, each <= {timeout}s. Build code/network access and source build metadata writes may occur; not an OS sandbox.")
    print_line("\n".join(f"{key}: {value}" for key, value in protocol.items()))
    print_line(f"Execution: {_command(argv)}\nDirectory: {cwd}\nOne invocation; timeout: {timeout}s; metrics: {', '.join(metrics)}")
    if not setup_steps:
        print_line("Uses the current environment, not an OS sandbox. No automatic installs, baseline training, or method changes are authorized.")
    return ["", "[execution]", f"command = {_array(argv)}", f"cwd = {_quote(str(cwd))}",
            f"timeout_sec = {timeout}", 'baseline_policy = "skip"', f"primary_metric = {_quote(metrics[0])}",
            f"metrics = {_array(metrics)}",
            *(["output_files = {" + ", ".join(f"{_quote(name)} = {_quote(path)}" for name, path in attachments.items()) + "}"] if attachments else []),
            *environment_rows, "", "[execution.protocol]",
            *[f"{key} = {_quote(value)}" for key, value in protocol.items()],
            f"metrics = {_array(metrics)}", "", "[budget]", f"process_invocations = {1 + setup_steps}",
            f"process_wall_seconds = {timeout * (1 + setup_steps)}"]


def prepare_start(args: argparse.Namespace) -> Path | None:
    """Collect bounded user choices and write one canonical, editable input set."""
    interactive = sys.stdin.isatty()
    print_line("Available now: survey, bug_fix, prepared reproduction, material-based writing, data_analysis (descriptive tables and figures).")
    print_line("Free-form scientific illustrations and autonomous reproduction preparation are not yet offered.")
    if interactive and args.kind is None:
        for index, (name, label) in enumerate(FUNCTION_LABELS.items(), 1):
            print_line(f"{index}. {label} ({name})")
    kind = _answer("Function number or name / 功能编号或名称", args.kind, interactive=interactive)
    numbers = {str(index): name for index, name in enumerate(FUNCTION_LABELS, 1)}
    kind = numbers.get(kind, kind)
    if kind not in FUNCTION_LABELS:
        raise ValueError("Choose 1–5 in the terminal, or survey, bug_fix, reproduction, writing or data_analysis.")
    project_python = getattr(args, "project_python", None)
    if project_python is not None:
        if kind != "bug_fix":
            raise ValueError("--project-python requires --kind bug_fix; reproduction uses its command/environment options.")
        from simple_ar.code_task.execution.environment import _resolve_external_python
        try:
            project_python = Path(_resolve_external_python(str(project_python)))
        except (FileNotFoundError, ValueError) as exc:
            raise ValueError(f"Project Python executable not found or invalid: {project_python}") from exc
    if args.environment != "current" or args.requirements or args.install_project:
        if kind != "reproduction" or args.environment != "venv":
            raise ValueError("Environment/requirements/project installation requires --kind reproduction --environment venv.")
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
            args.expected_outcome, args.metric, args.output_files, args.cwd is not None, args.timeout_sec is not None)):
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
        if args.validate or allowed or (args.project and kind != "reproduction"):
            raise ValueError(f"{kind} does not accept project editing or validation options; choose bug_fix.")
        if args.project and not args.project.expanduser().resolve().is_dir():
            raise ValueError(f"Project directory not found: {args.project}")
        if kind in {"reproduction", "writing"}:
            if sources not in {None, "materials"} or args.fulltext:
                raise ValueError("Prepared reproduction/writing uses supplied local material only; online preparation is not supported by this entry.")
            sources = "materials"
        else:
            sources = _answer("Source scope / 来源 [materials / search]", sources, interactive=interactive)
        if sources not in {"materials", "search"}:
            raise ValueError("Source scope must be materials or search.")
        if sources == "materials" and not (documents or materials):
            raise ValueError("materials requires at least one --document; no online search will be inferred.")
        if args.fulltext and sources != "search":
            raise ValueError("--fulltext enables remote retrieval and requires --sources search; local documents are already read in materials mode.")
        if kind == "reproduction":
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
                   + (f"project Python {project_python}" if project_python else "the current Python environment")
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
        if kind == "data_analysis":
            # Compose the explanation together, then inspect the complete
            # argument. Saved/expert configs retain their accepted scopes.
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
    elif kind == "bug_fix":
        if data_paths:
            rows.extend(["", "[assets]", f"data = {_array([str(path) for path in dict.fromkeys(data_paths)])}"])
        for name, text in _code_task_files(goal, project, validation, allowed, project_python).items():
            (root / name).write_text(text, encoding="utf-8")
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
