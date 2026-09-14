from __future__ import annotations

from dataclasses import replace
import tempfile
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

from simple_ar.app.research_application import (
    ResearchApplicationServices,
    ResearchApplicationError,
    create_session,
    load_session,
)
from simple_ar.core.budget import BudgetLedger
from simple_ar.integrations.llm import LLMClient, LLMSettings
from simple_ar.research.planning.capability import ResearchPlanRequest
from simple_ar.research.workflow_contracts import ResearchBrief


class ResearchApplicationTests(unittest.TestCase):
    def test_missing_selected_design_stops_code_preparation_before_processes(self):
        from simple_ar.research.design import ResearchDesignResult
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "paper.md"
            paper.write_text("# Replay\nA small memory retains prior examples.", encoding="utf-8")
            app = create_session(ResearchBrief(request_text="Study replay", requested_outputs=("experiments",),
                asset_requests=({"locator": str(paper), "kind": "file", "role": "paper"},)), root=root / "session",
                services=ResearchApplicationServices(max_attempts=20, config={"execution": {
                    "command": [sys.executable, "-c", "print('accuracy: 1')"], "cwd": str(root), "timeout_sec": 5,
                    "code_task": {"code_root": str(root), "approval_note": "Only isolated edits."},
                }}, budget_limits={"process_invocations": 2, "process_wall_seconds": 10}))
            with patch("simple_ar.research.design.build_research_design", return_value=ResearchDesignResult(
                status="needs_review", contract=None, diagnostics=("No candidate selected.",),
            )):
                view = app.advance(max_actions=20)
            self.assertEqual(view.status, "paused")
            self.assertIn("selected research design contract", view.status_reason)
            self.assertNotIn("preparation", view.state_refs)
            self.assertFalse(any("process_invocations" in e.reserved for e in app.budget_ledger.entries))

    def test_terminal_attempt_reservation_becomes_unknown_without_refunding_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_session(ResearchBrief(request_text="Study agents"), root=tmp,
                                 services=ResearchApplicationServices(budget_limits={"total_tokens": 100}))
            app.advance()
            attempt_id = app.controller.list_attempts()[0].attempt_id
            app.budget_ledger.reserve("interrupted-call", {"total_tokens": 60}, attempt_id=attempt_id)
            app.controller.pause("Inspect recovery without starting another action.")
            resumed = load_session(tmp)
            resumed.advance()
            self.assertEqual(resumed.budget_ledger.entries[-1].status, "unknown")
            self.assertEqual(resumed.budget_ledger.entries[-1].actual, {})
            self.assertEqual(resumed.budget_ledger.remaining("total_tokens"), 40)
            saved = (Path(tmp) / "budget_ledger.json").read_bytes()
            resumed.advance()
            self.assertEqual((Path(tmp) / "budget_ledger.json").read_bytes(), saved)

    def test_real_code_task_modification_is_measured_by_application_once(self):
        """Keep the old real bridge check, but exercise the formal lifecycle."""
        class FakeClient:
            model = "fake-research-and-code-model"

            def ask_json(
                self,
                _system: str,
                _user: str,
                *,
                label: str = "",
                **kwargs: object,
            ) -> dict[str, object]:
                del kwargs
                if label.startswith("code-task-review-"):
                    return {"findings": []}
                if label == "code-task-work-plan":
                    return {
                        "summary": "Improve prize-message classification in one small batch.",
                        "goal": "Classify prize messages as spam without changing tests.",
                        "success_criteria": ["The benchmark passes after the patch."],
                        "items": [
                            {
                                "id": "W1",
                                "objective": "Add the missing prize keyword handling.",
                                "target_files": ["spam_model.py"],
                                "read_only_evidence": ["tests/test_spam_model.py"],
                                "depends_on": [],
                                "validation": ["python -m unittest discover -s tests"],
                                "done_criteria": ["Prize messages are classified as spam."],
                                "risk": "Low.",
                                "parallelizable": False,
                                "budget_profile": "normal",
                                "requires_budget_override": False,
                                "suggested_budget_override": "",
                                "context_request": {
                                    "query": "predict prize",
                                    "files": ["spam_model.py"],
                                    "symbols": ["predict"],
                                },
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
                        "proposed_steps": ["Update the predict keyword check."],
                        "validation": ["python -m unittest discover -s tests"],
                        "risks": ["Keep the public API stable."],
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
                raise AssertionError(f"Unexpected JSON LLM label: {label}")


        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "reliable_agents.md"
            paper.write_text(
                "# Method\n\nValidation improves reliable agent behavior.\n\n"
                "# Results\n\nThe fixture reports accuracy: 0.75.\n",
                encoding="utf-8",
            )
            project = root / "project"
            project.mkdir()
            (project / "spam_model.py").write_text(
                "def predict(text):\n"
                "    return 'spam' if 'win' in text.lower() else 'ham'\n",
                encoding="utf-8",
            )
            (project / "benchmark.py").write_text(
                "from spam_model import predict\n\n"
                "rows = [('win now', 'spam'), ('prize only', 'spam')]\n"
                "correct = sum(predict(text) == label for text, label in rows)\n"
                "print(f'accuracy: {correct / len(rows):.6f}')\n",
                encoding="utf-8",
            )
            (project / "tests").mkdir()
            (project / "tests" / "test_spam_model.py").write_text(
                "import unittest\n\n"
                "from spam_model import predict\n\n\n"
                "class SpamModelTests(unittest.TestCase):\n"
                "    def test_predicts_win_as_spam(self):\n"
                "        self.assertEqual(predict('win now'), 'spam')\n",
                encoding="utf-8",
            )
            execution = {
                "command": [sys.executable, "benchmark.py"],
                "baseline": {"command": [sys.executable, "benchmark.py"]},
                "cwd": str(project), "timeout_sec": 20,
                "result_schema": {"primary_metric": "accuracy", "metric_directions": {"accuracy": "higher"}},
                "code_task": {"code_root": str(project), "allowed_patterns": ["spam_model.py"],
                              "approval_note": "Approve only the isolated spam_model.py keyword edit."},
            }
            app = create_session(ResearchBrief(
                request_text="Study reliable agents; add prize keyword support without changing tests.",
                requested_outputs=("experiments",),
                asset_requests=({"locator": str(paper), "role": "paper"},),
            ), root=root / "session", services=ResearchApplicationServices(
                max_results=1, config={"research_queries": ["reliable agents"]},
                budget_limits={"process_invocations": 2, "process_wall_seconds": 40},
            ))
            paused = app.advance(max_actions=10)
            self.assertEqual(paused.status, "paused")
            app.supply_execution(execution, task_text="Keep the existing prediction API unchanged.")
            app = load_session(root / "session")
            self.assertIn("Keep the existing prediction API", app.brief.request_text)
            self.assertEqual(app.view().state_refs["design"], paused.state_refs["design"])
            app.advance(max_actions=2)
            self.assertEqual(app.view().next_action, "implement", app.view().status_reason)
            client = FakeClient()
            app.services = replace(app.services, llm_client=client)
            with patch.object(LLMClient, "for_task", return_value=client):
                implemented = app.advance()
            self.assertEqual(implemented.next_action, "experiment", implemented.status_reason)
            app.services = replace(app.services, llm_client=None)
            final = app.advance(max_actions=5)
            self.assertEqual(final.status, "completed", final.status_reason)
            baseline = app.controller.store.read_json(final.state_refs["baseline"])
            candidate = app.controller.store.read_json(final.state_refs["experiment"])
            self.assertEqual(baseline["metrics"]["accuracy"], 0.5)
            self.assertEqual(candidate["metrics"]["accuracy"], 1.0)
            self.assertEqual(app.budget_ledger.remaining("process_invocations"), 0)
            self.assertNotIn("'prize'", (project / "spam_model.py").read_text())
            context, _ = app.report_inputs()
            self.assertIn("implementation", context.results)
            self.assertIn("prize", str(context.results["implementation"]))
            before = final.state_refs
            reloaded = load_session(root / "session")
            restored = reloaded.advance(max_actions=5)
            self.assertEqual(restored.state_refs, before)
            self.assertEqual(reloaded.budget_ledger.remaining("process_invocations"), 0)

    def test_explicit_pairs_recover_each_measurement_without_repeating_baselines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "paper.md"
            paper.write_text("# Classification\nCompare measured classifier outcomes.\n", encoding="utf-8")
            script = root / "evaluate.py"
            script.write_text(
                "import sys\nfrom pathlib import Path\n"
                "with Path('runs.txt').open('a') as f: f.write(sys.argv[1]+'\\n')\n"
                "labels=[0,1,1,0]\n"
                "predictions=[0,1,0,0] if sys.argv[1].startswith('baseline') else [0,0,0,0]\n"
                "print('accuracy:', sum(a==b for a,b in zip(labels,predictions))/len(labels))\n",
                encoding="utf-8",
            )
            pairs = [{"seed": seed,
                "baseline_command": [sys.executable, str(script), f"baseline-{seed}"],
                "candidate_command": [sys.executable, str(script), f"candidate-{seed}"]} for seed in (0, 1)]
            app = create_session(ResearchBrief(request_text="Compare classifiers.", requested_outputs=("experiments", "report"),
                asset_requests=({"locator": str(paper), "role": "paper"},)), root=root / "session",
                services=ResearchApplicationServices(max_results=1, max_attempts=24, config={"execution": {
                    "pairs": pairs, "cwd": str(root), "timeout_sec": 5,
                    "result_schema": {"primary_metric": "accuracy", "direction": "higher"},
                    "protocol": {"contract_id": "fixture-v1", "hypothesis": "Compare the fixture conditions.",
                                 "dataset_refs": [{"asset_id": "fixture"}], "split_spec": {"name": "test"},
                                 "metric_specs": [{"name": "accuracy", "unit": "fraction"}],
                                 "comparison_conditions": {"batch_size": 1}}},
                    "report": {"figures": {"enabled": True, "max_figures": 1}}},
                    budget_limits={"process_invocations": 4, "process_wall_seconds": 20}))
            plan = app.controller.store.read_json(app.view().state_refs["work_plan"])
            self.assertEqual(plan["requested_outputs"][0]["status"], "pending")
            app.advance(max_actions=8)
            self.assertEqual(app.view().next_action, "matrix_baseline_0")
            with patch.object(app, "_persist_application_views", side_effect=RuntimeError("after measurement")):
                with self.assertRaisesRegex(RuntimeError, "after measurement"):
                    app.advance()
            app = load_session(root / "session")
            app.advance()
            plan = app.controller.store.read_json(app.view().state_refs["work_plan"])
            self.assertEqual(plan["requested_outputs"][0]["status"], "partial")
            final = app.advance(max_actions=3)
            self.assertEqual(final.next_action, "report_write", final.status_reason)
            self.assertNotIn("report", final.state_refs)
            collection = app.controller.store.read_json(final.state_refs["matrix_results"])
            self.assertEqual([row["seed"] for row in collection["pairs"]], [0, 1])
            self.assertTrue(all(row["baseline"] and row["candidate"] for row in collection["pairs"]))
            for i in range(2):
                for role in ("baseline", "candidate"):
                    result = app.controller.store.read_json(final.state_refs[f"matrix_{role}_{i}"])
                    self.assertEqual(result["status"], "passed")
            self.assertEqual((root / "runs.txt").read_text().splitlines(),
                             ["baseline-0", "baseline-1", "candidate-0", "candidate-1"])
            self.assertEqual(len(app.budget_ledger.entries), 4)
            analysis = app.controller.store.read_json(final.state_refs["analysis"])
            self.assertEqual(analysis["execution_ref"], final.state_refs["matrix_results"].to_dict())
            self.assertTrue(analysis["analysis"]["claims"])
            self.assertEqual(
                analysis["analysis"]["claims"][0]["claim"],
                "Compare measured classifier outcomes.",
            )
            self.assertEqual(
                analysis["analysis"]["claims"][0]["verdict"],
                "not_evaluated",
            )
            paired = app.controller.store.read_json(
                app.controller.store.ref(Path(final.state_refs["analysis"].path).parent / "paired_analysis.json")
            )
            self.assertEqual(paired["planned_pairs"], 2)
            self.assertEqual(len(paired["paired_summary"]), 1)
            self.assertIn("Planned pairs: 2", analysis["analysis"]["readme_markdown"])
            context, memory = app.report_inputs()
            self.assertEqual(len(context.results.get("comparisons", [])), 2, context.results)
            self.assertTrue(context.experiment_plan.get("hypothesis"))
            self.assertEqual(len(context.experiment_plan["paired_protocols"]), 4)
            measured_sources = [m for m in context.metric_sources if m.source_kind == "measured"]
            self.assertEqual({m.label for m in measured_sources},
                             {f"{role}:seed={seed}" for role in ("baseline", "candidate") for seed in (0, 1)})
            self.assertEqual(len([m for m in context.metric_sources if m.source_kind == "measured"]), 4)
            self.assertEqual(len([m for m in context.metric_sources if m.source_kind == "derived_comparison"]), 2)
            self.assertEqual(len([m for m in context.metric_sources if m.source_kind == "derived_summary"]), 5)
            self.assertEqual(len({m.artifact for m in measured_sources}), 4)
            derived = [m for m in context.metric_sources if m.source_kind == "derived_comparison"]
            self.assertEqual([m.value for m in derived], [-0.25, -0.25])
            from dataclasses import replace
            from simple_ar.report.schema import AgentReportResult, ReportSectionDraft
            app.services = replace(app.services, llm_client=LLMClient(LLMSettings(api_key="fixture-key", api_mode="chat")))
            def writer(**kwargs):
                paper_id = kwargs["context"].papers[0]["id"]
                return AgentReportResult(report_body="", memory=kwargs["memory"], used_agent=True,
                    sections=[ReportSectionDraft(section_id=str(i), heading=heading,
                        draft_markdown=f"Fixture paired evaluation; no significance claim [@{paper_id}].",
                        used_sources=[paper_id]) for i, heading in enumerate(("Abstract", "Introduction", "Related Work",
                            "Method", "Experimental Setup", "Results", "Limitations", "Conclusion"))])
            with patch("simple_ar.report.writing.run_report_agent", side_effect=writer):
                final = app.advance(max_actions=3)
            self.assertEqual(final.status, "completed", final.status_reason)
            report = app.controller.store.read_text(final.state_refs["report"])
            for metric in context.metric_sources:
                self.assertIn(metric.label, report)
            audit = app.controller.store.read_json(final.state_refs["report_audit"])
            self.assertEqual(
                set(audit["metric_audit"]["matched_metrics"]),
                {
                    m.metric_id
                    for m in context.metric_sources
                    if m.source_kind == "derived_summary"
                },
            )
            report_dir = Path(final.state_refs["report"].path).parent
            report_text = app.controller.store.read_text(final.state_refs["report"])
            self.assertIn("Measured comparisons", report_text, msg=report_text)
            self.assertTrue((app.controller.store.resolve(report_dir / "figures" / "paired-1.svg")).is_file())
            self.assertIn("paired-1.svg", report_text)
            self.assertEqual(len(app.budget_ledger.entries), 4)

    def test_literature_report_does_not_require_or_launch_experiments(self):
        from dataclasses import replace
        from simple_ar.report.schema import AgentReportResult, ReportSectionDraft

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "paper.md"
            paper.write_text("# Calibration\n\nCalibration compares confidence with accuracy. Evidence remains limited.", encoding="utf-8")
            app = create_session(ResearchBrief(
                request_text="Review calibration evidence.", requested_outputs=("report",),
                asset_requests=({"locator": str(paper), "role": "paper"},),
            ), root=root / "session", services=ResearchApplicationServices(max_results=1,
                budget_limits={"process_invocations": 0}))
            view = app.advance(max_actions=6)
            self.assertEqual(view.next_action, "report_write")
            app.services = replace(app.services, llm_client=LLMClient(LLMSettings(api_key="fixture")))

            def writer(**kwargs):
                context = kwargs["context"]
                self.assertEqual(context.report_mode, "research_only")
                self.assertEqual(context.results, {})
                self.assertEqual(context.metric_sources, [])
                self.assertEqual(kwargs["template"].name, "survey")
                self.assertIn("No experiment", context.evidence_summary)
                citation = context.papers[0]["id"]
                return AgentReportResult(report_body="", memory=kwargs["memory"], used_agent=True,
                    sections=[ReportSectionDraft(section_id="review", heading="Evidence and Limitations",
                        draft_markdown=f"Prior work discusses calibration [@{citation}]. No experiment was conducted.",
                        used_sources=[f"paper:{citation}"])])

            with patch("simple_ar.report.writing.run_report_agent", side_effect=writer):
                view = app.advance(max_actions=3)
            self.assertEqual(view.status, "completed", view.status_reason)
            self.assertIn("report_audit", view.state_refs)
            self.assertNotIn("experiment", view.state_refs)
            self.assertNotIn("design", view.state_refs)
            self.assertEqual(app.budget_ledger.remaining("process_invocations"), 0)
            self.assertFalse(any("process_invocations" in entry.actual for entry in app.budget_ledger.entries))
            report = app.controller.store.read_text(view.state_refs["report"])
            self.assertIn("No experiment was conducted", report)
            self.assertIn("References", report)

    def test_report_audit_warning_is_deliverable_but_failed_audit_is_not(self):
        from dataclasses import replace
        from simple_ar.report.schema import AgentReportResult, ReportSectionDraft, ReviewerFinding

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "paper.md"
            paper.write_text("# Calibration\n\nCalibration evidence is limited.\n", encoding="utf-8")
            app = create_session(ResearchBrief(
                request_text="Review calibration evidence.", requested_outputs=("report",),
                asset_requests=({"locator": str(paper), "role": "paper"},),
            ), root=root / "session", services=ResearchApplicationServices(max_results=1))
            app.advance(max_actions=6)
            app.services = replace(app.services, llm_client=LLMClient(LLMSettings(api_key="fixture")))

            def writer(**kwargs):
                paper_id = kwargs["context"].papers[0]["id"]
                memory = kwargs["memory"].model_copy(update={"reviewer_findings": [ReviewerFinding(
                    finding_id="warning-1", type="style", severity="major", message="Needs a bounded qualification.") ]})
                return AgentReportResult(report_body="", memory=memory, used_agent=True, sections=[
                    ReportSectionDraft(section_id="review", heading="Evidence",
                                       draft_markdown=f"Calibration evidence is limited [@{paper_id}].",
                                       used_sources=[paper_id])])

            with patch("simple_ar.report.writing.run_report_agent", side_effect=writer):
                view = app.advance(max_actions=3)
            audit = app.controller.store.read_json(view.state_refs["report_audit"])
            self.assertEqual(audit["status"], "warning")

        self.assertEqual(view.status, "completed", view.status_reason)

    def test_request_report_reopens_completed_prefix_without_rerunning_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "paper.md"
            paper.write_text("# Classification\n\nThe fixture describes an accuracy metric.\n", encoding="utf-8")
            script = root / "evaluate.py"
            script.write_text("print('accuracy: 0.75')\n", encoding="utf-8")
            app = create_session(
                ResearchBrief(
                    request_text="Evaluate the fixture.",
                    requested_outputs=("experiments",),
                    asset_requests=({"locator": str(paper), "role": "paper"},),
                ),
                root=root / "session",
                services=ResearchApplicationServices(
                    max_results=1,
                    config={
                        "execution": {
                            "command": [sys.executable, str(script)],
                            "cwd": str(root),
                            "timeout_sec": 5,
                            "result_schema": {"primary_metric": "accuracy"},
                        }
                    },
                    budget_limits={"process_invocations": 1, "process_wall_seconds": 10},
                ),
            )
            completed = app.advance(max_actions=20)
            self.assertEqual(completed.status, "completed", completed.status_reason)
            analysis_ref = completed.state_refs["analysis"]
            attempt_count = len(completed.attempts)

            reopened = app.request_report()

            self.assertEqual(reopened.status, "running")
            self.assertEqual(reopened.next_action, "report_write")
            self.assertEqual(reopened.state_refs["analysis"], analysis_ref)
            self.assertEqual(len(reopened.attempts), attempt_count)
            self.assertEqual(app.controller.manifest.revision, 1)
            self.assertEqual(app.brief.revision, 2)
            self.assertIn("report", app.brief.requested_outputs)

    def test_csv_baseline_preparation_trains_and_scores_real_labels(self):
        for swapped in (False, True):
            with self.subTest(swapped=swapped), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                paper = root / "paper.md"
                paper.write_text("# Text classification baseline\n\nText classification baseline uses word counts and logistic regression.\n", encoding="utf-8")
                data = root / "reviews.csv"
                labels = ("negative", "positive") if swapped else ("positive", "negative")
                data.write_text("text,label,split\nbright sunny good,positive,train\nbright good,positive,train\n"
                                "dark rainy bad,negative,train\ndark bad,negative,train\n"
                                f"bright sunny good,{labels[0]},eval\ndark rainy bad,{labels[1]},eval\n", encoding="utf-8")
                app = create_session(ResearchBrief(
                    request_text="Text classification baseline", requested_outputs=("experiments", "report") if not swapped else ("experiments",),
                    asset_requests=({"locator": str(paper), "role": "paper"}, {"locator": str(data), "role": "dataset"}),
                ), root=root / "session", services=ResearchApplicationServices(max_results=1,
                    config={"execution": {"dataset": str(data), "timeout_sec": 10}},
                    budget_limits={"process_invocations": 1, "process_wall_seconds": 10}))
                prepared = app.advance(max_actions=9)
                self.assertIn("preparation", prepared.state_refs, prepared.status_reason)
                self.assertNotIn("experiment", prepared.state_refs)
                self.assertEqual(app.budget_ledger.remaining("process_invocations"), 1)
                app = load_session(root / "session")
                view = app.advance(max_actions=2)
                if not swapped:
                    from dataclasses import replace
                    from simple_ar.report.schema import AgentReportResult, ReportSectionDraft
                    from simple_ar.core.artifacts import read_json
                    self.assertEqual(view.next_action, "report_write")
                    app.services = replace(app.services, llm_client=LLMClient(LLMSettings(api_key="test-key", api_mode="chat")))
                    saved_sections = {"sections": [{"section_id": "abstract"}]}
                    def interrupted_writer(**kwargs):
                        kwargs["checkpoint_sink"](saved_sections)
                        return None
                    with patch("simple_ar.report.writing.run_report_agent", side_effect=interrupted_writer):
                        failed_write = app.advance()
                    self.assertEqual(failed_write.status, "paused")
                    self.assertNotIn("report", failed_write.state_refs)
                    failed_attempt = app.controller.manifest.current_attempt
                    self.assertTrue((app.controller.store.root / "attempts" / failed_attempt / "report_inputs.json").is_file())
                    app.continue_session(reason="Retry the failed writer without rerunning research.")
                    def writer(**kwargs):
                        self.assertEqual(kwargs["completed_checkpoint"], saved_sections)
                        attempt_id = app.controller.manifest.current_attempt
                        attempt = app.controller.store.root / "attempts" / attempt_id
                        self.assertTrue((attempt / "report_inputs.json").is_file())
                        self.assertEqual(app.controller.manifest.status, "running")
                        snapshot = read_json(attempt / "report_inputs.json")
                        self.assertTrue(snapshot["snapshot_id"])
                        kwargs["client"].ask_json("Fixture writer", "Return an empty JSON object.", label="report-test")
                        paper_id = kwargs["context"].papers[0]["id"]
                        return AgentReportResult(report_body="", memory=kwargs["memory"], used_agent=True,
                            sections=[ReportSectionDraft(section_id=str(i), heading=heading,
                                draft_markdown=f"Exploratory local text baseline evaluation [@{paper_id}].",
                                used_sources=[paper_id]) for i, heading in enumerate((
                                "Abstract", "Introduction", "Related Work", "Method", "Experimental Setup", "Results", "Limitations", "Conclusion"))])
                    response = {"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}
                    with patch("simple_ar.report.writing.run_report_agent", side_effect=writer), patch("simple_ar.integrations.llm._call_openai_sdk", return_value=response):
                        with patch.object(app, "_persist_application_views", side_effect=RuntimeError("interrupted writer")):
                            with self.assertRaisesRegex(RuntimeError, "interrupted writer"):
                                app.advance()
                    app = load_session(root / "session")
                    with patch("simple_ar.report.writing.run_report_agent", side_effect=AssertionError("Do not rewrite completed paper")):
                        view = app.advance(max_actions=2)
                    self.assertIn("report_audit", view.state_refs, view.status_reason)
                    writer_ids = {item["attempt_id"] for item in view.attempts if item["capability"] == "report_write"}
                    llm_entries = [entry for entry in app.budget_ledger.entries if "llm_requests" in entry.actual]
                    self.assertTrue(llm_entries)
                    self.assertTrue(all(entry.attempt_id in writer_ids for entry in llm_entries))
                    report = app.controller.store.read_text(view.state_refs["report"])
                    self.assertIn("Abstract", report)
                    self.assertIn("References", report)
                self.assertEqual(view.status, "completed", view.status_reason)
                result = app.controller.store.read_json(app.latest_experiment_ref())
                self.assertEqual(result["execution_status"], "passed")
                self.assertEqual(result["metrics"]["accuracy"], 0.0 if swapped else 1.0)
                self.assertEqual(result["metrics"]["train_examples"], 4)
                self.assertEqual(result["metrics"]["eval_examples"], 2)
                self.assertEqual(result["measurement"]["asset_integrity"]["status"], "observed_unchanged")
                prep = app.controller.store.read_json(view.state_refs["preparation"])
                self.assertTrue(any("possible leakage" in text for text in prep["limitations"]))
                self.assertEqual(result["preparation"]["source_ref"], view.state_refs["preparation"].to_dict())
                self.assertEqual(result["preparation"]["limitations"], prep["limitations"])
                analysis = app.controller.store.read_json(view.state_refs["analysis"])["analysis"]
                for limitation in prep["limitations"]:
                    self.assertIn(limitation, analysis["audit"]["limitations"])
                    self.assertIn(limitation, analysis["readme_markdown"])
                self.assertNotIn("implementation", view.state_refs)
                report_context, report_memory = app.report_inputs()
                self.assertEqual(report_context.results["metrics"]["accuracy"], result["metrics"]["accuracy"])
                self.assertEqual(report_context.experiment_plan, result["experiment_contract"])
                metric = next(row for row in report_context.metric_sources if row.name == "accuracy")
                self.assertEqual(metric.measurement_id, result["measurement"]["measurement_id"])
                self.assertEqual(metric.protocol_fingerprint, result["measurement"]["protocol_fingerprint"])
                self.assertEqual(metric.condition_id, "text_baseline")
                self.assertEqual(metric.source_kind, "measured")
                self.assertEqual(metric.unit, "fraction")
                self.assertEqual(metric.direction, "higher")
                for limitation in prep["limitations"]:
                    self.assertIn(limitation, report_memory.limitations)
                self.assertEqual(app.budget_ledger.remaining("process_invocations"), 0)

    def test_csv_preparation_does_not_guess_splits_or_truncate_data(self):
        from simple_ar.research.text_dataset import read_text_dataset
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.csv"
            path.write_text("text,label,split\nhello,a,train\nworld,b,train\nhello,a,test\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no split is inferred"):
                read_text_dataset(path)
            with self.assertRaisesRegex(ValueError, "byte"):
                read_text_dataset(path, max_bytes=4)
            path.write_text("text,label,split\nhello,a,train\nworld,b,train\nhello,a,eval\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "row preparation limit"):
                read_text_dataset(path, max_rows=2)

    def test_paired_experiment_recovers_baseline_and_finishes_negative_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "paper.md"
            paper.write_text("# Classification\n\nEvaluate threshold classifiers on held-out data.\n", encoding="utf-8")
            evaluator = root / "evaluate.py"
            evaluator.write_text(
                "import sys\nfrom pathlib import Path\n"
                "condition = sys.argv[1]\n"
                "with Path('runs.txt').open('a') as log: log.write(condition + '\\n')\n"
                "data = [(0, 0), (1, 1), (2, 1), (3, 0)]\n"
                "predictions = [int(x > 0) if condition == 'baseline' else 0 for x, y in data]\n"
                "accuracy = sum(p == y for p, (x, y) in zip(predictions, data)) / len(data)\n"
                "print('accuracy:', accuracy)\n", encoding="utf-8",
            )
            execution = {
                "command": [sys.executable, str(evaluator), "candidate"],
                "baseline": {"command": [sys.executable, str(evaluator), "baseline"]},
                "cwd": str(root), "timeout_sec": 5,
                "result_schema": {"primary_metric": "accuracy", "direction": "higher", "required_metrics": ["accuracy"]},
                "protocol": {"contract_id": "paired-fixture", "hypothesis": "Check a threshold change.",
                             "dataset_refs": [{"asset_id": "four-examples", "revision": "1"}],
                             "split_spec": {"held_out": [0, 1, 2, 3]},
                             "metric_specs": [{"name": "accuracy", "unit": "fraction", "direction": "higher"}],
                             "comparison_conditions": {"seed": 0},
                             "protected_assets": [{"asset_id": "evaluator", "path": str(evaluator)}]},
            }
            app = create_session(ResearchBrief(
                request_text="Compare threshold classifiers.", requested_outputs=("experiments",),
                asset_requests=({"locator": str(paper), "role": "paper"},),
            ), root=root / "session", services=ResearchApplicationServices(
                max_results=1, config={"execution": execution},
                budget_limits={"process_invocations": 2, "process_wall_seconds": 10},
            ))
            app.advance(max_actions=8)
            self.assertEqual(app.view().next_action, "baseline")
            with patch.object(app, "_persist_application_views", side_effect=RuntimeError("interrupted baseline")):
                with self.assertRaisesRegex(RuntimeError, "interrupted baseline"):
                    app.advance()
            app = load_session(root / "session")
            measured = app.advance()
            self.assertIn("baseline", measured.state_refs)
            self.assertIn("experiment", measured.state_refs)
            self.assertEqual(measured.next_action, "analysis")
            app = load_session(root / "session")
            final = app.advance()
            self.assertEqual(final.status, "completed")
            comparison = app.controller.store.read_json(final.state_refs["comparison"])
            self.assertEqual(comparison["comparability"], "declared_match")
            baseline = app.controller.store.read_json(final.state_refs["baseline"])
            self.assertEqual(baseline["measurement"]["asset_integrity"]["status"], "observed_unchanged")
            self.assertEqual(comparison["verdict"], "regressed")
            self.assertEqual(comparison["deltas"]["accuracy"], -0.25)
            analysis = app.controller.store.read_json(final.state_refs["analysis"])
            self.assertEqual(analysis["analysis"]["status"], "metric_below_target")
            app.advance(max_actions=20)
            self.assertEqual((root / "runs.txt").read_text().splitlines(), ["baseline", "candidate"])
            self.assertEqual(app.budget_ledger.remaining("process_invocations"), 0)

    def test_experiment_budget_exhaustion_preserves_research_without_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "paper.md"
            paper.write_text("# Evaluation\n\nValidation measures accuracy.\n", encoding="utf-8")
            app = create_session(ResearchBrief(
                request_text="Evaluate validation.", requested_outputs=("experiment",),
                asset_requests=({"locator": str(paper), "role": "paper"},),
            ), root=root / "session", services=ResearchApplicationServices(max_results=1, config={
                "execution": {"command": [sys.executable, "-c", "from pathlib import Path; Path('ran').touch()"],
                              "cwd": str(root), "timeout_sec": 5},
            }, budget_limits={"process_invocations": 0, "process_wall_seconds": 5}))
            view = app.advance(max_actions=10)
            self.assertEqual(view.status, "paused")
            self.assertIn("summary", view.state_refs)
            self.assertNotIn("experiment", view.state_refs)
            self.assertFalse((root / "ran").exists())
            self.assertEqual(len(app.budget_ledger.entries), 0)

    def test_explicit_retry_reuses_research_and_reanalyzes_corrected_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "paper.md"
            paper.write_text("# Evaluation\n\nMeasure classifier accuracy.\n", encoding="utf-8")
            services = ResearchApplicationServices(max_results=1, config={
                "execution": {
                    "command": [sys.executable, "-c", "print('accuracy: 0.2'); raise SystemExit(1)"],
                    "cwd": str(root), "timeout_sec": 5,
                    "result_schema": {"primary_metric": "accuracy", "required_metrics": ["accuracy"]},
                    "protocol": {
                        "contract_id": "retry-fixture", "hypothesis": "Fix a command failure.",
                        "dataset_refs": [{"asset_id": "fixture", "revision": "1"}],
                        "split_spec": {"evaluation": "all"},
                        "metric_specs": [{"name": "accuracy", "unit": "fraction"}],
                        "comparison_conditions": {"seed": 0},
                    },
                },
            }, budget_limits={"process_invocations": 2, "process_wall_seconds": 10})
            app = create_session(ResearchBrief(
                request_text="Evaluate classifier accuracy.", requested_outputs=("experiment",),
                asset_requests=({"locator": str(paper), "role": "paper"},),
            ), root=root / "session", services=services)
            app.advance(max_actions=8)
            failed = app.advance()
            self.assertEqual(failed.next_action, "analysis")
            first_attempt = next(item["attempt_id"] for item in failed.attempts if item["capability"] == "experiment")
            synthesis_ref = failed.state_refs["synthesis"]

            retried = app.retry_experiment(
                command=(sys.executable, "-c", "print('accuracy: 0.8')"),
                cwd=root,
                timeout_sec=5,
                parent_attempt_id=first_attempt,
            )

            self.assertEqual(retried.status, "completed")
            self.assertEqual(retried.revision, 1)
            self.assertEqual(app.brief.revision, 2)
            self.assertEqual(retried.state_refs["synthesis"], synthesis_ref)
            self.assertEqual(
                [item["capability"] for item in retried.attempts].count("experiment"), 2
            )
            retry_attempt = next(
                item for item in retried.attempts
                if item["capability"] == "experiment" and item["attempt_id"] != first_attempt
            )
            self.assertEqual(retry_attempt["parent_attempt"], first_attempt)
            result = app.controller.store.read_json(retried.state_refs["experiment"])
            self.assertEqual(result["execution_status"], "passed")
            self.assertEqual(result["metrics"]["accuracy"], 0.8)
            self.assertEqual(app.budget_ledger.remaining("process_invocations"), 0)

    def test_explicit_experiment_is_measured_once_and_analyzed_after_reload(self):
        for exitcode in (0, 3):
            with self.subTest(exitcode=exitcode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                paper = root / "paper.md"
                paper.write_text("# Evaluation\n\nValidation measures classifier accuracy.\n", encoding="utf-8")
                script = "from pathlib import Path; Path('ran').write_text('once'); print('accuracy: 0.5'); raise SystemExit(%d)" % exitcode
                services = ResearchApplicationServices(max_results=1, config={
                    "execution": {"command": [sys.executable, "-c", script], "cwd": str(root), "timeout_sec": 5,
                                  "result_schema": {"primary_metric": "accuracy", "required_metrics": ["accuracy"]},
                                  "protocol": {"contract_id": "fixture-1", "hypothesis": "Characterize a fixture.",
                                               "dataset_refs": [{"asset_id": "fixture", "revision": "1"}],
                                               "split_spec": {"evaluation": "all"},
                                               "metric_specs": [{"name": "accuracy", "unit": "fraction"}],
                                               "comparison_conditions": {"seed": 0}}},
                }, budget_limits={"process_invocations": 1, "process_wall_seconds": 5})
                # A command in configuration alone never authorizes execution.
                summary = create_session(ResearchBrief(
                    request_text="Summarize classifier validation.", requested_outputs=("summary",),
                    asset_requests=({"locator": str(paper), "role": "paper"},),
                ), root=root / "summary-session", services=services)
                summary.advance(max_actions=10)
                self.assertFalse((root / "ran").exists())
                self.assertEqual(summary.budget_ledger.remaining("process_invocations"), 1)
                app = create_session(ResearchBrief(
                    request_text="Evaluate classifier validation.", requested_outputs=("experiment",),
                    asset_requests=({"locator": str(paper), "role": "paper"},),
                ), root=root / "session", services=services)
                app.advance(max_actions=8)
                if exitcode:
                    # Simulate process termination after the physical result
                    # is durable but before application references are saved.
                    with patch.object(app, "_persist_application_views", side_effect=RuntimeError("interrupted")):
                        with self.assertRaisesRegex(RuntimeError, "interrupted"):
                            app.advance()
                else:
                    app.advance()
                measured = app.view()
                self.assertIn("experiment", measured.state_refs)
                self.assertNotIn("analysis", measured.state_refs)
                result = app.controller.store.read_json(measured.state_refs["experiment"])
                self.assertEqual(result["returncode"], exitcode)
                self.assertEqual(result["metrics"]["accuracy"], 0.5)
                self.assertEqual(result["measurement"]["protocol_status"], "declared")
                self.assertEqual(result["experiment_contract"]["contract_id"], "fixture-1")
                app = load_session(root / "session")
                view = app.advance(max_actions=2)
                self.assertIn("analysis", view.state_refs)
                self.assertEqual(app.budget_ledger.remaining("process_invocations"), 0)
                self.assertEqual(sum(item["capability"] == "experiment" for item in view.attempts), 1)
                analysis = app.controller.store.read_json(view.state_refs["analysis"])
                self.assertEqual(analysis["execution_status"], "passed" if exitcode == 0 else "failed")
                self.assertEqual(view.status, "completed")

    def test_model_assessment_drives_design_once_and_abstention_keeps_summary(self):
        import json
        from dataclasses import replace

        for recommend in (True, False):
            with self.subTest(recommend=recommend), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                paper = root / "paper.md"
                paper.write_text("# Results\n\nValidation improves accuracy for reliable agents.\n", encoding="utf-8")
                app = create_session(
                    ResearchBrief(request_text="Study validation for agents", requested_outputs=("research_design",),
                                  asset_requests=({"locator": str(paper), "role": "paper"},)),
                    root=root / "session", services=ResearchApplicationServices(max_results=1, config={
                        "execution": {"command": [sys.executable, "evaluate.py"], "cwd": str(root), "timeout_sec": 5,
                                      "protocol": {"protected_assets": [{"path": "heldout.csv"}]}}}),
                )
                app.advance(max_actions=6)
                synthesis = app.controller.store.read_json(app.view().state_refs["synthesis"])
                self.assertIn("heldout.csv", synthesis["execution_context"])
                self.assertIn("Study validation for agents", synthesis["execution_context"])
                self.assertNotIn("experiment", app.view().state_refs)

                class Client:
                    calls = 0
                    selected = None

                    def ask_json(self, system, user, **kwargs):
                        self.calls += 1
                        payload = json.loads(user)
                        assert "heldout.csv" in payload["constraints"]["research_request"]
                        self.selected = payload["candidates"][-1]["idea_id"] if recommend else None
                        rows = []
                        for candidate in payload["candidates"]:
                            rows.append(dict(
                                idea_id=candidate["idea_id"], relevance="Relevant", differentiation="Unknown",
                                feasibility="Small experiment", cost="One comparison", falsifiability="No gain",
                                recommendation="Check effect", supporting_evidence_refs=[payload["evidence"][0]["chunk_id"]],
                                counter_evidence_refs=[], unknowns=["Effect uncertain"],
                            ))
                        return dict(assessments=rows, recommended_idea_id=self.selected,
                                    recommendation_reason="Chosen from shared evidence")

                client = Client()
                app.services = replace(app.services, llm_client=client)
                # Synthesis permits paper IDs as motivation, while the model's
                # detailed comparison must still cite the supplied text chunks.
                read = app._load_read()
                paper_ref = read.paper_cards[0].paper_id
                self.assertNotIn(paper_ref, {chunk.chunk_id for chunk in read.bundle.chunks})
                source = app._load_synthesis()
                source = replace(source, ideas=tuple(
                    replace(idea, motivation_refs=(paper_ref,)) for idea in source.ideas
                ))
                with patch.object(app, "_load_synthesis", return_value=source):
                    view = app.advance(max_actions=2)
                assessment = app.controller.store.read_json(view.state_refs["assessment"])
                self.assertFalse(any("unresolved evidence refs" in item for item in assessment["diagnostics"]))
                self.assertEqual(client.calls, 1)
                self.assertIn("summary", view.state_refs)
                if recommend:
                    self.assertEqual(view.status, "completed")
                    design = app.controller.store.read_json(view.state_refs["design"])
                    self.assertEqual(design["selected_idea"]["idea_id"], client.selected)
                    self.assertEqual(design["selection_rationale"], "Chosen from shared evidence")
                else:
                    self.assertEqual(view.status, "paused")
                    self.assertNotIn("design", view.state_refs)

    def test_llm_request_keeps_application_attempt_id_in_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = LLMClient(
                LLMSettings(
                    api_key="test-key",
                    api_mode="chat",
                    max_output_tokens=5,
                    retry_attempts=1,
                )
            )
            app = create_session(
                ResearchBrief(request_text="Study agents"),
                root=tmp,
                services=ResearchApplicationServices(llm_client=client),
            )
            request = ResearchPlanRequest(
                topic="Study agents",
                use_llm=True,
                llm_client=app.services.llm_client,
            )
            bound = app._request_for_attempt(request, "plan-0001")
            response = {"choices": [{"message": {"content": "ok"}}]}
            with patch(
                "simple_ar.integrations.llm._call_openai_sdk", return_value=response
            ):
                bound.llm_client.ask("system", "user", label="plan")

            ledger = BudgetLedger.load(Path(tmp) / "budget_ledger.json")
            self.assertEqual(ledger.entries[0].attempt_id, "plan-0001")

    def test_reload_reuses_completed_result_before_state_reference_is_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = create_session(ResearchBrief(request_text="Study agents"), root=tmp)
            with patch.object(app.controller, "attempt_output_ref", side_effect=RuntimeError("interrupted")):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    app.advance()
            resumed = load_session(tmp)
            # Stop at the next boundary: recovery itself must not invoke plan.
            with patch.object(resumed, "_run_action", return_value=False) as next_action:
                view = resumed.advance()
            next_action.assert_called_once_with("search")
            self.assertEqual(len(view.attempts), 1)
            self.assertEqual(view.budget["attempts"], 1)
            self.assertIn("plan", view.state_refs)

    def test_reload_reconciles_terminal_attempt_before_budget_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = create_session(ResearchBrief(request_text="Study agents"), root=tmp)
            save = app.controller.save
            def interrupted_save():
                if app.controller.manifest.budget.attempts:
                    raise RuntimeError("interrupted budget save")
                return save()
            with patch.object(app.controller, "save", side_effect=interrupted_save):
                with self.assertRaisesRegex(RuntimeError, "interrupted budget save"):
                    app.advance()
            resumed = load_session(tmp)
            self.assertEqual(resumed.controller.manifest.budget.attempts, 0)
            with patch.object(resumed, "_run_action", return_value=False):
                view = resumed.advance()
            self.assertEqual(view.budget["attempts"], 1)
            self.assertEqual(len(view.attempts), 1)
            self.assertIn("plan", view.state_refs)

    def test_reload_services_preserve_limits_and_missing_config_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = create_session(
                ResearchBrief(request_text="Study agents"), root=tmp,
                services=ResearchApplicationServices(max_results=2, max_chunks=None, idea_limit=1),
            )
            resumed = load_session(tmp, services=ResearchApplicationServices(llm_client=object()))
            self.assertEqual(resumed.services.max_results, 2)
            self.assertIsNone(resumed.services.max_chunks)
            self.assertEqual(resumed.services.idea_limit, 1)
            (Path(tmp) / app.controller.manifest.state_refs["runtime_config"].path).unlink()
            with self.assertRaisesRegex(ResearchApplicationError, "runtime configuration"):
                load_session(tmp)

    def test_application_advances_local_research_to_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "reliable_agents.md"
            paper.write_text(
                "# Method\n\n"
                "Validation improves reliable agent behavior.\n\n"
                "# Results\n\n"
                "The AgentBench dataset reports accuracy: 0.75.\n",
                encoding="utf-8",
            )
            app = create_session(
                ResearchBrief(
                    request_text="Study validation methods for reliable agents.",
                    objective="Understand how validation affects reliable agents.",
                    requested_outputs=("research_summary",),
                    asset_requests=(
                        {"locator": str(paper), "role": "paper"},
                    ),
                ),
                root=root / "session",
                services=ResearchApplicationServices(max_results=2, max_chunks=20),
            )

            initial = app.view()
            self.assertEqual(initial.status, "created")
            self.assertEqual(initial.next_action, "plan")
            self.assertIn("brief", initial.state_refs)
            self.assertIn("assets", initial.state_refs)
            self.assertIsNotNone(app.controller.manifest.budget_ledger_ref)
            self.assertTrue((root / "session" / "budget_ledger.json").is_file())
            self.assertEqual(initial.work_plan["next_action"], "plan")
            self.assertEqual(initial.work_plan["status"], "ready")
            self.assertNotIn("brief", initial.work_plan["accepted_refs"])
            self.assertNotIn("assets", initial.work_plan["accepted_refs"])
            self.assertTrue((root / "session" / "planning" / "work_plan.json").is_file())
            self.assertFalse((root / "session" / "planning" / "readiness.json").exists())
            self.assertNotIn("readiness", initial.to_dict())

            final = app.advance(max_actions=6)

            self.assertEqual(final.status, "completed")
            self.assertIsNone(final.next_action)
            self.assertEqual(
                [item["capability"] for item in final.attempts],
                ["document_ingest", "plan", "read", "search", "synthesize"],
            )
            self.assertTrue(
                (root / "session" / "outputs" / "research_summary.md").is_file()
            )
            summary = (
                root / "session" / "outputs" / "research_summary.md"
            ).read_text(encoding="utf-8")
            self.assertIn("## Abstract", summary)
            self.assertIn("## Evidence collection", summary)
            self.assertIn("## Synthesis", summary)
            self.assertIn("summary", final.state_refs)
            self.assertIn("summary_snapshot", final.state_refs)
            self.assertEqual(final.work_plan["status"], "completed")
            self.assertEqual(final.work_plan["requested_outputs"][0]["status"], "satisfied")

            restored = load_session(root / "session")
            self.assertEqual(restored.view().status, "completed")
            export_ref = restored.export_session()
            self.assertEqual(export_ref.kind, "session_snapshot")
            self.assertTrue((root / "session" / export_ref.path).is_file())
            restored.continue_session(revised_brief=ResearchBrief(
                request_text="Compare validation methods for reliable agents.",
                requested_outputs=("research_summary",),
                asset_requests=({"locator": str(paper), "role": "paper"},),
            ))
            revised = restored.advance()
            self.assertEqual(revised.next_action, "search")
            self.assertEqual(revised.budget["attempts"], 6)
            self.assertNotEqual(revised.state_refs["plan"], final.state_refs["plan"])

    def test_application_can_be_advanced_in_small_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "agents.txt"
            paper.write_text(
                "Reliable agents use validation and report accuracy metrics.\n",
                encoding="utf-8",
            )
            app = create_session(
                ResearchBrief(
                    request_text="Study reliable agents.",
                    requested_outputs=("research_summary",),
                    asset_requests=({"locator": str(paper), "role": "paper"},),
                ),
                root=root / "session",
                services=ResearchApplicationServices(max_results=1, max_chunks=10),
            )

            partial = app.advance(max_actions=2)
            self.assertEqual(partial.status, "running")
            self.assertEqual(partial.next_action, "document_ingest")
            self.assertEqual(partial.budget["attempts"], 2)

            resumed = load_session(root / "session")
            final = resumed.advance(max_actions=4)
            self.assertEqual(final.status, "completed")
            self.assertEqual(final.budget["attempts"], 5)

    def test_requested_experiment_preserves_summary_and_design_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "agents.md"
            paper.write_text(
                "# Results\n\nValidation reports accuracy 0.75 for reliable agents.\n",
                encoding="utf-8",
            )
            app = create_session(
                ResearchBrief(
                    request_text="Study reliable agents.",
                    requested_outputs=("experiment", "report"),
                    asset_requests=({"locator": str(paper), "role": "paper"},),
                ),
                root=root / "session",
                services=ResearchApplicationServices(max_results=1, max_chunks=10),
            )

            view = app.advance(max_actions=9)

            self.assertEqual(view.status, "paused")
            self.assertIn("Provide execution", view.status_reason)
            self.assertEqual(
                [item["capability"] for item in view.attempts],
                ["assess_ideas", "document_ingest", "plan", "read", "research_design", "search", "synthesize"],
            )
            self.assertNotIn("experiment", {item["capability"] for item in view.attempts})
            self.assertTrue((root / "session" / "outputs" / "research_summary.md").is_file())
            assessment_ref = view.state_refs["assessment"]
            self.assertTrue((root / "session" / assessment_ref.path).is_file())
            self.assertIn("design", view.state_refs)
            self.assertEqual(view.work_plan["status"], "partial")
            outputs = {row["name"]: row for row in view.work_plan["requested_outputs"]}
            self.assertEqual(outputs["experiment"]["status"], "blocked")
            self.assertEqual(outputs["report"]["status"], "pending")

    def test_deterministic_plan_mode_keeps_later_llm_stages_available(self) -> None:
        class Client:
            model = "fake"

            def ask_json(self, system, user, **kwargs):
                raise AssertionError("deterministic plan mode must not invoke the planner")

        with tempfile.TemporaryDirectory() as tmp:
            app = create_session(
                ResearchBrief(request_text="Study agents"),
                root=Path(tmp),
                services=ResearchApplicationServices(
                    llm_client=Client(),
                    config={"research_plan_mode": "deterministic"},
                ),
            )
            view = app.advance(max_actions=1)

        self.assertEqual(view.status, "running")
        self.assertEqual(view.next_action, "search")
        self.assertIn("plan", view.state_refs)


if __name__ == "__main__":
    unittest.main()
