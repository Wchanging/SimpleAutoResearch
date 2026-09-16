from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from simple_ar.core import ArtifactStore, AttemptManifest, CapabilityContext
from simple_ar.research.assessment import (
    IdeaAssessmentRequest,
    assess_ideas,
    run_idea_assessment_capability,
)
from simple_ar.research.contracts import ClaimCard, IdeaCandidate, NoveltyCheck, TextChunk
from simple_ar.integrations.llm import LLMError


class IdeaAssessmentTests(unittest.TestCase):
    def test_comparison_accepts_source_backed_card_but_not_unresolved_card(self):
        candidate = IdeaCandidate(idea_id="a", title="Candidate", hypothesis="Effect",
                                  proposed_change="Change training", expected_outcome="Improvement",
                                  motivation_refs=["claim-a"], metrics=["accuracy"])
        class Client:
            def ask_json(self, system, user, **kwargs):
                self.payload = json.loads(user)
                return dict(assessments=[dict(
                    idea_id="a", relevance="Relevant", differentiation="Unknown",
                    feasibility="Small", cost="One run", falsifiability="No gain",
                    recommendation="Validate", supporting_evidence_refs=["claim-a"],
                    counter_evidence_refs=[], unknowns=[])], recommended_idea_id="a",
                    recommendation_reason="Bounded validation")
        client = Client()
        from dataclasses import replace
        request = IdeaAssessmentRequest(
            candidates=(candidate,), available_evidence_refs=("claim-a", "chunk-a"),
            evidence_chunks=(TextChunk(chunk_id="chunk-a", document_id="p", text="Source"),),
            evidence_cards=(ClaimCard(claim_id="claim-a", paper_id="p", claim="Scoped finding",
                                      evidence_refs=["chunk-a"]),), llm_client=client)
        result = assess_ideas(request)
        self.assertEqual(result.recommended_idea_id, "a")
        self.assertIn("Scoped finding", client.payload["evidence"][-1]["text"])
        self.assertEqual(result.model_context[-1]["source_chunk_ids"], ["chunk-a"])
        invalid = assess_ideas(replace(request, evidence_cards=(
            replace(request.evidence_cards[0], evidence_refs=["missing"]),)))
        self.assertEqual(invalid.recommended_idea_id, "a")
        self.assertIn("deterministic readiness", invalid.recommendation_reason)
        self.assertIn("outside the supplied context", invalid.diagnostics[-1])

    def test_model_comparison_uses_shared_sources_without_upgrading_readiness(self):
        candidate = IdeaCandidate(
            idea_id="a", title="Candidate", hypothesis="A testable effect",
            proposed_change="Change training", expected_outcome="Less error",
            motivation_refs=["a1"],  # Deliberately no metric: model cannot approve readiness.
        )
        chunks = tuple(TextChunk(chunk_id=f"a{i}", document_id="paper-a", text="First paper.") for i in range(30))
        chunks += (TextChunk(chunk_id="b1", document_id="paper-b", text="Counter evidence."),)
        row = dict(
            idea_id="a", relevance="Within scope", differentiation="Not established",
            feasibility="Needs a controlled run", cost="One paired run",
            falsifiability="No gain on held-out data", recommendation="Define metric first",
            supporting_evidence_refs=["a1"], counter_evidence_refs=["b1"], unknowns=["Unknown effect"],
        )

        class Client:
            def ask_json(self, system, user, **kwargs):
                self.payload = json.loads(user)
                return dict(assessments=[row], recommended_idea_id="a", recommendation_reason="Smallest informative experiment")

        client = Client()
        request = IdeaAssessmentRequest(candidates=(candidate,), available_evidence_refs=("a1", "b1"), evidence_chunks=chunks, llm_client=client)
        result = assess_ideas(request)
        self.assertEqual(result.generation_mode, "llm")
        self.assertEqual(result.assessments[0].status, "needs_evidence")
        self.assertEqual(result.assessments[0].counter_evidence_refs, ("b1",))
        self.assertEqual(result.recommended_idea_id, "a")
        self.assertIn("b1", [chunk["chunk_id"] for chunk in client.payload["evidence"]])
        self.assertEqual(list(result.model_context), client.payload["evidence"])

        # a29 is present in storage but excluded from the actual model context.
        row["supporting_evidence_refs"] = ["a29"]
        invalid = assess_ideas(request)
        self.assertEqual(invalid.generation_mode, "deterministic_fallback")
        self.assertIsNone(invalid.recommended_idea_id)
        self.assertIn("outside the supplied context", invalid.diagnostics[-1])

    def test_model_unavailable_retains_honest_partial_assessment(self):
        class Client:
            def ask_json(self, *args, **kwargs):
                raise LLMError("provider timeout")

        candidate = IdeaCandidate(idea_id="a", title="Candidate", hypothesis="Effect",
                                  proposed_change="Change", expected_outcome="Improvement",
                                  motivation_refs=["c"], metrics=["accuracy"])
        result = assess_ideas(IdeaAssessmentRequest(
            candidates=(candidate,), available_evidence_refs=("c",),
            evidence_chunks=(TextChunk(chunk_id="c", document_id="d", text="Evidence"),),
            llm_client=Client(),
        ))
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.assessments[0].source_kind, "deterministic_readiness")
        self.assertEqual(result.recommended_idea_id, "a")
        self.assertIn("deterministic readiness", result.recommendation_reason)
        self.assertIn("provider timeout", result.diagnostics[-1])

    def test_assessment_keeps_readiness_and_unknowns_explicit(self) -> None:
        strong = IdeaCandidate(
            idea_id="idea-strong",
            title="Validate a small change",
            hypothesis="Validation improves accuracy.",
            motivation_refs=["chunk-1"],
            proposed_change="Add validation.",
            expected_outcome="Accuracy improves.",
            required_baselines=["baseline"],
            required_datasets=["digits"],
            metrics=["accuracy"],
            feasibility="medium",
        )
        weak = IdeaCandidate(
            idea_id="idea-weak",
            title="A broad direction",
            hypothesis="The direction may help.",
            motivation_refs=["missing-chunk"],
            proposed_change="Explore the direction.",
            expected_outcome="The result is clearer.",
        )

        result = assess_ideas(
            IdeaAssessmentRequest(
                candidates=(strong, weak),
                novelty_checks=(
                    NoveltyCheck(
                        idea_id="idea-strong",
                        status="local_risk_hint",
                        similar_work_refs=["paper-2"],
                    ),
                ),
                available_evidence_refs=("chunk-1",),
            )
        )

        self.assertEqual(result.status, "partial")
        by_id = {item.idea_id: item for item in result.assessments}
        self.assertEqual(by_id["idea-strong"].status, "ready")
        self.assertEqual(by_id["idea-strong"].relevance, "evidence_grounded")
        self.assertEqual(by_id["idea-strong"].differentiation, "overlap_risk")
        self.assertEqual(by_id["idea-weak"].status, "needs_evidence")
        self.assertIn("No evaluation metric is specified.", by_id["idea-weak"].unknowns)
        self.assertIn("idea-weak has unresolved evidence refs", result.diagnostics[0])

    def test_duplicate_candidate_ids_are_rejected(self) -> None:
        candidate = IdeaCandidate(
            idea_id="same",
            title="Candidate",
            hypothesis="A hypothesis.",
        )
        with self.assertRaisesRegex(ValueError, "unique"):
            assess_ideas(IdeaAssessmentRequest(candidates=(candidate, candidate)))

    def test_capability_persists_json_and_review_markdown(self) -> None:
        candidate = IdeaCandidate(
            idea_id="idea-001",
            title="Bounded validation",
            hypothesis="Validation improves accuracy.",
            motivation_refs=["chunk-1"],
            proposed_change="Add validation.",
            expected_outcome="Accuracy improves.",
            required_baselines=["baseline"],
            required_datasets=["digits"],
            metrics=["accuracy"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            context = CapabilityContext(
                store=ArtifactStore(Path(tmp)),
                attempt=AttemptManifest(
                    attempt_id="assessment-001",
                    capability="assess_ideas",
                ),
            )
            result = run_idea_assessment_capability(
                context=context,
                request=IdeaAssessmentRequest(
                    candidates=(candidate,),
                    available_evidence_refs=("chunk-1",),
                ),
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(
                [artifact.kind for artifact in result.artifacts],
                ["idea_assessment", "idea_comparison"],
            )
            payload = context.store.read_json(result.artifacts[0])
            self.assertEqual(payload["schema_version"], "idea_assessment.v1")
            self.assertEqual(payload["assessments"][0]["status"], "ready")
            markdown = context.store.read_text(result.artifacts[1])
            self.assertIn("execution-readiness only", markdown)
            self.assertIn("prepare_bounded_validation", markdown)


if __name__ == "__main__":
    unittest.main()
