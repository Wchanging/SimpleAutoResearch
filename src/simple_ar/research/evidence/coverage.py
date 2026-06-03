from __future__ import annotations

import re
from typing import Any

from simple_ar.research.contracts import QueryPlan, ResearchQuestion


def build_coverage_report(
    *,
    topic: str,
    questions: list[ResearchQuestion],
    query_plan: QueryPlan,
    selection_rows: list[dict[str, Any]],
    retrieval_rows: list[dict[str, Any]],
    max_documents: int,
    next_query_limit: int = 3,
) -> dict[str, Any]:
    """Build a retrieval coverage report from provider traces and selections.

    The report is intentionally lexical and auditable. It does not claim that a
    topic is fully solved, nor does it perform reading-stage paper screening.
    It records which configured facets have at least one selected retrieval
    candidate and which follow-up queries should be tried next.
    """

    kept_paper_ids = {
        str(row.get("paper_id") or "")
        for row in selection_rows
        if row.get("decision") == "keep" and row.get("paper_id")
    }
    evidence_rows = [
        row
        for row in selection_rows
        if _is_evidence_row(row, kept_paper_ids)
    ]
    required_facets = _required_facets(questions, query_plan)
    covered_facets = sorted(
        {
            str(row.get("facet") or "").strip()
            for row in evidence_rows
            if str(row.get("facet") or "").strip() in required_facets
        }
    )
    missing_facets = [facet for facet in required_facets if facet not in set(covered_facets)]
    question_rows = [
        _question_coverage_row(question, evidence_rows)
        for question in questions
    ]
    executed_queries = _executed_queries(retrieval_rows)
    follow_ups = _follow_up_queries(
        topic=topic,
        missing_facets=missing_facets,
        query_plan=query_plan,
        executed_queries=executed_queries,
        limit=next_query_limit,
    )
    status = _coverage_status(
        required_facets=required_facets,
        missing_facets=missing_facets,
        kept_count=len(evidence_rows),
    )
    return {
        "schema_version": "coverage_report.v1",
        "status": status,
        "required_facets": required_facets,
        "covered_facets": covered_facets,
        "missing_facets": missing_facets,
        "questions": question_rows,
        "follow_up_queries": follow_ups,
        "retrieval": {
            "planned_rounds": query_plan.max_rounds,
            "executed_rounds": max(
                [int(row.get("round") or 0) for row in retrieval_rows] or [0]
            ),
            "executed_queries": executed_queries,
            "attempt_count": len(retrieval_rows),
            "sources": sorted({str(row.get("source") or "") for row in retrieval_rows if row.get("source")}),
        },
        "retrieval_selection": {
            "kept_documents": len([row for row in selection_rows if row.get("decision") == "keep"]),
            "evidence_rows": len(evidence_rows),
            "candidate_rows": len(selection_rows),
            "max_documents": max_documents,
        },
    }


