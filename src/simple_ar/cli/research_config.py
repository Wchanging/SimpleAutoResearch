"""Map research TOML onto the same options used by the CLI."""
import argparse
from pathlib import Path
import tomllib


FIELDS = {
    "task": {"goal": ("topic", str), "kind": ("task_kind", str), "outputs": ("outputs", list), "output_root": ("output_root", str),
              "selected_idea_id": ("selected_idea_id", str)},
    "model": {"name": ("model", str), "max_output_tokens": ("max_output_tokens", int)},
    "budget": {"total_tokens": ("total_tokens", int), "llm_requests": ("llm_requests", int),
               "process_invocations": ("process_invocations", int), "process_wall_seconds": ("process_wall_seconds", int)},
    "research": {"providers": ("providers", list), "queries": ("queries", list),
                 "max_results": ("max_results", int), "max_chunks": ("max_chunks", int),
                 "idea_limit": ("idea_limit", int), "cache_dir": ("cache_dir", str),
                 "use_fulltext": ("research_use_fulltext", bool),
                 "materials_only": ("research_materials_only", bool),
                 "allow_pdf_download": ("research_allow_pdf_download", bool),
                 "keep_raw_pdf": ("research_keep_raw_pdf", bool)},
    "assets": {"papers": ("local_document", list)},
    "execution": {"command": ("command_argv", list), "cwd": ("cwd", str),
                  "timeout_sec": ("timeout_sec", int), "code_task_config": ("code_task_config", str),
                  "primary_metric": ("primary_metric", str), "metrics": ("metric", list),
                  "metric_directions": ("metric_direction", list)},
    "report": {"template": ("report_template", str), "reviewer": ("report_reviewer", str),
               "max_review_iterations": ("max_review_iterations", int),
               "max_section_tokens": ("max_section_tokens", int),
               "figures": ("report_figures", dict)},
}
PATHS = {"output_root", "cache_dir", "cwd", "code_task_config", "local_document"}
LIST_FLAGS = {"providers": "--provider", "queries": "--query", "local_document": "--local-document",
              "metric": "--metric", "metric_direction": "--metric-direction"}


def research_defaults(arguments: list[str]) -> dict:
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
                "pairs", "protocol", "seeds", "seed_flag", "seed_count", "baseline_policy", "baseline_ref",
            }:
                if name == "pairs":
                    from simple_ar.app.research_execution import execution_pairs
                    execution_pairs({"pairs": value})
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
            if type(value) is not expected or (expected is list and any(type(item) is not str for item in value)):
                raise ValueError(f"Invalid type for {section}.{name}: expected {expected.__name__}")
            if expected is int and value < (0 if dest in {"max_review_iterations", "max_section_tokens", "process_invocations", "process_wall_seconds"} else 1):
                raise ValueError(f"Invalid value for {section}.{name}: {value}")
            if dest in PATHS:
                def resolve(item):
                    target = Path(item).expanduser()
                    return str(target if target.is_absolute() else (path.parent / target).resolve())
                value = [resolve(item) for item in value] if expected is list else resolve(value)
            defaults[dest] = value
    if "outputs" in defaults and (not defaults["outputs"] or set(defaults["outputs"]) - {"summary", "report", "experiments", "bug_fix"}):
        raise ValueError("task.outputs must contain summary, report, experiments and/or bug_fix")
    if defaults.get("task_kind", "auto") not in {"auto", "survey", "bug_fix"}:
        raise ValueError("task.kind must be auto, survey or bug_fix")
    if defaults.get("task_kind") == "bug_fix" and "outputs" in defaults and set(defaults["outputs"]) != {"bug_fix"}:
        raise ValueError("task.kind=bug_fix requires task.outputs = [\"bug_fix\"] or an omitted outputs field")
    if defaults.get("report_reviewer", "llm") not in {"llm", "disabled"}:
        raise ValueError("report.reviewer must be llm or disabled")
    for dest, flag in LIST_FLAGS.items():
        if any(option == flag or option.startswith(flag + "=") for option in options):
            defaults.pop(dest, None)
    return defaults
