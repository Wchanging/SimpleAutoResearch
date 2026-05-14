from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from simple_ar.literature.models import Paper


def build_report_quality(
    report: str,
    report_body: str,
    search_meta: dict[str, Any],
    results: dict[str, Any],
    papers: list[Paper],
    cited_papers: list[Paper],
) -> dict[str, Any]:
    """Create lightweight checks for the generated report package.

    Args:
        report: Final report Markdown, including generated references.
        report_body: Report Markdown before the generated References section.
        search_meta: Literature-search metadata loaded from ``search_meta.json``.
        results: Experiment execution result loaded from ``results.json``.
        papers: Papers loaded from ``papers.jsonl``.
        cited_papers: Papers cited in the report body.

    Returns:
        JSON-serializable provenance and reproducibility checks. The checks are
        intentionally rule-based; they verify traceability signals rather than
        judging scientific correctness.
    """
    checks = [
        _quality_check(
            "citations_known",
            "passed",
            "Report citations were validated against papers.jsonl before writing.",
        ),
        _quality_check(
            "body_citations_present",
            "passed" if not papers or cited_papers else "failed",
            (
                "At least one known paper is cited in the report body when paper "
                "metadata exists."
            ),
        ),
        _quality_check(
            "references_pruned",
            "passed" if len(cited_papers) <= len(papers) else "failed",
            "references.bib is generated from the body-cited subset of papers.jsonl.",
        ),
        _quality_check(
            "result_table_from_results_json",
            _result_table_status(report, results),
            (
                "When parsed metrics exist in results.json, those values are visible "
                "in the report results section."
            ),
        ),
        _quality_check(
            "runtime_limits_visible",
            _runtime_limit_status(report, search_meta, results),
            "Fallback, cache, non-zero return, and timeout limits are disclosed when present.",
        ),
    ]
    status = "passed" if all(item["status"] == "passed" for item in checks) else "warning"
    return {
        "schema_version": 1,
        "generated_at": _utcnow_iso(),
        "status": status,
        "checks": checks,
        "summary": {
            "paper_count": len(papers),
            "body_cited_paper_count": len(cited_papers),
            "metric_count": (
                len(results.get("metrics", {}))
                if isinstance(results.get("metrics"), dict)
                else 0
            ),
            "search_source": search_meta.get("source"),
            "search_status": search_meta.get("status"),
            "returncode": results.get("returncode"),
            "timed_out": results.get("timed_out"),
        },
        "notes": [
            "This file checks provenance and reproducibility signals, not scientific correctness.",
            "A passed status means the generated report is internally traceable to known artifacts.",
        ],
        "body_citation_ids": sorted(
            _body_citation_ids(report_body, {paper.id for paper in papers})
        ),
        "cited_paper_ids": [paper.id for paper in cited_papers],
    }


def _quality_check(name: str, status: str, message: str) -> dict[str, str]:
    return {"name": name, "status": status, "message": message}


def _result_table_status(report: str, results: dict[str, Any]) -> str:
    metrics = results.get("metrics", {})
    if not isinstance(metrics, dict) or not metrics:
        return "passed"
    lower = report.lower()
    for key, value in metrics.items():
        if str(key).lower() not in lower:
            return "warning"
        if _format_metric(value) not in report and str(value) not in report:
            return "warning"
    return "passed"


def _runtime_limit_status(
    report: str,
    search_meta: dict[str, Any],
    results: dict[str, Any],
) -> str:
    lower = report.lower()
    needs: list[tuple[bool, tuple[str, ...]]] = [
        (
            search_meta.get("status") in {"fallback", "fixture_fallback", "offline_fixture"}
            or search_meta.get("source") == "fixture",
            ("fallback", "fixture"),
        ),
        (
            str(search_meta.get("status", "")).startswith("cache")
            or "cache" in str(search_meta.get("source", "")).lower(),
            ("cache", "cached"),
        ),
        (results.get("timed_out") is True, ("timed out", "timeout")),
        (results.get("returncode") not in {0, "0", None}, ("non-zero", "did not exit cleanly")),
    ]
    for active, accepted_terms in needs:
        if active and not any(term in lower for term in accepted_terms):
            return "warning"
    return "passed"


def _body_citation_ids(markdown: str, allowed_ids: set[str]) -> set[str]:
    body = _strip_references_section(markdown)
    found = set(re.findall(r"@([A-Za-z0-9_.:-]+)", body))
    return found & allowed_ids


def _strip_references_section(markdown: str) -> str:
    lines = markdown.strip().splitlines()
    kept: list[str] = []
    for line in lines:
        if line.strip().lower().lstrip("#").strip() == "references":
            break
        kept.append(line)
    return "\n".join(kept).strip() + "\n"


def _format_metric(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
