from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from simple_ar.integrations.usage import record_usage
from simple_ar.core.artifacts import write_json
from simple_ar.integrations.llm import LLMClient, LLMError
from simple_ar.reviewing.schema import ReviewFinding, normalize_review_findings, review_report


MessageCallback = Callable[[str], None]

CODE_TASK_REVIEW_SYSTEM = (
    "You are a strict but practical senior code reviewer for an isolated code-task workspace. "
    "You cannot edit files. Review scope, runtime correctness, result validity, benchmark integrity, "
    "resource risk, and repair risk. Return only JSON."
)


def run_llm_review(
    *,
    meta_dir: Path,
    prompt: str,
    label: str,
    source: str,
    default_category: str,
    default_evidence: list[str],
    model: str | None = None,
    use_llm: bool = True,
    client: LLMClient | None = None,
    record_injected_usage: bool = False,
    message_callback: MessageCallback | None = None,
    max_findings: int = 16,
    allow_blocking: bool = False,
) -> list[ReviewFinding]:
    """Run the shared LLM reviewer and normalize its findings."""

    if not use_llm:
        return []
    try:
        _emit(message_callback, f"Calling LLM reviewer for {label}.")
        # Generated-project clients already own their task usage observer.
        # Existing-project review receives the unscoped session client.
        llm_client = client if client is not None and not record_injected_usage else LLMClient.for_task(
            client=client,
            model=model,
            usage_callback=lambda usage: record_usage(
                meta_dir,
                usage,
                stage="code_task.review",
                message_callback=message_callback,
            ),
        )
        response = llm_client.ask_json(CODE_TASK_REVIEW_SYSTEM, prompt, label=label)
    except LLMError as exc:
        _emit(message_callback, f"LLM reviewer unavailable; keeping deterministic review only. {exc}")
        return []
    findings = normalize_review_findings(
        response.get("findings"),
        source=source,
        default_category=default_category,
        default_evidence=default_evidence,
        max_findings=max_findings,
    )
    if allow_blocking:
        return findings
    return _downgrade_llm_blockers(findings)


def build_review_artifact(
    *,
    reviewer: str,
    subject: str,
    findings: list[ReviewFinding],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical code-task review artifact.

    The artifact uses ``review_report.v1`` everywhere, while the summary keeps
    ``error_count`` as a compatibility alias for older report/guard consumers.
    """

    report = review_report(
        reviewer=reviewer,
        subject=subject,
        findings=_dedupe_findings(findings),
        metadata=metadata or {},
    )
    return report.model_dump(mode="json")


def review_prompt(
    *,
    instructions: str,
    context: dict[str, Any],
    snippets: list[str],
) -> str:
    """Render a common JSON-review prompt from structured context and snippets."""

    return (
        "Return JSON with `findings`: a list of objects with fields "
        "`severity` (blocking|warning|info), `category`, `summary`, `evidence`, and `recommendation`.\n"
        "Use `blocking` only when the evidence clearly shows execution, validation, or result claims should not proceed.\n"
        "Prefer concrete, bounded findings over broad style feedback.\n\n"
        f"{instructions.strip()}\n\n"
        "Context JSON:\n"
        f"{json.dumps(context, indent=2, ensure_ascii=False)}\n\n"
        "Evidence snippets:\n"
        + ("\n\n".join(snippets) if snippets else "No source snippets available.")
    )




def _downgrade_llm_blockers(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Keep LLM feedback visible without letting snippet-only review hard-block."""

    rows: list[ReviewFinding] = []
    for finding in findings:
        if finding.severity == "blocking":
            rows.append(finding.model_copy(update={"severity": "warning"}))
        else:
            rows.append(finding)
    return rows


def _dedupe_findings(rows: list[ReviewFinding]) -> list[ReviewFinding]:
    found: dict[str, ReviewFinding] = {}
    for row in rows:
        key = row.key or f"{row.severity}:{row.category}:{row.summary}"
        if key not in found:
            found[key] = row.model_copy(update={"key": key})
    return list(found.values())


def _emit(callback: MessageCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)
