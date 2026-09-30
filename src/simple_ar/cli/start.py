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
from simple_ar.core.console import print_line


def add_data_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-file", type=Path, help="Data analysis: UTF-8 CSV/TSV or JSON records.")
    parser.add_argument("--value-column", action="append", default=[], help="Explicit numeric column; repeat for separate metrics/axes.")
    parser.add_argument("--group-column", default="", help="Category column; values mode requires unique labels.")
    parser.add_argument("--observation-unit", default="", help="What one row represents, e.g. one independent run or one supplied summary.")
    parser.add_argument("--value-unit", default="", help="Unit shared by selected value columns; omitted is recorded as unknown.")
    parser.add_argument("--data-mode", choices=("observations", "values"), default="observations",
                        help="Observations: count/mean/sample std; values: no re-aggregation or inferred uncertainty.")
    parser.add_argument("--data-missing", choices=("reject", "omit"), default="reject")
    parser.add_argument("--figure-width", choices=("column", "wide"), default="wide", help="Generic 3.5/7-inch figure target, not a conference-specific size.")
    parser.add_argument("--data-max-mb", type=int, default=20, help="Physical input size limit, MiB.")
    parser.add_argument("--data-max-figures", type=int, default=100, help="Physical SVG page limit; overflow fails without dropping categories.")


def data_settings(args: argparse.Namespace) -> dict:
    from simple_ar.result_analysis.table import TableSpec
    spec = TableSpec(tuple(args.value_column), args.observation_unit, args.group_column,
                     args.value_unit, args.data_mode, args.data_missing, args.figure_width, args.data_max_mb, args.data_max_figures)
    if args.data_file is None:
        raise ValueError("Data analysis requires --data-file.")
    path = args.data_file.expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in {".csv", ".tsv", ".json"}:
        raise ValueError("Provide an existing CSV/TSV or JSON records file.")
    from dataclasses import asdict
    return {"file": str(path), **asdict(spec)}


def add_start_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("start", help="Set up a survey, code fix, prepared reproduction, writing or descriptive data analysis without TOML.")
    parser.add_argument("--kind", choices=("survey", "bug_fix", "reproduction", "writing", "data_analysis"))
    add_data_options(parser)
    parser.add_argument("--goal", help="Describe the question or desired fix in your own words.")
    parser.add_argument("--document", action="append", default=[], type=Path)
    parser.add_argument("--material", action="append", default=[], type=Path, help="Writing: draft, notes or result description (Markdown/text/PDF), not a bibliographic paper.")
    parser.add_argument("--template", help="Writing: built-in report template or Markdown template path; default analysis_report. Use experiment for an honest paper-style draft.")
    parser.add_argument("--sources", choices=("materials", "search"), help="Use only supplied documents, or allow online search.")
    parser.add_argument("--fulltext", action="store_true", help="Allow remote full-text retrieval and PDF downloads for an online survey; otherwise read available abstracts/local materials.")
    parser.add_argument("--max-cited-sources", type=int, help="Optional maximum number of distinct sources cited in the final report.")
    parser.add_argument("--project", type=Path, help="Existing code project; edits are made in an isolated copy.")
    parser.add_argument("--validate", help="Explicit validation command, for example: python -m unittest discover -s tests.")
    parser.add_argument("--allow", action="append", default=[], help="Editable project-relative path/glob; repeat as needed.")
    parser.add_argument("--hypothesis", help="Prepared reproduction: the published conclusion to check.")
    parser.add_argument("--dataset", help="Prepared reproduction: data and any accepted adaptation.")
    parser.add_argument("--expected-outcome", help="Prepared reproduction: comparison criteria, not invented results.")
    parser.add_argument("--metric", action="append", default=[], help="Prepared reproduction: metric emitted by the command; repeat as needed.")
    parser.add_argument("--cwd", type=Path, help="Prepared reproduction: existing execution directory; defaults to the current directory.")
    parser.add_argument("--timeout-sec", type=int, help="Prepared reproduction: one process limit in seconds; defaults to 300.")
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


