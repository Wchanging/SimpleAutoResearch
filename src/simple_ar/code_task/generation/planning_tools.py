from __future__ import annotations

import json
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from simple_ar.code_task.generation.task_contract import contract_prompt_view
from simple_ar.core.artifacts import write_json
from simple_ar.integrations.llm import LLMClient, LLMError


MessageCallback = Callable[[str], None]

PLANNING_STAGES = ("requirements", "architecture", "interfaces", "file_plan")


def build_tool_agent_architecture_plan(
    *,
    contract: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
    domain_profile: Mapping[str, Any],
    client: LLMClient,
    retry_attempts: int = 1,
    review_rounds: int = 2,
    planning_dir: Path | None = None,
    message_callback: MessageCallback | None = None,
) -> dict[str, Any]:
    """Build a greenfield architecture plan through small reviewable LLM tools.

    The planner deliberately keeps each model call narrow: first summarize the
    task contract, then propose architecture, then freeze cross-file interfaces,
    then produce the file plan. A final reviewer can route feedback back to the
    earliest affected stage, and downstream stages are regenerated from that
    point. This keeps long prompts and repeated mistakes from becoming one
    opaque monolithic planning failure.
    """

    retry_attempts = max(1, int(retry_attempts or 1))
    review_rounds = max(0, int(review_rounds or 0))
    if planning_dir is not None:
        planning_dir.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {}
    feedback: dict[str, list[str]] = {stage: [] for stage in PLANNING_STAGES}
    rerun_from = "requirements"
    review: dict[str, Any] = {}
    revision_count = 0

    for round_index in range(review_rounds + 1):
        for stage in _stages_from(rerun_from):
            state[stage] = _run_stage(
                stage,
                contract=contract,
                result_schema=result_schema,
                resource_plan=resource_plan,
                domain_profile=domain_profile,
                state=state,
                feedback=feedback.get(stage, []),
                client=client,
                retry_attempts=retry_attempts,
                attempt_index=round_index + 1,
                planning_dir=planning_dir,
                message_callback=message_callback,
            )
        review = _run_review(
            contract=contract,
            result_schema=result_schema,
            resource_plan=resource_plan,
            domain_profile=domain_profile,
            state=state,
            client=client,
            retry_attempts=retry_attempts,
            attempt_index=round_index + 1,
            planning_dir=planning_dir,
            message_callback=message_callback,
        )
        if _review_passed(review):
            break
        if round_index >= review_rounds:
            _emit(
                message_callback,
                "Greenfield planning review still has findings after bounded revisions; continuing with recorded risks.",
            )
            break
        rerun_from = _review_target_stage(review)
        revision_count += 1
        _merge_review_feedback(feedback, review)
        _emit(
            message_callback,
            f"Planning review requested revision from `{rerun_from}`; regenerating downstream planning artifacts.",
        )

    plan = _assemble_architecture_plan(
        contract=contract,
        result_schema=result_schema,
        resource_plan=resource_plan,
        state=state,
        review=review,
        revision_count=revision_count,
    )
    if planning_dir is not None:
        write_json(planning_dir / "state.json", state)
        write_json(planning_dir / "final_plan.json", plan)
    return plan


def _run_stage(
    stage: str,
    *,
    contract: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
    domain_profile: Mapping[str, Any],
    state: Mapping[str, Any],
    feedback: list[str],
    client: LLMClient,
    retry_attempts: int,
    attempt_index: int,
    planning_dir: Path | None,
    message_callback: MessageCallback | None,
) -> dict[str, Any]:
    label = f"greenfield-plan-{stage}" if attempt_index == 1 else f"greenfield-plan-{stage}-r{attempt_index}"
    _emit(message_callback, f"Planning tool `{stage}`.")
    raw = _ask_stage_json(
        client,
        system=_stage_system(stage),
        prompt=_stage_prompt(
            stage,
            contract=contract,
            result_schema=result_schema,
            resource_plan=resource_plan,
            domain_profile=domain_profile,
            state=state,
            feedback=feedback,
        ),
        label=label,
        max_output_tokens=_stage_output_tokens(stage, resource_plan),
        retry_attempts=retry_attempts,
        message_callback=message_callback,
    )
    normalized = _normalize_stage_result(stage, raw)
    _write_stage_artifact(planning_dir, stage, attempt_index, normalized)
    return normalized


