from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from simple_ar.research.contracts import ExperimentContract


def build_tool_adapter_contract(
    *,
    pack: dict[str, Any],
    contract: ExperimentContract,
    tool_context: dict[str, Any],
) -> dict[str, Any]:
    """Return a read-only adapter contract for future Tool/MCP integrations."""

    return {
        "schema_version": "tool_adapter_contract.v1",
        "generated_at": _utcnow_iso(),
        "mode": "read_only_first",
        "topic": pack.get("topic"),
        "purpose": (
            "Expose research evidence and experiment handoff artifacts to "
            "external tools without allowing repository or run mutation."
        ),
        "inputs": {
            "evidence_pack": "04-synthesize/evidence/evidence_pack.json",
            "experiment_contract": "05-design/evidence/experiment_contract.json",
            "tool_context": "05-design/evidence/tool_context.json",
        },
        "permissions": {
            "read_artifacts": [
                "03-read/review/**",
                "03-read/cards/**",
                "04-synthesize/evidence/**",
                "05-design/evidence/**",
                "02-search/research_index/index_meta.json",
                "02-search/review/coverage_report.*",
            ],
            "write_artifacts": [
                "05-design/tools/tool_trace.jsonl",
            ],
            "network": False,
            "shell": False,
            "workspace_write": False,
        },
        "tool_schema": {
            "request": {
                "tool_name": "string",
                "operation": "summarize | inspect | draft_task",
                "artifact_refs": ["workspace-relative search artifact path"],
                "question": "string",
            },
            "response": {
                "status": "ok | error | skipped",
                "answer": "string",
                "used_artifacts": ["artifact ref"],
                "limitations": ["string"],
            },
        },
        "fallback": {
            "when_unavailable": "Use local evidence_pack and tool_context directly.",
            "when_error": "Record the error in tool_trace.jsonl and continue without stronger tool evidence.",
        },
        "experiment_contract": {
            "contract_id": contract.contract_id,
            "hypothesis": contract.hypothesis,
            "proposed_change": contract.proposed_change,
            "metrics": list(contract.metrics),
        },
        "tool_context_schema": tool_context.get("schema_version"),
        "human_review_required": True,
    }


