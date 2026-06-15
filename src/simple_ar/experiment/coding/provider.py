from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from simple_ar.agent_backends import (
    AgentPermissionPolicy,
    AgentRunRequest,
    build_greenfield_handoff,
    create_agent_backend,
    ingest_agent_outputs,
    is_external_agent_provider,
    normalize_agent_mode,
    validate_agent_mode_for_provider,
)
from simple_ar.core.artifacts import write_json, write_text
from simple_ar.integrations.llm import LLMClient
from simple_ar.experiment.coding.architecture import (
    build_architecture_plan,
    file_plan_from_architecture,
    render_architecture_markdown,
)
from simple_ar.experiment.coding.generation import (
    build_greenfield_harness_script,
    write_generated_project,
)
from simple_ar.experiment.coding.memory import initial_implementation_memory
from simple_ar.experiment.coding.review import review_generated_project


@dataclass(frozen=True, slots=True)
class GreenfieldImplementationResult:
    project_dir: Path
    experiment_script_path: Path
    architecture_plan_path: Path
    file_plan_path: Path
    memory_path: Path
    code_review_path: Path
    code_artifacts_path: Path
    code_backend_path: Path
    review_status: str


def implement_greenfield_project(
    *,
    stage_dir: Path,
    contract: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
    dependency_plan: Mapping[str, Any],
    domain_profile: Mapping[str, Any],
    client: LLMClient | None = None,
    implementation_provider: str = "local",
    agent_mode: str = "",
    allow_external_agent: bool = False,
    backend_timeout_sec: int = 600,
    agent_model: str = "",
    agent_binary: str = "",
    agent_args: list[str] | tuple[str, ...] = (),
) -> GreenfieldImplementationResult:
    """Plan, generate, review, and wrap a bounded greenfield project."""

    architecture, architecture_mode = build_architecture_plan(
        contract=contract,
        result_schema=result_schema,
        resource_plan=resource_plan,
        domain_profile=domain_profile,
        client=client,
    )
    architecture_path = stage_dir / "architecture_plan.json"
    file_plan_path = stage_dir / "file_plan.json"
    write_json(architecture_path, architecture)
    write_text(stage_dir / "architecture_plan.md", render_architecture_markdown(architecture))
    write_json(file_plan_path, file_plan_from_architecture(architecture))

    memory = initial_implementation_memory(
        contract=contract,
        architecture_plan=architecture,
        mode=architecture_mode,
    )
    project_dir = stage_dir / "generated_project"
    provider = implementation_provider.strip().lower().replace("-", "_") or "local"
    resolved_agent_mode = normalize_agent_mode(agent_mode, provider=provider)
    validate_agent_mode_for_provider(resolved_agent_mode, provider=provider)
    agent_result: dict[str, Any] | None = None
    if provider != "local" and is_external_agent_provider(provider):
        if not allow_external_agent and provider not in {"fake", "dry_run", "dryrun", "local_llm", "llm"}:
            raise RuntimeError(
                "External greenfield implementation provider requested but `implementation.allow_external_agent` "
                "is not enabled."
            )
        code_artifacts, agent_result = _write_project_from_agent_backend(
            stage_dir=stage_dir,
            project_dir=project_dir,
            provider=provider,
            agent_mode=resolved_agent_mode.value,
            contract=contract,
            result_schema=result_schema,
            architecture=architecture,
            memory=memory,
            client=client,
            timeout_sec=backend_timeout_sec,
            external_enabled=allow_external_agent,
            agent_model=agent_model,
            agent_binary=agent_binary,
            agent_args=tuple(agent_args),
        )
    else:
        code_artifacts = write_generated_project(
            project_dir=project_dir,
            architecture_plan=architecture,
            result_schema=result_schema,
            contract=contract,
            memory=memory,
            client=client,
            max_generated_lines=_int(resource_plan.get("max_generated_lines"), 1200),
            files_per_batch=_int(_generation_value(contract, "files_per_batch"), 4),
        )
    code_artifacts_path = stage_dir / "code_artifacts.json"
    write_json(code_artifacts_path, code_artifacts)
    memory_path = stage_dir / "implementation_memory.json"
    write_json(memory_path, memory)

    review = review_generated_project(
        project_dir=project_dir,
        code_artifacts=code_artifacts,
        result_schema=result_schema,
        resource_plan=resource_plan,
        client=client,
    )
    code_review_path = stage_dir / "code_review.json"
    write_json(code_review_path, review)
    backend = {
        "schema_version": "code_backend.v1",
        "backend": "greenfield_agent" if agent_result is not None else "greenfield_local",
        "provider": provider,
        "agent_mode": resolved_agent_mode.value,
        "architecture_mode": architecture_mode,
        "project_dir": "generated_project",
        "dependency_install_allowed": bool(dependency_plan.get("install_allowed")),
        "entrypoint": "python main.py",
    }
    if agent_result is not None:
        backend["agent_result"] = agent_result
    code_backend_path = stage_dir / "code_backend.json"
    write_json(code_backend_path, backend)
    experiment_script_path = stage_dir / "experiment.py"
    write_text(experiment_script_path, build_greenfield_harness_script())
    return GreenfieldImplementationResult(
        project_dir=project_dir,
        experiment_script_path=experiment_script_path,
        architecture_plan_path=architecture_path,
        file_plan_path=file_plan_path,
        memory_path=memory_path,
        code_review_path=code_review_path,
        code_artifacts_path=code_artifacts_path,
        code_backend_path=code_backend_path,
        review_status=str(review.get("status", "unknown")),
    )


