from __future__ import annotations

import json
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from simple_ar.core.artifacts import write_text
from simple_ar.integrations.llm import LLMClient, LLMError
from simple_ar.code_task.analysis.interfaces import dependency_context, order_file_specs, public_api
from simple_ar.code_task.generation.implementation_memory import record_generated_file, record_generation_batch
from simple_ar.code_task.generation.scaffold import fallback_file_content


def write_generated_project(
    *,
    project_dir: Path,
    architecture_plan: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    contract: Mapping[str, Any],
    memory: dict[str, Any],
    dependency_advice: Mapping[str, Any] | None = None,
    client: LLMClient | None = None,
    max_generated_lines: int = 1200,
    files_per_batch: int = 4,
    retry_attempts: int = 2,
    allow_fallback: bool = False,
) -> dict[str, Any]:
    """Write a bounded generated project from a file plan."""

    if project_dir.exists():
        shutil.rmtree(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    files = order_file_specs([row for row in architecture_plan.get("files", []) if isinstance(row, Mapping)])
    files = files[: max(1, len(files))]
    generated: list[dict[str, Any]] = []
    total_lines = 0
    batch_files: list[str] = []
    batch_id = "batch-001"

    for index, file_spec in enumerate(files, start=1):
        rel_path = _safe_path(str(file_spec.get("path", "")))
        if not rel_path:
            continue
        content, mode, summary = _file_content(
            file_spec=file_spec,
            architecture_plan=architecture_plan,
            result_schema=result_schema,
            contract=contract,
            dependency_api=dependency_context(project_dir, file_spec),
            dependency_advice=dependency_advice or {},
            memory=memory,
            retry_attempts=retry_attempts,
            allow_fallback=allow_fallback,
            client=client,
        )
        line_count = max(1, len(content.splitlines()))
        if total_lines + line_count > max_generated_lines and generated:
            break
        target = project_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text(target, content)
        exported_api = public_api(target) if target.suffix == ".py" else []
        total_lines += line_count
        generated.append(
            {
                "path": rel_path,
                "mode": mode,
                "line_count": line_count,
                "summary": summary,
                "public_api": exported_api,
            }
        )
        record_generated_file(memory, path=rel_path, summary=summary, mode=mode, public_api=exported_api)
        batch_files.append(rel_path)
        if len(batch_files) >= max(1, files_per_batch):
            record_generation_batch(memory, batch_id=batch_id, files=batch_files, mode=mode)
            batch_files = []
            batch_id = f"batch-{index + 1:03d}"
    total_lines = _ensure_required_entrypoint(
        project_dir=project_dir,
        result_schema=result_schema,
        contract=contract,
        generated=generated,
        memory=memory,
        total_lines=total_lines,
    )
    if batch_files:
        record_generation_batch(memory, batch_id=batch_id, files=batch_files, mode="mixed")
    return {
        "schema_version": "greenfield_code_artifacts.v1",
        "project_dir": str(project_dir),
        "generated_files": generated,
        "total_lines": total_lines,
        "entrypoint": "main.py",
    }


def _ensure_required_entrypoint(
    *,
    project_dir: Path,
    result_schema: Mapping[str, Any],
    contract: Mapping[str, Any],
    generated: list[dict[str, Any]],
    memory: dict[str, Any],
    total_lines: int,
) -> int:
    """Guarantee that line-budget truncation cannot remove the CLI entrypoint."""

    if any(row.get("path") == "main.py" for row in generated) and (project_dir / "main.py").is_file():
        return total_lines
    content = fallback_file_content("main.py", result_schema, contract)
    target = project_dir / "main.py"
    write_text(target, content)
    exported_api = public_api(target)
    line_count = max(1, len(content.splitlines()))
    row = {
        "path": "main.py",
        "mode": "deterministic_entrypoint_repair",
        "line_count": line_count,
        "summary": "Deterministic thin entrypoint added because the generated file set omitted main.py.",
        "public_api": exported_api,
    }
    generated.insert(0, row)
    record_generated_file(
        memory,
        path="main.py",
        summary=str(row["summary"]),
        mode=str(row["mode"]),
        public_api=exported_api,
    )
    return total_lines + line_count


def build_greenfield_harness_script(project_dir_name: str = "generated_project") -> str:
    return f'''from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    stage_dir = Path(__file__).resolve().parent
    project_dir = stage_dir / {project_dir_name!r}
    completed = subprocess.run(
        [sys.executable, "main.py"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.stderr:
        print(completed.stderr.rstrip(), file=sys.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _file_content(
    *,
    file_spec: Mapping[str, Any],
    architecture_plan: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    contract: Mapping[str, Any],
    dependency_api: Mapping[str, Any],
    dependency_advice: Mapping[str, Any],
    memory: Mapping[str, Any],
    retry_attempts: int,
    allow_fallback: bool,
    client: LLMClient | None,
) -> tuple[str, str, str]:
    path = _safe_path(str(file_spec.get("path", "")))
    if client is not None:
        feedback = ""
        for attempt in range(max(2, int(retry_attempts or 2))):
            try:
                response = client.ask_json(
                    GREENFIELD_FILE_SYSTEM,
                    greenfield_file_prompt(
                        file_spec=file_spec,
                        architecture_plan=architecture_plan,
                        result_schema=result_schema,
                        contract=contract,
                        dependency_api=dependency_api,
                        dependency_advice=dependency_advice,
                        implementation_memory=memory,
                        retry_feedback=feedback,
                    ),
                    label=f"greenfield-file-{path}" if attempt == 0 else f"greenfield-file-retry-{path}",
                )
            except LLMError as exc:
                feedback = f"The previous request failed validation: {exc}. Return smaller, complete Python."
                continue
            content = str(response.get("content", "")).strip()
            summary = str(response.get("summary", "")).strip() or str(file_spec.get("purpose", ""))
            if content and not _looks_like_markdown_fence(content):
                cleaned = _repair_common_generation_error(path, content.rstrip() + "\n")
                if _is_valid_file_content(cleaned, filename=path):
                    mode = "llm_repaired" if cleaned != content.rstrip() + "\n" else "llm"
                    return cleaned, mode, summary[:500]
            feedback = (
                "The previous response was empty, fenced, or invalid for the requested file type. "
                "Return a complete, concise file in the JSON content field and preserve exact dependency APIs."
            )
        if not allow_fallback:
            raise LLMError(
                f"LLM file generation failed for `{path}` after {max(2, int(retry_attempts or 2))} attempt(s); "
                "fallback is disabled for real greenfield runs."
            )
    elif not allow_fallback:
        raise LLMError(f"LLM file generation requires an LLM client for `{path}` when fallback is disabled.")
    summary = str(file_spec.get("purpose", ""))[:420]
    return fallback_file_content(path, result_schema, contract), "fallback", f"{summary} [LLM file generation unavailable]"


def greenfield_file_prompt(
    *,
    file_spec: Mapping[str, Any],
    architecture_plan: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    contract: Mapping[str, Any],
    dependency_api: Mapping[str, Any] | None = None,
    dependency_advice: Mapping[str, Any] | None = None,
    implementation_memory: Mapping[str, Any] | None = None,
    retry_feedback: str = "",
) -> str:
    path = _safe_path(str(file_spec.get("path", "")))
    file_kind = "Python" if path.endswith(".py") else ("JSON" if path.endswith(".json") else "text/Markdown")
    return (
        f"Generate exactly one {file_kind} file for this bounded Python project. "
        "Return JSON with string fields `content` and `summary`.\n\n"
        "Rules:\n"
        "- Use the Python standard library and dependencies explicitly declared by the file spec, architecture plan, "
        "or experiment contract. Do not invent or install new dependencies.\n"
        "- If a planned dependency is optional or unavailable, provide a bounded fallback or fail with a clear message.\n"
        "- Do not access network, shell, credentials, user home directories, or external datasets.\n"
        "- The project entrypoint must print each required metric as `metric_name: number`.\n"
        "- Required metrics must be computed from real project outputs. Do not fill missing required metrics with 0.0, empty records, or placeholder values.\n"
        "- Implement only this file's planned responsibility. Do not duplicate a full "
        "experiment pipeline in helper modules when another planned file owns orchestration.\n"
        "- Keep one authoritative `run_experiment` path for metric calculation; helper "
        "modules should expose data/model/metric functions used by that path.\n"
        "- Keep this single file complete, cohesive, and proportional to its planned responsibility. "
        "Simple helpers should stay small; core modules may be longer when the task genuinely requires it.\n"
        "- Prefer deterministic, testable logic over broad simulations, noisy logs, or unstructured frameworks.\n"
        "- Do not leave placeholders, unfinished functions, unterminated literals, or truncated JSON/Python/Markdown.\n"
        "- For Markdown files, write useful user-facing documentation, not an empty placeholder.\n"
        "- For JSON files, return valid JSON content only.\n\n"
        "Existing dependency contract:\n"
        "- The dependency APIs below come from files already written to disk and are authoritative.\n"
        "- Import and call the exact exported names and signatures. Do not invent synonyms or alternate helper names.\n"
        "- If the planned file cannot work with an existing dependency API, adapt this file rather than assuming a missing API.\n\n"
        "Project continuity contract:\n"
        "- Use the implementation memory to preserve decisions, shared data schemas, and generated public APIs from earlier files.\n"
        "- Treat explicit task requirements, deliverables, and metric contracts as hard requirements unless they conflict with safety/resource limits.\n"
        "- If this file creates records consumed by another planned file, include stable field names and document them in code-level constants or dataclasses.\n"
        "- If this file consumes records from another planned file, consume the existing producer schema instead of inventing a new one.\n\n"
        f"File spec:\n{json.dumps(dict(file_spec), indent=2, ensure_ascii=False)}\n\n"
        f"Actual dependency APIs:\n{json.dumps(dict(dependency_api or {}), indent=2, ensure_ascii=False)}\n\n"
        f"Dependency advice:\n{json.dumps(_dependency_advice_for_prompt(dependency_advice or {}), indent=2, ensure_ascii=False)}\n\n"
        f"Implementation memory:\n{json.dumps(_memory_for_prompt(implementation_memory or {}), indent=2, ensure_ascii=False)}\n\n"
        f"Architecture plan:\n{json.dumps(dict(architecture_plan), indent=2, ensure_ascii=False)}\n\n"
        f"Result schema:\n{json.dumps(dict(result_schema), indent=2, ensure_ascii=False)}\n\n"
        f"Experiment contract:\n{json.dumps(dict(contract), indent=2, ensure_ascii=False)}\n"
        + (f"\nRetry feedback:\n{retry_feedback}\n" if retry_feedback else "")
    )


GREENFIELD_FILE_SYSTEM = (
    "You are a cautious code implementer for bounded reproducible projects. "
    "Write runnable, maintainable Python files that satisfy the provided metric schema and architecture plan."
)


def _dependency_advice_for_prompt(advice: Mapping[str, Any], *, package_limit: int = 80) -> dict[str, Any]:
    packages = advice.get("packages")
    rows = [row for row in packages if isinstance(row, Mapping)] if isinstance(packages, list) else []
    environment_packages = advice.get("environment_packages")
    environment_rows = (
        [row for row in environment_packages if isinstance(row, Mapping)]
        if isinstance(environment_packages, list)
        else []
    )
    return {
        "schema_version": advice.get("schema_version", "code_task_dependency_advice.v1"),
        "policy": advice.get("policy", "advice_only_no_auto_install"),
        "selection_policy": advice.get("selection_policy", ""),
        "environment_package_count": advice.get("environment_package_count", 0),
        "installed_packages": advice.get("installed_packages", []),
        "missing_required": advice.get("missing_required", []),
        "missing_recommended": advice.get("missing_recommended", []),
        "missing_optional": advice.get("missing_optional", []),
        "risky_packages": advice.get("risky_packages", []),
        "task_relevant_packages": rows[:package_limit],
        "environment_packages_sample": environment_rows[:package_limit],
        "notes": advice.get("notes", []),
    }


def _memory_for_prompt(memory: Mapping[str, Any], *, file_limit: int = 40) -> dict[str, Any]:
    file_summaries = memory.get("file_summaries")
    batches = memory.get("generated_batches")
    repairs = memory.get("repair_history")
    reviews = memory.get("review_findings")
    return {
        "schema_version": memory.get("schema_version", "implementation_memory.v1"),
        "mode": memory.get("mode", ""),
        "task": memory.get("task", {}),
        "accepted_decisions": _string_list(memory.get("accepted_decisions"), limit=20),
        "file_summaries": _mapping_list(file_summaries, limit=file_limit),
        "generated_batches": _mapping_list(batches, limit=20),
        "open_issues": _string_list(memory.get("open_issues"), limit=20),
        "review_findings": _mapping_list(reviews, limit=20),
        "repair_history": _mapping_list(repairs, limit=10),
    }


def _mapping_list(value: Any, *, limit: int) -> list[dict[str, Any]]:
    rows = [dict(row) for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []
    return rows[-limit:]


def _string_list(value: Any, *, limit: int) -> list[str]:
    rows = [str(row) for row in value if str(row).strip()] if isinstance(value, list) else []
    return rows[-limit:]


def _safe_path(value: str) -> str:
    value = value.replace("\\", "/").strip().lstrip("/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    return path.as_posix()


def _looks_like_markdown_fence(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("```") or stripped.endswith("```")


def _is_valid_file_content(value: str, *, filename: str) -> bool:
    if filename.endswith(".py"):
        try:
            compile(value, filename, "exec")
        except SyntaxError:
            return False
    if filename.endswith(".json"):
        try:
            json.loads(value)
        except json.JSONDecodeError:
            return False
    return bool(value.strip())


def _repair_common_generation_error(path: str, value: str) -> str:
    """Fix tiny deterministic generation glitches before writing a file.

    This is deliberately narrow. Broad semantic fixes belong in the review and
    repair loop, but accepting a package marker with two stray leading
    underscores before a triple-quoted docstring would waste an otherwise
    useful run.
    """

    stripped = value.lstrip("\ufeff")
    leading = value[: len(value) - len(stripped)]
    if path.endswith("__init__.py"):
        for marker in ('__"""', "__'''"):
            if stripped.startswith(marker):
                return leading + stripped[2:]
    return value
