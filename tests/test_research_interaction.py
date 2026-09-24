from __future__ import annotations

import unittest

from simple_ar.app.research_interaction import (
    apply_decision_response,
    requires_confirmation,
    response_artifact_path,
    response_matches,
)


class ResearchInteractionTests(unittest.TestCase):
    def test_modes_share_critical_gates_and_assisted_exposes_research_choices(self):
        for mode in ("assisted", "checkpoints", "autonomous", None):
            self.assertTrue(requires_confirmation(mode, "required_input"))
        for mode in ("assisted", "checkpoints"):
            self.assertTrue(requires_confirmation(mode, "execution_protocol"))
            self.assertTrue(requires_confirmation(mode, "delivery"))
        self.assertTrue(requires_confirmation("assisted", "research_choice", "supplement"))
        self.assertTrue(requires_confirmation("assisted", "research_choice", "revise_candidate"))
        self.assertFalse(requires_confirmation("checkpoints", "research_choice", "supplement"))
        self.assertTrue(requires_confirmation("checkpoints", "research_choice", "revise_candidate"))
        self.assertFalse(requires_confirmation("autonomous", "execution_protocol"))

    def test_answer_transition_preserves_proposal_and_uses_a_distinct_response_artifact(self):
        proposed = {
            "action": "request_input",
            "research_iteration": 1,
            "max_research_iterations": 3,
            "bounded_cycle": {"automatic_follow_up": False},
            "evidence_refs": [{"path": "results/analysis.json"}],
        }
        interaction = {"id": "0123456789abcdef", "stage": "research_choice",
                       "status": "pending", "proposed_action": "supplement"}
        answered, resolved = apply_decision_response(proposed, interaction, "accept", "")
        self.assertEqual(proposed["action"], "request_input")
        self.assertEqual(answered["action"], "supplement")
        self.assertEqual(answered["remaining_authorized_rounds"], 1)
        self.assertEqual(resolved["status"], "accepted")
        self.assertEqual(response_artifact_path(interaction["id"]),
                         "outputs/research-decision-0123456789abcdef-response.json")
        self.assertTrue(response_matches(resolved["response"], "accept", ""))
        self.assertTrue(response_matches(resolved["response"], "accept", "", {}))
        self.assertFalse(response_matches(
            {**resolved["response"], "revision": {"brief": {"request_text": "first"}}},
            "accept", "", {"brief": {"request_text": "second"}},
        ))
        self.assertFalse(response_matches(resolved["response"], "reject", ""))
        stopped, rejected = apply_decision_response(proposed, interaction, "reject", "")
        self.assertEqual(stopped["action"], "stop")
        self.assertEqual(stopped["evidence_refs"], proposed["evidence_refs"])
        self.assertEqual(rejected["status"], "rejected")
