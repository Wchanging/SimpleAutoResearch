"""Opt-in environment preparation and checks through the existing process backend.

The preparation attempt owns the environment and receipts; this is not another
executor, installer service or environment database. Installation is executable
project input, not evidence that an experiment is scientifically valid.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import os
from pathlib import Path
import sys
from typing import Any

from simple_ar.core.capabilities import CapabilityContext, CapabilityResult
from simple_ar.experiment.execution.backend import ExecutionBackend, LocalExecutionBackend


def environment_profile(value: Any, *, project: Path | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) - {"mode", "requirements", "install_project", "python_executable", "timeout_sec", "check_command"}:
        raise ValueError("execution.environment accepts mode, requirements, install_project, python_executable, timeout_sec and check_command only.")
    mode = value.get("mode")
    check = value.get("check_command", [])
    if not isinstance(check, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in check):
        raise ValueError("environment.check_command must be an argv list of nonempty strings, without shell interpretation.")
    if mode not in ("current", "venv") or (mode == "current" and not check):
        raise ValueError('Use mode="venv", or mode="current" with an explicit check_command; otherwise omit the table.')
    if mode == "current" and (value.get("requirements") or value.get("install_project") or "python_executable" in value):
        raise ValueError("Current-environment checks do not install dependencies or override the interpreter; select it in the command itself.")
    requirements = value.get("requirements", [])
    if not isinstance(requirements, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in requirements):
        raise ValueError("environment.requirements must list project-relative requirements files.")
    for item in requirements:
        path = Path(item)
        if path.is_absolute() or ".." in path.parts or "\\" in item or item.startswith("-"):
            raise ValueError("Requirements paths must stay inside the named execution project.")
        if project is not None:
            resolved = (project / path).resolve()
            if not resolved.is_relative_to(project.resolve()) or not resolved.is_file():
                raise ValueError(f"Requirements file not found inside the project: {item}")
    install_project = value.get("install_project", False)
    if type(install_project) is not bool:
        raise ValueError("environment.install_project must be a boolean; omission does not install the project.")
    if install_project and project is not None:
        if not any((project / name).is_file() for name in ("pyproject.toml", "setup.py", "setup.cfg")):
            raise ValueError("Project installation requires pyproject.toml, setup.py or setup.cfg in execution.cwd; a declaration does not guarantee a build succeeds.")
    python = value.get("python_executable", sys.executable)
    if not isinstance(python, str) or not python.strip():
        raise ValueError("environment.python_executable must be an executable name/path.")
    timeout = value.get("timeout_sec", 300)
    if type(timeout) is not int or timeout < 1:
        raise ValueError("environment.timeout_sec must be positive.")
    return {"mode": mode, "requirements": list(dict.fromkeys(requirements)), "install_project": install_project,
            **({"python_executable": python} if mode == "venv" else {}), "timeout_sec": timeout,
            **({"check_command": list(check)} if check else {})}


def prepare_project_environment(*, context: CapabilityContext, request: Any,
                                backend: ExecutionBackend | None = None,
                                prepared_payload: Mapping[str, Any] | None = None) -> CapabilityResult:
    config = dict(request.execution)
    if request.run is None or "dataset" in config or config.get("pairs"):
        raise ValueError("Environment preparation requires one declared project command.")
    project = request.run.cwd.resolve()
    profile = environment_profile(config["environment"], project=project)
    if not project.is_dir():
        raise ValueError(f"Execution project not found: {project}")
    # Only the ordinary 'python' aliases are substituted. An explicit binary,
    # uv/conda launcher or shell remains the user's selected execution protocol.
    isolated = profile["mode"] == "venv"
    aliases = {"python", "python3", "python.exe", "python3.exe"}
    formal = list(config["command"]) if "code_task" in config else list(request.run.command)
    before = {}
    if "code_task" in config:
        from simple_ar.app.research_execution import code_task_validation
        from simple_ar.code_task.runtime.state import code_task_paths, load_code_task_manifest
        from simple_ar.experiment.execution.measurement import snapshot_protocol_assets
        task = dict(config["code_task"])
        run_dir = Path(task["run_dir"])
        if prepared_payload is None or code_task_paths(run_dir).workspace_dir.resolve() != project:
            raise ValueError("Environment installation requires the initialized isolated CodeTask workspace.")
        code_task_validation(config, required=True)
        manifest = load_code_task_manifest(run_dir)
        # Reuse the protocol's named assets. Only the actual Python checker/
        # formal entry and accepted source config are added; never hash a tree.
        locators = [argv[1] for argv in (formal, task["validation_command"])
                    if len(argv) > 1 and argv[1].endswith('.py')]
        conditions = config.get("protocol", {}).get("comparison_conditions", {})
        source_config = conditions.get("source_config") if isinstance(conditions, Mapping) else None
        if isinstance(source_config, str) and source_config.strip():
            locators.append(source_config)
        assets = [{"asset_id": f"entry:{locator}", "path": locator}
                  for locator in dict.fromkeys(locators) if (project / locator).is_file()]
        before = snapshot_protocol_assets(config.get("protocol"), project)
        before.update(snapshot_protocol_assets({"protected_assets": assets}, project))
    if isolated and formal[0] not in aliases:
        raise ValueError("Task venv requires the command's first argument to be python/python3; explicit interpreters and non-Python launchers are not silently replaced.")
    directory = context.store.root / "environment"
    if directory.exists() or (context.store.root / "environment_setup.json").exists():
        raise ValueError("Preparation environment already exists; use its recorded result or an explicit new attempt, not an in-place replay.")
    python = directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    steps = [[profile["python_executable"], "-m", "venv", str(directory)]] if isolated else []
    if isolated and (profile["requirements"] or profile["install_project"]):
        steps.append([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                      *[arg for path in profile["requirements"] for arg in ("-r", str(project / path))],
                      *([str(project)] if profile["install_project"] else [])])
    if isolated:
        steps.append([str(python), "-m", "pip", "check"])
    if profile.get("check_command"):
        check = list(profile["check_command"])
        if isolated and check[0] in aliases:
            check[0] = str(python)
        steps.append(check)
    observed = []
    refs = []
    selected_backend = backend or LocalExecutionBackend()
    limitations = [("Task venv; not an OS sandbox. Approved installation may run build code, access package indexes and write build metadata in the execution workspace." if isolated else "Current environment; no dependency installation or interpreter replacement. Checks execute project code, not in an OS sandbox."),
                   "Successful installation and pip check do not prove project imports, binary/GPU compatibility, data splits or scientific validity.",
                   "A check proves only its observed exit status; its outputs are preparation records, not scientific measurements. No automatic shortening or retry of the scientific command.",
                   "No automatic data download, framework-directed project-code edits or changes to the scientific command's arguments.",
                   *(["Only declared protocol assets and execution/checker entries are content-checked; CodeTask data/edit protections are not an OS sandbox for installation build code."] if "code_task" in config else [])]
    for number, argv in enumerate(steps, 1):
        # Allocate before launch. An interrupted attempt retains this unknown
        # result; normal completed recovery uses the existing prepared_execution.
        observed.append({"command": argv, "status": "allocated_result_unknown"})
        context.store.write_json("environment_setup.json", {"profile": profile, "steps": observed,
            "limitations": limitations}, kind="environment_setup", schema="environment_setup.v1")
        result = selected_backend.run(replace(request.run, command=argv, timeout_sec=profile["timeout_sec"],
            label=f"environment:{number}", output_dir=context.store.root / "environment_processes"))
        observed[-1] = result.to_json()
        refs.extend((context.store.write_text(f"environment_logs/{number}.stdout.txt", result.stdout, kind="execution_log"),
                     context.store.write_text(f"environment_logs/{number}.stderr.txt", result.stderr, kind="execution_log")))
        ref = context.store.write_json("environment_setup.json", {"profile": profile, "steps": observed,
            "limitations": limitations}, kind="environment_setup", schema="environment_setup.v1")
        if result.status != "passed":
            return CapabilityResult(status="failed", artifacts=(*refs, ref),
                diagnostics=(f"Environment preparation step {number} {result.status}; the scientific command was not run.",))
    if before:
        from simple_ar.experiment.execution.measurement import reconcile_protocol_assets
        integrity = reconcile_protocol_assets(before)
        guard = context.store.write_json("environment_integrity.json", integrity, kind="asset_integrity")
        refs.append(guard)
        if integrity["status"] == "changed":
            return CapabilityResult(status="failed", artifacts=(*refs, ref),
                diagnostics=("Environment setup changed a declared asset or execution/checker entry; no implementation or measurement is authorized.",))
    config.pop("environment")
    config["command"] = formal
    if isolated:
        config["command"][0] = str(python)
        baseline = config.get("baseline")
        if isinstance(baseline, Mapping) and isinstance(baseline.get("command"), (list, tuple)):
            argv = list(baseline["command"])
            if argv and argv[0] in aliases:
                config["baseline"] = {**baseline, "command": [str(python), *argv[1:]]}
        if "code_task" in config:
            from simple_ar.code_task.execution.environment import ensure_code_task_environment_policy
            ensure_code_task_environment_policy(run_dir, manifest,
                env_mode="external", python_executable=python)
            config["code_task"] = {**task, "env_mode": "external", "python_executable": str(python)}
    payload = {**(prepared_payload or {}), "schema_version": "prepared_execution.v1",
        "execution": config, "source_project": (prepared_payload or {}).get("source_project", str(project)), "workspace": str(project),
        "environment": {"mode": profile["mode"], **({"python_executable": str(python)} if isolated else {}), "setup_ref": ref.to_dict(), "requirements": profile["requirements"],
                        "install_project": profile["install_project"]},
        "limitations": [*limitations, *[item for item in (prepared_payload or {}).get("limitations", [])
                         if not item.startswith("No dependency installation")]]}
    prepared = context.store.write_json("execution.json", payload,
        kind="prepared_execution", schema="prepared_execution.v1", producer="research.preparation")
    return CapabilityResult(status="completed", artifacts=(*refs, ref, prepared))