def _code_task_files(goal: str, project: Path, validation: str, allowed: list[str]) -> dict[str, str]:
    return {
        "task.md": goal + "\n",
        "code_task.toml": "[code_task]\n" + f"code_root = {_quote(str(project))}\n"
        'task_file = "{config_dir}/task.md"\noutput_root = "{config_dir}/sessions"\n'
        '\n[workspace]\nmode = "copy"\n\n[environment]\nmode = "current"\n'
        + "\n[edit_scope]\n" + f"allowed_patterns = {_array(allowed)}\n"
        + 'protected_patterns = ["tests/**", "**/tests/**", "test_*.py", "**/test_*.py", ".env", ".env.*"]\n'
        + "\n[benchmark]\n" + f"command = {_quote(validation)}\n"
        + '\n[execute]\nuse_llm = true\nbaseline_policy = "skip"\ntimeout_sec = 300\nrepair_rounds = 1\n',
    }


def _reproduction_rows(args: argparse.Namespace, *, interactive: bool) -> list[str]:
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
    cwd = (args.cwd or Path.cwd()).expanduser().resolve()
    if not cwd.is_dir():
        raise ValueError(f"Execution directory not found: {cwd}")
    timeout = args.timeout_sec if args.timeout_sec is not None else 300
    if timeout < 1:
        raise ValueError("--timeout-sec must be positive.")
    print_line("\n".join(f"{key}: {value}" for key, value in protocol.items()))
    print_line(f"Execution: {_command(argv)}\nDirectory: {cwd}\nOne invocation; timeout: {timeout}s; metrics: {', '.join(metrics)}")
    print_line("Uses the current environment, not an OS sandbox. No automatic installs, baseline training, or method changes are authorized.")
    return ["", "[execution]", f"command = {_array(argv)}", f"cwd = {_quote(str(cwd))}",
            f"timeout_sec = {timeout}", 'baseline_policy = "skip"', f"primary_metric = {_quote(metrics[0])}",
            f"metrics = {_array(metrics)}", "", "[execution.protocol]",
            *[f"{key} = {_quote(value)}" for key, value in protocol.items()],
            f"metrics = {_array(metrics)}", "", "[budget]", "process_invocations = 1",
            f"process_wall_seconds = {timeout}"]


