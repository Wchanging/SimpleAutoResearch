from __future__ import annotations

from typing import Any, Mapping


def contract_prompt_context(contract: Mapping[str, Any], *, mode: str) -> dict[str, Any]:
    """Return the explicit contract view exposed to a model prompt.

    The durable task contract is never changed by this function.  ``minimal``
    preserves the historic end-to-end Plan-then-Code ablation.  ``plan_only``
    is intentionally narrower: it is valid only after an accepted architecture
    plan has been fixed and is supplied separately to the downstream writer,
    reviewer, and repair agents.
    """

    normalized = str(mode or "full").strip().lower().replace("-", "_")
    if normalized == "full":
        return dict(contract)
    if normalized == "minimal":
        return {
            "schema_version": str(contract.get("schema_version") or "code_task_contract.v1"),
            "context_mode": "minimal",
            "objective": str(contract.get("objective") or "")[:1200],
            "task_text": str(contract.get("task_text") or contract.get("task") or "")[:6000],
            "benchmark_command": str(contract.get("benchmark_command") or ""),
            "success_criteria": _string_list(contract.get("success_criteria"), limit=8),
            "constraints": _string_list(contract.get("constraints"), limit=8),
            "note": (
                "Ablation prompt view: full implementation/artifact/metric/analysis "
                "contracts are intentionally omitted from model context."
            ),
        }
    if normalized == "plan_only":
        return {
            "schema_version": str(contract.get("schema_version") or "code_task_contract.v1"),
            "context_mode": "plan_only",
            "note": (
                "Downstream ablation view: use the supplied accepted architecture and local "
                "execution evidence. The canonical task contract is intentionally omitted "
                "from this model prompt."
            ),
        }
    raise ValueError(f"Unsupported contract prompt context: {mode!r}")


def _string_list(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text[:500])
    return result[:limit]
