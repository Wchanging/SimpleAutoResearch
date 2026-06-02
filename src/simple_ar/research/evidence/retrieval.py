from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from simple_ar.literature.models import Paper


@dataclass(frozen=True)
class RetrievalCandidate:
    """One paper returned by one source/query attempt."""

    paper: Paper
    source: str
    query: str
    query_index: int
    round_index: int
    facet: str = ""
    returned_source: str = ""


def screen_retrieval_candidates(
    candidates: list[RetrievalCandidate],
    *,
    max_documents: int,
    negative_terms: list[str] | None = None,
    priority_facets: list[str] | None = None,
) -> tuple[list[Paper], list[dict[str, Any]]]:
    """Deduplicate, score, and keep the strongest retrieval candidates.

    Args:
        candidates: Raw paper candidates from source/query attempts.
        max_documents: Maximum number of papers to keep after screening.
        negative_terms: Optional out-of-scope hints from query planning.
        priority_facets: Optional required facets. When set, screening first
            reserves one relevant candidate per facet when possible, then fills
            the remaining budget by rank. This keeps coverage from collapsing
            to a single high-scoring query family.

    Returns:
        A pair of ``(kept_papers, screening_decision_rows)``.
    """

    limit = max(1, max_documents)
    negative_terms = negative_terms or []
    best_by_key: dict[str, tuple[RetrievalCandidate, int]] = {}
    decisions: list[dict[str, Any]] = []

    for candidate in candidates:
        key = paper_identity_key(candidate.paper)
        score = relevance_score(candidate.paper, candidate.query, negative_terms=negative_terms)
        existing = best_by_key.get(key)
        if existing is None or score > existing[1]:
            if existing is not None:
                decisions.append(_decision_row(existing[0], existing[1], "discard", "duplicate_lower_score"))
            best_by_key[key] = (candidate, score)
        else:
            decisions.append(_decision_row(candidate, score, "discard", "duplicate_lower_score"))

    ranked = sorted(
        best_by_key.values(),
        key=lambda item: (item[1], bool(item[0].paper.abstract), item[0].paper.title.lower()),
        reverse=True,
    )
    ranked_with_rank = [(rank, candidate, score) for rank, (candidate, score) in enumerate(ranked, start=1)]
    selected_keys: set[str] = set()
    kept_rows: list[tuple[int, RetrievalCandidate, int, str]] = []

    for facet in _facet_priority(priority_facets):
        if len(kept_rows) >= limit:
            break
        for rank, candidate, score in ranked_with_rank:
            key = paper_identity_key(candidate.paper)
            if key in selected_keys or candidate.facet != facet or score <= 0:
                continue
            selected_keys.add(key)
            kept_rows.append((rank, candidate, score, "facet_coverage"))
            break

    for rank, candidate, score in ranked_with_rank:
        if len(kept_rows) >= limit:
            break
        key = paper_identity_key(candidate.paper)
        if key in selected_keys:
            continue
        if score > 0 or not kept_rows:
            selected_keys.add(key)
            kept_rows.append((rank, candidate, score, "top_ranked"))

    kept: list[Paper] = []
    kept_by_key = {paper_identity_key(candidate.paper): (rank, candidate, score, reason) for rank, candidate, score, reason in kept_rows}
    for rank, candidate, score in ranked_with_rank:
        key = paper_identity_key(candidate.paper)
        selected = kept_by_key.get(key)
        if selected is not None:
            kept.append(candidate.paper)
            decisions.append(_decision_row(candidate, score, "keep", selected[3], rank=rank))
        else:
            reason = "below_document_budget" if len(kept_rows) >= limit else "low_relevance"
            decisions.append(_decision_row(candidate, score, "discard", reason, rank=rank))

    decisions.sort(key=lambda row: (row.get("decision") != "keep", row.get("rank") or 9999, row["paper_id"]))
    return kept, decisions


def paper_identity_key(paper: Paper) -> str:
    """Return a deduplication key for one paper metadata row."""

    if paper.doi:
        return f"doi:{paper.doi.lower()}"
    title = re.sub(r"[^a-z0-9]+", " ", paper.title.lower()).strip()
    if title:
        return f"title:{title}"
    if paper.source_id:
        return f"{paper.source}:{paper.source_id}".lower()
    return f"id:{paper.id.lower()}"


def relevance_score(
    paper: Paper,
    query: str,
    *,
    negative_terms: list[str] | None = None,
) -> int:
    """Compute a small lexical relevance score for screening metadata."""

    title_terms = _terms(paper.title)
    abstract_terms = _terms(paper.abstract)
    query_terms = _terms(query)
    if not query_terms:
        return 1
    score = len(query_terms & title_terms) * 3 + len(query_terms & abstract_terms)
    all_terms = title_terms | abstract_terms
    for phrase in negative_terms or []:
        phrase_terms = _terms(phrase)
        if phrase_terms and phrase_terms <= all_terms:
            score -= 3
        for alias in _negative_aliases(phrase):
            if alias in all_terms:
                score -= 3
    return max(0, score)


def _facet_priority(values: list[str] | None) -> list[str]:
    """Return unique non-empty facet names in caller-provided order."""

    result: list[str] = []
    for value in values or []:
        facet = str(value).strip()
        if facet and facet not in result:
            result.append(facet)
    return result


def _decision_row(
    candidate: RetrievalCandidate,
    score: int,
    decision: str,
    reason: str,
    *,
    rank: int | None = None,
) -> dict[str, Any]:
    paper = candidate.paper
    return {
        "schema_version": "screening_decision.v1",
        "paper_id": paper.id,
        "title": paper.title,
        "source": candidate.source,
        "returned_source": candidate.returned_source or candidate.source,
        "query": candidate.query,
        "query_index": candidate.query_index,
        "round": candidate.round_index,
        "facet": candidate.facet,
        "relevance_score": score,
        "decision": decision,
        "reason": reason,
        "rank": rank,
    }


def _terms(text: str) -> set[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
    return {
        word
        for word in re.findall(r"[a-z0-9][a-z0-9_+-]{1,}", text.lower())
        if word not in stopwords
    }


def _negative_aliases(text: str) -> set[str]:
    """Return compact aliases that often appear in abstracts.

    Literature metadata frequently uses acronyms such as ``MARL`` instead of a
    full negative-scope phrase like ``multi-agent reinforcement learning``.
    Keeping this small and deterministic improves screening without adding a
    heavier classifier to the V2.3 retrieval path.
    """

    words = [
        word
        for word in re.findall(r"[a-z0-9]+", text.lower())
        if word not in {"a", "an", "and", "for", "of", "the", "to", "with"}
    ]
    aliases: set[str] = set()
    if 2 <= len(words) <= 8:
        aliases.add("".join(word[0] for word in words))
    if words == ["multi", "agent", "reinforcement", "learning"]:
        aliases.add("marl")
    return aliases
