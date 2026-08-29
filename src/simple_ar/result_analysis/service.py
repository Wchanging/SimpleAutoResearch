from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Protocol

from simple_ar.integrations.llm import parse_json_object

from .metrics import build_metric_summary
from .schema import (
    AnalysisAudit,
    AnalysisClaim,
    AnalysisContext,
    AnalysisResult,
    AnalysisStatus,
)


class JsonLLMClient(Protocol):
    def ask(self, system: str, user: str, *, label: str = "") -> str:
        ...

    def ask_json(self, system: str, user: str, *, label: str = "") -> dict[str, Any]:
        ...


SYSTEM_PROMPT = """You are a rigorous benchmark and experiment result analyst.
Use only the provided task, criteria, metrics, artifacts, and writeup.
Do not invent metrics, datasets, judge outcomes, or unsupported claims.
Every supported or partially_supported claim must cite metric_refs or concrete evidence.
If evidence is missing, mark the claim as not_evaluated or unsupported.
Prefer concise, reviewable evidence over promotional language."""


def run_result_analysis(
    context: AnalysisContext | dict[str, Any],
    *,
    output_dir: Path | None = None,
    client: JsonLLMClient | None = None,
    use_llm: bool = False,
    label: str = "result-analysis",
) -> AnalysisResult:
    ctx = context if isinstance(context, AnalysisContext) else AnalysisContext.model_validate(context)
    metric_summary = build_metric_summary(ctx)
    metric_summary["result_tables"] = build_result_tables(ctx)
    metric_summary["rubric_categories"] = build_rubric_categories(ctx)
    metric_summary["weak_metric_signals"] = sorted(
        dict.fromkeys(
            list(metric_summary.get("weak_metric_signals") or [])
            + evidence_chain_warnings(ctx, metric_summary["result_tables"])
        )
    )
    result = deterministic_result(ctx, metric_summary)
    raw_response: dict[str, Any] | None = None

    if use_llm:
        if client is None:
            result.audit.notes.append("LLM analysis requested but no client was provided; used deterministic fallback.")
        else:
            raw_response = request_json_with_diagnostics(
                client,
                SYSTEM_PROMPT,
                build_prompt(ctx, metric_summary, result),
                label=label,
                output_dir=output_dir,
            )
            result = normalize_llm_result(raw_response, ctx, metric_summary, fallback=result)

    result.raw_llm_response = raw_response
    result.audit = audit_result(result, metric_summary)
    result.status, result.status_reasons = derive_analysis_status(ctx, metric_summary)
    if output_dir is not None:
        write_analysis_artifacts(output_dir, ctx, result)
    return result


def request_json_with_diagnostics(
    client: JsonLLMClient,
    system: str,
    user: str,
    *,
    label: str,
    output_dir: Path | None,
) -> dict[str, Any]:
    raw = client.ask(system, user + "\n\nReturn valid JSON only. Do not include markdown or extra text.", label=label)
    parsed = parse_json_object(raw)
    if parsed is None:
        hint = ""
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            write_text(output_dir / "analysis_prompt.txt", user)
            write_text(output_dir / "analysis_raw_response.txt", raw)
            hint = f" Raw response saved to {output_dir / 'analysis_raw_response.txt'}."
        raise ValueError("LLM result-analysis response did not contain a valid JSON object." + hint)
    return parsed


def deterministic_result(context: AnalysisContext, metric_summary: dict[str, Any]) -> AnalysisResult:
    claims = existing_claims(context)
    if not claims:
        claims = hypothesis_placeholder_claims(context, metric_summary)
    rubric_coverage = deterministic_rubric_coverage(context, metric_summary)
    audit = AnalysisAudit(
        llm_used=False,
        missing_required_metrics=list(metric_summary.get("missing_required_metrics") or []),
        weak_metric_signals=list(metric_summary.get("weak_metric_signals") or []),
        limitations=deterministic_limitations(metric_summary),
        notes=["deterministic result-analysis fallback"],
    )
    readme = deterministic_markdown(context, metric_summary, claims, rubric_coverage, audit)
    return AnalysisResult(
        readme_markdown=readme,
        claims=claims,
        claims_payload=claims_payload(context, metric_summary, claims, rubric_coverage),
        metric_summary=metric_summary,
        rubric_coverage=rubric_coverage,
        audit=audit,
    )


def existing_claims(context: AnalysisContext) -> list[AnalysisClaim]:
    source = context.project_results
    rows: Any = None
    for key in ("claims", "hypothesis_verdicts", "hypotheses"):
        value = source.get(key) if isinstance(source, dict) else None
        if isinstance(value, list) and value:
            rows = value
            break
        if isinstance(value, dict) and value:
            rows = [
                {"claim_id": claim_id, "hypothesis_id": claim_id, **row}
                if isinstance(row, dict)
                else {"claim_id": claim_id, "hypothesis_id": claim_id, "claim": str(row)}
                for claim_id, row in value.items()
            ]
            break
    if not isinstance(rows, list):
        return contract_claims(context)
    statements = hypothesis_statement_map(context.hypotheses)
    claims: list[AnalysisClaim] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        claim_id = str(row.get("claim_id") or row.get("hypothesis_id") or row.get("hypothesis") or f"claim-{index}")
        claim_text = str(
            row.get("claim")
            or row.get("statement")
            or row.get("summary")
            or statements.get(claim_id)
            or row.get("evidence")
            or ""
        ).strip()
        if not claim_text:
            continue
        verdict = row_verdict(row)
        claims.append(
            AnalysisClaim(
                claim_id=claim_id,
                claim=claim_text,
                verdict=verdict,
                evidence=normalize_evidence(row.get("evidence")),
                metric_refs=normalize_metric_refs(row.get("metric_refs")),
                limitations=normalize_string_list(row.get("limitations")),
                confidence=normalize_confidence(row.get("confidence")),
            )
        )
    return claims


