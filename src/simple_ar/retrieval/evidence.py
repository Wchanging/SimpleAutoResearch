from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from simple_ar.artifacts import append_jsonl, read_json, read_jsonl, write_json
from simple_ar.retrieval.chunking import build_artifact_chunks
from simple_ar.retrieval.index import build_artifact_index
from simple_ar.retrieval.search import search_chunks


SOURCE_PLAN_FILE = "source_plan.json"
ACTIVITY_LOG_FILE = "activity_log.jsonl"
EVIDENCE_LEDGER_FILE = "evidence_ledger.jsonl"


DEFAULT_STAGE_QUERIES: dict[str, list[str]] = {
    "read": [
        "paper metadata title abstract literature",
        "search provenance source fallback cache arxiv",
    ],
    "synthesize": [
        "literature notes problem method limitation relevance",
        "hypothesis research theme limitation",
    ],
    "report": [
        "results metrics accuracy precision recall",
        "hypothesis synthesis literature notes",
        "search provenance fallback cache arxiv",
        "experiment plan template dataset baseline method",
    ],
}


def ensure_source_plan(run_dir: Path, topic: str) -> dict[str, Any]:
    """Create or load the local source plan for a run.

    Args:
        run_dir: Root run directory.
        topic: Original research topic.

    Returns:
        JSON-serializable source plan. Existing plans are preserved so a human
        can edit the retrieval scope later.
    """
    root = Path(run_dir)
    path = root / SOURCE_PLAN_FILE
    if path.exists():
        data = read_json(path)
        if isinstance(data, dict):
            return data

    plan = {
        "schema_version": 1,
        "generated_at": _utcnow_iso(),
        "topic": topic,
        "retrieval_mode": "lexical",
        "source_scope": {
            "local_run_artifacts": True,
            "web_browsing": False,
            "full_paper_downloads": False,
            "notes": (
                "V2 starts with local staged artifacts, metadata, snippets, and "
                "small indexes. It does not store full PDFs by default."
            ),
        },
        "stages": {
            "read": {
                "objective": "Ground paper notes in search metadata and paper rows.",
                "queries": DEFAULT_STAGE_QUERIES["read"],
                "expected_sources": ["02-search/papers.jsonl", "02-search/search_meta.json"],
            },
            "synthesize": {
                "objective": "Ground themes and hypotheses in generated literature notes.",
                "queries": DEFAULT_STAGE_QUERIES["synthesize"],
                "expected_sources": ["03-read/notes.md", "03-read/paper_notes.json"],
            },
            "report": {
                "objective": "Ground the final report in evidence, metrics, and provenance.",
                "queries": DEFAULT_STAGE_QUERIES["report"],
                "expected_sources": [
                    "02-search/papers.jsonl",
                    "02-search/search_meta.json",
                    "03-read/notes.md",
                    "04-synthesize/synthesis.md",
                    "04-synthesize/hypothesis.md",
                    "05-design/experiment_plan.json",
                    "07-run/results.json",
                ],
            },
        },
    }
    write_json(path, plan)
    log_activity(
        root,
        "source_plan_created",
        "Created deterministic local source plan.",
        stage=None,
        source_plan=SOURCE_PLAN_FILE,
    )
    return plan


def collect_stage_evidence(
    run_dir: Path,
    topic: str,
    stage: str,
    *,
    queries: list[str] | None = None,
    top_k: int = 4,
) -> list[dict[str, Any]]:
    """Retrieve and record source snippets used by a pipeline stage.

    Args:
        run_dir: Root run directory.
        topic: Original research topic.
        stage: Logical stage name, such as ``read`` or ``report``.
        queries: Optional override query list. When omitted, the source plan's
            stage queries are used.
        top_k: Maximum matches per query.

    Returns:
        Evidence rows that were selected for the stage. Rows are also written to
        ``evidence_ledger.jsonl`` with stable identifiers.
    """
    root = Path(run_dir)
    plan = ensure_source_plan(root, topic)
    stage_plan = _stage_plan(plan, stage)
    stage_queries = queries or list(stage_plan.get("queries", [])) or DEFAULT_STAGE_QUERIES.get(stage, [])
    limit = max(1, int(top_k))
    log_activity(
        root,
        "retrieval_search_started",
        "Searching local artifacts for stage evidence.",
        stage=stage,
        queries=stage_queries,
        top_k=limit,
    )

    index = build_artifact_index(root, write=True)
    chunks = build_artifact_chunks(root, index=index, write=True)
    expected_sources = [
        str(item)
        for item in stage_plan.get("expected_sources", [])
        if isinstance(item, str)
    ]
    scoped_chunks = _scope_chunks(chunks, expected_sources)
    rows: list[dict[str, Any]] = []
    seen_sources: set[tuple[str, int, int, str]] = set()
    for query in stage_queries:
        for match in search_chunks(scoped_chunks, query, top_k=limit):
            source_key = (
                str(match["path"]),
                int(match["line_start"]),
                int(match["line_end"]),
                query,
            )
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            rows.append(_evidence_row(stage, query, match))

    appended = append_unique_evidence(root, rows)
    log_activity(
        root,
        "evidence_recorded",
        "Recorded source snippets for stage.",
        stage=stage,
        selected=len(rows),
        appended=appended,
        expected_sources=expected_sources,
    )
    return rows


