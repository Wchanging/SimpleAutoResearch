"""Finite, deterministic transition policy for research sessions.

The policy only validates a caller's proposed next capability.  It does not
inspect a whole run, call an LLM, or discover arbitrary paths through the
workflow.  A profile can provide a different allow-list without changing the
session controller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal


FailureKind = Literal[
    "none",
    "transient",
    "schema",
    "interface",
    "runtime",
    "metric",
    "evidence",
    "quality",
    "resource",
    "unknown",
]
TransitionAction = Literal["accept", "revise", "repair", "block"]

_FAILURE_KINDS = {
    "none",
    "transient",
    "schema",
    "interface",
    "runtime",
    "metric",
    "evidence",
    "quality",
    "resource",
    "unknown",
}

# This is an allow-list, not a graph traversal algorithm.  It reflects the
# conventional research path while permitting bounded backtracking.
_DEFAULT_EDGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("plan", ("search", "plan")),
    ("search", ("document_ingest", "read", "search")),
    ("document_ingest", ("read", "research_brief", "document_ingest")),
    ("read", ("synthesize", "search", "read", "report")),
    (
        "synthesize",
        ("design", "experiment", "search", "read", "synthesize", "report"),
    ),
    ("design", ("code", "synthesize", "design")),
    ("code", ("run", "design", "code")),
    # ``analysis`` is the canonical standalone capability name. Keep
    # ``analyze`` as a legacy session alias for older callers.
    ("run", ("analysis", "analyze", "report", "code", "design", "run")),
    ("analysis", ("report", "run", "design", "analysis", "analyze", "experiment")),
    ("analyze", ("report", "run", "design", "analyze", "analysis")),
    ("report", ("report", "report_audit", "read", "search", "synthesize", "design")),
    ("research_brief", ("experiment", "report", "research_brief")),
    ("experiment", ("analysis", "experiment", "report")),
    ("report_audit", ("report_audit", "report", "read", "search", "synthesize")),
)


@dataclass(frozen=True, slots=True)
class TransitionRecipe:
    """Explicit allow-list of source capability to next capability edges."""

    edges: tuple[tuple[str, tuple[str, ...]], ...] = _DEFAULT_EDGES
    name: str = "research-v1"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Transition recipe name cannot be empty.")
        seen_sources: set[str] = set()
        for source, targets in self.edges:
            normalized_source = source.strip()
            if not normalized_source or normalized_source in seen_sources:
                raise ValueError("Transition recipe sources must be unique and non-empty.")
            seen_sources.add(normalized_source)
            if not targets or any(not target.strip() for target in targets):
                raise ValueError("Transition recipe targets must be non-empty.")
            if len(set(targets)) != len(targets):
                raise ValueError("Transition recipe targets must be unique.")

    @classmethod
    def default(cls) -> "TransitionRecipe":
        return cls()

    def allowed_targets(self, source: str) -> tuple[str, ...]:
        normalized = source.strip()
        for edge_source, targets in self.edges:
            if edge_source == normalized:
                return targets
        # Unknown standalone capabilities may retry themselves, but cannot
        # silently jump into the research workflow.
        return (normalized,)

    def allows(self, source: str, target: str) -> bool:
        return target.strip() in self.allowed_targets(source)


@dataclass(frozen=True, slots=True)
class TransitionRequest:
    """Normalized outcome and optional semantic signals for one transition."""

    source: str
    result_status: str
    failure_kind: str | None = None
    target: str | None = None
    evidence_sufficient: bool | None = None
    hypothesis_supported: bool | None = None
    experiment_needed: bool | None = None
    report_auditable: bool | None = None
    expected_delta: str = ""
    signals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.result_status.strip():
            raise ValueError("TransitionRequest requires source and result_status.")
        if self.target is not None and not self.target.strip():
            raise ValueError("TransitionRequest.target cannot be blank.")


@dataclass(frozen=True, slots=True)
class TransitionDecision:
    """Validated transition proposal returned by the deterministic policy."""

    action: TransitionAction
    failure_kind: FailureKind
    target: str | None
    reason: str
    expected_delta: str = ""


def classify_failure(
    result_status: str,
    signals: Iterable[str] = (),
) -> FailureKind:
    """Classify common failures from explicit status and short diagnostics."""

    status = result_status.strip().lower()
    signal_text = " ".join(
        str(signal).strip().lower() for signal in signals if str(signal).strip()
    )
    text = " ".join(item for item in (status, signal_text) if item)
    if status == "completed":
        return "none"

    patterns: tuple[tuple[FailureKind, tuple[str, ...]], ...] = (
        (
            "resource",
            ("timeout", "timed out", "out of memory", "oom", "quota", "disk full", "cpu limit", "gpu limit"),
        ),
        (
            "transient",
            ("temporar", "connection", "network", "rate limit", "429", "503", "unavailable", "retry"),
        ),
        ("schema", ("schema", "json", "parse", "required field", "validation", "pydantic", "keyerror")),
        ("interface", ("attributeerror", "importerror", "module not found", "signature", "argument", "path")),
        ("metric", ("metric", "score", "baseline", "target", "below")),
        ("evidence", ("evidence", "citation", "source", "unsupported claim")),
        ("quality", ("quality", "review", "incomplete", "coherence")),
        ("runtime", ("traceback", "exception", "runtime", "exit code", "syntaxerror")),
    )
    for kind, tokens in patterns:
        if any(token in text for token in tokens):
            return kind
    if status == "partial":
        return "quality"
    if status == "failed":
        return "runtime"
    return "unknown"


class TransitionPolicy:
    """Apply the fixed recipe without making semantic decisions itself."""

    def __init__(self, recipe: TransitionRecipe | None = None) -> None:
        self.recipe = recipe or TransitionRecipe.default()

    def decide(self, request: TransitionRequest) -> TransitionDecision:
        failure_kind = self._failure_kind(request)
        target = request.target.strip() if request.target else None
        status = request.result_status.strip().lower()
        semantic_reasons: list[str] = []
        if request.evidence_sufficient is False:
            semantic_reasons.append("evidence is insufficient")
        if request.hypothesis_supported is False:
            semantic_reasons.append("hypothesis is not supported")
        if request.experiment_needed is True:
            semantic_reasons.append("additional experiment is needed")
        if request.report_auditable is False:
            semantic_reasons.append("report is not auditable")

        if status == "completed" and not semantic_reasons:
            action: TransitionAction = "accept"
            default_target = target
            reason = "Capability completed."
        elif status == "partial" or semantic_reasons:
            action = "revise"
            default_target = target or request.source.strip()
            reason = "; ".join(semantic_reasons) or "Capability completed partially."
        elif status == "failed":
            action = "revise" if failure_kind in {"metric", "evidence", "quality"} else "repair"
            default_target = target or request.source.strip()
            reason = (
                request.signals[0]
                if request.signals
                else f"Classified failure as {failure_kind}."
            )
        else:
            action = "block"
            default_target = None
            reason = f"Unsupported or blocked capability status: {request.result_status}."

        if default_target is not None and not self.recipe.allows(request.source, default_target):
            return TransitionDecision(
                action="block",
                failure_kind=failure_kind,
                target=None,
                reason=(
                    f"Transition {request.source.strip()} -> {default_target} is not allowed by "
                    f"recipe {self.recipe.name}."
                ),
                expected_delta=request.expected_delta,
            )

        if request.signals and failure_kind == "unknown":
            reason = f"{reason} Diagnostic signals were inconclusive."
        return TransitionDecision(
            action=action,
            failure_kind=failure_kind,
            target=default_target,
            reason=reason,
            expected_delta=request.expected_delta,
        )

    @staticmethod
    def _failure_kind(request: TransitionRequest) -> FailureKind:
        if request.failure_kind:
            normalized = request.failure_kind.strip().lower()
            if normalized in _FAILURE_KINDS:
                return normalized  # type: ignore[return-value]
        return classify_failure(request.result_status, (*request.signals, request.failure_kind or ""))


__all__ = [
    "FailureKind",
    "TransitionAction",
    "TransitionDecision",
    "TransitionPolicy",
    "TransitionRecipe",
    "TransitionRequest",
    "classify_failure",
]
