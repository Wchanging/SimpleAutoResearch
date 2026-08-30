"""Small, named lifecycle scopes for bounded research sessions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LifecycleProfile:
    """A named allow-list of capabilities for one session path.

    A lifecycle profile describes the capabilities that may participate in a
    session; it does not execute them or impose a mandatory start point.
    ``aliases`` names fixed composite capability adapters that share the
    profile's scope without changing the stage-oriented capability list.
    """

    name: str
    capabilities: tuple[str, ...]
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Lifecycle profile name cannot be empty.")
        if not self.capabilities or any(not item.strip() for item in self.capabilities):
            raise ValueError("Lifecycle profile capabilities cannot be empty.")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("Lifecycle profile capabilities must be unique.")
        if len(set(self.aliases)) != len(self.aliases):
            raise ValueError("Lifecycle profile aliases must be unique.")
        if set(self.capabilities) & set(self.aliases):
            raise ValueError("Lifecycle profile aliases must differ from capabilities.")

    def allows(self, capability: str) -> bool:
        return capability.strip() in (*self.capabilities, *self.aliases)


_LIFECYCLE_PROFILES: tuple[LifecycleProfile, ...] = (
    LifecycleProfile(
        "research_brief",
        ("plan", "search", "document_ingest", "read", "synthesize"),
        aliases=("research_brief",),
    ),
    LifecycleProfile(
        "survey",
        ("plan", "search", "document_ingest", "read", "synthesize", "report"),
        aliases=("report_audit",),
    ),
    LifecycleProfile(
        "experiment",
        (
            "synthesize",
            "research_design",
            "design",
            "code",
            "run",
            "analysis",
            "report",
        ),
        aliases=("experiment", "analyze", "report_audit"),
    ),
    LifecycleProfile(
        "paper_audit",
        ("read", "report"),
        aliases=("report_audit",),
    ),
    LifecycleProfile(
        "full_research",
        (
            "plan",
            "search",
            "document_ingest",
            "read",
            "synthesize",
            "research_design",
            "design",
            "code",
            "run",
            "analysis",
            "report",
        ),
        aliases=("research_brief", "experiment", "analyze", "report_audit"),
    ),
)
_PROFILE_BY_NAME = {profile.name: profile for profile in _LIFECYCLE_PROFILES}


def resolve_lifecycle_profile(name: str | None) -> LifecycleProfile | None:
    """Return a built-in scope, or ``None`` for an unset/legacy profile."""

    normalized = name.strip() if name else ""
    return _PROFILE_BY_NAME.get(normalized)


def lifecycle_profile_names() -> tuple[str, ...]:
    """Return built-in names in stable documentation order."""

    return tuple(profile.name for profile in _LIFECYCLE_PROFILES)


__all__ = [
    "LifecycleProfile",
    "lifecycle_profile_names",
    "resolve_lifecycle_profile",
]
