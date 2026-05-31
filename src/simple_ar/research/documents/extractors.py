from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from simple_ar.research.contracts import DocumentRecord, SourcePlan


EXTRACTION_SCHEMA_VERSION = "fulltext_extraction.v1"
TEXT_SUFFIXES = {".md", ".txt"}
MOJIBAKE_SEGMENT_PATTERN = re.compile(r"鈥[\u4e00-\u9fff]")


def apply_fulltext_extraction(
    *,
    records: list[DocumentRecord],
    fulltext_manifest: dict[str, Any],
    source_plan: SourcePlan,
    extraction_dir: Path,
) -> tuple[list[DocumentRecord], dict[str, Any]]:
    """Parse cached/local full-text hints into document records.

    Args:
        records: Document records built from paper metadata and local inputs.
        fulltext_manifest: Output of ``build_fulltext_manifest``. Only cached
            hints are parsed; blocked or failed fetches remain manifest rows.
        source_plan: Research source plan controlling parser intent and budget.
        extraction_dir: Directory for extracted text when the source is not
            already plain text.

    Returns:
        A pair of ``(updated_records, extraction_manifest)``. Parsing failures
        are recorded in the manifest and do not fail the search stage.
    """
    if not bool(fulltext_manifest.get("enabled", False)):
        return records, _manifest(
            enabled=False,
            source_plan=source_plan,
            rows=[],
            notes=["Full-text extraction is disabled by research_use_fulltext/use_fulltext."],
        )

    docs_by_id = {
        str(row.get("document_id", "")): row
        for row in _list_of_dicts(fulltext_manifest.get("documents"))
    }
    updated: list[DocumentRecord] = []
    rows: list[dict[str, Any]] = []
    extraction_dir.mkdir(parents=True, exist_ok=True)
    for record in records:
        hint = _first_cached_hint(docs_by_id.get(record.document_id, {}))
        if hint is None:
            updated.append(record)
            rows.append(_row(record, status="skipped", reason="no_cached_fulltext"))
            continue
        parsed_record, row = _parse_hint(
            record=record,
            hint=hint,
            source_plan=source_plan,
            extraction_dir=extraction_dir,
        )
        updated.append(parsed_record)
        rows.append(row)

    return updated, _manifest(enabled=True, source_plan=source_plan, rows=rows, notes=[])


def _parse_hint(
    *,
    record: DocumentRecord,
    hint: dict[str, Any],
    source_plan: SourcePlan,
    extraction_dir: Path,
) -> tuple[DocumentRecord, dict[str, Any]]:
    source_path = Path(str(hint.get("local_path") or ""))
    if not source_path.is_file():
        return record, _row(record, status="failed", reason="cached_path_missing", source_path=source_path)

    try:
        text, parser = _extract_text(source_path, source_plan=source_plan)
    except Exception as exc:  # pragma: no cover - parser backend behavior varies by environment.
        return record, _row(
            record,
            status="failed",
            reason=str(exc)[:300] or "extraction_failed",
            source_path=source_path,
            hint=hint,
        )

    text = _normalize_text(text)
    if not text.strip():
        return record, _row(record, status="failed", reason="empty_extraction", source_path=source_path, hint=hint)

    text_path = source_path if source_path.suffix.lower() in TEXT_SUFFIXES else _write_extracted_text(
        extraction_dir=extraction_dir,
        record=record,
        text=text,
    )
    metadata = dict(record.metadata)
    metadata["fulltext_extraction"] = {
        "status": "parsed",
        "parser": parser,
        "source_path": str(source_path),
        "extracted_text_path": str(text_path),
        "hint_kind": str(hint.get("kind") or ""),
        "chars": len(text),
    }
    parsed_record = replace(
        record,
        abstract=_abstract(text) or record.abstract,
        local_path=str(text_path),
        content_hash=_sha256(text_path),
        extraction_status="parsed",
        parser=parser,
        metadata=metadata,
    )
    return parsed_record, _row(
        parsed_record,
        status="parsed",
        reason="ok",
        source_path=source_path,
        extracted_text_path=text_path,
        hint=hint,
        chars=len(text),
        parser=parser,
    )


