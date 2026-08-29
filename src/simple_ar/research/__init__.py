"""Research primitives with lazy capability exports."""

from simple_ar.research.contracts import (
    ClaimCard,
    CodeLink,
    DatasetCard,
    DocumentRecord,
    ExperimentContract,
    FulltextHint,
    IdeaCandidate,
    MethodCard,
    NoveltyCheck,
    PaperCard,
    QueryPlan,
    ResearchContract,
    ResearchExperimentContract,
    ResearchQuestion,
    SourcePlan,
    TextChunk,
)
__all__ = [
    "ClaimCard",
    "CodeLink",
    "DatasetCard",
    "DocumentRecord",
    "ExperimentContract",
    "ResearchExperimentContract",
    "FulltextHint",
    "IdeaCandidate",
    "MethodCard",
    "NoveltyCheck",
    "PaperCard",
    "QueryPlan",
    "ResearchContract",
    "ResearchQuestion",
    "SourcePlan",
    "TextChunk",
    "ResearchPlanRequest",
    "ResearchPlanResult",
    "build_research_plan",
    "search_request_from_plan",
    "run_research_plan_capability",
    "DocumentBundle",
    "DocumentIngestRequest",
    "build_document_bundle",
    "build_local_document_bundle",
    "run_document_ingest_capability",
    "SynthesisRequest",
    "SynthesisResult",
    "SynthesisStatus",
    "synthesize_evidence",
    "run_synthesis_capability",
    "ReadRequest",
    "ReadResult",
    "ReadStatus",
    "read_documents",
    "run_read_capability",
    "validate_read_evidence",
    "ExperimentRequest",
    "experiment_request_from_synthesis",
    "ExperimentResult",
    "ExperimentEvaluation",
    "run_and_analyze",
    "run_experiment",
    "run_experiment_capability",
    "AnalysisRequest",
    "AnalysisHandoff",
    "analyze_results",
    "compare_experiment_results",
    "analyze_experiment_capability",
    "transition_request_from_analysis",
    "transition_request_from_synthesis",
    "transition_request_from_report_audit",
    "ResearchBriefRequest",
    "ResearchBriefResult",
    "ResearchBriefStatus",
    "build_research_brief",
    "evidence_pack_from_read",
    "run_research_brief_capability",
    "SearchRequest",
    "SearchResult",
    "SearchStatus",
    "search_sources",
    "run_search_capability",
    "register_research_capabilities",
    "research_capability_names",
]

_LAZY_EXPORTS = {
    "ResearchPlanRequest": (
        "simple_ar.research.planning.capability",
        "ResearchPlanRequest",
    ),
    "ResearchPlanResult": (
        "simple_ar.research.planning.capability",
        "ResearchPlanResult",
    ),
    "build_research_plan": (
        "simple_ar.research.planning.capability",
        "build_research_plan",
    ),
    "search_request_from_plan": (
        "simple_ar.research.planning.capability",
        "search_request_from_plan",
    ),
    "run_research_plan_capability": (
        "simple_ar.research.planning.capability",
        "run_research_plan_capability",
    ),
    "DocumentBundle": ("simple_ar.research.documents.ingest", "DocumentBundle"),
    "DocumentIngestRequest": (
        "simple_ar.research.documents.ingest",
        "DocumentIngestRequest",
    ),
    "build_document_bundle": (
        "simple_ar.research.documents.ingest",
        "build_document_bundle",
    ),
    "build_local_document_bundle": (
        "simple_ar.research.documents.ingest",
        "build_local_document_bundle",
    ),
    "run_document_ingest_capability": (
        "simple_ar.research.documents.ingest",
        "run_document_ingest_capability",
    ),
    "SynthesisRequest": ("simple_ar.research.synthesis", "SynthesisRequest"),
    "SynthesisResult": ("simple_ar.research.synthesis", "SynthesisResult"),
    "SynthesisStatus": ("simple_ar.research.synthesis", "SynthesisStatus"),
    "synthesize_evidence": ("simple_ar.research.synthesis", "synthesize_evidence"),
    "run_synthesis_capability": (
        "simple_ar.research.synthesis",
        "run_synthesis_capability",
    ),
    "ReadRequest": ("simple_ar.research.evidence.reader", "ReadRequest"),
    "ReadResult": ("simple_ar.research.evidence.reader", "ReadResult"),
    "ReadStatus": ("simple_ar.research.evidence.reader", "ReadStatus"),
    "read_documents": ("simple_ar.research.evidence.reader", "read_documents"),
    "run_read_capability": (
        "simple_ar.research.evidence.reader",
        "run_read_capability",
    ),
    "validate_read_evidence": (
        "simple_ar.research.evidence.reader",
        "validate_read_evidence",
    ),
    "ExperimentRequest": ("simple_ar.research.experiment", "ExperimentRequest"),
    "experiment_request_from_synthesis": (
        "simple_ar.research.experiment",
        "experiment_request_from_synthesis",
    ),
    "ExperimentResult": ("simple_ar.research.experiment", "ExperimentResult"),
    "ExperimentEvaluation": ("simple_ar.research.experiment", "ExperimentEvaluation"),
    "run_and_analyze": ("simple_ar.research.experiment", "run_and_analyze"),
    "run_experiment": ("simple_ar.research.experiment", "run_experiment"),
    "run_experiment_capability": (
        "simple_ar.research.experiment",
        "run_experiment_capability",
    ),
    "AnalysisRequest": ("simple_ar.research.analysis", "AnalysisRequest"),
    "AnalysisHandoff": ("simple_ar.research.analysis", "AnalysisHandoff"),
    "analyze_results": ("simple_ar.research.analysis", "analyze_results"),
    "compare_experiment_results": (
        "simple_ar.research.analysis",
        "compare_experiment_results",
    ),
    "analyze_experiment_capability": (
        "simple_ar.research.analysis",
        "analyze_experiment_capability",
    ),
    "transition_request_from_analysis": (
        "simple_ar.research.decisions",
        "transition_request_from_analysis",
    ),
    "transition_request_from_synthesis": (
        "simple_ar.research.decisions",
        "transition_request_from_synthesis",
    ),
    "transition_request_from_report_audit": (
        "simple_ar.research.decisions",
        "transition_request_from_report_audit",
    ),
    "ResearchBriefRequest": ("simple_ar.research.brief", "ResearchBriefRequest"),
    "ResearchBriefResult": ("simple_ar.research.brief", "ResearchBriefResult"),
    "ResearchBriefStatus": ("simple_ar.research.brief", "ResearchBriefStatus"),
    "build_research_brief": ("simple_ar.research.brief", "build_research_brief"),
    "evidence_pack_from_read": (
        "simple_ar.research.brief",
        "evidence_pack_from_read",
    ),
    "run_research_brief_capability": (
        "simple_ar.research.brief",
        "run_research_brief_capability",
    ),
    "SearchRequest": ("simple_ar.research.sources.capability", "SearchRequest"),
    "SearchResult": ("simple_ar.research.sources.capability", "SearchResult"),
    "SearchStatus": ("simple_ar.research.sources.capability", "SearchStatus"),
    "search_sources": ("simple_ar.research.sources.capability", "search_sources"),
    "run_search_capability": (
        "simple_ar.research.sources.capability",
        "run_search_capability",
    ),
    "register_research_capabilities": (
        "simple_ar.research.registry",
        "register_research_capabilities",
    ),
    "research_capability_names": (
        "simple_ar.research.registry",
        "research_capability_names",
    ),
}


def __getattr__(name: str):
    """Lazily expose capability facades without importing all domains."""

    try:
        module_name, attribute = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    from importlib import import_module

    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
