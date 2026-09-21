"""Prepare an existing project with the established CodeTask workspace builder."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import os
import shlex
import subprocess
import sys

from simple_ar.code_task.orchestration.workflow import initialize_code_task
from simple_ar.code_task.review_pipeline import build_review_index
from simple_ar.core.capabilities import CapabilityContext, CapabilityResult
from simple_ar.experiment.execution.backend import RunRequest
from simple_ar.experiment.templates import build_experiment_code
from simple_ar.research.text_dataset import read_text_dataset


@dataclass(frozen=True)
class PreparationRequest:
    execution: Mapping[str, Any]
    task_text: str
    run: RunRequest | None = None


def inspect_execution_entry(execution: Mapping[str, Any]) -> dict[str, Any]:
    """Inspect the supplied project boundary without creating a workspace.

    The result is a compact fact pack for research design. It records existing
    entrypoints and the caller's benchmark argv, but never executes a process,
    installs dependencies, or grants a model a new cwd/timeout/resource scope.
    """

    config = dict(execution)
    facts: dict[str, Any] = {
        "schema_version": "execution_entry_facts.v1",
        "authorized_argv_prefixes": [],
        "entrypoint_candidates": [],
        "limitations": [
            "Static entry inspection does not prove runtime success or metric correctness.",
        ],
    }
    command = config.get("command")
    if isinstance(command, (list, tuple)) and command and all(isinstance(item, str) for item in command):
        facts["benchmark_argv"] = list(command)
        facts["authorized_argv_prefixes"].append(list(command))
    baseline = config.get("baseline")
    if isinstance(baseline, Mapping):
        baseline_command = baseline.get("command")
        if isinstance(baseline_command, (list, tuple)) and baseline_command and all(isinstance(item, str) for item in baseline_command):
            facts["baseline_argv"] = list(baseline_command)
            facts["authorized_argv_prefixes"].append(list(baseline_command))

    task = config.get("code_task")
    if isinstance(task, Mapping) and task.get("code_root"):
        root = Path(str(task["code_root"])).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("code_task.code_root must be an existing absolute project directory.")
        index = build_review_index(root, result_schema=config.get("result_schema"))
        entrypoints = [str(item) for item in index.get("entrypoints", [])]
        facts["project"] = {
            "root": str(root),
            "file_count": int(index.get("file_count", 0)),
            "python_file_count": int(index.get("python_file_count", 0)),
            "test_file_count": sum(
                1 for row in index.get("files", [])
                if isinstance(row, Mapping) and "test" in str(row.get("role") or "")
            ),
            "entrypoint_candidates": entrypoints,
        }
        facts["entrypoint_candidates"] = entrypoints
        for entrypoint in entrypoints:
            prefix = [sys.executable, entrypoint]
            if prefix not in facts["authorized_argv_prefixes"]:
                facts["authorized_argv_prefixes"].append(prefix)
    elif "dataset" in config:
        path = Path(str(config["dataset"])).expanduser().resolve()
        inspected = read_text_dataset(path)
        facts["dataset"] = {
            key: value for key, value in inspected.items() if key != "rows"
        }

    facts["configured_result_schema"] = dict(config.get("result_schema", {})) if isinstance(config.get("result_schema"), Mapping) else {}
    facts["configured_timeout_sec"] = config.get("timeout_sec")
    facts["configured_cwd"] = str(config.get("cwd") or "")
    return facts


def run_preparation_capability(*, context: CapabilityContext, request: PreparationRequest) -> CapabilityResult:
    if "dataset" in request.execution:
        return _prepare_text_baseline(context, request)
    config = dict(request.execution)
    task = dict(config["code_task"])
    if set(task) - {
        "code_root", "approval_note", "max_repairs", "allowed_patterns",
        "budget_profile", "allow_large_edits", "workspace_mode", "protected_patterns",
    }:
        raise ValueError(
            "Preparing code_task accepts code_root, approval_note, max_repairs, "
            "allowed_patterns, protected_patterns, budget_profile, allow_large_edits and workspace_mode."
        )
    allowed = task.pop("allowed_patterns", None)
    protected = task.pop("protected_patterns", ())
    workspace_mode = task.pop("workspace_mode", "auto")
    if workspace_mode not in {"auto", "copy", "git_worktree"}:
        raise ValueError("Research project preparation supports auto, copy or git_worktree; sparse/empty workspaces require standalone CodeTask.")
    for name, patterns in (("allowed_patterns", () if allowed is None else allowed), ("protected_patterns", protected)):
        if not isinstance(patterns, (list, tuple)) or any(not isinstance(p, str) or not p.strip() for p in patterns):
            raise ValueError(f"{name} must be a list of workspace-relative patterns; empty uses CodeTask defaults.")
    root = Path(task["code_root"])
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("code_task.code_root must be an existing absolute project directory.")
    if not isinstance(task.get("approval_note"), str) or not task["approval_note"].strip():
        raise ValueError("Preparing code_task requires explicit isolated-edit approval.")
    config.setdefault("cwd", str(root))
    if request.run.cwd.resolve() != root.resolve():
        raise ValueError("Preparation cwd must match code_root; shared data should use explicit external paths.")
    task_ref = context.store.write_text("inputs/task.md", request.task_text, kind="task_input", schema="markdown.v1")
    command = subprocess.list2cmdline(request.run.command) if os.name == "nt" else shlex.join(request.run.command)
    initialized = initialize_code_task(
        run_dir=context.store.root / "project_run", code_root=root,
        task_file=context.store.resolve(task_ref), benchmark_command=command,
        workspace_mode=workspace_mode,
        edit_scope_allowed_patterns=tuple(allowed or ()),
        edit_scope_protected_patterns=tuple(protected),
    )
    config["cwd"] = str(initialized.workspace_dir)
    if "baseline" in config:
        baseline = dict(config["baseline"])
        if "cwd" in baseline and Path(baseline["cwd"]).resolve() == root.resolve():
            baseline["cwd"] = str(initialized.workspace_dir)
        config["baseline"] = baseline
    task.pop("code_root")
    task["run_dir"] = str(initialized.run_dir)
    config["code_task"] = task
    ref = context.store.write_json("execution.json", {
        "schema_version": "prepared_execution.v1", "execution": config,
        "source_project": str(root), "workspace": str(initialized.workspace_dir),
        "copy_report": initialized.copy_report.to_json(),
        "workspace_info": initialized.workspace.to_manifest(run_dir=initialized.run_dir),
        "limitations": ["No dependency installation or dataset download; shared datasets remain external assets.",
                         "Copy modes may exclude large files; inspect the recorded copy report.",
                         *initialized.workspace.warnings],
    }, kind="prepared_execution", schema="prepared_execution.v1", producer="research.preparation")
    return CapabilityResult(status="completed", artifacts=(task_ref, ref))


def _prepare_text_baseline(context: CapabilityContext, request: PreparationRequest) -> CapabilityResult:
    config = request.execution
    if set(config) - {"dataset", "timeout_sec"}:
        raise ValueError("CSV baseline preparation accepts dataset and timeout_sec; other methods require an existing project.")
    path = Path(config["dataset"])
    timeout = config.get("timeout_sec")
    if not path.is_absolute() or path.suffix.lower() != ".csv":
        raise ValueError("dataset must be an absolute CSV path with text,label,split columns.")
    if type(timeout) is not int or timeout < 1:
        raise ValueError("CSV baseline requires positive timeout_sec.")
    inspected = read_text_dataset(path)
    rows = inspected.pop("rows")
    data = context.store.write_json("baseline/dataset.json", rows, kind="experiment_dataset")
    script = context.store.write_text("baseline/experiment.py", build_experiment_code({"template": "csv_text_classification"}),
                                      kind="experiment_code", schema="python.v1")
    inspection = context.store.write_json("dataset_inspection.json", inspected, kind="dataset_inspection")
    workspace = context.store.resolve(script).parent
    execution = {
        "command": [sys.executable, "experiment.py"], "cwd": str(workspace), "timeout_sec": timeout,
        "label": "text_baseline", "result_schema": {"primary_metric": "accuracy", "direction": "higher",
                                                     "required_metrics": ["accuracy", "macro_f1"]},
        "protocol": {"contract_id": "csv-text-baseline", "hypothesis": request.task_text,
                     "dataset_refs": [{"asset_id": "text_csv", "sha256": inspected["sha256"]}],
                     "split_spec": {split: [i for i, row in enumerate(rows) if row["split"] == split] for split in ("train", "eval")},
                     "metric_specs": [{"name": name, "unit": "fraction"} for name in ("accuracy", "macro_f1")],
                     "comparison_conditions": {"method": "CountVectorizer+LogisticRegression", "max_iter": 200, "seed": 0},
                     "protected_assets": [{"asset_id": "data", "path": "dataset.json"},
                                          {"asset_id": "evaluator", "path": "experiment.py"}]},
    }
    ref = context.store.write_json("execution.json", {
        "schema_version": "prepared_execution.v1", "execution": execution,
        "dataset_inspection": inspection.to_dict(),
        "limitations": inspected["limitations"] + ["One explicit CSV baseline, not paper reproduction or candidate search.",
                                                  "Default word tokenizer; language suitability has not been assessed."],
    }, kind="prepared_execution", schema="prepared_execution.v1", producer="research.preparation")
    return CapabilityResult(status="completed", artifacts=(data, script, inspection, ref),
                            diagnostics=tuple(inspected["limitations"]))