def _run_review(
    *,
    contract: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
    domain_profile: Mapping[str, Any],
    state: Mapping[str, Any],
    client: LLMClient,
    retry_attempts: int,
    attempt_index: int,
    planning_dir: Path | None,
    message_callback: MessageCallback | None,
) -> dict[str, Any]:
    _emit(message_callback, "Reviewing greenfield planning artifacts.")
    raw = _ask_stage_json(
        client,
        system=PLANNING_REVIEW_SYSTEM,
        prompt=_review_prompt(
            contract=contract,
            result_schema=result_schema,
            resource_plan=resource_plan,
            domain_profile=domain_profile,
            state=state,
        ),
        label="greenfield-plan-review" if attempt_index == 1 else f"greenfield-plan-review-r{attempt_index}",
        max_output_tokens=1200,
        retry_attempts=retry_attempts,
        message_callback=message_callback,
    )
    review = _normalize_review(raw)
    _write_stage_artifact(planning_dir, "review", attempt_index, review)
    return review


def _ask_stage_json(
    client: LLMClient,
    *,
    system: str,
    prompt: str,
    label: str,
    max_output_tokens: int,
    retry_attempts: int,
    message_callback: MessageCallback | None,
) -> dict[str, Any]:
    last_error: LLMError | None = None
    for attempt in range(1, retry_attempts + 1):
        try:
            result = client.ask_json(
                system,
                prompt + _retry_suffix(last_error, attempt),
                label=label if attempt == 1 else f"{label}-retry-{attempt}",
                max_output_tokens=max_output_tokens,
            )
            return result if isinstance(result, dict) else {}
        except LLMError as exc:
            last_error = exc
            if attempt >= retry_attempts:
                _emit(
                    message_callback,
                    f"Planning tool `{label}` failed after {attempt}/{retry_attempts} attempt(s). {exc}",
                )
                raise
            delay = _stage_retry_delay(attempt)
            _emit(
                message_callback,
                f"Planning tool `{label}` failed ({attempt}/{retry_attempts}); retrying in {delay:.1f}s. {exc}",
            )
            time.sleep(delay)
    raise LLMError(f"Planning tool `{label}` failed without a captured error.")


