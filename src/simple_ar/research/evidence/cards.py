from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from simple_ar.research.contracts import (
    ClaimCard,
    CodeLink,
    DatasetCard,
    DocumentRecord,
    MethodCard,
    PaperCard,
    TextChunk,
)


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
METHOD_SECTIONS = {"method", "experiments"}
DATASET_SECTIONS = {"experiments", "results", "method"}
LIMITATION_SECTIONS = {"limitations", "discussion"}
CLAIM_SECTIONS = {"results", "discussion", "experiments", "method", "abstract"}
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
        method_text = _text_for_sections(doc_chunks, METHOD_SECTIONS) or text
        dataset_text = _text_for_sections(doc_chunks, DATASET_SECTIONS) or text
        limitation_text = _text_for_sections(doc_chunks, LIMITATION_SECTIONS) or text
        evidence_refs = _preferred_evidence_refs(doc_chunks)
        paper_id = _paper_id(document)
        paper_cards.append(
            PaperCard(
                paper_id=paper_id,
                title=document.title,
                problem=_sentence_matching(text, {"problem", "challenge", "study", "task"}) or "unknown",
                method_summary=_sentence_matching(method_text, METHOD_TERMS) or "unknown",
                datasets=_phrases_for_terms(dataset_text, DATASET_TERMS),
                metrics=_phrases_for_terms(dataset_text, METRIC_TERMS),
                main_claims=_claim_sentences(text),
                limitations=_sentences_for_terms(limitation_text, LIMITATION_TERMS),
                code_links=_urls(text),
                evidence_refs=evidence_refs,
                confidence=_confidence(document, evidence_refs),
            )
        )
        for index, (claim, evidence_ref) in enumerate(_claim_sentences_from_chunks(doc_chunks, fallback=text), start=1):
            claim_cards.append(
                ClaimCard(
                    claim_id=f"{paper_id}#claim-{index:03d}",
                    paper_id=paper_id,
                    claim=claim,
                    evidence_refs=[evidence_ref] if evidence_ref else evidence_refs[:1],
                    scope=_scope_for_claim(claim),
                    limitations=_sentences_for_terms(claim, LIMITATION_TERMS),
                    confidence=_claim_confidence(evidence_ref),
                )
            )
    return paper_cards, claim_cards


def build_method_cards(*, documents: list[DocumentRecord], chunks: list[TextChunk]) -> list[MethodCard]:
    """Build method cards from method/experiment sections when available."""
    chunks_by_document = _chunks_by_document(chunks)
    cards: list[MethodCard] = []
    for document in documents:
        doc_chunks = chunks_by_document.get(document.document_id, [])
        method_chunks = _chunks_for_sections(doc_chunks, METHOD_SECTIONS) or doc_chunks[:2]
        method_text = "\n".join(chunk.text for chunk in method_chunks) or document.abstract
        summary = _sentence_matching(method_text, METHOD_TERMS) or "unknown"
        components = _phrases_for_terms(method_text, METHOD_TERMS, limit=4)
        baselines = _phrases_for_terms(method_text, {"baseline", "baselines", "compare", "comparison"}, limit=4)
        if summary == "unknown" and not components and not baselines:
            continue
        paper_id = _paper_id(document)
        cards.append(
            MethodCard(
                method_id=f"{paper_id}#method-001",
                paper_id=paper_id,
                name=_clip(summary, 100) if summary != "unknown" else "unknown",
                components=components,
                training_or_runtime_notes=_sentence_matching(method_text, {"training", "runtime", "latency", "inference"}) or "unknown",
                comparison_baselines=baselines,
                evidence_refs=[chunk.chunk_id for chunk in method_chunks[:3]],
            )
        )
    return cards


