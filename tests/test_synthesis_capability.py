from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.core.capabilities import ArtifactStore, AttemptManifest, CapabilityContext
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
