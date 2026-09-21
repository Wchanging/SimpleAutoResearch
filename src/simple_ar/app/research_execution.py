"""Translate a bounded execution specification to the existing runner."""

from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any

from simple_ar.experiment.execution.backend import RunRequest
from simple_ar.research.experiment import ExperimentRequest
from simple_ar.research.contracts import ResearchExperimentContract
from simple_ar.research.implementation import ImplementationRequest
from simple_ar.code_task.editing.budget import VALID_BUDGET_PROFILES


def normalize_execution_config(
    config: Mapping,
    *,
    # Kept as a source-compatible no-op for callers that supplied the old
    # keyword. Natural-language interpretation belongs to planning/design, not
    # this deterministic argv adapter.
    task_text: str = "",
) -> dict[str, Any]:
    """Bind a compact declared protocol to literal execution pairs.

    This is the only expansion point for seed-aware execution.  It appends a
    declared flag/value to an already validated argv; it never parses shell
    text or turns a model suggestion into an executable command.
    """

    if not isinstance(config, Mapping):
        raise ValueError("Provide execution with command (argv), absolute cwd and timeout_sec.")
    normalized = dict(config)
    pairs = _validated_pairs(normalized.get("pairs", ()))
    baseline_policy = _baseline_policy(normalized, pairs=pairs)
    if pairs and any(key in normalized for key in ("seeds", "seed_count", "seed_flag")):
        raise ValueError("Use execution.pairs or compact seed settings, not both.")

    del task_text
    seed_values, seed_reason = _declared_seed_values(normalized)
    if seed_values and not pairs:
        seed_flag = normalized.get("seed_flag", "")
        if not isinstance(seed_flag, str) or not seed_flag.strip():
            raise ValueError(
                "Seed-aware execution requires an explicit execution.seed_flag; "
                "the task text cannot authorize command inference."
            )
        command = _argv(normalized.get("command"), "execution.command")
        baseline = normalized.get("baseline")
        baseline_command = command
        if isinstance(baseline, Mapping) and "command" in baseline:
            baseline_command = _argv(baseline.get("command"), "execution.baseline.command")
        pairs = tuple(
            {
                "seed": seed,
                "baseline_command": [*baseline_command, seed_flag.strip(), str(seed)],
                "candidate_command": [*command, seed_flag.strip(), str(seed)],
            }
            for seed in seed_values
        )
        normalized["pairs"] = [dict(row) for row in pairs]
        normalized["protocol_seed_reason"] = seed_reason
        normalized["protocol_seed_flag"] = seed_flag.strip()
    elif pairs:
        normalized["pairs"] = [dict(row) for row in pairs]
        normalized.setdefault(
            "protocol_seed_reason",
            "Paired execution conditions were explicitly declared in the accepted protocol.",
        )
    else:
        normalized.pop("pairs", None)

    for key in ("seeds", "seed_count", "seed_flag"):
        normalized.pop(key, None)
    normalized["baseline_policy"] = baseline_policy
    if baseline_policy == "run" and not pairs and "baseline" not in normalized:
        # A resolved run decision needs an executable control.  When no
        # separate evaluator was supplied, the inspected/configured command
        # is the only authorized same-condition evaluator; use it before any
        # candidate implementation.  If it is missing or malformed, leave
        # the input missing so execution_request raises the normal boundary
        # error instead of inventing a command.
        command = normalized.get("command")
        if isinstance(command, (list, tuple)) and command and all(
            isinstance(arg, str) and arg for arg in command
        ):
            normalized["baseline"] = {
                "command": list(command),
                "label": "baseline",
            }
    if "protocol_seed_reason" not in normalized:
        normalized["protocol_seed_reason"] = seed_reason
    return normalized


def execution_pairs(config: Mapping, *, task_text: str = "") -> tuple[Mapping, ...]:
    """Return the bounded literal pairs consumed by the existing runner."""

    return tuple(normalize_execution_config(config, task_text=task_text).get("pairs", ()))


def _validated_pairs(rows: object) -> tuple[Mapping, ...]:
    """Validate explicit paired argv without interpreting command text."""

    if not isinstance(rows, (list, tuple)):
        raise ValueError("execution.pairs must be an explicit list.")
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"seed", "baseline_command", "candidate_command"}:
            raise ValueError("Each execution pair requires seed, baseline_command and candidate_command.")
        seed = row["seed"]
        if type(seed) is not int or seed in seen:
            raise ValueError("Execution pair seeds must be unique integers.")
        seen.add(seed)
        for name in ("baseline_command", "candidate_command"):
            _argv(row[name], name)
    return tuple(rows)


