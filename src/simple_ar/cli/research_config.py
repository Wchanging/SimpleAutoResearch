"""Map research TOML onto the same options used by the CLI."""
import argparse
from dataclasses import dataclass
from pathlib import Path
import tomllib


FIELDS = {
    "task": {"goal": ("topic", str), "kind": ("task_kind", str), "outputs": ("outputs", list), "output_root": ("output_root", str),
              "selected_idea_id": ("selected_idea_id", str)},
    "model": {"name": ("model", str), "feasibility_review_model": ("feasibility_review_model", str),
              "max_output_tokens": ("max_output_tokens", int)},
    "budget": {"total_tokens": ("total_tokens", int), "llm_requests": ("llm_requests", int),
               "process_invocations": ("process_invocations", int), "process_wall_seconds": ("process_wall_seconds", int)},
    "continuation": {"authorization_id": ("authorization_id", str),
                     "reason": ("authorization_reason", str),
                     "additional_attempts": ("additional_attempts", int),
                     "additional_no_progress": ("additional_no_progress", int),
                     "decision_id": ("decision_id", str),
                     "decision_response": ("decision_response", str),
                     "decision_guidance": ("decision_guidance", str),
                     "remaining": ("authorize_remaining", dict)},
    "research": {"providers": ("providers", list), "queries": ("queries", list),
                 "max_results": ("max_results", int), "max_chunks": ("max_chunks", int),
                 "max_pdf_pages": ("research_max_pdf_pages", int),
                 "max_fulltext_documents": ("research_max_fulltext_documents", int),
                 "max_pdf_mb": ("research_max_pdf_mb", int),
                 "read_max_shortlist": ("read_max_shortlist", int),
                 "idea_limit": ("idea_limit", int), "cache_dir": ("cache_dir", str),
                 "use_fulltext": ("research_use_fulltext", bool),
                 "materials_only": ("research_materials_only", bool),
                 "allow_pdf_download": ("research_allow_pdf_download", bool),
                  "max_iterations": ("max_research_iterations", int),
                 "keep_raw_pdf": ("research_keep_raw_pdf", bool),
                 "interaction": ("interaction", str)},
    "assets": {"papers": ("local_document", list), "materials": ("material", list)},
    "analysis": {"file": ("data_file", str), "value_columns": ("value_column", list),
                 "group_column": ("group_column", str), "observation_unit": ("observation_unit", str),
                 "value_unit": ("value_unit", str), "mode": ("data_mode", str),
                 "missing": ("data_missing", str), "width": ("figure_width", str), "max_mb": ("data_max_mb", int),
                 "max_figures": ("data_max_figures", int), "plot": ("data_plot", str),
                 "x_column": ("x_column", str), "x_unit": ("x_unit", str), "max_points": ("data_max_points", int),
                 "series_layout": ("series_layout", str)},
    "execution": {"command": ("command_argv", list), "cwd": ("cwd", str),
                  "timeout_sec": ("timeout_sec", int), "code_task_config": ("code_task_config", str),
                  "primary_metric": ("primary_metric", str), "metrics": ("metric", list),
                  "metric_directions": ("metric_direction", list)},
    "report": {"template": ("report_template", str), "reviewer": ("report_reviewer", str),
               "outline_strategy": ("report_outline_strategy", str),
               "data_tables": ("report_data_tables", str),
               "max_review_iterations": ("max_review_iterations", int),
               "document_review": ("report_document_review", bool),
               "max_section_tokens": ("max_section_tokens", int),
               "max_cited_sources": ("report_max_cited_sources", int),
               "figures": ("report_figures", dict)},
}
PATHS = {"output_root", "cache_dir", "cwd", "code_task_config", "local_document", "material", "data_file"}
LIST_FLAGS = {"providers": "--provider", "queries": "--query", "local_document": "--local-document", "material": "--material",
              "metric": "--metric", "metric_direction": "--metric-direction", "value_column": "--value-column"}


@dataclass(frozen=True)
class SessionArguments:
    """Validated CLI/TOML input, not another persisted task model."""
    task_kind: str
    command: tuple[str, ...]
    execution_details: dict
    outputs: list[str] | None
    materials: list[Path]
    data_analysis: dict | None


