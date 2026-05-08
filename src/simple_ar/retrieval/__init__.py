"""Local artifact retrieval utilities for SimpleAutoResearch V2."""

from simple_ar.retrieval.chunking import ArtifactChunk, build_artifact_chunks
from simple_ar.retrieval.evidence import collect_stage_evidence, format_evidence_snippets
from simple_ar.retrieval.index import build_artifact_index
from simple_ar.retrieval.search import search_artifacts

__all__ = [
    "ArtifactChunk",
    "build_artifact_chunks",
    "build_artifact_index",
    "collect_stage_evidence",
    "format_evidence_snippets",
    "search_artifacts",
]