def _generation_value(contract: Mapping[str, Any], name: str) -> object:
    generation = contract.get("generation_plan") or contract.get("generation")
    return generation.get(name) if isinstance(generation, Mapping) else None


def _write_project_from_agent_backend(
    *,
    stage_dir: Path,
    project_dir: Path,
    provider: str,
    agent_mode: str,
    contract: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    architecture: Mapping[str, Any],
    memory: dict[str, Any],
    client: LLMClient | None,
    timeout_sec: int,
    external_enabled: bool,
    agent_model: str = "",
    agent_binary: str = "",
    agent_args: tuple[str, ...] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    run_dir = stage_dir.parent
    package = build_greenfield_handoff(
        run_dir,
        name=f"greenfield-{provider}",
        permission_policy=AgentPermissionPolicy(
            allow_file_write=True,
            allow_shell_commands=True,
            allow_network=False,
            allowed_write_patterns=["generated_files/**", "review.md", "results.json", "agent_result.json"],
            notes=[
                "Write generated source files only under generated_files/.",
                "Shell use is only allowed for creating handoff output files inside this directory.",
                "Do not install dependencies, run the generated project, access secrets, or read/write outside the handoff.",
                "Ignore previous-run archives or stale logs; the current instructions and artifact handles are authoritative.",
                "SimpleAutoResearch will copy, validate, and run the generated project separately.",
            ],
        ),
    )
    request = AgentRunRequest(
        provider=provider,
        run_dir=run_dir,
        handoff_dir=package.handoff_dir,
        workspace_dir=stage_dir,
        timeout_sec=timeout_sec,
        metadata={
            "mode": "greenfield",
            "agent_mode": agent_mode,
            "result_schema": dict(result_schema),
            "architecture_file_count": len(architecture.get("files", [])) if isinstance(architecture.get("files"), list) else 0,
        },
    )
    backend = create_agent_backend(
        provider,
        enabled=external_enabled,
        client=client,
        model=agent_model or None,
        timeout_sec=timeout_sec,
        binary=agent_binary or None,
        extra_args=agent_args,
    )
    result = backend.run(request)
    ingestion = ingest_agent_outputs(run_dir=run_dir, handoff_dir=package.handoff_dir)
    generated_dir = package.handoff_dir / "generated_files"
    generated_files = _generated_file_paths(generated_dir)
    if not result.ok or not generated_dir.is_dir() or not generated_files:
        raise RuntimeError(
            "Agent greenfield backend did not produce validated generated_files/. "
            f"Status={result.status}; generated_file_count={len(generated_files)}; "
            f"message={result.message}; "
            f"result={result.result_path or package.handoff_dir}."
        )
    if project_dir.exists():
        shutil.rmtree(project_dir)
    shutil.copytree(generated_dir, project_dir)
    artifacts = _code_artifacts_from_project(project_dir, source=f"agent:{provider}")
    memory.setdefault("agent_backends", []).append(
        {
            "provider": provider,
            "status": result.status,
            "handoff_dir": package.handoff_dir.relative_to(run_dir).as_posix(),
            "ingestion": ingestion,
        }
    )
    return artifacts, {
        "provider": provider,
        "status": result.status,
        "handoff_dir": package.handoff_dir.relative_to(run_dir).as_posix(),
        "ingestion": ingestion,
        "result_path": result.result_path,
        "review_path": result.review_path,
    }


def _code_artifacts_from_project(project_dir: Path, *, source: str) -> dict[str, Any]:
    generated: list[dict[str, Any]] = []
    total_lines = 0
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = _safe_project_path(path.relative_to(project_dir).as_posix())
        if not rel:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        line_count = max(1, len(text.splitlines()))
        total_lines += line_count
        generated.append(
            {
                "path": rel,
                "mode": source,
                "line_count": line_count,
                "summary": "Generated by external agent backend.",
            }
        )
    return {
        "schema_version": "greenfield_code_artifacts.v1",
        "project_dir": str(project_dir),
        "generated_files": generated,
        "total_lines": total_lines,
        "entrypoint": "main.py",
    }


def _generated_file_paths(generated_dir: Path) -> list[Path]:
    if not generated_dir.is_dir():
        return []
    return [path for path in generated_dir.rglob("*") if path.is_file()]


def _safe_project_path(value: str) -> str:
    value = value.replace("\\", "/").strip().lstrip("/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    return path.as_posix()


def _int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
