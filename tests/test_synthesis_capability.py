from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.core.capabilities import ArtifactStore, AttemptManifest, CapabilityContext
from simple_ar.integrations.llm import LLMError
from simple_ar.research import SynthesisRequest as PublicSynthesisRequest
from simple_ar.research.synthesis import (
    SynthesisRequest,
    SynthesisResult,
    run_synthesis_capability,
    synthesize_evidence,
)


def _pack() -> dict[str, object]:
    return {
        "schema_version": "evidence_pack.v1",
        "topic": "reliable coding agents",
        "coverage": {"status": "covered", "covered_facets": ["method"]},
        "counts": {"documents": 1, "chunks": 2},
        "papers": [{"id": "paper-1", "title": "Reliable agents"}],
        "paper_cards": [
            {
                "paper_id": "paper-1",
                "title": "Reliable agents",
                "method_summary": "A method improves validation.",
                "evidence_refs": ["paper-1#chunk-1"],
            }
        ],
        "claim_cards": [
            {
                "claim_id": "claim-1",
                "paper_id": "paper-1",
                "claim": "The method improves validation.",
                "evidence_refs": ["paper-1#chunk-1"],
            }
        ],
        "method_cards": [
            {
                "method_id": "method-1",
                "paper_id": "paper-1",
                "name": "validation method",
                "components": ["checker"],
                "evidence_refs": ["paper-1#chunk-1"],
            }
        ],
        "dataset_cards": [
            {
                "dataset_id": "dataset-1",
                "name": "agent benchmark",
                "metrics": ["success"],
                "evidence_refs": ["paper-1#chunk-2"],
            }
        ],
        "limitations": [],
    }