def tool_adapter_contract_markdown(contract: dict[str, Any]) -> str:
    """Render the tool adapter contract for human review."""

    permissions = _dict(contract.get("permissions"))
    schema = _dict(contract.get("tool_schema"))
    request = _dict(schema.get("request"))
    response = _dict(schema.get("response"))
    lines = [
        "# Tool Adapter Contract",
        "",
        f"Generated: `{contract.get('generated_at', '')}`",
        f"Mode: `{contract.get('mode', '')}`",
        "",
        "## Purpose",
        "",
        str(contract.get("purpose", "")),
        "",
        "## Permissions",
        "",
        f"- Network: `{permissions.get('network', False)}`",
        f"- Shell: `{permissions.get('shell', False)}`",
        f"- Workspace write: `{permissions.get('workspace_write', False)}`",
        "- Read artifacts:",
        *[f"  - `{path}`" for path in _string_list(permissions.get("read_artifacts"))],
        "- Write artifacts:",
        *[f"  - `{path}`" for path in _string_list(permissions.get("write_artifacts"))],
        "",
        "## Request Schema",
        "",
        *[f"- `{key}`: {value}" for key, value in request.items()],
        "",
        "## Response Schema",
        "",
        *[f"- `{key}`: {value}" for key, value in response.items()],
        "",
        "## Fallback",
        "",
        f"- Unavailable: {_dict(contract.get('fallback')).get('when_unavailable', '')}",
        f"- Error: {_dict(contract.get('fallback')).get('when_error', '')}",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def build_tool_trace_rows(contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Return initial trace rows for the read-only tool contract."""

    return [
        {
            "schema_version": "tool_trace.v1",
            "timestamp": _utcnow_iso(),
            "event": "contract_created",
            "status": "ok",
            "contract_schema": contract.get("schema_version"),
            "mode": contract.get("mode"),
        },
        {
            "schema_version": "tool_trace.v1",
            "timestamp": _utcnow_iso(),
            "event": "permission_snapshot",
            "status": "ok",
            "permissions": contract.get("permissions", {}),
        },
    ]


def external_agent_backend_markdown(
    *,
    contract: ExperimentContract,
    tool_context: dict[str, Any],
    adapter_contract: dict[str, Any],
) -> str:
    """Describe how external coding agents should plug into the workflow."""

    lines = [
        "# External Agent Backend",
        "",
        "External agents such as Codex CLI, Claude Code, OpenCode, or future MCP-backed editors are treated as editor/reviewer backends, not as owners of the workflow.",
        "",
        "## Contract",
        "",
        "- Input artifacts:",
        "  - `05-design/evidence/tool_context.json`",
        "  - `05-design/evidence/experiment_contract.json`",
        "  - approved `code_task/task.md` when a code workspace exists",
        "- Output artifacts:",
        "  - reviewable diff or structured edit proposal",
        "  - tool trace rows describing calls, errors, and limitations",
        "- Forbidden by default:",
        "  - direct source-repo mutation outside a code-task workspace",
        "  - hidden shell/network actions not recorded in trace artifacts",
        "  - claims that are not linked back to evidence or benchmark output",
        "",
        "## Experiment Handoff",
        "",
        f"- Contract id: `{contract.contract_id}`",
        f"- Hypothesis: {contract.hypothesis}",
        f"- Proposed change: {contract.proposed_change or 'not specified'}",
        f"- Metrics: {_join_or_none(contract.metrics)}",
        "",
        "## Backend Boundary",
        "",
        "The external backend may help draft code edits or review generated patches. The SimpleAutoResearch orchestrator still owns workspace creation, approval gates, validation, benchmark execution, artifact retention, and final reporting.",
        "",
        "## Current Tool Context",
        "",
        f"- Tool context schema: `{tool_context.get('schema_version', '')}`",
        f"- Adapter contract schema: `{adapter_contract.get('schema_version', '')}`",
        f"- Human review required: `{adapter_contract.get('human_review_required', True)}`",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def build_artifact_retention_policy(
    *,
    compact_artifacts: bool,
    source_plan: dict[str, Any],
) -> dict[str, Any]:
    """Return the search-stage artifact/cache/trace retention policy."""

    return {
        "schema_version": "artifact_retention_policy.v1",
        "generated_at": _utcnow_iso(),
        "compact_artifacts": compact_artifacts,
        "categories": {
            "run_artifacts": {
                "keep": True,
                "paths": [
                    "02-search/planning/research_plan.json",
                    "02-search/papers.jsonl",
                    "02-search/search_meta.json",
                    "02-search/review/coverage_report.md",
                    "03-read/review/reading_table.md",
                    "04-synthesize/evidence/evidence_pack.md",
                    "05-design/evidence/experiment_contract.md",
                ],
                "reason": "Human-facing or downstream stage inputs.",
            },
            "evidence_tables": {
                "keep": True,
                "paths": [
                    "03-read/cards/*.jsonl",
                    "02-search/documents/documents.jsonl",
                    "02-search/documents/sections.jsonl",
                ],
                "reason": "Structured evidence with provenance.",
            },
            "cache_artifacts": {
                "keep": bool(source_plan.get("cache", True)),
                "paths": [
                    "02-search/documents/fulltext_cache/**",
                    "02-search/documents/extracted_text/**",
                ],
                "reason": "Expensive to rebuild when full-text retrieval was enabled.",
            },
            "trace_artifacts": {
                "keep": True,
                "paths": [
                    "02-search/traces/*.jsonl",
                    "05-design/evidence/decision_log.jsonl",
                    "05-design/tools/tool_trace.jsonl",
                ],
                "reason": "Needed for auditability and failure diagnosis.",
            },
            "rebuildable_artifacts": {
                "keep": not compact_artifacts,
                "paths": [
                    "02-search/research_index/chunks.jsonl",
                    "02-search/research_index/index_meta.json",
                ],
                "reason": "Can be rebuilt from documents/cards when compact mode is preferred.",
            },
            "debug_artifacts": {
                "keep": not compact_artifacts,
                "paths": [
                    "02-search/documents/fulltext_extraction.json",
                    "05-design/governance/artifact_retention_policy.json",
                ],
                "reason": "Useful for development, but not required for normal review.",
            },
        },
    }


def artifact_retention_policy_markdown(policy: dict[str, Any]) -> str:
    """Render retention policy into a concise Markdown document."""

    lines = [
        "# Artifact Retention Policy",
        "",
        f"Generated: `{policy.get('generated_at', '')}`",
        f"Compact artifacts: `{policy.get('compact_artifacts', False)}`",
        "",
    ]
    categories = _dict(policy.get("categories"))
    for name, raw in categories.items():
        category = _dict(raw)
        lines.extend(
            [
                f"## {str(name).replace('_', ' ').title()}",
                "",
                f"- Keep by default: `{category.get('keep', False)}`",
                f"- Reason: {category.get('reason', '')}",
                "- Paths:",
            ]
        )
        lines.extend(f"  - `{path}`" for path in _string_list(category.get("paths")))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _join_or_none(values: list[str]) -> str:
    return ", ".join(values) if values else "none"