def _stage_prompt(
    stage: str,
    *,
    contract: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
    domain_profile: Mapping[str, Any],
    state: Mapping[str, Any],
    feedback: list[str],
) -> str:
    base = _base_context(
        contract=contract,
        result_schema=result_schema,
        resource_plan=resource_plan,
        domain_profile=domain_profile,
    )
    feedback_block = _feedback_block(feedback)
    if stage == "requirements":
        return (
            "Tool: requirements_brief.\n"
            "Extract the task into implementation-ready requirements. Return one strict JSON object with fields:\n"
            "- objective: string\n"
            "- hard_requirements: array of concrete requirements that implementation must satisfy\n"
            "- deliverables: array of expected files/artifacts/outputs\n"
            "- constraints: array of runtime/resource/dependency constraints\n"
            "- data_requirements: array of required inputs/datasets/source handling rules\n"
            "- evaluation_targets: array of metrics/checks/output contracts\n"
            "- dependency_strategy: array of packages that may help and fallback expectations\n"
            "- open_questions: array of ambiguities that implementation should handle conservatively\n\n"
            "Rules:\n"
            "- Do not invent benchmark-specific fields. Preserve fields explicitly requested by the task/result schema.\n"
            "- Convert prose requirements into testable implementation obligations.\n"
            "- Mention optional dependencies only if they are task-relevant and installed/available in the context.\n\n"
            f"{base}{feedback_block}"
        )
    if stage == "architecture":
        return (
            "Tool: architecture_outline.\n"
            "Design the project architecture from the requirements brief. Return one strict JSON object with fields:\n"
            "- architecture_summary: string\n"
            "- modules: array of {name, responsibility, inputs, outputs, dependencies}\n"
            "- data_flow: array of ordered flow steps\n"
            "- test_strategy: array of validation/smoke/benchmark checks\n"
            "- risks: array of realistic implementation risks and mitigations\n\n"
            "Rules:\n"
            "- Keep the design bounded by resource_plan.max_files and max_generated_lines.\n"
            "- Prefer one authoritative orchestrator plus small modules with clear ownership.\n"
            "- Make cross-module dependencies explicit; downstream file planning will use this directly.\n"
            "- Avoid filler modules and avoid hardcoding one benchmark's hidden conventions.\n\n"
            f"{base}\nRequirements brief JSON:\n{_json_block(state.get('requirements'))}\n{feedback_block}"
        )
    if stage == "interfaces":
        return (
            "Tool: interface_contract.\n"
            "Define cross-file interfaces before files are written. Return one strict JSON object with fields:\n"
            "- shared_schemas: array of {name, fields, producer, consumers, notes}\n"
            "- module_apis: array of {module, public_api, consumes, produces}\n"
            "- cross_file_contracts: array of exact call/data-flow contracts\n"
            "- stdout_contract: array of parseable stdout lines/metric formats if relevant\n\n"
            "Rules:\n"
            "- public_api entries must be exact function/class names or concise signatures.\n"
            "- Every planned consumer must use an API declared by the producer.\n"
            "- Include artifact/metric schemas that later files can share instead of guessing independently.\n\n"
            f"{base}\nRequirements brief JSON:\n{_json_block(state.get('requirements'))}\n"
            f"Architecture outline JSON:\n{_json_block(state.get('architecture'))}\n{feedback_block}"
        )
    if stage == "file_plan":
        return (
            "Tool: file_plan.\n"
            "Produce the final file plan. Return one strict JSON object with fields:\n"
            "- objective: string\n"
            "- files: array of {path, purpose, dependencies, public_api, acceptance_criteria, entrypoint}\n\n"
            "Rules:\n"
            "- Include main.py as the command-line entrypoint.\n"
            "- Keep paths relative, POSIX-style, and inside the generated project.\n"
            "- Keep file count within resource_plan.max_files.\n"
            "- Each file's dependencies must refer only to paths in this file plan.\n"
            "- Each dependency must be justified by a declared public_api or shared schema.\n"
            "- Acceptance criteria should be checkable and tied to task/result schema requirements.\n"
            "- Do not add task-irrelevant boilerplate just to increase size.\n\n"
            f"{base}\nRequirements brief JSON:\n{_json_block(state.get('requirements'))}\n"
            f"Architecture outline JSON:\n{_json_block(state.get('architecture'))}\n"
            f"Interface contract JSON:\n{_json_block(state.get('interfaces'))}\n{feedback_block}"
        )
    raise ValueError(f"Unsupported planning stage: {stage}")


def _review_prompt(
    *,
    contract: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
    domain_profile: Mapping[str, Any],
    state: Mapping[str, Any],
) -> str:
    return (
        "Review this greenfield planning package before code generation. Return one strict JSON object with fields:\n"
        "- status: pass or needs_revision\n"
        "- summary: short summary\n"
        "- findings: array of {target_stage, severity, issue, required_change}\n\n"
        "Review criteria:\n"
        "- The plan must satisfy explicit task/result-schema requirements without benchmark-specific hidden assumptions.\n"
        "- File dependencies and public APIs must be coherent enough that per-file code generation will not guess names.\n"
        "- Required outputs, metrics, artifacts, and resource constraints must be carried into file acceptance criteria.\n"
        "- If a finding affects an upstream concept, set target_stage to the earliest stage that should be regenerated.\n"
        "- Use severity critical only when code generation should not proceed without revision.\n\n"
        f"{_base_context(contract=contract, result_schema=result_schema, resource_plan=resource_plan, domain_profile=domain_profile)}\n"
        f"Planning state JSON:\n{_json_block(state)}\n"
    )