def data_settings(args: argparse.Namespace) -> dict:
    """Validate table settings shared by guided and direct session input."""
    from simple_ar.result_analysis.table import TableSpec, read_table_source, validate_table_columns
    spec = TableSpec(tuple(args.value_column), args.observation_unit, args.group_column,
                     args.value_unit, args.data_mode, args.data_missing, args.figure_width, args.data_max_mb, args.data_max_figures,
                     args.data_plot, args.x_column, args.x_unit, args.data_max_points, args.series_layout)
    if args.data_file is None:
        raise ValueError("Data analysis requires --data-file.")
    path = args.data_file.expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in {".csv", ".tsv", ".json"}:
        raise ValueError("Provide an existing CSV/TSV or JSON records file.")
    # Preview checks shape and names; ingestion freezes and validates fresh bytes.
    _, rows = read_table_source(path, max_mb=spec.max_mb)
    validate_table_columns(rows, spec)
    from dataclasses import asdict
    return {"file": str(path), **asdict(spec)}


def validate_session_arguments(args: argparse.Namespace) -> SessionArguments:
    """Validate merged arguments before model setup or session writes."""
    if getattr(args, "reanalyze", False) and not getattr(args, "session_root", None):
        raise SystemExit("--reanalyze requires --session-root.")
    if getattr(args, "recover_interrupted", False) and not getattr(args, "session_root", None):
        raise SystemExit("--recover-interrupted requires --session-root.")
    if args.max_results < 1 or args.max_chunks < 1 or args.idea_limit < 1:
        raise SystemExit(
            "--max-results, --max-chunks, and --idea-limit must be positive."
        )
    if getattr(args, "research_max_pdf_pages", None) is not None and args.research_max_pdf_pages < 1:
        raise SystemExit("research.max_pdf_pages must be positive.")
    for name in ("research_max_fulltext_documents", "research_max_pdf_mb"):
        value = getattr(args, name, None)
        if value is not None and value < 1:
            raise SystemExit(f"{name.removeprefix('research_')} must be positive when provided.")
    if args.timeout_sec is not None and args.timeout_sec < 1:
        raise SystemExit("--timeout-sec must be positive when provided.")
    if args.max_review_iterations < 0:
        raise SystemExit("--max-review-iterations cannot be negative.")
    if args.max_research_iterations < 0:
        raise SystemExit("--max-research-iterations cannot be negative.")
    task_kind = str(getattr(args, "task_kind", "auto") or "auto").strip().lower()
    if task_kind not in {"auto", "survey", "bug_fix", "measurement", "reproduction", "writing", "data_analysis"}:
        raise SystemExit("--task-kind must be auto, survey, bug_fix, measurement, reproduction, writing or data_analysis.")
    command = tuple(args.command_argv or ())
    execution_details = getattr(args, "execution_details", {})
    if command and execution_details.get("pairs"):
        raise SystemExit("Use execution.pairs or a single command, not both; paired argv must be explicit.")
    outputs = getattr(args, "outputs", None)
    materials = getattr(args, "material", [])
    if outputs and "data_analysis" in outputs and task_kind != "data_analysis":
        raise SystemExit("data_analysis output requires an explicit data_analysis task.")
    data_analysis = None
    if task_kind == "data_analysis":
        if command or execution_details or args.code_task_config or args.local_document or materials or args.queries or args.providers or args.with_report or outputs not in (None, ["data_analysis"]):
            raise SystemExit("Data analysis accepts only a supplied table and descriptive settings, without research, CodeTask or experiment execution.")
        if not getattr(args, "session_root", None):
            try:
                data_analysis = data_settings(args)
            except (OSError, ValueError) as exc:
                raise SystemExit(str(exc)) from exc
    elif any((args.data_file, args.value_column, args.group_column, args.observation_unit, args.value_unit,
              args.data_mode != "observations", args.data_missing != "reject", args.figure_width != "wide", args.data_max_mb != 20, args.data_max_figures != 100,
              args.data_plot != "bar", args.x_column, args.x_unit, args.data_max_points != 10000, args.series_layout != "separate")):
        raise SystemExit("Data options require --task-kind data_analysis.")
    if materials and task_kind != "writing":
        raise SystemExit("--material/assets.materials currently requires task.kind=writing.")
    if task_kind == "writing":
        if command or execution_details or getattr(args, "code_task_config", None) or args.no_report or outputs not in (None, ["report"]):
            raise SystemExit("Writing requests only a report without execution or CodeTask configuration.")
        if not getattr(args, "session_root", None) and not (materials or args.local_document):
            raise SystemExit("Writing requires --material and/or --local-document.")
        if args.queries or args.providers or getattr(args, "research_materials_only", None) is False or getattr(args, "research_allow_pdf_download", None):
            raise SystemExit("Writing uses supplied local material only; online research is a survey task.")
        supplied = [Path(path).expanduser().resolve() for path in [*materials, *args.local_document]]
        if len(supplied) != len(set(supplied)):
            raise SystemExit("Writing material must have one unambiguous role per file; do not repeat a paper as material.")
        text_suffixes = {".md", ".markdown", ".txt", ".pdf"}
        for paths, suffixes in ((args.local_document, text_suffixes), (materials, text_suffixes | {".json"})):
            if any(not Path(path).expanduser().is_file() or Path(path).suffix.lower() not in suffixes for path in paths):
                raise SystemExit("Writing requires text/PDF or a table_analysis.v1 analysis package; raw tables are not writing results.")
    if outputs and task_kind != "bug_fix" and "experiments" not in outputs and (command or execution_details or getattr(args, "code_task_config", None)):
        raise SystemExit("Execution configuration requires experiments in --outputs/task.outputs.")
    if task_kind == "bug_fix" and outputs and set(outputs) != {"bug_fix"}:
        raise SystemExit("--task-kind bug_fix requires --outputs bug_fix or no explicit outputs.")
    if task_kind == "survey" and (command or execution_details or getattr(args, "code_task_config", None)):
        raise SystemExit("--task-kind survey cannot include execution or CodeTask configuration.")
    if task_kind == "measurement":
        if outputs != ["experiments"]:
            raise SystemExit("--task-kind measurement requires --outputs experiments.")
        if not command or getattr(args, "code_task_config", None) or execution_details.get("pairs") or execution_details.get("baseline_policy") in {"run", "reuse"}:
            raise SystemExit("--task-kind measurement requires one explicit command without CodeTask, paired runs, or baseline comparison.")
    if task_kind == "reproduction":
        if not outputs or "experiments" not in outputs or set(outputs) - {"experiments", "report"}:
            raise SystemExit("--task-kind reproduction requires outputs experiments and optionally report.")
        if not command or getattr(args, "code_task_config", None) or execution_details.get("pairs") or execution_details.get("baseline_policy") in {"run", "reuse"}:
            raise SystemExit("Prepared reproduction requires one explicit command without CodeTask or paired runs.")
        protocol = execution_details.get("protocol")
        if not isinstance(protocol, dict) or any(not str(protocol.get(key) or "").strip()
                                                for key in ("hypothesis", "dataset", "expected_outcome")):
            raise SystemExit("Prepared reproduction requires execution.protocol hypothesis, dataset and expected_outcome.")
        if not getattr(args, "local_document", None) or not getattr(args, "research_materials_only", False):
            raise SystemExit("Prepared reproduction requires local documents and research.materials_only=true.")
    if outputs and (args.with_report or args.no_report):
        raise SystemExit("Use explicit outputs or --with-report/--no-report, not both.")
    for field in ("total_tokens", "llm_requests", "max_output_tokens", "process_invocations", "process_wall_seconds"):
        value = getattr(args, field, None)
        if value is not None and value < (0 if field.startswith("process_") else 1):
            raise SystemExit(f"Invalid {field}: {value}")
    if task_kind == "bug_fix" and not getattr(args, "code_task_config", None):
        raise SystemExit("--task-kind bug_fix requires --code-task-config for an existing project.")
    return SessionArguments(task_kind, command, execution_details, outputs, materials, data_analysis)