def _argv(value: object, name: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value or any(
        not isinstance(arg, str) or not arg for arg in value
    ):
        raise ValueError(f"{name} must be a non-empty argv list, not shell text.")
    return list(value)


def _declared_seed_values(config: Mapping) -> tuple[tuple[int, ...], str]:
    explicit = config.get("seeds")
    if explicit is not None:
        return _seed_list(explicit, "execution.seeds"), "Seeds were explicitly declared in execution.seeds."
    protocol = config.get("protocol")
    comparison = protocol.get("comparison_conditions") if isinstance(protocol, Mapping) else None
    if isinstance(comparison, Mapping) and "seeds" in comparison:
        return _seed_list(comparison["seeds"], "execution.protocol.comparison_conditions.seeds"), (
            "Seeds were explicitly declared in the execution contract."
        )
    count = config.get("seed_count")
    if count is not None:
        if type(count) is not int or count < 1:
            raise ValueError("execution.seed_count must be a positive integer.")
        return tuple(range(count)), f"Seeds defaulted to 0..{count - 1} from execution.seed_count."
    return (), "No repeated seed condition was declared; the single configured execution is retained."


def _seed_list(value: object, name: str) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{name} must be a non-empty list of unique integers.")
    result: list[int] = []
    for item in value:
        if type(item) is not int or item in result:
            raise ValueError(f"{name} must contain unique integers.")
        result.append(item)
    return tuple(result)


def _baseline_policy(config: Mapping, *, pairs: tuple[Mapping, ...]) -> str:
    raw = str(config.get("baseline_policy") or "").strip().lower()
    if raw == "run":
        return "run"
    if raw == "skip":
        return "skip"
    if raw == "reuse":
        return "reuse"
    if raw:
        raise ValueError("execution.baseline_policy must be run, skip or reuse.")
    # A pair or an explicit baseline command is already an explicit comparison
    # request. Otherwise the honest deterministic default is no baseline;
    # design may replace it with a structured comparison decision later.
    if pairs or "baseline" in config:
        return "run"
    return "skip"


def execution_protocol(config: Mapping, *, task_text: str = "") -> dict[str, Any]:
    """Return a small explainable protocol projection for work-plan views."""

    normalized = normalize_execution_config(config, task_text=task_text)
    pairs = tuple(normalized.get("pairs", ()))
    seeds = [int(row["seed"]) for row in pairs]
    if not seeds:
        protocol = normalized.get("protocol")
        comparison = protocol.get("comparison_conditions") if isinstance(protocol, Mapping) else None
        if isinstance(comparison, Mapping) and type(comparison.get("seed")) is int:
            seeds = [int(comparison["seed"])]
    return {
        "condition_count": len(pairs) if pairs else 1,
        "seeds": seeds,
        "seed_reason": str(normalized.get("protocol_seed_reason") or "No repeated seed condition was declared."),
        "baseline_policy": str(normalized.get("baseline_policy") or "skip"),
        "paired": bool(pairs),
    }


def merge_execution_protocol(
    config: Mapping,
    protocol: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Apply a validated design proposal without replacing expert inputs.

    The design boundary may fill an omitted command, seed parameter or
    comparison policy. Explicit application configuration remains authoritative;
    this function only translates the small accepted protocol into the existing
    execution shape consumed by :func:`normalize_execution_config`.
    """

    merged = dict(config)
    if not isinstance(protocol, Mapping):
        return merged
    compact_keys = {"seeds", "seed_count", "seed_flag"}
    for key in (
        "command", "cwd", "timeout_sec", "result_schema", "pairs", "seeds",
        "seed_count", "seed_flag", "baseline_policy", "baseline_ref",
    ):
        if key == "pairs" and any(item in merged for item in compact_keys):
            continue
        if key in compact_keys and "pairs" in merged:
            continue
        if key not in merged and key in protocol:
            merged[key] = protocol[key]
    if "baseline" not in merged and "baseline_command" in protocol:
        merged["baseline"] = {
            "command": protocol["baseline_command"],
            "label": "baseline",
        }
    if "protocol" not in merged and isinstance(protocol.get("contract"), Mapping):
        merged["protocol"] = dict(protocol["contract"])
    return merged


def _merge_protocol_contract(
    configured: object,
    design: ResearchExperimentContract | Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Combine design facts with explicit execution protocol fields."""

    if design is None:
        return dict(configured) if isinstance(configured, Mapping) else configured
    if isinstance(design, ResearchExperimentContract):
        merged = design.to_row()
    elif isinstance(design, Mapping):
        merged = dict(design)
    else:
        raise ValueError("The research design contract must be an object.")
    if configured is not None:
        if not isinstance(configured, Mapping):
            raise ValueError("execution.protocol must be a research experiment contract object.")
        for key, value in configured.items():
            if (
                key in {"comparison_conditions", "split_spec", "resource_budget"}
                and isinstance(value, Mapping)
                and isinstance(merged.get(key), Mapping)
            ):
                merged[key] = {**dict(merged[key]), **dict(value)}
            else:
                merged[key] = value
    return merged


def execution_request(
    config: object,
    *,
    condition: str = "candidate",
    pair_index: int | None = None,
    task_text: str = "",
    contract: ResearchExperimentContract | Mapping[str, Any] | None = None,
) -> ExperimentRequest:
    """Build a runner request; do not infer shell commands or launch a process."""

    if not isinstance(config, Mapping):
        raise ValueError("Provide execution with command (argv), absolute cwd and timeout_sec.")
    config = normalize_execution_config(config, task_text=task_text)
    pairs = tuple(config.get("pairs", ()))
    config.pop("pairs", None)
    config.pop("code_task", None)  # Consumed by the separate implementation action.
    config.pop("baseline_policy", None)
    config.pop("baseline_ref", None)
    config.pop("protocol_seed_reason", None)
    config.pop("protocol_seed_flag", None)
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
        if pair_index is not None and not 0 <= pair_index < len(pairs):
            raise ValueError(f"Execution pair index {pair_index} is outside the accepted protocol.")
        pair = pairs[0 if pair_index is None else pair_index]
        role = "baseline" if condition == "baseline" else "candidate"
        config["command"] = pair[f"{role}_command"]
        config["label"] = f"{role}:seed={pair['seed']}"
        raw_protocol = config.get("protocol")
        if raw_protocol is not None and not isinstance(raw_protocol, Mapping):
            raise ValueError("execution.protocol must be a research experiment contract object.")
        protocol = dict(raw_protocol or {})
        comparison = protocol.get("comparison_conditions")
        if comparison is not None and not isinstance(comparison, Mapping):
            raise ValueError("execution.protocol.comparison_conditions must be an object.")
        protocol["comparison_conditions"] = {**dict(comparison or {}), "seed": pair["seed"]}
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
    protocol = _merge_protocol_contract(config.get("protocol"), contract)
    if protocol is not None and not isinstance(protocol, Mapping):
        raise ValueError("execution.protocol must be a research experiment contract object.")
    if protocol is not None:
        unsupported = set(protocol) - {item.name for item in fields(ResearchExperimentContract)}
        if unsupported:
            raise ValueError(f"Unsupported protocol fields: {', '.join(sorted(unsupported))}")
    try:
        parsed_contract = ResearchExperimentContract.from_row(protocol) if protocol is not None else None
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid execution.protocol: {exc}") from exc
    return ExperimentRequest(
        run=RunRequest(list(command), cwd, timeout, label=str(config.get("label", "experiment"))),
        result_schema=dict(schema),
        experiment_contract=parsed_contract,
    )


def implementation_request(
    config: Mapping,
    client: object,
    *,
    validate: bool = False,
    task_text: str = "",
    contract: ResearchExperimentContract | Mapping[str, Any] | None = None,
) -> ImplementationRequest:
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
    execution = execution_request(config, task_text=task_text, contract=contract)
    return ImplementationRequest(
        run_dir,
        execution.run.cwd,
        note,
        client,
        execution.normalized_experiment_contract(),
        validation_command=tuple(execution.run.command) if validate else None,
        validation_timeout_sec=execution.run.timeout_sec if validate else None,
        budget_profile=budget_profile,
        allow_large_edits=allow_large_edits,
    )


def repair_limit(config: Mapping) -> int:
    task = config.get("code_task", {})
    value = task.get("max_repairs", 0) if isinstance(task, Mapping) else 0
    if type(value) is not int or value < 0:
        raise ValueError("code_task.max_repairs must be a non-negative integer.")
    return value


__all__ = [
    "execution_pairs", "execution_protocol", "execution_request", "implementation_request",
    "merge_execution_protocol", "normalize_execution_config", "repair_limit",
]
