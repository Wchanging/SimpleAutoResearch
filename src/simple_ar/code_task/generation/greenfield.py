from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from simple_ar.app.usage import summarize_usage
from simple_ar.code_task.analysis.index import build_codebase_index
from simple_ar.code_task.analysis.repo_map import build_repo_map
from simple_ar.code_task.editing.scope import (
    allowed_patterns_from_manifest,
    protected_patterns_from_manifest,
)
from simple_ar.code_task.execution.summary import write_code_task_summary
from simple_ar.code_task.runtime.state import (
    code_task_paths,
    load_code_task_manifest,
    manifest_section,
    save_code_task_manifest,
    utcnow_iso,
)
from simple_ar.core.artifacts import append_jsonl, read_json, read_jsonl, read_text, write_json, write_text
from simple_ar.code_task.generation.architecture import (
    build_architecture_plan,
    file_plan_from_architecture,
    render_architecture_markdown,
)
from simple_ar.code_task.generation.writer import write_generated_project
from simple_ar.code_task.generation.implementation_memory import initial_implementation_memory
from simple_ar.code_task.generation.review import review_generated_project
from simple_ar.integrations.llm import LLMClient, LLMError, LLMUsage

from .agent_backend import (
    should_use_agent_backend,
    write_greenfield_project_from_agent_backend,
)
from .dependencies import (
    build_dependency_advice,
    dependency_advice_messages,
    render_dependency_advice_markdown,
)


MessageCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class GreenfieldCodeTaskResult:
    run_dir: Path
    project_dir: Path
    implementation_plan_path: Path
    architecture_plan_path: Path
    file_plan_path: Path
    code_artifacts_path: Path
    review_report_path: Path
    review_status: str
    generated_files: tuple[str, ...]