def contract_claims(context: AnalysisContext) -> list[AnalysisClaim]:
    contract = context.task_contract if isinstance(context.task_contract, dict) else {}
    rows = contract.get("claim_specs")
    claims: list[AnalysisClaim] = []
    if isinstance(rows, list):
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            statement = str(row.get("statement") or row.get("claim") or row.get("description") or "").strip()
            if not statement:
                continue
            claims.append(
                AnalysisClaim(
                    claim_id=str(row.get("claim_id") or row.get("id") or f"claim-{index}"),
                    claim=statement,
                    verdict="not_evaluated",
                    evidence=[],
                    metric_refs=normalize_metric_refs(row.get("metric_refs") or row.get("required_metrics")),
                    limitations=["Claim was derived from task_contract and requires measured evidence."],
                    confidence="low",
                )
            )
    if claims:
        return claims
    evidence_plan = contract.get("evidence_plan") if isinstance(contract.get("evidence_plan"), dict) else {}
    hypotheses = evidence_plan.get("hypotheses") if isinstance(evidence_plan, dict) else []
    for index, item in enumerate(hypotheses if isinstance(hypotheses, list) else [], start=1):
        statement = str(item).strip()
        if not statement:
            continue
        claims.append(
            AnalysisClaim(
                claim_id=f"contract-hypothesis-{index}",
                claim=statement,
                verdict="not_evaluated",
                evidence=[],
                metric_refs=[],
                limitations=["Hypothesis was derived from task_contract.evidence_plan and requires measured evidence."],
                confidence="low",
            )
        )
    return claims


def hypothesis_statement_map(hypotheses: list[dict[str, Any]]) -> dict[str, str]:
    statements: dict[str, str] = {}
    for index, row in enumerate(hypotheses, start=1):
        if not isinstance(row, dict):
            continue
        key = str(row.get("id") or row.get("hypothesis_id") or f"hypothesis-{index}")
        text = str(row.get("statement") or row.get("claim") or "").strip()
        if key and text:
            statements[key] = text
    return statements


def row_verdict(row: dict[str, Any]) -> str:
    if isinstance(row.get("supported"), bool):
        return "supported" if row["supported"] else "unsupported"
    return normalize_verdict(row.get("verdict") or row.get("status"))


def hypothesis_placeholder_claims(context: AnalysisContext, metric_summary: dict[str, Any]) -> list[AnalysisClaim]:
    hypotheses = context.hypotheses or []
    if not hypotheses and isinstance(context.task_contract, dict):
        evidence_plan = context.task_contract.get("evidence_plan")
        if isinstance(evidence_plan, dict) and isinstance(evidence_plan.get("hypotheses"), list):
            hypotheses = [
                {"id": f"contract-hypothesis-{index}", "statement": str(item)}
                for index, item in enumerate(evidence_plan["hypotheses"], start=1)
                if str(item).strip()
            ]
    if not hypotheses:
        return [
            AnalysisClaim(
                claim_id="claim-1",
                claim="The run produced numeric metrics, but no explicit hypothesis-level claim was found.",
                verdict="partially_supported" if metric_summary.get("metric_count") else "not_evaluated",
                evidence=[],
                metric_refs=available_metric_names(metric_summary),
                limitations=["No structured hypothesis verdict was available in the run artifacts."],
                confidence="low",
            )
        ]
    claims: list[AnalysisClaim] = []
    for index, row in enumerate(hypotheses, start=1):
        statement = str(row.get("statement") or row.get("claim") or row).strip()
        claims.append(
            AnalysisClaim(
                claim_id=str(row.get("id") or f"hypothesis-{index}"),
                claim=statement,
                verdict="not_evaluated",
                evidence=[],
                metric_refs=[],
                limitations=["No grounded verdict was found in the run artifacts."],
                confidence="low",
            )
        )
    return claims


def deterministic_markdown(
    context: AnalysisContext,
    metric_summary: dict[str, Any],
    claims: list[AnalysisClaim],
    rubric_coverage: list[dict[str, Any]],
    audit: AnalysisAudit,
) -> str:
    lines = [
        f"# Result Analysis: {context.task_id or context.title or 'experiment'}",
        "",
        "## Task",
        "",
        context.research_question or context.title or "(not provided)",
        "",
        "## Rubric Coverage",
        "",
        "| Category | Leaves | Status | Evidence |",
        "| --- | ---: | --- | --- |",
    ]
    for row in rubric_coverage:
        lines.append(
            f"| {row.get('category', 'Uncategorized')} | {row.get('leaf_count', 0)} | "
            f"{row.get('verdict', 'not_evaluated')} | {escape_table_text(row.get('evidence', ''))} |"
        )
    lines.extend(
        [
            "",
        "## Metrics",
        "",
        "| Metric | Value | Direction | Issues |",
        "| --- | ---: | --- | --- |",
        ]
    )
    for metric in metric_summary.get("metrics", []):
        lines.append(
            f"| `{metric.get('name')}` | {format_metric_value(metric.get('value'))} | "
            f"{metric.get('direction')} | {', '.join(metric.get('issues') or []) or '-'} |"
        )
    result_tables = metric_summary.get("result_tables") or {}
    primary_rows = result_tables.get("primary_metric_rows") if isinstance(result_tables, dict) else None
    if isinstance(primary_rows, list) and primary_rows:
        lines.extend(["", "## Primary Metric Table", "", "| Dataset | Condition | Metric | Mean | Std | Count | Evidence ID |", "| --- | --- | --- | ---: | ---: | ---: | --- |"])
        for row in primary_rows[:40]:
            lines.append(
                f"| {row.get('dataset', '')} | {row.get('condition', '')} | {row.get('metric', '')} | "
                f"{format_metric_value(row.get('mean'))} | {format_metric_value(row.get('std'))} | "
                f"{row.get('count', '-')} | `{row.get('evidence_id', '')}` |"
            )
    lines.extend(["", "## Claims", ""])
    for claim in claims:
        lines.append(f"- **{claim.verdict}** `{claim.claim_id}`: {claim.claim}")
        if claim.metric_refs:
            lines.append(f"  Metric refs: {', '.join(claim.metric_refs)}")
        if claim.limitations:
            lines.append(f"  Limitations: {'; '.join(claim.limitations)}")
    if audit.limitations or audit.weak_metric_signals:
        lines.extend(["", "## Limitations", ""])
        for item in list(audit.limitations) + list(audit.weak_metric_signals):
            lines.append(f"- {item}")
    return "\n".join(lines).strip() + "\n"


