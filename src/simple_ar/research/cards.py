from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from simple_ar.research.contracts import ClaimCard, DocumentRecord, PaperCard, TextChunk


METHOD_TERMS = {
    "agent",
    "architecture",
    "framework",
    "method",
    "model",
    "pipeline",
    "protocol",
    "system",
}
DATASET_TERMS = {"dataset", "benchmark", "task", "corpus", "suite"}
METRIC_TERMS = {"accuracy", "f1", "latency", "metric", "reward", "runtime", "success"}
LIMITATION_TERMS = {"fail", "failure", "limitation", "limitations", "risk", "risks", "unstable", "weakness"}
CLAIM_VERBS = {
    "compare",
    "compared",
    "compares",
    "demonstrate",
    "demonstrated",
    "demonstrates",
    "evaluate",
    "evaluated",
    "evaluates",
    "find",
    "finds",
    "improve",
    "improved",
    "improves",
    "measure",
    "measured",
    "measures",
    "propose",
    "proposed",
    "proposes",
    "report",
    "reported",
    "reports",
    "show",
    "showed",
    "shown",
    "shows",
}


def build_evidence_cards(
    *,
    documents: list[DocumentRecord],
    chunks: list[TextChunk],
) -> tuple[list[PaperCard], list[ClaimCard]]:
    """Build deterministic evidence cards from documents and chunks.

    Args:
        documents: Document records from the search document store.
        chunks: Local chunks derived from abstracts and parsed local files.

    Returns:
        ``(paper_cards, claim_cards)`` grounded in chunk ids. This first V2.3
        implementation is deliberately conservative: it extracts short evidence
        hints from text and uses ``unknown`` when a field is not visible.
    """
    chunks_by_document: dict[str, list[TextChunk]] = defaultdict(list)
    for chunk in chunks:
        chunks_by_document[chunk.document_id].append(chunk)

    paper_cards: list[PaperCard] = []
    claim_cards: list[ClaimCard] = []
    for document in documents:
        doc_chunks = chunks_by_document.get(document.document_id, [])
        text = "\n".join(chunk.text for chunk in doc_chunks) or document.abstract
        evidence_refs = [chunk.chunk_id for chunk in doc_chunks[:3]]
        paper_id = _paper_id(document)
        paper_cards.append(
            PaperCard(
                paper_id=paper_id,
                title=document.title,
                problem=_sentence_matching(text, {"problem", "challenge", "study", "task"}) or "unknown",
                method_summary=_sentence_matching(text, METHOD_TERMS) or "unknown",
                datasets=_phrases_for_terms(text, DATASET_TERMS),
                metrics=_phrases_for_terms(text, METRIC_TERMS),
                main_claims=_claim_sentences(text),
                limitations=_sentences_for_terms(text, LIMITATION_TERMS),
                code_links=_urls(text),
                evidence_refs=evidence_refs,
                confidence=_confidence(document, evidence_refs),
            )
        )
        for index, claim in enumerate(_claim_sentences(text), start=1):
            claim_cards.append(
                ClaimCard(
                    claim_id=f"{paper_id}#claim-{index:03d}",
                    paper_id=paper_id,
                    claim=claim,
                    evidence_refs=evidence_refs[:1] or evidence_refs,
                    scope=_scope_for_claim(claim),
                    limitations=_sentences_for_terms(claim, LIMITATION_TERMS),
                    confidence="medium" if evidence_refs else "low",
                )
            )
    return paper_cards, claim_cards


def _paper_id(document: DocumentRecord) -> str:
    value = document.metadata.get("paper_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return document.document_id


def _confidence(document: DocumentRecord, evidence_refs: list[str]) -> str:
    if document.extraction_status == "parsed" and evidence_refs:
        return "medium"
    if document.abstract and evidence_refs:
        return "medium"
    if document.abstract:
        return "low"
    return "unknown"


def _claim_sentences(text: str, *, limit: int = 3) -> list[str]:
    claims: list[str] = []
    for sentence in _sentences(text):
        terms = _terms(sentence)
        if terms & CLAIM_VERBS:
            claims.append(sentence)
        if len(claims) >= limit:
            break
    if not claims:
        first = next(iter(_sentences(text)), "")
        if first:
            claims.append(first)
    return claims[:limit]


def _sentence_matching(text: str, terms: set[str]) -> str:
    for sentence in _sentences(text):
        if _terms(sentence) & terms:
            return sentence
    return ""


def _sentences_for_terms(text: str, terms: set[str], *, limit: int = 3) -> list[str]:
    rows: list[str] = []
    for sentence in _sentences(text):
        if _terms(sentence) & terms:
            rows.append(sentence)
        if len(rows) >= limit:
            break
    return rows


def _phrases_for_terms(text: str, terms: set[str], *, limit: int = 6) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for sentence in _sentences(text):
        sentence_terms = _terms(sentence)
        if not (sentence_terms & terms):
            continue
        phrase = _clip(sentence, 180)
        key = phrase.lower()
        if phrase and key not in seen:
            found.append(phrase)
            seen.add(key)
        if len(found) >= limit:
            break
    return found


def _scope_for_claim(claim: str) -> str:
    terms = _terms(claim)
    if terms & LIMITATION_TERMS:
        return "limitation"
    if terms & DATASET_TERMS:
        return "evaluation"
    if terms & METHOD_TERMS:
        return "method"
    return "general"


def _urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s)>\]]+", text)


def _clip(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    clipped = compact[: limit - 3].rsplit(" ", 1)[0].strip()
    return f"{clipped}..." if clipped else compact[: limit - 3].strip() + "..."


def _sentences(text: str) -> Iterable[str]:
    segments: list[str] = []
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph_lines:
            return
        segments.append(" ".join(paragraph_lines))
        paragraph_lines.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            continue
        if line.startswith("#"):
            flush_paragraph()
            continue
        paragraph_lines.append(re.sub(r"^[-*]\s+", "", line))
    flush_paragraph()
    if not segments:
        return []
    pieces: list[str] = []
    for segment in segments:
        compact = " ".join(segment.split())
        pieces.extend(re.split(r"(?<=[.!?])\s+", compact))
    return [piece.strip() for piece in pieces if piece.strip()]


def _terms(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9][a-z0-9_+-]{1,}", text.lower())
        if word not in {"a", "an", "and", "are", "as", "at", "by", "for", "from", "in", "is", "of", "on", "or", "the", "to", "with"}
    }