def _assemble_architecture_plan(
    *,
    contract: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
    state: Mapping[str, Any],
    review: Mapping[str, Any],
    revision_count: int,
) -> dict[str, Any]:
    requirements = state.get("requirements") if isinstance(state.get("requirements"), Mapping) else {}
    architecture = state.get("architecture") if isinstance(state.get("architecture"), Mapping) else {}
    interfaces = state.get("interfaces") if isinstance(state.get("interfaces"), Mapping) else {}
    file_plan = state.get("file_plan") if isinstance(state.get("file_plan"), Mapping) else {}
    files = _normalize_files(file_plan.get("files"), max_files=_positive_int(resource_plan.get("max_files"), 8))
    risks = _list(architecture.get("risks"))[:8]
    findings = review.get("findings") if isinstance(review.get("findings"), list) else []
    if findings:
        risks.extend(_finding_to_risk(row) for row in findings if isinstance(row, Mapping))
    return {
        "schema_version": "greenfield_architecture.v1",
        "mode": "greenfield_project",
        "objective": _text(file_plan.get("objective")) or _text(requirements.get("objective")) or _text(contract.get("objective")),
        "architecture_summary": _text(architecture.get("architecture_summary"))
        or "Bounded generated project with explicit interfaces and a command-line entrypoint.",
        "data_flow": _list(architecture.get("data_flow"))[:8],
        "interfaces": _render_interfaces(interfaces)[:10],
        "test_strategy": _list(architecture.get("test_strategy"))[:8],
        "risks": [item for item in risks if item][:10],
        "files": files,
        "planning": {
            "mode": "tool_agent",
            "review_status": _text(review.get("status")) or "unknown",
            "revision_count": revision_count,
            "review_summary": _text(review.get("summary")),
        },
        "result_schema": dict(result_schema),
    }


