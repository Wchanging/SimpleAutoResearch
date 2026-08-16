from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

from simple_ar.core.artifacts import (
    read_json as read_json_file,
    read_text as read_text_file,
    write_json as write_json_file,
    write_text as write_text_file,
)


ArtifactStatus = Literal["available", "missing", "not_rendered", "failed"]
CapabilityStatus = Literal["completed", "partial", "failed", "blocked"]
AttemptStatus = Literal["created", "running", "completed", "failed", "blocked"]

_ARTIFACT_STATUSES = {"available", "missing", "not_rendered", "failed"}
_CAPABILITY_STATUSES = {"completed", "partial", "failed", "blocked"}
_ATTEMPT_STATUSES = {"created", "running", "completed", "failed", "blocked"}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_relative_path(root: Path, path: str | Path) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Artifact path must be inside the store root.") from exc
    if ".." in candidate.parts:
        raise ValueError("Artifact path cannot escape the store root.")
    normalized = candidate.as_posix()
    if not normalized or normalized == ".":
        raise ValueError("Artifact path must name a file or directory.")
    return normalized


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """Small reference to an artifact owned by one run or attempt."""

    path: str
    kind: str = "artifact"
    schema: str | None = None
    producer: str | None = None
    status: ArtifactStatus = "available"

    def __post_init__(self) -> None:
        if not self.path or Path(self.path).is_absolute() or ".." in Path(self.path).parts:
            raise ValueError("ArtifactRef.path must be a non-empty relative path.")
        if self.status not in _ARTIFACT_STATUSES:
            raise ValueError(f"Unsupported artifact status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "schema": self.schema,
            "producer": self.producer,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArtifactRef":
        return cls(
            path=str(data["path"]),
            kind=str(data.get("kind", "artifact")),
            schema=str(data["schema"]) if data.get("schema") else None,
            producer=str(data["producer"]) if data.get("producer") else None,
            status=str(data.get("status", "available")),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    """Common result envelope for a replaceable capability."""

    status: CapabilityStatus
    artifacts: tuple[ArtifactRef, ...] = ()
    diagnostics: tuple[str, ...] = ()
    usage: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in _CAPABILITY_STATUSES:
            raise ValueError(f"Unsupported capability status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "diagnostics": list(self.diagnostics),
            "usage": dict(self.usage),
            "provenance": dict(self.provenance),
        }


class CapabilityRegistry:
    """Explicit registry for replaceable capability implementations.

    The registry intentionally has no discovery or import side effects. A
    caller registers the implementation it wants and receives the common
    ``CapabilityResult`` envelope back from ``run``.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[..., CapabilityResult]] = {}

    def register(
        self,
        name: str,
        handler: Callable[..., CapabilityResult],
        *,
        replace: bool = False,
    ) -> None:
        normalized = name.strip()
        if not normalized:
            raise ValueError("Capability name cannot be empty.")
        if normalized in self._handlers and not replace:
            raise ValueError(f"Capability already registered: {normalized}")
        self._handlers[normalized] = handler

    def resolve(self, name: str) -> Callable[..., CapabilityResult]:
        normalized = name.strip()
        try:
            return self._handlers[normalized]
        except KeyError as exc:
            raise KeyError(f"Unknown capability: {normalized}") from exc

    def run(self, name: str, *args: Any, **kwargs: Any) -> CapabilityResult:
        result = self.resolve(name)(*args, **kwargs)
        if not isinstance(result, CapabilityResult):
            raise TypeError(
                f"Capability {name.strip()} returned {type(result).__name__}; "
                "expected CapabilityResult."
            )
        return result

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))


@dataclass(frozen=True, slots=True)
class AttemptManifest:
    """Lineage metadata for one isolated capability or pipeline attempt."""

    attempt_id: str
    parent_attempt: str | None = None
    trigger: str = "initial"
    profile: str | None = None
    status: AttemptStatus = "created"
    inputs: tuple[ArtifactRef, ...] = ()
    outputs: tuple[ArtifactRef, ...] = ()
    created_at: str = field(default_factory=_utcnow_iso)
    updated_at: str = field(default_factory=_utcnow_iso)

    def __post_init__(self) -> None:
        if not self.attempt_id.strip():
            raise ValueError("AttemptManifest.attempt_id cannot be empty.")
        if self.status not in _ATTEMPT_STATUSES:
            raise ValueError(f"Unsupported attempt status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "attempt_manifest.v1",
            "attempt_id": self.attempt_id,
            "parent_attempt": self.parent_attempt,
            "trigger": self.trigger,
            "profile": self.profile,
            "status": self.status,
            "inputs": [artifact.to_dict() for artifact in self.inputs],
            "outputs": [artifact.to_dict() for artifact in self.outputs],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AttemptManifest":
        return cls(
            attempt_id=str(data["attempt_id"]),
            parent_attempt=str(data["parent_attempt"]) if data.get("parent_attempt") else None,
            trigger=str(data.get("trigger", "initial")),
            profile=str(data["profile"]) if data.get("profile") else None,
            status=str(data.get("status", "created")),  # type: ignore[arg-type]
            inputs=tuple(
                ArtifactRef.from_dict(item)
                for item in data.get("inputs", [])
                if isinstance(item, dict)
            ),
            outputs=tuple(
                ArtifactRef.from_dict(item)
                for item in data.get("outputs", [])
                if isinstance(item, dict)
            ),
            created_at=str(data.get("created_at", _utcnow_iso())),
            updated_at=str(data.get("updated_at", _utcnow_iso())),
        )


class ArtifactStore:
    """Small run-relative store used by standalone capabilities.

    It deliberately does not hash every file or scan the whole workspace.
    Producers explicitly create references for artifacts they expose.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def ref(
        self,
        path: str | Path,
        *,
        kind: str = "artifact",
        schema: str | None = None,
        producer: str | None = None,
        status: ArtifactStatus = "available",
    ) -> ArtifactRef:
        return ArtifactRef(
            path=_safe_relative_path(self.root, path),
            kind=kind,
            schema=schema,
            producer=producer,
            status=status,
        )

    def resolve(self, ref: ArtifactRef | str | Path) -> Path:
        artifact_ref = ref if isinstance(ref, ArtifactRef) else self.ref(ref)
        return self.root / artifact_ref.path

    def exists(self, ref: ArtifactRef | str | Path) -> bool:
        return self.resolve(ref).exists()

    def require(self, ref: ArtifactRef | str | Path) -> Path:
        path = self.resolve(ref)
        if not path.exists():
            raise FileNotFoundError(path)
        return path

    def read_text(self, ref: ArtifactRef | str | Path) -> str:
        return read_text_file(self.require(ref))

    def read_json(self, ref: ArtifactRef | str | Path) -> Any:
        return read_json_file(self.require(ref))

    def write_text(
        self,
        path: str | Path,
        text: str,
        *,
        kind: str = "artifact",
        schema: str | None = None,
        producer: str | None = None,
    ) -> ArtifactRef:
        ref = self.ref(path, kind=kind, schema=schema, producer=producer)
        write_text_file(self.resolve(ref), text)
        return ref

    def write_json(
        self,
        path: str | Path,
        data: Any,
        *,
        kind: str = "artifact",
        schema: str | None = None,
        producer: str | None = None,
    ) -> ArtifactRef:
        ref = self.ref(path, kind=kind, schema=schema, producer=producer)
        write_json_file(self.resolve(ref), data)
        return ref

    def write_manifest(
        self,
        artifacts: Iterable[ArtifactRef],
        *,
        path: str = "artifact_manifest.json",
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        payload: dict[str, Any] = {
            "schema_version": "artifact_manifest.v1",
            "artifacts": [artifact.to_dict() for artifact in artifacts],
        }
        if metadata:
            payload["metadata"] = dict(metadata)
        return self.write_json(
            path,
            payload,
            kind="manifest",
            schema="artifact_manifest.v1",
            producer="artifact_store",
        )

    def read_manifest(self, path: str = "artifact_manifest.json") -> list[ArtifactRef]:
        payload = self.read_json(path)
        if not isinstance(payload, dict):
            raise ValueError("Artifact manifest must be a JSON object.")
        return list(
            ArtifactRef.from_dict(item)
            for item in payload.get("artifacts", [])
            if isinstance(item, dict)
        )

    def write_attempt_manifest(
        self,
        manifest: AttemptManifest,
        *,
        path: str = "attempt_manifest.json",
    ) -> ArtifactRef:
        return self.write_json(
            path,
            manifest.to_dict(),
            kind="attempt",
            schema="attempt_manifest.v1",
            producer="artifact_store",
        )

    def read_attempt_manifest(
        self,
        path: str = "attempt_manifest.json",
    ) -> AttemptManifest:
        payload = self.read_json(path)
        if not isinstance(payload, dict):
            raise ValueError("Attempt manifest must be a JSON object.")
        return AttemptManifest.from_dict(payload)

    def new_attempt(
        self,
        attempt_id: str,
        *,
        parent_attempt: str | None = None,
        trigger: str = "manual",
        profile: str | None = None,
        inputs: Iterable[ArtifactRef] = (),
    ) -> tuple["ArtifactStore", AttemptManifest]:
        child_root = self.root / "attempts" / attempt_id
        if child_root.exists():
            raise FileExistsError(f"Attempt already exists: {child_root}")
        manifest = AttemptManifest(
            attempt_id=attempt_id,
            parent_attempt=parent_attempt,
            trigger=trigger,
            profile=profile,
            inputs=tuple(inputs),
        )
        child_store = ArtifactStore(child_root)
        child_store.write_attempt_manifest(manifest)
        return child_store, manifest