def research_defaults(
    arguments: list[str], *, explicit_destinations: set[str] | None = None,
) -> dict:
    if not arguments or arguments[0] != "research-session":
        return {}
    # Everything after --command belongs to the external process.
    options = arguments[:arguments.index("--command")] if "--command" in arguments else arguments
    probe = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    probe.add_argument("--config", type=Path)
    path = probe.parse_known_args(options)[0].config
    if path is None:
        return {}
    path = path.resolve()
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    defaults = {"model": "env"}
    for section, values in data.items():
        if section not in FIELDS or not isinstance(values, dict):
            raise ValueError(f"Unknown research configuration section: {section}")
        for name, value in values.items():
            if section == "execution" and name in {
                "pairs", "protocol", "seeds", "seed_flag", "seed_count", "baseline_policy", "baseline_ref", "output_files",
            }:
                if name == "pairs":
                    from simple_ar.app.research_execution import execution_pairs
                    execution_pairs({"pairs": value})
                elif name == "output_files":
                    from simple_ar.experiment.execution.outputs import output_files
                    output_files({"output_files": value})
                elif name == "protocol":
                    if not isinstance(value, dict):
                        raise ValueError("execution.protocol must be a table")
                elif name == "seeds":
                    if not isinstance(value, list) or not value or any(type(item) is not int for item in value) or len(set(value)) != len(value):
                        raise ValueError("execution.seeds must be a non-empty list of unique integers")
                elif name == "seed_count":
                    if type(value) is not int or value < 1:
                        raise ValueError("execution.seed_count must be a positive integer")
                elif name == "seed_flag":
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError("execution.seed_flag must be a non-empty string")
                elif name == "baseline_policy":
                    if not isinstance(value, str) or value.strip().lower() not in {"run", "skip", "reuse"}:
                        raise ValueError("execution.baseline_policy must be run, skip or reuse")
                elif name == "baseline_ref":
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError("execution.baseline_ref must be a non-empty artifact path")
                defaults.setdefault("execution_details", {})[name] = value
                continue
            if name not in FIELDS[section]:
                raise ValueError(f"Unknown research configuration field: {section}.{name}")
            dest, expected = FIELDS[section][name]
            if explicit_destinations is not None:
                explicit_destinations.add(dest)
            if type(value) is not expected or (expected is list and any(type(item) is not str for item in value)):
                raise ValueError(f"Invalid type for {section}.{name}: expected {expected.__name__}")
            if expected is int and dest not in {"additional_attempts", "additional_no_progress"} and value < (0 if dest in {"max_review_iterations", "max_section_tokens", "report_max_cited_sources", "max_research_iterations", "process_invocations", "process_wall_seconds"} else 1):
                raise ValueError(f"Invalid value for {section}.{name}: {value}")
            if dest == "interaction" and value not in {"assisted", "checkpoints", "autonomous"}:
                raise ValueError("research.interaction must be assisted, checkpoints or autonomous")
            if dest == "report_outline_strategy" and value not in {"auto", "template", "adaptive"}:
                raise ValueError("report.outline_strategy must be auto, template or adaptive")
            if dest == "report_data_tables" and value not in {"linked", "full"}:
                raise ValueError("report.data_tables must be linked or full")
            if dest == "decision_response" and value not in {"accept", "reject", "revise"}:
                raise ValueError("continuation.decision_response must be accept, reject or revise")
            if dest in PATHS:
                def resolve(item):
                    target = Path(item).expanduser()
                    return str(target if target.is_absolute() else (path.parent / target).resolve())
                value = [resolve(item) for item in value] if expected is list else resolve(value)
            defaults[dest] = value
    allowances = defaults.get("authorize_remaining")
    if allowances is not None:
        if not allowances or any(
            not isinstance(key, str) or not key.strip()
            or type(value) not in {int, float}
            for key, value in allowances.items()
        ):
            raise ValueError("continuation.remaining must map resource names to numeric amounts")
    if "outputs" in defaults and (not defaults["outputs"] or set(defaults["outputs"]) - {"summary", "report", "experiments", "bug_fix", "data_analysis"}):
        raise ValueError("task.outputs must contain summary, report, experiments, bug_fix and/or data_analysis")
    if defaults.get("task_kind", "auto") not in {"auto", "survey", "bug_fix", "measurement", "reproduction", "writing", "data_analysis"}:
        raise ValueError("task.kind must be auto, survey, bug_fix, measurement, reproduction, writing or data_analysis")
    if defaults.get("task_kind") == "writing" and "outputs" in defaults and defaults["outputs"] != ["report"]:
        raise ValueError('task.kind=writing requires task.outputs = ["report"]')
    if "analysis" in data and defaults.get("task_kind") != "data_analysis":
        raise ValueError("analysis configuration requires task.kind=data_analysis")
    if defaults.get("task_kind") == "data_analysis" and defaults.get("outputs", ["data_analysis"]) != ["data_analysis"]:
        raise ValueError('task.kind=data_analysis requires only data_analysis in task.outputs')
    if defaults.get("task_kind") == "reproduction" and (
        "experiments" not in defaults.get("outputs", []) or set(defaults.get("outputs", [])) - {"experiments", "report"}
    ):
        raise ValueError("task.kind=reproduction requires experiments and optionally report in task.outputs")
    if defaults.get("task_kind") == "bug_fix" and "outputs" in defaults and set(defaults["outputs"]) != {"bug_fix"}:
        raise ValueError("task.kind=bug_fix requires task.outputs = [\"bug_fix\"] or an omitted outputs field")
    if defaults.get("task_kind") == "measurement" and defaults.get("outputs") != ["experiments"]:
        raise ValueError("task.kind=measurement requires task.outputs = [\"experiments\"]")
    if defaults.get("report_reviewer", "llm") not in {"llm", "disabled"}:
        raise ValueError("report.reviewer must be llm or disabled")
    for dest, flag in LIST_FLAGS.items():
        if any(option == flag or option.startswith(flag + "=") for option in options):
            defaults.pop(dest, None)
    return defaults
