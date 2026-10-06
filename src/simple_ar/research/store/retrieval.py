"""Read-only lexical ranking and bounded views of already retained source text."""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from simple_ar.research.contracts import TextChunk


def order_source_chunks(chunks: list[TextChunk]) -> list[TextChunk]:
    """Restore retained source order when every chunk has comparable positions.

    Ingest may budget the body before front matter. Storage priority is not
    paragraph adjacency. Missing/mixed locations retain legacy order rather
    than guessing from titles or IDs; even located retained chunks may have gaps.
    """
    if (chunks and len({(row.document_id, row.source_path) for row in chunks}) == 1
            and all(type(row.line_start) is int and row.line_start > 0 for row in chunks)):
        return sorted(chunks, key=lambda row: row.line_start)
    return list(chunks)


def _source_label_match(text: str, query: str) -> re.Match[str] | None:
    """Locate an explicitly requested numbered object, not its prose mention.

    PDF extraction can separate definitions/captions from their mentions. A
    line-start label is a navigation cue, never a quality/support verdict.
    """
    labels = re.findall(r"\b(table|figure|fig\.?|theorem|proposition|lemma|corollary|assumption|algorithm)"
                        r"\s*((?:[A-Z]\.?)?\d+(?:[.-]\d+)*[a-z]?|[IVX]+)\b", query, re.I)
    for kind, number in labels:
        kinds = r"(?:figure|fig\.?)" if kind.casefold().startswith("fig") else re.escape(kind)
        match = re.search(rf"(?im)^[ \t]*{kinds}[ \t]*{re.escape(number)}\b(?![.-]\d)[ \t]*[:：.(]", text)
        if match:
            return match
    return None


def source_query_terms(query: str) -> set[str]:
    """Lexical anchors, not a semantic relevance or evidence verdict."""
    terms = set(re.findall(r"[^\W\d_]{3,}|\d+(?:\.\d+)?", query.casefold()))
    for run in re.findall(r"[\u3400-\u9fff]+", query):
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return terms


def rank_source_chunks(chunks: list[TextChunk], query: str, *, limit: int) -> list[TextChunk]:
    """Rank retained source text without creating another index or opening files."""
    terms = source_query_terms(query)
    if not terms:
        return []
    # Section headings are retained navigation metadata, not body evidence.
    # Searching "Proofs" must not require the proof text to repeat its heading.
    headings = [str(chunk.metadata.get("heading") or "") for chunk in chunks]
    overlaps = [(source_query_terms(chunk.text) | source_query_terms(heading)) & terms
                for chunk, heading in zip(chunks, headings)]
    weights = {term: 1 + math.log((len(chunks) + 1) / (1 + sum(term in row for row in overlaps)))
               for term in terms}
    phrase = " ".join(re.findall(r"\w+", query.casefold()))
    phrase_weight = math.fsum(weights[term] for term in sorted(terms))
    scored = [(math.fsum(weights[term] for term in sorted(matches)) +
               (phrase_weight if phrase and any(f" {phrase} " in
                " " + " ".join(re.findall(r"\w+", text.casefold())) + " "
                for text in (chunk.text, heading)) else 0), index)
              for index, (chunk, heading, matches) in enumerate(zip(chunks, headings, overlaps)) if matches]
    # Full quoted prose can score above the numbered definition or caption.
    # Explicit labels route there first; ordinary queries keep lexical order.
    scored.sort(key=lambda row: (
        _source_label_match(chunks[row[1]].text, query) is None,
        " ".join(re.findall(r"\w+", headings[row[1]].casefold())) != phrase,
        -row[0], row[1],
    ))
    ranked = [chunks[index] for _, index in scored]
    labelled = [row for row in ranked if _source_label_match(row.text, query)]
    if labelled:
        # Captions/definitions can start at a retained chunk's tail. The next
        # chunk may hold their conditions, not another independent search hit.
        # Return separate source views within the same limit; never stitch text
        # or infer continuity across a missing section/source boundary.
        ordered = order_source_chunks(chunks)
        continuations = []
        for row in labelled:
            position = ordered.index(row) + 1
            if position < len(ordered) and row.metadata.get("section_id"):
                next_row = ordered[position]
                if (next_row.document_id == row.document_id and next_row.source_path == row.source_path
                        and next_row.metadata.get("section_id") == row.metadata["section_id"]):
                    continuations.append(next_row)
        ranked = list({row.chunk_id: row for row in [*labelled, *continuations, *ranked]}.values())
    return ranked[:limit]