def build_prompt(
    context: AnalysisContext,
    metric_summary: dict[str, Any],
    fallback: AnalysisResult,
) -> str:
    context_payload = context.model_dump(mode="json")
    context_payload["project_results"] = compact_project_results_for_prompt(context.project_results, metric_summary)
    context_payload["task_contract"] = compact_task_contract_for_prompt(context.task_contract)
    payload = {
        "context": context_payload,
        "metric_summary": metric_summary,
        "deterministic_claims": [claim.model_dump(mode="json") for claim in fallback.claims],
        "deterministic_limitations": fallback.audit.limitations,
    }
    return (
        "Regenerate an experiment result analysis from the provided JSON.\n\n"
        "Return JSON with exactly these keys:\n"
        "- summary: object with method, results, limitations, reproduction_notes. Values must be short plain strings, not Markdown.\n"
        "- rubric_coverage: list of objects with category, verdict, evidence, limitations. Use categories from rubric_categories.\n"
        "- claims: list of claim objects. Each needs claim_id, claim, verdict, evidence, metric_refs, limitations, confidence.\n"
        "- analysis_audit: object with missing_required_metrics, weak_metric_signals, unsupported_claims, limitations, notes.\n\n"
        "Rules:\n"
        "- Use only provided metrics and artifacts.\n"
        "- Do not claim judge success unless judge evidence appears in context.\n"
        "- supported/partially_supported claims must include metric_refs or evidence.\n"
        "- Use metric_refs from result_tables evidence_id values, not raw JSON objects.\n"
        "- Use unsupported when the measured evidence refutes a hypothesis; do not use not_evaluated for refuted hypotheses.\n"
        "- If metrics are weak, missing, all zero, or only resource signals, say so clearly.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}"
    )


def compact_task_contract_for_prompt(contract: dict[str, Any], *, max_task_chars: int = 1600) -> dict[str, Any]:
    if not isinstance(contract, dict) or not contract:
        return {}
    metric_contract = contract.get("metric_contract") if isinstance(contract.get("metric_contract"), dict) else {}
    artifact_contract = contract.get("artifact_contract") if isinstance(contract.get("artifact_contract"), dict) else {}
    evidence_plan = contract.get("evidence_plan") if isinstance(contract.get("evidence_plan"), dict) else {}
    task = str(contract.get("task") or "")
    return {
        "schema_version": contract.get("schema_version", ""),
        "contract_id": contract.get("contract_id", ""),
        "version_hash": contract.get("version_hash", ""),
        "task_kind": contract.get("task_kind", ""),
        "objective": contract.get("objective", ""),
        "task_excerpt": task[:max_task_chars],
        "success_criteria": normalize_string_list(contract.get("success_criteria"))[:20],
        "metric_contract": {
            "primary_metric": metric_contract.get("primary_metric", ""),
            "required_metrics": normalize_string_list(metric_contract.get("required_metrics"))[:30],
            "metric_directions": dict(metric_contract.get("metric_directions") or {}),
        },
        "artifact_contract": {
            "required_artifacts": normalize_string_list(artifact_contract.get("required_artifacts"))[:30],
            "required_comparisons": normalize_string_list(artifact_contract.get("required_comparisons"))[:30],
        },
        "evidence_plan": {
            "hypotheses": normalize_string_list(evidence_plan.get("hypotheses"))[:20],
            "required_conditions": normalize_string_list(evidence_plan.get("required_conditions"))[:30],
            "required_datasets": normalize_string_list(evidence_plan.get("required_datasets"))[:30],
            "required_comparisons": normalize_string_list(evidence_plan.get("required_comparisons"))[:30],
        },
        "claim_specs": [
            row for row in contract.get("claim_specs", []) if isinstance(row, dict)
        ][:20]
        if isinstance(contract.get("claim_specs"), list)
        else [],
    }


def normalize_llm_result(
    response: dict[str, Any],
    context: AnalysisContext,
    metric_summary: dict[str, Any],
    *,
    fallback: AnalysisResult,
) -> AnalysisResult:
    claims = parse_claims(response.get("claims"), fallback=fallback.claims)
    rubric_coverage = parse_rubric_coverage(response.get("rubric_coverage"), fallback=fallback.rubric_coverage)
    audit_data = response.get("analysis_audit") if isinstance(response.get("analysis_audit"), dict) else {}
    audit = AnalysisAudit(
        llm_used=True,
        missing_required_metrics=list(metric_summary.get("missing_required_metrics") or []),
        weak_metric_signals=list(metric_summary.get("weak_metric_signals") or []),
        unsupported_claims=normalize_string_list(audit_data.get("unsupported_claims")),
        limitations=normalize_string_list(audit_data.get("limitations")),
        notes=normalize_string_list(audit_data.get("notes")),
    )
    readme = render_analyzed_markdown(response.get("summary"), context, metric_summary, claims, rubric_coverage, audit)
    return AnalysisResult(
        readme_markdown=readme.strip() + "\n",
        claims=claims,
        claims_payload=claims_payload(context, metric_summary, claims, rubric_coverage),
        metric_summary=metric_summary,
        rubric_coverage=rubric_coverage,
        audit=audit,
    )