def _normalize_stage_result(stage: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if stage == "requirements":
        return {
            "objective": _text(value.get("objective")),
            "hard_requirements": _list(value.get("hard_requirements"))[:50],
            "deliverables": _list(value.get("deliverables"))[:30],
            "constraints": _list(value.get("constraints"))[:30],
            "data_requirements": _list(value.get("data_requirements"))[:30],
            "evaluation_targets": _list(value.get("evaluation_targets"))[:30],
            "dependency_strategy": _list(value.get("dependency_strategy"))[:20],
            "open_questions": _list(value.get("open_questions"))[:20],
        }
    if stage == "architecture":
        modules = value.get("modules")
        return {
            "architecture_summary": _text(value.get("architecture_summary")),
            "modules": _normalize_modules(modules),
            "data_flow": _list(value.get("data_flow"))[:20],
            "test_strategy": _list(value.get("test_strategy"))[:20],
            "risks": _list(value.get("risks"))[:20],
        }
    if stage == "interfaces":
        return {
            "shared_schemas": _normalize_named_rows(value.get("shared_schemas"), max_rows=24),
            "module_apis": _normalize_named_rows(value.get("module_apis"), max_rows=32),
            "cross_file_contracts": _list(value.get("cross_file_contracts"))[:40],
            "stdout_contract": _list(value.get("stdout_contract"))[:20],
        }
    if stage == "file_plan":
        return {
            "objective": _text(value.get("objective")),
            "files": _normalize_files(value.get("files"), max_files=64),
        }
    return dict(value)


def _normalize_review(value: Mapping[str, Any]) -> dict[str, Any]:
    status = _text(value.get("status")).lower().replace("-", "_")
    if status not in {"pass", "needs_revision"}:
        status = "needs_revision"
    findings: list[dict[str, str]] = []
    raw_findings = value.get("findings")
    rows = raw_findings if isinstance(raw_findings, list) else []
    for row in rows[:20]:
        if not isinstance(row, Mapping):
            continue
        target = _normalize_target_stage(row.get("target_stage"))
        findings.append(
            {
                "target_stage": target,
                "severity": _normalize_severity(row.get("severity")),
                "issue": _text(row.get("issue"))[:800],
                "required_change": _text(row.get("required_change"))[:800],
            }
        )
    if status == "pass" and findings:
        status = "needs_revision"
    return {
        "status": status,
        "summary": _text(value.get("summary"))[:1200],
        "findings": findings,
    }


def _normalize_modules(value: object) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    result: list[dict[str, Any]] = []
    for row in rows[:32]:
        if not isinstance(row, Mapping):
            continue
        result.append(
            {
                "name": _text(row.get("name"))[:120],
                "responsibility": _text(row.get("responsibility"))[:500],
                "inputs": _list(row.get("inputs"))[:12],
                "outputs": _list(row.get("outputs"))[:12],
                "dependencies": _list(row.get("dependencies"))[:12],
            }
        )
    return result


def _normalize_named_rows(value: object, *, max_rows: int) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    result: list[dict[str, Any]] = []
    for row in rows[:max_rows]:
        if isinstance(row, Mapping):
            result.append({str(key): _coerce_jsonable(val) for key, val in row.items() if str(key).strip()})
        elif _text(row):
            result.append({"description": _text(row)})
    return result


def _normalize_files(value: object, *, max_files: int) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    files: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        path = _safe_path(_text(row.get("path")))
        if not path:
            continue
        files.append(
            {
                "path": path,
                "purpose": _text(row.get("purpose"))[:500] or "Generated project file.",
                "dependencies": _list(row.get("dependencies"))[:16],
                "public_api": _list(row.get("public_api"))[:40],
                "acceptance_criteria": _list(row.get("acceptance_criteria"))[:16],
                "entrypoint": bool(row.get("entrypoint")) or path == "main.py",
            }
        )
    if not any(row["path"] == "main.py" for row in files):
        files.insert(
            0,
            {
                "path": "main.py",
                "purpose": "Command-line entrypoint that calls the generated project orchestrator.",
                "dependencies": [],
                "public_api": ["main(argv=None)"],
                "acceptance_criteria": ["Runs with `python main.py` and prints parseable metrics."],
                "entrypoint": True,
            },
        )
    return _prune_dependencies(files[: max(1, max_files)])


def _base_context(
    *,
    contract: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    resource_plan: Mapping[str, Any],
    domain_profile: Mapping[str, Any],
) -> str:
    return (
        f"Experiment contract JSON:\n{_json_block(_contract_view(contract))}\n\n"
        f"Result schema JSON:\n{_json_block(dict(result_schema))}\n\n"
        f"Resource plan JSON:\n{_json_block(dict(resource_plan))}\n\n"
        f"Domain profile JSON:\n{_json_block(_domain_profile_view(domain_profile))}\n"
    )


def _contract_view(contract: Mapping[str, Any]) -> dict[str, Any]:
    return contract_prompt_view(
        contract,
        max_task_chars=2200,
        max_requirements=48,
        max_success_criteria=28,
    )


def _domain_profile_view(profile: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(profile)
    packages = data.get("available_task_relevant_packages")
    if isinstance(packages, list):
        data["available_task_relevant_packages"] = packages[:80]
    excerpt = data.get("task_excerpt")
    if isinstance(excerpt, str) and len(excerpt) > 1800:
        data["task_excerpt"] = excerpt[:1800].rstrip()
    return data


def _render_interfaces(value: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for row in value.get("shared_schemas", []) if isinstance(value.get("shared_schemas"), list) else []:
        if isinstance(row, Mapping):
            name = _text(row.get("name") or row.get("description"))
            fields = row.get("fields")
            field_text = ", ".join(str(item) for item in fields[:10]) if isinstance(fields, list) else _text(fields)
            lines.append(f"Schema {name}: {field_text}".strip())
    for row in value.get("module_apis", []) if isinstance(value.get("module_apis"), list) else []:
        if isinstance(row, Mapping):
            module = _text(row.get("module") or row.get("name"))
            api = row.get("public_api")
            api_text = ", ".join(str(item) for item in api[:12]) if isinstance(api, list) else _text(api)
            lines.append(f"{module}: {api_text}".strip())
    lines.extend(_list(value.get("cross_file_contracts"))[:20])
    lines.extend(_list(value.get("stdout_contract"))[:10])
    return [line for line in lines if line]


def _merge_review_feedback(feedback: dict[str, list[str]], review: Mapping[str, Any]) -> None:
    findings = review.get("findings")
    if not isinstance(findings, list):
        return
    for row in findings:
        if not isinstance(row, Mapping):
            continue
        target = _normalize_target_stage(row.get("target_stage"))
        message = _text(row.get("required_change")) or _text(row.get("issue"))
        if message:
            feedback.setdefault(target, []).append(message)


def _review_target_stage(review: Mapping[str, Any]) -> str:
    findings = review.get("findings")
    rows = findings if isinstance(findings, list) else []
    ranks = {stage: index for index, stage in enumerate(PLANNING_STAGES)}
    best = "file_plan"
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        target = _normalize_target_stage(row.get("target_stage"))
        if ranks[target] < ranks[best]:
            best = target
    return best


def _review_passed(review: Mapping[str, Any]) -> bool:
    return _text(review.get("status")).lower() == "pass"


def _finding_to_risk(row: Mapping[str, Any]) -> str:
    issue = _text(row.get("issue"))
    change = _text(row.get("required_change"))
    severity = _text(row.get("severity"))
    parts = [f"Planning review {severity} finding".strip()]
    if issue:
        parts.append(issue)
    if change:
        parts.append("Mitigation: " + change)
    return ". ".join(parts)


def _feedback_block(feedback: list[str]) -> str:
    if not feedback:
        return ""
    return "\nRevision feedback to address:\n" + "\n".join(f"- {item}" for item in feedback[-8:]) + "\n"


def _stages_from(stage: str) -> tuple[str, ...]:
    target = _normalize_target_stage(stage)
    index = PLANNING_STAGES.index(target)
    return PLANNING_STAGES[index:]


def _normalize_target_stage(value: object) -> str:
    text = _text(value).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "requirement": "requirements",
        "requirements_brief": "requirements",
        "architecture_outline": "architecture",
        "interface": "interfaces",
        "interface_contract": "interfaces",
        "file": "file_plan",
        "files": "file_plan",
        "fileplan": "file_plan",
    }
    target = aliases.get(text, text)
    return target if target in PLANNING_STAGES else "file_plan"


def _normalize_severity(value: object) -> str:
    text = _text(value).lower()
    return text if text in {"low", "medium", "high", "critical"} else "medium"


def _stage_system(stage: str) -> str:
    names = {
        "requirements": "You are a careful product and experiment requirements analyst. Extract only implementation-relevant obligations.",
        "architecture": "You are a pragmatic Python software architect. Design cohesive, bounded projects with clear module ownership.",
        "interfaces": "You are an interface-contract reviewer. Make cross-file calls, schemas, and stdout/metric contracts explicit.",
        "file_plan": "You are a code-generation file planner. Convert architecture and interfaces into a concrete, bounded file plan.",
    }
    return names[stage] + " Return strict JSON only."


PLANNING_REVIEW_SYSTEM = (
    "You are a strict planning reviewer for generated Python projects. "
    "Route each issue to the earliest planning stage that should be regenerated. Return strict JSON only."
)


def _stage_output_tokens(stage: str, resource_plan: Mapping[str, Any]) -> int:
    max_files = _positive_int(resource_plan.get("max_files"), 8)
    if stage == "requirements":
        return 1100
    if stage == "architecture":
        return 1500 if max_files >= 12 else 1200
    if stage == "interfaces":
        return 1800 if max_files >= 12 else 1400
    if max_files >= 24:
        return 2600
    if max_files >= 12:
        return 2200
    return 1600


def _write_stage_artifact(planning_dir: Path | None, stage: str, attempt_index: int, value: Mapping[str, Any]) -> None:
    if planning_dir is None:
        return
    stage_dir = planning_dir / stage
    stage_dir.mkdir(parents=True, exist_ok=True)
    write_json(stage_dir / f"attempt-{attempt_index:03d}.json", dict(value))
    write_json(stage_dir / "latest.json", dict(value))


def _retry_suffix(error: LLMError | None, attempt: int) -> str:
    if error is None:
        return ""
    return (
        "\nPrevious attempt failed before attempt "
        f"{attempt}: {error}\n"
        "Return exactly one JSON object. Do not include Markdown fences, commentary, or trailing analysis.\n"
    )


def _stage_retry_delay(attempt: int) -> float:
    return min(30.0, 2.0 * (2 ** max(0, attempt - 1)))


def _prune_dependencies(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    known = {row["path"] for row in files}
    for row in files:
        row["dependencies"] = [dep for dep in row.get("dependencies", []) if dep in known]
    return files


def _safe_path(value: str) -> str:
    value = value.replace("\\", "/").strip().lstrip("/")
    if not value or value.startswith("../") or "/../" in value or value == "..":
        return ""
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    return path.as_posix()


def _json_block(value: object) -> str:
    return json.dumps(value if value is not None else {}, indent=2, ensure_ascii=False)


def _coerce_jsonable(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_coerce_jsonable(item) for item in value[:24]]
    if isinstance(value, Mapping):
        return {str(key): _coerce_jsonable(val) for key, val in list(value.items())[:24]}
    return str(value)


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _emit(callback: MessageCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)
