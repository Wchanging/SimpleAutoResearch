from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

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
        "backend": "greenfield_local",
        "architecture_mode": architecture_mode,
        "project_dir": "generated_project",
        "dependency_install_allowed": bool(dependency_plan.get("install_allowed")),
        "entrypoint": "python main.py",
    }
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


def _int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
