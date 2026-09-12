from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal


DiagnosticSeverity = Literal["error", "warning", "info"]


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A visible input or asset diagnostic; it never silently changes intent."""

    code: str
    message: str
    severity: DiagnosticSeverity = "error"
    field: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "field": self.field,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Diagnostic":
        return cls(
            code=str(data.get("code") or "unknown"),
            message=str(data.get("message") or ""),
            severity=_severity(data.get("severity")),
            field=str(data.get("field") or ""),
        )


@dataclass(frozen=True, slots=True)
class ResearchAsset:
    """A normalized user or externally supplied research asset."""

    asset_id: str
    kind: str
    role: str
    locator: str
    identity: dict[str, Any] = field(default_factory=dict)
    availability: str = "unknown"
    understanding: str = "unknown"
    mutability: str = "read_only"
    allowed_uses: tuple[str, ...] = ()
    provenance: str = "user_provided"
    diagnostics: tuple[Diagnostic, ...] = ()
    schema_version: str = "research_asset.v1"

    def __post_init__(self) -> None:
        for name in ("asset_id", "kind", "role", "locator"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"ResearchAsset.{name} cannot be empty")
        object.__setattr__(self, "identity", dict(self.identity))
        object.__setattr__(self, "allowed_uses", _string_tuple(self.allowed_uses))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "kind": self.kind,
            "role": self.role,
            "locator": self.locator,
            "identity": dict(self.identity),
            "availability": self.availability,
            "understanding": self.understanding,
            "mutability": self.mutability,
            "allowed_uses": list(self.allowed_uses),
            "provenance": self.provenance,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResearchAsset":
        if data.get("schema_version", "research_asset.v1") != "research_asset.v1":
            raise ValueError(f"Unsupported research asset schema: {data.get('schema_version')!r}")
        raw_diagnostics = data.get("diagnostics", [])
        if not isinstance(raw_diagnostics, list):
            raise ValueError("ResearchAsset.diagnostics must be a list")
        return cls(
            asset_id=str(data.get("asset_id") or ""),
            kind=str(data.get("kind") or "file"),
            role=str(data.get("role") or "input"),
            locator=str(data.get("locator") or ""),
            identity=dict(data.get("identity") or {}),
            availability=str(data.get("availability") or "unknown"),
            understanding=str(data.get("understanding") or "unknown"),
            mutability=str(data.get("mutability") or "read_only"),
            allowed_uses=_string_tuple(data.get("allowed_uses")),
            provenance=str(data.get("provenance") or "user_provided"),
            diagnostics=tuple(Diagnostic.from_dict(item) for item in raw_diagnostics),
        )


@dataclass(frozen=True, slots=True)
class ResearchBrief:
    """Normalized research request before application planning begins.

    The brief preserves the user's request and explicit configuration. It does
    not infer a goal from prose and does not perform network, model, repository,
    or dependency operations.
    """

    request_text: str
    brief_id: str = "brief-001"
    revision: int = 1
    parent_revision: int | None = None
    objective: str = ""
    intents: tuple[str, ...] = ()
    requested_outputs: tuple[str, ...] = ()
    asset_ids: tuple[str, ...] = ()
    user_hypotheses: tuple[str, ...] = ()
    hard_constraints: tuple[str, ...] = ()
    preferences: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    accepted_assumptions: tuple[str, ...] = ()
    asset_requests: tuple[dict[str, Any], ...] = ()
    source_path: str | None = None
    schema_version: str = "research_brief_input.v1"

    def __post_init__(self) -> None:
        if not self.request_text.strip():
            raise ValueError("ResearchBrief.request_text cannot be empty")
        if not self.brief_id.strip():
            raise ValueError("ResearchBrief.brief_id cannot be empty")
        if self.revision < 1:
            raise ValueError("ResearchBrief.revision must be positive")
        if self.parent_revision is not None and self.parent_revision < 1:
            raise ValueError("ResearchBrief.parent_revision must be positive")
        if self.schema_version != "research_brief_input.v1":
            raise ValueError(f"Unsupported research brief schema: {self.schema_version!r}")
        for name in (
            "intents",
            "requested_outputs",
            "asset_ids",
            "user_hypotheses",
            "hard_constraints",
            "preferences",
            "open_questions",
            "accepted_assumptions",
        ):
            object.__setattr__(self, name, _string_tuple(getattr(self, name)))
        requests = tuple(_asset_request(item) for item in self.asset_requests)
        object.__setattr__(self, "asset_requests", requests)
        if not self.asset_ids:
            object.__setattr__(
                self,
                "asset_ids",
                tuple(str(item["asset_id"]) for item in requests if item.get("asset_id")),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_text": self.request_text,
            "brief_id": self.brief_id,
            "revision": self.revision,
            "parent_revision": self.parent_revision,
            "objective": self.objective,
            "intents": list(self.intents),
            "requested_outputs": list(self.requested_outputs),
            "asset_ids": list(self.asset_ids),
            "user_hypotheses": list(self.user_hypotheses),
            "hard_constraints": list(self.hard_constraints),
            "preferences": list(self.preferences),
            "open_questions": list(self.open_questions),
            "accepted_assumptions": list(self.accepted_assumptions),
            "asset_requests": [dict(item) for item in self.asset_requests],
            "source_path": self.source_path,
            "schema_version": self.schema_version,
        }

    to_row = to_dict

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResearchBrief":
        if data.get("schema_version", "research_brief_input.v1") != "research_brief_input.v1":
            raise ValueError(
                f"Unsupported research brief schema: {data.get('schema_version')!r}"
            )
        raw_requests = data.get("asset_requests", [])
        if not isinstance(raw_requests, list):
            raise ValueError("ResearchBrief.asset_requests must be a list")
        return cls(
            request_text=str(data.get("request_text") or ""),
            brief_id=str(data.get("brief_id") or "brief-001"),
            revision=_positive_int(data.get("revision"), 1),
            parent_revision=_optional_positive_int(data.get("parent_revision")),
            objective=str(data.get("objective") or ""),
            intents=_string_tuple(data.get("intents")),
            requested_outputs=_string_tuple(data.get("requested_outputs")),
            asset_ids=_string_tuple(data.get("asset_ids")),
            user_hypotheses=_string_tuple(data.get("user_hypotheses")),
            hard_constraints=_string_tuple(data.get("hard_constraints")),
            preferences=_string_tuple(data.get("preferences")),
            open_questions=_string_tuple(data.get("open_questions")),
            accepted_assumptions=_string_tuple(data.get("accepted_assumptions")),
            asset_requests=tuple(raw_requests),
            source_path=str(data.get("source_path")) if data.get("source_path") else None,
        )

    from_row = from_dict


def _asset_request(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("ResearchBrief.asset_requests items must be objects")
    locator = str(value.get("locator") or value.get("path") or "").strip()
    if not locator:
        raise ValueError("Research asset request requires locator or path")
    result = {
        "asset_id": str(value.get("asset_id") or "").strip(),
        "locator": locator,
        "kind": str(value.get("kind") or "file").strip(),
        "role": str(value.get("role") or "input").strip(),
        "mutability": str(value.get("mutability") or "read_only").strip(),
        "allowed_uses": list(_string_tuple(value.get("allowed_uses"))),
    }
    return result


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _positive_int(value: object, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Expected a positive integer") from exc
    if parsed < 1:
        raise ValueError("Expected a positive integer")
    return parsed


def _optional_positive_int(value: object) -> int | None:
    if value is None:
        return None
    return _positive_int(value, 1)


def _severity(value: object) -> DiagnosticSeverity:
    severity = str(value or "error").lower()
    if severity not in {"error", "warning", "info"}:
        return "error"
    return severity  # type: ignore[return-value]
