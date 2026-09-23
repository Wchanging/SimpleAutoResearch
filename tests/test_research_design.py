from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
import sys

from simple_ar.core import ArtifactStore, AttemptManifest
from simple_ar.core.capabilities import CapabilityContext
from simple_ar.integrations.llm import LLMError
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
    def test_literature_baseline_does_not_request_a_control_run(self) -> None:
        result = build_research_design(ResearchDesignRequest(
            synthesis=self._synthesis(),
            execution_boundary={"command": [sys.executable, "benchmark.py"]},
        ))
        self.assertFalse(result.execution_protocol["comparison_required"])
        self.assertEqual(result.execution_protocol["baseline_policy"], "skip")

    def test_explicit_execution_boundary_overrides_conflicting_model_protocol(self) -> None:
        class FakeClient:
            model = "fake-protocol-model"

            def ask_json(self, _system: str, _user: str, *, label: str = "", **kwargs: object):
                return {
                    "selected_idea_id": "idea-002",
                    "rationale": "Use the inspected benchmark with the caller's declared conditions.",
                    "execution_protocol": {
                        "command": [sys.executable, "benchmark.py", "--batch-size", "4"],
                        "pairs": [
                            {
                                "seed": 0,
                                "baseline_command": [sys.executable, "benchmark.py", "--seed", "0"],
                                "candidate_command": [sys.executable, "benchmark.py", "--seed", "0"],
                            },
                        ],
                        "comparison_required": False,
                        "baseline_policy": "skip",
                        "result_schema": {"primary_metric": "loss"},
                    },
                }

        result = build_research_design(
            ResearchDesignRequest(
                synthesis=self._synthesis(),
                use_llm=True,
                llm_client=FakeClient(),
                execution_schema={"primary_metric": "f1"},
                execution_boundary={
                    "command": [sys.executable, "benchmark.py"],
                    "seeds": [7, 9],
                    "seed_flag": "--seed",
                    "baseline_policy": "run",
                    "result_schema": {"primary_metric": "f1"},
                },
                entry_facts={
                    "benchmark_argv": [sys.executable, "benchmark.py"],
                    "authorized_argv_prefixes": [[sys.executable, "benchmark.py"]],
                },
            )
        )

        self.assertEqual(result.execution_protocol["seeds"], [7, 9])
        self.assertNotIn("pairs", result.execution_protocol)
        self.assertEqual(result.execution_protocol["baseline_policy"], "run")
        self.assertTrue(result.execution_protocol["comparison_required"])
        self.assertEqual(result.execution_protocol["result_schema"], {"primary_metric": "f1"})

    def test_llm_design_rejects_pair_command_outside_inspected_entry(self) -> None:
        class FakeClient:
            model = "fake-protocol-model"

            def ask_json(self, _system: str, _user: str, *, label: str = "", **kwargs: object):
                return {
                    "selected_idea_id": "idea-002",
                    "rationale": "Try an uninspected command.",
                    "execution_protocol": {
                        "pairs": [
                            {
                                "seed": 0,
                                "baseline_command": [sys.executable, "other.py"],
                                "candidate_command": [sys.executable, "benchmark.py"],
                            },
                        ],
                    },
                }

        with self.assertRaises(LLMError):
            build_research_design(
                ResearchDesignRequest(
                    synthesis=self._synthesis(),
                    use_llm=True,
                    llm_client=FakeClient(),
                    entry_facts={
                        "authorized_argv_prefixes": [[sys.executable, "benchmark.py"]],
                    },
                )
            )

    def test_llm_design_binds_inspected_entry_facts_to_execution_protocol(self) -> None:
        class FakeClient:
            model = "fake-protocol-model"

            def ask_json(self, _system: str, _user: str, *, label: str = "", **kwargs: object):
                self.label = label
                return {
                    "selected_idea_id": "idea-002",
                    "rationale": "The supplied entrypoint supports a small measured comparison.",
                    "execution_protocol": {
                        "command": [sys.executable, "benchmark.py", "--batch-size", "4"],
                        "seeds": [0, 1],
                        "seed_flag": "--seed",
                        "baseline_policy": "run",
                        "comparison_required": True,
                        "decision_reason": "The selected direction needs two comparable seed conditions.",
                        "stopping_criteria": ["Stop after both bounded conditions pass."],
                    },
                }

        result = build_research_design(
            ResearchDesignRequest(
                synthesis=self._synthesis(),
                use_llm=True,
                llm_client=FakeClient(),
                execution_schema={"primary_metric": "f1"},
                execution_boundary={"result_schema": {"primary_metric": "f1"}},
                entry_facts={
                    "benchmark_argv": [sys.executable, "benchmark.py"],
                    "authorized_argv_prefixes": [[sys.executable, "benchmark.py"]],
                    "input_refs": ["preparation:entry-facts"],
                },
            )
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.execution_protocol["seeds"], [0, 1])
        self.assertEqual(result.execution_protocol["command"][-2:], ["--batch-size", "4"])
        from simple_ar.app.research_execution import merge_execution_protocol, normalize_execution_config

        execution = normalize_execution_config(merge_execution_protocol({}, result.execution_protocol))
        self.assertEqual(
            execution["pairs"][0]["baseline_command"],
            execution["pairs"][0]["candidate_command"],
        )
        self.assertEqual(result.execution_protocol["input_refs"], ["preparation:entry-facts"])

    def test_llm_mode_selects_only_an_existing_candidate(self) -> None:
        class FakeClient:
            model = "fake-design-model"

            def __init__(self) -> None:
                self.labels: list[str] = []
                self.users: list[str] = []

            def ask_json(
                self,
                _system: str,
                _user: str,
                *,
                label: str = "",
            ) -> dict[str, str]:
                self.labels.append(label)
                self.users.append(_user)
                return {
                    "selected_idea_id": "idea-002",
                    "rationale": "It has a measurable metric and a bounded change.",
                }

        client = FakeClient()
        result = build_research_design(
            ResearchDesignRequest(
                synthesis=self._synthesis(),
                topic="reliable agents",
                execution_context="Dataset: prepared digits benchmark; do not substitute another task.",
                use_llm=True,
                llm_client=client,
            )
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.generation_mode, "llm")
        self.assertEqual(result.selected_idea.idea_id, "idea-002")
        self.assertEqual(
            result.selection_rationale,
            "It has a measurable metric and a bounded change.",
        )
        self.assertEqual(client.labels, ["research-design"])
        self.assertIn("Topic: reliable agents", client.users[0])
        self.assertIn("prepared digits benchmark", client.users[0])
        restored = ResearchDesignResult.from_handoff_dict(result.to_handoff_dict())
        self.assertEqual(restored.selection_rationale, result.selection_rationale)

    def test_selects_requested_idea_and_round_trips_handoff(self) -> None:
        synthesis = self._synthesis()

        result = build_research_design(
            ResearchDesignRequest(synthesis=synthesis, idea_id="idea-002")
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.generation_mode, "deterministic")
        self.assertEqual(result.selected_idea.idea_id, "idea-002")
        self.assertEqual(result.contract.contract_id, "contract-1/idea-002")
        self.assertEqual(result.contract.baseline, "calibration-baseline")
        self.assertEqual(result.contract.metrics, ["f1"])
        self.assertEqual(result.contract.expected_outcome, "f1 improves")
        restored = ResearchDesignResult.from_handoff_dict(result.to_handoff_dict())
        self.assertEqual(restored.contract, result.contract)
        self.assertEqual(restored.novelty_check.idea_id, "idea-002")

    def test_prepared_execution_boundary_does_not_invent_protocol_facts(self) -> None:
        result = build_research_design(
            ResearchDesignRequest(
                synthesis=self._synthesis(),
                idea_id="idea-002",
                execution_schema={
                    "primary_metric": "accuracy",
                    "required_metrics": ["accuracy", "macro_f1"],
                },
                execution_boundary={"code_task": {"code_root": "examples/code_task_digits_mlp/project"}},
                execution_context=(
                    "Prepared project: examples/code_task_digits_mlp/project."
                ),
            )
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.contract.baseline, "calibration-baseline")
        self.assertEqual(result.contract.dataset, "unknown")
        self.assertEqual(result.contract.metrics, ["accuracy", "macro_f1"])
        self.assertEqual(result.contract.dataset_refs, [])
        self.assertEqual(result.contract.split_spec, {})
        self.assertEqual(result.contract.comparison_conditions, {})
        self.assertEqual(
            [item["name"] for item in result.contract.metric_specs],
            ["accuracy", "macro_f1"],
        )

    def test_default_selection_prefers_a_more_executable_candidate(self) -> None:
        synthesis = SynthesisResult(
            status="ready",
            gap_summary="The fixture contains two directions.",
            ideas=(
                IdeaCandidate(
                    idea_id="idea-001",
                    title="Underspecified direction",
                    hypothesis="A change may help.",
                    proposed_change="try a change",
                    feasibility="medium",
                ),
                IdeaCandidate(
                    idea_id="idea-002",
                    title="Grounded direction",
                    hypothesis="Validation improves accuracy.",
                    motivation_refs=["paper-1#claim-1"],
                    proposed_change="add validation",
                    expected_outcome="accuracy improves",
                    required_baselines=["baseline"],
                    required_datasets=["fixture"],
                    metrics=["accuracy"],
                    feasibility="medium",
                ),
            ),
            novelty_checks=(),
            experiment_contract=ResearchExperimentContract(
                contract_id="contract-1",
                hypothesis="A change may help.",
                proposed_change="try a change",
            ),
        )

        result = build_research_design(ResearchDesignRequest(synthesis=synthesis))

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.selected_idea.idea_id, "idea-002")
        self.assertEqual(result.contract.contract_id, "contract-1/idea-002")

    def test_llm_mode_rejects_a_candidate_outside_the_handoff(self) -> None:
        class FakeClient:
            def ask_json(
                self,
                _system: str,
                _user: str,
                *,
                label: str = "",
            ) -> dict[str, str]:
                return {
                    "selected_idea_id": "invented-idea",
                    "rationale": "This is not in the supplied candidates.",
                }

        with self.assertRaises(LLMError):
            build_research_design(
                ResearchDesignRequest(
                    synthesis=self._synthesis(),
                    use_llm=True,
                    llm_client=FakeClient(),
                )
            )

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

    def test_explicit_candidate_keeps_contract_without_approving_uncertain_science(self):
        synthesis = replace(self._synthesis(), status="needs_review", diagnostics=("Novelty is uncertain.",))
        result = build_research_design(ResearchDesignRequest(
            synthesis=synthesis, idea_id="idea-002", selection_rationale="Compare the bounded hypothesis.",
        ))
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.source_synthesis_status, "needs_review")
        self.assertEqual(result.selected_idea.idea_id, "idea-002")
        self.assertEqual(result.contract.contract_id, "contract-1/idea-002")
        self.assertIn("Novelty is uncertain.", result.diagnostics)
        missing = build_research_design(ResearchDesignRequest(synthesis=synthesis))
        self.assertIsNone(missing.contract)

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

    def test_execution_schema_mismatch_requires_review(self) -> None:
        result = build_research_design(
            ResearchDesignRequest(
                synthesis=self._synthesis(),
                execution_schema={
                    "primary_metric": "loss",
                    "required_metrics": ["loss"],
                    "metric_directions": {"loss": "lower"},
                },
            )
        )

        self.assertEqual(result.status, "needs_review")
        self.assertIsNotNone(result.contract)
        self.assertTrue(any("do not overlap" in item for item in result.diagnostics))

    def test_execution_schema_accepts_metric_embedded_in_extracted_prose(self) -> None:
        synthesis = self._synthesis()
        contract = ResearchExperimentContract(
            contract_id="contract-prose",
            hypothesis=synthesis.experiment_contract.hypothesis,
            metrics=["The fixture reports accuracy: 0.75."],
            proposed_change="add validation",
        )
        result = build_research_design(
            ResearchDesignRequest(
                synthesis=SynthesisResult(
                    status="ready",
                    gap_summary=synthesis.gap_summary,
                    ideas=(),
                    novelty_checks=(),
                    experiment_contract=contract,
                ),
                execution_schema={"primary_metric": "accuracy"},
            )
        )

        self.assertEqual(result.status, "ready")

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
            self.assertEqual(payload["contract"]["contract_id"], "contract-1/idea-002")
            self.assertEqual(payload["contract"]["baseline"], "calibration-baseline")
            self.assertEqual(payload["contract"]["expected_outcome"], "f1 improves")

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
                expected_outcome="f1 improves",
                required_baselines=["calibration-baseline"],
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
