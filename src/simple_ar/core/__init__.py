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
from simple_ar.core.transitions import (
    FailureKind,
    TransitionAction,
    TransitionDecision,
    TransitionPolicy,
    TransitionRecipe,
    TransitionRequest,
    classify_failure,
)
from simple_ar.core.profiles import (
    LifecycleProfile,
    lifecycle_profile_names,
    resolve_lifecycle_profile,
)
from simple_ar.core.session_plan import SessionStep, run_session_plan

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
    "FailureKind",
    "TransitionAction",
    "TransitionDecision",
    "TransitionPolicy",
    "TransitionRecipe",
    "TransitionRequest",
    "classify_failure",
    "LifecycleProfile",
    "lifecycle_profile_names",
    "resolve_lifecycle_profile",
    "SessionStep",
    "run_session_plan",
]