class SynthesisCapabilityTests(unittest.TestCase):
    def test_capability_can_add_explicit_llm_synthesis(self) -> None:
        class FakeClient:
            model = "fake-synthesis-model"

            def ask_json(self, system: str, user: str, *, label: str = "") -> dict[str, object]:
                self.label = label
                return {
                    "synthesis_markdown": "## Themes\n\nThe evidence describes validation-oriented agents [paper-1].",
                    "hypothesis_markdown": "## Hypothesis\n\nAdding validation should improve accuracy under the fixture metric.",
                }

        with tempfile.TemporaryDirectory() as tmp:
            context = CapabilityContext(
                store=ArtifactStore(Path(tmp)),
                attempt=AttemptManifest(
                    attempt_id="attempt-001",
                    capability="synthesis",
                ),
            )
            result = run_synthesis_capability(
                context=context,
                request=SynthesisRequest(
                    evidence_pack=_pack(),
                    use_llm=True,
                    llm_client=FakeClient(),
                ),
            )
            payload = context.store.read_json(result.artifacts[0])

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.provenance["mode"], "llm")
        self.assertEqual(payload["generation_mode"], "llm")
        self.assertGreater(len(payload["synthesis_markdown"]), 0)
        self.assertIn("Themes", payload["synthesis_markdown"])
        self.assertIn("Hypothesis", payload["hypothesis_markdown"])

    def test_llm_can_replace_rule_ideas_with_grounded_candidates(self) -> None:
        class FakeClient:
            model = "fake-synthesis-model"

            def ask_json(self, system: str, user: str, *, label: str = "") -> dict[str, object]:
                self.user = user
                return {
                    "synthesis_markdown": "## Themes\n\nValidation is the main theme.",
                    "hypothesis_markdown": "## Hypothesis\n\nA focused checker should improve success.",
                    "idea_candidates": [
                        {
                            "idea_id": "idea-llm-001",
                            "title": "Add a focused validation checker",
                            "hypothesis": "A focused checker improves the success metric.",
                            "motivation_refs": ["paper-1#chunk-1"],
                            "proposed_change": "Add the checker to the validation path.",
                            "expected_outcome": "The success metric increases.",
                            "required_baselines": ["existing baseline"],
                            "required_datasets": ["agent benchmark"],
                            "metrics": ["success"],
                            "feasibility": "high",
                            "risks": "The local fixture may be too small.",
                        }
                    ],
                }

        client = FakeClient()
        result = synthesize_evidence(
            SynthesisRequest(
                evidence_pack=_pack(),
                use_llm=True,
                llm_client=client,
            )
        )

        self.assertEqual(result.ideas[0].idea_id, "idea-llm-001")
        self.assertEqual(result.ideas[0].risks, ["The local fixture may be too small."])
        self.assertEqual(result.experiment_contract.hypothesis, result.ideas[0].hypothesis)
        self.assertEqual(result.novelty_checks[0].idea_id, "idea-llm-001")
        self.assertIn("idea_candidates", client.user)

    def test_llm_candidates_reject_unknown_evidence_references(self) -> None:
        class FakeClient:
            def ask_json(self, system: str, user: str, *, label: str = "") -> dict[str, object]:
                return {
                    "synthesis_markdown": "Evidence summary.",
                    "hypothesis_markdown": "Testable hypothesis.",
                    "idea_candidates": [
                        {
                            "idea_id": "idea-llm-001",
                            "title": "Unverifiable idea",
                            "hypothesis": "It improves success.",
                            "motivation_refs": ["invented-paper"],
                            "proposed_change": "Change the implementation.",
                        }
                    ],
                }

        with self.assertRaisesRegex(LLMError, "unknown motivation refs"):
            synthesize_evidence(
                SynthesisRequest(
                    evidence_pack=_pack(),
                    use_llm=True,
                    llm_client=FakeClient(),
                )
            )

    def test_research_package_keeps_capability_exports_lazy_but_compatible(self) -> None:
        self.assertIs(PublicSynthesisRequest, SynthesisRequest)

    def test_synthesis_returns_existing_structured_handoffs(self) -> None:
        result = synthesize_evidence(
            SynthesisRequest(evidence_pack=_pack(), idea_limit=2)
        )

        self.assertEqual(result.status, "ready")
        self.assertTrue(result.ideas)
        self.assertTrue(result.ideas[0].motivation_refs)
        self.assertEqual(len(result.novelty_checks), len(result.ideas))
        self.assertIsNotNone(result.experiment_contract)
        self.assertEqual(result.to_dict()["schema_version"], "synthesis_result.v1")

    def test_synthesis_can_stop_before_experiment_contract(self) -> None:
        result = synthesize_evidence(
            SynthesisRequest(
                evidence_pack=_pack(),
                include_experiment_contract=False,
            )
        )

        self.assertIsNone(result.experiment_contract)
        self.assertTrue(result.gap_summary.startswith("# Gap Summary"))

    def test_synthesis_handoff_round_trips_without_network_or_llm(self) -> None:
        result = synthesize_evidence(
            SynthesisRequest(evidence_pack=_pack(), idea_limit=2)
        )

        restored = SynthesisResult.from_handoff_dict(result.to_handoff_dict())

        self.assertEqual(restored.status, result.status)
        self.assertEqual(restored.gap_summary, result.gap_summary)
        self.assertEqual(restored.ideas[0].idea_id, result.ideas[0].idea_id)
        self.assertEqual(
            restored.novelty_checks[0].idea_id,
            result.novelty_checks[0].idea_id,
        )
        self.assertEqual(
            restored.experiment_contract.contract_id
            if restored.experiment_contract
            else None,
            "experiment-contract-001",
        )

    def test_synthesis_handoff_rejects_unknown_schema_or_status(self) -> None:
        with self.assertRaises(ValueError):
            SynthesisResult.from_handoff_dict({"schema_version": "other"})
        with self.assertRaises(ValueError):
            SynthesisResult.from_handoff_dict(
                {"schema_version": "synthesis_result.v1", "status": "failed"}
            )

    def test_sparse_pack_is_reviewable_not_silent_success(self) -> None:
        result = synthesize_evidence(
            SynthesisRequest(
                evidence_pack={"topic": "unknown", "counts": {"documents": 0, "chunks": 0}},
            )
        )

        self.assertEqual(result.status, "needs_review")
        self.assertTrue(result.diagnostics)
        self.assertTrue(result.ideas)

    def test_llm_mode_rejects_empty_evidence_instead_of_falling_back(self) -> None:
        class FakeClient:
            def ask_json(self, system: str, user: str, *, label: str = "") -> dict[str, object]:
                raise AssertionError("The model must not be called without evidence.")

        with self.assertRaises(LLMError):
            synthesize_evidence(
                SynthesisRequest(
                    evidence_pack={"topic": "unknown", "counts": {"documents": 0, "chunks": 0}},
                    use_llm=True,
                    llm_client=FakeClient(),
                )
            )

    def test_request_rejects_invalid_limits(self) -> None:
        with self.assertRaises(ValueError):
            SynthesisRequest(evidence_pack={}, idea_limit=0)
        with self.assertRaises(ValueError):
            SynthesisRequest(evidence_pack={}, novelty_backend=" ")

    def test_synthesis_capability_persists_complete_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = CapabilityContext(
                store=ArtifactStore(Path(tmp)),
                attempt=AttemptManifest(
                    attempt_id="attempt-001",
                    capability="synthesis",
                ),
            )
            result = run_synthesis_capability(
                context=context,
                request=SynthesisRequest(evidence_pack=_pack()),
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.artifacts[0].path, "synthesis_result.json")
            payload = context.store.read_json(result.artifacts[0])
            self.assertEqual(payload["schema_version"], "synthesis_result.v1")
            self.assertEqual(payload["ideas"][0]["idea_id"], "idea-001")
            self.assertEqual(
                payload["experiment_contract"]["contract_id"],
                "experiment-contract-001",
            )


if __name__ == "__main__":
    unittest.main()