def coverage_report_markdown(report: dict[str, Any]) -> str:
    """Render a compact Markdown coverage report."""

    lines = [
        "# Coverage Report",
        "",
        f"Status: `{report.get('status', 'unknown')}`",
        "",
        "## Facet Coverage",
        "",
        f"- Required facets: {_join(report.get('required_facets'))}",
        f"- Covered facets: {_join(report.get('covered_facets'))}",
        f"- Missing facets: {_join(report.get('missing_facets'))}",
        "",
        "## Research Questions",
        "",
    ]
    for row in report.get("questions", []):
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- `{row.get('question_id')}` [{row.get('facet')}]: "
            f"{row.get('status')} ({row.get('evidence_count')} kept evidence item(s))"
        )
    lines.extend(["", "## Follow-Up Queries", ""])
    follow_ups = report.get("follow_up_queries", [])
    if isinstance(follow_ups, list) and follow_ups:
        for item in follow_ups:
            if isinstance(item, dict):
                lines.append(f"- [{item.get('facet')}] `{item.get('query')}`")
    else:
        lines.append("- No follow-up query recommended within the current budget.")
    lines.extend(["", "## Retrieval Summary", ""])
    retrieval = report.get("retrieval") if isinstance(report.get("retrieval"), dict) else {}
    selection = report.get("retrieval_selection") if isinstance(report.get("retrieval_selection"), dict) else {}
    lines.extend(
        [
            f"- Executed rounds: {retrieval.get('executed_rounds', 0)} / {retrieval.get('planned_rounds', 0)}",
            f"- Source attempts: {retrieval.get('attempt_count', 0)}",
            f"- Selected retrieval candidates: {selection.get('kept_documents', 0)} / {selection.get('max_documents', 0)}",
            f"- Candidate rows: {selection.get('candidate_rows', 0)}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _required_facets(questions: list[ResearchQuestion], query_plan: QueryPlan) -> list[str]:
    facets: list[str] = []
    for question in questions:
        if question.required and question.facet:
            facets.append(question.facet)
    facets.extend(query_plan.required_facets)
    return _unique(facet for facet in facets if facet)


def _question_coverage_row(question: ResearchQuestion, kept_rows: list[dict[str, Any]]) -> dict[str, Any]:
    supporting = _unique(
        str(row.get("paper_id") or "")
        for row in kept_rows
        if row.get("facet") == question.facet and row.get("paper_id")
    )
    return {
        "question_id": question.question_id,
        "facet": question.facet,
        "required": question.required,
        "status": "covered" if supporting else "missing",
        "evidence_count": len(supporting),
        "supporting_papers": supporting[:5],
        "question": question.question,
}


def _is_evidence_row(row: dict[str, Any], kept_paper_ids: set[str]) -> bool:
    if int(row.get("relevance_score") or 0) <= 0:
        return False
    if row.get("decision") == "keep":
        return True
    return row.get("reason") == "duplicate_lower_score" and str(row.get("paper_id") or "") in kept_paper_ids


def _follow_up_queries(
    *,
    topic: str,
    missing_facets: list[str],
    query_plan: QueryPlan,
    executed_queries: list[str],
    limit: int,
) -> list[dict[str, str]]:
    if limit <= 0:
        return []
    executed = {query.lower() for query in executed_queries}
    follow_ups: list[dict[str, str]] = []
    for facet in missing_facets:
        query = _planned_query_for_facet(query_plan, facet, executed)
        reason = "planned_query"
        if not query:
            query = _fallback_query(topic, facet)
            reason = "generated_from_missing_facet"
        key = query.lower()
        if key in executed or any(item["query"].lower() == key for item in follow_ups):
            continue
        follow_ups.append({"query": query, "facet": facet, "reason": reason})
        if len(follow_ups) >= limit:
            break
    return follow_ups


def _planned_query_for_facet(query_plan: QueryPlan, facet: str, executed: set[str]) -> str:
    for spec in query_plan.query_specs:
        if not isinstance(spec, dict):
            continue
        query = str(spec.get("query") or "").strip()
        if query and str(spec.get("facet") or "").strip() == facet and query.lower() not in executed:
            return query
    return ""


def _fallback_query(topic: str, facet: str) -> str:
    facet_terms = {
        "method": "method architecture system design",
        "benchmark": "benchmark evaluation metric",
        "dataset": "dataset task benchmark",
        "code_link": "github code implementation repository",
        "limitation": "limitation challenge failure",
        "overview": "survey overview landscape",
    }
    terms = _terms(topic)[:5] + _terms(facet_terms.get(facet, facet))[:4]
    return " ".join(_unique(terms)[:8]) or topic


def _coverage_status(*, required_facets: list[str], missing_facets: list[str], kept_count: int) -> str:
    if not kept_count:
        return "insufficient"
    if not missing_facets:
        return "covered"
    if len(missing_facets) < len(required_facets):
        return "partial"
    return "insufficient"


def _executed_queries(retrieval_rows: list[dict[str, Any]]) -> list[str]:
    return _unique(str(row.get("query") or "").strip() for row in retrieval_rows if row.get("query"))


def _terms(text: str) -> list[str]:
    stopwords = {"a", "an", "and", "are", "as", "at", "by", "for", "from", "in", "is", "of", "on", "or", "the", "to", "with"}
    return [
        word
        for word in re.findall(r"[a-z0-9][a-z0-9_+-]{1,}", text.lower())
        if word not in stopwords
    ]


def _unique(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _join(value: object) -> str:
    if not isinstance(value, list) or not value:
        return "`none`"
    return ", ".join(f"`{item}`" for item in value)