def build_dataset_cards(*, documents: list[DocumentRecord], chunks: list[TextChunk]) -> list[DatasetCard]:
    """Build dataset/metric cards from evaluation-oriented sections."""
    chunks_by_document = _chunks_by_document(chunks)
    cards: list[DatasetCard] = []
    seen: set[str] = set()
    for document in documents:
        doc_chunks = chunks_by_document.get(document.document_id, [])
        dataset_chunks = _chunks_for_sections(doc_chunks, DATASET_SECTIONS) or doc_chunks[:2]
        text = "\n".join(chunk.text for chunk in dataset_chunks) or document.abstract
        dataset_phrases = _phrases_for_terms(text, DATASET_TERMS, limit=4)
        metrics = _phrases_for_terms(text, METRIC_TERMS, limit=4)
        if not dataset_phrases and not metrics:
            continue
        paper_id = _paper_id(document)
        for index, phrase in enumerate(dataset_phrases or ["unspecified evaluation setting"], start=1):
            key = f"{paper_id}:{phrase.lower()}"
            if key in seen:
                continue
            seen.add(key)
            cards.append(
                DatasetCard(
                    dataset_id=f"{paper_id}#dataset-{index:03d}",
                    name=_clip(phrase, 120),
                    task=_sentence_matching(text, {"task", "benchmark", "evaluate", "evaluation"}) or "unknown",
                    metrics=metrics,
                    access_notes="unknown",
                    evidence_refs=[chunk.chunk_id for chunk in dataset_chunks[:3]],
                )
            )
    return cards


def build_code_links(*, documents: list[DocumentRecord], chunks: list[TextChunk]) -> list[CodeLink]:
    """Extract repository or code links from document text."""
    chunks_by_document = _chunks_by_document(chunks)
    links: list[CodeLink] = []
    seen: set[str] = set()
    for document in documents:
        doc_chunks = chunks_by_document.get(document.document_id, [])
        text = "\n".join(chunk.text for chunk in doc_chunks) or document.abstract
        paper_id = _paper_id(document)
        for index, url in enumerate(_urls(text), start=1):
            if url in seen:
                continue
            seen.add(url)
            links.append(
                CodeLink(
                    link_id=f"{paper_id}#code-{index:03d}",
                    url=url,
                    paper_id=paper_id,
                    repository=_repository_name(url),
                    runnable_hint="unknown",
                    evidence_refs=_first_chunk_refs_containing(doc_chunks, url),
                )
            )
    return links


def _paper_id(document: DocumentRecord) -> str:
    value = document.metadata.get("paper_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return document.document_id


def _chunks_by_document(chunks: list[TextChunk]) -> dict[str, list[TextChunk]]:
    rows: dict[str, list[TextChunk]] = defaultdict(list)
    for chunk in chunks:
        rows[chunk.document_id].append(chunk)
    return rows


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


def _claim_sentences_from_chunks(chunks: list[TextChunk], *, fallback: str, limit: int = 3) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    preferred = _chunks_for_sections(chunks, CLAIM_SECTIONS) or chunks
    for chunk in preferred:
        for sentence in _claim_sentences(chunk.text, limit=limit):
            if (sentence, chunk.chunk_id) not in rows:
                rows.append((sentence, chunk.chunk_id))
            if len(rows) >= limit:
                return rows
    if rows:
        return rows[:limit]
    return [(claim, "") for claim in _claim_sentences(fallback, limit=limit)]


def _claim_confidence(evidence_ref: str) -> str:
    if not evidence_ref:
        return "low"
    if "#section-" in evidence_ref:
        return "medium"
    return "medium"


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


def _chunks_for_sections(chunks: list[TextChunk], sections: set[str]) -> list[TextChunk]:
    return [chunk for chunk in chunks if str(chunk.metadata.get("section") or "") in sections]


def _text_for_sections(chunks: list[TextChunk], sections: set[str]) -> str:
    return "\n".join(chunk.text for chunk in _chunks_for_sections(chunks, sections))


def _preferred_evidence_refs(chunks: list[TextChunk], *, limit: int = 3) -> list[str]:
    preferred_sections = ["method", "experiments", "results", "limitations", "abstract"]
    refs: list[str] = []
    for section in preferred_sections:
        for chunk in chunks:
            if str(chunk.metadata.get("section") or "") == section and chunk.chunk_id not in refs:
                refs.append(chunk.chunk_id)
            if len(refs) >= limit:
                return refs
    for chunk in chunks:
        if chunk.chunk_id not in refs:
            refs.append(chunk.chunk_id)
        if len(refs) >= limit:
            break
    return refs


def _first_chunk_refs_containing(chunks: list[TextChunk], value: str, *, limit: int = 2) -> list[str]:
    refs = [chunk.chunk_id for chunk in chunks if value in chunk.text]
    return refs[:limit]


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


def _repository_name(url: str) -> str | None:
    match = re.search(r"github\.com/([^/\s]+/[^/\s)#?]+)", url)
    if match:
        return match.group(1).rstrip(".")
    return None


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
