from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from simple_ar.core.artifacts import write_text
from simple_ar.integrations.llm import LLMClient, LLMError
from simple_ar.code_task.analysis.interfaces import dependency_context, order_file_specs, public_api
from simple_ar.code_task.analysis.python_source import has_non_ascii_identifier
from simple_ar.code_task.generation.agent_step import run_json_agent_step
from simple_ar.code_task.generation.common import mapping_list, safe_relative_path, string_list
from simple_ar.code_task.generation.file_specs import is_model_generated_file, is_runtime_placeholder
from simple_ar.code_task.generation.implementation_memory import record_generated_file, record_generation_batch
from simple_ar.code_task.generation.scaffold import fallback_file_content
from simple_ar.code_task.generation.task_contract import contract_prompt_view


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
    agent_step_dir: Path | None = None,
    message_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Write a bounded generated project from a file plan."""

    if project_dir.exists():
        shutil.rmtree(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    files = order_file_specs([row for row in architecture_plan.get("files", []) if isinstance(row, Mapping)])
    files = files[: max(1, len(files))]
    planned_parent_dirs = _planned_parent_dirs(files)
    generated: list[dict[str, Any]] = []
    total_lines = 0
    batch_files: list[str] = []
    batch_id = "batch-001"

    for index, file_spec in enumerate(files, start=1):
        rel_path = safe_relative_path(str(file_spec.get("path", "")))
        if not rel_path:
            continue
        if is_runtime_placeholder(file_spec) or rel_path in planned_parent_dirs:
            generated.append(_create_runtime_placeholder(project_dir, rel_path, file_spec))
            continue
        if not is_model_generated_file(file_spec):
            file_spec = dict(file_spec)
            file_spec["kind"] = "data"
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
            agent_step_dir=agent_step_dir,
            message_callback=message_callback,
            client=client,
        )
        line_count = max(1, len(content.splitlines()))
        if total_lines + line_count > max_generated_lines and generated:
            raise LLMError(
                f"Generated file `{rel_path}` would exceed max_generated_lines "
                f"({total_lines + line_count}>{max_generated_lines}); refusing to write a partial project."
            )
        target = project_dir / rel_path
        if target.parent.exists() and not target.parent.is_dir():
            raise LLMError(
                f"Generated file plan has a file/directory conflict: parent `{target.parent.relative_to(project_dir)}` "
                f"must be a directory before writing `{rel_path}`."
            )
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
    agent_step_dir: Path | None,
    message_callback: Callable[[str], None] | None,
    client: LLMClient | None,
) -> tuple[str, str, str]:
    path = safe_relative_path(str(file_spec.get("path", "")))
    if client is not None:
        feedback = ""
        attempts = max(2, int(retry_attempts or 2))
        for attempt in range(1, attempts + 1):
            try:
                prompt = greenfield_file_prompt(
                    file_spec=file_spec,
                    architecture_plan=architecture_plan,
                    result_schema=result_schema,
                    contract=contract,
                    dependency_api=dependency_api,
                    dependency_advice=dependency_advice,
                    implementation_memory=memory,
                    retry_feedback=feedback,
                )
                response = run_json_agent_step(
                    client=client,
                    system=GREENFIELD_FILE_SYSTEM,
                    prompt=prompt,
                    label=f"greenfield-file-{path}" if attempt == 1 else f"greenfield-file-retry-{path}",
                    stage=f"file-{path.replace('/', '_')}",
                    attempt_index=attempt,
                    retry_attempts=1,
                    max_output_tokens=_file_output_tokens(path, file_spec),
                    artifact_dir=agent_step_dir,
                    feedback=[feedback] if feedback else [],
                    output_summary_callback=_file_output_summary,
                    message_callback=message_callback,
                )
            except LLMError as exc:
                feedback = f"The previous request failed validation: {exc}. Return smaller, complete Python."
                if attempt < attempts:
                    delay = _stage_retry_delay(attempt)
                    _emit(
                        message_callback,
                        f"File generation for `{path}` failed "
                        f"(attempt {attempt}/{attempts}); retrying in {delay:.1f}s. {exc}",
                    )
                    time.sleep(delay)
                continue
            content = str(response.get("content", "")).strip()
            summary = str(response.get("summary", "")).strip() or str(file_spec.get("purpose", ""))
            if _response_self_reports_defect(response):
                feedback = (
                    "The previous response self-reported an unresolved typo, broken import, or file that must be "
                    "corrected before execution. Return a corrected, runnable file and do not mention known defects "
                    "in the summary unless they are fully fixed."
                )
                continue
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
                f"LLM file generation failed for `{path}` after {attempts} attempt(s); "
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
    path = safe_relative_path(str(file_spec.get("path", "")))
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
        "- Entrypoints may print friendly errors, but must not catch broad exceptions without traceback.print_exc(), "
        "logging.exception/logger.exception, or re-raising; repair needs real traceback files and lines.\n"
        "- Implement only this file's planned responsibility. Do not duplicate a full "
        "experiment pipeline in helper modules when another planned file owns orchestration.\n"
        "- Keep one authoritative `run_experiment` path for metric calculation; helper "
        "modules should expose data/model/metric functions used by that path.\n"
        "- Keep this single file complete, cohesive, and proportional to its planned responsibility. "
        "Simple helpers should stay small; core modules may be longer when the task genuinely requires it.\n"
        "- Keep Python files concise enough for reliable transport: target <= 120 lines for normal modules "
        "and <= 160 lines only for the main orchestrator or core runner. Prefer more small planned files "
        "over one large file.\n"
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
        "- Treat the task evidence_plan as authoritative: hypotheses, comparisons, required datasets/conditions, and artifacts must remain traceable through records and reports.\n"
        "- If this file creates records consumed by another planned file, include stable field names and document them in code-level constants or dataclasses.\n"
        "- If this file consumes records from another planned file, consume the existing producer schema instead of inventing a new one.\n\n"
        "Metric and evidence data-flow contract:\n"
        "- If a downstream metric needs features, labels, predictions, losses, condition names, seeds, or per-dataset rows, pass those fields explicitly instead of reconstructing them from summaries.\n"
        "- Aggregation code must preserve enough cell-level evidence to justify every required comparison; do not collapse records before analysis/reporting modules have consumed them.\n\n"
        f"File spec:\n{json.dumps(dict(file_spec), indent=2, ensure_ascii=False)}\n\n"
        f"Actual dependency APIs:\n{json.dumps(dict(dependency_api or {}), indent=2, ensure_ascii=False)}\n\n"
        f"Dependency advice:\n{json.dumps(_dependency_advice_for_prompt(dependency_advice or {}), indent=2, ensure_ascii=False)}\n\n"
        f"Implementation memory:\n{json.dumps(_memory_for_prompt(implementation_memory or {}), indent=2, ensure_ascii=False)}\n\n"
        f"Project planning context:\n{json.dumps(_architecture_for_file_prompt(architecture_plan, file_spec), indent=2, ensure_ascii=False)}\n\n"
        f"Result schema:\n{json.dumps(dict(result_schema), indent=2, ensure_ascii=False)}\n\n"
        f"Experiment contract:\n{json.dumps(_contract_for_file_prompt(contract), indent=2, ensure_ascii=False)}\n"
        + (f"\nRetry feedback:\n{retry_feedback}\n" if retry_feedback else "")
    )


GREENFIELD_FILE_SYSTEM = (
    "You are a cautious code implementer for bounded reproducible projects. "
    "Write runnable, maintainable Python files that satisfy the provided metric schema and architecture plan."
)


def _architecture_for_file_prompt(
    architecture_plan: Mapping[str, Any],
    file_spec: Mapping[str, Any],
    *,
    file_limit: int = 32,
    context_limit: int = 12,
) -> dict[str, Any]:
    """Return the compact planning slice needed to write one file."""

    current_path = safe_relative_path(str(file_spec.get("path", "")))
    files = architecture_plan.get("files")
    rows = [dict(row) for row in files if isinstance(row, Mapping)] if isinstance(files, list) else []
    by_path = {safe_relative_path(str(row.get("path", ""))): row for row in rows}
    dependencies = [
        by_path[path]
        for path in (safe_relative_path(item) for item in string_list(file_spec.get("dependencies"), limit=context_limit))
        if path in by_path
    ]
    consumers = [
        row
        for row in rows
        if current_path
        and current_path in {safe_relative_path(item) for item in string_list(row.get("dependencies"), limit=64)}
    ][:context_limit]
    return {
        "schema_version": architecture_plan.get("schema_version", "greenfield_architecture.v1"),
        "objective": architecture_plan.get("objective", ""),
        "architecture_summary": architecture_plan.get("architecture_summary", ""),
        "current_file": dict(file_spec),
        "dependency_files": _compact_file_specs(dependencies, limit=context_limit),
        "consumer_files": _compact_file_specs(consumers, limit=context_limit),
        "project_file_outline": _compact_file_specs(rows, limit=file_limit),
        "interfaces": string_list(architecture_plan.get("interfaces"), limit=16),
        "data_flow": string_list(architecture_plan.get("data_flow"), limit=12),
        "test_strategy": string_list(architecture_plan.get("test_strategy"), limit=10),
        "risks": string_list(architecture_plan.get("risks"), limit=8),
        "metric_contract": _compact_mapping(architecture_plan.get("metric_contract"), limit=2800),
        "artifact_contract": _compact_mapping(architecture_plan.get("artifact_contract"), limit=2800),
        "resource_contract": _compact_mapping(architecture_plan.get("resource_contract"), limit=2200),
        "interface_registry": _compact_mapping(architecture_plan.get("interface_registry"), limit=4200),
        "planning": architecture_plan.get("planning", {}),
    }


def _compact_file_specs(files: list[Mapping[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in files[:limit]:
        result.append(
            {
                "path": safe_relative_path(str(row.get("path", ""))),
                "purpose": str(row.get("purpose", ""))[:240],
                "dependencies": string_list(row.get("dependencies"), limit=12),
                "public_api": string_list(row.get("public_api"), limit=12),
                "entrypoint": bool(row.get("entrypoint")),
                "kind": str(row.get("kind") or ""),
            }
        )
    return [row for row in result if row["path"]]


def _compact_mapping(value: object, *, limit: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    text = json.dumps(dict(value), ensure_ascii=False, default=str)
    if len(text) <= limit:
        return dict(value)
    return {"truncated_json": text[:limit], "truncated": True}


def _dependency_advice_for_prompt(advice: Mapping[str, Any], *, package_limit: int = 40) -> dict[str, Any]:
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


def _file_output_tokens(path: str, file_spec: Mapping[str, Any]) -> int:
    """Bound one file-generation response to avoid provider long-output stalls."""

    suffix = Path(path).suffix.lower()
    role_text = " ".join(
        [
            path,
            str(file_spec.get("purpose", "")),
            " ".join(str(item) for item in string_list(file_spec.get("acceptance_criteria"), limit=8)),
        ]
    ).lower()
    if suffix in {".md", ".txt", ".json", ".toml", ".yaml", ".yml"}:
        return 900
    if path == "main.py":
        return 1400
    if any(token in role_text for token in ("runner", "orchestrator", "experiment", "pipeline", "core")):
        return 1900
    return 1500


def _contract_for_file_prompt(contract: Mapping[str, Any]) -> dict[str, Any]:
    return contract_prompt_view(
        contract,
        max_task_chars=900,
        max_requirements=24,
        max_success_criteria=14,
    )


def _memory_for_prompt(memory: Mapping[str, Any], *, file_limit: int = 12) -> dict[str, Any]:
    file_summaries = memory.get("file_summaries")
    batches = memory.get("generated_batches")
    repairs = memory.get("repair_history")
    reviews = memory.get("review_findings")
    return {
        "schema_version": memory.get("schema_version", "implementation_memory.v1"),
        "mode": memory.get("mode", ""),
        "task": _compact_memory_task(memory.get("task")),
        "accepted_decisions": string_list(memory.get("accepted_decisions"), limit=8, tail=True),
        "file_summaries": mapping_list(file_summaries, limit=file_limit, tail=True),
        "generated_batches": mapping_list(batches, limit=8, tail=True),
        "open_issues": string_list(memory.get("open_issues"), limit=8, tail=True),
        "review_findings": mapping_list(reviews, limit=8, tail=True),
        "repair_history": mapping_list(repairs, limit=6, tail=True),
    }


def _planned_parent_dirs(files: list[Mapping[str, Any]]) -> set[str]:
    parents: set[str] = set()
    paths = [safe_relative_path(str(row.get("path", ""))) for row in files]
    for path in paths:
        if not path:
            continue
        current = Path(path).parent
        while current.as_posix() not in {"", "."}:
            parents.add(current.as_posix())
            current = current.parent
    return parents


def _create_runtime_placeholder(
    project_dir: Path,
    rel_path: str,
    file_spec: Mapping[str, Any],
) -> dict[str, Any]:
    target = project_dir / rel_path
    if target.exists() and target.is_dir():
        pass
    elif target.suffix:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    else:
        if target.exists() and not target.is_dir():
            target.unlink()
        target.mkdir(parents=True, exist_ok=True)
    return {
        "path": rel_path,
        "mode": "deterministic_runtime_placeholder",
        "kind": str(file_spec.get("kind") or "artifact_placeholder"),
        "line_count": 0,
        "summary": str(file_spec.get("purpose") or "Runtime output placeholder created deterministically.")[:500],
        "public_api": [],
    }


def _compact_memory_task(value: object) -> dict[str, Any]:
    task = value if isinstance(value, Mapping) else {}
    metric_contract = task.get("metric_contract") if isinstance(task.get("metric_contract"), Mapping) else {}
    evidence_plan = task.get("evidence_plan") if isinstance(task.get("evidence_plan"), Mapping) else {}
    return {
        "objective": str(task.get("objective") or "")[:300],
        "explicit_requirements": string_list(task.get("explicit_requirements"), limit=12),
        "constraints": string_list(task.get("constraints"), limit=8),
        "evaluation_focus": string_list(task.get("evaluation_focus"), limit=10),
        "metric_contract": {
            "primary_metric": metric_contract.get("primary_metric", ""),
            "required_metrics": string_list(metric_contract.get("required_metrics"), limit=30),
            "default_fill_policy": metric_contract.get("default_fill_policy", ""),
        },
        "evidence_plan": {
            "required_conditions": string_list(evidence_plan.get("required_conditions"), limit=10),
            "required_datasets": string_list(evidence_plan.get("required_datasets"), limit=8),
            "required_metrics": string_list(evidence_plan.get("required_metrics"), limit=30),
            "required_artifacts": string_list(evidence_plan.get("required_artifacts"), limit=8),
            "required_comparisons": string_list(evidence_plan.get("required_comparisons"), limit=8),
        },
    }


def _looks_like_markdown_fence(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("```") or stripped.endswith("```")


def _is_valid_file_content(value: str, *, filename: str) -> bool:
    if filename.endswith(".py"):
        try:
            compile(value, filename, "exec")
        except SyntaxError:
            return False
        if has_non_ascii_identifier(value, path=filename):
            return False
    if filename.endswith(".json"):
        try:
            json.loads(value)
        except json.JSONDecodeError:
            return False
    return bool(value.strip())


def _response_self_reports_defect(response: Mapping[str, Any]) -> bool:
    text = " ".join(
        str(response.get(key) or "")
        for key in ("summary", "notes", "known_issues", "limitations")
    ).lower()
    if not text.strip():
        return False
    patterns = (
        r"\b(?:contains|includes|has)\b.{0,80}\btypo\b",
        r"\b(?:should|must|needs? to)\s+be\s+(?:corrected|fixed|repaired|resolved)\b",
        r"\b(?:unresolved|remaining|known)\b.{0,80}\bbefore\s+execution\b",
        r"\bnot\s+(?:runnable|executable)\b",
        r"\bwill\s+fail\b",
        r"\bknown\s+(?:bug|issue|defect)\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _file_output_summary(stage: str, output: Mapping[str, Any]) -> dict[str, Any]:
    content = str(output.get("content") or "")
    summary = str(output.get("summary") or "")
    return {
        "stage": stage,
        "has_content": bool(content.strip()),
        "content_chars": len(content),
        "summary_chars": len(summary),
    }


def _stage_retry_delay(attempt: int) -> float:
    return min(30.0, 2.0 * (2 ** max(0, attempt - 1)))


def _emit(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is not None:
        callback(message)


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
