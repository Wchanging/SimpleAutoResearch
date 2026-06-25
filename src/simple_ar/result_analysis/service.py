from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from simple_ar.integrations.llm import parse_json_object

from .metrics import build_metric_summary
from .schema import AnalysisAudit, AnalysisClaim, AnalysisContext, AnalysisResult


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
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_text(output_dir / "analysis_prompt.txt", user)

    raw = client.ask(system, user + "\n\nReturn valid JSON only. Do not include markdown or extra text.", label=label)
    if output_dir is not None:
        write_text(output_dir / "analysis_raw_response.txt", raw)

    parsed = parse_json_object(raw)
    if parsed is None:
        hint = ""
        if output_dir is not None:
            hint = f" Raw response saved to {output_dir / 'analysis_raw_response.txt'}."
        raise ValueError("LLM result-analysis response did not contain a valid JSON object." + hint)
    return parsed


def deterministic_result(context: AnalysisContext, metric_summary: dict[str, Any]) -> AnalysisResult:
    claims = existing_claims(context)
    if not claims:
        claims = hypothesis_placeholder_claims(context, metric_summary)
    audit = AnalysisAudit(
        llm_used=False,
        missing_required_metrics=list(metric_summary.get("missing_required_metrics") or []),
        weak_metric_signals=list(metric_summary.get("weak_metric_signals") or []),
        limitations=deterministic_limitations(metric_summary),
        notes=["deterministic result-analysis fallback"],
    )
    readme = deterministic_markdown(context, metric_summary, claims, audit)
    return AnalysisResult(
        readme_markdown=readme,
        claims=claims,
        claims_payload=claims_payload(context, metric_summary, claims),
        metric_summary=metric_summary,
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
    if not isinstance(rows, list):
        return []
    claims: list[AnalysisClaim] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        claim_text = str(row.get("claim") or row.get("statement") or row.get("summary") or "").strip()
        if not claim_text:
            continue
        claims.append(
            AnalysisClaim(
                claim_id=str(row.get("claim_id") or row.get("hypothesis_id") or f"claim-{index}"),
                claim=claim_text,
                verdict=normalize_verdict(row.get("verdict")),
                evidence=normalize_evidence(row.get("evidence")),
                metric_refs=[str(item) for item in row.get("metric_refs", []) if item],
                limitations=[str(item) for item in row.get("limitations", []) if item],
                confidence=normalize_confidence(row.get("confidence")),
            )
        )
    return claims


def hypothesis_placeholder_claims(context: AnalysisContext, metric_summary: dict[str, Any]) -> list[AnalysisClaim]:
    hypotheses = context.hypotheses or []
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
    audit: AnalysisAudit,
) -> str:
    lines = [
        f"# Result Analysis: {context.task_id or context.title or 'experiment'}",
        "",
        "## Task",
        "",
        context.research_question or context.title or "(not provided)",
        "",
        "## Metrics",
        "",
        "| Metric | Value | Direction | Issues |",
        "| --- | ---: | --- | --- |",
    ]
    for metric in metric_summary.get("metrics", []):
        lines.append(
            f"| `{metric.get('name')}` | {format_metric_value(metric.get('value'))} | "
            f"{metric.get('direction')} | {', '.join(metric.get('issues') or []) or '-'} |"
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
    payload = {
        "context": context.model_dump(mode="json"),
        "metric_summary": metric_summary,
        "deterministic_claims": [claim.model_dump(mode="json") for claim in fallback.claims],
        "deterministic_limitations": fallback.audit.limitations,
    }
    return (
        "Regenerate an experiment result analysis from the provided JSON.\n\n"
        "Return JSON with exactly these keys:\n"
        "- summary: object with method, results, limitations, reproduction_notes. Values must be short plain strings, not Markdown.\n"
        "- claims: list of claim objects. Each needs claim_id, claim, verdict, evidence, metric_refs, limitations, confidence.\n"
        "- analysis_audit: object with missing_required_metrics, weak_metric_signals, unsupported_claims, limitations, notes.\n\n"
        "Rules:\n"
        "- Use only provided metrics and artifacts.\n"
        "- Do not claim judge success unless judge evidence appears in context.\n"
        "- supported/partially_supported claims must include metric_refs or evidence.\n"
        "- If metrics are weak, missing, all zero, or only resource signals, say so clearly.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}"
    )


def normalize_llm_result(
    response: dict[str, Any],
    context: AnalysisContext,
    metric_summary: dict[str, Any],
    *,
    fallback: AnalysisResult,
) -> AnalysisResult:
    claims = parse_claims(response.get("claims"), fallback=fallback.claims)
    audit_data = response.get("analysis_audit") if isinstance(response.get("analysis_audit"), dict) else {}
    audit = AnalysisAudit(
        llm_used=True,
        missing_required_metrics=list(metric_summary.get("missing_required_metrics") or []),
        weak_metric_signals=list(metric_summary.get("weak_metric_signals") or []),
        unsupported_claims=[str(item) for item in audit_data.get("unsupported_claims", []) if item],
        limitations=[str(item) for item in audit_data.get("limitations", []) if item],
        notes=[str(item) for item in audit_data.get("notes", []) if item],
    )
    readme = render_analyzed_markdown(response.get("summary"), context, metric_summary, claims, audit)
    return AnalysisResult(
        readme_markdown=readme.strip() + "\n",
        claims=claims,
        claims_payload=claims_payload(context, metric_summary, claims),
        metric_summary=metric_summary,
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
                verdict=normalize_verdict(row.get("verdict")),
                evidence=normalize_evidence(row.get("evidence")),
                metric_refs=[str(item) for item in row.get("metric_refs", []) if item],
                limitations=[str(item) for item in row.get("limitations", []) if item],
                confidence=normalize_confidence(row.get("confidence")),
            )
        )
    return claims or fallback


