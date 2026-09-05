"""Bounded semantic screening for retrieved research documents.

This module owns the small amount of model-assisted Read policy that is useful
to both the canonical research session and the frozen eight-stage facade:
coarse relevance screening, focused reranking, shortlist backfilling, and
structured paper notes.  It does not read files or persist artifacts.  The
caller supplies plain paper rows and decides how the decisions are projected
into its own handoff.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from simple_ar.integrations.llm import LLMRequest
from simple_ar.research.prompts import (
    READ_SYSTEM,
    paper_note_user_prompt,
    read_coarse_screening_user_prompt,
    read_rerank_user_prompt,
)


EmitMessage = Callable[[str], None]


def screen_papers_with_llm(
    client: Any,
    *,
    topic: str,
    problem_markdown: str,
    research_plan_json: str,
    papers: Sequence[Mapping[str, Any]],
    config: Mapping[str, object] | None = None,
    emit: EmitMessage | None = None,
) -> list[dict[str, Any]]:
    """Coarse-screen and rerank papers using bounded structured LLM calls.

    The two-pass policy preserves the useful behavior of the historical Read
    stage while keeping the transport and artifact policy outside this module.
    Invalid or unknown paper identifiers are ignored.  If reranking returns no
    usable rows, the coarse decisions are deterministically prioritized instead
    of widening the shortlist or silently selecting every paper.
    """

    settings = dict(config or {})
    paper_rows = [dict(row) for row in papers]
    if not paper_rows:
        return []

    max_shortlist = _screening_max_shortlist(settings, len(paper_rows))
    min_shortlist = _screening_min_shortlist(
        settings,
        len(paper_rows),
        max_shortlist,
    )
    required_facets = _required_facets(settings)
    coarse_decisions = _coarse_screen(
        client,
        topic=topic,
        problem_markdown=problem_markdown,
        research_plan_json=research_plan_json,
        papers=paper_rows,
        min_shortlist=min_shortlist,
        config=settings,
        emit=emit,
    )
    if not coarse_decisions:
        return []

    known_ids = {_paper_id(paper, index) for index, paper in enumerate(paper_rows, start=1)}
    valid_coarse = [
        row for row in coarse_decisions if str(row.get("paper_id") or "") in known_ids
    ]
    kept_ids = {
        str(row.get("paper_id") or "")
        for row in valid_coarse
        if _decision_value(row.get("decision")) == "keep"
    }
    if not kept_ids:
        return valid_coarse

    paper_by_id = {
        _paper_id(paper, index): paper
        for index, paper in enumerate(paper_rows, start=1)
    }
    kept_papers = [
        paper_by_id[paper_id]
        for paper_id in kept_ids
        if paper_id in paper_by_id
    ]
    rerank_input = _rerank_input_papers(
        kept_papers,
        valid_coarse,
        max_shortlist=max_shortlist,
    )
    reranked = _rerank(
        client,
        topic=topic,
        problem_markdown=problem_markdown,
        research_plan_json=research_plan_json,
        papers=rerank_input,
        coarse_decisions=valid_coarse,
        max_shortlist=max_shortlist,
        min_shortlist=min_shortlist,
        emit=emit,
    )
    if not reranked:
        return _coarse_decisions_with_priorities(
            valid_coarse,
            max_shortlist=max_shortlist,
        )
    return _merge_decisions(
        coarse_decisions=valid_coarse,
        rerank_decisions=reranked,
        reranked_ids={_paper_id(paper, index) for index, paper in enumerate(rerank_input, start=1)},
        max_shortlist=max_shortlist,
        min_shortlist=min_shortlist,
        required_facets=required_facets,
        papers_by_id=paper_by_id,
    )


def read_paper_notes_with_llm(
    client: Any,
    *,
    papers: Sequence[Mapping[str, Any]],
    evidence_snippets: str = "",
    config: Mapping[str, object] | None = None,
    emit: EmitMessage | None = None,
) -> list[dict[str, Any]]:
    """Create normalized, bounded paper notes for the selected papers."""

    settings = dict(config or {})
    paper_rows = [dict(row) for row in papers]
    requests = [
        LLMRequest(
            system=READ_SYSTEM,
            user=paper_note_user_prompt(
                json.dumps(
                    _paper_screening_record(paper, index),
                    ensure_ascii=False,
                ),
                evidence_snippets=evidence_snippets,
            ),
            label=_paper_id(paper, index),
        )
        for index, paper in enumerate(paper_rows, start=1)
    ]
    if not requests:
        return []
    workers = min(_llm_max_workers(settings), len(requests))
    _emit(
        emit,
        f"Calling LLM for {len(requests)} paper note(s) with {workers} worker(s).",
    )
    responses = client.ask_json_many(requests, max_workers=workers)
    return [
        _normalize_paper_note(paper, response, index)
        for index, (paper, response) in enumerate(
            zip(paper_rows, responses),
            start=1,
        )
    ]


def render_paper_notes_markdown(notes: Sequence[Mapping[str, Any]]) -> str:
    """Render normalized paper notes as inspectable Markdown."""

    lines = ["# Literature Notes", ""]
    for note in notes:
        paper_id = str(note.get("paper_id") or "unknown")
        lines.append(f"## {paper_id}")
        if note.get("title"):
            lines.append(f"Title: {note['title']}")
        if note.get("evidence_role"):
            lines.append(f"Role: {note['evidence_role']}")
        lines.extend(
            [
                f"- Summary: {note.get('one_sentence_summary') or 'Not specified.'}",
                f"- Problem: {note.get('problem') or 'Not specified.'}",
                f"- Method: {note.get('method') or 'Not specified.'}",
                f"- Datasets: {_join_inline(note.get('datasets'))}",
                f"- Metrics: {_join_inline(note.get('metrics'))}",
                f"- Key claims: {_join_inline(note.get('key_claims'))}",
                f"- Limitations: {_join_inline(note.get('limitations'))}",
                f"- Relation to topic: {note.get('relation_to_topic') or note.get('relevance') or 'Not specified.'}",
                f"- Synthesis hint: {note.get('synthesis_hint') or 'Not specified.'}",
                f"- Experiment hooks: {_join_inline(note.get('possible_experiment_hooks'))}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _coarse_screen(
    client: Any,
    *,
    topic: str,
    problem_markdown: str,
    research_plan_json: str,
    papers: list[dict[str, Any]],
    min_shortlist: int,
    config: Mapping[str, object],
    emit: EmitMessage | None,
) -> list[dict[str, Any]]:
    batches = _batched(papers, _screening_batch_size(config))
    if not batches:
        return []
    workers = min(_screening_workers(config), len(batches))
    _emit(
        emit,
        "Calling LLM for read-stage coarse screening "
        f"({len(batches)} batch(es), {workers} worker(s)).",
    )
    requests = [
        LLMRequest(
            READ_SYSTEM,
            read_coarse_screening_user_prompt(
                topic=topic,
                problem_markdown=problem_markdown,
                papers_json=json.dumps(
                    [
                        _paper_screening_record(paper, index)
                        for index, paper in enumerate(batch, start=1)
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                research_plan_json=research_plan_json,
                min_shortlist=min_shortlist,
            ),
            label=f"read-coarse-{batch_index:03d}",
        )
        for batch_index, batch in enumerate(batches, start=1)
    ]
    responses = client.ask_json_many(requests, max_workers=workers)
    decisions: list[dict[str, Any]] = []
    for response in responses:
        if not isinstance(response, Mapping):
            continue
        raw = response.get("decisions")
        if not isinstance(raw, list):
            continue
        for row in raw:
            if isinstance(row, Mapping):
                decisions.append(_normalize_coarse_decision(row))
    return decisions


def _rerank(
    client: Any,
    *,
    topic: str,
    problem_markdown: str,
    research_plan_json: str,
    papers: list[dict[str, Any]],
    coarse_decisions: list[dict[str, Any]],
    max_shortlist: int,
    min_shortlist: int,
    emit: EmitMessage | None,
) -> list[dict[str, Any]]:
    if not papers:
        return []
    _emit(
        emit,
        f"Calling LLM for read-stage reranking ({len(papers)} candidate paper(s)).",
    )
    response = client.ask_json(
        READ_SYSTEM,
        read_rerank_user_prompt(
            topic=topic,
            problem_markdown=problem_markdown,
            papers_json=json.dumps(
                [
                    _paper_screening_record(paper, index)
                    for index, paper in enumerate(papers, start=1)
                ],
                ensure_ascii=False,
                indent=2,
            ),
            research_plan_json=research_plan_json,
            coarse_decisions_json=json.dumps(
                coarse_decisions,
                ensure_ascii=False,
                indent=2,
            ),
            max_shortlist=max_shortlist,
            min_shortlist=min_shortlist,
        ),
        label="read-rerank",
    )
    if not isinstance(response, Mapping):
        return []
    raw = response.get("ranked_papers")
    if not isinstance(raw, list):
        raw = response.get("decisions")
    if not isinstance(raw, list):
        return []
    known_ids = {
        _paper_id(paper, index) for index, paper in enumerate(papers, start=1)
    }
    decisions: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        paper_id = str(row.get("paper_id") or "").strip()
        if paper_id not in known_ids:
            continue
        decisions.append(_normalize_rerank_decision(row))
    return decisions


def _screening_batch_size(config: Mapping[str, object]) -> int:
    return _bounded_config_int(
        config,
        ("read_screening_batch_size", "research_read_batch_size"),
        default=4,
        minimum=1,
        maximum=8,
    )


def _screening_workers(config: Mapping[str, object]) -> int:
    llm_workers = _llm_max_workers(config)
    return _bounded_config_int(
        config,
        ("read_screening_workers", "research_read_workers"),
        default=min(3, llm_workers),
        minimum=1,
        maximum=llm_workers,
    )


def _screening_max_shortlist(config: Mapping[str, object], paper_count: int) -> int:
    default = paper_count if paper_count <= 24 else 24
    return _bounded_config_int(
        config,
        ("read_screening_max_shortlist", "research_read_max_shortlist"),
        default=default,
        minimum=1,
        maximum=max(1, paper_count),
    )


def _screening_min_shortlist(
    config: Mapping[str, object],
    paper_count: int,
    max_shortlist: int,
) -> int:
    return _bounded_config_int(
        config,
        ("read_screening_min_shortlist", "research_read_min_shortlist"),
        default=0,
        minimum=0,
        maximum=max(0, min(paper_count, max_shortlist)),
    )


def _bounded_config_int(
    config: Mapping[str, object],
    keys: tuple[str, ...],
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    for key in keys:
        value = config.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return max(minimum, min(maximum, value))
    return max(minimum, min(maximum, default))


def _llm_max_workers(config: Mapping[str, object]) -> int:
    value = config.get("llm_max_workers", 4)
    if isinstance(value, bool):
        return 4
    try:
        workers = int(value)
    except (TypeError, ValueError):
        workers = 4
    return max(1, min(32, workers))


def _batched(items: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]


def _paper_screening_record(paper: Mapping[str, Any], index: int) -> dict[str, Any]:
    """Return compact metadata suitable for screening prompts."""

    return {
        "paper_id": _paper_id(paper, index),
        "title": _truncate_text(str(paper.get("title") or ""), 240),
        "abstract": _truncate_text(str(paper.get("abstract") or ""), 1400),
        "source": str(paper.get("source") or ""),
        "published": paper.get("published"),
        "authors": _string_items(paper.get("authors"), limit=5),
        "categories": _string_items(paper.get("categories"), limit=6),
        "url": paper.get("url"),
        "doi": paper.get("doi"),
    }


def _truncate_text(text: str, max_chars: int) -> str:
    stripped = " ".join(text.split())
    if len(stripped) <= max_chars:
        return stripped
    return stripped[: max_chars - 3].rstrip() + "..."


def _normalize_coarse_decision(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "paper_id": str(row.get("paper_id") or "").strip(),
        "decision": _decision_value(row.get("decision")),
        "coarse_relevance_score": _optional_int(row.get("coarse_relevance_score")),
        "reason": str(row.get("reason") or "").strip(),
        "likely_facet": str(row.get("likely_facet") or row.get("facet") or "").strip(),
        "confidence": str(row.get("confidence") or "").strip(),
    }


def _normalize_rerank_decision(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "paper_id": str(row.get("paper_id") or "").strip(),
        "decision": _decision_value(row.get("decision")),
        "reading_priority": _optional_int(row.get("reading_priority")),
        "relevance_score": _optional_int(row.get("relevance_score")),
        "quality_score": _optional_int(row.get("quality_score")),
        "evidence_role": str(row.get("evidence_role") or "").strip(),
        "reason": str(row.get("reason") or "").strip(),
        "synthesis_hint": str(row.get("synthesis_hint") or "").strip(),
        "confidence": str(row.get("confidence") or "").strip(),
    }


def _decision_value(value: object) -> str:
    text = str(value or "keep").strip().lower()
    return text if text in {"keep", "drop"} else "keep"


def _optional_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _rerank_input_papers(
    papers: list[dict[str, Any]],
    coarse_decisions: list[dict[str, Any]],
    *,
    max_shortlist: int,
) -> list[dict[str, Any]]:
    score_by_id = {
        str(row.get("paper_id") or ""): int(row.get("coarse_relevance_score") or 0)
        for row in coarse_decisions
    }
    limit = max(max_shortlist, min(max_shortlist * 2, 48))
    return sorted(
        papers,
        key=lambda paper: (
            -score_by_id.get(str(paper.get("id") or paper.get("paper_id") or ""), 0),
            str(paper.get("id") or paper.get("paper_id") or ""),
        ),
    )[:limit]


def _coarse_decisions_with_priorities(
    coarse_decisions: list[dict[str, Any]],
    *,
    max_shortlist: int,
) -> list[dict[str, Any]]:
    kept = [
        row for row in coarse_decisions if _decision_value(row.get("decision")) == "keep"
    ]
    kept.sort(
        key=lambda row: (
            -int(row.get("coarse_relevance_score") or 0),
            str(row.get("paper_id") or ""),
        )
    )
    keep_ids = {str(row.get("paper_id") or "") for row in kept[:max_shortlist]}
    output: list[dict[str, Any]] = []
    priority = 1
    for row in coarse_decisions:
        paper_id = str(row.get("paper_id") or "")
        item = dict(row)
        if paper_id in keep_ids:
            item["decision"] = "keep"
            item["reading_priority"] = priority
            item["relevance_score"] = item.get("coarse_relevance_score")
            item.setdefault("quality_score", None)
            priority += 1
        elif _decision_value(row.get("decision")) == "keep":
            item["decision"] = "drop"
            item["reason"] = str(item.get("reason") or "") + " Outside read-stage shortlist budget."
        output.append(item)
    return output


def _merge_decisions(
    *,
    coarse_decisions: list[dict[str, Any]],
    rerank_decisions: list[dict[str, Any]],
    reranked_ids: set[str],
    max_shortlist: int,
    min_shortlist: int,
    required_facets: list[str],
    papers_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    coarse_by_id = {str(row.get("paper_id") or ""): row for row in coarse_decisions}
    rerank_by_id = {str(row.get("paper_id") or ""): row for row in rerank_decisions}
    output: list[dict[str, Any]] = []
    kept_priorities = 0
    for paper_id, coarse in coarse_by_id.items():
        row = dict(coarse)
        rerank = rerank_by_id.get(paper_id)
        if rerank is not None:
            row.update({key: value for key, value in rerank.items() if value not in (None, "")})
            if _decision_value(row.get("decision")) == "keep":
                kept_priorities += 1
                if _optional_int(row.get("reading_priority")) is None:
                    row["reading_priority"] = kept_priorities
        elif _decision_value(row.get("decision")) == "keep" and paper_id not in reranked_ids:
            row["decision"] = "drop"
            row["reason"] = str(row.get("reason") or "") + " Outside read-stage rerank budget."
        output.append(row)

    kept_rows = [row for row in output if _decision_value(row.get("decision")) == "keep"]
    kept_rows.sort(
        key=lambda row: (
            int(row.get("reading_priority") or 9999),
            -int(row.get("relevance_score") or row.get("coarse_relevance_score") or 0),
            str(row.get("paper_id") or ""),
        )
    )
    allowed_keep_ids = {str(row.get("paper_id") or "") for row in kept_rows[:max_shortlist]}
    for row in output:
        paper_id = str(row.get("paper_id") or "")
        if _decision_value(row.get("decision")) == "keep" and paper_id not in allowed_keep_ids:
            row["decision"] = "drop"
            row["reason"] = str(row.get("reason") or "") + " Outside read-stage shortlist budget."
    _backfill_shortlist(
        output,
        coarse_by_id=coarse_by_id,
        min_shortlist=min_shortlist,
        max_shortlist=max_shortlist,
        required_facets=required_facets,
        papers_by_id=papers_by_id,
    )
    return output


def _backfill_shortlist(
    rows: list[dict[str, Any]],
    *,
    coarse_by_id: Mapping[str, Mapping[str, Any]],
    min_shortlist: int,
    max_shortlist: int,
    required_facets: list[str],
    papers_by_id: Mapping[str, Mapping[str, Any]],
) -> None:
    """Backfill plausible candidates when model reranking is over-conservative."""

    if min_shortlist <= 0:
        return
    kept = [row for row in rows if _decision_value(row.get("decision")) == "keep"]
    target = min(max_shortlist, min_shortlist)
    if len(kept) >= target:
        return

    covered_facets = {_facet_value(row) for row in kept if _facet_value(row)}
    covered_required_facets = {
        facet
        for facet in required_facets
        if any(_facet_matches(covered, facet) for covered in covered_facets)
    }
    next_priority = max(
        (_optional_int(row.get("reading_priority")) or 0 for row in kept),
        default=0,
    ) + 1

    candidates: list[dict[str, Any]] = []
    for row in rows:
        if _decision_value(row.get("decision")) == "keep":
            continue
        paper_id = str(row.get("paper_id") or "")
        coarse = coarse_by_id.get(paper_id, {})
        coarse_decision = _decision_value(coarse.get("decision"))
        relevance = _score_value(
            row.get("relevance_score"),
            row.get("coarse_relevance_score"),
            coarse.get("coarse_relevance_score"),
        )
        if coarse_decision != "keep" and relevance < 3:
            continue
        candidate = dict(row)
        candidate["_backfill_score"] = relevance + _score_value(
            row.get("quality_score"),
            coarse.get("quality_score"),
        )
        candidate["_backfill_facet"] = (
            _facet_value(row)
            or _facet_value(coarse)
            or _infer_required_facet(papers_by_id.get(paper_id, {}), required_facets)
        )
        candidates.append(candidate)

    candidates.sort(
        key=lambda row: (
            0
            if _backfill_targets_missing_required(
                row,
                covered_required_facets,
                required_facets,
            )
            else 1,
            0 if str(row.get("_backfill_facet") or "") not in covered_facets else 1,
            -int(row.get("_backfill_score") or 0),
            str(row.get("paper_id") or ""),
        )
    )
    row_by_id = {str(row.get("paper_id") or ""): row for row in rows}
    for candidate in candidates:
        if len(kept) >= target:
            break
        paper_id = str(candidate.get("paper_id") or "")
        row = row_by_id.get(paper_id)
        if row is None:
            continue
        row["decision"] = "keep"
        row["reading_priority"] = next_priority
        if row.get("relevance_score") in (None, ""):
            row["relevance_score"] = (
                candidate.get("coarse_relevance_score")
                or candidate.get("relevance_score")
            )
        if row.get("quality_score") in (None, ""):
            row["quality_score"] = candidate.get("quality_score")
        row["reason"] = (
            str(row.get("reason") or "").strip()
            + " Backfilled from plausible coarse-screened candidates to meet the configured "
            "read-stage coverage target."
        ).strip()
        facet = str(candidate.get("_backfill_facet") or "").strip()
        if facet:
            covered_facets.add(facet)
            matched_required = _matched_required_facet(facet, required_facets)
            if matched_required:
                covered_required_facets.add(matched_required)
                row["reason"] = (
                    str(row.get("reason") or "").strip()
                    + f" Target facet: {matched_required}."
                ).strip()
            if not row.get("likely_facet") and not row.get("evidence_role"):
                row["likely_facet"] = facet
        kept.append(row)
        next_priority += 1


def _score_value(*values: object) -> int:
    for value in values:
        parsed = _optional_int(value)
        if parsed is not None:
            return parsed
    return 0


def _facet_value(row: Mapping[str, Any]) -> str:
    return str(row.get("evidence_role") or row.get("likely_facet") or "").strip().lower()


def _required_facets(config: Mapping[str, object]) -> list[str]:
    raw = config.get("research_required_facets") or config.get("required_facets")
    if not isinstance(raw, list):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in raw:
        clean = _normalize_facet(str(item or ""))
        if clean and clean not in seen:
            seen.add(clean)
            output.append(clean)
    return output


def _infer_required_facet(
    paper: Mapping[str, Any],
    required_facets: list[str],
) -> str:
    if not paper or not required_facets:
        return ""
    text = " ".join(
        str(value or "")
        for value in (
            paper.get("title"),
            paper.get("abstract"),
            " ".join(_string_items(paper.get("categories"), limit=12)),
        )
    ).lower()
    best_facet = ""
    best_score = 0
    for facet in required_facets:
        score = sum(1 for term in _facet_terms(facet) if term and term in text)
        if score > best_score:
            best_score = score
            best_facet = facet
    return best_facet if best_score > 0 else ""


def _backfill_targets_missing_required(
    row: Mapping[str, Any],
    covered_required_facets: set[str],
    required_facets: list[str],
) -> bool:
    facet = str(row.get("_backfill_facet") or "").strip().lower()
    matched = _matched_required_facet(facet, required_facets)
    return bool(matched and matched not in covered_required_facets)


def _matched_required_facet(facet: str, required_facets: list[str]) -> str:
    for required in required_facets:
        if _facet_matches(facet, required):
            return required
    return ""


def _facet_matches(value: str, required: str) -> bool:
    left = _normalize_facet(value)
    right = _normalize_facet(required)
    if not left or not right:
        return False
    if left == right:
        return True
    left_terms = _facet_terms(left)
    right_terms = _facet_terms(right)
    return bool(left_terms and right_terms and len(left_terms & right_terms) >= min(2, len(right_terms)))


def _facet_terms(value: str) -> set[str]:
    normalized = _normalize_facet(value)
    terms: set[str] = set()
    for chunk in normalized.replace("-", "_").replace("/", "_").split("_"):
        clean = chunk.strip()
        if len(clean) >= 3 and clean not in {"and", "the", "for", "with", "from"}:
            terms.add(clean)
    return terms


def _normalize_facet(value: str) -> str:
    text = str(value or "").strip().lower()
    cleaned: list[str] = []
    previous_sep = False
    for char in text:
        if char.isalnum():
            cleaned.append(char)
            previous_sep = False
        elif not previous_sep:
            cleaned.append("_")
            previous_sep = True
    return "".join(cleaned).strip("_")


def _normalize_paper_note(
    paper: Mapping[str, Any],
    response: Mapping[str, Any] | object,
    index: int,
) -> dict[str, Any]:
    row = response if isinstance(response, Mapping) else {}
    limitation_text = _text_field(row, "limitation")
    limitations = _string_list_field(row, "limitations")
    if limitation_text and limitation_text not in limitations:
        limitations.append(limitation_text)
    relation = _text_field(row, "relation_to_topic") or _text_field(row, "relevance")
    return {
        "paper_id": _text_field(row, "paper_id") or _paper_id(paper, index),
        "title": _text_field(row, "title") or str(paper.get("title") or ""),
        "evidence_role": _text_field(row, "evidence_role") or "other",
        "one_sentence_summary": _text_field(row, "one_sentence_summary")
        or _text_field(row, "summary"),
        "problem": _text_field(row, "problem") or "Not specified.",
        "method": _text_field(row, "method") or "Not specified.",
        "datasets": _string_list_field(row, "datasets"),
        "metrics": _string_list_field(row, "metrics"),
        "key_claims": _string_list_field(row, "key_claims")
        or _string_list_field(row, "main_claims"),
        "limitations": limitations or ["Not specified."],
        "relation_to_topic": relation or "Not specified.",
        "synthesis_hint": _text_field(row, "synthesis_hint"),
        "possible_experiment_hooks": _string_list_field(row, "possible_experiment_hooks"),
        "open_questions": _string_list_field(row, "open_questions"),
        "evidence_refs": _string_list_field(row, "evidence_refs"),
        "confidence": _text_field(row, "confidence") or "unknown",
        "limitation": limitation_text or (limitations[0] if limitations else "Not specified."),
        "relevance": _text_field(row, "relevance") or relation or "Not specified.",
    }


def _string_items(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            output.append(text)
        if len(output) >= limit:
            break
    return output


def _string_list_field(row: Mapping[str, Any], key: str) -> list[str]:
    value = row.get(key)
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _join_inline(value: object) -> str:
    rows = value if isinstance(value, list) else []
    return ", ".join(str(item) for item in rows if str(item).strip()) or "Not specified."


def _text_field(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    return value.strip() if isinstance(value, str) else ""


def _paper_id(paper: Mapping[str, Any], index: int) -> str:
    value = paper.get("id") or paper.get("paper_id") or paper.get("document_id")
    return str(value) if value else f"paper-{index:03d}"


def _emit(emit: EmitMessage | None, message: str) -> None:
    if emit is not None:
        emit(message)


__all__ = [
    "read_paper_notes_with_llm",
    "render_paper_notes_markdown",
    "screen_papers_with_llm",
]
