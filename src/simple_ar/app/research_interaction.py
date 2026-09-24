"""Decision gates for the existing research-session execution chain."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

INTERACTION_MODES = ("assisted", "checkpoints", "autonomous")


def requires_confirmation(mode: str | None, stage: str, action: str = "") -> bool:
    """Return whether this typed decision point needs a user answer.

    Missing facts remain hard blockers in every mode. Research actions and
    milestone checkpoints are classified by their structured action/stage,
    never by matching free-text reasons.
    """

    if stage == "required_input":
        return True
    if mode == "assisted":
        return stage in {"execution_protocol", "delivery", "research_choice"} and (
            stage != "research_choice" or action in {"supplement", "revise_candidate"}
        )
    if mode == "checkpoints":
        return stage in {"execution_protocol", "delivery"} or (
            stage == "research_choice" and action == "revise_candidate"
        )
    if mode in {None, "autonomous"}:
        return False
    raise ValueError(f"Unsupported research interaction mode: {mode!r}")


def response_artifact_path(decision_id: str) -> str:
    return f"outputs/research-decision-{decision_id}-response.json"


def response_terms_match(saved: Mapping[str, Any], action: str | None, guidance: str | None) -> bool:
    return saved.get("action") == action and saved.get("guidance", "") == (guidance or "")


def response_matches(
    saved: Mapping[str, Any], action: str | None, guidance: str | None,
    revision: Mapping[str, Any] | None = None,
) -> bool:
    saved_revision = saved.get("revision") or {}
    return (
        response_terms_match(saved, action, guidance)
        and isinstance(saved_revision, Mapping)
        and dict(saved_revision) == dict(revision or {})
    )


def apply_decision_response(
    previous: Mapping[str, Any], interaction: Mapping[str, Any],
    action: str, guidance: str, *, source: str = "user",
    revision: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply a reply to the persisted decision without performing I/O."""
    decision = dict(previous)
    updated = dict(interaction)
    updated["status"] = {"accept": "accepted", "reject": "rejected", "revise": "revised"}[action]
    saved_response = {"action": action, "guidance": guidance, "source": source}
    if revision:
        saved_response["revision"] = dict(revision)
    updated["response"] = saved_response
    stage = str(updated.get("stage") or "")
    if stage == "research_choice":
        if action == "accept":
            proposed = str(updated.get("proposed_action") or "")
            if proposed not in {"supplement", "revise_candidate"}:
                raise ValueError("The pending research decision has no executable accepted action.")
            iteration = int(decision.get("research_iteration", 0)) + 1
            decision.update({
                "action": proposed,
                "accepted_action": proposed,
                "disposition": "continue_bounded",
                "remaining_authorized_rounds": max(
                    0, int(decision.get("max_research_iterations", 0))
                    - int(decision.get("research_iteration", 0)) - 1,
                ),
            })
            cycle = decision.get("bounded_cycle")
            cycle = dict(cycle) if isinstance(cycle, Mapping) else {}
            cycle.update({
                "automatic_follow_up": True,
                "next_step": (
                    f"supplement_baseline:{iteration}" if proposed == "supplement"
                    else f"prepare_candidate:{iteration}"
                ),
            })
            decision["bounded_cycle"] = cycle
        elif action == "reject":
            decision.update({
                "action": "stop",
                "accepted_action": "stop",
                "disposition": "deliver_observed_result" if decision.get("evidence_refs") else "deliver_with_limits",
                "decision_reason": "The user declined the proposed research follow-up; the measured evidence is retained.",
            })
        else:
            decision.update({"action": "revised", "accepted_action": "revised"})
    elif action == "accept":
        if decision.get("action") == "request_input":
            decision.update({"action": "continue", "accepted_action": "continue"})
    elif action == "reject":
        decision.update({
            "action": "stop",
            "accepted_action": "stop",
            "decision_reason": str(interaction.get("reason") or "The user chose to stop before the proposed action."),
        })
    else:
        decision.update({"action": "revised", "accepted_action": "revised"})
    decision["interaction"] = updated
    return decision, updated