def prepare_start(args: argparse.Namespace) -> Path | None:
    """Collect bounded user choices and write one canonical, editable input set."""
    interactive = sys.stdin.isatty()
    print_line("Available now: survey, bug_fix, prepared reproduction, material-based writing, data_analysis (descriptive tables and figures).")
    print_line("Free-form scientific illustrations and autonomous reproduction preparation are not yet offered.")
    kind = _answer("Function / 功能 [survey / bug_fix / reproduction / writing / data_analysis]", args.kind, interactive=interactive)
    if kind not in {"survey", "bug_fix", "reproduction", "writing", "data_analysis"}:
        raise ValueError("Choose survey, bug_fix, reproduction, writing or data_analysis.")
    goal = _answer("Goal / 目标", args.goal, interactive=interactive)
    analysis = None
    if kind == "data_analysis":
        if interactive:
            if args.data_file is None:
                args.data_file = Path(_answer("Data file / 数据路径", None, interactive=True))
            if not args.value_column:
                args.value_column = [_answer("Numeric column / 数值列", None, interactive=True)]
            args.observation_unit = _answer("What one row represents / 每行代表什么", args.observation_unit, interactive=True)
        analysis = data_settings(args)
    elif any((args.data_file, args.value_column, args.group_column, args.observation_unit, args.value_unit,
              args.data_mode != "observations", args.data_missing != "reject", args.figure_width != "wide", args.data_max_mb != 20, args.data_max_figures != 100)):
        raise ValueError("Data options require --kind data_analysis.")
    documents = [path.expanduser().resolve() for path in args.document]
    materials = [path.expanduser().resolve() for path in args.material]
    if kind != "writing" and (materials or args.template):
        raise ValueError("--material and --template require --kind writing.")
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
        if any(path.suffix.lower() not in {".md", ".markdown", ".txt", ".pdf"} for path in [*documents, *materials]):
            raise ValueError("Writing material must be Markdown, text or PDF; raw data is not a verified result description.")
    project = None
    validation = None
    allowed = list(args.allow)
    sources = args.sources
    reproduction_rows: list[str] = []
    writing_template = args.template or "analysis_report"
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
            args.expected_outcome, args.metric, args.cwd is not None, args.timeout_sec is not None)):
        raise ValueError("Execution/protocol options require --kind reproduction; use --validate for bug_fix.")
    if kind == "data_analysis":
        if any((documents, materials, args.template, sources, args.fulltext, args.project, args.validate, allowed, args.max_cited_sources)):
            raise ValueError("Data analysis accepts a data file and descriptive settings, not literature, editing or report-generation options.")
    elif kind in {"survey", "reproduction", "writing"}:
        if args.max_cited_sources is not None and args.max_cited_sources < 1:
            raise ValueError("--max-cited-sources must be positive when specified.")
        if args.project or args.validate or allowed:
            raise ValueError(f"{kind} does not accept project editing or validation options; choose bug_fix.")
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
    print_line(f"Task: {kind}\nGoal: {goal}\nModel: {'not used (descriptive analysis)' if kind == 'data_analysis' else args.model}\nInteraction: {args.interaction}")
    if project:
        print_line(f"Project: {project}\nEdit scope: {', '.join(allowed)}\nValidation: {validation}")
        print_line("The validation command is authorized to execute in a copy using the current Python environment; copying is not an OS sandbox.")
    elif kind == "data_analysis":
        print_line(f"Data: {analysis['file']}; values: {', '.join(args.value_column)}; mode: {args.data_mode}; missing: {args.data_missing}")
        print_line("No model/API calls, code execution or experiment verification. Descriptive statistics only; copied data may be sensitive.")
    else:
        print_line(f"Sources: {sources}; papers: {len(documents)}; materials: {len(materials)}. " +
                   ("One declared reproduction command requested." if kind == "reproduction" else "No code execution requested."))
        if args.max_cited_sources is not None:
            print_line(f"Final report source limit: {args.max_cited_sources} distinct cited source(s).")
        if sources == "search":
            print_line("Reading: remote full-text/PDF retrieval enabled (best effort)." if args.fulltext else
                       "Reading: abstracts and supplied local materials only. Use --fulltext for remote full-text/PDF retrieval.")
        if kind == "writing":
            print_line("Writes from supplied text only; user results are not independently verified. No experiment, research synthesis, or online search is requested.")

    # Persist before the final confirmation: an EOF/decline here loses no inputs.
    root = new_research_session_root(args.output_root, goal)
    config = root / "research.toml"
    outputs = {"survey": ["report"], "bug_fix": ["bug_fix"], "reproduction": ["experiments", "report"], "writing": ["report"], "data_analysis": ["data_analysis"]}
    rows = ["[task]", f"goal = {_quote(goal)}", f"kind = {_quote(kind)}",
            f"outputs = {_array(outputs[kind])}",
            'output_root = "sessions"', "", "[model]", f"name = {_quote(args.model)}", "",
            "[research]", f"interaction = {_quote(args.interaction)}"]
    if kind in {"survey", "reproduction", "writing"}:
        rows.extend(_literature_rows(args, sources, documents))
        if kind == "writing":
            rows.append(f"materials = {_array([str(path) for path in materials])}")
        if args.max_cited_sources is not None or kind in {"reproduction", "writing"}:
            rows.extend(["", "[report]"])
            if args.max_cited_sources is not None:
                rows.append(f"max_cited_sources = {args.max_cited_sources}")
        if kind == "reproduction":
            rows.extend(['template = "reproduction"', "document_review = true", *reproduction_rows])
        elif kind == "writing":
            rows.extend([f"template = {_quote(writing_template)}", "document_review = true"])
    elif kind == "data_analysis":
        rows.extend(["", "[analysis]", *[f"{key} = {_array(list(value)) if isinstance(value, tuple) else str(value) if type(value) is int else _quote(value)}"
                                             for key, value in analysis.items()]])
    else:
        for name, text in _code_task_files(goal, project, validation, allowed).items():
            (root / name).write_text(text, encoding="utf-8")
        rows.extend(["", "[execution]", 'code_task_config = "code_task.toml"'])
    config.write_text("\n".join(rows) + "\n", encoding="utf-8")
    # Validate through the existing parser, not an independent wizard schema.
    from simple_ar.cli.research_config import research_defaults
    research_defaults(["research-session", "--config", str(config)])
    print_line(f"Saved task configuration: {config}")
    print_line("Run: " + _command(["simple-ar", "research-session", "--config", str(config)]))
    print_line("After a session starts, resume its printed path with research-session --session-root PATH --model env; do not rerun this setup to resume.")
    if args.prepare_only:
        return config
    if not args.yes:
        if not interactive:
            raise ValueError(f"Task saved at {config}. Use --yes to start non-interactively, or --prepare-only to save only.")
        if input("Start now / 现在执行? [y/N]: ").strip().lower() not in {"y", "yes"}:
            print_line("Saved without execution. Edit the files or use the printed run command later.")
            return None
    return config
