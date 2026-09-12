"""Stable public entry points for the shared runtime core."""

from simple_ar.core.capabilities import (
    ArtifactRef,
    ArtifactStore,
    AttemptManifest,
    CapabilityContext,
    CapabilityRegistry,
    CapabilityResult,
)
from simple_ar.core.session import (
    BudgetState,
    DecisionRecord,
    SessionController,
    SessionManifest,
)
from simple_ar.core.profiles import (
    LifecycleProfile,
    lifecycle_profile_names,
    resolve_lifecycle_profile,
)
from simple_ar.core.budget import (
    BudgetEntry,
    BudgetError,
    BudgetExceededError,
    BudgetLedger,
    BudgetConflictError,
    BudgetUnknownError,
)
from simple_ar.core.locking import SessionBusyError, SessionFileLock, SessionLockError

__all__ = [
    "ArtifactRef",
    "ArtifactStore",
    "AttemptManifest",
    "CapabilityContext",
    "CapabilityRegistry",
    "CapabilityResult",
    "BudgetState",
    "DecisionRecord",
    "SessionController",
    "SessionManifest",
    "LifecycleProfile",
    "lifecycle_profile_names",
    "resolve_lifecycle_profile",
    "BudgetEntry",
    "BudgetError",
    "BudgetExceededError",
    "BudgetLedger",
    "BudgetConflictError",
    "BudgetUnknownError",
    "SessionBusyError",
    "SessionFileLock",
    "SessionLockError",
]