def generate_greenfield_code_task(
    run_dir: Path,
    *,
    model: str | None = None,
    use_llm: bool = True,
    max_files: int = 8,
    max_generated_lines: int = 1600,
    max_source_chars_per_file: int = 4000,
    implementation_provider: str = "local",
    implementation_agent_mode: str = "",
    implementation_allow_external_agent: bool = False,
    implementation_agent_model: str = "",
    implementation_agent_binary: str = "",
    implementation_agent_args: tuple[str, ...] = (),
    implementation_agent_timeout_sec: int = 600,
    message_callback: MessageCallback | None = None,
) -> GreenfieldCodeTaskResult:
    """Generate a greenfield project inside a code-task workspace.

    This is intentionally a code-task strategy, not an 8-stage experiment
    special case. It writes the same durable code-task artifacts as existing
    project runs: implementation metadata under ``code_task/meta``, generated
    files under ``code_task/workspace``, memory under ``code_task/memory``, and
    benchmark settings in the shared manifest.
    """

    root = Path(run_dir)
    paths = code_task_paths(root)
    manifest = load_code_task_manifest(root)
    if _code_task_kind(manifest) != "greenfield":
        raise RuntimeError("generate_greenfield_code_task requires [code_task].kind = greenfield")
    paths.meta_dir.mkdir(parents=True, exist_ok=True)
    memory_dir = paths.task_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    task_text = _read_task(paths.task_dir / "task.md", limit=max_source_chars_per_file * 2)
    resource_decision = _optional_json(paths.meta_dir / "resource_decision.json")
    contract = _contract_from_task(
        task_text,
        benchmark_command=_benchmark_command(manifest),
        max_files=max_files,
        max_generated_lines=max_generated_lines,
    )
    result_schema = _result_schema_from_manifest(manifest)
    resource_plan = _resource_plan(resource_decision, max_files=max_files, max_generated_lines=max_generated_lines)
    dependency_plan = _dependency_plan(task_text)
    domain_profile = _domain_profile(task_text)
    dependency_advice = build_dependency_advice(task_text)
    dependency_advice_path = paths.meta_dir / "dependency_advice.json"
    write_json(dependency_advice_path, dependency_advice)
    write_text(paths.meta_dir / "dependency_advice.md", render_dependency_advice_markdown(dependency_advice))
    for message in dependency_advice_messages(dependency_advice):
        _emit(message_callback, message)

    client = _llm_client(
        paths.meta_dir,
        model=model,
        use_llm=use_llm,
        message_callback=message_callback,
    )
    _emit(message_callback, "Planning greenfield project architecture.")
    architecture, architecture_mode = build_architecture_plan(
        contract=contract,
        result_schema=result_schema,
        resource_plan=resource_plan,
        domain_profile=domain_profile,
        client=client,
    )
    implementation_plan_path = paths.meta_dir / "implementation_plan.json"
    architecture_plan_path = paths.meta_dir / "architecture_plan.json"
    file_plan_path = paths.meta_dir / "file_plan.json"
    write_json(
        implementation_plan_path,
        {
            "schema_version": "code_task_implementation_plan.v1",
            "mode": "greenfield",
            "architecture_mode": architecture_mode,
            "provider": _normalize_provider(implementation_provider),
            "agent_mode": implementation_agent_mode or "",
            "project_dir": "code_task/workspace/generated_project",
            "entrypoint": _benchmark_command(manifest),
            "resource_plan": resource_plan,
            "dependency_plan": dependency_plan,
            "dependency_advice": "code_task/meta/dependency_advice.json",
        },
    )
    write_json(architecture_plan_path, architecture)
    write_text(paths.meta_dir / "architecture_plan.md", render_architecture_markdown(architecture))
    write_json(file_plan_path, file_plan_from_architecture(architecture))

    memory = initial_implementation_memory(
        contract=contract,
        architecture_plan=architecture,
        mode=architecture_mode,
    )
    project_dir = paths.workspace_dir / "generated_project"
    provider = _normalize_provider(implementation_provider)
    agent_result: dict[str, Any] | None = None
    if should_use_agent_backend(provider):
        _emit(message_callback, f"Generating project through `{provider}` agent handoff.")
        code_artifacts, agent_result = write_greenfield_project_from_agent_backend(
            run_dir=root,
            project_dir=project_dir,
            provider=provider,
            agent_mode=implementation_agent_mode,
            contract=contract,
            result_schema=result_schema,
            architecture=architecture,
            memory=memory,
            client=client,
            timeout_sec=implementation_agent_timeout_sec,
            external_enabled=implementation_allow_external_agent,
            agent_model=implementation_agent_model,
            agent_binary=implementation_agent_binary,
            agent_args=implementation_agent_args,
        )
    else:
        _emit(message_callback, "Writing generated project files.")
        code_artifacts = write_generated_project(
            project_dir=project_dir,
            architecture_plan=architecture,
            result_schema=result_schema,
            contract=contract,
            memory=memory,
            client=client,
            max_generated_lines=max_generated_lines,
            files_per_batch=4,
        )
    generated_files = tuple(
        f"generated_project/{row.get('path')}"
        for row in code_artifacts.get("generated_files", [])
        if isinstance(row, dict) and row.get("path")
    )
    code_artifacts_path = paths.meta_dir / "code_artifacts.json"
    write_json(code_artifacts_path, code_artifacts)
    write_json(memory_dir / "implementation_memory.json", memory)
    write_json(
        paths.meta_dir / "code_backend.json",
        {
            "schema_version": "code_backend.v1",
            "backend": "greenfield_agent" if agent_result else "greenfield_local",
            "provider": provider,
            "agent_mode": implementation_agent_mode or ("handoff" if agent_result else "model"),
            "project_dir": "code_task/workspace/generated_project",
            "entrypoint": _benchmark_command(manifest),
            "agent_result": agent_result,
        },
    )

    _refresh_codebase_artifacts(root, generated_files=generated_files)
    _emit(message_callback, "Reviewing generated greenfield project.")
    review = review_generated_project(
        project_dir=project_dir,
        code_artifacts=code_artifacts,
        result_schema=result_schema,
        resource_plan=resource_plan,
        contract=contract,
        dependency_advice=dependency_advice,
        implementation_memory=memory,
        architecture_plan=architecture,
        client=client,
        meta_dir=paths.meta_dir,
    )
    review_report_path = paths.meta_dir / "review_report.json"
    write_json(review_report_path, review)
    _update_manifest_after_generation(
        root,
        manifest=load_code_task_manifest(root),
        generated_files=generated_files,
        project_dir=project_dir,
        review=review,
        provider=provider,
        agent_mode=implementation_agent_mode or ("handoff" if agent_result else "model"),
    )
    write_code_task_summary(root)
    return GreenfieldCodeTaskResult(
        run_dir=root,
        project_dir=project_dir,
        implementation_plan_path=implementation_plan_path,
        architecture_plan_path=architecture_plan_path,
        file_plan_path=file_plan_path,
        code_artifacts_path=code_artifacts_path,
        review_report_path=review_report_path,
        review_status=str(review.get("status", "unknown")),
        generated_files=generated_files,
    )


