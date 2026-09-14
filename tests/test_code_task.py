from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from simple_ar.core.artifacts import read_json, read_jsonl, read_text, write_json, write_text
from simple_ar.cli import main
from simple_ar.code_task import (
    PatchValidationError,
    analyze_code_task_failure,
    apply_patch_edits,
    build_code_task_context_pack,
    build_code_task_repo_map,
    create_code_task_batch,
    execute_code_task,
    generate_code_task_work_plan,
    generate_patch_plan,
    initialize_code_task,
    locate_code_task_context,
    probe_code_task_environment,
    propose_patch_edits,
    propose_repair_edits,
    record_plan_decision,
    run_code_task_baseline,
    run_code_task_benchmark,
    validate_code_task,
)
from simple_ar.code_task.runtime.config import CodeTaskConfigError, load_code_task_init_options
from simple_ar.code_task.editing.actions import apply_repair_actions
from simple_ar.code_task.generation.dependencies import DEPENDENCY_CATALOG, build_dependency_advice
from simple_ar.code_task.generation.file_specs import infer_file_kind
from simple_ar.code_task.analysis.resource_static import analyze_resource_risks, resource_review_findings
from simple_ar.code_task.generation.generated_project_repair import (
    repair_generated_project_from_review,
    repair_generated_project_from_run_failure,
)
from simple_ar.code_task.generation.review import review_generated_project
from simple_ar.code_task.orchestration.execute import (
    _update_generated_repair_artifacts,
    _attempt_greenfield_run_repair,
)
from simple_ar.code_task.editing.patching import _write_text_atomically
from simple_ar.code_task.editing.work_plan import _normalize_work_items
from simple_ar.code_task.runtime.state import code_task_paths
from simple_ar.integrations.llm import LLMError, LLMClient, LLMSettings
from simple_ar.core.budget import BudgetLedger


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class CodeTaskTests(unittest.TestCase):
    def test_implementation_authorization_preserves_rejections_and_dry_run(self):
        from simple_ar.code_task.orchestration.execute import implement_code_task
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_toy_project(root / "project")
            write_text(root / "task.md", "Improve the existing classifier.")
            run_dir = root / "run"
            initialize_code_task(run_dir=run_dir, code_root=root / "project", task_file=root / "task.md")
            execute_code_task(run_dir, to_step="plan", baseline_policy="skip", use_llm=False)
            before = read_json(run_dir / "manifest.json")["plan"]
            result = implement_code_task(run_dir, approval_note="Isolated implementation only", use_llm=False, dry_run=True)
            self.assertEqual(result.stop_reason, "dry_run")
            self.assertEqual(read_json(run_dir / "manifest.json")["plan"], before)
            for decision, expected in (("reject", "plan_rejected"), ("revise", "plan_revision_requested")):
                record_plan_decision(run_dir, decision=decision)
                before = read_json(run_dir / "manifest.json")["plan"]
                result = implement_code_task(run_dir, approval_note="Isolated implementation only", use_llm=False)
                self.assertEqual(result.stop_reason, expected)
                self.assertEqual(read_json(run_dir / "manifest.json")["plan"], before)
            self.assertFalse((run_dir / "code_task" / "meta" / "applied_edits.json").exists())

    def test_repair_accounting_keeps_review_and_run_facts_out_of_implementation(self):
        from simple_ar.code_task.orchestration.execute import (
            _greenfield_repair_available, _record_greenfield_repair_result,
        )

        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            implementation = {"status": "review_failed", "review_status": "failed"}
            write_json(root / "manifest.json", {"workflow": "code_task", "implementation": implementation})
            for phase, status in (("review", "failed"), ("run", "patched")):
                self.assertTrue(_greenfield_repair_available(root, 1, phase=phase))
                _record_greenfield_repair_result(root, phase=phase, repair={"status": status, "changed_files": ["model.py"]})
                self.assertFalse(_greenfield_repair_available(root, 1, phase=phase))
            manifest = read_json(root / "manifest.json")
            self.assertEqual(manifest["implementation"], implementation)
            self.assertEqual(manifest["repair"]["review_repair_count"], 1)
            self.assertEqual(manifest["repair"]["run_repair_count"], 1)
            self.assertEqual(manifest["repair"]["latest_review_repair"], "code_task/meta/review_repair.json")
            self.assertNotIn("effective_status", manifest["repair"])

    def test_application_modifies_code_between_two_canonical_measurements(self):
        self._exercise_application_code_change(repair_failure=False)

    def test_application_repairs_failed_candidate_without_repeating_baseline(self):
        self._exercise_application_code_change(repair_failure=True)

    def test_application_stops_after_authorized_repair_limit(self):
        self._exercise_application_code_change(repair_failure=True, repair_succeeds=False)

    def test_application_prepares_source_project_and_resumes_without_reinitializing(self):
        self._exercise_application_code_change(repair_failure=False, prepare_source=True)

    def test_application_implements_once_after_all_paired_baselines(self):
        self._exercise_application_code_change(repair_failure=False, prepare_source=True, paired=True)

    def test_application_does_not_edit_after_a_failed_paired_baseline(self):
        self._exercise_application_code_change(repair_failure=False, prepare_source=True, paired=True, baseline_failure=True)

    def test_application_repairs_paired_candidate_without_mixing_revisions(self):
        self._exercise_application_code_change(repair_failure=True, prepare_source=True, paired=True)

    def test_application_does_not_pool_a_successful_old_seed_after_later_failure(self):
        self._exercise_application_code_change(repair_failure=True, prepare_source=True, paired=True, late_failure=True)

    def test_paired_repair_limit_keeps_failure_and_missing_seed(self):
        self._exercise_application_code_change(repair_failure=True, repair_succeeds=False, prepare_source=True, paired=True)

    def _exercise_application_code_change(self, *, repair_failure, repair_succeeds=True, prepare_source=False, paired=False, baseline_failure=False, late_failure=False):
        from dataclasses import replace
        from simple_ar.app.research_application import ResearchApplicationServices, create_session, load_session
        from simple_ar.research.workflow_contracts import ResearchBrief
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, task, code_run = root / "project", root / "task.md", root / "code-task"
            _write_toy_project(project)
            (project / "evaluate.py").write_text(
                "from spam_model import predict\nimport sys\n"
                "with open('evaluation_count.txt', 'a') as log: log.write('run\\n')\n"
                "print('accuracy:', sum(predict(x) == 'spam' for x in ('win', 'prize')) / 2)\n"
                + ("raise SystemExit(3 if sys.argv[-1] == '1' else 0)\n" if baseline_failure else ""),
                encoding="utf-8",
            )
            task.write_text("Improve spam prediction for prize messages.", encoding="utf-8")
            if not prepare_source:
                initialize_code_task(run_dir=code_run, code_root=project, task_file=task,
                                     benchmark_command="python evaluate.py")
            workspace = project if prepare_source else code_task_paths(code_run).workspace_dir
            paper = root / "paper.md"
            paper.write_text("# Keyword classifier improvement\n\nCompare a keyword classifier improvement using spam features.\n", encoding="utf-8")
            command = [sys.executable, "evaluate.py"]
            app = create_session(ResearchBrief(
                request_text="Compare a keyword classifier improvement.", requested_outputs=("experiments",),
                asset_requests=({"locator": str(paper), "role": "paper"},),
            ), root=root / "session", services=ResearchApplicationServices(max_results=1, max_attempts=24 if paired else 16, config={
                "execution": {"command": command, "baseline": {"command": command},
                              **({"pairs": [{"seed": seed, "baseline_command": command + [str(seed)],
                                               "candidate_command": command + [str(seed)]} for seed in (0, 1)]} if paired else {}),
                              "cwd": str(workspace), "timeout_sec": 5,
                              "code_task": {("code_root" if prepare_source else "run_dir"): str(project if prepare_source else code_run), "approval_note": "Authorize isolated source edits only.",
                                            "max_repairs": int(repair_failure)},
                              "result_schema": {"primary_metric": "accuracy", "direction": "higher"},
                              "protocol": {"contract_id": "keyword-pair", "hypothesis": "Improve keyword coverage.",
                                           "dataset_refs": [{"asset_id": "two-messages", "revision": "1"}],
                                           "split_spec": {"held_out": [0, 1]},
                                           "metric_specs": [{"name": "accuracy", "unit": "fraction"}],
                                           "comparison_conditions": {"seed": 0},
                                           "protected_assets": [{"asset_id": "evaluator", "path": "evaluate.py"}]}},
            }, budget_limits={"llm_requests": 12, "total_tokens": 50000,
                              "process_invocations": (5 + int(late_failure) if repair_failure else 4) if paired else 3 if repair_failure else 2,
                              "process_wall_seconds": 30 if paired else 15}))
            if prepare_source:
                app.advance(max_actions=8)
                self.assertEqual(app.view().next_action, "prepare_execution")
                with patch.object(app, "_persist_application_views", side_effect=RuntimeError("interrupted preparation")):
                    with self.assertRaisesRegex(RuntimeError, "interrupted preparation"):
                        app.advance()
                app = load_session(root / "session")
                with patch("simple_ar.research.preparation.initialize_code_task", side_effect=AssertionError("Do not prepare twice")):
                    app.advance()
                prepared = app.controller.store.read_json(app.view().state_refs["preparation"])
                code_run = Path(prepared["execution"]["code_task"]["run_dir"])
                workspace = Path(prepared["execution"]["cwd"])
                self.assertNotEqual(workspace, project)
                self.assertEqual(prepared["source_project"], str(project))
            else:
                app.advance(max_actions=9)
            if paired:
                app.advance()
            self.assertEqual(app.view().next_action, "implement", repr(app.view()))
            if baseline_failure:
                stopped = app.advance()
                self.assertEqual(stopped.status, "paused")
                self.assertIn("paired baseline failed", stopped.status_reason)
                self.assertNotIn("implementation", stopped.state_refs)
                self.assertEqual((workspace / "evaluation_count.txt").read_text().splitlines(), ["run"] * 2)
                self.assertNotIn("lowered =", (workspace / "spam_model.py").read_text())
                return
            client = LLMClient(LLMSettings(api_key="test-key", api_mode="chat", max_output_tokens=2048))
            app.services = replace(app.services, llm_client=client)
            fake = _FakeCodeTaskClient()
            payloads = [fake.ask_json("", "", label=label) for label in
                        ("code-task-work-plan", "code-task-plan", "code-task-propose-edits")]
            if repair_failure:
                broken = "(text.lower() if __import__('sys').argv[-1] == '0' else text.lowerr())" if late_failure else "text.lowerr()"
                payloads[2]["edits"][0]["new"] = payloads[2]["edits"][0]["new"].replace("text.lower()", broken)
            payloads += [{"findings": []}] * 8
            responses = [{"choices": [{"message": {"content": json.dumps(payload)}}],
                          "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50}}
                         for payload in payloads]
            with patch("simple_ar.integrations.llm._call_openai_sdk", side_effect=responses) as transport, patch.object(
                LLMClient, "from_env", side_effect=AssertionError("Use the session client"),
            ):
                with patch.object(app, "_persist_application_views", side_effect=RuntimeError("interrupted implementation")):
                    with self.assertRaisesRegex(RuntimeError, "interrupted implementation"):
                        app.advance()
            sent = json.dumps(transport.call_args_list[0].args[1], ensure_ascii=False)
            self.assertIn("Execution boundary and observed baseline", sent)
            self.assertIn("keyword-pair", sent)
            design = app.controller.store.read_json(app.controller.manifest.state_refs["design"])
            self.assertIn(design["contract"]["hypothesis"], sent)
            frozen = read_json(code_task_paths(code_run).task_dir / "research_handoff.json")
            implementation_task = (code_task_paths(code_run).task_dir / "task.md").read_text(encoding="utf-8")
            self.assertIn("Implementation-only CodeTask", implementation_task)
            self.assertIn("outer ResearchApplication owns", implementation_task)
            self.assertNotIn("complete academic Markdown paper", implementation_task)
            if paired:
                self.assertEqual(len(frozen["consumed"]["paired_baselines"]), 2)
                self.assertEqual([row["metrics"]["accuracy"] for row in frozen["consumed"]["paired_baselines"]], [0.5, 0.5])
            self.assertEqual(frozen["consumed"]["baseline"]["metrics"]["accuracy"], 0.5)
            if prepare_source:
                self.assertIn("Compare a keyword classifier improvement.", frozen["original_task"])
                self.assertNotIn("lowered =", (project / "spam_model.py").read_text())
                self.assertFalse((project / "evaluation_count.txt").exists())
            else:
                self.assertEqual(frozen["original_task"], "Improve spam prediction for prize messages.")
            app = load_session(root / "session")
            if repair_failure:
                self.assertEqual(app.advance(max_actions=2 if late_failure else 1).next_action, "matrix_repair_1" if paired else "repair:1")
                failure_key = f"matrix_candidate_{int(late_failure)}" if paired else "experiment"
                initial_failure = app.controller.store.read_json(app.view().state_refs[failure_key])
                self.assertEqual(initial_failure["execution_status"], "failed")
                app.services = replace(app.services, llm_client=client)
                repair_payload = {"summary": "Fix typo", "edits": [{"path": "spam_model.py",
                    "old": "text.lowerr()", "new": "text.lower()" if repair_succeeds else "text.lower_again()", "reason": "AttributeError"}],
                    "validation": ["Run evaluator separately"], "risks": []}
                repaired_responses = [{"choices": [{"message": {"content": json.dumps(payload)}}],
                    "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50}}
                    for payload in [repair_payload] + [{"findings": []}] * 8]
                with patch("simple_ar.integrations.llm._call_openai_sdk", side_effect=repaired_responses):
                    with patch.object(app, "_persist_application_views", side_effect=RuntimeError("interrupted repair")):
                        with self.assertRaisesRegex(RuntimeError, "interrupted repair"):
                            app.advance()
                app = load_session(root / "session")
            view = app.advance(max_actions=3 if paired else 2)
            if paired:
                self.assertEqual(view.status, "completed", view.status_reason)
                if not repair_succeeds:
                    collection = app.controller.store.read_json(view.state_refs["matrix_results"])
                    self.assertEqual(collection["candidate_revision"], 1)
                    self.assertEqual(collection["pairs"][0]["candidate"], view.state_refs["matrix_candidate_r1_0"].to_dict())
                    self.assertIsNone(collection["pairs"][1]["candidate"])
                    self.assertNotIn("matrix_repair_2", view.state_refs)
                    analysis = app.controller.store.read_json(view.state_refs["analysis"])
                    self.assertEqual(analysis["execution_status"], "incomplete")
                    self.assertEqual((workspace / "evaluation_count.txt").read_text().splitlines(), ["run"] * 4)
                    self.assertEqual(app.budget_ledger.remaining("process_invocations"), 1)
                    return
                revision = 1 if repair_failure else 0
                for i in range(2):
                    self.assertEqual(app.controller.store.read_json(view.state_refs[f"matrix_baseline_{i}"])["metrics"]["accuracy"], 0.5)
                    key = f"matrix_candidate_r{revision}_{i}" if revision else f"matrix_candidate_{i}"
                    self.assertEqual(app.controller.store.read_json(view.state_refs[key])["metrics"]["accuracy"], 1.0)
                collection = app.controller.store.read_json(view.state_refs["matrix_results"])
                self.assertEqual(collection["candidate_revision"], revision)
                implementation_key = "matrix_repair_1" if revision else "implementation"
                self.assertEqual(collection["implementation_ref"], view.state_refs[implementation_key].to_dict())
                self.assertEqual(len(collection["superseded_candidates"]), revision * (1 + int(late_failure)))
                if late_failure:
                    old_success = app.controller.store.read_json(view.state_refs["matrix_candidate_0"])
                    self.assertEqual(old_success["status"], "passed")
                    self.assertNotIn(view.state_refs["matrix_candidate_0"].to_dict(), [row["candidate"] for row in collection["pairs"]])
                self.assertEqual(len([a for a in view.attempts if a["capability"] == "implement"]), 1 + revision)
                for attempt in view.attempts:
                    if attempt["trigger"].startswith("application:matrix_candidate_"):
                        expected = "matrix_repair_1" if "_r1_" in attempt["trigger"] else "implementation"
                        self.assertIn(view.state_refs[expected].to_dict(), attempt["inputs"])
                self.assertEqual((workspace / "evaluation_count.txt").read_text().splitlines(), ["run"] * (4 + revision + int(late_failure)))
                self.assertEqual(app.budget_ledger.remaining("process_invocations"), 0)
                return
            self.assertEqual(view.status, "completed", view.status_reason)
            self.assertIn("implementation", view.state_refs)
            final_key = "experiment_repair_1" if repair_failure else "experiment"
            self.assertEqual(app.latest_experiment_ref(), view.state_refs[final_key])
            delivery = next(row for row in view.work_plan["requested_outputs"] if row["name"] == "experiments")
            self.assertEqual(delivery["artifact"], view.state_refs[final_key].to_dict())
            analysis_payload = app.controller.store.read_json(view.state_refs["analysis"])
            self.assertEqual(analysis_payload["execution_ref"], delivery["artifact"])
            snapshot = app.controller.store.read_text(app.export_session())
            self.assertIn("## Latest candidate measurement", snapshot)
            self.assertIn(view.state_refs[final_key].path, snapshot.split("## Latest candidate measurement")[1])
            comparison = app.controller.store.read_json(view.state_refs["comparison"])
            self.assertEqual(comparison["verdict"], "improved" if repair_succeeds else "inconclusive")
            report_context, report_memory = app.report_inputs()
            self.assertEqual(report_context.results["execution_status"], "passed" if repair_succeeds else "failed")
            for metric in report_context.metric_sources:
                expected_ref = view.state_refs["baseline"] if metric.label == "baseline" else (
                    view.state_refs["comparison"] if metric.label == "comparison_delta" else view.state_refs[final_key])
                self.assertEqual(metric.artifact, expected_ref.path)
            self.assertEqual(report_memory.metric_sources, report_context.metric_sources)
            self.assertEqual(report_context.results["comparisons"][0]["verdict"], comparison["verdict"])
            if repair_succeeds:
                self.assertEqual(comparison["deltas"]["accuracy"], 0.5)
            self.assertEqual((workspace / "evaluation_count.txt").read_text().splitlines(), ["run"] * (3 if repair_failure else 2))
            if repair_failure:
                self.assertEqual(app.controller.store.read_json(view.state_refs["experiment"]), initial_failure)
                self.assertIn("repair_1", view.state_refs)
                self.assertIn("experiment_repair_1", view.state_refs)
                self.assertNotIn("repair_2", view.state_refs)
                repair_artifact = app.controller.store.read_json(view.state_refs["repair_1"])
                self.assertIn("failure_evidence", repair_artifact["artifact_refs"])
                self.assertIn("repair_proposal", repair_artifact["artifact_refs"])
                expected_status = "passed" if repair_succeeds else "failed"
                self.assertEqual(app.controller.store.read_json(view.state_refs["experiment_repair_1"])["execution_status"], expected_status)
            self.assertEqual(app.budget_ledger.remaining("process_invocations"), 0)
            implementation_id = next(item["attempt_id"] for item in view.attempts if item["capability"] == "implement")
            implementation_attempt = next(item for item in view.attempts if item["attempt_id"] == implementation_id)
            self.assertIn(view.state_refs["baseline"].path, [ref["path"] for ref in implementation_attempt["inputs"]])
            model_entries = [entry for entry in app.budget_ledger.entries if "llm_requests" in entry.actual]
            self.assertGreater(len(model_entries), 2)
            implementation_ids = {item["attempt_id"] for item in view.attempts if item["capability"] == "implement"}
            self.assertTrue(all(entry.attempt_id in implementation_ids for entry in model_entries))
            self.assertIn("evaluate.py", read_json(code_run / "manifest.json")["edit_scope"]["protected_patterns"])
            # A later research revision must not silently reuse this accepted patch plan.
            from simple_ar.core.capabilities import ArtifactStore, AttemptManifest, CapabilityContext
            from simple_ar.research.implementation import ImplementationRequest, run_implementation_capability
            changed_store = ArtifactStore(root / "revised-inputs")
            design["contract"]["proposed_change"] = "A different research intervention"
            revised_design = changed_store.write_json("design.json", design, kind="research_design")
            baseline_payload = app.controller.store.read_json(view.state_refs["baseline"])
            baseline_ref = changed_store.write_json("baseline.json", baseline_payload, kind="experiment_result")
            context = CapabilityContext(store=ArtifactStore(root / "revision-attempt"),
                                        attempt=AttemptManifest("implement-revised"),
                                        inputs=(revised_design, baseline_ref), input_store=changed_store)
            request = ImplementationRequest(code_run, workspace, "Allow source edits", client,
                                            frozen["consumed"]["protocol"])
            old_task = (code_task_paths(code_run).task_dir / "task.md").read_text(encoding="utf-8")
            with patch("simple_ar.integrations.llm._call_openai_sdk", side_effect=AssertionError("No stale-plan execution")):
                with self.assertRaisesRegex(ValueError, "Research inputs changed"):
                    run_implementation_capability(context=context, request=request)
            self.assertEqual((code_task_paths(code_run).task_dir / "task.md").read_text(encoding="utf-8"), old_task)
            # The session evidence remains readable without the external CodeTask run.
            copied_session = root / "portable-session"
            shutil.copytree(root / "session", copied_session)
            code_run.rename(root / "code-task-unavailable")
            portable = load_session(copied_session)
            implementation_ref = portable.view().state_refs["implementation"]
            implementation = portable.controller.store.read_json(implementation_ref)
            self.assertEqual(implementation["artifact_base"], "attempt")
            self.assertTrue({"patch", "validation", "patch_plan", "research_handoff"}.issubset(implementation["artifact_refs"]))
            attempt_store = ArtifactStore(copied_session / Path(implementation_ref.path).parent)
            from simple_ar.core.capabilities import ArtifactRef
            for name, row in implementation["artifact_refs"].items():
                contents = attempt_store.read_text(ArtifactRef.from_dict(row))
                self.assertTrue(contents.strip(), name)
            patch_ref = ArtifactRef.from_dict(implementation["artifact_refs"]["patch"])
            self.assertIn("prize", attempt_store.read_text(patch_ref))
            self.assertEqual(portable.advance().status, "completed")

    def test_execute_keeps_shared_budget_and_local_usage_through_edits_and_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, task, run_dir = root / "project", root / "task.md", root / "run"
            _write_toy_project(project)
            task.write_text("Improve spam prediction for prize messages.", encoding="utf-8")
            initialize_code_task(run_dir=run_dir, code_root=project, task_file=task,
                                 benchmark_command="python -m unittest discover -s tests")
            ledger = BudgetLedger({"llm_requests": 12, "total_tokens": 50000,
                                   "process_invocations": 1, "process_wall_seconds": 5})
            observed = []
            client = LLMClient(LLMSettings(api_key="test-key", api_mode="chat", max_output_tokens=2048),
                               budget_ledger=ledger, budget_session_id="research", budget_attempt_id="implement-1",
                               usage_callback=observed.append)
            fake = _FakeCodeTaskClient()
            responses = [{
                "choices": [{"message": {"content": json.dumps(fake.ask_json("", "", label=label))}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
            } for label in ("code-task-work-plan", "code-task-plan", "code-task-propose-edits")]
            responses += [{
                "choices": [{"message": {"content": '{"findings": []}'}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
            } for _ in range(8)]
            with patch("simple_ar.integrations.llm._call_openai_sdk", side_effect=responses), patch.object(
                LLMClient, "from_env", side_effect=AssertionError("Injected execution must not reload credentials"),
            ):
                first = execute_code_task(run_dir, llm_client=client, baseline_policy="skip", timeout_sec=5)
                self.assertEqual(first.stop_reason, "approval_required")
                record_plan_decision(run_dir, decision="approve", note="Test authorization", reviewer="test")
                proposal = execute_code_task(run_dir, llm_client=client, baseline_policy="skip", timeout_sec=5)
                self.assertEqual(proposal.stop_reason, "proposal_review_required")
                result = execute_code_task(
                    run_dir, llm_client=client, baseline_policy="skip", timeout_sec=5,
                    to_step="run", apply_proposed_edits=True,
                    budget_ledger=ledger, session_id="research", attempt_id="implement-1",
                )
            self.assertEqual(result.stop_reason, "completed")
            self.assertGreater(len(observed), 3)  # Planning, edits and actual layered reviews.
            self.assertEqual(ledger.remaining("llm_requests"), 12 - len(observed))
            self.assertEqual(ledger.remaining("process_invocations"), 0)
            self.assertTrue(all(entry.attempt_id == "implement-1" for entry in ledger.entries))
            usage = read_jsonl(run_dir / "code_task/meta/llm_usage.jsonl")
            self.assertEqual(len(usage), len(observed))
            self.assertEqual(sum(row["label"] == "code-task-work-plan" for row in usage), 0)
            self.assertEqual(sum(row["label"] == "code-task-plan" for row in usage), 1)
            self.assertTrue(any(row["stage"] == "code_task.review" for row in usage))
            self.assertEqual(read_json(run_dir / "code_task/run/patched/execution_report.json")["status"], "passed")

    def test_atomic_text_write_retries_transient_destination_lock(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            target = Path(tmp) / "result.txt"
            target.write_text("old\n", encoding="utf-8")
            original_replace = Path.replace
            attempts = 0

            def flaky_replace(source: Path, destination: Path) -> Path:
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError("transient destination lock")
                return original_replace(source, destination)

            with patch.object(Path, "replace", new=flaky_replace):
                _write_text_atomically(target, "new\n")

            self.assertEqual(attempts, 3)
            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")

    def test_python_file_in_runtime_named_directory_remains_source(self) -> None:
        self.assertEqual(infer_file_kind("artifacts/artifacts_io.py"), "source")
        self.assertEqual(
            infer_file_kind("report/report_generator.py", "output_placeholder"),
            "source",
        )
        self.assertEqual(infer_file_kind("artifacts/results.json", "output_placeholder"), "output_placeholder")

    def test_repair_add_file_can_populate_empty_placeholder(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            target = root / "artifacts" / "artifacts_io.py"
            target.parent.mkdir(parents=True)
            target.touch()

            result = apply_repair_actions(
                root,
                [{"action": "add_file", "path": "artifacts/artifacts_io.py", "content": "def save():\n    return 1\n"}],
            )

            self.assertEqual(result["status"], "patched")
            self.assertIn("def save", read_text(target))

    def test_repair_rejects_path_below_existing_file(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            write_text(root / "artifacts_io.py", "VALUE = 1\n")

            result = apply_repair_actions(
                root,
                [
                    {
                        "action": "add_file",
                        "path": "artifacts_io.py/submission_writer.py",
                        "content": "VALUE = 2\n",
                    }
                ],
            )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["rejected_actions"][0]["reason"], "parent_path_is_file")

    def test_greenfield_review_does_not_infer_hidden_labels_from_helper_names(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            write_text(root / "main.py", "from strategies import select_qbc\n")
            write_text(
                root / "strategies.py",
                (
                    "from sklearn.linear_model import LogisticRegression\n"
                    "def _as_arrays(pool_state):\n"
                    "    return pool_state.X_train, pool_state.y_train\n"
                    "def select_qbc(pool_state, seed):\n"
                    "    X, y = _as_arrays(pool_state)\n"
                    "    model = LogisticRegression(max_iter=1000)\n"
                    "    model.fit(X, y)\n"
                    "    return 0\n"
                ),
            )

            report = review_generated_project(
                project_dir=root,
                code_artifacts={
                    "generated_files": [
                        {"path": "main.py", "line_count": 1, "mode": "llm"},
                        {"path": "strategies.py", "line_count": 8, "mode": "llm"},
                    ]
                },
                result_schema={"primary_metric": "score", "required_metrics": []},
                resource_plan={
                    "max_files": 10,
                    "max_generated_lines": 200,
                },
                contract={
                    "task": "Run a pool-based active learning query strategy benchmark with an unlabeled pool and label budget.",
                    "success_criteria": [],
                },
                use_llm=False,
            )

            categories = {item.get("category") for item in report.get("findings", [])}
            self.assertNotIn("hidden_label_acquisition_leakage", categories)
            self.assertFalse(any(item.get("severity") == "blocking" for item in report["findings"]))

    def test_greenfield_review_blocks_return_contract_mismatch(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            write_text(
                root / "producer.py",
                (
                    "from __future__ import annotations\n\n"
                    "def run_all_experiments(config):\n"
                    "    bundle = {'seed_summaries': [], 'required_metrics': {}}\n"
                    "    return bundle\n"
                ),
            )
            write_text(
                root / "metrics.py",
                (
                    "from __future__ import annotations\n\n"
                    "from typing import Sequence\n\n"
                    "class SeedSummary:\n"
                    "    pass\n\n"
                    "def aggregate_seed_summaries(seed_summaries: Sequence[SeedSummary]):\n"
                    "    return list(seed_summaries)\n"
                ),
            )
            write_text(
                root / "main.py",
                (
                    "from __future__ import annotations\n\n"
                    "from producer import run_all_experiments\n"
                    "from metrics import aggregate_seed_summaries\n\n"
                    "def main():\n"
                    "    seed_summaries = run_all_experiments({})\n"
                    "    return aggregate_seed_summaries(seed_summaries)\n"
                ),
            )

            report = review_generated_project(
                project_dir=root,
                code_artifacts={
                    "generated_files": [
                        {"path": "producer.py", "mode": "llm"},
                        {"path": "metrics.py", "mode": "llm"},
                        {"path": "main.py", "mode": "llm"},
                    ]
                },
                result_schema={"primary_metric": "accuracy", "required_metrics": ["accuracy"]},
                resource_plan={"max_files": 10, "max_generated_lines": 300},
                contract={"task": "Run an experiment and aggregate seed summaries."},
                use_llm=False,
            )

            categories = {item.get("category") for item in report.get("findings", [])}
            self.assertIn("return_contract_mismatch", categories)

    def test_resource_static_reports_observations_without_model_specific_policy(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            write_text(
                root / "model_loop.py",
                (
                    "from sklearn.linear_model import LogisticRegression\n"
                    "def run(X, y):\n"
                    "    for seed in range(5):\n"
                    "        model = LogisticRegression(max_iter=1000)\n"
                    "        model.fit(X, y)\n"
                    "    return model\n"
                ),
            )

            analysis = analyze_resource_risks(root)
            self.assertEqual(analysis["nested_fit_call_count"], 1)
            self.assertNotIn("risk_score", analysis)
            findings = resource_review_findings(root)
            self.assertEqual([item["severity"] for item in findings], ["warning"])
            before = analysis
            source = read_text(root / "model_loop.py")
            write_text(root / "model_loop.py", source.replace("LogisticRegression", "AnotherEstimator") + "# StandardScaler budget cap\n")
            self.assertEqual(analyze_resource_risks(root), before)
            self.assertTrue(any(item.get("category") == "resource_fit_loop_risk" for item in findings))

    def test_repair_action_rewrite_function_preserves_method_indentation(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            module = root / "worker.py"
            write_text(
                module,
                (
                    "class Worker:\n"
                    "    def run(self):\n"
                    "        return 1\n"
                ),
            )

            result = apply_repair_actions(
                root,
                [
                    {
                        "action": "rewrite_function",
                        "path": "worker.py",
                        "function_name": "run",
                        "new_source": "def run(self):\n    return 2\n",
                        "rationale": "Fix method body.",
                    }
                ],
            )

            self.assertEqual(result["status"], "patched")
            self.assertIn("    def run(self):\n        return 2", read_text(module))

    def test_dependency_advice_scans_environment_beyond_static_hints(self) -> None:
        base = build_dependency_advice("Use pydantic if available for schema validation.")
        self.assertEqual(base["selection_policy"], "dynamic_environment_scan_plus_semantic_hints")
        self.assertGreater(base["environment_package_count"], 0)
        self.assertTrue(base["environment_packages"])
        self.assertTrue(
            any(
                row.get("package", "").lower() == "pydantic" and row.get("status") == "installed"
                for row in base["packages"]
            )
        )

        hinted = {candidate.package.lower() for candidate in DEPENDENCY_CATALOG}
        dynamic_package = next(
            (
                str(row["package"])
                for row in base["environment_packages"]
                if row.get("package") and str(row["package"]).lower() not in hinted
            ),
            "",
        )
        if dynamic_package:
            dynamic = build_dependency_advice(f"Use {dynamic_package} if available for this task.")
            self.assertTrue(
                any(
                    str(row.get("package", "")).lower() == dynamic_package.lower()
                    and row.get("status") == "installed"
                    for row in dynamic["packages"]
                )
            )

    def test_greenfield_review_repair_regenerates_fallback_core_file(self) -> None:
        class FakeClient:
            def ask_json(self, _system: str, _prompt: str, *, label: str = "") -> dict[str, str]:
                self.label = label
                return {
                    "content": (
                        "from __future__ import annotations\n\n"
                        "def run_experiment() -> dict[str, float]:\n"
                        "    return {'accuracy': 1.0}\n"
                    ),
                    "summary": "Regenerated runner with real metric path.",
                }

        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project = root / "generated_project"
            runner = project / "generated_experiment" / "runner.py"
            runner.parent.mkdir(parents=True)
            write_text(runner, "from __future__ import annotations\n\n# Reserved generated module.\n")
            review = {
                "status": "failed",
                "findings": [
                    {
                        "category": "mixed_generation_fallback",
                        "summary": "Core file `generated_experiment/runner.py` fell back while related files were LLM-generated.",
                    }
                ],
            }
            artifacts = {
                "generated_files": [
                    {"path": "generated_experiment/runner.py", "mode": "fallback", "line_count": 3}
                ]
            }
            architecture = {
                "files": [
                    {
                        "path": "generated_experiment/runner.py",
                        "purpose": "Single authoritative orchestrator.",
                        "dependencies": [],
                        "public_api": ["run_experiment() -> dict[str, float]"],
                    }
                ]
            }

            result = repair_generated_project_from_review(
                project_dir=project,
                review_report=review,
                output_path=root / "review_repair.json",
                code_artifacts=artifacts,
                architecture_plan=architecture,
                result_schema={"required_metrics": ["accuracy"]},
                contract={"objective": "Generate a runnable metric project."},
                client=FakeClient(),  # type: ignore[arg-type]
            )

            self.assertEqual(result["status"], "patched")
            self.assertEqual(result["regenerated_files"][0]["path"], "generated_experiment/runner.py")
            self.assertIn("return {'accuracy': 1.0}", read_text(runner))

    def test_rejected_repair_actions_do_not_fall_back_to_whole_file_content(self) -> None:
        class Client:
            def ask_json(self, _system, _prompt, *, label=""):
                return {
                    "actions": [
                        {"action": "replace_block", "path": "module.py", "old_string": "VALUE = 1", "new_string": "VALUE = 2"},
                        {"action": "rewrite_file", "path": "outside.py", "content": "VALUE = 3"},
                    ],
                    "content": "VALUE = 4\n",
                }

        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            write_text(project / "module.py", "VALUE = 1\n")
            result = repair_generated_project_from_review(
                project_dir=project,
                review_report={"status": "failed", "findings": [{"summary": "module.py needs repair."}]},
                output_path=root / "repair.json", client=Client(),
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["changed_files"], [])
            self.assertEqual(read_text(project / "module.py"), "VALUE = 1\n")
            self.assertFalse((project / "outside.py").exists())
            self.assertTrue(result["unresolved_errors"])

    def test_generated_repair_does_not_hide_internal_errors(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            target = project / "module.py"
            write_text(target, "VALUE = 1\n")
            client = Mock()
            kwargs = dict(
                project_dir=project,
                review_report={"status": "failed", "findings": [{"summary": "module.py needs repair."}]},
                output_path=root / "repair.json", client=client,
            )
            client.ask_json.side_effect = LLMError("provider unavailable")
            result = repair_generated_project_from_review(**kwargs)
            self.assertEqual(result["status"], "failed")
            self.assertIn("provider unavailable", result["unresolved_errors"][0])
            client.ask_json.side_effect = TypeError("invalid internal state")
            with self.assertRaisesRegex(TypeError, "invalid internal state"):
                repair_generated_project_from_review(**kwargs)
            client.ask_json.reset_mock()
            with patch("simple_ar.code_task.generation.generated_project_repair.build_review_index",
                       side_effect=RuntimeError("index construction failed")):
                with self.assertRaisesRegex(RuntimeError, "index construction failed"):
                    repair_generated_project_from_review(**kwargs)
            client.ask_json.assert_not_called()
            self.assertEqual(read_text(target), "VALUE = 1\n")

    def test_generated_repair_formats_share_validation_and_restore(self) -> None:
        cases = [
            ("print(0)\n", "print(1)\n", "patched"),
            ("VALUE = 1\n", "def broken(\n", "failed"),
            ("def evaluate():\n    return 1\n", "print(1)\n", "failed"),
        ]
        for structured in (False, True):
            for before, after, expected in cases:
                with self.subTest(structured=structured, after=after), tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
                    class Client:
                        def ask_json(self, _system, _prompt, *, label=""):
                            if structured:
                                return {"actions": [{"action": "rewrite_file", "path": "module.py", "content": after}]}
                            return {"content": after}

                    root = Path(tmp)
                    project = root / "project"
                    project.mkdir()
                    target = project / "module.py"
                    write_text(target, before)
                    result = repair_generated_project_from_review(
                        project_dir=project,
                        review_report={"status": "failed", "findings": [{"summary": "module.py needs repair."}]},
                        output_path=root / "repair.json", client=Client(),
                    )
                    self.assertEqual(result["status"], expected)
                    self.assertEqual(read_text(target), after if expected == "patched" else before)
                    self.assertEqual(result["changed_files"], ["module.py"] if expected == "patched" else [])
                    self.assertEqual(bool(result["unresolved_errors"]), expected == "failed")

    def test_generated_repair_inventory_uses_actual_files_and_explicit_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "meta"
            project = Path(tmp) / "project"
            project.mkdir()
            (project / "model.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
            (project / "README.md").write_text("Original description.\n", encoding="utf-8")
            (project / "review.md").write_text("Requested deliverable.\n", encoding="utf-8")
            write_json(meta / "code_artifacts.json", {
                "generated_files": [
                    {"path": "model.py", "mode": "fallback", "line_count": 999},
                    {"path": "README.md", "mode": "fallback", "line_count": 999},
                    {"path": "review.md", "mode": "llm", "line_count": 999},
                ],
            })
            repair = {
                "status": "failed",
                "changed_files": ["model.py", "README.md"],
                "unresolved_errors": ["another file: provider error"],
                "regenerated_files": [{
                    "path": "model.py", "mode": "llm_review_repair",
                    "line_count": 42, "summary": "Repaired model.", "public_api": [],
                }],
            }
            _update_generated_repair_artifacts(meta, project, repair)
            artifacts = read_json(meta / "code_artifacts.json")
            rows = {row["path"]: row for row in artifacts["generated_files"]}
            self.assertEqual(rows["model.py"]["mode"], "llm_review_repair")
            self.assertEqual(rows["model.py"]["line_count"], 2)
            self.assertEqual(rows["README.md"]["mode"], "fallback")
            self.assertNotIn("summary", rows["README.md"])
            self.assertIn("review.md", rows)
            self.assertEqual(artifacts["total_lines"], 4)

    def test_greenfield_review_repair_does_not_invent_resource_policy_without_llm(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project = root / "generated_project"
            package = project / "generated_experiment"
            package.mkdir(parents=True)
            resources = package / "resources.py"
            write_text(resources, "from __future__ import annotations\n\n# Reserved generated module.\n")
            review = {
                "status": "failed",
                "findings": [
                    {
                        "severity": "blocking",
                        "category": "mixed_generation_fallback",
                        "summary": "Core file `generated_experiment/resources.py` fell back.",
                    }
                ],
            }
            artifacts = {
                "generated_files": [
                    {"path": "generated_experiment/resources.py", "mode": "fallback", "line_count": 3}
                ]
            }

            repair = repair_generated_project_from_review(
                project_dir=project,
                review_report=review,
                output_path=root / "review_repair.json",
                code_artifacts=artifacts,
                client=None,
            )

            self.assertEqual(repair["status"], "skipped")
            self.assertEqual(repair["changed_files"], [])
            self.assertEqual(repair["review_status"], "failed")
            self.assertEqual(
                read_text(resources),
                "from __future__ import annotations\n\n# Reserved generated module.\n",
            )

    def test_review_repair_without_model_preserves_files_and_missing_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            write_text(project / "__init__.py", "def broken(:\n    pass\n")
            write_text(project / "helpers.py", "def _normalize(x):\n    return x\n")
            write_text(project / "consumer.py", "from helpers import normalize\n")
            before = {p.name: p.read_bytes() for p in project.iterdir()}
            review = {"status": "failed", "findings": [
                {"category": "missing_local_api", "summary": "Missing `helpers.normalize`."},
                {"category": "syntax_error", "summary": "__init__.py does not compile."},
                {"category": "missing_entrypoint", "summary": "Missing main.py."},
                {"category": "missing_required_artifact", "summary": "Missing README and config."},
            ]}
            repair = repair_generated_project_from_review(
                project_dir=project, review_report=review,
                output_path=root / "repair.json", client=None,
            )
            self.assertEqual(repair["status"], "skipped")
            self.assertEqual(repair["review_status"], "failed")
            self.assertEqual(repair["changed_files"], [])
            self.assertEqual({p.name: p.read_bytes() for p in project.iterdir()}, before)

    def test_init_copies_workspace_and_indexes_python_ast(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove the spam classifier without changing the API.\n")

            run_dir = root / "runs" / "code-task-run"
            result = initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
                max_file_bytes=10_000,
            )

            workspace = result.workspace_dir
            self.assertTrue((workspace / "spam_model.py").is_file())
            self.assertTrue((workspace / "tests" / "test_spam_model.py").is_file())
            self.assertFalse((workspace / ".env").exists())
            self.assertFalse((workspace / ".git" / "config").exists())
            self.assertEqual(
                read_text(code_root / "spam_model.py"),
                read_text(workspace / "spam_model.py"),
            )

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["workflow"], "code_task")
            self.assertEqual(manifest["layout"]["workspace"], "code_task/workspace")
            self.assertEqual(manifest["benchmark"]["executed"], False)
            self.assertEqual(manifest["environment"]["policy"]["mode"], "current")
            self.assertEqual(manifest["environment"]["policy"]["python_executable"], sys.executable)
            self.assertEqual(manifest["workspace"]["mode"], "copy")
            self.assertEqual(manifest["workspace"]["workspace_dir"], "code_task/workspace")
            self.assertEqual(manifest["edit_scope"]["mode"], "source_only_default")
            self.assertIn("tests/**", manifest["edit_scope"]["protected_patterns"])
            self.assertGreaterEqual(manifest["copy"]["skipped_count"], 2)
            task_contract = read_json(run_dir / "code_task" / "meta" / "task_contract.json")
            self.assertEqual(task_contract["schema_version"], "code_task_contract.v4")
            self.assertEqual(task_contract["task_kind"], "existing_project")
            self.assertEqual(task_contract["contract_id"], "code-task-existing_project")
            self.assertIn("version_hash", task_contract)
            self.assertEqual(task_contract["metric_contract"]["primary_metric"], "score")
            coverage = read_json(run_dir / "code_task" / "meta" / "task_contract_coverage.json")
            self.assertEqual(coverage["contract_id"], task_contract["contract_id"])

            index = read_json(result.codebase_index_path)
            self.assertEqual(index["project"]["python_file_count"], 2)
            self.assertEqual(index["project"]["test_file_count"], 1)
            spam_model = _indexed_file(index, "spam_model.py")
            self.assertIn("source", spam_model["role_tags"])
            self.assertEqual(spam_model["python"]["syntax_ok"], True)
            self.assertIn("math", spam_model["python"]["imports"])
            self.assertEqual(
                [item["name"] for item in spam_model["python"]["classes"]],
                ["SpamModel"],
            )
            self.assertEqual(
                [item["name"] for item in spam_model["python"]["functions"]],
                ["predict"],
            )
            self.assertEqual(spam_model["python"]["has_main_guard"], True)

            self.assertTrue(result.repo_map_path.is_file())
            self.assertTrue(result.repo_map_summary_path.is_file())
            repo_map = read_json(result.repo_map_path)
            self.assertEqual(repo_map["schema_version"], 1)
            self.assertEqual(repo_map["project"]["file_count"], 3)
            self.assertEqual(repo_map["project"]["python_file_count"], 2)
            self.assertEqual(repo_map["project"]["test_file_count"], 1)
            self.assertGreaterEqual(repo_map["project"]["symbol_count"], 4)
            mapped_files = {item["path"]: item for item in repo_map["files"]}
            self.assertEqual(mapped_files["spam_model.py"]["access_role"], "editable")
            self.assertEqual(
                mapped_files["tests/test_spam_model.py"]["access_role"],
                "read_only_evidence",
            )
            symbols = {item["qualified_name"] for item in repo_map["symbols"]}
            self.assertIn("SpamModel", symbols)
            self.assertIn("SpamModel.score", symbols)
            self.assertIn("predict", symbols)
            self.assertIn("SpamModelTests.test_predicts_spam_keyword", symbols)
            entrypoint_paths = {item["path"] for item in repo_map["entrypoints"]}
            self.assertIn("spam_model.py", entrypoint_paths)
            self.assertEqual(repo_map["tests"][0]["path"], "tests/test_spam_model.py")
            self.assertEqual(repo_map["configs"][0]["path"], "pyproject.toml")
            repo_summary = read_text(result.repo_map_summary_path)
            self.assertIn("# Repo Map Summary", repo_summary)
            self.assertIn("## Prompt Budget", repo_summary)

    def test_greenfield_init_uses_empty_workspace_without_code_root(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_file = root / "task.md"
            write_text(task_file, "# Task\n\nCreate a small runnable Python experiment.\n")
            config = root / "greenfield.toml"
            write_text(
                config,
                """
[code_task]
kind = "greenfield"
task_file = "task.md"
name = "greenfield-smoke"

[benchmark]
command = "python generated_project/main.py"
primary_metric = "accuracy"
""".strip(),
            )

            options = load_code_task_init_options(config_path=str(config))
            self.assertEqual(options.kind, "greenfield")
            self.assertIsNone(options.code_root)
            self.assertEqual(options.workspace_mode, "empty")

            run_dir = root / "runs" / "greenfield-run"
            result = initialize_code_task(
                run_dir=run_dir,
                code_root=None,
                task_file=task_file,
                kind=options.kind,
                benchmark_command=options.benchmark_command,
                workspace_mode=options.workspace_mode,
                primary_metric=options.primary_metric,
            )

            self.assertEqual(result.kind, "greenfield")
            self.assertTrue(result.workspace_dir.is_dir())
            self.assertEqual(list(result.workspace_dir.iterdir()), [])
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["code_task"]["kind"], "greenfield")
            self.assertEqual(manifest["workspace"]["mode"], "empty")
            self.assertEqual(manifest["source"]["code_root"], "")
            self.assertFalse((run_dir / "code_task" / "meta" / "task_contract.json").exists())

    def test_persisted_repair_counters_do_not_silently_grant_new_attempts(self) -> None:
        from simple_ar.code_task.orchestration.execute import _greenfield_repair_available

        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_file = root / "task.md"
            write_text(task_file, "Generate a small CPU experiment.")
            run_dir = root / "run"
            initialize_code_task(run_dir=run_dir, code_root=None, task_file=task_file,
                                 kind="greenfield", workspace_mode="empty")
            manifest_path = run_dir / "manifest.json"
            manifest = read_json(manifest_path)
            for value in (0, 1, -1, "invalid", None, True):
                with self.subTest(value=value):
                    manifest["repair"] = {"run_repair_count": value}
                    write_json(manifest_path, manifest)
                    before = manifest_path.read_bytes()
                    if type(value) is int and value >= 0:
                        self.assertEqual(_greenfield_repair_available(run_dir, 1, phase="run"), value == 0)
                    else:
                        with self.assertRaisesRegex(ValueError, "persisted repair counter"):
                            _greenfield_repair_available(run_dir, 1, phase="run")
                    self.assertEqual(manifest_path.read_bytes(), before)

    def test_multistep_planning_preserves_file_scope_without_inventing_modules(self) -> None:
        from simple_ar.code_task.generation.planning_tools import build_tool_agent_architecture_plan

        for paths in ([], ["main.py"], ["main.py", "custom_model.py"]):
            with self.subTest(paths=paths):
                client = Mock()
                client.ask_json.side_effect = [
                    {"objective": "Small experiment"},
                    {"architecture_summary": "A bounded experiment", "modules": [{"name": "model"}, {"name": "evaluation"}]},
                    {},
                    {"files": [{"path": path} for path in [*paths, "../escape.py"]]},
                    {"status": "needs_revision", "findings": [{
                        "severity": "critical", "target_stage": "file_plan",
                        "issue": "Generalization outside the measured domain is unknown.",
                        "required_change": "State this research limitation.",
                    }]},
                ]
                kwargs = dict(contract={}, result_schema={}, resource_plan={}, domain_profile={},
                              client=client, review_rounds=0)
                if not paths:
                    with self.assertRaisesRegex(LLMError, "no main.py entrypoint"):
                        build_tool_agent_architecture_plan(**kwargs)
                else:
                    plan = build_tool_agent_architecture_plan(**kwargs)
                    self.assertEqual([row["path"] for row in plan["files"]], paths)
                    self.assertTrue(any("outside the measured domain" in risk for risk in plan["risks"]))
                    self.assertEqual(plan["planning_status"], "needs_revision")
                    if paths == ["main.py"]:
                        from simple_ar.code_task.generation.writer import write_generated_project

                        writer = Mock()
                        writer.ask_json.return_value = {"content": "def main():\n    print('accuracy: 0.75')\n\nmain()\n"}
                        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
                            project = Path(tmp) / "project"
                            artifacts = write_generated_project(
                                project_dir=project, architecture_plan=plan,
                                result_schema={"primary_metric": "accuracy"}, contract={},
                                memory={}, client=writer,
                            )
                            self.assertEqual([row["path"] for row in artifacts["generated_files"]], ["main.py"])
                            self.assertEqual([p.relative_to(project).as_posix() for p in project.rglob("*.py")], ["main.py"])
                            completed = subprocess.run(
                                [sys.executable, str(project / "main.py")], cwd=project,
                                capture_output=True, text=True, timeout=10, check=True,
                            )
                            self.assertEqual(completed.stdout.strip(), "accuracy: 0.75")

    def test_planning_modes_share_file_contract_with_different_call_costs(self) -> None:
        from simple_ar.code_task.generation.architecture import build_architecture_plan

        plans = []
        for mode, expected_calls in (("compact", 1), ("tool_agent", 5)):
            client = Mock()
            client.ask_json.return_value = {
                "files": [{"path": "main.py", "public_api": ["main(argv=None)"]}],
                "status": "pass", "findings": [],
            }
            plan, source = build_architecture_plan(
                contract={}, result_schema={}, resource_plan={}, domain_profile={},
                client=client, planning_mode=mode, planning_review_rounds=0,
            )
            self.assertEqual(source, mode)
            self.assertEqual(client.ask_json.call_count, expected_calls)
            self.assertTrue(plan["files"][0]["entrypoint"])
            plans.append(plan)
        self.assertEqual(plans[0]["files"], plans[1]["files"])

    def test_empty_architecture_plan_requires_explicit_fallback(self) -> None:
        from simple_ar.code_task.generation.architecture import build_architecture_plan

        client = Mock()
        client.ask_json.return_value = {"files": []}
        for mode in ("compact", "tool_agent"):
            with self.subTest(mode=mode), patch(
                "simple_ar.code_task.generation.architecture.build_tool_agent_architecture_plan",
                return_value={"files": []},
            ):
                kwargs = dict(contract={}, result_schema={}, resource_plan={}, domain_profile={},
                              client=client, planning_mode=mode)
                with self.assertRaisesRegex(LLMError, "fallback is disabled"):
                    build_architecture_plan(**kwargs)
                plan, source = build_architecture_plan(**kwargs, allow_fallback=True)
                self.assertEqual(source, "fallback")
                self.assertTrue(plan["files"])

    def test_greenfield_execute_generates_validates_and_runs_project(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_file = root / "task.md"
            write_text(
                task_file,
                "# Task\n\nGenerate a deterministic project that prints accuracy and macro_f1 metrics.\n",
            )
            run_dir = root / "runs" / "greenfield-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=None,
                task_file=task_file,
                kind="greenfield",
                benchmark_command="python generated_project/main.py",
                workspace_mode="empty",
                primary_metric="accuracy",
                metric_directions={"accuracy": "higher_is_better"},
            )

            result = execute_code_task(
                run_dir,
                use_llm=False,
                to_step="run",
                timeout_sec=30,
                max_files=8,
            )

            self.assertEqual(result.stop_reason, "completed")
            self.assertTrue((run_dir / "code_task" / "workspace" / "generated_project" / "main.py").is_file())
            self.assertTrue((run_dir / "code_task" / "meta" / "resource_probe.json").is_file())
            self.assertTrue((run_dir / "code_task" / "meta" / "resource_decision.json").is_file())
            advice = read_json(run_dir / "code_task" / "meta" / "dependency_advice.json")
            self.assertEqual(advice["schema_version"], "code_task_dependency_advice.v1")
            self.assertEqual(advice["policy"], "advice_only_no_auto_install")
            task_contract = read_json(run_dir / "code_task" / "meta" / "task_contract.json")
            self.assertEqual(task_contract["schema_version"], "code_task_contract.v4")
            self.assertEqual(task_contract["task_kind"], "greenfield")
            self.assertEqual(task_contract["metric_contract"]["primary_metric"], "accuracy")
            self.assertIn("version_hash", task_contract)
            coverage = read_json(run_dir / "code_task" / "meta" / "task_contract_coverage.json")
            self.assertEqual(coverage["version_hash"], task_contract["version_hash"])
            analyzer_registry = read_json(run_dir / "code_task" / "meta" / "analyzer_registry.json")
            self.assertEqual(analyzer_registry["schema_version"], "code_task_analyzer_registry.v1")
            self.assertTrue(any(row["id"] == "local_api_contract" for row in analyzer_registry["analyzers"]))
            validation = read_json(run_dir / "code_task" / "meta" / "validation_report.json")
            self.assertEqual(validation["status"], "passed")
            metrics = read_json(run_dir / "code_task" / "run" / "patched" / "metrics.json")
            self.assertIn("accuracy", metrics)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["implementation"]["status"], "generated")
            self.assertEqual(manifest["patch"]["mode"], "greenfield_generated")

            # Refresh retired review rules without regenerating or rerunning the experiment.
            review_path = run_dir / "code_task/meta/review_report.json"
            prior_review = read_json(review_path)
            prior_review["metadata"]["review_contract_version"] = 12
            write_json(review_path, prior_review)
            metrics_path = run_dir / "code_task/run/patched/metrics.json"
            measured_bytes = metrics_path.read_bytes()
            with patch("simple_ar.code_task.orchestration.execute.run_code_task_benchmark") as benchmark:
                refreshed = execute_code_task(run_dir, use_llm=False, to_step="review", max_files=8)
            benchmark.assert_not_called()
            self.assertEqual(read_json(review_path)["metadata"]["review_contract_version"], 13)
            self.assertTrue(any(step.step == "review" and step.detail.startswith("refreshed status")
                                for step in refreshed.steps))
            self.assertEqual(metrics_path.read_bytes(), measured_bytes)

            # Exercise the same runner after an actual subprocess failure.
            # The repair boundary restores this fixture; it does not fake a metric.
            entrypoint = run_dir / "code_task/workspace/generated_project/main.py"
            original_source = entrypoint.read_text(encoding="utf-8")
            write_text(entrypoint, original_source + "\nraise RuntimeError('fixture run failure')\n")

            def restore_fixture(*args, **kwargs):
                write_text(entrypoint, original_source)
                return True

            with patch(
                "simple_ar.code_task.orchestration.execute._attempt_greenfield_run_repair",
                side_effect=restore_fixture,
            ), patch(
                "simple_ar.code_task.orchestration.execute.run_code_task_benchmark",
                wraps=run_code_task_benchmark,
            ) as runner:
                repaired = execute_code_task(run_dir, use_llm=False, to_step="run",
                                             timeout_sec=30, max_files=8)
            self.assertEqual(repaired.stop_reason, "completed")
            self.assertEqual(runner.call_count, 2)
            self.assertEqual([step.detail for step in repaired.steps if step.step == "run"],
                             ["status failed", "status passed"])
            self.assertEqual(read_json(run_dir / "code_task/run/patched/metrics.json"), metrics)

    def test_greenfield_review_failure_without_model_does_not_erase_package_code(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_file = root / "task.md"
            write_text(
                task_file,
                "# Task\n\nGenerate a deterministic project that prints accuracy and macro_f1 metrics.\n",
            )
            run_dir = root / "runs" / "greenfield-review-repair"
            initialize_code_task(
                run_dir=run_dir,
                code_root=None,
                task_file=task_file,
                kind="greenfield",
                benchmark_command="python generated_project/main.py",
                workspace_mode="empty",
                primary_metric="accuracy",
                metric_directions={"accuracy": "higher_is_better", "macro_f1": "higher_is_better"},
            )

            first = execute_code_task(
                run_dir,
                use_llm=False,
                to_step="work-plan",
                timeout_sec=30,
                max_files=8,
            )
            self.assertEqual(first.stop_reason, "stop_point")
            init_file = (
                run_dir
                / "code_task"
                / "workspace"
                / "generated_project"
                / "generated_experiment"
                / "__init__.py"
            )
            write_text(init_file, '__"""generated_experiment package."""\n')
            write_json(
                run_dir / "code_task" / "meta" / "review_report.json",
                {
                    "schema_version": "review_report.v1",
                    "status": "failed",
                    "findings": [
                        {
                            "severity": "blocking",
                            "category": "python_compile_failed",
                            "summary": "generated_experiment/__init__.py does not compile.",
                        }
                    ],
                    "summary": {"blocking_count": 1, "error_count": 1, "warning_count": 0},
                },
            )

            result = execute_code_task(
                run_dir,
                use_llm=False,
                to_step="run",
                timeout_sec=30,
                max_files=8,
                repair_rounds=1,
            )

            self.assertNotEqual(result.stop_reason, "completed")
            repair = read_json(run_dir / "code_task" / "meta" / "review_repair.json")
            self.assertEqual(repair["status"], "skipped")
            self.assertEqual(read_text(init_file), '__"""generated_experiment package."""\n')
            self.assertFalse((run_dir / "code_task" / "run" / "patched" / "metrics.json").exists())

    def test_runtime_repair_does_not_invent_presets_or_ignore_parameters(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project_dir = root / "generated_project"
            package_dir = project_dir / "generated_experiment"
            package_dir.mkdir(parents=True)
            write_json(
                project_dir / "config.json",
                {
                    "objective": "Greenfield run repair test.",
                    "presets": {
                        "{preset_name}": {
                            "conditions": ["baseline", "candidate"],
                            "max_items": 32,
                        }
                    },
                },
            )
            write_text(
                package_dir / "runner.py",
                (
                    "def run_experiment(preset='smoke'):\n"
                    "    return {'score': 1.0}\n"
                ),
            )

            preset_repair = repair_generated_project_from_run_failure(
                project_dir=project_dir,
                failure_analysis={"status": "needs_repair"},
                stderr_text=(
                    "raise KeyError(f\"Unknown preset '{preset_name}'. Available presets: {available}\")\n"
                    "KeyError: \"Unknown preset 'smoke'. Available presets: {preset_name}\"\n"
                ),
                output_path=root / "run_repair_preset.json",
            )

            self.assertEqual(preset_repair["status"], "skipped")
            self.assertEqual(preset_repair["changed_files"], [])
            config = read_json(project_dir / "config.json")
            self.assertNotIn("smoke", config["presets"])
            self.assertIn("{preset_name}", config["presets"])

            signature_repair = repair_generated_project_from_run_failure(
                project_dir=project_dir,
                failure_analysis={"status": "needs_repair"},
                stderr_text="TypeError: run_experiment() got an unexpected keyword argument 'data_source'",
                output_path=root / "run_repair_signature.json",
            )

            self.assertEqual(signature_repair["status"], "skipped")
            self.assertEqual(signature_repair["changed_files"], [])
            self.assertEqual(read_text(package_dir / "runner.py"),
                             "def run_experiment(preset='smoke'):\n    return {'score': 1.0}\n")

    def test_greenfield_run_repair_uses_llm_for_runtime_contract_mismatch(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.labels: list[str] = []
                self.prompts: list[str] = []

            def ask_json(self, _system: str, _prompt: str, *, label: str = "") -> dict[str, str]:
                self.labels.append(label)
                self.prompts.append(_prompt)
                if label == "greenfield-run-repair-plan":
                    return {
                        "diagnosis": "The data producer and processing consumer disagree on metadata fields.",
                        "root_cause": "DatasetInput metadata contract mismatch.",
                        "target_files": [
                            {"path": "generated_experiment/inputs.py", "reason": "producer"},
                            {"path": "generated_experiment/processing.py", "reason": "consumer"},
                            {"path": "generated_experiment/runner.py", "reason": "orchestrator"},
                        ],
                        "repair_strategy": "Make the generated data bundle and processing path share one contract.",
                        "risks": [],
                    }
                path = label.removeprefix("greenfield-run-repair-")
                return {
                    "content": (
                        "from __future__ import annotations\n\n"
                        f"REPAIRED_PATH = {path!r}\n"
                    ),
                    "summary": f"Repaired {path}.",
                }

        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project_dir = root / "generated_project"
            package_dir = project_dir / "generated_experiment"
            package_dir.mkdir(parents=True)
            for name in ["inputs.py", "processing.py", "runner.py", "__init__.py"]:
                write_text(package_dir / name, "from __future__ import annotations\n")
            write_text(project_dir / "main.py", "from __future__ import annotations\n")

            fake = FakeClient()
            repair = repair_generated_project_from_run_failure(
                project_dir=project_dir,
                failure_analysis={"status": "needs_repair", "implicated_files": ["generated_project/main.py"]},
                stderr_text=(
                    "ERROR: Experiment run failed: "
                    "\"DatasetInput.metadata for 'wine' is missing required keys: features, labels\""
                ),
                output_path=root / "run_repair_contract.json",
                code_artifacts={
                    "generated_files": [
                        {"path": "generated_experiment/inputs.py", "mode": "llm"},
                        {"path": "generated_experiment/processing.py", "mode": "llm"},
                        {"path": "generated_experiment/runner.py", "mode": "llm"},
                        {"path": "main.py", "mode": "llm"},
                    ]
                },
                client=fake,
            )

            self.assertEqual(repair["status"], "patched")
            self.assertIn("generated_experiment/inputs.py", repair["changed_files"])
            self.assertIn("generated_experiment/processing.py", repair["changed_files"])
            self.assertTrue(fake.labels)
            file_labels = [label for label in fake.labels if label != "greenfield-run-repair-plan"]
            self.assertEqual(
                file_labels[:3],
                [
                    "greenfield-run-repair-generated_experiment/inputs.py",
                    "greenfield-run-repair-generated_experiment/processing.py",
                    "greenfield-run-repair-generated_experiment/runner.py",
                ],
            )
            repair_prompts = [prompt for label, prompt in zip(fake.labels, fake.prompts) if label != "greenfield-run-repair-plan"]
            self.assertTrue(any("Runtime repair plan" in prompt for prompt in repair_prompts))
            self.assertTrue(any("Relevant project context" in prompt for prompt in repair_prompts))
            self.assertIn("REPAIRED_PATH", read_text(package_dir / "inputs.py"))
            repair_plan = read_json(root / "run_repair_plan.json")
            self.assertEqual(repair_plan["schema_version"], "code_task_repair_plan.v1")
            self.assertIn("schema", repair_plan["affected_contracts"])
            self.assertEqual(repair_plan["target_files"][0]["path"], "generated_experiment/inputs.py")
            patch_set = read_json(root / "atomic_patch_set.json")
            self.assertEqual(patch_set["schema_version"], "code_task_atomic_patch_set.v1")
            self.assertEqual(patch_set["status"], "patched")
            self.assertIn("generated_experiment/inputs.py", patch_set["changed_files"])

    def test_greenfield_benchmark_rejects_empty_zero_evidence(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_file = root / "task.md"
            write_text(task_file, "# Task\n\nRun a greenfield experiment with condition-level evidence.\n")
            run_dir = root / "runs" / "empty-greenfield-evidence"
            initialize_code_task(
                run_dir=run_dir,
                code_root=None,
                task_file=task_file,
                kind="greenfield",
                benchmark_command="python generated_project/main.py",
                workspace_mode="empty",
                primary_metric="test_accuracy",
                metric_directions={
                    "test_accuracy": "higher",
                    "macro_f1": "higher",
                    "accuracy_std": "lower",
                    "runtime_sec": "resource",
                },
            )
            project_dir = run_dir / "code_task" / "workspace" / "generated_project"
            project_dir.mkdir(parents=True)
            write_text(
                project_dir / "main.py",
                (
                    "from pathlib import Path\n"
                    "import json\n\n"
                    "artifacts = Path('generated_project') / 'artifacts'\n"
                    "artifacts.mkdir(parents=True, exist_ok=True)\n"
                    "(artifacts / 'results.json').write_text(json.dumps({\n"
                    "    'condition_summaries': {},\n"
                    "    'dataset_comparisons': {},\n"
                    "    'raw_records': [{'condition': {'name': 'baseline'}, 'records': []}],\n"
                    "    'global_metrics': {'test_accuracy': 0.0, 'macro_f1': 0.0, 'accuracy_std': 0.0, 'runtime_sec': 0.0},\n"
                    "}), encoding='utf-8')\n"
                    "print('test_accuracy: 0.0')\n"
                    "print('macro_f1: 0.0')\n"
                    "print('accuracy_std: 0.0')\n"
                    "print('runtime_sec: 0.0')\n"
                ),
            )

            result = run_code_task_benchmark(
                run_dir,
                timeout_sec=30,
                skip_validation=True,
                run_label="patched",
            )

            self.assertEqual(result.status, "failed")
            self.assertIn("Generated benchmark quality guard failed", read_text(result.stderr_path))
            report = read_json(result.report_path)
            self.assertEqual(report["quality_guard"]["reason"], "empty_greenfield_evidence")
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "benchmark_failed")

    def test_greenfield_benchmark_rejects_zero_exit_without_required_results(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_file = root / "task.md"
            write_text(
                task_file,
                "# Task\n\nWrite artifacts/results.json and artifacts/report.md with measured rows.\n",
            )
            run_dir = root / "runs" / "missing-results"
            initialize_code_task(
                run_dir=run_dir,
                code_root=None,
                task_file=task_file,
                kind="greenfield",
                benchmark_command="python generated_project/main.py",
                workspace_mode="empty",
                primary_metric="score",
                metric_directions={"score": "higher"},
            )
            project_dir = run_dir / "code_task" / "workspace" / "generated_project"
            project_dir.mkdir(parents=True)
            write_text(
                project_dir / "main.py",
                "print('ERROR: experiment failed but was swallowed')\n",
            )

            result = run_code_task_benchmark(run_dir, timeout_sec=30, skip_validation=True)

            self.assertEqual(result.status, "failed")
            report = read_json(result.report_path)
            self.assertEqual(report["quality_guard"]["reason"], "missing_results_artifact")

    def test_greenfield_benchmark_rejects_missing_required_report(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_file = root / "task.md"
            write_text(
                task_file,
                "# Task\n\nWrite artifacts/results.json and artifacts/report.md with measured rows.\n",
            )
            run_dir = root / "runs" / "missing-report"
            initialize_code_task(
                run_dir=run_dir,
                code_root=None,
                task_file=task_file,
                kind="greenfield",
                benchmark_command="python generated_project/main.py",
                workspace_mode="empty",
                primary_metric="score",
                metric_directions={"score": "higher"},
            )
            project_dir = run_dir / "code_task" / "workspace" / "generated_project"
            project_dir.mkdir(parents=True)
            write_text(
                project_dir / "main.py",
                (
                    "from pathlib import Path\n"
                    "artifacts = Path('generated_project') / 'artifacts'\n"
                    "artifacts.mkdir(parents=True, exist_ok=True)\n"
                    "(artifacts / 'results.json').write_text("
                    "'{\"raw_records\": [{\"score\": 1.0}], \"global_metrics\": {\"score\": 1.0}}'"
                    ")\n"
                    "print('score: 1.0')\n"
                ),
            )

            result = run_code_task_benchmark(run_dir, timeout_sec=30, skip_validation=True)

            self.assertEqual(result.status, "failed")
            report = read_json(result.report_path)
            self.assertEqual(report["quality_guard"]["reason"], "missing_required_artifact")
            self.assertIn("report.md", report["quality_guard"]["message"])

    def test_greenfield_benchmark_reports_artifact_path_mismatch(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_file = root / "task.md"
            write_text(
                task_file,
                "# Task\n\nWrite artifacts/results.json and artifacts/report.md with measured rows.\n",
            )
            run_dir = root / "runs" / "artifact-path-mismatch"
            initialize_code_task(
                run_dir=run_dir,
                code_root=None,
                task_file=task_file,
                kind="greenfield",
                benchmark_command="python generated_project/main.py",
                workspace_mode="empty",
                primary_metric="score",
                metric_directions={"score": "higher"},
            )
            project_dir = run_dir / "code_task" / "workspace" / "generated_project"
            project_dir.mkdir(parents=True)
            write_text(
                project_dir / "main.py",
                (
                    "from pathlib import Path\n"
                    "root_artifacts = Path('artifacts')\n"
                    "root_artifacts.mkdir(parents=True, exist_ok=True)\n"
                    "(root_artifacts / 'results.json').write_text('{\"raw_records\": [{\"score\": 1.0}], \"global_metrics\": {\"score\": 1.0}}')\n"
                    "(root_artifacts / 'report.md').write_text('# Report\\n')\n"
                    "expected = Path('generated_project') / 'artifacts'\n"
                    "expected.mkdir(parents=True, exist_ok=True)\n"
                    "(expected / 'results.json').write_text('')\n"
                    "print('score: 1.0')\n"
                ),
            )

            result = run_code_task_benchmark(run_dir, timeout_sec=30, skip_validation=True)

            self.assertEqual(result.status, "failed")
            report = read_json(result.report_path)
            self.assertEqual(report["quality_guard"]["reason"], "artifact_path_mismatch")
            scan = read_json(run_dir / report["artifact_scan"])
            self.assertEqual(scan["status"], "warning")
            self.assertTrue(
                any(item.get("code") == "artifact_path_mismatch" for item in scan["findings"])
            )
            analysis = analyze_code_task_failure(run_dir)
            self.assertIn("artifact_path_mismatch", read_text(analysis.analysis_path))

    def test_greenfield_artifact_scan_ignores_downstream_submission_signals(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_file = root / "task.md"
            write_text(
                task_file,
                (
                    "# Task\n\n"
                    "- Execution produces a machine-readable metrics artifact.\n"
                    "- Write `generated_project/artifacts/results.json` with measured rows.\n"
                    "- Write `generated_project/artifacts/report.md` with the final report.\n"
                    "- A downstream adapter will convert this run into `submission/results/metrics.json` "
                    "and `submission/README.md`.\n"
                ),
            )
            run_dir = root / "runs" / "downstream-signals"
            initialize_code_task(
                run_dir=run_dir,
                code_root=None,
                task_file=task_file,
                kind="greenfield",
                benchmark_command="python generated_project/main.py",
                workspace_mode="empty",
                primary_metric="score",
                metric_directions={"score": "higher"},
            )
            project_dir = run_dir / "code_task" / "workspace" / "generated_project"
            project_dir.mkdir(parents=True)
            write_text(
                project_dir / "main.py",
                (
                    "from pathlib import Path\n"
                    "import json\n"
                    "artifacts = Path('generated_project/artifacts')\n"
                    "artifacts.mkdir(parents=True, exist_ok=True)\n"
                    "(artifacts / 'results.json').write_text(json.dumps({'raw_records': [{'score': 1.0}], 'global_metrics': {'score': 1.0}}))\n"
                    "(artifacts / 'report.md').write_text('# Report\\n')\n"
                    "submission = Path('submission/results')\n"
                    "submission.mkdir(parents=True, exist_ok=True)\n"
                    "(submission / 'metrics.json').write_text('{}')\n"
                    "print('score: 1.0')\n"
                ),
            )

            result = run_code_task_benchmark(run_dir, timeout_sec=30, skip_validation=True)

            self.assertEqual(result.status, "passed")
            report = read_json(result.report_path)
            self.assertNotIn("quality_guard", report)
            scan = read_json(run_dir / report["artifact_scan"])
            scanned_paths = {row["expected_path"] for row in scan["artifacts"]}
            self.assertIn("generated_project/artifacts/results.json", scanned_paths)
            self.assertIn("generated_project/artifacts/report.md", scanned_paths)
            self.assertNotIn("generated_project/submission/results/metrics.json", scanned_paths)

    def test_greenfield_run_repair_targets_custom_project_layout(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.labels: list[str] = []

            def ask_json(self, _system: str, _prompt: str, *, label: str = "") -> dict[str, str]:
                self.labels.append(label)
                if label == "greenfield-run-repair-plan":
                    if "has no attribute" in _prompt:
                        targets = [
                            "src/loaders.py",
                            "src/preprocess.py",
                            "src/model_core.py",
                            "src/pipeline.py",
                        ]
                    else:
                        targets = [
                            {"path": "src/loaders.py"},
                            {"path": "src/preprocess.py"},
                            {"path": "src/pipeline.py"},
                        ]
                    return {
                        "diagnosis": "Use project-specific producer and consumer files instead of fixed generated_experiment names.",
                        "root_cause": "The generated project has a custom package layout.",
                        "target_files": targets,
                        "repair_strategy": "Repair the producer, transformer, and orchestrator in that order.",
                        "risks": [],
                    }
                path = label.removeprefix("greenfield-run-repair-")
                return {
                    "content": (
                        "from __future__ import annotations\n\n"
                        f"REPAIRED_PATH = {path!r}\n"
                    ),
                    "summary": f"Repaired {path}.",
                }

        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project_dir = root / "generated_project"
            for rel_path in [
                "app.py",
                "src/loaders.py",
                "src/preprocess.py",
                "src/pipeline.py",
                "src/model_core.py",
            ]:
                target = project_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                write_text(target, "from __future__ import annotations\n")

            fake = FakeClient()
            repair = repair_generated_project_from_run_failure(
                project_dir=project_dir,
                failure_analysis={"status": "needs_repair", "implicated_files": ["generated_project/app.py"]},
                stderr_text=(
                    "ERROR: Experiment run failed: "
                    "\"DatasetInput.metadata is missing required keys: features, labels\""
                ),
                output_path=root / "run_repair_custom_layout.json",
                code_artifacts={
                    "generated_files": [
                        {"path": "app.py", "mode": "llm"},
                        {"path": "src/loaders.py", "mode": "llm"},
                        {"path": "src/preprocess.py", "mode": "llm"},
                        {"path": "src/pipeline.py", "mode": "llm"},
                        {"path": "src/model_core.py", "mode": "llm"},
                    ]
                },
                client=fake,
            )

            self.assertEqual(repair["status"], "patched")
            file_labels = [label for label in fake.labels if label != "greenfield-run-repair-plan"]
            self.assertEqual(
                file_labels[:3],
                [
                    "greenfield-run-repair-src/loaders.py",
                    "greenfield-run-repair-src/preprocess.py",
                    "greenfield-run-repair-src/pipeline.py",
                ],
            )
            self.assertIn("src/loaders.py", repair["changed_files"])

            fake_attr = FakeClient()
            attr_repair = repair_generated_project_from_run_failure(
                project_dir=project_dir,
                failure_analysis={"status": "needs_repair", "implicated_files": ["generated_project/app.py"]},
                stderr_text="Experiment failed: 'str' object has no attribute 'X'",
                output_path=root / "run_repair_custom_attribute_error.json",
                code_artifacts={
                    "generated_files": [
                        {"path": "app.py", "mode": "llm"},
                        {"path": "src/loaders.py", "mode": "llm"},
                        {"path": "src/preprocess.py", "mode": "llm"},
                        {"path": "src/pipeline.py", "mode": "llm"},
                        {"path": "src/model_core.py", "mode": "llm"},
                    ]
                },
                client=fake_attr,
            )

            self.assertEqual(attr_repair["status"], "patched")
            attr_file_labels = [label for label in fake_attr.labels if label != "greenfield-run-repair-plan"]
            self.assertEqual(
                attr_file_labels[:4],
                [
                    "greenfield-run-repair-src/loaders.py",
                    "greenfield-run-repair-src/preprocess.py",
                    "greenfield-run-repair-src/model_core.py",
                    "greenfield-run-repair-src/pipeline.py",
                ],
            )

    def test_greenfield_run_repair_includes_attribute_consumers(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.labels: list[str] = []

            def ask_json(self, _system: str, _prompt: str, *, label: str = "") -> dict[str, str]:
                self.labels.append(label)
                if label == "greenfield-run-repair-plan":
                    return {
                        "diagnosis": "The producer changed metrics to dicts but the plan missed one report consumer.",
                        "root_cause": "Metric contract mismatch across producer and consumer files.",
                        "target_files": [
                            {"path": "src/aggregator.py"},
                            {"path": "src/runner.py"},
                            {"path": "app.py"},
                        ],
                        "repair_strategy": "Repair all files that directly consume the missing metric symbol.",
                        "risks": [],
                    }
                path = label.removeprefix("greenfield-run-repair-")
                if path == "src/report.py":
                    return {
                        "content": (
                            "from __future__ import annotations\n\n"
                            f"REPAIRED_PATH = {path!r}\n\n"
                            "def render(summary):\n"
                            "    return summary['mean']['balanced_accuracy']\n"
                        ),
                        "summary": f"Repaired {path}.",
                    }
                return {
                    "content": (
                        "from __future__ import annotations\n\n"
                        f"REPAIRED_PATH = {path!r}\n"
                    ),
                    "summary": f"Repaired {path}.",
                }

        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project_dir = root / "generated_project"
            files = {
                "app.py": "from __future__ import annotations\n",
                "src/aggregator.py": "from __future__ import annotations\nmean = {'balanced_accuracy': 1.0}\n",
                "src/runner.py": "from __future__ import annotations\n",
                "src/report.py": (
                    "from __future__ import annotations\n\n"
                    "def render(summary):\n"
                    "    return summary.mean.balanced_accuracy\n"
                ),
            }
            for rel_path, content in files.items():
                target = project_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                write_text(target, content)

            fake = FakeClient()
            repair = repair_generated_project_from_run_failure(
                project_dir=project_dir,
                failure_analysis={"status": "needs_repair", "implicated_files": []},
                stderr_text="ERROR: 'dict' object has no attribute 'balanced_accuracy'",
                output_path=root / "run_repair_attribute_consumer.json",
                code_artifacts={
                    "generated_files": [
                        {"path": "app.py", "mode": "llm"},
                        {"path": "src/aggregator.py", "mode": "llm"},
                        {"path": "src/runner.py", "mode": "llm"},
                        {"path": "src/report.py", "mode": "llm"},
                    ]
                },
                client=fake,
            )

            self.assertEqual(repair["status"], "patched")
            file_labels = [label for label in fake.labels if label != "greenfield-run-repair-plan"]
            self.assertIn("greenfield-run-repair-src/report.py", file_labels)
            self.assertIn("src/report.py", repair["changed_files"])

    def test_greenfield_run_repair_prefers_structured_local_actions(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.labels: list[str] = []

            def ask_json(self, _system: str, _prompt: str, *, label: str = "") -> dict[str, object]:
                self.labels.append(label)
                if label == "greenfield-run-repair-plan":
                    return {
                        "failure_kind": "runtime",
                        "diagnosis": "The report consumer uses attribute access on a dict.",
                        "root_cause": "Producer returns a mapping while consumer expects an object.",
                        "observed_error": "'dict' object has no attribute 'balanced_accuracy'",
                        "repeated_failure": False,
                        "repair_scope": "block",
                        "why_not_smaller_scope": "The failing expression is one local line.",
                        "why_not_larger_scope": "The producer API is otherwise coherent.",
                        "target_files": [{"path": "src/report.py"}],
                        "dependency_trace": ["metrics dict -> report consumer"],
                        "repair_strategy": "Use mapping access in the consumer.",
                        "risks": [],
                    }
                return {
                    "summary": "Replace one consumer expression with mapping access.",
                    "actions": [
                        {
                            "action": "replace_block",
                            "path": "src/report.py",
                            "old_string": "return summary.mean.balanced_accuracy\n",
                            "new_string": "return summary['mean']['balanced_accuracy']\n",
                            "rationale": "The summary object is a dict.",
                        }
                    ],
                }

        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project_dir = root / "generated_project"
            report = project_dir / "src" / "report.py"
            report.parent.mkdir(parents=True)
            write_text(
                report,
                (
                    "from __future__ import annotations\n\n"
                    "def render(summary):\n"
                    "    return summary.mean.balanced_accuracy\n"
                ),
            )

            repair = repair_generated_project_from_run_failure(
                project_dir=project_dir,
                failure_analysis={"status": "needs_repair", "implicated_files": []},
                stderr_text="ERROR: 'dict' object has no attribute 'balanced_accuracy'",
                output_path=root / "run_repair_actions.json",
                code_artifacts={"generated_files": [{"path": "src/report.py", "mode": "llm"}]},
                client=FakeClient(),
            )

            self.assertEqual(repair["status"], "patched")
            self.assertEqual(repair["changed_files"], ["src/report.py"])
            content = read_text(report)
            self.assertIn("summary['mean']['balanced_accuracy']", content)
            self.assertNotIn("summary.mean.balanced_accuracy", content)
            edit_application = repair["regenerated_files"][0]["edit_application"]
            self.assertEqual(edit_application["status"], "patched")
            self.assertEqual(edit_application["applied_actions"][0]["action"], "replace_block")
            self.assertEqual(repair["snapshot"]["captured_count"], 1)
            self.assertTrue(Path(repair["snapshot"]["manifest"]).is_file())
            self.assertFalse((root / "run_repair_backups").exists())

    def test_greenfield_run_repair_rejects_unapproved_public_api_drift(self) -> None:
        class FakeClient:
            def ask_json(self, _system: str, _prompt: str, *, label: str = "") -> dict[str, object]:
                if label == "greenfield-run-repair-plan":
                    return {
                        "diagnosis": "The artifact path producer/consumer contract is mismatched.",
                        "root_cause": "config.get_artifact_paths contract drifted.",
                        "target_files": [{"path": "config.py"}],
                        "repair_scope": "file",
                        "repair_strategy": "Rewrite config.",
                    }
                return {
                    "summary": "Rewrite config but accidentally change the public return contract.",
                    "content": (
                        "from __future__ import annotations\n\n"
                        "from pathlib import Path\n"
                        "from typing import Tuple\n\n"
                        "def get_artifact_paths() -> Tuple[Path, Path]:\n"
                        "    return Path('artifacts/results.json'), Path('artifacts/report.md')\n"
                    ),
                }

        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project_dir = root / "generated_project"
            project_dir.mkdir(parents=True)
            original = (
                "from __future__ import annotations\n\n"
                "from pathlib import Path\n\n"
                "def get_artifact_paths() -> dict[str, Path]:\n"
                "    return {'results': Path('artifacts/results.json'), 'report': Path('artifacts/report.md')}\n"
            )
            write_text(project_dir / "config.py", original)

            repair = repair_generated_project_from_run_failure(
                project_dir=project_dir,
                failure_analysis={"status": "needs_repair", "implicated_files": ["generated_project/config.py"]},
                stderr_text='TypeError: tuple indices must be integers or slices, not str; paths["results"]',
                output_path=root / "run_repair_api_drift.json",
                code_artifacts={"generated_files": [{"path": "config.py", "mode": "llm"}]},
                client=FakeClient(),
            )

            self.assertNotEqual(repair["status"], "patched")
            self.assertIn("public_api_signature_would_change", "\n".join(repair["unresolved_errors"]))
            self.assertEqual((project_dir / "config.py").read_text(encoding="utf-8"), original)

    def test_greenfield_review_flags_stdlib_shadow_and_nested_artifact_path(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project_dir = root / "generated_project"
            project_dir.mkdir(parents=True)
            write_text(project_dir / "main.py", "from __future__ import annotations\n")
            write_text(project_dir / "types.py", "from __future__ import annotations\n\nclass Row: pass\n")
            write_text(
                project_dir / "writer.py",
                (
                    "from __future__ import annotations\n"
                    "from pathlib import Path\n\n"
                    "CANONICAL_RESULTS = Path('artifacts/results.json')\n\n"
                    "def write_artifacts(results_dir):\n"
                    "    base = Path(results_dir)\n"
                    "    (base / CANONICAL_RESULTS).parent.mkdir(parents=True, exist_ok=True)\n"
                    "    (base / CANONICAL_RESULTS).write_text('{}')\n"
                ),
            )

            report = review_generated_project(
                project_dir=project_dir,
                code_artifacts={
                    "generated_files": [
                        {"path": "main.py", "mode": "llm"},
                        {"path": "types.py", "mode": "llm"},
                        {"path": "writer.py", "mode": "llm"},
                    ]
                },
                result_schema={"required_metrics": ["accuracy"]},
                resource_plan={"max_files": 10, "max_generated_lines": 500},
                contract={"objective": "Write artifacts/results.json with measured accuracy."},
                use_llm=False,
            )

            categories = {finding["category"] for finding in report["findings"]}
            self.assertIn("stdlib_module_shadow", categories)
            self.assertIn("nested_artifact_path_risk", categories)

    def test_runtime_repair_without_model_does_not_guess_imports_or_output_paths(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            write_text(project / "types.py", "class ConditionSpec:\n    pass\n")
            write_text(project / "main.py", "from types import ConditionSpec\nLABEL = 'types'\n")
            (project / "io").mkdir()
            write_text(project / "io" / "__init__.py", "")
            write_text(project / "writer.py", 'from pathlib import Path\nCANONICAL_RESULTS = Path("artifacts/results.json")\ndef write(results_dir):\n    base = Path(results_dir)\n    return base / CANONICAL_RESULTS\n')
            before = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
            failures = [
                f"ImportError: cannot import name 'ConditionSpec' from 'types' ({project / 'types.py'})",
                "ModuleNotFoundError: No module named 'io.artifacts'; 'io' is not a package",
                "ERROR: artifacts/results.json was not written",
            ]
            for index, stderr in enumerate(failures):
                with self.subTest(stderr=stderr):
                    repair = repair_generated_project_from_run_failure(
                        project_dir=project, failure_analysis={"status": "needs_repair"},
                        stderr_text=stderr, output_path=root / f"repair-{index}.json", client=None,
                    )
                    self.assertEqual(repair["status"], "skipped")
                    self.assertEqual(repair["failure_status"], "needs_repair")
                    self.assertEqual(repair["changed_files"], [])
                    self.assertEqual({p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}, before)




    def test_greenfield_execute_can_use_fake_agent_backend(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_file = root / "task.md"
            write_text(
                task_file,
                "# Task\n\nUse an external handoff backend to generate a runnable metric project.\n",
            )
            run_dir = root / "runs" / "greenfield-agent-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=None,
                task_file=task_file,
                kind="greenfield",
                benchmark_command="python generated_project/main.py",
                workspace_mode="empty",
                primary_metric="accuracy",
            )

            result = execute_code_task(
                run_dir,
                use_llm=False,
                to_step="run",
                timeout_sec=30,
                implementation_provider="fake",
                implementation_agent_mode="handoff",
            )

            self.assertEqual(result.stop_reason, "completed")
            self.assertTrue((run_dir / "agent_handoff" / "code-task-greenfield-fake").is_dir())
            self.assertTrue((run_dir / "agent_outputs" / "code-task-greenfield-fake" / "ingestion.json").is_file())
            self.assertTrue((run_dir / "code_task" / "meta" / "dependency_advice.md").is_file())
            backend = read_json(run_dir / "code_task" / "meta" / "code_backend.json")
            self.assertEqual(backend["backend"], "greenfield_agent")
            self.assertEqual(backend["provider"], "fake")
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["implementation"]["provider"], "fake")
            self.assertEqual(manifest["implementation"]["agent_mode"], "handoff")
            metrics = read_json(run_dir / "code_task" / "run" / "patched" / "metrics.json")
            self.assertIn("accuracy", metrics)

    def test_configured_edit_scope_limits_editable_repo_map_and_apply(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            config = root / "code_task.toml"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove the model implementation only.\n")
            config.write_text(
                f"""
[code_task]
code_root = "{code_root.as_posix()}"
task_file = "{task_file.as_posix()}"

[edit_scope]
allowed_patterns = ["spam_model.py"]
protected_patterns = ["pyproject.toml"]
""".strip(),
                encoding="utf-8",
            )
            options = load_code_task_init_options(config_path=str(config))

            run_dir = root / "runs" / "scoped-code-task"
            initialize_code_task(
                run_dir=run_dir,
                code_root=Path(options.code_root),
                task_file=Path(options.task_file or ""),
                benchmark_command="python -m unittest discover -s tests",
                edit_scope_allowed_patterns=options.edit_scope_allowed_patterns,
                edit_scope_protected_patterns=options.edit_scope_protected_patterns,
            )

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["edit_scope"]["allowed_patterns"], ["spam_model.py"])
            self.assertIn("pyproject.toml", manifest["edit_scope"]["protected_patterns"])
            repo_map = read_json(run_dir / "code_task" / "meta" / "repo_map.json")
            files = {row["path"]: row for row in repo_map["files"]}
            self.assertEqual(files["spam_model.py"]["access_role"], "editable")
            self.assertEqual(files["pyproject.toml"]["access_role"], "read_only_evidence")

            write_text(run_dir / "code_task" / "patch_plan.md", "# Patch Plan\n\n- Keep edits in scope.\n")
            record_plan_decision(run_dir, decision="approve", note="scope test")
            edits_path = root / "bad_scope_patch.json"
            write_json(
                edits_path,
                {
                    "schema_version": 1,
                    "edits": [
                        {
                            "path": "pyproject.toml",
                            "old": "[project]\nname = \"toy-project\"\nversion = \"0.1.0\"\n",
                            "new": "[project]\nname = \"toy-project\"\nversion = \"0.2.0\"\n",
                            "reason": "This file is intentionally outside the edit scope.",
                        }
                    ],
                },
            )
            with self.assertRaises(PatchValidationError) as caught:
                apply_patch_edits(run_dir, edits_file=edits_path)
            self.assertIn("path is not editable by the edit scope", str(caught.exception))

    def test_init_can_create_git_worktree_workspace(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "git_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            shutil.rmtree(code_root / ".git")
            write_text(task_file, "# Task\n\nImprove the spam classifier.\n")
            _git(code_root, "init")
            _git(code_root, "config", "user.email", "test@example.com")
            _git(code_root, "config", "user.name", "SimpleAR Test")
            _git(code_root, "add", ".")
            _git(code_root, "commit", "-m", "initial")

            run_dir = root / "runs" / "git-worktree-run"
            result = initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
                workspace_mode="git_worktree",
                max_file_bytes=10_000,
            )

            self.assertTrue((result.workspace_dir / "spam_model.py").is_file())
            self.assertTrue((result.workspace_dir / ".git").exists())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["workspace"]["mode"], "git_worktree")
            self.assertEqual(manifest["workspace"]["workspace_dir"], "code_task/workspace")
            self.assertEqual(manifest["workspace"]["project_root"], "code_task/workspace")
            self.assertEqual(manifest["copy"]["files_copied"], 0)
            self.assertTrue(manifest["workspace"]["git"]["origin_commit"])
            self.assertEqual(manifest["workspace"]["environment_mapping"]["mode"], "git_worktree")
            index = read_json(result.codebase_index_path)
            self.assertEqual(index["project"]["python_file_count"], 2)
            self.assertNotIn(".env", {item["path"] for item in index["files"]})

    def test_auto_workspace_prefers_git_worktree_for_git_project(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "git_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            shutil.rmtree(code_root / ".git")
            write_text(task_file, "# Task\n\nImprove the spam classifier.\n")
            _git(code_root, "init")
            _git(code_root, "config", "user.email", "test@example.com")
            _git(code_root, "config", "user.name", "SimpleAR Test")
            _git(code_root, "add", ".")
            _git(code_root, "commit", "-m", "initial")

            run_dir = root / "runs" / "auto-worktree-run"
            result = initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                workspace_mode="auto",
                max_file_bytes=10_000,
            )

            self.assertEqual(result.workspace.mode, "git_worktree")
            self.assertEqual(result.workspace.requested_mode, "auto")
            self.assertEqual(result.workspace.selected_mode, "git_worktree")
            self.assertTrue((result.workspace.workspace_dir / ".git").exists())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["workspace"]["requested_mode"], "auto")
            self.assertEqual(manifest["workspace"]["selected_mode"], "git_worktree")
            self.assertEqual(manifest["workspace"]["fallback_reason"], "")

            write_text(code_root / "uncommitted.py", "VALUE = 7\n")
            dirty = initialize_code_task(
                run_dir=root / "runs" / "dirty-auto", code_root=code_root,
                task_file=task_file, workspace_mode="auto",
            )
            self.assertEqual(dirty.workspace.mode, "copy")
            self.assertIn("uncommitted", dirty.workspace.fallback_reason)
            self.assertEqual((dirty.workspace_dir / "uncommitted.py").read_text(), "VALUE = 7\n")

    def test_git_worktree_supports_project_subdirectory(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            repo = root / "repo"
            code_root = repo / "package"
            code_root.mkdir(parents=True)
            write_text(code_root / "module.py", "VALUE = 1\n")
            write_text(repo / "root_only.py", "ROOT_VALUE = 1\n")
            task_file = root / "task.md"
            write_text(task_file, "# Task\n\nChange VALUE.\n")
            _git(repo, "init")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "SimpleAR Test")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "initial")

            run_dir = root / "runs" / "subdir-worktree-run"
            result = initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                workspace_mode="git_worktree",
            )

            self.assertEqual(result.workspace_dir, result.workspace.project_root)
            self.assertTrue((result.workspace_dir / "module.py").is_file())
            self.assertFalse((result.workspace_dir / ".git").exists())
            self.assertTrue((result.workspace.workspace_dir / ".git").exists())
            manifest = read_json(root / "runs" / "subdir-worktree-run" / "manifest.json")
            self.assertEqual(manifest["workspace"]["mode"], "git_worktree")
            self.assertEqual(manifest["workspace"]["workspace_dir"], "code_task/workspace")
            self.assertEqual(manifest["workspace"]["project_root"], "code_task/workspace/package")
            self.assertEqual(manifest["workspace"]["project_relative_path"], "package")
            self.assertIn("subdirectory", " ".join(manifest["workspace"]["warnings"]))
            index = read_json(result.codebase_index_path)
            self.assertEqual(index["project"]["file_count"], 1)
            proposal = run_dir / "code_task" / "meta" / "escape_proposal.json"
            write_json(
                proposal,
                {
                    "edits": [
                        {
                            "path": "../root_only.py",
                            "old": "ROOT_VALUE = 1",
                            "new": "ROOT_VALUE = 2",
                            "reason": "This must not escape the package project root.",
                        }
                    ]
                },
            )
            with self.assertRaises(PatchValidationError):
                apply_patch_edits(run_dir, edits_file=proposal, allow_unapproved_plan=True)
            self.assertEqual(read_text(repo / "root_only.py"), "ROOT_VALUE = 1\n")

    def test_auto_workspace_falls_back_to_copy_for_non_git_project(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "plain_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove the plain project.\n")

            run_dir = root / "runs" / "auto-copy-run"
            result = initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                workspace_mode="auto",
                max_file_bytes=10_000,
            )

            self.assertEqual(result.workspace.mode, "copy")
            self.assertEqual(result.workspace.requested_mode, "auto")
            self.assertEqual(result.workspace.selected_mode, "copy")
            self.assertTrue((result.workspace_dir / "spam_model.py").is_file())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["workspace"]["requested_mode"], "auto")
            self.assertEqual(manifest["workspace"]["selected_mode"], "copy")
            self.assertTrue(manifest["workspace"]["fallback_reason"])
            self.assertTrue(manifest["workspace"]["user_next_steps"])

    def test_init_can_create_sparse_copy_workspace(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "sparse_project"
            task_file = root / "task.md"
            write_text(code_root / "src" / "pkg" / "model.py", "def predict():\n    return 1\n")
            write_text(code_root / "tests" / "test_model.py", "def test_predict():\n    assert True\n")
            write_text(code_root / "benchmark.py", "print('accuracy: 1.0')\n")
            write_text(code_root / "pyproject.toml", "[project]\nname = 'sparse-project'\n")
            write_text(code_root / "data" / "dataset.csv", "id,label\n1,spam\n")
            write_text(code_root / "models" / "weights.bin", "not really weights\n")
            write_text(code_root / ".env", "TOKEN=secret\n")
            write_text(task_file, "# Task\n\nImprove sparse project.\n")

            run_dir = root / "runs" / "sparse-run"
            result = initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python benchmark.py",
                workspace_mode="sparse_copy",
                workspace_include=("src/**", "tests/**", "benchmark.py", "pyproject.toml"),
                workspace_exclude=("models/**",),
            )

            workspace = result.workspace_dir
            self.assertTrue((workspace / "src" / "pkg" / "model.py").is_file())
            self.assertTrue((workspace / "tests" / "test_model.py").is_file())
            self.assertTrue((workspace / "benchmark.py").is_file())
            self.assertTrue((workspace / "pyproject.toml").is_file())
            self.assertFalse((workspace / "data" / "dataset.csv").exists())
            self.assertFalse((workspace / "models" / "weights.bin").exists())
            self.assertFalse((workspace / ".env").exists())

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["workspace"]["mode"], "sparse_copy")
            self.assertEqual(
                manifest["workspace"]["patterns"]["include"],
                ["src/**", "tests/**", "benchmark.py", "pyproject.toml"],
            )
            self.assertIn("models/**", manifest["workspace"]["patterns"]["exclude"])
            skipped_reasons = {item["reason"] for item in manifest["copy"]["skipped"]}
            self.assertIn("sparse_excluded_dir", skipped_reasons)

    def test_code_task_init_cli_prints_summary(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            output_root = root / "runs"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove tests.\n")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--output-root",
                        str(output_root),
                        "--name",
                        "demo-code-task",
                    ]
                )

            output = stdout.getvalue()
            self.assertIn("Code task run:", output)
            self.assertIn("Workspace:", output)
            self.assertIn("Indexed:", output)
            self.assertIn("Repo map:", output)
            run_dir = next(output_root.iterdir())
            self.assertTrue((run_dir / "code_task" / "workspace" / "spam_model.py").is_file())
            self.assertTrue((run_dir / "code_task" / "meta" / "codebase_index.json").is_file())
            self.assertTrue((run_dir / "code_task" / "meta" / "repo_map.json").is_file())

            status_stdout = io.StringIO()
            with contextlib.redirect_stdout(status_stdout):
                main(["status", str(run_dir)])

            status_text = status_stdout.getvalue()
            self.assertIn("Workflow: code_task", status_text)
            self.assertIn("python files: 2", status_text)

    def test_code_task_map_rebuilds_repo_map_from_workspace(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nMap this project.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            write_text(
                run_dir / "code_task" / "workspace" / "feature.py",
                "def improve_feature(value):\n    return value + 1\n",
            )

            result = build_code_task_repo_map(run_dir)

            self.assertTrue(result.refreshed_index)
            repo_map = read_json(result.repo_map_path)
            mapped_files = {item["path"] for item in repo_map["files"]}
            self.assertIn("feature.py", mapped_files)
            symbols = {item["qualified_name"] for item in repo_map["symbols"]}
            self.assertIn("improve_feature", symbols)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["layout"]["repo_map"], "code_task/meta/repo_map.json")
            self.assertEqual(
                manifest["codebase"]["repo_map"]["summary"],
                "code_task/meta/repo_map_summary.md",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["code-task", "map", str(run_dir), "--no-refresh-index"])
            output = stdout.getvalue()
            self.assertIn("Repo map:", output)
            self.assertIn("Index refreshed: False", output)

    def test_code_task_locate_writes_ranked_targets_and_evidence(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove spam keyword prediction.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = locate_code_task_context(
                run_dir,
                query="improve spam keyword predict behavior",
                top_k=4,
            )

            self.assertTrue(result.results_path.is_file())
            self.assertTrue(result.summary_path.is_file())
            self.assertEqual(result.editable_targets[0]["path"], "spam_model.py")
            evidence_paths = {row["path"] for row in result.read_only_evidence}
            self.assertIn("tests/test_spam_model.py", evidence_paths)
            locate_data = read_json(result.results_path)
            self.assertEqual(locate_data["schema_version"], 1)
            self.assertIn("spam", locate_data["query_terms"])
            summary = read_text(result.summary_path)
            self.assertIn("# Locate Results", summary)
            self.assertIn("spam_model.py", summary)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["layout"]["locate_results"], "code_task/meta/locate_results.json")
            self.assertEqual(manifest["locate"]["status"], "completed")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(
                    [
                        "code-task",
                        "locate",
                        str(run_dir),
                        "--query",
                        "spam keyword",
                        "--top-k",
                        "3",
                    ]
                )
            output = stdout.getvalue()
            self.assertIn("Locate results:", output)
            self.assertIn("Editable targets:", output)

    def test_code_task_context_pack_writes_prompt_and_snippets(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove spam keyword prediction.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = build_code_task_context_pack(
                run_dir,
                query="improve spam keyword predict behavior",
                top_k=4,
                max_files=3,
                max_source_chars_per_file=600,
                max_total_chars=1200,
            )

            self.assertTrue(result.context_pack_path.is_file())
            self.assertTrue(result.prompt_context_path.is_file())
            self.assertTrue(result.snippets_path.is_file())
            self.assertIn("spam_model.py", result.selected_files)
            snippets = read_jsonl(result.snippets_path)
            snippet_paths = {row["path"] for row in snippets}
            self.assertIn("spam_model.py", snippet_paths)
            self.assertIn("tests/test_spam_model.py", snippet_paths)
            prompt_context = read_text(result.prompt_context_path)
            self.assertIn("# Code Task Context Pack", prompt_context)
            self.assertIn("## Editable Targets", prompt_context)
            self.assertIn("## Read-Only Evidence", prompt_context)
            context_pack = read_json(result.context_pack_path)
            self.assertEqual(context_pack["schema_version"], 1)
            self.assertLessEqual(context_pack["budget"]["used_chars"], 1200)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["layout"]["context_packs"], "code_task/context_packs")
            self.assertEqual(manifest["context_pack"]["status"], "completed")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(
                    [
                        "code-task",
                        "context",
                        str(run_dir),
                        "--query",
                        "spam keyword",
                        "--max-files",
                        "2",
                    ]
                )
            output = stdout.getvalue()
            self.assertIn("Context pack:", output)
            self.assertIn("Selected files:", output)

    def test_work_plan_offline_writes_batchable_items_and_manifest(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(
                task_file,
                "# Task\n\nImprove spam keyword prediction without changing tests.\n",
            )
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            build_code_task_context_pack(
                run_dir,
                query="improve spam keyword prediction",
                top_k=4,
                max_files=3,
            )

            result = generate_code_task_work_plan(run_dir, use_llm=False)

            self.assertEqual(result.mode, "offline")
            self.assertTrue(result.pending_approval)
            self.assertEqual(result.item_count, 1)
            self.assertTrue(result.work_plan_path.is_file())
            self.assertTrue(result.work_plan_markdown_path.is_file())
            plan = read_json(result.work_plan_path)
            self.assertEqual(plan["schema_version"], 1)
            self.assertEqual(plan["items"][0]["id"], "W1")
            self.assertIn("spam_model.py", plan["items"][0]["target_files"])
            self.assertNotIn("tests/test_spam_model.py", plan["items"][0]["target_files"])
            self.assertIn("tests/test_spam_model.py", plan["items"][0]["read_only_evidence"])
            markdown = read_text(result.work_plan_markdown_path)
            self.assertIn("# Work Plan", markdown)
            self.assertIn("## Work Items", markdown)
            self.assertIn("W1", markdown)

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "work_planned")
            self.assertEqual(manifest["layout"]["work_plan"], "code_task/work_plan.json")
            self.assertEqual(manifest["work_plan"]["status"], "pending_approval")
            self.assertEqual(manifest["work_plan"]["item_count"], 1)

    def test_work_plan_and_batch_cli_create_attempt_state(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            output_root = root / "runs"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove spam classifier accuracy.\n")

            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--output-root",
                        str(output_root),
                    ]
                )
            run_dir = next(output_root.iterdir())

            work_plan_stdout = io.StringIO()
            with contextlib.redirect_stdout(work_plan_stdout):
                main(["code-task", "work-plan", str(run_dir), "--no-llm"])

            self.assertIn("Work plan:", work_plan_stdout.getvalue())
            self.assertTrue((run_dir / "code_task" / "work_plan.json").is_file())

            batch_stdout = io.StringIO()
            with contextlib.redirect_stdout(batch_stdout):
                main(["code-task", "batch", str(run_dir), "--work-item", "W1"])

            output = batch_stdout.getvalue()
            self.assertIn("Attempt: attempt-001", output)
            self.assertIn("Batch: batch-001", output)
            attempt_state = read_json(
                run_dir / "code_task" / "attempts" / "attempt-001" / "attempt_state.json"
            )
            batch_state = read_json(
                run_dir
                / "code_task"
                / "attempts"
                / "attempt-001"
                / "batches"
                / "batch-001"
                / "batch_state.json"
            )
            self.assertEqual(attempt_state["state"], "batching")
            self.assertEqual(batch_state["state"], "created")
            self.assertEqual(batch_state["work_item_id"], "W1")
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "batch_created")
            self.assertEqual(manifest["attempts"]["active"], "attempt-001")
            self.assertIn("latest_batch", manifest["attempts"])
            self.assertNotIn("items", manifest["attempts"])

    def test_work_plan_escalates_budget_for_a_three_file_cohesive_item(self) -> None:
        items = _normalize_work_items(
            [
                {
                    "id": "W1",
                    "objective": "Change implementation and its configuration together.",
                    "target_files": ["features.py", "model.py", "config.json"],
                    "budget_profile": "normal",
                }
            ],
            {"features.py", "model.py", "config.json"},
            selected_files=["features.py", "model.py", "config.json"],
            allowed_patterns=(),
            protected_patterns=(),
        )

        self.assertEqual(items[0]["budget_profile"], "large")
        self.assertTrue(items[0]["requires_budget_override"])
        self.assertIn("more files than the normal", items[0]["suggested_budget_override"])

    def test_create_code_task_batch_reuses_existing_item_batch(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove spam classifier accuracy.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            generate_code_task_work_plan(run_dir, use_llm=False)

            first = create_code_task_batch(run_dir, work_item_id="W1")
            second = create_code_task_batch(run_dir, work_item_id="W1")
            forced = create_code_task_batch(run_dir, work_item_id="W1", force=True)

            self.assertEqual(first.batch_id, "batch-001")
            self.assertEqual(second.batch_id, "batch-001")
            self.assertEqual(forced.batch_id, "batch-002")
            self.assertTrue(forced.batch_state_path.is_file())

    def test_execute_selects_first_implementation_work_item(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove spam prediction.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            _write_analysis_first_work_plan(run_dir)

            result = execute_code_task(run_dir, use_llm=False, to_step="batch", timeout_sec=10)

            self.assertEqual(result.stop_reason, "stop_point")
            batch_state = read_json(
                run_dir
                / "code_task"
                / "attempts"
                / "attempt-001"
                / "batches"
                / "batch-001"
                / "batch_state.json"
            )
            self.assertEqual(batch_state["work_item_id"], "W2")
            self.assertIn("Implement", batch_state["work_item"]["objective"])

    def test_create_code_task_batch_merges_serial_dependent_items(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(code_root / "extra.py", "VALUE = 1\n")
            write_text(task_file, "# Task\n\nImplement a coupled feature, scorer, and config change.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            _write_dependent_work_plan(run_dir)

            result = create_code_task_batch(run_dir, work_item_id="W1")

            batch_state = read_json(result.batch_state_path)
            self.assertEqual(batch_state["work_item_id"], "W1")
            work_item = batch_state["work_item"]
            self.assertEqual(work_item["source_work_item_ids"], ["W1", "W2", "W3"])
            self.assertEqual(work_item["execution_scope"], "merged_dependent_chain")
            self.assertEqual(
                work_item["target_files"],
                ["spam_model.py", "extra.py", "pyproject.toml"],
            )
            self.assertEqual(work_item["budget_profile"], "large")
            self.assertTrue(work_item["requires_budget_override"])

    def test_create_code_task_batch_can_disable_serial_merge(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(code_root / "extra.py", "VALUE = 1\n")
            write_text(task_file, "# Task\n\nImplement a coupled feature safely.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            _write_dependent_work_plan(run_dir)

            result = create_code_task_batch(
                run_dir,
                work_item_id="W1",
                merge_dependent_chain=False,
            )

            batch_state = read_json(result.batch_state_path)
            work_item = batch_state["work_item"]
            self.assertEqual(work_item["source_work_item_ids"], ["W1"])
            self.assertEqual(work_item["execution_scope"], "single_work_item")
            self.assertEqual(work_item["target_files"], ["spam_model.py"])
            self.assertEqual(work_item["budget_profile"], "normal")
            self.assertFalse(work_item["requires_budget_override"])

    def test_record_plan_decision_updates_work_plan_approval(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nApprove both plans.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            generate_code_task_work_plan(run_dir, use_llm=False)
            generate_patch_plan(run_dir, use_llm=False)

            record_plan_decision(run_dir, decision="approve", note="ready")

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["plan"]["status"], "approved")
            self.assertEqual(manifest["work_plan"]["status"], "ready")
            self.assertEqual(manifest["work_plan"]["approval"]["status"], "approved")

    def test_probe_code_task_environment_writes_report_and_manifest(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nProbe this project.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = probe_code_task_environment(run_dir)

            self.assertTrue(result.report_path.is_file())
            self.assertIn(result.status, {"ok", "warning"})
            report = read_json(result.report_path)
            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(report["project"]["dependency_files"], ["pyproject.toml"])
            self.assertEqual(report["project"]["test_dirs"], ["tests"])
            self.assertTrue(report["tools"]["python"]["available"])
            self.assertEqual(report["execution_policy"]["mode"], "current")
            self.assertIn("available", report["gpu"])

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["layout"]["environment_report"], "code_task/meta/environment_report.json")
            self.assertEqual(manifest["environment"]["report"], "code_task/meta/environment_report.json")
            self.assertEqual(manifest["status"], "environment_probed")
            summary = read_text(run_dir / "code_task" / "summary.md")
            self.assertIn("## Environment", summary)
            self.assertIn("pyproject.toml", summary)

    def test_code_task_probe_cli_prints_environment_summary(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            output_root = root / "runs"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nProbe from CLI.\n")

            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--output-root",
                        str(output_root),
                    ]
                )
            run_dir = next(output_root.iterdir())

            probe_stdout = io.StringIO()
            with contextlib.redirect_stdout(probe_stdout):
                main(["code-task", "probe", str(run_dir)])

            output = probe_stdout.getvalue()
            self.assertIn("Environment report:", output)
            self.assertIn("Status:", output)
            status_stdout = io.StringIO()
            with contextlib.redirect_stdout(status_stdout):
                main(["status", str(run_dir)])
            self.assertIn("Environment:", status_stdout.getvalue())

    def test_patch_plan_offline_writes_reviewable_plan_and_updates_manifest(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(
                task_file,
                "# Task\n\nImprove spam keyword handling and keep the public predict API stable.\n",
            )
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = generate_patch_plan(run_dir, use_llm=False)

            self.assertEqual(result.mode, "offline")
            self.assertTrue(result.pending_approval)
            self.assertTrue((run_dir / "code_task" / "patch_plan.md").is_file())
            plan_text = read_text(run_dir / "code_task" / "patch_plan.md")
            self.assertIn("# Patch Plan", plan_text)
            self.assertIn("## Files To Modify", plan_text)
            self.assertIn("spam_model.py", plan_text)
            self.assertIn("python -m unittest discover -s tests", plan_text)

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "planned")
            self.assertEqual(manifest["plan"]["status"], "pending_approval")
            self.assertEqual(manifest["layout"]["patch_plan"], "code_task/patch_plan.md")

    def test_plan_and_propose_use_latest_context_pack_when_available(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove spam keyword prediction without editing tests.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            context = build_code_task_context_pack(
                run_dir,
                query="improve spam keyword prediction",
                top_k=4,
                max_files=3,
            )

            plan = generate_patch_plan(run_dir, use_llm=False)

            self.assertIn("spam_model.py", plan.selected_files)
            plan_text = read_text(plan.patch_plan_path)
            self.assertIn("Context pack:", plan_text)
            self.assertIn("code_task/context_packs/context-001/context_pack.json", plan_text)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(
                manifest["plan"]["context_pack"]["path"],
                "code_task/context_packs/context-001/context_pack.json",
            )
            self.assertEqual(
                manifest["plan"]["context_pack"]["prompt_context"],
                "code_task/context_packs/context-001/prompt_context.md",
            )

            record_plan_decision(run_dir, decision="approve")
            proposal = propose_patch_edits(run_dir, use_llm=False)

            self.assertEqual(proposal.edit_count, 0)
            proposal_data = read_json(proposal.proposal_path)
            self.assertEqual(
                proposal_data["context_pack"]["path"],
                "code_task/context_packs/context-001/context_pack.json",
            )
            self.assertEqual(proposal_data["selected_files"], ["spam_model.py"])
            self.assertIn("tests/test_spam_model.py", proposal_data["read_only_context"])
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(
                manifest["patch"]["context_pack"]["path"],
                "code_task/context_packs/context-001/context_pack.json",
            )
            self.assertTrue(context.context_pack_path.is_file())

    def test_propose_edits_restricts_llm_to_current_batch_targets(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(code_root / "extra.py", "VALUE = 1\n")
            write_text(task_file, "# Task\n\nImprove spam prediction only.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            generate_code_task_work_plan(run_dir, use_llm=False)
            work_plan_path = run_dir / "code_task" / "work_plan.json"
            work_plan = read_json(work_plan_path)
            work_plan["items"][0]["target_files"] = ["spam_model.py"]
            write_json(work_plan_path, work_plan)
            create_code_task_batch(run_dir, work_item_id="W1")
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            fake_client = _FakeRepairClient(
                {
                    "summary": "Try one valid batch edit and one unrelated edit.",
                    "edits": [
                        {
                            "path": "spam_model.py",
                            "old": (
                                "def predict(text):\n"
                                "    return 'spam' if 'win' in text.lower() else 'ham'\n"
                            ),
                            "new": (
                                "def predict(text):\n"
                                "    lowered = text.lower()\n"
                                "    return 'spam' if any(keyword in lowered for keyword in ('win', 'prize')) else 'ham'\n"
                            ),
                            "reason": "Improve the selected batch target.",
                        },
                        {
                            "path": "extra.py",
                            "old": "VALUE = 1\n",
                            "new": "VALUE = 2\n",
                            "reason": "This file is outside the current batch.",
                        },
                    ],
                    "validation": ["Run unit tests."],
                    "risks": [],
                }
            )

            with patch("simple_ar.code_task.editing.patching.LLMClient.from_env", return_value=fake_client):
                proposal = propose_patch_edits(run_dir, use_llm=True)

            self.assertEqual(proposal.edit_count, 1)
            data = read_json(proposal.proposal_path)
            self.assertEqual(data["editor"]["backend"], "controlled_patch")
            self.assertEqual([edit["path"] for edit in data["edits"]], ["spam_model.py"])
            self.assertIn(
                "Dropped edit outside current batch target files: extra.py",
                data["warnings"],
            )
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["patch"]["editor_backend"], "controlled_patch")
            self.assertEqual(manifest["patch"]["editor"]["backend"], "controlled_patch")
            batch_state = read_json(
                run_dir
                / "code_task"
                / "attempts"
                / "attempt-001"
                / "batches"
                / "batch-001"
                / "batch_state.json"
            )
            self.assertEqual(batch_state["state"], "proposal_ready")
            self.assertEqual(batch_state["editor"]["backend"], "controlled_patch")
            self.assertEqual(
                batch_state["artifacts"]["proposed_edits"],
                "code_task/attempts/attempt-001/batches/batch-001/proposed_edits.json",
            )
            batch_proposal = read_json(
                run_dir
                / "code_task"
                / "attempts"
                / "attempt-001"
                / "batches"
                / "batch-001"
                / "proposed_edits.json"
            )
            self.assertEqual(batch_proposal["editor"]["backend"], "controlled_patch")

    def test_propose_edits_budget_requires_large_approval(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nMake a deliberately large local implementation.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            generate_code_task_work_plan(run_dir, use_llm=False)
            create_code_task_batch(run_dir, work_item_id="W1")
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            large_new = "def predict(text):\n" + "    lowered = text.lower()\n" * 260 + "    return 'spam'\n"
            fake_client = _FakeRepairClient(
                {
                    "summary": "Large but localized function replacement.",
                    "edits": [
                        {
                            "path": "spam_model.py",
                            "old": (
                                "def predict(text):\n"
                                "    return 'spam' if 'win' in text.lower() else 'ham'\n"
                            ),
                            "new": large_new,
                            "reason": "Large local implementation.",
                        }
                    ],
                    "validation": ["Run unit tests."],
                    "risks": ["Large edit."],
                }
            )

            with patch("simple_ar.code_task.editing.patching.LLMClient.from_env", return_value=fake_client):
                blocked = propose_patch_edits(run_dir, use_llm=True)

            blocked_data = read_json(blocked.proposal_path)
            self.assertEqual(blocked.edit_count, 0)
            self.assertEqual(blocked_data["budget"]["status"], "large_requires_approval")
            self.assertTrue(blocked_data["budget"]["requires_approval"])
            self.assertIn("Proposal exceeds the selected edit budget", blocked_data["warnings"][0])

            with patch("simple_ar.code_task.editing.patching.LLMClient.from_env", return_value=fake_client):
                approved = propose_patch_edits(
                    run_dir,
                    use_llm=True,
                    force=True,
                    allow_large_edits=True,
                )

            approved_data = read_json(approved.proposal_path)
            self.assertEqual(approved.edit_count, 1)
            self.assertEqual(approved_data["budget"]["status"], "large_approved")
            self.assertTrue(approved_data["budget"]["approved"])



    def test_repair_consumes_external_measurement_without_a_legacy_run(self) -> None:
        from simple_ar.code_task.execution.repair import RepairEvidence
        from simple_ar.research.experiment import ExperimentRequest, run_experiment
        from simple_ar.experiment.execution.backend import RunRequest
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, task, run_dir = root / "project", root / "task.md", root / "run"
            _write_toy_project(project)
            (project / "spam_model.py").write_text("def predict(text):\n    return missing_name\n", encoding="utf-8")
            (project / "evaluate.py").write_text("from spam_model import predict\nprint(predict('prize'))\n", encoding="utf-8")
            task.write_text("Repair spam_model.py without changing evaluation.", encoding="utf-8")
            initialize_code_task(run_dir=run_dir, code_root=project, task_file=task,
                                 benchmark_command="python evaluate.py")
            paths = code_task_paths(run_dir)
            result = run_experiment(ExperimentRequest(RunRequest([sys.executable, "evaluate.py"], paths.workspace_dir, 5)))
            measured = result.to_dict()
            self.assertNotEqual(measured["returncode"], 0)
            evidence = RepairEvidence("attempts/experiment-1/results.json", measured, result.run.stderr)
            response = {"choices": [{"message": {"content": json.dumps({
                "summary": "Remove undefined variable", "edits": [{"path": "spam_model.py",
                "old": "return missing_name", "new": "return 'ham'", "reason": "NameError"}],
                "validation": ["Run evaluation separately"], "risks": ["Prediction quality not established"],
            })}}], "usage": {"prompt_tokens": 40, "completion_tokens": 30, "total_tokens": 70}}
            client = LLMClient(LLMSettings(api_key="test-key", api_mode="chat"))
            with patch("simple_ar.integrations.llm._call_openai_sdk", return_value=response) as transport, patch(
                "simple_ar.code_task.execution.repair.analyze_code_task_failure",
                side_effect=AssertionError("Do not rediscover a legacy run"),
            ):
                proposal = propose_repair_edits(run_dir, llm_client=client, failure_evidence=evidence)
            self.assertEqual(proposal.edit_count, 1)
            self.assertIn("NameError", json.dumps(transport.call_args.args[1]))
            saved = read_json(proposal.repair_dir / "failure_evidence.json")
            self.assertEqual(saved["execution_report"], measured)
            self.assertEqual(saved["source"], evidence.source)
            from simple_ar.integrations.llm import LLMError
            before_failure = read_json(run_dir / "manifest.json")
            with patch.object(LLMClient, "ask_json", side_effect=LLMError("provider unavailable")):
                with self.assertRaisesRegex(LLMError, "provider unavailable"):
                    propose_repair_edits(run_dir, llm_client=client, failure_evidence=evidence)
            after_failure = read_json(run_dir / "manifest.json")
            self.assertEqual(after_failure["repair"], before_failure["repair"])
            self.assertEqual(after_failure["status"], before_failure["status"])
            self.assertEqual(len(list(paths.repairs_dir.glob("*/proposed_edits.json"))), 1)
            self.assertIn("missing_name", (paths.workspace_dir / "spam_model.py").read_text())
            self.assertFalse((paths.run_artifact_dir / "patched/execution_report.json").exists())
            with self.assertRaisesRegex(ValueError, "valid low-scoring"):
                RepairEvidence("result", {**measured, "execution_status": "passed", "metrics": {"accuracy": 0}}, "No gain")
            timed_out = RepairEvidence("timed-out-result", {**measured, "execution_status": "timed_out"}, "Time limit reached")
            self.assertEqual(timed_out.execution_report["execution_status"], "timed_out")



    def test_patch_plan_includes_baseline_and_environment_context(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "metric_project"
            task_file = root / "task.md"
            _write_metric_project(code_root, value="0.50")
            write_text(task_file, "# Task\n\nImprove the printed accuracy metric.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python benchmark.py",
            )
            probe_code_task_environment(run_dir)
            run_code_task_baseline(run_dir, timeout_sec=10)

            result = generate_patch_plan(run_dir, use_llm=False)

            self.assertEqual(result.mode, "offline")
            plan_text = read_text(run_dir / "code_task" / "patch_plan.md")
            self.assertIn("## Run Context", plan_text)
            self.assertIn("Baseline metrics", plan_text)
            self.assertIn("`accuracy`=0.5", plan_text)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["plan"]["context"]["baseline_status"], "passed")
            self.assertEqual(manifest["plan"]["context"]["baseline_metrics"]["accuracy"], 0.5)

    def test_code_task_plan_and_decide_cli(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            output_root = root / "runs"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove spam classifier accuracy.\n")

            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--output-root",
                        str(output_root),
                    ]
                )
            run_dir = next(output_root.iterdir())

            plan_stdout = io.StringIO()
            with contextlib.redirect_stdout(plan_stdout):
                main(["code-task", "plan", str(run_dir), "--no-llm"])

            self.assertIn("Patch plan:", plan_stdout.getvalue())
            self.assertTrue((run_dir / "code_task" / "patch_plan.md").is_file())

            decide_stdout = io.StringIO()
            with contextlib.redirect_stdout(decide_stdout):
                main(
                    [
                        "code-task",
                        "decide-plan",
                        str(run_dir),
                        "--decision",
                        "approve",
                        "--note",
                        "Looks small enough.",
                    ]
                )

            self.assertIn("Decision: approve", decide_stdout.getvalue())
            decisions = read_jsonl(run_dir / "code_task" / "meta" / "hitl_decisions.jsonl")
            self.assertEqual(decisions[-1]["decision"], "approve")
            self.assertEqual(decisions[-1]["note"], "Looks small enough.")
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "plan_approved")
            self.assertEqual(manifest["plan"]["status"], "approved")

            status_stdout = io.StringIO()
            with contextlib.redirect_stdout(status_stdout):
                main(["status", str(run_dir)])
            self.assertIn("Plan:", status_stdout.getvalue())
            self.assertIn("status: approved", status_stdout.getvalue())

    def test_apply_edits_requires_approved_plan_and_then_patches_workspace(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nAlso detect prize as spam.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(run_dir=run_dir, code_root=code_root, task_file=task_file)
            generate_patch_plan(run_dir, use_llm=False)
            proposal_path = _write_valid_edit_proposal(run_dir)

            with self.assertRaises(PermissionError):
                apply_patch_edits(run_dir, edits_file=proposal_path)

            record_plan_decision(run_dir, decision="approve", note="Small targeted edit.")
            result = apply_patch_edits(run_dir, edits_file=proposal_path)

            workspace_model = run_dir / "code_task" / "workspace" / "spam_model.py"
            self.assertIn("'prize'", read_text(workspace_model))
            self.assertNotIn("'prize'", read_text(code_root / "spam_model.py"))
            self.assertEqual(result.changed_files, ("spam_model.py",))
            self.assertTrue((run_dir / "code_task" / "patch.diff").is_file())
            self.assertTrue((run_dir / "code_task" / "meta" / "applied_edits.json").is_file())
            self.assertFalse((run_dir / "code_task" / "meta" / "pre_patch_manifest.json").exists())
            self.assertFalse((run_dir / "code_task" / "meta" / "post_patch_manifest.json").exists())
            applied = read_json(run_dir / "code_task" / "meta" / "applied_edits.json")
            self.assertEqual(applied["changed_files"], ["spam_model.py"])
            self.assertEqual(applied["editor"]["backend"], "controlled_patch")
            self.assertEqual(applied["editor"]["source"], "legacy_or_manual_proposal")
            self.assertTrue(applied["edits"][0]["old_sha256"])
            self.assertTrue(applied["edits"][0]["new_sha256"])
            self.assertEqual(applied["snapshot"]["captured_count"], 1)
            snapshot_path = Path(applied["snapshot"]["manifest"])
            self.assertTrue(snapshot_path.is_file())
            snapshot = read_json(snapshot_path)
            self.assertEqual(snapshot["files"][0]["path"], "spam_model.py")
            self.assertEqual(snapshot["files"][0]["kind"], "file")
            diff_text = read_text(run_dir / "code_task" / "patch.diff")
            self.assertIn("+    lowered = text.lower()", diff_text)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "patched")
            self.assertEqual(manifest["patch"]["status"], "applied")
            self.assertEqual(manifest["patch"]["editor_backend"], "controlled_patch")
            self.assertEqual(manifest["patch"]["editor"]["backend"], "controlled_patch")
            self.assertNotIn("pre_patch_manifest", manifest["patch"])
            self.assertNotIn("post_patch_manifest", manifest["patch"])

    def test_apply_large_edits_records_apply_time_approval(self) -> None:
        from simple_ar.code_task.editing.patching import EditBudgetApprovalRequired
        from simple_ar.code_task.orchestration.execute import implement_code_task
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nApply a reviewed large proposal.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(run_dir=run_dir, code_root=code_root, task_file=task_file)
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            proposal_path = _write_valid_edit_proposal(run_dir)
            proposal = read_json(proposal_path)
            proposal["budget"] = {
                "status": "accepted",
                "profile": "large",
                "requires_approval": True,
                "approved": False,
            }
            write_json(proposal_path, proposal)

            with self.assertRaises(EditBudgetApprovalRequired):
                apply_patch_edits(run_dir, edits_file=proposal_path)

            pending = implement_code_task(run_dir, approval_note="Review this isolated patch", use_llm=False)
            self.assertEqual(pending.stop_reason, "large_edit_approval_required")
            self.assertFalse((run_dir / "code_task" / "meta" / "applied_edits.json").exists())

            apply_patch_edits(run_dir, edits_file=proposal_path, allow_large_edits=True)

            applied = read_json(run_dir / "code_task" / "meta" / "applied_edits.json")
            self.assertTrue(applied["budget"]["approved"])
            self.assertEqual(applied["budget"]["approval_source"], "apply_edits_allow_large_edits")
            manifest = read_json(run_dir / "manifest.json")
            self.assertTrue(manifest["patch"]["budget"]["approved"])
            self.assertEqual(
                manifest["patch"]["budget"]["approval_source"],
                "apply_edits_allow_large_edits",
            )

    def test_apply_repair_proposal_records_latest_applied_proposal(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nApply a reviewed repair proposal.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(run_dir=run_dir, code_root=code_root, task_file=task_file)
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            repair_dir = run_dir / "code_task" / "repairs" / "repair-001"
            repair_dir.mkdir(parents=True)
            repair_proposal = _write_valid_edit_proposal(run_dir, path=repair_dir / "proposed_edits.json")
            manifest = read_json(run_dir / "manifest.json")
            manifest["repair"] = {
                "status": "repair_proposed",
                "repair_count": 1,
                "latest_proposed_edits": "code_task/repairs/repair-001/proposed_edits.json",
            }
            write_json(run_dir / "manifest.json", manifest)

            apply_patch_edits(run_dir, edits_file=repair_proposal)

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["patch"]["latest_applied_proposal"], "code_task/repairs/repair-001/proposed_edits.json")
            self.assertEqual(manifest["repair"]["status"], "repair_applied")
            self.assertEqual(
                manifest["repair"]["latest_applied_proposal"],
                "code_task/repairs/repair-001/proposed_edits.json",
            )
            applied = read_json(run_dir / "code_task" / "meta" / "applied_edits.json")
            self.assertEqual(applied["proposal"], "code_task/repairs/repair-001/proposed_edits.json")

    def test_apply_edits_allows_multiple_ordered_edits_in_one_file(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nApply two edits in the same file.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(run_dir=run_dir, code_root=code_root, task_file=task_file)
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            proposal_path = run_dir / "code_task" / "meta" / "proposed_edits.json"
            write_json(
                proposal_path,
                {
                    "edits": [
                        {
                            "path": "spam_model.py",
                            "old": "import math\n\n\n",
                            "new": "import math\n\nSPAM_KEYWORDS = ('win', 'prize')\n\n",
                            "reason": "Add shared keyword configuration.",
                        },
                        {
                            "path": "spam_model.py",
                            "old": (
                                "def predict(text):\n"
                                "    return 'spam' if 'win' in text.lower() else 'ham'\n"
                            ),
                            "new": (
                                "def predict(text):\n"
                                "    lowered = text.lower()\n"
                                "    return 'spam' if any(keyword in lowered for keyword in SPAM_KEYWORDS) else 'ham'\n"
                            ),
                            "reason": "Use the shared keyword configuration.",
                        },
                    ]
                },
            )

            result = apply_patch_edits(run_dir, edits_file=proposal_path)

            self.assertEqual(result.changed_files, ("spam_model.py",))
            text = read_text(run_dir / "code_task" / "workspace" / "spam_model.py")
            self.assertIn("SPAM_KEYWORDS", text)
            self.assertIn("any(keyword in lowered", text)
            applied = read_json(run_dir / "code_task" / "meta" / "applied_edits.json")
            self.assertEqual(applied["edit_count"], 2)
            self.assertEqual(applied["changed_files"], ["spam_model.py"])
            diff_text = read_text(run_dir / "code_task" / "patch.diff")
            self.assertEqual(diff_text.count("--- a/spam_model.py"), 1)

    def test_apply_edits_rejects_path_traversal_without_modifying_workspace(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nTry an unsafe edit.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(run_dir=run_dir, code_root=code_root, task_file=task_file)
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            proposal_path = root / "bad_edits.json"
            write_json(
                proposal_path,
                {
                    "edits": [
                        {
                            "path": "../outside.py",
                            "old": "x",
                            "new": "y",
                            "reason": "unsafe",
                        }
                    ]
                },
            )
            before = read_text(run_dir / "code_task" / "workspace" / "spam_model.py")

            with self.assertRaises(PatchValidationError):
                apply_patch_edits(run_dir, edits_file=proposal_path)

            after = read_text(run_dir / "code_task" / "workspace" / "spam_model.py")
            self.assertEqual(before, after)
            self.assertFalse((run_dir / "code_task" / "patch.diff").exists())

    def test_apply_edits_rejects_protected_test_and_benchmark_files(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(code_root / "benchmark.py", "print('accuracy: 0.5')\n")
            write_text(task_file, "# Task\n\nImprove source behavior without changing validation targets.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(run_dir=run_dir, code_root=code_root, task_file=task_file)
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            proposal_path = root / "protected_edits.json"
            write_json(
                proposal_path,
                {
                    "edits": [
                        {
                            "path": "tests/test_spam_model.py",
                            "old": "        self.assertEqual(predict('win now'), 'spam')\n",
                            "new": "        self.assertEqual(predict('win now'), 'ham')\n",
                            "reason": "Should be blocked because tests are read-only evidence.",
                        },
                        {
                            "path": "benchmark.py",
                            "old": "print('accuracy: 0.5')\n",
                            "new": "print('accuracy: 1.0')\n",
                            "reason": "Should be blocked because benchmarks are read-only evidence.",
                        },
                    ]
                },
            )

            before_test = read_text(run_dir / "code_task" / "workspace" / "tests" / "test_spam_model.py")
            before_benchmark = read_text(run_dir / "code_task" / "workspace" / "benchmark.py")
            with self.assertRaises(PatchValidationError) as caught:
                apply_patch_edits(run_dir, edits_file=proposal_path)

            self.assertIn("path is protected by the edit scope", str(caught.exception))
            self.assertEqual(
                before_test,
                read_text(run_dir / "code_task" / "workspace" / "tests" / "test_spam_model.py"),
            )
            self.assertEqual(
                before_benchmark,
                read_text(run_dir / "code_task" / "workspace" / "benchmark.py"),
            )
            self.assertFalse((run_dir / "code_task" / "patch.diff").exists())

    def test_propose_edits_drops_protected_llm_paths(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove the spam model without changing tests.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(run_dir=run_dir, code_root=code_root, task_file=task_file)
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")

            fake_client = _FakeRepairClient(
                {
                    "summary": "Attempt one valid edit and one protected edit.",
                    "edits": [
                        {
                            "path": "spam_model.py",
                            "old": (
                                "def predict(text):\n"
                                "    return 'spam' if 'win' in text.lower() else 'ham'\n"
                            ),
                            "new": (
                                "def predict(text):\n"
                                "    lowered = text.lower()\n"
                                "    return 'spam' if 'win' in lowered or 'prize' in lowered else 'ham'\n"
                            ),
                            "reason": "Improve source behavior.",
                        },
                        {
                            "path": "tests/test_spam_model.py",
                            "old": "        self.assertEqual(predict('win now'), 'spam')\n",
                            "new": "        self.assertEqual(predict('win now'), 'ham')\n",
                            "reason": "This protected edit should be dropped.",
                        },
                    ],
                    "validation": ["Run tests."],
                    "risks": ["Changing tests would invalidate evidence."],
                }
            )

            with patch("simple_ar.code_task.editing.patching.LLMClient.from_env", return_value=fake_client):
                result = propose_patch_edits(run_dir, use_llm=True)

            proposal = read_json(result.proposal_path)
            self.assertEqual(result.edit_count, 1)
            self.assertEqual([item["path"] for item in proposal["edits"]], ["spam_model.py"])
            self.assertIn(
                "Dropped edit for protected read-only path: tests/test_spam_model.py",
                proposal["warnings"],
            )

    def test_code_task_propose_and_apply_cli_with_manual_edits_file(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            output_root = root / "runs"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nDetect prize messages as spam.\n")
            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--output-root",
                        str(output_root),
                    ]
                )
            run_dir = next(output_root.iterdir())
            with contextlib.redirect_stdout(io.StringIO()):
                main(["code-task", "plan", str(run_dir), "--no-llm"])
                main(["code-task", "decide-plan", str(run_dir), "--decision", "approve"])

            propose_stdout = io.StringIO()
            with contextlib.redirect_stdout(propose_stdout):
                main(["code-task", "propose-edits", str(run_dir), "--no-llm"])
            self.assertIn("Edit count: 0", propose_stdout.getvalue())
            self.assertTrue((run_dir / "code_task" / "meta" / "proposed_edits.json").is_file())

            edits_file = _write_valid_edit_proposal(run_dir, path=root / "manual_edits.json")
            apply_stdout = io.StringIO()
            with contextlib.redirect_stdout(apply_stdout):
                main(["code-task", "apply-edits", str(run_dir), "--edits-file", str(edits_file)])

            self.assertIn("Changed files: 1", apply_stdout.getvalue())
            status_stdout = io.StringIO()
            with contextlib.redirect_stdout(status_stdout):
                main(["status", str(run_dir)])
            self.assertIn("Patch:", status_stdout.getvalue())
            self.assertIn("status: applied", status_stdout.getvalue())

    def test_code_task_apply_cli_reports_validation_errors_without_traceback(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            output_root = root / "runs"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nDetect prize messages as spam.\n")
            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--output-root",
                        str(output_root),
                    ]
                )
            run_dir = next(output_root.iterdir())
            with contextlib.redirect_stdout(io.StringIO()):
                main(["code-task", "plan", str(run_dir), "--no-llm"])
                main(["code-task", "decide-plan", str(run_dir), "--decision", "approve"])

            edits_file = root / "bad_edits.json"
            write_json(
                edits_file,
                {
                    "schema_version": 1,
                    "edits": [
                        {
                            "path": "spam_model.py",
                            "old": "def missing():\n    pass\n",
                            "new": "def missing():\n    return None\n",
                            "reason": "This cannot match the workspace.",
                        }
                    ],
                },
            )

            apply_stdout = io.StringIO()
            with contextlib.redirect_stdout(apply_stdout):
                with self.assertRaises(SystemExit) as caught:
                    main(["code-task", "apply-edits", str(run_dir), "--edits-file", str(edits_file)])

            self.assertEqual(caught.exception.code, 1)
            output = apply_stdout.getvalue()
            self.assertIn("Patch validation failed; no workspace files were changed.", output)
            self.assertIn("old text was not found", output)

    def test_validate_code_task_reports_warnings_and_strict_errors(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(
                code_root / "danger.py",
                "import os\n\n\ndef run():\n    os.system('echo unsafe')\n",
            )
            write_text(task_file, "# Task\n\nValidate risky code.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(run_dir=run_dir, code_root=code_root, task_file=task_file)

            result = validate_code_task(run_dir)

            self.assertEqual(result.status, "passed")
            self.assertEqual(result.error_count, 0)
            self.assertGreaterEqual(result.warning_count, 1)
            report = read_json(run_dir / "code_task" / "meta" / "validation_report.json")
            self.assertTrue(any(item["code"] == "risky_call" for item in report["issues"]))

            strict = validate_code_task(run_dir, strict=True)
            self.assertEqual(strict.status, "failed")
            self.assertGreaterEqual(strict.error_count, 1)

    def test_run_code_task_benchmark_captures_outputs_and_updates_status(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nRun existing tests.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = run_code_task_benchmark(run_dir, timeout_sec=10)

            self.assertEqual(result.label, "patched")
            self.assertEqual(result.status, "passed")
            self.assertEqual(result.returncode, 0)
            self.assertTrue((run_dir / "code_task" / "run" / "patched" / "execution_report.json").is_file())
            self.assertTrue((run_dir / "code_task" / "run" / "patched" / "stdout.txt").is_file())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["benchmark"]["last_status"], "passed")
            self.assertEqual(manifest["benchmark"]["latest_label"], "patched")
            self.assertEqual(manifest["benchmark"]["runs"]["patched"]["status"], "passed")
            report = read_json(run_dir / "code_task" / "run" / "patched" / "execution_report.json")
            self.assertEqual(report["environment"]["mode"], "current")
            self.assertEqual(report["command"][0], sys.executable)
            self.assertEqual(manifest["status"], "benchmark_passed")
            attempt_1 = run_dir / "code_task" / "run" / "patched" / "attempts" / "attempt-001"
            self.assertTrue((attempt_1 / "execution_report.json").is_file())
            self.assertTrue((attempt_1 / "stdout.txt").is_file())
            self.assertTrue((attempt_1 / "stderr.txt").is_file())
            self.assertEqual(report["history_attempt"], "attempt-001")

            second = run_code_task_benchmark(run_dir, timeout_sec=10)

            self.assertEqual(second.status, "passed")
            attempt_2 = run_dir / "code_task" / "run" / "patched" / "attempts" / "attempt-002"
            self.assertTrue((attempt_2 / "execution_report.json").is_file())
            self.assertTrue((attempt_2 / "stdout.txt").is_file())
            manifest = read_json(run_dir / "manifest.json")
            run_record = manifest["benchmark"]["runs"]["patched"]
            self.assertEqual(run_record["latest_attempt"], "attempt-002")
            self.assertEqual(run_record["attempt_count"], 2)
            self.assertEqual(len(run_record["attempts"]), 2)
            latest_report = read_json(run_dir / "code_task" / "run" / "patched" / "execution_report.json")
            self.assertEqual(latest_report["history_attempt"], "attempt-002")

    def test_run_code_task_benchmark_stops_warning_flood_with_watchdog(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nStop noisy runaway benchmark output.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python warning_flood.py",
            )
            write_text(
                run_dir / "code_task" / "workspace" / "warning_flood.py",
                (
                    "import sys, time\n"
                    "for _ in range(10000):\n"
                    "    print('ConvergenceWarning: STOP: TOTAL NO. OF ITERATIONS REACHED LIMIT', file=sys.stderr, flush=True)\n"
                    "    time.sleep(0.001)\n"
                ),
            )

            result = run_code_task_benchmark(run_dir, timeout_sec=30, skip_validation=True)

            self.assertEqual(result.status, "failed")
            report = read_json(result.report_path)
            self.assertEqual(report["runtime_watchdog"]["reason"], "warning_flood")
            self.assertIn("Runtime output watchdog", read_text(result.stderr_path))

    def test_run_code_task_baseline_records_pre_patch_result(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nCapture baseline.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = run_code_task_baseline(run_dir, timeout_sec=10)

            self.assertEqual(result.label, "baseline")
            self.assertEqual(result.status, "passed")
            self.assertTrue((run_dir / "code_task" / "run" / "baseline" / "execution_report.json").is_file())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "baseline_passed")
            self.assertEqual(manifest["benchmark"]["latest_label"], "baseline")
            self.assertEqual(manifest["benchmark"]["runs"]["baseline"]["status"], "passed")
            summary = read_text(run_dir / "code_task" / "summary.md")
            self.assertIn("### Baseline", summary)
            self.assertIn("Environment mode: `current`", summary)

    def test_patched_run_writes_comparison_when_baseline_exists(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "metric_project"
            task_file = root / "task.md"
            _write_metric_project(code_root, value="0.50")
            write_text(task_file, "# Task\n\nImprove the printed accuracy metric.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python benchmark.py",
            )

            baseline = run_code_task_baseline(run_dir, timeout_sec=10)
            self.assertEqual(baseline.metrics["accuracy"], 0.5)
            write_text(run_dir / "code_task" / "workspace" / "metric_value.txt", "0.80\n")
            patched = run_code_task_benchmark(run_dir, timeout_sec=10)

            self.assertEqual(patched.metrics["accuracy"], 0.8)
            comparison_path = run_dir / "code_task" / "run" / "comparison.json"
            self.assertTrue(comparison_path.is_file())
            comparison = read_json(comparison_path)
            self.assertEqual(comparison["verdict"], "improved")
            self.assertAlmostEqual(comparison["deltas"]["accuracy"], 0.3)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["benchmark"]["comparison"]["verdict"], "improved")
            self.assertEqual(manifest["layout"]["comparison"], "code_task/run/comparison.json")
            summary = read_text(run_dir / "code_task" / "summary.md")
            self.assertIn("## Result", summary)
            self.assertIn("Outcome: `improved`", summary)
            self.assertIn("Next step:", summary)
            self.assertIn("### Comparison", summary)
            self.assertIn("Verdict: `improved`", summary)
            self.assertIn("+0.3", summary)

    def test_patched_regression_sets_objective_status(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "metric_project"
            task_file = root / "task.md"
            _write_metric_project(code_root, value="0.80")
            write_text(task_file, "# Task\n\nImprove the printed accuracy metric.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python benchmark.py",
            )
            run_code_task_baseline(run_dir, timeout_sec=10)
            manifest = read_json(run_dir / "manifest.json")
            manifest["failure_analysis"] = {
                "status": "needs_repair",
                "analysis": "code_task/run/patched/failure_analysis.md",
            }
            manifest["repair"] = {
                "status": "repair_applied",
                "repair_count": 1,
                "latest_proposed_edits": "code_task/repairs/repair-001/proposed_edits.json",
            }
            write_json(run_dir / "manifest.json", manifest)
            failure_path = run_dir / "code_task" / "run" / "patched" / "failure_analysis.md"
            write_text(failure_path, "# Failure Analysis\n\nOld failure.\n")
            write_text(run_dir / "code_task" / "workspace" / "metric_value.txt", "0.50\n")

            run_code_task_benchmark(run_dir, timeout_sec=10)

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "objective_regressed")
            self.assertEqual(manifest["objective"]["status"], "regressed")
            self.assertEqual(manifest["failure_analysis"]["status"], "resolved")
            self.assertEqual(manifest["repair"]["status"], "benchmark_passed")
            summary = read_text(run_dir / "code_task" / "summary.md")
            self.assertIn("Outcome: `regressed`", summary)
            self.assertIn("Patched status: `passed`", summary)
            self.assertIn("Verdict: `regressed`", summary)
            self.assertNotIn("Blocker:", summary)
            self.assertNotIn("Evidence-chain gap:", summary)
            self.assertIn("Attempted repairs: existing-project=1", summary)
            self.assertNotIn("## Failure Analysis", summary)
            self.assertNotIn("## Repair", summary)

    def test_manual_validate_and_run_sync_latest_batch_state(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nDetect prize messages as spam.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            generate_code_task_work_plan(run_dir, use_llm=False)
            create_code_task_batch(run_dir, work_item_id="W1")
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            apply_patch_edits(run_dir, edits_file=_write_valid_edit_proposal(run_dir))

            validate_code_task(run_dir)
            batch_path = (
                run_dir
                / "code_task"
                / "attempts"
                / "attempt-001"
                / "batches"
                / "batch-001"
                / "batch_state.json"
            )
            self.assertEqual(read_json(batch_path)["state"], "validating")
            run_code_task_benchmark(run_dir, timeout_sec=10)

            self.assertEqual(read_json(batch_path)["state"], "completed")
            attempt = read_json(run_dir / "code_task" / "attempts" / "attempt-001" / "attempt_state.json")
            self.assertEqual(attempt["state"], "completed")
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["work_plan"]["status"], "completed")

    def test_comparison_uses_configured_direction_for_custom_metric(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "metric_project"
            task_file = root / "task.md"
            _write_metric_project(
                code_root,
                value="10.0",
                metric_name="custom_reward",
            )
            write_text(task_file, "# Task\n\nImprove the custom reward metric.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python benchmark.py",
                primary_metric="custom_reward",
                metric_directions={"custom_reward": "higher"},
            )

            baseline = run_code_task_baseline(run_dir, timeout_sec=10)
            self.assertEqual(baseline.metrics["custom_reward"], 10.0)
            write_text(
                run_dir / "code_task" / "workspace" / "metric_value.txt",
                "12.5\n",
            )
            run_code_task_benchmark(run_dir, timeout_sec=10)

            comparison = read_json(run_dir / "code_task" / "run" / "comparison.json")
            self.assertEqual(comparison["verdict"], "improved")
            self.assertEqual(comparison["metric_config"]["primary_metric"], "custom_reward")
            row = comparison["metrics"][0]
            self.assertEqual(row["name"], "custom_reward")
            self.assertEqual(row["direction"], "higher_is_better")
            self.assertEqual(row["direction_source"], "configured")
            self.assertEqual(row["interpretation"], "improved")
            self.assertEqual(row["is_primary"], True)
            summary = read_text(run_dir / "code_task" / "summary.md")
            self.assertIn("Primary metric: `custom_reward` (higher_is_better)", summary)
            self.assertIn("Outcome: `improved`", summary)

            status_stdout = io.StringIO()
            with contextlib.redirect_stdout(status_stdout):
                main(["status", str(run_dir)])
            status = status_stdout.getvalue()
            self.assertIn("- summary:", status)
            self.assertIn("- primary metric: custom_reward", status)
            self.assertIn("- comparison: improved", status)
            self.assertIn("custom_reward=+2.5", status)

    def test_unknown_metric_is_recorded_but_not_overinterpreted(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "metric_project"
            task_file = root / "task.md"
            _write_metric_project(code_root, value="10.0", metric_name="custom_reward")
            write_text(task_file, "# Task\n\nImprove an unknown custom metric.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python benchmark.py",
            )

            run_code_task_baseline(run_dir, timeout_sec=10)
            write_text(
                run_dir / "code_task" / "workspace" / "metric_value.txt",
                "12.5\n",
            )
            run_code_task_benchmark(run_dir, timeout_sec=10)

            comparison = read_json(run_dir / "code_task" / "run" / "comparison.json")
            self.assertEqual(comparison["verdict"], "inconclusive")
            self.assertEqual(comparison["deltas"]["custom_reward"], 2.5)
            self.assertEqual(comparison["metrics"][0]["direction"], "unknown")
            self.assertEqual(comparison["metrics"][0]["interpretation"], "changed")

    def test_code_task_init_cli_records_metric_config(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "metric_project"
            task_file = root / "task.md"
            output_root = root / "runs"
            _write_metric_project(code_root, value="0.50", metric_name="macro_f1")
            write_text(task_file, "# Task\n\nImprove macro F1.\n")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--output-root",
                        str(output_root),
                        "--benchmark-command",
                        "python benchmark.py",
                        "--primary-metric",
                        "macro_f1",
                        "--metric-direction",
                        "macro_f1=higher",
                        "--metric-direction",
                        "inference_time_ms=resource",
                    ]
                )

            run_dir = next(output_root.iterdir())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["benchmark"]["primary_metric"], "macro_f1")
            self.assertEqual(
                manifest["benchmark"]["metric_directions"]["macro_f1"],
                "higher_is_better",
            )
            self.assertEqual(
                manifest["benchmark"]["metric_directions"]["inference_time_ms"],
                "resource",
            )
            output = stdout.getvalue()
            self.assertIn("Primary metric:", output)
            self.assertIn("macro_f1", output)
            self.assertIn("Metric directions:", output)

    def test_code_task_init_cli_reads_toml_config(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "metric_project"
            task_file = root / "task.md"
            output_root = root / "configured_runs"
            config_file = root / "code_task.toml"
            _write_metric_project(code_root, value="10.0", metric_name="custom_reward")
            write_text(task_file, "# Task\n\nImprove configured reward.\n")
            write_text(
                config_file,
                (
                    "[code_task]\n"
                    f'code_root = "{code_root.as_posix()}"\n'
                    f'task_file = "{task_file.as_posix()}"\n'
                    f'output_root = "{output_root.as_posix()}"\n'
                    'name = "configured-metric-task"\n'
                    "\n"
                    "[benchmark]\n"
                    'command = "python benchmark.py"\n'
                    'primary_metric = "custom_reward"\n'
                    "\n"
                    "[benchmark.metric_directions]\n"
                    'custom_reward = "higher"\n'
                    'latency_ms = "resource"\n'
                    "\n"
                    "[environment]\n"
                    'mode = "current"\n'
                    "\n"
                    "[safety]\n"
                    "max_file_bytes = 10000\n"
                ),
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["code-task", "init", "--config", str(config_file)])

            run_dir = next(output_root.iterdir())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["benchmark"]["command"], "python benchmark.py")
            self.assertEqual(manifest["benchmark"]["primary_metric"], "custom_reward")
            self.assertEqual(
                manifest["benchmark"]["metric_directions"]["custom_reward"],
                "higher_is_better",
            )
            self.assertEqual(
                manifest["benchmark"]["metric_directions"]["latency_ms"],
                "resource",
            )
            self.assertEqual(manifest["copy"]["max_file_bytes"], 10000)
            self.assertIn("Config:", stdout.getvalue())

    def test_code_task_config_rejects_wrong_section_types(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            config_file = root / "code_task.toml"
            write_text(
                config_file,
                (
                    "[code_task]\n"
                    f'code_root = "{root.as_posix()}"\n'
                    "task_file = [\"not\", \"a\", \"path\"]\n"
                ),
            )

            with self.assertRaises(CodeTaskConfigError) as raised:
                load_code_task_init_options(config_path=str(config_file))

            self.assertIn("Invalid code-task config", str(raised.exception))
            self.assertIn("task_file", str(raised.exception))

    def test_external_env_mode_records_python_policy_and_uses_it(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nRun with an explicit interpreter.\n")
            run_dir = root / "runs" / "code-task-run"
            expected_python = str(Path(sys.executable).resolve())
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
                env_mode="external",
                python_executable=sys.executable,
            )

            result = probe_code_task_environment(run_dir)
            self.assertIn(result.status, {"ok", "warning"})
            baseline = run_code_task_baseline(run_dir, timeout_sec=10)

            report = read_json(baseline.report_path)
            self.assertEqual(report["environment"]["mode"], "external")
            self.assertEqual(report["environment"]["python_executable"], expected_python)
            self.assertEqual(report["command"][0], expected_python)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["environment"]["policy"]["mode"], "external")
            self.assertEqual(
                manifest["benchmark"]["runs"]["baseline"]["environment"]["mode"],
                "external",
            )

    def test_code_task_cli_can_override_env_mode_for_baseline(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            output_root = root / "runs"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nRun CLI baseline with explicit env.\n")
            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--output-root",
                        str(output_root),
                        "--benchmark-command",
                        "python -m unittest discover -s tests",
                    ]
                )
            run_dir = next(output_root.iterdir())

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(
                    [
                        "code-task",
                        "baseline",
                        str(run_dir),
                        "--timeout",
                        "10",
                        "--env-mode",
                        "external",
                        "--python",
                        sys.executable,
                    ]
                )

            self.assertIn("Status: passed", stdout.getvalue())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["environment"]["policy"]["mode"], "external")

    def test_analyze_failure_and_offline_repair_proposal(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nBreak then diagnose the spam classifier.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            apply_patch_edits(run_dir, edits_file=_write_failing_edit_proposal(run_dir))
            failed = run_code_task_benchmark(run_dir, timeout_sec=10)
            self.assertEqual(failed.status, "failed")

            analysis = analyze_code_task_failure(run_dir)

            self.assertEqual(analysis.status, "needs_repair")
            self.assertEqual(analysis.source, "benchmark")
            analysis_text = read_text(analysis.analysis_path)
            self.assertIn("# Failure Analysis", analysis_text)
            self.assertIn("AssertionError", analysis_text)
            history_dir = run_dir / "code_task" / "run" / "patched" / "attempts" / "attempt-001"
            self.assertTrue((history_dir / "failure_analysis.md").is_file())
            self.assertTrue((history_dir / "failure_graph.json").is_file())
            manifest = read_json(run_dir / "manifest.json")
            latest_attempt = manifest["benchmark"]["runs"]["patched"]["attempts"][0]
            self.assertEqual(
                latest_attempt["failure_analysis"],
                "code_task/run/patched/attempts/attempt-001/failure_analysis.md",
            )

            repair = propose_repair_edits(run_dir, use_llm=False)
            self.assertEqual(repair.mode, "offline")
            self.assertEqual(repair.edit_count, 0)
            self.assertTrue((repair.repair_dir / "proposed_edits.json").is_file())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["repair"]["status"], "repair_proposed")
            self.assertIn("## Repair", read_text(run_dir / "code_task" / "summary.md"))

    def test_failure_analysis_prefers_runtime_stderr_over_validation_warning(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nDiagnose runtime stderr before validation warnings.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python broken_runtime.py",
            )
            write_text(
                run_dir / "code_task" / "workspace" / "broken_runtime.py",
                (
                    "import sys\n"
                    "print(\"Experiment failed: 'str' object has no attribute 'X'\", file=sys.stderr)\n"
                    "raise SystemExit(1)\n"
                ),
            )
            failed = run_code_task_benchmark(run_dir, timeout_sec=10, skip_validation=True)
            self.assertEqual(failed.status, "failed")

            analysis = analyze_code_task_failure(run_dir)

            self.assertEqual(analysis.status, "needs_repair")
            analysis_text = read_text(analysis.analysis_path)
            self.assertIn("Experiment failed: 'str' object has no attribute 'X'", analysis_text)
            self.assertNotIn("strongest error signal is: `warning", analysis_text)

    def test_greenfield_run_repair_analyzes_failure_when_budget_exhausted(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nDiagnose final failure even when repair budget is gone.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python broken_runtime.py",
            )
            write_text(
                run_dir / "code_task" / "workspace" / "broken_runtime.py",
                "print(\"ERROR: final budget exhausted signal\")\nraise SystemExit(1)\n",
            )
            failed = run_code_task_benchmark(run_dir, timeout_sec=10, skip_validation=True)
            self.assertEqual(failed.status, "failed")

            repaired = _attempt_greenfield_run_repair(
                run_dir,
                code_task_paths(run_dir),
                [],
                repair_rounds=0,
                model=None,
                repair_model=None,
                use_llm=False,
                message_callback=None,
            )

            self.assertFalse(repaired)
            analysis_text = read_text(run_dir / "code_task" / "run" / "patched" / "failure_analysis.md")
            self.assertIn("final budget exhausted signal", analysis_text)

    def test_execute_runs_to_approval_gate(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove the spam classifier.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = execute_code_task(run_dir, use_llm=False, timeout_sec=10)

            self.assertEqual(result.stop_reason, "approval_required")
            self.assertTrue((run_dir / "code_task" / "meta" / "environment_report.json").is_file())
            self.assertTrue((run_dir / "code_task" / "run" / "baseline" / "execution_report.json").is_file())
            self.assertFalse((run_dir / "code_task" / "work_plan.json").exists())
            self.assertFalse((run_dir / "code_task" / "attempts" / "attempt-001").exists())
            self.assertTrue((run_dir / "code_task" / "patch_plan.md").is_file())
            self.assertFalse((run_dir / "code_task" / "meta" / "proposed_edits.json").exists())
            self.assertEqual(
                [(step.step, step.status) for step in result.steps],
                [
                    ("probe", "done"),
                    ("baseline", "done"),
                    ("batch", "skipped"),
                    ("plan", "done"),
                ],
            )

    def test_execute_blocks_on_llm_work_plan_failure_without_fallback(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove the spam classifier with LLM planning.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            fake_client = _FailingCodeTaskClient()

            with patch("simple_ar.code_task.editing.work_plan.LLMClient.from_env", return_value=fake_client):
                result = execute_code_task(
                    run_dir,
                    use_llm=True,
                    to_step="work-plan",
                    timeout_sec=10,
                    llm_retry_attempts=2,
                )

            self.assertEqual(result.stop_reason, "llm_planning_failed")
            self.assertEqual(result.steps[-1].step, "work-plan")
            self.assertEqual(result.steps[-1].status, "blocked")
            self.assertIn("LLM work planning failed", result.steps[-1].detail)
            self.assertEqual(fake_client.calls, 2)
            self.assertFalse((run_dir / "code_task" / "work_plan.json").exists())
            self.assertFalse((run_dir / "code_task" / "work_plan.md").exists())

    def test_execute_uses_planning_fallback_only_when_explicitly_allowed(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove the spam classifier with fallback allowed.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            fake_client = _FailingCodeTaskClient()

            with patch("simple_ar.code_task.editing.work_plan.LLMClient.from_env", return_value=fake_client):
                result = execute_code_task(
                    run_dir,
                    use_llm=True,
                    to_step="work-plan",
                    timeout_sec=10,
                    allow_planning_fallback=True,
                    llm_retry_attempts=2,
                )

            self.assertEqual(result.stop_reason, "stop_point")
            self.assertEqual(result.steps[-1].step, "work-plan")
            self.assertEqual(result.steps[-1].status, "done")
            self.assertEqual(fake_client.calls, 2)
            work_plan = read_json(run_dir / "code_task" / "work_plan.json")
            self.assertEqual(work_plan["mode"], "offline")

    def test_execute_blocks_on_llm_patch_plan_failure_without_fallback(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove the spam classifier patch plan.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            first = execute_code_task(run_dir, use_llm=False, to_step="batch", timeout_sec=10)
            self.assertEqual(first.stop_reason, "stop_point")
            fake_client = _FailingCodeTaskClient()

            with patch("simple_ar.code_task.editing.planning.LLMClient.from_env", return_value=fake_client):
                result = execute_code_task(
                    run_dir,
                    use_llm=True,
                    to_step="plan",
                    timeout_sec=10,
                    llm_retry_attempts=2,
                )

            self.assertEqual(result.stop_reason, "llm_planning_failed")
            self.assertEqual(result.steps[-1].step, "plan")
            self.assertEqual(result.steps[-1].status, "blocked")
            self.assertIn("LLM patch planning failed", result.steps[-1].detail)
            self.assertEqual(fake_client.calls, 2)
            self.assertFalse((run_dir / "code_task" / "patch_plan.md").exists())

    def test_execute_can_skip_expensive_baseline(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nSkip unchanged baseline for an expensive task.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = execute_code_task(
                run_dir,
                use_llm=False,
                to_step="baseline",
                baseline_policy="skip",
            )

            self.assertEqual(result.stop_reason, "stop_point")
            self.assertFalse((run_dir / "code_task" / "run" / "baseline" / "execution_report.json").exists())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["benchmark"]["baseline_policy"]["policy"], "skip")
            self.assertEqual(manifest["benchmark"]["baseline_policy"]["status"], "skipped")
            summary = read_text(run_dir / "code_task" / "summary.md")
            self.assertIn("Baseline policy", summary)
            self.assertIn("skip", summary)

    def test_execute_can_record_provided_baseline_metrics(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            metrics_file = root / "baseline_metrics.json"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nUse existing baseline evidence.\n")
            write_json(metrics_file, {"accuracy": 0.75, "loss": 1.25})
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = execute_code_task(
                run_dir,
                use_llm=False,
                to_step="baseline",
                baseline_policy="provided",
                baseline_metrics_file=metrics_file,
            )

            self.assertEqual(result.stop_reason, "stop_point")
            report = read_json(run_dir / "code_task" / "run" / "baseline" / "execution_report.json")
            self.assertTrue(report["provided_baseline"])
            self.assertEqual(report["metric_values"]["accuracy"], 0.75)
            manifest = read_json(run_dir / "manifest.json")
            baseline = manifest["benchmark"]["runs"]["baseline"]
            self.assertTrue(baseline["provided"])
            self.assertEqual(manifest["benchmark"]["baseline_policy"]["policy"], "provided")

    def test_execute_dry_run_has_no_side_effects(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nPreview orchestration.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = execute_code_task(run_dir, dry_run=True, use_llm=False)

            self.assertEqual(result.stop_reason, "dry_run")
            self.assertEqual(result.steps[-1].status, "would_run")
            self.assertEqual(result.steps[-1].step, "probe")
            self.assertFalse((run_dir / "code_task" / "meta" / "environment_report.json").exists())

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["code-task", "execute", str(run_dir), "--dry-run", "--no-llm"])
            output = stdout.getvalue()
            self.assertIn("Stop reason", output)
            self.assertIn("dry_run", output)
            self.assertIn("probe", output)
            self.assertIn("would_run", output)

    def test_execute_cli_reads_runtime_config(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            config_file = root / "execute.toml"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nRun configured execute.\n")
            write_text(
                config_file,
                (
                    "[execute]\n"
                    'to_step = "baseline"\n'
                    "use_llm = false\n"
                    "timeout_sec = 10\n"
                    "max_files = 3\n"
                    "max_source_chars_per_file = 900\n"
                    "\n"
                    "[models.code_task]\n"
                    'planner = "planner-model"\n'
                    'editor = "editor-model"\n'
                    'repair = "repair-model"\n'
                    "\n"
                    "[budget]\n"
                    'profile = "normal"\n'
                    "max_batches = 2\n"
                    "cost_cap_usd = 1.0\n"
                    "\n"
                    "[budget.normal]\n"
                    "max_edits = 3\n"
                    "\n"
                    "[environment]\n"
                    'mode = "current"\n'
                ),
            )
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["code-task", "execute", str(run_dir), "--config", str(config_file)])

            output = stdout.getvalue()
            self.assertIn("Stop reason", output)
            self.assertIn("stop_point", output)
            self.assertIn("baseline", output)
            self.assertIn("done", output)
            self.assertFalse((run_dir / "code_task" / "work_plan.json").exists())

    def test_execute_interactive_skips_completed_steps_without_prompting(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nResume already completed execute steps.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            result = execute_code_task(run_dir, to_step="baseline", use_llm=False, timeout_sec=10)
            self.assertEqual(result.stop_reason, "stop_point")

            stdout = io.StringIO()
            with patch("simple_ar.cli.main.confirm_next_step") as confirm:
                with contextlib.redirect_stdout(stdout):
                    main(["code-task", "execute", str(run_dir), "--to-step", "baseline", "--interactive", "--no-llm"])

            confirm.assert_not_called()
            output = stdout.getvalue()
            self.assertIn("probe", output)
            self.assertIn("baseline", output)
            self.assertIn("skipped", output)

            with patch("simple_ar.cli.main.confirm_next_step", return_value=True):
                with contextlib.redirect_stdout(io.StringIO()):
                    main(["code-task", "execute", str(run_dir), "--to-step", "plan", "--interactive", "--no-llm"])
            self.assertTrue((run_dir / "code_task/patch_plan.md").is_file())
            self.assertFalse((run_dir / "code_task/work_plan.json").exists())

    def test_execute_inline_review_can_approve_plan_and_continue(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nApprove plan inline and continue.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            stdout = io.StringIO()
            with (
                patch("sys.stdin.isatty", return_value=True),
                patch("simple_ar.cli.main.confirm_review_gate", return_value=True) as confirm,
                contextlib.redirect_stdout(stdout),
            ):
                main(
                    [
                        "code-task",
                        "execute",
                        str(run_dir),
                        "--to-step",
                        "propose-edits",
                        "--no-llm",
                    ]
                )

            confirm.assert_called_once()
            output = stdout.getvalue()
            self.assertIn("Patch Plan Review", output)
            self.assertTrue((run_dir / "code_task" / "meta" / "proposed_edits.json").is_file())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["plan"]["status"], "approved")

    def test_execute_applies_reviewed_proposal_after_approval(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nAdd another spam keyword.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            execute_code_task(run_dir, use_llm=False, timeout_sec=10, to_step='batch')
            first = execute_code_task(run_dir, use_llm=False, timeout_sec=10)
            self.assertEqual(first.stop_reason, "approval_required")
            record_plan_decision(run_dir, decision="approve")
            _write_valid_edit_proposal(run_dir)

            result = execute_code_task(
                run_dir,
                use_llm=False,
                timeout_sec=10,
                apply_proposed_edits=True,
            )

            self.assertEqual(result.stop_reason, "completed")
            self.assertTrue((run_dir / "code_task" / "patch.diff").is_file())
            self.assertTrue((run_dir / "code_task" / "run" / "patched" / "execution_report.json").is_file())
            self.assertIn("'prize'", read_text(run_dir / "code_task" / "workspace" / "spam_model.py"))
            step_status = [(step.step, step.status) for step in result.steps]
            self.assertIn(("apply-edits", "done"), step_status)
            self.assertIn(("validate", "done"), step_status)
            self.assertIn(("run", "done"), step_status)
            batch_state = read_json(
                run_dir
                / "code_task"
                / "attempts"
                / "attempt-001"
                / "batches"
                / "batch-001"
                / "batch_state.json"
            )
            self.assertEqual(batch_state["state"], "completed")
            self.assertEqual(batch_state["validation_status"], "passed")
            self.assertEqual(batch_state["benchmark_status"], "passed")
            attempt_state = read_json(
                run_dir / "code_task" / "attempts" / "attempt-001" / "attempt_state.json"
            )
            self.assertEqual(attempt_state["state"], "completed")
            self.assertEqual(attempt_state["batches"][0]["state"], "completed")
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["work_plan"]["status"], "completed")

            # A saved failure must not turn into success merely on resume.
            report_path = run_dir / "code_task" / "meta" / "review_report.json"
            saved_review = read_json(report_path)
            for status, expected in (("failed", "review_failed"), ("warning", "stop_point"), ("passed", "stop_point")):
                with self.subTest(cached_review=status):
                    write_json(report_path, {**saved_review, "status": status})
                    before = report_path.read_bytes()
                    with patch("simple_ar.code_task.orchestration.execute.review_code_task_changes") as reviewer, \
                         patch("simple_ar.code_task.orchestration.execute.run_code_task_benchmark") as benchmark:
                        resumed = execute_code_task(run_dir, use_llm=False, to_step="review")
                    self.assertEqual(resumed.stop_reason, expected)
                    reviewer.assert_not_called()
                    benchmark.assert_not_called()
                    self.assertEqual(report_path.read_bytes(), before)

            post_run = run_dir / "code_task" / "meta" / "review_report_post_run.json"
            write_json(post_run, {**read_json(post_run), "status": "failed"})
            before = post_run.read_bytes()
            with patch("simple_ar.code_task.orchestration.execute.review_code_task_changes") as reviewer:
                resumed = execute_code_task(run_dir, use_llm=False, to_step="run", timeout_sec=10)
            self.assertEqual(resumed.stop_reason, "review_failed")
            reviewer.assert_not_called()
            self.assertEqual(post_run.read_bytes(), before)

    def test_execute_generates_repair_proposal_after_failed_run(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nBreak then repair the spam classifier.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            execute_code_task(run_dir, use_llm=False, timeout_sec=10, to_step='batch')
            first = execute_code_task(run_dir, use_llm=False, timeout_sec=10)
            self.assertEqual(first.stop_reason, "approval_required")
            record_plan_decision(run_dir, decision="approve")
            _write_failing_edit_proposal(run_dir)

            result = execute_code_task(
                run_dir,
                use_llm=False,
                timeout_sec=10,
                apply_proposed_edits=True,
                to_step="repair",
                repair_rounds=1,
            )

            self.assertEqual(result.stop_reason, "repair_review_required")
            self.assertTrue((run_dir / "code_task" / "run" / "patched" / "failure_analysis.md").is_file())
            self.assertTrue((run_dir / "code_task" / "repairs" / "repair-001" / "proposed_edits.json").is_file())
            repair_batch = read_json(
                run_dir
                / "code_task"
                / "attempts"
                / "attempt-001"
                / "batches"
                / "batch-002"
                / "batch_state.json"
            )
            self.assertEqual(repair_batch["kind"], "repair")
            self.assertEqual(repair_batch["parent_batch_id"], "batch-001")
            self.assertIn("repair_proposal", repair_batch["artifacts"])
            attempt_state = read_json(
                run_dir / "code_task" / "attempts" / "attempt-001" / "attempt_state.json"
            )
            self.assertEqual(attempt_state["state"], "failed")
            self.assertEqual(attempt_state["batches"][0]["state"], "failed")
            self.assertEqual(attempt_state["batches"][1]["kind"], "repair")
            step_status = [(step.step, step.status) for step in result.steps]
            self.assertIn(("analyze-failure", "done"), step_status)
            self.assertIn(("repair", "done"), step_status)

    def test_execute_reports_patch_apply_failure_without_traceback(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nHandle an invalid proposal.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            execute_code_task(run_dir, use_llm=False, timeout_sec=10)
            record_plan_decision(run_dir, decision="approve")
            write_json(
                run_dir / "code_task" / "meta" / "proposed_edits.json",
                {
                    "edits": [
                        {
                            "path": "spam_model.py",
                            "old": "text that is not in the file",
                            "new": "replacement",
                            "reason": "invalid proposal",
                        }
                    ]
                },
            )

            result = execute_code_task(
                run_dir,
                use_llm=False,
                timeout_sec=10,
                apply_proposed_edits=True,
            )

            self.assertEqual(result.stop_reason, "patch_apply_failed")
            self.assertEqual(result.steps[-1].step, "apply-edits")
            self.assertEqual(result.steps[-1].status, "blocked")
            self.assertIn("old text was not found", result.steps[-1].detail)

            # File permissions are not an edit-budget approval request, even
            # when the failing file happens to contain 'budget' in its name.
            with patch("simple_ar.code_task.orchestration.execute.apply_patch_edits",
                       side_effect=PermissionError("budget.json: access denied")):
                with self.assertRaisesRegex(PermissionError, "access denied"):
                    execute_code_task(run_dir, use_llm=False, timeout_sec=10, apply_proposed_edits=True)

    def test_analyze_validation_failure_without_benchmark_run(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nRepair a syntax error before running tests.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            write_text(run_dir / "code_task" / "workspace" / "spam_model.py", "def broken(:\n")
            validation = validate_code_task(run_dir)
            self.assertEqual(validation.status, "failed")

            analysis = analyze_code_task_failure(run_dir)

            self.assertEqual(analysis.status, "needs_repair")
            self.assertEqual(analysis.source, "validation")
            self.assertEqual(analysis.analysis_path.name, "failure_analysis.md")
            self.assertIn("spam_model.py", analysis.implicated_files)
            analysis_text = read_text(analysis.analysis_path)
            self.assertIn("Static validation failed", analysis_text)

            repair = propose_repair_edits(run_dir, use_llm=False)
            self.assertEqual(repair.mode, "offline")
            proposal = read_json(repair.proposal_path)
            self.assertEqual(proposal["source_analysis"], "code_task/meta/failure_analysis.md")
            self.assertIn("spam_model.py", proposal["selected_files"])

    def test_repair_proposal_drops_edits_outside_selected_context(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(code_root / "extra.py", "VALUE = 1\n")
            write_text(task_file, "# Task\n\nRepair the broken spam classifier.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            apply_patch_edits(run_dir, edits_file=_write_failing_edit_proposal(run_dir))
            failed = run_code_task_benchmark(run_dir, timeout_sec=10)
            self.assertEqual(failed.status, "failed")
            analyze_code_task_failure(run_dir)

            fake_client = _FakeRepairClient(
                {
                    "summary": "Attempt to repair an unrelated file.",
                    "edits": [
                        {
                            "path": "extra.py",
                            "old": "VALUE = 1\n",
                            "new": "VALUE = 2\n",
                            "reason": "This is outside the selected repair context.",
                        }
                    ],
                    "validation": ["Would need tests."],
                    "risks": ["Unrelated edit."],
                }
            )

            with patch("simple_ar.code_task.execution.repair.LLMClient.from_env", return_value=fake_client):
                repair = propose_repair_edits(run_dir, use_llm=True, max_files=1)

            proposal = read_json(repair.proposal_path)
            self.assertEqual(proposal["mode"], "llm")
            self.assertEqual(proposal["edits"], [])
            self.assertIn("spam_model.py", proposal["selected_files"])
            self.assertIn("Dropped edit outside repair context: extra.py", proposal["warnings"])

    def test_repair_proposal_drops_diff_marker_edits(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nRepair the broken spam classifier.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            apply_patch_edits(run_dir, edits_file=_write_failing_edit_proposal(run_dir))
            failed = run_code_task_benchmark(run_dir, timeout_sec=10)
            self.assertEqual(failed.status, "failed")
            analyze_code_task_failure(run_dir)

            fake_client = _FakeRepairClient(
                {
                    "summary": "Accidentally return a diff hunk.",
                    "edits": [
                        {
                            "path": "spam_model.py",
                            "old": (
                                "-def predict(text):\n"
                                "-    return 'ham'\n"
                                "+def predict(text):\n"
                                "+    return 'spam'\n"
                            ),
                            "new": (
                                "def predict(text):\n"
                                "    return 'spam'\n"
                            ),
                            "reason": "The model should not put diff markers in old.",
                        }
                    ],
                    "validation": ["python -m unittest discover -s tests"],
                    "risks": [],
                }
            )

            with patch("simple_ar.code_task.execution.repair.LLMClient.from_env", return_value=fake_client):
                repair = propose_repair_edits(run_dir, use_llm=True)

            proposal = read_json(repair.proposal_path)
            self.assertEqual(proposal["edits"], [])
            self.assertIn(
                "Dropped edit for spam_model.py: old/new must be exact text, not a diff fragment.",
                proposal["warnings"],
            )

    def test_code_task_validate_run_and_failure_cli(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            output_root = root / "runs"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nRun CLI validation and tests.\n")
            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--output-root",
                        str(output_root),
                        "--benchmark-command",
                        "python -m unittest discover -s tests",
                    ]
                )
            run_dir = next(output_root.iterdir())

            validate_stdout = io.StringIO()
            with contextlib.redirect_stdout(validate_stdout):
                main(["code-task", "validate", str(run_dir)])
            self.assertIn("Status: passed", validate_stdout.getvalue())

            run_stdout = io.StringIO()
            with contextlib.redirect_stdout(run_stdout):
                main(["code-task", "run", str(run_dir), "--timeout", "10"])
            self.assertIn("Status: passed", run_stdout.getvalue())
            self.assertTrue((run_dir / "code_task" / "run" / "patched" / "execution_report.json").is_file())

            status_stdout = io.StringIO()
            with contextlib.redirect_stdout(status_stdout):
                main(["status", str(run_dir)])
            self.assertIn("Validation:", status_stdout.getvalue())
            self.assertIn("last status: passed", status_stdout.getvalue())


def _write_toy_project(code_root: Path) -> None:
    write_text(
        code_root / "spam_model.py",
        (
            "import math\n\n\n"
            "class SpamModel:\n"
            "    def score(self, text):\n"
            "        return math.log(len(text) + 1)\n\n\n"
            "def predict(text):\n"
            "    return 'spam' if 'win' in text.lower() else 'ham'\n\n\n"
            "if __name__ == \"__main__\":\n"
            "    print(predict('win a prize'))\n"
        ),
    )
    write_text(
        code_root / "tests" / "test_spam_model.py",
        (
            "import unittest\n\n"
            "from spam_model import predict\n\n\n"
            "class SpamModelTests(unittest.TestCase):\n"
            "    def test_predicts_spam_keyword(self):\n"
            "        self.assertEqual(predict('win now'), 'spam')\n"
        ),
    )
    write_text(code_root / ".git" / "config", "[core]\nrepositoryformatversion = 0\n")
    write_text(code_root / ".env", "TOKEN=secret\n")
    write_text(
        code_root / "pyproject.toml",
        "[project]\nname = \"toy-project\"\nversion = \"0.1.0\"\n",
    )


def _write_metric_project(
    code_root: Path,
    *,
    value: str,
    metric_name: str = "accuracy",
) -> None:
    write_text(code_root / "metric_value.txt", value + "\n")
    write_text(
        code_root / "benchmark.py",
        (
            "from pathlib import Path\n\n"
            "value = float(Path('metric_value.txt').read_text().strip())\n"
            f"print(f'{metric_name}: {{value:.6f}}')\n"
            "print('train_time_sec: 0.010000')\n"
        ),
    )


def _write_analysis_first_work_plan(run_dir: Path) -> None:
    work_plan = {
        "schema_version": 1,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "mode": "test",
        "summary": "Analysis item appears before the actual implementation item.",
        "goal": "Improve spam prediction.",
        "success_criteria": ["Tests pass after a useful code change."],
        "items": [
            {
                "id": "W1",
                "status": "pending",
                "objective": "Inspect current behavior and identify candidate improvements.",
                "target_files": ["spam_model.py"],
                "read_only_evidence": ["tests/test_spam_model.py"],
                "depends_on": [],
                "validation": ["python -m unittest discover -s tests"],
                "done_criteria": ["Behavior is understood."],
                "risk": "Low",
                "parallelizable": False,
                "budget_profile": "normal",
                "requires_budget_override": False,
                "suggested_budget_override": "",
                "context_request": {"query": "inspect", "files": ["spam_model.py"], "symbols": []},
            },
            {
                "id": "W2",
                "status": "pending",
                "objective": "Implement keyword handling improvement in the classifier.",
                "target_files": ["spam_model.py"],
                "read_only_evidence": ["tests/test_spam_model.py"],
                "depends_on": ["W1"],
                "validation": ["python -m unittest discover -s tests"],
                "done_criteria": ["The classifier recognizes the requested keyword."],
                "risk": "Low",
                "parallelizable": False,
                "budget_profile": "normal",
                "requires_budget_override": False,
                "suggested_budget_override": "",
                "context_request": {"query": "implement", "files": ["spam_model.py"], "symbols": []},
            },
        ],
        "context_requests": [],
        "risks": [],
        "approval": {"required": True, "status": "pending", "reason": "Review before editing."},
        "selected_files": ["spam_model.py", "tests/test_spam_model.py"],
        "context_pack": None,
        "run_context": {},
        "budget_profiles": {},
    }
    write_json(run_dir / "code_task" / "work_plan.json", work_plan)
    write_text(run_dir / "code_task" / "work_plan.md", "# Work Plan\n")
    manifest = read_json(run_dir / "manifest.json")
    manifest["layout"]["work_plan"] = "code_task/work_plan.json"
    manifest["layout"]["work_plan_markdown"] = "code_task/work_plan.md"
    manifest["work_plan"] = {
        "status": "pending_approval",
        "mode": "test",
        "path": "code_task/work_plan.json",
        "markdown": "code_task/work_plan.md",
        "item_count": 2,
        "selected_files": ["spam_model.py", "tests/test_spam_model.py"],
        "context_pack": None,
        "approval": work_plan["approval"],
    }
    write_json(run_dir / "manifest.json", manifest)


def _write_dependent_work_plan(run_dir: Path) -> None:
    work_plan = {
        "schema_version": 1,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "mode": "test",
        "summary": "Three tightly coupled implementation items.",
        "goal": "Implement a feature, scorer, and config change together.",
        "success_criteria": ["The coupled implementation passes benchmark validation."],
        "items": [
            {
                "id": "W1",
                "status": "pending",
                "objective": "Add the feature producer.",
                "target_files": ["spam_model.py"],
                "read_only_evidence": ["tests/test_spam_model.py"],
                "depends_on": [],
                "validation": ["python -m unittest discover -s tests"],
                "done_criteria": ["The producer emits the new feature."],
                "risk": "Producer must remain backward compatible.",
                "parallelizable": False,
                "budget_profile": "normal",
                "requires_budget_override": False,
                "suggested_budget_override": "",
                "context_request": {"query": "producer", "files": ["spam_model.py"], "symbols": []},
            },
            {
                "id": "W2",
                "status": "pending",
                "objective": "Use the feature in scoring.",
                "target_files": ["extra.py"],
                "read_only_evidence": ["tests/test_spam_model.py"],
                "depends_on": ["W1"],
                "validation": ["python -m unittest discover -s tests"],
                "done_criteria": ["The scorer consumes the new feature."],
                "risk": "Scoring can drift if the feature is absent.",
                "parallelizable": False,
                "budget_profile": "normal",
                "requires_budget_override": False,
                "suggested_budget_override": "",
                "context_request": {"query": "scorer", "files": ["extra.py"], "symbols": []},
            },
            {
                "id": "W3",
                "status": "pending",
                "objective": "Enable the new behavior in configuration.",
                "target_files": ["pyproject.toml"],
                "read_only_evidence": ["tests/test_spam_model.py"],
                "depends_on": ["W2"],
                "validation": ["python -m unittest discover -s tests"],
                "done_criteria": ["The default config enables the feature."],
                "risk": "Config changes should be additive.",
                "parallelizable": False,
                "budget_profile": "normal",
                "requires_budget_override": False,
                "suggested_budget_override": "",
                "context_request": {"query": "config", "files": ["pyproject.toml"], "symbols": []},
            },
        ],
        "context_requests": [],
        "risks": [],
        "approval": {"required": True, "status": "pending", "reason": "Review before editing."},
        "selected_files": ["spam_model.py", "extra.py", "pyproject.toml", "tests/test_spam_model.py"],
        "context_pack": None,
        "run_context": {},
        "budget_profiles": {},
    }
    write_json(run_dir / "code_task" / "work_plan.json", work_plan)
    write_text(run_dir / "code_task" / "work_plan.md", "# Work Plan\n")
    manifest = read_json(run_dir / "manifest.json")
    manifest["layout"]["work_plan"] = "code_task/work_plan.json"
    manifest["layout"]["work_plan_markdown"] = "code_task/work_plan.md"
    manifest["work_plan"] = {
        "status": "pending_approval",
        "mode": "test",
        "path": "code_task/work_plan.json",
        "markdown": "code_task/work_plan.md",
        "item_count": 3,
        "selected_files": work_plan["selected_files"],
        "context_pack": None,
        "approval": work_plan["approval"],
    }
    write_json(run_dir / "manifest.json", manifest)


def _write_valid_edit_proposal(run_dir: Path, path: Path | None = None) -> Path:
    proposal_path = path or run_dir / "code_task" / "meta" / "proposed_edits.json"
    write_json(
        proposal_path,
        {
            "schema_version": 1,
            "edits": [
                {
                    "path": "spam_model.py",
                    "old": (
                        "def predict(text):\n"
                        "    return 'spam' if 'win' in text.lower() else 'ham'\n"
                    ),
                    "new": (
                        "def predict(text):\n"
                        "    lowered = text.lower()\n"
                        "    return 'spam' if any(keyword in lowered for keyword in ('win', 'prize')) else 'ham'\n"
                    ),
                    "reason": "Extend the keyword baseline while preserving the public API.",
                }
            ],
        },
    )
    return proposal_path


def _write_failing_edit_proposal(run_dir: Path) -> Path:
    proposal_path = run_dir / "code_task" / "meta" / "proposed_edits.json"
    write_json(
        proposal_path,
        {
            "schema_version": 1,
            "edits": [
                {
                    "path": "spam_model.py",
                    "old": (
                        "def predict(text):\n"
                        "    return 'spam' if 'win' in text.lower() else 'ham'\n"
                    ),
                    "new": (
                        "def predict(text):\n"
                        "    return 'ham'\n"
                    ),
                    "reason": "Deliberately break the classifier for failure-analysis coverage.",
                }
            ],
        },
    )
    return proposal_path


def _git(cwd: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed: {completed.stderr or completed.stdout}"
        )


class _FakeRepairClient:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response

    def ask_json(self, system: str, user: str, *, label: str = "") -> dict[str, object]:
        return self._response


class _FakeCodeTaskClient:
    def ask_json(self, system: str, user: str, *, label: str = "") -> dict[str, object]:
        if label.startswith("code-task-review-"):
            return {"findings": []}
        if label == "code-task-work-plan":
            return {
                "summary": "Improve prize-message classification in one small batch.",
                "goal": "Classify prize messages as spam without changing tests.",
                "success_criteria": ["Benchmark passes after the patch."],
                "items": [
                    {
                        "id": "W1",
                        "objective": "Implement a small keyword handling improvement.",
                        "target_files": ["spam_model.py"],
                        "read_only_evidence": ["tests/test_spam_model.py"],
                        "depends_on": [],
                        "validation": ["python -m unittest discover -s tests"],
                        "done_criteria": ["Prediction handles prize messages."],
                        "risk": "Low.",
                        "parallelizable": False,
                        "budget_profile": "normal",
                        "requires_budget_override": False,
                        "suggested_budget_override": "",
                        "context_request": {"query": "predict prize", "files": ["spam_model.py"], "symbols": ["predict"]},
                    }
                ],
                "context_requests": [],
                "risks": [],
                "approval": {"required": True, "reason": "Review before editing."},
            }
        if label == "code-task-plan":
            return {
                "summary": "Patch the classifier keyword logic.",
                "goals": ["Handle prize messages as spam."],
                "files_to_modify": [
                    {
                        "path": "spam_model.py",
                        "reason": "Contains predict keyword logic.",
                        "change_type": "modify",
                    }
                ],
                "new_files": [],
                "proposed_steps": ["Update predict keyword check."],
                "validation": ["python -m unittest discover -s tests"],
                "risks": ["Keep API stable."],
                "rollback": ["Discard workspace changes."],
                "open_questions": [],
                "requires_approval_before_patch": True,
            }
        if label == "code-task-propose-edits":
            return {
                "summary": "Add prize keyword support.",
                "edits": [
                    {
                        "path": "spam_model.py",
                        "old": (
                            "def predict(text):\n"
                            "    return 'spam' if 'win' in text.lower() else 'ham'\n"
                        ),
                        "new": (
                            "def predict(text):\n"
                            "    lowered = text.lower()\n"
                            "    return 'spam' if any(keyword in lowered for keyword in ('win', 'prize')) else 'ham'\n"
                        ),
                        "reason": "Classify prize messages as spam.",
                    }
                ],
                "validation": ["python -m unittest discover -s tests"],
                "risks": [],
            }
        raise AssertionError(f"Unexpected LLM label: {label}")


class _FailingCodeTaskClient:
    def __init__(self) -> None:
        self.calls = 0

    def ask_json(self, system: str, user: str, *, label: str = "") -> dict[str, object]:
        self.calls += 1
        raise LLMError("LLM response did not contain a JSON object")


def _indexed_file(index: dict[str, object], path: str) -> dict[str, object]:
    files = index.get("files", [])
    if isinstance(files, list):
        for item in files:
            if isinstance(item, dict) and item.get("path") == path:
                return item
    raise AssertionError(f"Missing indexed file: {path}")


if __name__ == "__main__":
    unittest.main()