def parse_claims(value: Any, *, fallback: list[AnalysisClaim]) -> list[AnalysisClaim]:
    if isinstance(value, dict):
        for key in ("claims", "hypothesis_verdicts"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
    if not isinstance(value, list):
        return fallback
    claims: list[AnalysisClaim] = []
    for index, row in enumerate(value, start=1):
        if not isinstance(row, dict):
            continue
        claim = str(row.get("claim") or row.get("statement") or "").strip()
        if not claim:
            continue
        claims.append(
            AnalysisClaim(
                claim_id=str(row.get("claim_id") or row.get("hypothesis_id") or f"claim-{index}"),
                claim=claim,
                verdict=row_verdict(row),
                evidence=normalize_evidence(row.get("evidence")),
                metric_refs=normalize_metric_refs(row.get("metric_refs")),
                limitations=normalize_string_list(row.get("limitations")),
                confidence=normalize_confidence(row.get("confidence")),
            )
        )
    return claims or fallback


def render_analyzed_markdown(
    summary: Any,
    context: AnalysisContext,
    metric_summary: dict[str, Any],
    claims: list[AnalysisClaim],
    rubric_coverage: list[dict[str, Any]],
    audit: AnalysisAudit,
) -> str:
    if not isinstance(summary, dict):
        return deterministic_markdown(context, metric_summary, claims, rubric_coverage, audit)
    lines = [
        f"# Result Analysis: {context.task_id or context.title or 'experiment'}",
        "",
        "## Task",
        "",
        context.research_question or context.title or "(not provided)",
        "",
        "## Method",
        "",
        str(summary.get("method") or "(not provided)").strip(),
        "",
        "## Results",
        "",
        str(summary.get("results") or "(not provided)").strip(),
        "",
        "## Rubric Coverage",
        "",
        "| Category | Leaves | Status | Evidence |",
        "| --- | ---: | --- | --- |",
    ]
    for row in rubric_coverage:
        lines.append(
            f"| {row.get('category', 'Uncategorized')} | {row.get('leaf_count', 0)} | "
            f"{row.get('verdict', 'not_evaluated')} | {escape_table_text(row.get('evidence', ''))} |"
        )
    lines.extend(
        [
            "",
            "## Global Metrics",
            "",
        "| Metric | Value | Direction | Issues |",
        "| --- | ---: | --- | --- |",
        ]
    )
    for metric in metric_summary.get("metrics", []):
        lines.append(
            f"| `{metric.get('name')}` | {format_metric_value(metric.get('value'))} | "
            f"{metric.get('direction')} | {', '.join(metric.get('issues') or []) or '-'} |"
        )
    result_tables = metric_summary.get("result_tables") or {}
    primary_rows = result_tables.get("primary_metric_rows") if isinstance(result_tables, dict) else None
    if isinstance(primary_rows, list) and primary_rows:
        lines.extend(
            [
                "",
                "## Primary Metric Table",
                "",
                "| Dataset | Condition | Metric | Mean | Std | Count | Evidence ID |",
                "| --- | --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
        for row in primary_rows[:40]:
            lines.append(
                f"| {row.get('dataset', '')} | {row.get('condition', '')} | {row.get('metric', '')} | "
                f"{format_metric_value(row.get('mean'))} | {format_metric_value(row.get('std'))} | "
                f"{row.get('count', '-')} | `{row.get('evidence_id', '')}` |"
            )
    lines.extend(["", "## Claim Verdicts", ""])
    for claim in claims:
        lines.append(f"- **{claim.verdict}** `{claim.claim_id}`: {claim.claim}")
        if claim.metric_refs:
            lines.append(f"  Metric refs: {', '.join(claim.metric_refs)}")
        if claim.limitations:
            lines.append(f"  Limitations: {'; '.join(claim.limitations)}")
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            str(summary.get("limitations") or "; ".join(audit.limitations) or "(not provided)").strip(),
            "",
            "## Reproduction Notes",
            "",
            str(summary.get("reproduction_notes") or "(not provided)").strip(),
        ]
    )
    return "\n".join(lines)


def audit_result(result: AnalysisResult, metric_summary: dict[str, Any]) -> AnalysisAudit:
    audit = result.audit.model_copy(deep=True)
    audit.missing_required_metrics = sorted(
        set(audit.missing_required_metrics) | set(metric_summary.get("missing_required_metrics") or [])
    )
    audit.weak_metric_signals = sorted(
        set(audit.weak_metric_signals) | set(metric_summary.get("weak_metric_signals") or [])
    )
    available = set(available_metric_names(metric_summary))
    downgraded: list[str] = []
    unsupported: list[str] = []
    for claim in result.claims:
        if claim.verdict in {"supported", "partially_supported"}:
            refs = set(claim.metric_refs)
            has_metric = bool(refs & available)
            has_evidence = bool(claim.evidence)
            if not has_metric and not has_evidence:
                claim.verdict = "not_evaluated"
                claim.confidence = "low"
                claim.limitations.append("Claim was downgraded because no metric reference or evidence was provided.")
                downgraded.append(claim.claim_id)
        if claim.verdict in {"unsupported", "not_evaluated"}:
            unsupported.append(claim.claim_id)
    audit.downgraded_claims = sorted(set(audit.downgraded_claims) | set(downgraded))
    audit.unsupported_claims = sorted(set(audit.unsupported_claims) | set(unsupported))
    if not result.claims:
        audit.limitations.append("No claims were generated.")
    return audit


def derive_analysis_status(
    context: AnalysisContext,
    metric_summary: Mapping[str, Any],
) -> tuple[AnalysisStatus, list[str]]:
    """Classify observed experiment evidence without choosing a next step.

    A standalone analysis has no execution outcome to classify, so it remains
    ``incomplete``. When a canonical execution record is present, execution
    failure and blocking are preserved, guard or metric deficiencies are
    reported as incomplete, and only an explicit comparison verdict can
    produce ``metric_below_target``. Research policy remains in the
    caller-owned session layer instead of being inferred from prose or raw
    metric values.
    """

    execution = context.project_results.get("execution_result")
    if not isinstance(execution, Mapping):
        return "incomplete", ["No canonical execution result was provided."]

    execution_status = str(execution.get("status") or "unknown").strip().lower()
    if execution_status in {"blocked", "blocked_by_validation"}:
        return "blocked", [f"Execution was blocked with status `{execution_status}`."]
    if execution_status not in {"passed", "completed"}:
        return "failed", [f"Execution did not pass: `{execution_status}`."]

    guard = execution.get("guard")
    if isinstance(guard, Mapping) and str(guard.get("status") or "").strip().lower() == "failed":
        return "incomplete", ["The result guard reported blocking errors."]
    missing = list(metric_summary.get("missing_required_metrics") or [])
    if missing:
        return "incomplete", [
            "Required metrics are missing: " + ", ".join(str(item) for item in missing[:12])
        ]

    comparisons = execution.get("comparisons")
    if isinstance(comparisons, list):
        for comparison in comparisons:
            if not isinstance(comparison, Mapping):
                continue
            verdict = str(comparison.get("verdict") or "").strip().lower()
            if verdict in {"regressed", "metric_below_target"}:
                return "metric_below_target", [
                    f"An explicit experiment comparison returned `{verdict}`."
                ]

    return "passed", ["Execution passed and required result evidence is present."]


def write_analysis_artifacts(output_dir: Path, context: AnalysisContext, result: AnalysisResult) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "analysis_context.json", context.model_dump(mode="json"))
    write_json(output_dir / "metric_summary.json", result.metric_summary)
    write_json(output_dir / "rubric_coverage.json", result.rubric_coverage)
    write_json(output_dir / "claims.json", result.claims_payload)
    write_text(output_dir / "analysis_report.md", result.readme_markdown)
    write_json(output_dir / "analysis_audit.json", result.audit.model_dump(mode="json"))
    write_json(
        output_dir / "analysis_status.json",
        {
            "schema_version": "analysis_status.v1",
            "status": result.status,
            "reasons": result.status_reasons,
        },
    )
    if result.raw_llm_response is not None:
        write_json(output_dir / "analysis_response.json", result.raw_llm_response)