def _llm_client(
    meta_dir: Path,
    *,
    model: str | None,
    use_llm: bool,
    message_callback: MessageCallback | None,
) -> LLMClient | None:
    if not use_llm:
        return None
    try:
        return LLMClient.from_env(
            model=model,
            usage_callback=lambda usage: _record_usage(
                meta_dir,
                usage,
                message_callback=message_callback,
            ),
        )
    except LLMError as exc:
        _emit(message_callback, f"LLM unavailable; using bounded fallback generation. {exc}")
        return None


def _record_usage(
    meta_dir: Path,
    usage: LLMUsage,
    *,
    message_callback: MessageCallback | None,
) -> None:
    usage_path = meta_dir / "llm_usage.jsonl"
    row = usage.to_row()
    row["stage"] = "code_task.greenfield"
    append_jsonl(usage_path, row)
    write_json(meta_dir / "llm_usage_summary.json", summarize_usage(read_jsonl(usage_path)))
    _emit(
        message_callback,
        f"LLM usage {row.get('label', '')}: "
        f"{row['prompt_tokens']} input + {row['completion_tokens']} output = "
        f"{row['total_tokens']} tokens ({row['source']}).",
    )


def _contract_from_task(
    task_text: str,
    *,
    benchmark_command: str,
    max_files: int,
    max_generated_lines: int,
) -> dict[str, Any]:
    objective = _first_meaningful_line(task_text) or "Implement the requested greenfield project."
    return {
        "schema_version": "code_task_greenfield_contract.v1",
        "contract_id": "code-task-greenfield",
        "objective": objective,
        "task": task_text,
        "benchmark_command": benchmark_command,
        "success_criteria": [
            "Generated project lives under code_task/workspace/generated_project.",
            f"The configured benchmark command exits with status 0 exactly as written: `{benchmark_command}`.",
            "The entrypoint prints parseable metric lines when metrics are requested.",
            "No network access or destructive filesystem behavior is required.",
        ],
        "generation_plan": {
            "max_files": max_files,
            "max_generated_lines": max_generated_lines,
            "files_per_batch": 4,
        },
    }


def _result_schema_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    benchmark = manifest.get("benchmark", {}) if isinstance(manifest.get("benchmark"), dict) else {}
    primary = str(benchmark.get("primary_metric") or "score").strip() or "score"
    directions = benchmark.get("metric_directions")
    required = [primary]
    if isinstance(directions, dict):
        required.extend(str(name) for name in directions if str(name).strip() and str(name) != primary)
    return {
        "schema_version": "code_task_greenfield_result_schema.v1",
        "primary_metric": primary,
        "required_metrics": list(dict.fromkeys(required)),
        "metric_directions": directions if isinstance(directions, dict) else {},
    }


def _resource_plan(
    resource_decision: dict[str, Any],
    *,
    max_files: int,
    max_generated_lines: int,
) -> dict[str, Any]:
    profile = str(resource_decision.get("profile") or "local_cpu")
    return {
        "schema_version": "code_task_greenfield_resource_plan.v1",
        "profile": profile,
        "allow_gpu": bool(resource_decision.get("allow_gpu")),
        "max_files": max_files,
        "max_generated_lines": max_generated_lines,
        "decision": resource_decision,
    }


def _dependency_plan(task_text: str) -> dict[str, Any]:
    mentioned = _mentioned_packages(task_text)
    return {
        "schema_version": "code_task_greenfield_dependency_plan.v1",
        "install_allowed": False,
        "allowed_dependency_policy": "standard_library_preferred_or_explicitly_available",
        "mentioned_packages": mentioned,
        "notes": [
            "Generated code should prefer the Python standard library for local smoke paths.",
            "If the task explicitly names installed packages, generated code may use them but must not install new dependencies.",
            "If an optional package is missing, fail clearly or provide a bounded fallback when the task allows it.",
        ],
    }


def _mentioned_packages(task_text: str) -> list[str]:
    text = task_text.lower()
    candidates = [
        "numpy",
        "pandas",
        "scikit-learn",
        "sklearn",
        "torch",
        "pytorch",
        "transformers",
        "datasets",
        "rich",
        "pydantic",
        "tree-sitter",
        "lancedb",
    ]
    return [name for name in candidates if name in text]


def _domain_profile(task_text: str) -> dict[str, Any]:
    return {
        "schema_version": "code_task_greenfield_domain_profile.v1",
        "task_excerpt": task_text[:2000],
        "expected_entrypoints": ["python generated_project/main.py"],
    }


