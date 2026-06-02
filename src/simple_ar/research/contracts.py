from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


ResearchMode = Literal["lite", "standard", "strong"]
ExtractionStatus = Literal["metadata_only", "pending", "parsed", "failed", "skipped"]


@dataclass(frozen=True)
class ResearchContract:
    """User-facing research scope used to drive retrieval and synthesis.

    Args:
        topic: Original research topic or user request.
        goals: Concrete goals the run should satisfy.
        non_goals: Explicitly excluded goals to keep the run bounded.
        success_criteria: Signals used to judge whether the run is useful.
        mode: Retrieval/evidence strength: ``lite``, ``standard``, or ``strong``.
        requires_experiment: Whether the research should produce an experiment contract.
        constraints: Budget, source, domain, or safety constraints.
    """

    topic: str
    goals: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    mode: ResearchMode = "standard"
    requires_experiment: bool = True
    constraints: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "research_contract.v1"

    def to_row(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "ResearchContract":
        """Build a contract from a JSON row, tolerating missing optional fields."""
        return cls(
            topic=str(row.get("topic", "")),
            goals=_string_list(row.get("goals")),
            non_goals=_string_list(row.get("non_goals")),
            success_criteria=_string_list(row.get("success_criteria")),
            mode=_mode(row.get("mode")),
            requires_experiment=bool(row.get("requires_experiment", True)),
            constraints=_dict(row.get("constraints")),
            schema_version=str(row.get("schema_version", "research_contract.v1")),
        )


@dataclass(frozen=True)
class SourcePlan:
    """Planned retrieval strategy for a research run."""

    queries: list[str]
    sources: list[str] = field(default_factory=lambda: ["openalex", "semantic_scholar", "arxiv"])
    max_results_per_query: int = 10
    mode: ResearchMode = "standard"
    require_fulltext: bool = False
    allow_pdf_download: bool = False
    local_documents: list[str] = field(default_factory=list)
    cache_enabled: bool = True
    index_backend: str = "keyword"
    index_root: str | None = None
    filters: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    schema_version: str = "source_plan.v1"

    def to_row(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class ResearchQuestion:
    """One scoped sub-question used for retrieval planning.

    Args:
        question_id: Stable identifier such as ``RQ1``.
        question: Natural-language question to answer with evidence.
        facet: Evidence facet, such as ``method`` or ``benchmark``.
        rationale: Why this question is useful for the topic.
        required: Whether the coverage checker should treat this question as
            required evidence.
        negative_scope: Topics or claims this question should avoid.
        success_criteria: Signals that would make the answer useful.
    """

    question_id: str
    question: str
    facet: str = "general"
    rationale: str = ""
    required: bool = True
    negative_scope: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    schema_version: str = "research_question.v1"

    def to_row(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class QueryPlan:
    """Seed and follow-up queries derived from research questions.

    Args:
        topic: Original research topic.
        seed_queries: User or topic-derived queries.
        follow_up_queries: Facet-driven query expansions.
        queries: Executable query order used by the source planner.
        query_specs: Structured paper-search query intents used to derive
            source-friendly keyword queries.
        required_facets: Facets the evidence search should try to cover.
        negative_terms: Terms that should be treated as out-of-scope hints.
        max_rounds: Planned retrieval rounds for later DeepResearch loops.
        auto_expansion: Whether query expansion was enabled.
        rationale: Human-readable provenance for why the plan was generated.
        planner: Planner backend that produced the plan, such as
            ``deterministic`` or ``llm``.
    """

    topic: str
    seed_queries: list[str]
    follow_up_queries: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    query_specs: list[dict[str, Any]] = field(default_factory=list)
    required_facets: list[str] = field(default_factory=list)
    negative_terms: list[str] = field(default_factory=list)
    max_rounds: int = 1
    auto_expansion: bool = True
    rationale: str = ""
    planner: str = "deterministic"
    schema_version: str = "query_plan.v1"

    def to_row(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class DocumentRecord:
    """A retrievable or ingested document with provenance.

    ``DocumentRecord`` is intentionally broader than a paper metadata row: it
    can represent metadata-only records, local PDFs/text files, parsed HTML, or
    future MCP-provided resources.
    """

    document_id: str
    title: str
    source: str
    source_id: str | None = None
    url: str | None = None
    doi: str | None = None
    published: str | None = None
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    local_path: str | None = None
    content_hash: str | None = None
    extraction_status: ExtractionStatus = "metadata_only"
    parser: str | None = None
    license_hint: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "document_record.v1"

    def to_row(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class FulltextHint:
    """A non-destructive full-text access hint for one document.

    Args:
        document_id: Document this hint belongs to.
        kind: Resource type, such as ``pdf``, ``html``, ``text``, or ``landing``.
        source: Where the hint came from, such as ``arxiv`` or ``openalex``.
        url: Remote URL when available.
        local_path: Local file path when available.
        access: Access class, such as ``open``, ``local``, ``restricted``, or
            ``unknown``.
        status: Planner/fetch status. Remote downloads are only attempted when
            full-text intent, permissions, and budgets allow them.
        reason: Short explanation for the status.
        size_bytes: Local file size when known.
    """

    document_id: str
    kind: str
    source: str
    url: str | None = None
    local_path: str | None = None
    access: str = "unknown"
    status: str = "hint_only"
    reason: str = ""
    size_bytes: int | None = None
    schema_version: str = "fulltext_hint.v1"

    def to_row(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class TextChunk:
    """A source-labelled text span used by local retrieval and evidence cards."""

    chunk_id: str
    document_id: str
    text: str
    source_path: str | None = None
    page: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    token_estimate: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "text_chunk.v1"

    def to_row(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class DocumentSection:
    """A section-aware text span extracted from one document.

    The section record is intentionally lightweight. It keeps enough structure
    for cards, report drafting, and future parser backends without requiring a
    heavy PDF/HTML layout model.
    """

    section_id: str
    document_id: str
    section: str
    heading: str
    text: str
    source_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    token_estimate: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "document_section.v1"

    def to_row(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class PaperCard:
    """Structured paper summary used by synthesis, contracts, and reports."""

    paper_id: str
    title: str
    problem: str = "unknown"
    method_summary: str = "unknown"
    datasets: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    main_claims: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    code_links: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    confidence: str = "unknown"
    schema_version: str = "paper_card.v1"

    def to_row(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class ClaimCard:
    """One literature claim with source references and scope notes."""

    claim_id: str
    paper_id: str
    claim: str
    evidence_refs: list[str] = field(default_factory=list)
    scope: str = "unknown"
    limitations: list[str] = field(default_factory=list)
    confidence: str = "unknown"
    schema_version: str = "claim_card.v1"

    def to_row(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class MethodCard:
    """Method-level structure extracted from papers or local documents."""

    method_id: str
    paper_id: str
    name: str
    components: list[str] = field(default_factory=list)
    training_or_runtime_notes: str = "unknown"
    comparison_baselines: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    schema_version: str = "method_card.v1"

    def to_row(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class DatasetCard:
    """Dataset and metric information relevant to experiment design."""

    dataset_id: str
    name: str
    task: str = "unknown"
    metrics: list[str] = field(default_factory=list)
    access_notes: str = "unknown"
    license_hint: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    schema_version: str = "dataset_card.v1"

    def to_row(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class CodeLink:
    """A paper or project code link with reproducibility notes."""

    link_id: str
    url: str
    paper_id: str | None = None
    repository: str | None = None
    license_hint: str | None = None
    runnable_hint: str = "unknown"
    evidence_refs: list[str] = field(default_factory=list)
    schema_version: str = "code_link.v1"

    def to_row(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class IdeaCandidate:
    """A candidate research idea grounded in evidence cards."""

    idea_id: str
    title: str
    hypothesis: str
    motivation_refs: list[str] = field(default_factory=list)
    proposed_change: str = ""
    expected_outcome: str = ""
    required_baselines: list[str] = field(default_factory=list)
    required_datasets: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    feasibility: str = "unknown"
    risks: list[str] = field(default_factory=list)
    schema_version: str = "idea_candidate.v1"

    def to_row(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class NoveltyCheck:
    """Lightweight novelty and overlap assessment for one idea."""

    idea_id: str
    status: str
    similar_work_refs: list[str] = field(default_factory=list)
    risk_level: str = "unknown"
    rationale: str = ""
    schema_version: str = "novelty_check.v1"

    def to_row(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass(frozen=True)
class ExperimentContract:
    """Bridge artifact from a research hypothesis to code/reproduction work."""

    contract_id: str
    hypothesis: str
    motivation_refs: list[str] = field(default_factory=list)
    baseline: str = "unknown"
    dataset: str = "unknown"
    metrics: list[str] = field(default_factory=list)
    proposed_change: str = ""
    implementation_scope: list[str] = field(default_factory=list)
    validation_hints: list[str] = field(default_factory=list)
    resource_budget: dict[str, Any] = field(default_factory=dict)
    risks: list[str] = field(default_factory=list)
    report_claim_plan: list[str] = field(default_factory=list)
    schema_version: str = "experiment_contract.v1"

    def to_row(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _mode(value: object) -> ResearchMode:
    text = str(value or "standard")
    if text in {"lite", "standard", "strong"}:
        return text  # type: ignore[return-value]
    return "standard"
