from __future__ import annotations

from dataclasses import dataclass
from typing import Any


VALID_BUDGET_PROFILES = {"normal", "large", "absolute"}


@dataclass(frozen=True)
class EditBudget:
    """Limits for one controlled patch proposal.

    Args:
        profile: Budget profile name.
        max_files: Maximum distinct editable files in a proposal.
        max_edits: Maximum edit records.
        max_old_chars: Maximum characters in one ``old`` block.
        max_new_chars: Maximum characters in one ``new`` block.
        max_total_edit_chars: Maximum combined ``old`` + ``new`` characters.
        max_proposal_chars: Maximum serialized proposal size.
        requires_approval: Whether this profile requires explicit override
            before edits may be accepted.
    """

    profile: str
    max_files: int
    max_edits: int
    max_old_chars: int
    max_new_chars: int
    max_total_edit_chars: int
    max_proposal_chars: int
    requires_approval: bool = False

    def to_json(self) -> dict[str, Any]:
        """Return a JSON-serializable budget description."""

        return {
            "profile": self.profile,
            "max_files": self.max_files,
            "max_edits": self.max_edits,
            "max_old_chars": self.max_old_chars,
            "max_new_chars": self.max_new_chars,
            "max_total_edit_chars": self.max_total_edit_chars,
            "max_proposal_chars": self.max_proposal_chars,
            "requires_approval": self.requires_approval,
        }


DEFAULT_EDIT_BUDGETS: dict[str, EditBudget] = {
    "normal": EditBudget(
        profile="normal",
        max_files=2,
        max_edits=4,
        max_old_chars=3000,
        max_new_chars=4000,
        max_total_edit_chars=12_000,
        max_proposal_chars=24_000,
        requires_approval=False,
    ),
    "large": EditBudget(
        profile="large",
        max_files=4,
        max_edits=8,
        max_old_chars=12_000,
        max_new_chars=16_000,
        max_total_edit_chars=48_000,
        max_proposal_chars=96_000,
        requires_approval=True,
    ),
    "absolute": EditBudget(
        profile="absolute",
        max_files=8,
        max_edits=16,
        max_old_chars=24_000,
        max_new_chars=32_000,
        max_total_edit_chars=120_000,
        max_proposal_chars=240_000,
        requires_approval=True,
    ),
}


def edit_budget_for_profile(
    profile: str | None,
    *,
    overrides: dict[str, Any] | None = None,
) -> EditBudget:
    """Resolve an edit budget profile plus optional TOML/manifest overrides."""

    normalized = (profile or "normal").strip().lower()
    if normalized not in VALID_BUDGET_PROFILES:
        normalized = "normal"
    base = DEFAULT_EDIT_BUDGETS[normalized]
    data = overrides if isinstance(overrides, dict) else {}
    return EditBudget(
        profile=normalized,
        max_files=_positive_int(data.get("max_files"), base.max_files),
        max_edits=_positive_int(data.get("max_edits"), base.max_edits),
        max_old_chars=_positive_int(data.get("max_old_chars"), base.max_old_chars),
        max_new_chars=_positive_int(data.get("max_new_chars"), base.max_new_chars),
        max_total_edit_chars=_positive_int(
            data.get("max_total_edit_chars"),
            base.max_total_edit_chars,
        ),
        max_proposal_chars=_positive_int(
            data.get("max_proposal_chars"),
            base.max_proposal_chars,
        ),
        requires_approval=base.requires_approval,
    )


def budget_profiles_json() -> dict[str, dict[str, Any]]:
    """Return all default budget profiles for prompts and manifests."""

    return {name: budget.to_json() for name, budget in DEFAULT_EDIT_BUDGETS.items()}


def _positive_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value > 0:
        return value
    return default
