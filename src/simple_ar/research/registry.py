"""Explicit registration of the built-in research capability adapters."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Callable

from simple_ar.core import CapabilityRegistry, CapabilityResult


CapabilityHandler = Callable[..., CapabilityResult]

# These are opt-in adapters, not an automatic workflow. Legacy domain-specific
# design, code generation, and execution remain caller-owned; the small
# research-design handoff only selects an existing research contract.
_HANDLER_NAMES = (
    "plan",
    "search",
    "document_ingest",
    "read",
    "synthesize",
    "research_design",
    "experiment",
    "analysis",
    "analyze",
    "report",
    "report_audit",
)


def research_capability_names() -> tuple[str, ...]:
    """Return the names supported by the built-in research adapters."""

    return _HANDLER_NAMES


def register_research_capabilities(
    registry: CapabilityRegistry,
    *,
    names: Iterable[str] | None = None,
    replace: bool = False,
) -> tuple[str, ...]:
    """Register selected built-in adapters without changing global state.

    Registration is explicit so applications can choose a small path or
    replace one adapter with an implementation of their own. All names are
    checked before the registry is mutated, avoiding a partially registered
    selection when a name is misspelled.
    """

    selected = _normalize_names(names)
    handlers = _load_handlers(selected)
    if not replace:
        conflicts = tuple(name for name in selected if name in registry.names())
        if conflicts:
            raise ValueError(
                "Research capabilities already registered: "
                + ", ".join(conflicts)
            )
    for name in selected:
        registry.register(name, handlers[name], replace=replace)
    return selected


def _normalize_names(names: Iterable[str] | None) -> tuple[str, ...]:
    selected = (
        _HANDLER_NAMES
        if names is None
        else (str(names).strip(),)
        if isinstance(names, str)
        else tuple(str(name).strip() for name in names)
    )
    if not selected or any(not name for name in selected):
        raise ValueError("Research capability names cannot be empty.")
    if len(set(selected)) != len(selected):
        raise ValueError("Research capability names must be unique.")
    unknown = tuple(name for name in selected if name not in _HANDLER_NAMES)
    if unknown:
        raise ValueError(
            "Unknown research capability: " + ", ".join(unknown)
        )
    return selected


def _load_handlers(names: tuple[str, ...]) -> dict[str, CapabilityHandler]:
    """Load only the adapters selected by the caller."""

    handlers: dict[str, CapabilityHandler] = {}
    if "plan" in names:
        from simple_ar.research.planning.capability import run_research_plan_capability

        handlers["plan"] = run_research_plan_capability
    if "search" in names:
        from simple_ar.research.sources.capability import run_search_capability

        handlers["search"] = run_search_capability
    if "document_ingest" in names:
        from simple_ar.research.documents.ingest import run_document_ingest_capability

        handlers["document_ingest"] = run_document_ingest_capability
    if "read" in names:
        from simple_ar.research.evidence.reader import run_read_capability

        handlers["read"] = run_read_capability
    if "synthesize" in names:
        from simple_ar.research.synthesis import run_synthesis_capability

        handlers["synthesize"] = run_synthesis_capability
    if "research_design" in names:
        from simple_ar.research.design import run_research_design_capability

        handlers["research_design"] = run_research_design_capability
    if "experiment" in names:
        from simple_ar.research.experiment import run_experiment_capability

        handlers["experiment"] = run_experiment_capability
    if "analysis" in names or "analyze" in names:
        from simple_ar.research.analysis import analyze_experiment_capability

        if "analysis" in names:
            handlers["analysis"] = analyze_experiment_capability
        if "analyze" in names:
            handlers["analyze"] = analyze_experiment_capability
    if "report" in names:
        from simple_ar.report.capability import run_report_capability

        handlers["report"] = run_report_capability
    if "report_audit" in names:
        from simple_ar.report.audit import run_report_audit_capability

        handlers["report_audit"] = run_report_audit_capability
    return handlers


__all__ = ["register_research_capabilities", "research_capability_names"]