def _source_window_start(text: str, query: str, limit: int) -> int:
    """Cover a query's nearby lexical anchors, not one isolated substring.

    Word/number boundaries match chunk ranking. A sliding window maximizes
    distinct weighted query terms; repetition alone cannot displace conditions.
    Numbered-object/exact-phrase navigation stays explicit. This is not semantic ranking.
    """
    if limit <= 0 or len(text) <= limit or not query.strip():
        return 0
    phrase = query.strip()
    pattern = re.escape(phrase)
    if phrase[0].isalnum() and not '\u3400' <= phrase[0] <= '\u9fff':
        pattern = (r"(?<![\w.])" if phrase[0].isdigit() else r"(?<!\w)") + pattern
    if phrase[-1].isalnum() and not '\u3400' <= phrase[-1] <= '\u9fff':
        pattern += r"(?!\w|\.\d)" if phrase[-1].isdigit() else r"(?!\w)"
    exact = _source_label_match(text, query) or re.search(pattern, text, re.I)
    if exact:
        return min(max(0, exact.start() - limit // 3), len(text) - limit)
    terms = source_query_terms(query)
    hits = [(match.start(), match.end(), match.group().casefold())
            for match in re.finditer(r"[^\W\d_]{3,}|\d+(?:\.\d+)?", text)
            if match.group().casefold() in terms]
    # Chinese queries use the same overlapping bigrams as chunk ranking;
    # whole Chinese runs do not have Latin-style word boundaries.
    for term in sorted(terms):
        if re.fullmatch(r"[\u3400-\u9fff]{2}", term):
            hits.extend((match.start(), match.end(), term)
                        for match in re.finditer(re.escape(term), text))
    hits.sort()
    if not hits:
        return 0
    frequency = Counter(term for _, _, term in hits)
    weights = {term: 1 + math.log((len(hits) + 1) / (count + 1))
               for term, count in frequency.items()}
    counts: Counter[str] = Counter()
    right = 0
    score = 0.0
    best = (-1.0, -1, 0, 0)
    for left, (start, _, term) in enumerate(hits):
        while right < len(hits) and hits[right][1] - start <= limit:
            current = hits[right][2]
            if not counts[current]:
                score += weights[current]
            counts[current] += 1
            right += 1
        key = (round(score, 12), len(counts), -start)
        if key > best[:3]:
            best = (*key, hits[right - 1][1] if right > left else start)
        if right > left:
            counts[term] -= 1
            if not counts[term]:
                score -= weights[term]
                del counts[term]
        else:
            right = left + 1  # One token can exceed a tiny window allowance.
    start, end = -best[2], best[3]
    # Spend available slack on context before the first hit, without clipping
    # the last matched condition. Offsets always address unchanged Unicode text.
    return min(max(0, start - min(limit // 3, limit - (end - start))), len(text) - limit)


def source_chunk_views(chunks: list[TextChunk], *, query: str = "", max_chars: int = 4800,
                       max_chunk_chars: int | None = None) -> list[dict[str, Any]]:
    """Expose bounded source text and exact offsets; no identity/support verdict."""
    if max_chars < 1 or (max_chunk_chars is not None and max_chunk_chars < 1):
        raise ValueError("Source view character budgets must be positive.")
    remaining = max_chars
    rows = []
    for chunk in chunks:
        limit = remaining // (len(chunks) - len(rows))
        if max_chunk_chars is not None:
            limit = min(limit, max_chunk_chars)
        start = _source_window_start(chunk.text, query, limit)
        text = chunk.text[start:start + limit]
        remaining -= len(text)
        rows.append({"chunk_id": chunk.chunk_id, "document_id": chunk.document_id,
            "text": text, "character_start": start, "character_end": start + len(text),
            "total_characters": len(chunk.text), "truncated": len(text) < len(chunk.text),
            "source_path": chunk.source_path, "page": chunk.page, "line_start": chunk.line_start,
            "line_end": chunk.line_end, "extraction_status": chunk.metadata.get("extraction_status", "unknown"),
            **{key: chunk.metadata[key] for key in ("section", "section_id", "heading") if key in chunk.metadata}})
    return rows


def material_overview_views(chunks: list[TextChunk], query: str, *, limit: int = 6,
                            max_chunk_chars: int = 1400, max_chars: int = 7200) -> list[dict[str, Any]]:
    """A bounded structured overview plus question-relevant retained evidence.

    An abstract can span several windows of the SAME chunk. Preserve these
    exact offsets rather than cutting its results off at the first prefix.
    At most half the slots go to overview; all remaining slots use the existing
    lexical retrieval. This is source selection, not a claim of full reading.
    """
    if limit < 1 or max_chunk_chars < 1 or max_chars < 1:
        raise ValueError("Material view limits must be positive.")
    limit = min(limit, max_chars)
    ordered = order_source_chunks(chunks)
    # Legacy ingest labelled short unheaded parsed text as "abstract". Its
    # span starts at line 1, unlike text after an explicit abstract heading.
    abstracts = [row for row in ordered if row.metadata.get("section") == "abstract"
                 and not (row.metadata.get("extraction_status") == "parsed" and row.line_start == 1)]
    overview_slots = max(1, limit // 2)
    views = []
    remaining = max_chars
    for chunk in abstracts:
        start = 0
        while start < len(chunk.text):
            if len(views) >= overview_slots:
                break
            # Preserve capacity for the non-overview slots too. This is one
            # shared budget, not a new allowance for every selected paragraph.
            size = min(max_chunk_chars, remaining - (limit - len(views) - 1))
            if size < 1:
                break
            row = source_chunk_views([chunk], max_chars=size)[0]
            text = chunk.text[start:start + size]
            row.update(text=text, character_start=start, character_end=start + len(text),
                       truncated=len(text) < len(chunk.text), selection="abstract_overview")
            views.append(row)
            remaining -= len(text)
            start += len(text)
        if len(views) >= overview_slots:
            break
    selected_ids = {row["chunk_id"] for row in views}
    # Without an explicit abstract, pre-heading text may contain the paper's
    # unheaded summary or main results, not just a byline. Keep it searchable
    # instead of treating an uncertain section classification as irrelevance.
    excluded = {"references", "front_matter"} if abstracts else {"references"}
    candidates = [row for row in ordered if row.chunk_id not in selected_ids
                  and row.metadata.get("section") not in excluded]
    # A reference-only or unstructured input is still available; do not infer
    # that filtering retained sections proves the document lacks evidence.
    if not candidates:
        candidates = [row for row in ordered if row.chunk_id not in selected_ids]
    available = limit - len(views)
    # An overview is not a narrow lookup: repeated hits from one long section
    # must not crowd out distinct retained sections. First expose a section's
    # entry context (definitions/conditions can precede its highest-scoring
    # interior), then fill ranked interiors within the SAME budget. An explicit
    # caption still routes directly. Missing IDs keep the legacy ranking.
    ranked_pool = rank_source_chunks(candidates, query, limit=len(candidates))
    lexical_ids = {row.chunk_id for row in ranked_pool}
    entries = {}
    for row in candidates:
        entries.setdefault(str(row.metadata.get("section_id") or row.chunk_id), row)
    ranked = []
    sections_seen = set()
    for row in ranked_pool:
        section_id = str(row.metadata.get("section_id") or row.chunk_id)
        if section_id not in sections_seen:
            ranked.append(row if _source_label_match(row.text, query) else entries[section_id])
            sections_seen.add(section_id)
    chosen = {row.chunk_id for row in ranked}
    ranked = (ranked + [row for row in ranked_pool if row.chunk_id not in chosen])[:available]
    matched = {row.chunk_id for row in ranked}
    selected = (ranked + [row for row in candidates if row.chunk_id not in matched])[:available]
    for row in source_chunk_views(selected, query=query, max_chars=max(1, remaining),
                                  max_chunk_chars=max_chunk_chars):
        row["selection"] = ("task_lexical_match" if row["chunk_id"] in lexical_ids
                            else "section_entry_context" if row["chunk_id"] in matched else "source_order_fallback")
        views.append(row)
    return views
