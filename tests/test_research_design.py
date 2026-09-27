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
    def test_initial_research_design_clarifies_method_before_code_task(self):
        from unittest.mock import Mock
        from simple_ar.code_task.analysis.source_context import source_file_inventory

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "model.py").write_text("def forward(x):\n    return x\n", encoding="utf-8")
            (workspace / "data.npy").write_bytes(b"not source")
            index = source_file_inventory(workspace)
            self.assertEqual([row["path"] for row in index["files"]], ["model.py"])
            client = Mock()
            client.ask_json.side_effect = [
                {"selected_idea_id": "idea-002", "rationale": "Test a bounded mechanism.",
                 "execution_protocol": {}},
                {"status": "ready", "implementation_spec":
                    "Observe a changed forward output on a small input; keep the accepted evaluator.",
                 "unresolved_questions": [], "target_paths": ["model.py"],
                 "source_quotes": [{"path": "model.py", "quote": "return x"}]},
                {"verdict": "accept", "issues": []},
            ]
            result = build_research_design(ResearchDesignRequest(
                synthesis=self._synthesis(), idea_id="idea-002", idea_id_is_fixed=False,
                execution_boundary={"code_task": {"code_root": str(workspace)}},
                source_workspace=workspace, source_index=index,
                use_llm=True, llm_client=client,
            ))
            self.assertEqual(result.status, "ready")
            self.assertIn("changed forward output", result.implementation_spec)
            self.assertIn("def forward", client.ask_json.call_args.args[1])

    def test_initial_design_reads_active_config_and_rejects_protected_targets(self):
        from unittest.mock import Mock
        from simple_ar.code_task.analysis.source_context import source_file_inventory

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "model.py").write_text("def build(k):\n    return k\n", encoding="utf-8")
            (workspace / "experiment.toml").write_text("[model]\nk = 32\n", encoding="utf-8")
            client = Mock()
            client.ask_json.side_effect = [
                {"selected_idea_id": "idea-002", "rationale": "Try a bounded change."},
                {"status": "ready", "implementation_spec": "Change protected config only.",
                 "unresolved_questions": [], "target_paths": ["experiment.toml"],
                 "source_quotes": [{"path": "experiment.toml", "quote": "k = 32"}]},
                {"status": "ready", "implementation_spec": "Change model.py based on the active k=32.",
                 "unresolved_questions": [], "target_paths": ["model.py"],
                 "source_quotes": [{"path": "experiment.toml", "quote": "k = 32"},
                                   {"path": "model.py", "quote": "return k"}]},
                {"verdict": "accept", "issues": []},
            ]
            result = build_research_design(ResearchDesignRequest(
                synthesis=self._synthesis(), idea_id="idea-002", idea_id_is_fixed=False,
                execution_boundary={"code_task": {
                    "code_root": str(workspace), "allowed_patterns": ["model.py"],
                    "protected_patterns": ["experiment.toml"]},
                    "protocol": {"comparison_conditions": {"source_config": "experiment.toml"}}},
                source_workspace=workspace, source_index=source_file_inventory(workspace),
                use_llm=True, llm_client=client,
            ))
            self.assertEqual(result.status, "ready")
            self.assertEqual(client.ask_json.call_count, 4)
            self.assertIn("k = 32", client.ask_json.call_args.args[1])
            self.assertIn("not editable", client.ask_json.call_args_list[2].args[1])

    def test_initial_design_revises_when_review_finds_dormant_edit(self):
        from unittest.mock import Mock
        from simple_ar.code_task.analysis.source_context import source_file_inventory

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "model.py").write_text("def build(k=5):\n    return k\n", encoding="utf-8")
            (workspace / "experiment.toml").write_text("[model]\nk = 32\n", encoding="utf-8")
            client = Mock()
            client.ask_json.side_effect = [
                {"selected_idea_id": "idea-002", "rationale": "Try a bounded change."},
                {"status": "ready", "implementation_spec": "Change the default k=5 to 3.",
                 "unresolved_questions": [], "target_paths": ["model.py"],
                 "source_quotes": [{"path": "experiment.toml", "quote": "k = 32"},
                                   {"path": "model.py", "quote": "def build(k=5)"}]},
                {"verdict": "revise", "issues": ["Changing the default is dormant: active config supplies k=32."]},
                {"status": "blocked", "implementation_spec": "", "unresolved_questions": [
                    "A config-only change is outside the authorized edit scope."]},
            ]
            result = build_research_design(ResearchDesignRequest(
                synthesis=self._synthesis(), idea_id="idea-002", idea_id_is_fixed=False,
                execution_boundary={"code_task": {"code_root": str(workspace),
                    "allowed_patterns": ["model.py"], "protected_patterns": ["experiment.toml"]},
                    "protocol": {"comparison_conditions": {"source_config": "experiment.toml"}}},
                source_workspace=workspace, source_index=source_file_inventory(workspace),
                use_llm=True, llm_client=client,
            ))
            self.assertEqual(result.status, "blocked")
            self.assertEqual(client.ask_json.call_count, 4)
            self.assertIn("dormant", client.ask_json.call_args_list[-1].args[1])

    def test_initial_design_blocks_unreadable_active_config(self):
        from unittest.mock import Mock
        from simple_ar.code_task.analysis.source_context import source_file_inventory

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "model.py").write_text("pass\n", encoding="utf-8")
            client = Mock()
            client.ask_json.return_value = {"selected_idea_id": "idea-002", "rationale": "Try it."}
            result = build_research_design(ResearchDesignRequest(
                synthesis=self._synthesis(), idea_id="idea-002", idea_id_is_fixed=False,
                execution_boundary={"code_task": {"code_root": str(workspace)},
                    "protocol": {"comparison_conditions": {"source_config": "missing.toml"}}},
                source_workspace=workspace, source_index=source_file_inventory(workspace),
                use_llm=True, llm_client=client,
            ))
            self.assertEqual(result.status, "blocked")
            self.assertIn("source config", result.diagnostics[0])

    def test_source_inventory_retains_declared_config_beyond_generic_limit(self):
        from simple_ar.code_task.analysis.source_context import source_file_inventory

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "model.py").write_text("pass\n", encoding="utf-8")
            (workspace / "experiment.toml").write_text("[model]\nk = 32\n", encoding="utf-8")
            index = source_file_inventory(workspace, max_files=1,
                                          required_paths=("experiment.toml", "../outside.toml"))
            self.assertEqual([row["path"] for row in index["files"]], ["model.py", "experiment.toml"])

    def test_source_followup_uses_new_symbol_window_in_requested_file(self):
        from simple_ar.code_task.analysis.source_context import requested_source_context, source_file_inventory

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "model.py").write_text(
                "def forward():\n    return 1\n" + "# padding\n" * 700
                + "def forward_again():\n    return 2\n", encoding="utf-8",
            )
            (workspace / "report.json").write_text('{"forward": "irrelevant"}', encoding="utf-8")
            index = source_file_inventory(workspace)
            request = {"files": ["model.py"], "symbols": ["forward"], "query": "find forward aggregation"}
            first = requested_source_context(workspace, index, request,
                supplied=[], max_files=2, max_chars=1000)
            second = requested_source_context(workspace, index, request,
                supplied=first, max_files=2, max_chars=1000)
            self.assertEqual([row["path"] for row in second], ["model.py"])
            self.assertIn("forward_again", second[0]["text"])
            self.assertGreater(second[0]["source_offset"], first[0]["source_offset"])
            self.assertEqual(requested_source_context(workspace, index, request,
                supplied=first + second, max_files=2, max_chars=1000), [])

    def test_source_followup_uses_query_after_symbol_window_is_exhausted(self):
        from simple_ar.code_task.analysis.source_context import requested_source_context, source_file_inventory

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "model.py").write_text(
                "def forward():\n    return 1\n" + "# padding\n" * 700
                + "predictions = scores.mean(1)\n", encoding="utf-8",
            )
            index = source_file_inventory(workspace)
            request = {"files": ["model.py"], "symbols": ["Model.forward"],
                       "query": "Where are predictions aggregated?"}
            first = requested_source_context(workspace, index,
                {"files": ["model.py"], "symbols": ["Model.forward"], "query": ""},
                supplied=[], max_files=1, max_chars=1000)
            second = requested_source_context(workspace, index, request,
                supplied=first, max_files=1, max_chars=1000)
            self.assertIn("predictions = scores.mean(1)", second[0]["text"])

    def test_source_followup_skips_near_duplicate_symbol_window(self):
        from simple_ar.code_task.analysis.source_context import requested_source_context, source_file_inventory

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = ("x" * 2881 + "forward" + "x" * 3512 + "LinearEfficientEnsemble"
                      + "x" * 1930 + "forward" + "x" * 7290 + "currently" + "x" * 2701
                      + "predictions = heads.mean(1)\n")
            (workspace / "model.py").write_text(source, encoding="utf-8")
            index = source_file_inventory(workspace)
            supplied = [
                {"path": "model.py", "text": source[:6000], "source_offset": 0},
                {"path": "model.py", "text": source[6869:12869], "source_offset": 6869},
            ]
            found = requested_source_context(workspace, index,
                {"files": ["model.py"], "symbols": ["Model.forward", "LinearEfficientEnsemble"],
                 "query": "How are predictions currently aggregated?"},
                supplied=supplied, max_files=1, max_chars=6000)
            self.assertEqual(len(found), 1)
            self.assertIn("predictions = heads.mean(1)", found[0]["text"])
            self.assertGreater(found[0]["source_offset"], 10921)

    def test_exact_source_lookup_reaches_a_downstream_consumer(self):
        from simple_ar.code_task.analysis.source_context import requested_source_context, source_file_inventory

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "producer.py").write_text(
                "def forward(x):\n    return x\n" + "# pad\n" * 500, encoding="utf-8")
            (workspace / "consumer.py").write_text(
                "# pad\n" * 500 + "def evaluate(heads):\n    return heads.mean(1)\n",
                encoding="utf-8")
            index = source_file_inventory(workspace)
            request = {"files": ["producer.py", "consumer.py"],
                       "symbols": ["forward"], "query": "where does output change?",
                       "literal": "heads.mean(1)"}
            found = requested_source_context(workspace, index, request,
                supplied=[], max_files=2, max_chars=500)
            self.assertEqual([row["path"] for row in found], ["consumer.py"])
            self.assertIn("heads.mean(1)", found[0]["text"])
            self.assertGreater(found[0]["start_line"], 400)
            self.assertGreaterEqual(found[0]["end_line"], found[0]["start_line"])
            self.assertEqual(requested_source_context(workspace, index, request,
                supplied=found, max_files=2, max_chars=500), [])

    def test_initial_feasibility_requires_code_evidence_not_just_config(self):
        from unittest.mock import Mock
        from simple_ar.code_task.analysis.source_context import source_file_inventory

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "consumer.py").write_text(
                "def score(heads):\n    return heads.mean(1)\n", encoding="utf-8")
            (workspace / "experiment.toml").write_text("k = 32\n", encoding="utf-8")
            client = Mock()
            client.ask_json.side_effect = [
                {"selected_idea_id": "idea-002", "rationale": "Try an in-scope change."},
                {"status": "ready", "implementation_spec": "Change scoring aggregation.",
                 "unresolved_questions": [], "target_paths": ["consumer.py"],
                 "source_quotes": [{"path": "experiment.toml", "quote": "k = 32"}]},
                {"status": "ready", "implementation_spec": "Change scoring aggregation.",
                 "unresolved_questions": [], "target_paths": ["consumer.py"],
                 "source_quotes": [{"path": "experiment.toml", "quote": "k = 32"},
                                   {"path": "consumer.py", "quote": "heads.mean(1)"}]},
                {"verdict": "accept", "issues": []},
            ]
            result = build_research_design(ResearchDesignRequest(
                synthesis=self._synthesis(), idea_id="idea-002", idea_id_is_fixed=False,
                execution_boundary={"code_task": {"code_root": str(workspace)},
                    "protocol": {"comparison_conditions": {"source_config": "experiment.toml"}}},
                source_workspace=workspace, source_index=source_file_inventory(workspace),
                use_llm=True, llm_client=client,
            ))
            self.assertEqual(result.status, "ready")
            self.assertEqual(client.ask_json.call_count, 4)
            self.assertIn("exact observed source quote from target", client.ask_json.call_args_list[2].args[1])

    def test_initial_feasibility_reserves_budget_for_three_source_followups(self):
        from unittest.mock import Mock
        from simple_ar.code_task.analysis.source_context import source_file_inventory

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "model.py").write_text(
                "# prefix\n" * 800 + "def late_one():\n    pass\n"
                + "# middle\n" * 800 + "def late_two():\n    return 2\n"
                + "# more\n" * 800 + "def late_three():\n    return 3\n", encoding="utf-8",
            )
            (workspace / "helper.py").write_text(
                "# prefix\n" * 800 + "def late_one():\n    return 1\n", encoding="utf-8",
            )
            (workspace / "experiment.toml").write_text("[model]\nk = 32\n", encoding="utf-8")
            client = Mock()
            client.ask_json.side_effect = [
                {"selected_idea_id": "idea-002", "rationale": "Inspect the implementation."},
                {"status": "inspect_source", "context_request": {"files": ["model.py", "helper.py"],
                    "symbols": ["late_one"], "query": ""}},
                {"status": "inspect_source", "context_request": {"files": ["model.py"],
                    "symbols": ["late_two"], "query": ""}},
                {"status": "inspect_source", "context_request": {"files": ["model.py"],
                    "symbols": ["late_three"], "query": ""}},
                {"status": "ready", "implementation_spec": "Modify model.py at late_three.",
                    "target_paths": ["model.py"],
                    "source_quotes": [{"path": "experiment.toml", "quote": "k = 32"},
                                      {"path": "model.py", "quote": "def late_three()"}],
                    "unresolved_questions": []},
                {"verdict": "accept", "issues": []},
            ]
            result = build_research_design(ResearchDesignRequest(
                synthesis=self._synthesis(), idea_id="idea-002", idea_id_is_fixed=False,
                execution_boundary={"code_task": {"code_root": str(workspace),
                    "allowed_patterns": ["model.py", "helper.py"]},
                    "protocol": {"comparison_conditions": {"source_config": "experiment.toml"}}},
                source_workspace=workspace, source_index=source_file_inventory(workspace),
                use_llm=True, llm_client=client,
            ))
            self.assertEqual(result.status, "ready")
            self.assertIn("late_three", client.ask_json.call_args_list[4].args[1])

    def test_refinement_reads_real_source_and_persists_trace_on_provider_failure(self):
        from unittest.mock import Mock
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            source = "# padding\n" * 900 + "def end_task(model):\n    return model.copy()\n"
            (workspace / "lifecycle.py").write_text(source, encoding="utf-8")
            original = build_research_design(ResearchDesignRequest(synthesis=self._synthesis()))
            client = Mock()
            inspect = {"status": "inspect_source", "context_request": {
                "files": ["lifecycle.py"], "symbols": ["end_task"], "query": ""}}
            client.ask_json.side_effect = [inspect, LLMError("provider unavailable")]
            request = ResearchDesignRequest(synthesis=self._synthesis(), previous_design=original.to_handoff_dict(),
                source_workspace=workspace, source_index={"files": [{"path": "lifecycle.py"}]},
                use_llm=True, llm_client=client)
            context = CapabilityContext(store=ArtifactStore(root / "attempt"),
                attempt=AttemptManifest(attempt_id="design-1", capability="research_design"))
            with self.assertRaisesRegex(LLMError, "provider unavailable"):
                run_research_design_capability(context=context, request=request)
            trace = json.loads((root / "attempt" / "design_refinement_trace.json").read_text())
            excerpt = trace["turns"][0]["source_excerpts"][0]
            self.assertIn("def end_task", excerpt["text"])
            self.assertTrue(excerpt["truncated"])
            self.assertIn("def end_task", client.ask_json.call_args.args[1])
            self.assertEqual((workspace / "lifecycle.py").read_text(), source)
            # A repeated request terminates rather than looping on unchanged evidence.
            client.ask_json.side_effect = [inspect, inspect]
            self.assertEqual(build_research_design(request).status, "blocked")

    def test_refinement_reselects_only_available_candidates_with_same_protocol(self):
        from unittest.mock import Mock
        synthesis = self._synthesis()
        original = build_research_design(ResearchDesignRequest(synthesis=synthesis))
        alternate = replace(original.selected_idea, idea_id="alternate", title="Simpler candidate",
            hypothesis="An alternative may help", proposed_change="Use a simpler objective")
        synthesis = replace(synthesis, ideas=(*synthesis.ideas, alternate))
        client = Mock()
        client.ask_json.return_value = {"status": "ready", "implementation_spec": "Implement the simpler objective; coefficient 0.1 is our trial choice.",
            "unresolved_questions": [], "selected_idea_id": "alternate", "selection_rationale": "Original candidate is not implementable."}
        request = ResearchDesignRequest(synthesis=synthesis, previous_design=original.to_handoff_dict(), use_llm=True, llm_client=client)
        result = build_research_design(request)
        self.assertEqual(result.selected_idea, alternate)
        self.assertEqual(result.contract.hypothesis, alternate.hypothesis)
        self.assertEqual(result.execution_protocol, original.execution_protocol)
        self.assertEqual(result.contract.dataset, original.contract.dataset)
        self.assertEqual(result.contract.implementation_scope, original.contract.implementation_scope)
        self.assertIsNone(result.novelty_check)
        with self.assertRaises(LLMError):
            build_research_design(replace(request, idea_id=original.selected_idea.idea_id))
        different_data = replace(alternate, required_datasets=["other"])
        self.assertEqual(build_research_design(replace(request,
            synthesis=replace(synthesis, ideas=(different_data,)))).status, "blocked")

    def test_measured_revision_cannot_reselect_a_tried_or_evidence_blocked_idea(self):
        from unittest.mock import Mock

        synthesis = self._synthesis()
        original = build_research_design(ResearchDesignRequest(synthesis=synthesis))
        alternate = replace(original.selected_idea, idea_id="alternate", title="Distinct candidate")
        synthesis = replace(synthesis, ideas=(*synthesis.ideas, alternate))
        client = Mock()
        client.ask_json.return_value = {"status": "ready", "implementation_spec": "Implement the selected idea.",
            "unresolved_questions": [], "selected_idea_id": "alternate", "selection_rationale": "Test a distinct direction."}
        feedback = {"kind": "research_revision", "candidate_options": [
            {"idea_id": original.selected_idea.idea_id, "selected": True, "tried": True, "assessment_status": "ready"},
            {"idea_id": "alternate", "selected": False, "tried": False, "assessment_status": "ready"},
        ]}
        request = ResearchDesignRequest(synthesis=synthesis, previous_design=original.to_handoff_dict(),
            implementation_feedback=feedback, use_llm=True, llm_client=client)
        self.assertEqual(build_research_design(request).selected_idea.idea_id, "alternate")
        client.ask_json.return_value["selected_idea_id"] = original.selected_idea.idea_id
        self.assertEqual(build_research_design(request).status, "blocked")
        client.ask_json.return_value["selected_idea_id"] = "alternate"
        feedback["candidate_options"][1]["assessment_status"] = "needs_evidence"
        self.assertEqual(build_research_design(replace(request, implementation_feedback=feedback)).status, "blocked")

    def test_implementation_refinement_preserves_contract_and_surfaces_missing_evidence(self):
        from unittest.mock import Mock
        original = build_research_design(ResearchDesignRequest(synthesis=self._synthesis()))
        client = Mock()
        request = ResearchDesignRequest(synthesis={}, previous_design=original.to_handoff_dict(),
            implementation_feedback={"questions": ["When is the checkpoint updated?"]},
            use_llm=True, llm_client=client)
        client.ask_json.return_value = {"status": "ready", "implementation_spec": "Chosen engineering detail: snapshot after end_task.",
                                      "unresolved_questions": [], "execution_protocol": {"command": ["unapproved"]}}
        refined = build_research_design(request)
        self.assertEqual(refined.contract, original.contract)
        self.assertEqual(refined.execution_protocol, original.execution_protocol)
        self.assertIn("end_task", ResearchDesignResult.from_handoff_dict(refined.to_handoff_dict()).implementation_spec)
        client.ask_json.return_value = {"status": "blocked", "implementation_spec": "", "unresolved_questions": ["Need the actual method definition."]}
        blocked = build_research_design(request)
        self.assertEqual(blocked.status, "blocked")
        self.assertEqual(blocked.diagnostics, ("Need the actual method definition.",))
        client.ask_json.return_value = {"status": "ready", "implementation_spec": "Guess", "unresolved_questions": ["Unknown shape"]}
        with self.assertRaises(LLMError):
            build_research_design(request)

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

    def test_prepared_design_preserves_explicit_evaluation_facts(self):
        declared = {"dataset": "user-data", "dataset_refs": [{"asset_id": "user-data"}],
                    "split_spec": {"split": "fixed-validation"},
                    "comparison_conditions": {"seed": 0, "epochs": 1}}
        result = build_research_design(ResearchDesignRequest(
            synthesis=self._synthesis(), idea_id="idea-002",
            execution_schema={"required_metrics": ["accuracy"]},
            execution_boundary={"code_task": {"code_root": "prepared"}, "protocol": declared},
            execution_context="Prepared user project",
        ))
        for name, value in declared.items():
            self.assertEqual(getattr(result.contract, name), value)
        self.assertEqual(result.contract.metric_specs, [{"name": "accuracy"}])

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