def _extract_text(path: Path, *, source_plan: SourcePlan) -> tuple[str, str]:
    parser_backend = str(source_plan.budget.get("parser_backend") or "basic").strip().lower()
    if parser_backend == "unstructured":
        return _read_unstructured(path), "unstructured"

    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return _read_text(path), "plain_text"
    if suffix in {".html", ".htm"}:
        return _html_to_text(_read_text(path)), "basic_html"
    if suffix == ".pdf":
        return _read_pdf(path, max_pages=_positive_int(source_plan.budget.get("max_pdf_pages"), default=20)), "pypdf_optional"
    raise RuntimeError(f"unsupported_fulltext_suffix:{suffix or 'none'}")


def _read_pdf(path: Path, *, max_pages: int) -> str:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError("pypdf is not installed; install it or keep PDF parsing disabled") from exc
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages[:max_pages]:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _read_unstructured(path: Path) -> str:
    try:
        from unstructured.partition.auto import partition  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "unstructured is not installed; install the optional document parser or use parser_backend='basic'/'pypdf'"
        ) from exc
    elements = partition(filename=str(path))
    return "\n\n".join(str(element).strip() for element in elements if str(element).strip())


class _HTMLTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            stripped = data.strip()
            if stripped:
                self.parts.append(stripped)


def _html_to_text(text: str) -> str:
    parser = _HTMLTextParser()
    parser.feed(text)
    return "\n".join(parser.parts)


def _write_extracted_text(*, extraction_dir: Path, record: DocumentRecord, text: str) -> Path:
    path = extraction_dir / f"{_safe_name(record.document_id)}.txt"
    path.write_text(_normalize_text(text), encoding="utf-8")
    return path


def _first_cached_hint(row: dict[str, Any]) -> dict[str, Any] | None:
    for hint in _list_of_dicts(row.get("hints")):
        if hint.get("status") == "cached" and hint.get("local_path"):
            return hint
    return None


def _manifest(
    *,
    enabled: bool,
    source_plan: SourcePlan,
    rows: list[dict[str, Any]],
    notes: list[str],
) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "schema_version": EXTRACTION_SCHEMA_VERSION,
        "enabled": enabled,
        "parser_backend": str(source_plan.budget.get("parser_backend") or "basic"),
        "document_count": len(rows),
        "parsed_count": status_counts.get("parsed", 0),
        "status_counts": dict(sorted(status_counts.items())),
        "documents": rows,
        "notes": notes
        or [
            "Only cached or local full-text hints are parsed.",
            "PDF extraction uses optional pypdf when available; failures are recorded without failing the run.",
        ],
    }


def _row(
    record: DocumentRecord,
    *,
    status: str,
    reason: str,
    source_path: Path | None = None,
    extracted_text_path: Path | None = None,
    hint: dict[str, Any] | None = None,
    chars: int | None = None,
    parser: str | None = None,
) -> dict[str, Any]:
    return {
        "document_id": record.document_id,
        "title": record.title,
        "status": status,
        "reason": reason,
        "parser": parser or record.parser or "",
        "hint_kind": str((hint or {}).get("kind") or ""),
        "source_path": str(source_path) if source_path else None,
        "extracted_text_path": str(extracted_text_path) if extracted_text_path else None,
        "chars": chars,
    }


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _normalize_text(text: str) -> str:
    repaired = _repair_common_mojibake(text)
    return re.sub(r"\n{3,}", "\n\n", repaired.replace("\r\n", "\n").replace("\r", "\n")).strip() + "\n"


def _repair_common_mojibake(text: str) -> str:
    """Repair common UTF-8-as-GBK artifacts from lightweight PDF extraction."""
    if "鈥" not in text:
        return text

    def _repair_segment(match: re.Match[str]) -> str:
        segment = match.group(0)
        try:
            return segment.encode("gb18030").decode("utf-8")
        except UnicodeError:
            return segment

    repaired = MOJIBAKE_SEGMENT_PATTERN.sub(_repair_segment, text)
    repaired = re.sub(r"(?<=[A-Za-z])鈥\?(?=[A-Za-z])", "’", repaired)
    repaired = repaired.replace("鈥?", "”").replace("鈥�", "”")
    return repaired


def _abstract(text: str, *, limit: int = 1200) -> str:
    return " ".join(text.split())[:limit]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return safe[:120] or "document"


def _positive_int(value: object, *, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default
