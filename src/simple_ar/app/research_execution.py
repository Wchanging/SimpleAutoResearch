"""Translate an explicit user execution specification to the existing runner."""

from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path

from simple_ar.experiment.execution.backend import RunRequest
from simple_ar.research.experiment import ExperimentRequest
from simple_ar.research.contracts import ResearchExperimentContract
from simple_ar.research.implementation import ImplementationRequest
from simple_ar.code_task.editing.budget import VALID_BUDGET_PROFILES


def execution_pairs(config: Mapping) -> tuple[Mapping, ...]:
    """Explicit paired argv, not parameter interpolation or a search space."""
    rows = config.get("pairs", ())
    if not isinstance(rows, (list, tuple)):
        raise ValueError("execution.pairs must be an explicit list.")
    keys = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"seed", "baseline_command", "candidate_command"}:
            raise ValueError("Each execution pair requires seed, baseline_command and candidate_command.")
        seed = row["seed"]
        if type(seed) is not int or seed in keys:
            raise ValueError("Execution pair seeds must be unique integers.")
        keys.add(seed)
        for name in ("baseline_command", "candidate_command"):
            argv = row[name]
            if not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(arg, str) or not arg for arg in argv):
                raise ValueError(f"{name} must be nonempty argv.")
    return tuple(rows)


def execution_request(config: object, *, condition: str = "candidate", pair_index: int | None = None) -> ExperimentRequest:
    """No command inference, shell parsing, installation or process launch."""
    if not isinstance(config, Mapping):
        raise ValueError("Provide execution with command (argv), absolute cwd and timeout_sec.")
    config = dict(config)
    pairs = execution_pairs(config)
    config.pop("pairs", None)
    config.pop("code_task", None)  # Consumed by the separate implementation action.
    if "baseline" in config:
        baseline = config.pop("baseline")
        if not isinstance(baseline, Mapping) or "command" not in baseline:
            raise ValueError("execution.baseline must provide an explicit command.")
        if condition == "baseline":
            config.update(baseline)
            config["label"] = baseline.get("label", "baseline")
        else:
            config.setdefault("label", "candidate")
    if pairs:
        pair = pairs[0 if pair_index is None else pair_index]
        role = "baseline" if condition == "baseline" else "candidate"
        config["command"] = pair[f"{role}_command"]
        config["label"] = f"{role}:seed={pair['seed']}"
        protocol = dict(config.get("protocol", {}))
        protocol["comparison_conditions"] = {**protocol.get("comparison_conditions", {}), "seed": pair["seed"]}
        config["protocol"] = protocol
    unknown = set(config) - {"command", "cwd", "timeout_sec", "result_schema", "label", "protocol"}
    if unknown:
        raise ValueError(f"Unsupported execution settings: {', '.join(sorted(unknown))}")
    command = config.get("command")
    if not isinstance(command, (list, tuple)) or not command or any(
        not isinstance(arg, str) or not arg for arg in command
    ):
        raise ValueError("execution.command must be a non-empty argv list, not shell text.")
    cwd = Path(str(config.get("cwd", "")))
    if not cwd.is_absolute() or not cwd.is_dir():
        raise ValueError("execution.cwd must be an existing absolute directory.")
    timeout = config.get("timeout_sec")
    if type(timeout) is not int or timeout < 1:
        raise ValueError("execution.timeout_sec must be a positive integer.")
    schema = config.get("result_schema", {})
    if not isinstance(schema, Mapping):
        raise ValueError("execution.result_schema must be an object.")
    protocol = config.get("protocol")
    if protocol is not None and not isinstance(protocol, Mapping):
        raise ValueError("execution.protocol must be a research experiment contract object.")
    if protocol is not None:
        unsupported = set(protocol) - {item.name for item in fields(ResearchExperimentContract)}
        if unsupported:
            raise ValueError(f"Unsupported protocol fields: {', '.join(sorted(unsupported))}")
    try:
        contract = ResearchExperimentContract.from_row(protocol) if protocol is not None else None
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid execution.protocol: {exc}") from exc
    return ExperimentRequest(
        run=RunRequest(list(command), cwd, timeout, label=str(config.get("label", "experiment"))),
        result_schema=dict(schema),
        experiment_contract=contract,
    )


def implementation_request(config: Mapping, client: object) -> ImplementationRequest:
    task = config.get("code_task")
    if not isinstance(task, Mapping) or set(task) - {
        "run_dir", "approval_note", "max_repairs", "budget_profile", "allow_large_edits"
    }:
        raise ValueError(
            "execution.code_task accepts run_dir, approval_note, max_repairs, "
            "budget_profile and allow_large_edits."
        )
    repair_limit(config)
    run_dir = Path(str(task.get("run_dir", "")))
    note = task.get("approval_note")
    if not run_dir.is_absolute() or not (run_dir / "manifest.json").is_file():
        raise ValueError("code_task.run_dir must be an absolute, initialized CodeTask run.")
    if not isinstance(note, str) or not note.strip():
        raise ValueError("code_task.approval_note must explicitly authorize isolated edits.")
    budget_profile = task.get("budget_profile")
    if budget_profile is not None:
        if not isinstance(budget_profile, str) or budget_profile.strip().lower() not in VALID_BUDGET_PROFILES:
            raise ValueError("code_task.budget_profile must be normal, large or absolute.")
        budget_profile = budget_profile.strip().lower()
    allow_large_edits = task.get("allow_large_edits", False)
    if not isinstance(allow_large_edits, bool):
        raise ValueError("code_task.allow_large_edits must be a boolean.")
    execution = execution_request(config)
    return ImplementationRequest(
        run_dir,
        execution.run.cwd,
        note,
        client,
        execution.normalized_experiment_contract(),
        budget_profile=budget_profile,
        allow_large_edits=allow_large_edits,
    )


def repair_limit(config: Mapping) -> int:
    task = config.get("code_task", {})
    value = task.get("max_repairs", 0) if isinstance(task, Mapping) else 0
    if type(value) is not int or value < 0:
        raise ValueError("code_task.max_repairs must be a non-negative integer.")
    return value