def append_unique_evidence(run_dir: Path, rows: list[dict[str, Any]]) -> int:
    """Append evidence rows that are not already present in the ledger."""
    root = Path(run_dir)
    ledger = root / EVIDENCE_LEDGER_FILE
    existing_ids: set[str] = set()
    if ledger.exists():
        existing_ids = {
            str(row.get("evidence_id"))
            for row in read_jsonl(ledger)
            if isinstance(row.get("evidence_id"), str)
        }
    appended = 0
    for row in rows:
        evidence_id = str(row.get("evidence_id", ""))
        if not evidence_id or evidence_id in existing_ids:
            continue
        append_jsonl(ledger, row)
        existing_ids.add(evidence_id)
        appended += 1
    return appended


def log_activity(
    run_dir: Path,
    event: str,
    message: str,
    *,
    stage: str | None,
    **data: Any,
) -> None:
    """Append one structured activity log entry for retrieval work."""
    append_jsonl(
        Path(run_dir) / ACTIVITY_LOG_FILE,
        {
            "timestamp": _utcnow_iso(),
            "event": event,
            "stage": stage,
            "message": message,
            "data": data,
        },
    )


def format_evidence_snippets(rows: list[dict[str, Any]], *, max_rows: int = 10) -> str:
    """Render evidence rows into compact prompt context with provenance."""
    if not rows:
        return ""
    lines: list[str] = []
    for row in rows[:max_rows]:
        source = f"{row['source_path']}:{row['line_start']}-{row['line_end']}"
        lines.append(f"[{row['evidence_id']} | {source} | query={row['query']}]")
        lines.append(str(row["quote_or_summary"]).strip())
        lines.append("")
    return "\n".join(lines).strip()


def _stage_plan(plan: dict[str, Any], stage: str) -> dict[str, Any]:
    stages = plan.get("stages")
    if not isinstance(stages, dict):
        return {}
    value = stages.get(stage)
    return value if isinstance(value, dict) else {}


def _scope_chunks(chunks: list[Any], expected_sources: list[str]) -> list[Any]:
    """Prefer chunks from planned sources, falling back to all chunks if absent."""
    if not expected_sources:
        return chunks
    scoped = [
        chunk
        for chunk in chunks
        if any(_matches_expected_source(chunk.path, expected) for expected in expected_sources)
    ]
    return scoped or chunks


def _matches_expected_source(path: str, expected: str) -> bool:
    normalized_path = path.replace("\\", "/")
    normalized_expected = expected.replace("\\", "/").strip("/")
    return normalized_path == normalized_expected or normalized_path.startswith(
        normalized_expected.rstrip("/") + "/"
    )


def _evidence_row(stage: str, query: str, match: dict[str, Any]) -> dict[str, Any]:
    source_path = str(match["path"])
    line_start = int(match["line_start"])
    line_end = int(match["line_end"])
    snippet = str(match.get("snippet", "")).strip()
    evidence_id = _evidence_id(stage, query, source_path, line_start, line_end, snippet)
    return {
        "schema_version": 1,
        "evidence_id": evidence_id,
        "claim_id": f"{stage}:{_slug(query)}",
        "query": query,
        "source_path": source_path,
        "line_start": line_start,
        "line_end": line_end,
        "quote_or_summary": snippet,
        "used_by_stage": stage,
        "confidence": "medium",
        "retrieval_mode": "lexical",
        "score": match.get("score"),
        "recorded_at": _utcnow_iso(),
    }


def _evidence_id(stage: str, query: str, path: str, start: int, end: int, snippet: str) -> str:
    raw = f"{stage}\0{query}\0{path}\0{start}\0{end}\0{snippet}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"ev-{digest}"


def _slug(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in text).strip("-")
    parts = [part for part in cleaned.split("-") if part]
    return "-".join(parts[:6]) or "evidence"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
