from __future__ import annotations

from simple_ar.experiment.tools.specs import ExperimentToolSpec


def default_experiment_tool_specs() -> list[ExperimentToolSpec]:
    return [
        ExperimentToolSpec(
            name="read_experiment_contract",
            description="Read an archived eight-stage run's experiment contract, result schema, resource plan, and domain profile.",
            permission="read_only",
        ),
        ExperimentToolSpec(
            name="list_experiment_artifacts",
            description="List archived 05-design, 06-code, and 07-run experiment artifacts.",
            permission="read_only",
        ),
        ExperimentToolSpec(
            name="read_results_json",
            description="Read archived 07-run/results.json; this is not the current session attempt result API.",
            permission="read_only",
        ),
        ExperimentToolSpec(
            name="read_experiment_diagnosis",
            description="Read 07-run/diagnosis.json with guard, review, and repair-planning signals.",
            permission="read_only",
        ),
        ExperimentToolSpec(
            name="validate_results_schema",
            description="Run local result guard checks against 07-run/results.json and 05-design/result_schema.json.",
            permission="read_only",
        ),
        ExperimentToolSpec(
            name="inspect_execution_failure",
            description="Summarize stdout, stderr, and guard report for repair planning.",
            permission="read_only",
        ),
        ExperimentToolSpec(
            name="list_generated_code_files",
            description="List files in 06-code/generated_project with size and line-count metadata.",
            permission="read_only",
            input_schema={
                "type": "object",
                "properties": {
                    "extensions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional file extensions such as .py or .json.",
                    }
                },
            },
        ),
        ExperimentToolSpec(
            name="read_generated_code_file",
            description="Read a bounded line range from a generated-project file.",
            permission="read_only",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "max_lines": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["path"],
            },
        ),
        ExperimentToolSpec(
            name="search_generated_code",
            description="Search generated-project text files for a keyword, metric, or symbol name.",
            permission="read_only",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_matches": {"type": "integer", "minimum": 1, "maximum": 100},
                    "case_sensitive": {"type": "boolean"},
                },
                "required": ["query"],
            },
        ),
    ]


def experiment_tool_spec_map() -> dict[str, ExperimentToolSpec]:
    return {spec.name: spec for spec in default_experiment_tool_specs()}