def record_result_analysis_memory(
    run_dir: Path,
    result: AnalysisResult,
    *,
    output_dir: Path | None = None,
) -> None:
    """Optionally write result-analysis signals back to code-task memory.

    This helper keeps result-analysis independent from code-task orchestration:
    callers opt in only when they know ``run_dir`` is a code-task run. It records
    weak or missing evidence as repair memory so a later repair attempt can see
    why the result writeup or metric evidence was judged insufficient.
    """

    audit = result.audit
    weak = list(audit.weak_metric_signals)
    missing = list(audit.missing_required_metrics)
    unsupported = list(audit.unsupported_claims)
    downgraded = list(audit.downgraded_claims)
    if not (weak or missing or unsupported or downgraded):
        return
    try:
        from simple_ar.code_task.memory import record_code_task_memory_event, record_repair_memory
    except Exception:
        return
    artifacts = _analysis_memory_artifacts(output_dir)
    summary_parts: list[str] = []
    if weak:
        summary_parts.append("weak=" + "; ".join(weak[:4]))
    if missing:
        summary_parts.append("missing_metrics=" + ", ".join(missing[:8]))
    if unsupported:
        summary_parts.append("unsupported_claims=" + ", ".join(unsupported[:8]))
    if downgraded:
        summary_parts.append("downgraded_claims=" + ", ".join(downgraded[:8]))
    summary = "Result-analysis weak evidence: " + " | ".join(summary_parts)
    record_code_task_memory_event(
        run_dir,
        event_type="result_analysis",
        summary=summary,
        status="weak_evidence",
        artifacts=artifacts,
        metadata={
            "weak_metric_signals": weak,
            "missing_required_metrics": missing,
            "unsupported_claims": unsupported,
            "downgraded_claims": downgraded,
        },
        key="result-analysis:weak-evidence:" + str(output_dir or ""),
    )
    record_repair_memory(
        run_dir,
        failure_summary=summary,
        attempted_fix="Inspect result-analysis evidence gaps before the next repair or report-generation attempt.",
        status="weak_evidence",
        artifacts=artifacts,
        metadata={
            "weak_metric_signals": weak,
            "missing_required_metrics": missing,
            "unsupported_claims": unsupported,
            "downgraded_claims": downgraded,
        },
        key="result-analysis-repair-memory:" + str(output_dir or ""),
    )


def _analysis_memory_artifacts(output_dir: Path | None) -> list[str]:
    if output_dir is None:
        return []
    return [
        (output_dir / "analysis_report.md").as_posix(),
        (output_dir / "analysis_audit.json").as_posix(),
        (output_dir / "metric_summary.json").as_posix(),
    ]


def claims_payload(
    context: AnalysisContext,
    metric_summary: dict[str, Any],
    claims: list[AnalysisClaim],
    rubric_coverage: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "simple_ar_result_claims.v1",
        "task_id": context.task_id,
        "topic_id": context.task_id,
        "summary_metrics": context.metrics,
        "metric_summary": metric_summary,
        "rubric_coverage": rubric_coverage,
        "task_contract": {
            "contract_id": context.task_contract.get("contract_id", ""),
            "version_hash": context.task_contract.get("version_hash", ""),
            "schema_version": context.task_contract.get("schema_version", ""),
        }
        if isinstance(context.task_contract, dict) and context.task_contract
        else {},
        "hypothesis_verdicts": [claim.model_dump(mode="json") for claim in claims],
        "claims": [claim.model_dump(mode="json") for claim in claims],
    }


def build_rubric_categories(context: AnalysisContext) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for criterion in context.criteria:
        if not isinstance(criterion, dict):
            continue
        category = str(criterion.get("task_category") or "Uncategorized")
        group = groups.setdefault(category, {"category": category, "leaf_count": 0, "weight": 0.0, "leaves": []})
        group["leaf_count"] += 1
        try:
            group["weight"] += float(criterion.get("weight") or 0.0)
        except (TypeError, ValueError):
            pass
        group["leaves"].append(
            {
                "id": criterion.get("id"),
                "requirements": criterion.get("requirements"),
                "weight": criterion.get("weight"),
                "finegrained_task_category": criterion.get("finegrained_task_category"),
            }
        )
    return list(groups.values())


