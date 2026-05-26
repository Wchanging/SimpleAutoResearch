from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from simple_ar.literature.models import Paper
from simple_ar.research.contracts import DocumentRecord, FulltextHint, SourcePlan


PDF_SUFFIX = ".pdf"
TEXT_SUFFIXES = {".md", ".txt"}
HTML_SUFFIXES = {".html", ".htm"}


def fulltext_hints_for_paper(paper: Paper, *, document_id: str) -> list[FulltextHint]:
    """Return non-destructive full-text hints derived from paper metadata.

    Args:
        paper: Normalized metadata row from a source connector.
        document_id: Document id that will own the hint.

    Returns:
        Candidate full-text resources. The function only infers hints; it does
        not fetch remote content.
    """
    hints: list[FulltextHint] = []
    arxiv_id = _arxiv_id(paper.source_id or "") or _arxiv_id(paper.url)
    if paper.source == "arxiv" and arxiv_id:
        hints.append(
            FulltextHint(
                document_id=document_id,
                kind="pdf",
                source="arxiv",
                url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                access="open",
                reason="arxiv_pdf_url",
            )
        )
    if paper.fulltext_url:
        hints.append(_hint_from_url(document_id=document_id, url=paper.fulltext_url, source=paper.source, reason="provider_fulltext_url"))
    elif paper.url and not (paper.source == "arxiv" and arxiv_id):
        hints.append(_hint_from_url(document_id=document_id, url=paper.url, source=paper.source, reason="metadata_url"))
    return _deduplicate_hints(hints)


def build_fulltext_manifest(
    *,
    records: list[DocumentRecord],
    source_plan: SourcePlan,
) -> dict[str, Any]:
    """Build the Day9 full-text hint and fetch-budget manifest.

    Args:
        records: Search document records.
        source_plan: Search source plan with full-text intent and budgets.

    Returns:
        A JSON-friendly manifest describing full-text hints, selected candidates,
        and skipped reasons. Remote content is not downloaded in Day9.
    """
    max_documents = _positive_int(source_plan.budget.get("max_fulltext_documents"), default=0)
    max_pdf_bytes = _positive_int(source_plan.budget.get("max_pdf_mb"), default=0) * 1024 * 1024
    keep_raw_pdf = bool(source_plan.budget.get("keep_raw_pdf")) if isinstance(source_plan.budget.get("keep_raw_pdf"), bool) else False
    parser_backend = str(source_plan.budget.get("parser_backend") or "basic")

    rows: list[dict[str, Any]] = []
    selected_count = 0
    hint_count = 0
    for record in records:
        hints = _hints_for_record(record)
        hint_count += len(hints)
        selected_for_fetch = False
        row_hints: list[dict[str, Any]] = []
        for hint in hints:
            planned = _plan_hint(
                hint,
                require_fulltext=source_plan.require_fulltext,
                allow_pdf_download=source_plan.allow_pdf_download,
                selected_count=selected_count,
                max_documents=max_documents,
                max_pdf_bytes=max_pdf_bytes,
            )
            if planned.status == "selected":
                selected_count += 1
                selected_for_fetch = True
            row_hints.append(planned.to_row())
        rows.append(
            {
                "document_id": record.document_id,
                "title": record.title,
                "source": record.source,
                "extraction_status": record.extraction_status,
                "selected_for_fetch": selected_for_fetch,
                "hints": row_hints,
            }
        )

    status_counts = Counter(
        str(hint.get("status", "unknown"))
        for row in rows
        for hint in row.get("hints", [])
        if isinstance(hint, dict)
    )
    return {
        "schema_version": "research_fulltext_manifest.v1",
        "enabled": source_plan.require_fulltext,
        "allow_pdf_download": source_plan.allow_pdf_download,
        "budget": {
            "max_fulltext_documents": max_documents,
            "max_pdf_mb": _positive_int(source_plan.budget.get("max_pdf_mb"), default=0),
            "keep_raw_pdf": keep_raw_pdf,
            "parser_backend": parser_backend,
        },
        "document_count": len(records),
        "hint_count": hint_count,
        "selected_count": selected_count,
        "status_counts": dict(sorted(status_counts.items())),
        "documents": rows,
        "notes": [
            "Day9 records full-text hints and budget decisions only; remote downloads are implemented later.",
            "Remote PDFs are selected only when both use_fulltext and allow_pdf_download are enabled.",
            "Local files can be used as full-text inputs without network access.",
        ],
    }


def _hints_for_record(record: DocumentRecord) -> list[FulltextHint]:
    hints: list[FulltextHint] = []
    if record.local_path:
        local_hint = _hint_from_local_path(record)
        if local_hint:
            hints.append(local_hint)
    raw_hints = record.metadata.get("fulltext_hints")
    if isinstance(raw_hints, list):
        for row in raw_hints:
            if isinstance(row, dict):
                hint = _hint_from_row(record.document_id, row)
                if hint:
                    hints.append(hint)
    elif record.url and not record.local_path:
        hints.append(_hint_from_url(document_id=record.document_id, url=record.url, source=record.source, reason="document_url"))
    return _deduplicate_hints(hints)