def _refresh_codebase_artifacts(run_dir: Path, *, generated_files: tuple[str, ...]) -> None:
    paths = code_task_paths(run_dir)
    index_path = paths.meta_dir / "codebase_index.json"
    codebase_index = build_codebase_index(paths.workspace_dir, output_path=index_path)
    allowed = allowed_patterns_from_manifest(load_code_task_manifest(run_dir))
    protected = protected_patterns_from_manifest(load_code_task_manifest(run_dir))
    repo_map_path = paths.meta_dir / "repo_map.json"
    repo_map_summary_path = paths.meta_dir / "repo_map_summary.md"
    repo_map = build_repo_map(
        codebase_index,
        output_path=repo_map_path,
        summary_path=repo_map_summary_path,
        allowed_patterns=tuple(allowed),
        protected_patterns=tuple(protected),
    )
    manifest = load_code_task_manifest(run_dir)
    codebase = manifest_section(manifest, "codebase")
    project = codebase_index.get("project", {}) if isinstance(codebase_index.get("project"), dict) else {}
    repo_project = repo_map.get("project", {}) if isinstance(repo_map.get("project"), dict) else {}
    codebase.update(
        {
            "file_count": project.get("file_count", 0),
            "python_file_count": project.get("python_file_count", 0),
            "test_file_count": project.get("test_file_count", 0),
            "entrypoint_candidates": project.get("entrypoint_candidates", []),
            "generated_files": list(generated_files),
            "repo_map": {
                "schema_version": repo_map.get("schema_version"),
                "path": "code_task/meta/repo_map.json",
                "summary": "code_task/meta/repo_map_summary.md",
                "directory_count": repo_project.get("directory_count", 0),
                "symbol_count": repo_project.get("symbol_count", 0),
                "benchmark_file_count": repo_project.get("benchmark_file_count", 0),
                "config_file_count": repo_project.get("config_file_count", 0),
            },
        }
    )
    manifest["codebase"] = codebase
    save_code_task_manifest(run_dir, manifest)


def _update_manifest_after_generation(
    run_dir: Path,
    *,
    manifest: dict[str, Any],
    generated_files: tuple[str, ...],
    project_dir: Path,
    review: dict[str, Any],
    provider: str,
    agent_mode: str,
) -> None:
    layout = manifest_section(manifest, "layout")
    layout.update(
        {
            "generated_project": "code_task/workspace/generated_project",
            "implementation_plan": "code_task/meta/implementation_plan.json",
            "architecture_plan": "code_task/meta/architecture_plan.json",
            "file_plan": "code_task/meta/file_plan.json",
            "code_artifacts": "code_task/meta/code_artifacts.json",
            "code_backend": "code_task/meta/code_backend.json",
            "review_report": "code_task/meta/review_report.json",
        }
    )
    manifest["layout"] = layout
    implementation = manifest_section(manifest, "implementation")
    implementation.update(
        {
            "status": "generated" if review.get("status") != "failed" else "review_failed",
            "mode": "greenfield",
            "generated_at": utcnow_iso(),
            "project_dir": "code_task/workspace/generated_project",
            "generated_files": list(generated_files),
            "review_status": review.get("status", "unknown"),
            "provider": _normalize_provider(provider),
            "agent_mode": agent_mode,
        }
    )
    manifest["implementation"] = implementation
    patch = manifest_section(manifest, "patch")
    patch.update(
        {
            "status": "applied",
            "mode": "greenfield_generated",
            "changed_files": list(generated_files),
            "applied_at": utcnow_iso(),
        }
    )
    manifest["patch"] = patch
    benchmark = manifest_section(manifest, "benchmark")
    if not benchmark.get("command"):
        benchmark["command"] = "python generated_project/main.py"
    manifest["benchmark"] = benchmark
    manifest["status"] = "generated" if review.get("status") != "failed" else "review_failed"
    save_code_task_manifest(run_dir, manifest)


def _benchmark_command(manifest: dict[str, Any]) -> str:
    benchmark = manifest.get("benchmark")
    if isinstance(benchmark, dict) and benchmark.get("command"):
        return str(benchmark["command"])
    return "python generated_project/main.py"


def _read_task(path: Path, *, limit: int) -> str:
    text = read_text(path) if path.is_file() else ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = read_json(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().strip("#").strip()
        if stripped:
            return stripped[:240]
    return ""


def _code_task_kind(manifest: dict[str, Any]) -> str:
    section = manifest.get("code_task")
    if isinstance(section, dict):
        return str(section.get("kind") or "existing_project").strip().lower()
    return "existing_project"


def _normalize_provider(value: object) -> str:
    return str(value or "local").strip().lower().replace("-", "_") or "local"


def _emit(callback: MessageCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)