def deterministic_rubric_coverage(
    context: AnalysisContext,
    metric_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in metric_summary.get("rubric_categories", []) or build_rubric_categories(context):
        category = str(group.get("category") or "Uncategorized")
        if category == "Code Execution" and metric_summary.get("metric_count"):
            verdict = "partially_supported"
            evidence = "Numeric metrics were produced; detailed criterion coverage still needs review."
        elif category == "Result Analysis" and context.existing_writeup:
            verdict = "partially_supported"
            evidence = "A writeup was found; claim grounding still needs review."
        else:
            verdict = "not_evaluated"
            evidence = "No category-specific review was generated."
        rows.append(
            {
                "category": category,
                "leaf_count": group.get("leaf_count", 0),
                "weight": group.get("weight", 0.0),
                "verdict": verdict,
                "evidence": evidence,
                "limitations": [],
            }
        )
    return rows


def parse_rubric_coverage(value: Any, *, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return fallback
    fallback_by_category = {
        str(row.get("category") or "Uncategorized"): row
        for row in fallback
        if isinstance(row, dict)
    }
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        category = str(row.get("category") or "Uncategorized")
        base = fallback_by_category.get(category, {})
        leaf_count = safe_positive_int(row.get("leaf_count"), default=safe_positive_int(base.get("leaf_count"), default=0))
        weight = row.get("weight", base.get("weight", 0.0))
        rows.append(
            {
                "category": category,
                "leaf_count": leaf_count,
                "weight": weight,
                "verdict": normalize_verdict(row.get("verdict")),
                "evidence": str(row.get("evidence") or ""),
                "limitations": normalize_string_list(row.get("limitations")),
            }
        )
        seen.add(category)
    for category, row in fallback_by_category.items():
        if category not in seen:
            rows.append(row)
    return rows or fallback


def build_result_tables(context: AnalysisContext) -> dict[str, Any]:
    table_rows = extract_result_table_rows(context.project_results)
    primary_metric = ""
    expected = context.expected_metrics or []
    if expected and isinstance(expected[0], dict):
        primary_metric = str(expected[0].get("name") or "")
    primary_metric = primary_metric or infer_primary_metric(context)
    primary_rows = [row for row in table_rows if metric_names_match(str(row.get("metric") or ""), primary_metric)]
    return {
        "primary_metric": primary_metric,
        "primary_metric_rows": primary_rows,
        "all_metric_rows": table_rows[:240],
    }


def infer_primary_metric(context: AnalysisContext) -> str:
    for name, direction in context.metric_directions.items():
        if direction not in {"resource", "ignore"} and not is_auxiliary_metric_name(str(name)):
            return str(name)
    for name in context.metrics:
        text = str(name)
        if not is_auxiliary_metric_name(text):
            return text
    return next(iter(context.metrics), "")


def metric_names_match(observed: str, expected: str) -> bool:
    if not observed or not expected:
        return False
    return bool(metric_name_aliases(observed) & metric_name_aliases(expected))


def metric_name_aliases(name: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    aliases = {normalized} if normalized else set()
    if not normalized:
        return aliases

    prefixes = (
        "test",
        "heldout",
        "held_out",
        "validation",
        "val",
        "mean",
        "avg",
        "average",
        "overall",
        "final",
    )
    for prefix in prefixes:
        marker = prefix + "_"
        if normalized.startswith(marker) and len(normalized) > len(marker):
            aliases.add(normalized[len(marker) :])

    suffixes = ("_mean", "_score")
    for suffix in suffixes:
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            aliases.add(normalized[: -len(suffix)])

    # Keep semantically specific names such as balanced_accuracy distinct, but
    # let common project-level labels match per-cell metric records.
    if normalized in {"test_accuracy", "heldout_accuracy", "held_out_accuracy", "mean_accuracy", "avg_accuracy"}:
        aliases.add("accuracy")
    if normalized in {"test_rmse", "mean_rmse", "avg_rmse", "overall_rmse"}:
        aliases.add("rmse")
    if normalized in {"test_mae", "mean_mae", "avg_mae", "overall_mae"}:
        aliases.add("mae")
    return aliases


def evidence_chain_warnings(context: AnalysisContext, result_tables: dict[str, Any]) -> list[str]:
    rows = result_tables.get("all_metric_rows") if isinstance(result_tables, dict) else None
    if isinstance(rows, list) and rows:
        return []
    warnings: list[str] = []
    if context.metrics:
        warnings.append("no condition-level result table extracted")
    raw_count = len(discover_raw_records(context.project_results))
    if raw_count:
        warnings.append(f"raw condition records were present but not normalized into result tables: {raw_count}")
    return warnings


def find_per_cell_records(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    summary = data.get("summary")
    if isinstance(summary, dict) and isinstance(summary.get("per_cell"), list):
        return [row for row in summary["per_cell"] if isinstance(row, dict)]
    if isinstance(data.get("per_cell"), list):
        return [row for row in data["per_cell"] if isinstance(row, dict)]
    return []


def extract_result_table_rows(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, (dict, list)):
        return []
    rows: list[dict[str, Any]] = []
    for record in aggregate_records(data):
        rows.extend(normalized_metric_rows(record))
    rows.extend(aggregate_raw_records(raw_records(data)))
    if rows:
        return dedupe_table_rows(rows)
    return []


def aggregate_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return discover_aggregate_records(data)
    if not isinstance(data, dict):
        return []
    records: list[dict[str, Any]] = []
    summary = data.get("summary")
    if isinstance(summary, dict) and isinstance(summary.get("per_cell"), list):
        records.extend(row for row in summary["per_cell"] if isinstance(row, dict))
    for key in (
        "per_cell",
        "cells",
        "aggregates",
        "aggregate_rows",
        "summaries",
        "condition_aggregates",
    ):
        value = data.get(key)
        if isinstance(value, list):
            records.extend(row for row in value if isinstance(row, dict))
    records.extend(discover_aggregate_records(data))
    return records


def discover_aggregate_records(value: Any, *, depth: int = 0, max_depth: int = 4) -> list[dict[str, Any]]:
    if depth > max_depth:
        return []
    records: list[dict[str, Any]] = []
    if isinstance(value, list):
        for row in value:
            if isinstance(row, dict) and looks_like_aggregate_record(row):
                records.append(row)
            elif isinstance(row, (dict, list)):
                records.extend(discover_aggregate_records(row, depth=depth + 1, max_depth=max_depth))
        return records
    if not isinstance(value, dict):
        return records
    if looks_like_aggregate_record(value):
        records.append(value)
    for key, child in value.items():
        if key in {"split_rows", "rows", "runs", "splits", "seed_evidence"}:
            continue
        if isinstance(child, (dict, list)):
            records.extend(discover_aggregate_records(child, depth=depth + 1, max_depth=max_depth))
    return records


def looks_like_aggregate_record(record: dict[str, Any]) -> bool:
    if not record_dataset(record) or not record_condition(record):
        return False
    if isinstance(record.get("mean"), dict):
        return True
    if record.get("metric") and is_number(record.get("mean")):
        return True
    for key, value in record.items():
        if isinstance(value, dict) and "mean" in value:
            return True
        if key.endswith("_mean") and is_number(value):
            return True
        if key.startswith("mean_") and is_number(value):
            return True
    return False


def raw_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return dedupe_records(discover_raw_records(data))
    if not isinstance(data, dict):
        return []
    for keys in (
        ("cells", "cell_records", "records", "per_cell"),
        ("rows", "runs", "splits", "seed_evidence"),
        ("condition_summaries", "summaries"),
    ):
        records: list[dict[str, Any]] = []
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                records.extend(row for row in value if isinstance(row, dict))
        if records:
            return dedupe_records(records)
    return dedupe_records(discover_raw_records(data))


def dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        try:
            key = json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)
        except TypeError:
            key = repr(sorted(record.items()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def discover_raw_records(value: Any, *, depth: int = 0, max_depth: int = 4) -> list[dict[str, Any]]:
    if depth > max_depth:
        return []
    records: list[dict[str, Any]] = []
    if isinstance(value, list):
        dict_rows = [row for row in value if isinstance(row, dict)]
        raw_rows = [
            row
            for row in dict_rows
            if looks_like_raw_result_record(row) and not looks_like_aggregate_record(row)
        ]
        if raw_rows:
            return raw_rows
        for row in value:
            if isinstance(row, (dict, list)):
                records.extend(discover_raw_records(row, depth=depth + 1, max_depth=max_depth))
        return records
    if not isinstance(value, dict):
        return []
    for child in value.values():
        if isinstance(child, (dict, list)):
            records.extend(discover_raw_records(child, depth=depth + 1, max_depth=max_depth))
    return records


def looks_like_raw_result_record(record: dict[str, Any]) -> bool:
    if not record_condition(record):
        return False
    numeric_keys = [
        key
        for key, value in record.items()
        if is_number(value) and key not in RAW_NON_METRIC_KEYS and not key.endswith("_id")
    ]
    nested_metrics = record.get("metrics")
    return bool(
        numeric_keys
        or (
            isinstance(nested_metrics, dict)
            and any(is_number(value) for value in nested_metrics.values())
        )
    )


def normalized_metric_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    dataset = record_dataset(record)
    condition = record_condition(record)
    if not dataset or not condition:
        return []
    count = first_present(record, "count", "n", "n_seeds", "seed_count", "n_splits", "split_count")
    rows: list[dict[str, Any]] = []

    for key, value in record.items():
        if isinstance(value, dict) and "mean" in value:
            rows.append(table_row(dataset, condition, key, value.get("mean"), value.get("std"), value.get("count", count)))

    mean_map = record.get("mean")
    std_map = record.get("std")
    if isinstance(mean_map, dict):
        for metric, mean in mean_map.items():
            std = std_map.get(metric) if isinstance(std_map, dict) else None
            rows.append(table_row(dataset, condition, str(metric), mean, std, count))

    scalar_metric = record.get("metric")
    if scalar_metric and is_number(record.get("mean")):
        rows.append(table_row(dataset, condition, str(scalar_metric), record.get("mean"), record.get("std"), count))

    for key, value in record.items():
        if key.endswith("_mean") and is_number(value):
            metric = key[: -len("_mean")]
            std = record.get(f"{metric}_std")
            rows.append(table_row(dataset, condition, metric, value, std, count))
        elif key.startswith("mean_") and is_number(value):
            metric = key[len("mean_") :]
            std = record.get(f"std_{metric}")
            rows.append(table_row(dataset, condition, metric, value, std, count))

    return rows


def aggregate_raw_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], list[float]] = {}
    for record in records:
        dataset = record_dataset(record)
        condition = record_condition(record)
        if not dataset or not condition:
            continue
        metric_values = raw_metric_values(record)
        for metric, value in metric_values.items():
            if is_number(value):
                buckets.setdefault((dataset, condition, metric), []).append(float(value))
    rows: list[dict[str, Any]] = []
    for (dataset, condition, metric), values in buckets.items():
        if not values:
            continue
        mean = sum(values) / len(values)
        std = sample_std(values)
        rows.append(table_row(dataset, condition, metric, mean, std, len(values)))
    return rows


def raw_metric_values(record: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    nested = record.get("metrics")
    if isinstance(nested, dict):
        values.update(nested)
    for key, value in record.items():
        if key in RAW_NON_METRIC_KEYS or key.endswith("_id") or key.endswith("_name"):
            continue
        if isinstance(value, (dict, list, tuple, set)):
            continue
        if is_number(value):
            values[key] = value
    return values


RAW_NON_METRIC_KEYS = {
    "seed",
    "split_seed",
    "split_idx",
    "fold",
    "count",
    "n",
    "n_train",
    "n_test",
    "n_features",
    "n_classes",
    "n_samples",
    "dataset_num_classes",
    "dataset_split_seed",
    "fallback_used",
    "is_modal_selection",
}


def is_auxiliary_metric_name(name: str) -> bool:
    lowered = name.lower()
    return any(
        token in lowered
        for token in (
            "runtime",
            "time",
            "wall_clock",
            "memory",
            "seed_count",
            "metric_count",
            "dataset_count",
            "condition_count",
            "row_count",
            "n_samples",
        )
    )


def record_dataset(record: dict[str, Any]) -> str:
    aliases = ("dataset_name", "dataset", "dataset_id", "family", "regime", "source", "data")
    value = first_nonempty(record, aliases)
    if isinstance(value, dict):
        value = value.get("name") or value.get("id") or value.get("dataset_id") or ""
    text = str(value).strip()
    if text:
        return text
    nested = nested_identity(record)
    value = first_nonempty(nested, aliases)
    if value:
        return str(value).strip()
    for key in ("n_train", "train_size", "sample_size", "horizon", "step"):
        if nested.get(key) is not None:
            return f"{key}={nested[key]}"
    for key in ("n_train", "train_size", "sample_size", "horizon", "step"):
        if record.get(key) is not None:
            return f"{key}={record[key]}"
    return "all"


def record_condition(record: dict[str, Any]) -> str:
    aliases = (
        "condition_name",
        "condition_id",
        "strategy_name",
        "model",
        "model_name",
        "model_type",
        "method",
        "method_name",
        "algorithm",
        "algorithm_name",
        "detector",
        "detector_name",
        "estimator",
        "estimator_name",
        "learner",
        "learner_name",
        "kernel",
        "kernel_name",
        "condition",
        "schedule",
    )
    value = first_nonempty(record, aliases)
    if isinstance(value, dict):
        value = (
            value.get("condition_id")
            or value.get("name")
            or value.get("id")
            or value.get("method")
            or value.get("model")
            or ""
        )
    text = str(value).strip()
    nested = nested_identity(record)
    if not text:
        text = str(first_nonempty(nested, aliases) or "").strip()
    if not text:
        text = inferred_identifier(record)
    if not text:
        text = inferred_identifier(nested)
    if not text and record.get("n_train") is not None and record.get("method") is not None:
        text = f"{record.get('method')}:n_train={record.get('n_train')}"
    return text


def first_nonempty(record: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None and value != "":
            return value
    return ""


def nested_identity(record: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("key", "identity", "configuration", "config", "parameters"):
        value = record.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def inferred_identifier(record: Mapping[str, Any]) -> str:
    excluded = {
        "dataset_id",
        "hypothesis_id",
        "claim_id",
        "seed_id",
        "split_id",
        "fold_id",
        "run_id",
        "trial_id",
    }
    for key, value in record.items():
        lowered = str(key).lower()
        if lowered in excluded or not lowered.endswith("_id"):
            continue
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
    return ""


def table_row(dataset: str, condition: str, metric: str, mean: Any, std: Any, count: Any) -> dict[str, Any]:
    evidence_id = f"{metric}:{dataset}:{condition}".replace(" ", "_")
    return {
        "evidence_id": evidence_id,
        "dataset": dataset,
        "condition": condition,
        "metric": metric,
        "mean": mean,
        "std": std,
        "count": count,
    }


def dedupe_table_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (str(row.get("dataset")), str(row.get("condition")), str(row.get("metric")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def first_present(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if record.get(key) is not None:
            return record[key]
    return None


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def sample_std(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return variance ** 0.5


def compact_project_results_for_prompt(data: Any, metric_summary: dict[str, Any]) -> Any:
    if not isinstance(data, dict):
        return data
    compact: dict[str, Any] = {
        "available_keys": sorted(str(key) for key in data.keys()),
        "result_tables": metric_summary.get("result_tables", {}),
    }
    for key in ("claims", "hypothesis_verdicts", "verdicts", "hypotheses", "metrics", "metric_bundle", "limitations"):
        value = data.get(key)
        if value is not None:
            compact[key] = value
    if isinstance(data.get("summary"), dict):
        compact["summary"] = data["summary"]
    source = data.get("_artifact_source")
    if source:
        compact["_artifact_source"] = source
    tables = metric_summary.get("result_tables")
    all_rows = tables.get("all_metric_rows") if isinstance(tables, dict) else None
    if not all_rows:
        records = raw_records(data)
        if records:
            compact["raw_result_count"] = len(records)
            compact["raw_result_sample"] = bounded_record_sample(records)
    return compact


def bounded_record_sample(
    records: list[dict[str, Any]],
    *,
    max_records: int = 8,
    max_chars: int = 12000,
) -> list[Any]:
    sample: list[Any] = []
    used = 0
    for record in records[:max_records]:
        rendered = json.dumps(record, ensure_ascii=False, default=str)
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(rendered) <= remaining:
            sample.append(record)
            used += len(rendered)
            continue
        sample.append({"_truncated_record": rendered[:remaining]})
        break
    return sample


def deterministic_limitations(metric_summary: dict[str, Any]) -> list[str]:
    limitations: list[str] = []
    if metric_summary.get("missing_required_metrics"):
        limitations.append("Some expected metrics were missing.")
    if metric_summary.get("weak_metric_signals"):
        limitations.append("Metric signals were weak or incomplete.")
    if not metric_summary.get("metric_count"):
        limitations.append("No numeric metrics were found.")
    return limitations


def normalize_verdict(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    if text in {"supported", "support", "supports", "pass", "passed"}:
        return "supported"
    if text in {"partial", "partially_supported", "partially supported"}:
        return "partially_supported"
    if text in {"unsupported", "failed", "fail", "refute", "refuted", "contradicted", "false"}:
        return "unsupported"
    return "not_evaluated"


def safe_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def normalize_confidence(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"high", "medium", "low"}:
        return text
    return "low"


def normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        return [json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)]
    if isinstance(value, (list, tuple, set)):
        rows: list[str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            else:
                text = str(item).strip()
            if text:
                rows.append(text)
        return rows
    text = str(value).strip()
    return [text] if text else []


def normalize_evidence(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item if isinstance(item, dict) else {"text": str(item)} for item in value]
    if isinstance(value, dict):
        return [value]
    if value:
        return [{"text": str(value)}]
    return []


def normalize_metric_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    refs: list[str] = []
    for item in value:
        if isinstance(item, dict):
            metric = item.get("metric")
            dataset = item.get("dataset_name") or item.get("dataset")
            condition = item.get("condition_name") or item.get("condition") or item.get("model")
            if metric and dataset and condition:
                refs.append(f"{metric}:{dataset}:{condition}".replace(" ", "_"))
            elif metric:
                refs.append(str(metric))
            else:
                refs.append(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))
        elif item:
            refs.append(str(item))
    return refs


def available_metric_names(metric_summary: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for row in metric_summary.get("metrics", []):
        if isinstance(row, dict) and row.get("present") and row.get("name"):
            names.append(str(row["name"]))
    return names


def format_metric_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def escape_table_text(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