def _plan_hint(
    hint: FulltextHint,
    *,
    require_fulltext: bool,
    allow_pdf_download: bool,
    selected_count: int,
    max_documents: int,
    max_pdf_bytes: int,
) -> FulltextHint:
    if hint.local_path:
        if not require_fulltext:
            return _replace_hint(hint, status="hint_only", reason="fulltext_disabled")
        if hint.kind == "pdf" and max_pdf_bytes and hint.size_bytes and hint.size_bytes > max_pdf_bytes:
            return _replace_hint(hint, status="skipped", reason="local_pdf_exceeds_max_pdf_mb")
        return _replace_hint(hint, status="selected", reason="local_fulltext_available")
    if not require_fulltext:
        return _replace_hint(hint, status="hint_only", reason="fulltext_disabled")
    if hint.kind == "pdf" and not allow_pdf_download:
        return _replace_hint(hint, status="blocked", reason="pdf_download_disabled")
    if max_documents and selected_count >= max_documents:
        return _replace_hint(hint, status="skipped", reason="max_fulltext_documents_reached")
    if hint.kind in {"pdf", "html", "text"}:
        return _replace_hint(hint, status="selected", reason="within_fulltext_budget")
    return _replace_hint(hint, status="hint_only", reason="not_fetchable_by_day9")


def _hint_from_row(document_id: str, row: dict[str, Any]) -> FulltextHint | None:
    url = str(row.get("url") or "").strip() or None
    local_path = str(row.get("local_path") or "").strip() or None
    if not url and not local_path:
        return None
    return FulltextHint(
        document_id=document_id,
        kind=str(row.get("kind") or _kind_from_url(url or local_path or "")),
        source=str(row.get("source") or "metadata"),
        url=url,
        local_path=local_path,
        access=str(row.get("access") or "unknown"),
        status=str(row.get("status") or "hint_only"),
        reason=str(row.get("reason") or ""),
        size_bytes=row.get("size_bytes") if isinstance(row.get("size_bytes"), int) else None,
    )


def _hint_from_url(*, document_id: str, url: str, source: str, reason: str) -> FulltextHint:
    kind = _kind_from_url(url)
    access = "open" if kind in {"pdf", "html", "text"} and url.startswith(("http://", "https://")) else "unknown"
    return FulltextHint(
        document_id=document_id,
        kind=kind,
        source=source,
        url=url,
        access=access,
        reason=reason,
    )


def _hint_from_local_path(record: DocumentRecord) -> FulltextHint | None:
    if not record.local_path:
        return None
    path = Path(record.local_path)
    suffix = path.suffix.lower()
    if suffix == PDF_SUFFIX:
        kind = "pdf"
    elif suffix in TEXT_SUFFIXES:
        kind = "text"
    elif suffix in HTML_SUFFIXES:
        kind = "html"
    else:
        return None
    return FulltextHint(
        document_id=record.document_id,
        kind=kind,
        source="local_files",
        local_path=str(path),
        access="local",
        reason="local_file",
        size_bytes=path.stat().st_size if path.exists() and path.is_file() else None,
    )


def _replace_hint(hint: FulltextHint, *, status: str, reason: str) -> FulltextHint:
    return FulltextHint(
        document_id=hint.document_id,
        kind=hint.kind,
        source=hint.source,
        url=hint.url,
        local_path=hint.local_path,
        access=hint.access,
        status=status,
        reason=reason,
        size_bytes=hint.size_bytes,
    )


def _kind_from_url(url: str) -> str:
    lower = url.lower()
    parsed = urlparse(lower)
    path = parsed.path or lower
    if path.endswith(PDF_SUFFIX) or "/pdf/" in path:
        return "pdf"
    if any(path.endswith(suffix) for suffix in TEXT_SUFFIXES):
        return "text"
    if any(path.endswith(suffix) for suffix in HTML_SUFFIXES):
        return "html"
    if parsed.scheme in {"http", "https"}:
        return "landing"
    return "unknown"


def _arxiv_id(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        return ""
    for marker in ("/abs/", "/pdf/"):
        if marker in value:
            value = value.split(marker, maxsplit=1)[1]
            break
    value = value.removesuffix(".pdf")
    if re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", value):
        return value
    if re.match(r"^[a-z-]+(\.[A-Z]{2})?/\d{7}(v\d+)?$", value):
        return value
    return ""


def _deduplicate_hints(hints: list[FulltextHint]) -> list[FulltextHint]:
    seen: set[tuple[str, str, str | None, str | None]] = set()
    unique: list[FulltextHint] = []
    for hint in hints:
        key = (hint.document_id, hint.kind, hint.url, hint.local_path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(hint)
    return unique


def _positive_int(value: object, *, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default
