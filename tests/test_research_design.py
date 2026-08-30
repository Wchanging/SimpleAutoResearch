from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.core import ArtifactStore, AttemptManifest
from simple_ar.core.capabilities import CapabilityContext
from simple_ar.research.contracts import (
    IdeaCandidate,
    NoveltyCheck,
    ResearchExperimentContract,
)
from simple_ar.research.design import (
    ResearchDesignRequest,
    ResearchDesignResult,
    build_research_design,
    run_research_design_capability,
)
from simple_ar.research.synthesis import SynthesisResult


class ResearchDesignTests(unittest.TestCase):
    def test_selects_requested_idea_and_round_trips_handoff(self) -> None:
        synthesis = self._synthesis()

        result = build_research_design(
            ResearchDesignRequest(synthesis=synthesis, idea_id="idea-002")
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.generation_mode, "deterministic")
        self.assertEqual(result.selected_idea.idea_id, "idea-002")
        self.assertEqual(result.contract.contract_id, "contract-1/idea-002")
        self.assertEqual(result.contract.metrics, ["f1"])
        restored = ResearchDesignResult.from_handoff_dict(result.to_handoff_dict())
        self.assertEqual(restored.contract, result.contract)
        self.assertEqual(restored.novelty_check.idea_id, "idea-002")

    def test_non_ready_synthesis_is_not_approved(self) -> None:
        result = build_research_design(
            ResearchDesignRequest(
                synthesis=SynthesisResult(
                    status="needs_review",
                    gap_summary="insufficient evidence",
                    ideas=(),
                    novelty_checks=(),
                    diagnostics=("No source chunks are available.",),
                )
            )
        )

        self.assertEqual(result.status, "needs_review")
        self.assertIsNone(result.contract)
        self.assertIn("needs_review", result.diagnostics[0])

    def test_missing_contract_is_blocked(self) -> None:
        result = build_research_design(
            ResearchDesignRequest(
                synthesis=SynthesisResult(
                    status="ready",
                    gap_summary="ready",
                    ideas=(),
                    novelty_checks=(),
                )
            )
        )

        self.assertEqual(result.status, "blocked")
        self.assertIsNone(result.contract)

    def test_capability_persists_one_typed_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = CapabilityContext(
                store=ArtifactStore(Path(tmp)),
                attempt=AttemptManifest(
                    attempt_id="design-001",
                    capability="research_design",
                ),
            )
            result = run_research_design_capability(
                context=context,
                request=ResearchDesignRequest(synthesis=self._synthesis()),
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.artifacts[0].path, "research_design.json")
            payload = context.store.read_json(result.artifacts[0])
            self.assertEqual(payload["schema_version"], "research_design.v1")
            self.assertEqual(payload["contract"]["contract_id"], "contract-1/idea-001")

    @staticmethod
    def _synthesis() -> SynthesisResult:
        contract = ResearchExperimentContract(
            contract_id="contract-1",
            hypothesis="Validation improves reliable agent accuracy.",
            motivation_refs=["paper-1#claim-1"],
            baseline="baseline",
            dataset="fixture",
            metrics=["accuracy"],
            proposed_change="add validation",
        )
        ideas = (
            IdeaCandidate(
                idea_id="idea-001",
                title="Validation",
                hypothesis="Validation improves accuracy.",
                motivation_refs=["paper-1#claim-1"],
                proposed_change="add validation",
                metrics=["accuracy"],
            ),
            IdeaCandidate(
                idea_id="idea-002",
                title="Calibration",
                hypothesis="Calibration improves F1.",
                motivation_refs=["paper-2#claim-1"],
                proposed_change="add calibration",
                metrics=["f1"],
            ),
        )
        return SynthesisResult(
            status="ready",
            gap_summary="The fixture leaves room for validation.",
            ideas=ideas,
            novelty_checks=(
                NoveltyCheck(
                    idea_id="idea-001",
                    status="local_risk_hint",
                ),
                NoveltyCheck(
                    idea_id="idea-002",
                    status="local_risk_hint",
                ),
            ),
            experiment_contract=contract,
        )


if __name__ == "__main__":
    unittest.main()
