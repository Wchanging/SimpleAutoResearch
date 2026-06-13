from __future__ import annotations

import json
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from simple_ar.core.artifacts import write_text
from simple_ar.integrations.llm import LLMClient, LLMError
from simple_ar.experiment.coding.memory import record_generated_file, record_generation_batch
from simple_ar.experiment.coding.scaffold import fallback_file_content


def write_generated_project(
    *,
    project_dir: Path,
    architecture_plan: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    contract: Mapping[str, Any],
    memory: dict[str, Any],
    client: LLMClient | None = None,
    max_generated_lines: int = 1200,
    files_per_batch: int = 4,
) -> dict[str, Any]:
    """Write a bounded generated project from a file plan."""

    if project_dir.exists():
        shutil.rmtree(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    files = [row for row in architecture_plan.get("files", []) if isinstance(row, Mapping)]
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
            client=client,
        )
        line_count = max(1, len(content.splitlines()))
        if total_lines + line_count > max_generated_lines and generated:
            break
        target = project_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text(target, content)
        total_lines += line_count
        generated.append(
            {
                "path": rel_path,
                "mode": mode,
                "line_count": line_count,
                "summary": summary,
            }
        )
        record_generated_file(memory, path=rel_path, summary=summary, mode=mode)
        batch_files.append(rel_path)
        if len(batch_files) >= max(1, files_per_batch):
            record_generation_batch(memory, batch_id=batch_id, files=batch_files, mode=mode)
            batch_files = []
            batch_id = f"batch-{index + 1:03d}"
    if batch_files:
        record_generation_batch(memory, batch_id=batch_id, files=batch_files, mode="mixed")
    return {
        "schema_version": "greenfield_code_artifacts.v1",
        "project_dir": str(project_dir),
        "generated_files": generated,
        "total_lines": total_lines,
        "entrypoint": "main.py",
    }


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
    client: LLMClient | None,
) -> tuple[str, str, str]:
    path = _safe_path(str(file_spec.get("path", "")))
    if client is not None and path.endswith(".py"):
        try:
            response = client.ask_json(
                GREENFIELD_FILE_SYSTEM,
                greenfield_file_prompt(
                    file_spec=file_spec,
                    architecture_plan=architecture_plan,
                    result_schema=result_schema,
                    contract=contract,
                ),
                label=f"greenfield-file-{path}",
            )
            content = str(response.get("content", "")).strip()
            summary = str(response.get("summary", "")).strip() or str(file_spec.get("purpose", ""))
            if content and not _looks_like_markdown_fence(content):
                return content.rstrip() + "\n", "llm", summary[:500]
        except LLMError:
            pass
    return fallback_file_content(path, result_schema, contract), "fallback", str(file_spec.get("purpose", ""))[:500]


def greenfield_file_prompt(
    *,
    file_spec: Mapping[str, Any],
    architecture_plan: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> str:
    return (
        "Generate exactly one file for this bounded Python experiment project. "
        "Return JSON with string fields `content` and `summary`.\n\n"
        "Rules:\n"
        "- Use only Python standard library unless the contract explicitly implies a declared dependency.\n"
        "- Do not access network, shell, credentials, user home directories, or external datasets.\n"
        "- The project entrypoint must print each required metric as `metric_name: number`.\n"
        "- Keep this single file complete and concise; target under 120 lines.\n"
        "- Prefer simple deterministic logic over broad simulations, logs, or test frameworks.\n"
        "- Do not leave placeholders, unfinished functions, unterminated literals, or truncated JSON/Python.\n\n"
        f"File spec:\n{json.dumps(dict(file_spec), indent=2, ensure_ascii=False)}\n\n"
        f"Architecture plan:\n{json.dumps(dict(architecture_plan), indent=2, ensure_ascii=False)}\n\n"
        f"Result schema:\n{json.dumps(dict(result_schema), indent=2, ensure_ascii=False)}\n\n"
        f"Experiment contract:\n{json.dumps(dict(contract), indent=2, ensure_ascii=False)}\n"
    )


GREENFIELD_FILE_SYSTEM = (
    "You are a cautious code implementer for small reproducible experiments. "
    "Write runnable, bounded Python files that satisfy the provided metric schema."
)


def _safe_path(value: str) -> str:
    value = value.replace("\\", "/").strip().lstrip("/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    return path.as_posix()


def _looks_like_markdown_fence(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("```") or stripped.endswith("```")