def render_analyzed_markdown(
    summary: Any,
    context: AnalysisContext,
    metric_summary: dict[str, Any],
    claims: list[AnalysisClaim],
    audit: AnalysisAudit,
) -> str:
    if not isinstance(summary, dict):
        return deterministic_markdown(context, metric_summary, claims, audit)
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
        "| Metric | Value | Direction | Issues |",
        "| --- | ---: | --- | --- |",
    ]
    for metric in metric_summary.get("metrics", []):
        lines.append(
            f"| `{metric.get('name')}` | {format_metric_value(metric.get('value'))} | "
            f"{metric.get('direction')} | {', '.join(metric.get('issues') or []) or '-'} |"
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


def write_analysis_artifacts(output_dir: Path, context: AnalysisContext, result: AnalysisResult) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "analysis_context.json", context.model_dump(mode="json"))
    write_json(output_dir / "metric_summary.json", result.metric_summary)
    write_json(output_dir / "claims.json", result.claims_payload)
    write_text(output_dir / "analysis_report.md", result.readme_markdown)
    write_json(output_dir / "analysis_audit.json", result.audit.model_dump(mode="json"))
    if result.raw_llm_response is not None:
        write_json(output_dir / "analysis_response.json", result.raw_llm_response)


def claims_payload(
    context: AnalysisContext,
    metric_summary: dict[str, Any],
    claims: list[AnalysisClaim],
) -> dict[str, Any]:
    return {
        "schema_version": "simple_ar_result_claims.v1",
        "task_id": context.task_id,
        "topic_id": context.task_id,
        "summary_metrics": context.metrics,
        "metric_summary": metric_summary,
        "hypothesis_verdicts": [claim.model_dump(mode="json") for claim in claims],
        "claims": [claim.model_dump(mode="json") for claim in claims],
    }


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
    if text in {"supported", "pass", "passed"}:
        return "supported"
    if text in {"partial", "partially_supported", "partially supported"}:
        return "partially_supported"
    if text in {"unsupported", "failed", "fail"}:
        return "unsupported"
    return "not_evaluated"


def normalize_confidence(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"high", "medium", "low"}:
        return text
    return "low"


def normalize_evidence(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item if isinstance(item, dict) else {"text": str(item)} for item in value]
    if isinstance(value, dict):
        return [value]
    if value:
        return [{"text": str(value)}]
    return []


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


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
