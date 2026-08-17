from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simple_ar.core.artifacts import read_json, read_jsonl, read_text, write_json
from simple_ar.experiment.code_task_bridge import (
    CODE_TASK_PROJECT_TEMPLATE,
    CODE_TASK_TOY_SPAM_TEMPLATE,
    CodeTaskExperimentResult,
)
from simple_ar.core.pipeline import Context, MissingInputError, PipelineEvent, PipelineRunner
from simple_ar.integrations.llm import LLMError
from simple_ar.pipeline_stages.common import _handle_llm_failure
from simple_ar.pipeline_stages.registry import HANDLERS
from simple_ar.pipeline_stages.experiment import execute_code, execute_design
from simple_ar.pipeline_stages.research import execute_read
from simple_ar.core.stages import Stage


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


def handlers():
    return {Stage(number): handler for number, handler in HANDLERS.items()}


class PipelineTests(unittest.TestCase):
    def test_online_llm_failure_does_not_silently_fallback(self) -> None:
        ctx = Context(Path("offline-test-run"), "toy topic", config={"use_llm": True})
        with self.assertRaises(LLMError):
            _handle_llm_failure(ctx, "LLM stage failed", RuntimeError("503"))

    def test_llm_fallback_requires_explicit_opt_in(self) -> None:
        messages: list[str] = []
        ctx = Context(
            Path("offline-test-run"),
            "toy topic",
            config={"use_llm": True, "allow_llm_fallback": True},
            reporter=lambda event: messages.append(event.message),
        )
        _handle_llm_failure(ctx, "LLM stage failed", RuntimeError("503"))
        self.assertTrue(any("explicit offline fallback" in message for message in messages))

    def test_run_to_plan_creates_expected_outputs(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            ctx = Context(Path(tmp) / "run", "toy topic")
            PipelineRunner(handlers()).run(ctx, to_stage=Stage.PLAN)

            self.assertTrue((ctx.run_dir / "01-plan" / "goal.md").is_file())
            self.assertTrue((ctx.run_dir / "01-plan" / "problem.md").is_file())
            self.assertTrue((ctx.run_dir / "01-plan" / "contract.json").is_file())
            self.assertTrue((ctx.run_dir / "01-plan" / "report.md").is_file())
            self.assertTrue((ctx.run_dir / "01-plan" / "stage_meta.json").is_file())
            self.assertTrue((ctx.run_dir / "state.json").is_file())

    def test_missing_input_fails_clearly(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            ctx = Context(Path(tmp) / "run", "toy topic")
            with self.assertRaises(MissingInputError):
                PipelineRunner(handlers()).run(
                    ctx,
                    from_stage=Stage.SEARCH,
                    to_stage=Stage.SEARCH,
                )

    def test_full_stub_pipeline_reaches_report(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            ctx = Context(Path(tmp) / "run", "toy topic")
            executions = PipelineRunner(handlers()).run(ctx)

            self.assertEqual(len(executions), 8)
            self.assertTrue((ctx.run_dir / "02-search" / "contract.json").is_file())
            self.assertTrue((ctx.run_dir / "02-search" / "report.md").is_file())
            self.assertTrue((ctx.run_dir / "02-search" / "search_meta.json").is_file())
            self.assertFalse((ctx.run_dir / "02-search" / "planning" / "research_plan.json").exists())
            self.assertFalse((ctx.run_dir / "02-search" / "traces" / "retrieval_rounds.jsonl").exists())
            self.assertFalse((ctx.run_dir / "02-search" / "review" / "coverage_report.json").exists())
            self.assertTrue((ctx.run_dir / "02-search" / "documents" / "documents.jsonl").is_file())
            self.assertFalse((ctx.run_dir / "02-search" / "documents" / "sections.jsonl").exists())
            self.assertTrue((ctx.run_dir / "02-search" / "research_index" / "chunks.jsonl").is_file())
            self.assertFalse((ctx.run_dir / "02-search" / "cards").exists())
            self.assertTrue((ctx.run_dir / "03-read" / "review" / "screening_decisions.jsonl").is_file())
            self.assertTrue((ctx.run_dir / "03-read" / "review" / "shortlist.jsonl").is_file())
            self.assertTrue((ctx.run_dir / "03-read" / "review" / "reading_table.md").is_file())
            self.assertFalse((ctx.run_dir / "03-read" / "cards").exists())
            self.assertTrue((ctx.run_dir / "04-synthesize" / "synthesis_brief.json").is_file())
            self.assertFalse((ctx.run_dir / "04-synthesize" / "evidence" / "evidence_pack.json").exists())
            self.assertFalse((ctx.run_dir / "04-synthesize" / "evidence" / "gap_summary.md").exists())
            self.assertFalse((ctx.run_dir / "04-synthesize" / "evidence" / "idea_candidates.jsonl").exists())
            self.assertFalse((ctx.run_dir / "04-synthesize" / "evidence" / "novelty_checks.jsonl").exists())
            self.assertTrue((ctx.run_dir / "05-design" / "evidence" / "experiment_contract.json").is_file())
            self.assertTrue((ctx.run_dir / "05-design" / "experiment_contract.json").is_file())
            self.assertTrue((ctx.run_dir / "05-design" / "experiment_contract.md").is_file())
            self.assertTrue((ctx.run_dir / "05-design" / "result_schema.json").is_file())
            self.assertTrue((ctx.run_dir / "05-design" / "resource_plan.json").is_file())
            self.assertTrue((ctx.run_dir / "05-design" / "dependency_plan.json").is_file())
            self.assertTrue((ctx.run_dir / "05-design" / "domain_profile.json").is_file())
            self.assertTrue((ctx.run_dir / "05-design" / "contract_validation.json").is_file())
            self.assertFalse((ctx.run_dir / "05-design" / "evidence" / "tool_context.json").exists())
            self.assertFalse((ctx.run_dir / "05-design" / "evidence" / "eval_report.json").exists())
            self.assertFalse((ctx.run_dir / "02-search" / "tools").exists())
            self.assertFalse((ctx.run_dir / "02-search" / "governance").exists())
            search_meta = read_json(ctx.run_dir / "02-search" / "search_meta.json")
            self.assertTrue(search_meta["compact_artifacts"])
            self.assertIn("source_plan", search_meta)
            self.assertNotIn("sections", search_meta)
            self.assertNotIn("method_cards", search_meta)
            self.assertNotIn("evidence_pack", search_meta)
            self.assertNotIn("experiment_contract", search_meta)
            self.assertNotIn("research_plan", search_meta)
            self.assertNotIn("retrieval_rounds", search_meta)
            synthesis_brief = read_json(ctx.run_dir / "04-synthesize" / "synthesis_brief.json")
            self.assertEqual(
                synthesis_brief["source_plan"]["queries"],
                search_meta["source_plan"]["queries"],
            )
            self.assertTrue((ctx.run_dir / "08-report" / "report.md").is_file())
            self.assertTrue((ctx.run_dir / "08-report" / "contract.json").is_file())
            self.assertTrue((ctx.run_dir / "08-report" / "references.bib").is_file())
            self.assertTrue((ctx.run_dir / "08-report" / "citation_map.json").is_file())
            self.assertTrue((ctx.run_dir / "08-report" / "manifest.json").is_file())
            self.assertTrue((ctx.run_dir / "08-report" / "report_quality.json").is_file())
            self.assertTrue((ctx.run_dir / "manifest.json").is_file())
            self.assertTrue((ctx.run_dir / "state.json").is_file())
            self.assertTrue((ctx.run_dir / "source_plan.json").is_file())
            self.assertTrue((ctx.run_dir / "activity_log.jsonl").is_file())
            self.assertTrue((ctx.run_dir / "evidence_ledger.jsonl").is_file())

            manifest = read_json(ctx.run_dir / "manifest.json")
            self.assertEqual(manifest["schema_version"], 2)
            self.assertTrue(all(item["status"] == "done" for item in manifest["stages"]))
            self.assertTrue(all(item["contract_path"] for item in manifest["stages"]))
            self.assertTrue(all(item["report_path"] for item in manifest["stages"]))

            report_manifest = read_json(ctx.run_dir / "08-report" / "manifest.json")
            self.assertEqual(report_manifest["experiment"]["template"], "greenfield_project")
            self.assertIn("results.json", report_manifest["source_artifacts"])
            self.assertIn("evidence_ledger.jsonl", report_manifest["source_artifacts"])
            self.assertIn("report_quality.json", report_manifest["report_artifacts"])
            self.assertGreaterEqual(len(report_manifest["cited_papers"]), 1)
            self.assertLessEqual(len(report_manifest["cited_papers"]), len(report_manifest["papers"]))
            report_quality = read_json(ctx.run_dir / "08-report" / "report_quality.json")
            self.assertEqual(report_quality["status"], "passed")
            self.assertEqual(report_quality["summary"]["body_cited_paper_count"], 1)
            results = read_json(ctx.run_dir / "07-run" / "results.json")
            self.assertEqual(results["schema_version"], "2.5")
            self.assertEqual(results["status"], "passed")
            self.assertIn(results["guard"]["status"], {"passed", "warning"})
            primary_metric = results["result_schema"]["primary_metric"]
            self.assertIn(primary_metric, results["metrics"])
            self.assertTrue((ctx.run_dir / "07-run" / "guard_report.json").is_file())

            report = read_text(ctx.run_dir / "08-report" / "report.md")
            self.assertIn("## Abstract", report)
            self.assertIn("## Evidence Summary", report)
            self.assertIn("## Literature Search", report)
            self.assertIn("## Limitations", report)
            self.assertIn("fixture metadata", report)
            self.assertIn("## References", report)

    def test_synthesize_to_report_uses_research_only_mode_without_results(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            ctx = Context(Path(tmp) / "run", "toy topic")
            runner = PipelineRunner(handlers())
            runner.run(ctx, to_stage=Stage.SYNTHESIZE)

            executions = runner.run(ctx, from_stage=Stage.REPORT, to_stage=Stage.REPORT)

            self.assertEqual(len(executions), 1)
            self.assertFalse((ctx.run_dir / "07-run" / "results.json").exists())
            report_manifest = read_json(ctx.run_dir / "08-report" / "manifest.json")
            self.assertEqual(report_manifest["report_mode"], "research_only")
            report = read_text(ctx.run_dir / "08-report" / "report.md")
            self.assertIn("## Draft Status", report)
            self.assertIn("## Research Question", report)
            self.assertIn("## Available Sources", report)
            self.assertIn("## Evidence Handoff", report)
            self.assertIn("## Boundaries And Next Steps", report)
            self.assertIn("conservative fallback", report)
            self.assertNotRegex(report, r"(?m)^## Method\s*$")
            self.assertNotRegex(report, r"(?m)^## Experiments\s*$")
            self.assertNotRegex(report, r"(?m)^## Results\s*$")
            self.assertIn("No experiment was executed", report)

    def test_reporter_receives_stage_progress_events(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            events: list[PipelineEvent] = []
            ctx = Context(Path(tmp) / "run", "toy topic")

            PipelineRunner(handlers(), reporter=events.append).run(ctx, to_stage=Stage.PLAN)

            event_names = [event.name for event in events]
            self.assertIn("pipeline_start", event_names)
            self.assertIn("stage_start", event_names)
            self.assertIn("stage_done", event_names)
            self.assertIn("pipeline_done", event_names)

    def test_code_task_experiment_template_writes_harness_artifacts(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            ctx = Context(
                run_dir,
                "LLM code task demo",
                config={
                    "experiment_template": CODE_TASK_TOY_SPAM_TEMPLATE,
                    "experiment_timeout_sec": 45,
                    "use_llm": True,
                },
            )
            synth_dir = run_dir / "04-synthesize"
            synth_dir.mkdir(parents=True)
            (synth_dir / "hypothesis.md").write_text(
                "# Hypothesis\n\nPatch an existing baseline.\n",
                encoding="utf-8",
            )

            ctx.current_stage = Stage.DESIGN
            ctx.stage_dir().mkdir(parents=True)
            execute_design(ctx)

            plan = read_json(run_dir / "05-design" / "experiment_plan.json")
            self.assertEqual(plan["template"], CODE_TASK_TOY_SPAM_TEMPLATE)
            self.assertEqual(plan["mode"], "embedded_code_task")
            self.assertEqual(plan["method"], "llm_planned_controlled_patch")
            contract = read_json(run_dir / "05-design" / "experiment_contract.json")
            self.assertEqual(contract["task_kind"], "existing_project")
            self.assertEqual(contract["implementation_mode"], "patch_existing")
            self.assertEqual(contract["result_schema"]["primary_metric"], "benchmark_passed")

            code_dir = run_dir / "06-code"
            fake_result = CodeTaskExperimentResult(
                code_task_run_dir=code_dir / "code_task_run",
                workspace_dir=code_dir / "code_task_run" / "code_task" / "workspace",
                patch_plan_path=code_dir / "code_task_run" / "code_task" / "patch_plan.md",
                proposed_edits_path=(
                    code_dir / "code_task_run" / "code_task" / "meta" / "proposed_edits.json"
                ),
                patch_diff_path=code_dir / "code_task_run" / "code_task" / "patch.diff",
                validation_report_path=(
                    code_dir / "code_task_run" / "code_task" / "meta" / "validation_report.json"
                ),
                plan_mode="llm",
                edit_mode="llm",
                edit_count=1,
                changed_files=("spamfilter/rules.py",),
                validation_status="passed",
            )

            ctx.current_stage = Stage.CODE
            ctx.stage_dir().mkdir(parents=True)
            with patch(
                "simple_ar.experiment.code.prepare_code_task_experiment",
                return_value=fake_result,
            ):
                execute_code(ctx)

            script = read_text(run_dir / "06-code" / "experiment.py")
            self.assertIn("run_code_task_benchmark", script)
            self.assertIn("spamfilter/rules.py", script)
            meta = read_json(run_dir / "06-code" / "code_task_experiment.json")
            self.assertEqual(meta["template"], CODE_TASK_TOY_SPAM_TEMPLATE)
            self.assertEqual(meta["changed_files"], ["spamfilter/rules.py"])

    def test_code_task_project_template_accepts_user_project_config(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        repo_root = Path(__file__).resolve().parents[1]
        config_path = repo_root / "examples" / "full_pipeline_tiny_mlp" / "configs" / "pipeline.toml"
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            ctx = Context(
                run_dir,
                "Tiny digits MLP",
                config={
                    "experiment_template": CODE_TASK_PROJECT_TEMPLATE,
                    "code_task_config": str(config_path),
                    "experiment_timeout_sec": 45,
                    "use_llm": True,
                },
            )
            synth_dir = run_dir / "04-synthesize"
            synth_dir.mkdir(parents=True)
            (synth_dir / "hypothesis.md").write_text(
                "# Hypothesis\n\nImprove an existing lightweight MLP benchmark.\n",
                encoding="utf-8",
            )

            ctx.current_stage = Stage.DESIGN
            ctx.stage_dir().mkdir(parents=True)
            execute_design(ctx)

            plan = read_json(run_dir / "05-design" / "experiment_plan.json")
            self.assertEqual(plan["template"], CODE_TASK_PROJECT_TEMPLATE)
            self.assertEqual(plan["code_task"]["benchmark_command"], "python benchmark.py")
            self.assertEqual(plan["code_task"]["primary_metric"], "accuracy")
            self.assertEqual(plan["code_task"]["scope"], "user_project")
            contract = read_json(run_dir / "05-design" / "experiment_contract.json")
            self.assertEqual(contract["task_kind"], "existing_project")
            self.assertIn("python benchmark.py", contract["benchmark_command"])
            self.assertEqual(contract["result_schema"]["primary_metric"], "accuracy")
            self.assertEqual(read_json(run_dir / "05-design" / "contract_validation.json")["status"], "passed")

            code_dir = run_dir / "06-code"
            fake_result = CodeTaskExperimentResult(
                code_task_run_dir=code_dir / "code_task_run",
                workspace_dir=code_dir / "code_task_run" / "code_task" / "workspace",
                patch_plan_path=code_dir / "code_task_run" / "code_task" / "patch_plan.md",
                proposed_edits_path=(
                    code_dir / "code_task_run" / "code_task" / "meta" / "proposed_edits.json"
                ),
                patch_diff_path=code_dir / "code_task_run" / "code_task" / "patch.diff",
                validation_report_path=(
                    code_dir / "code_task_run" / "code_task" / "meta" / "validation_report.json"
                ),
                plan_mode="llm",
                edit_mode="llm",
                edit_count=2,
                changed_files=("digits_mlp/model.py", "digits_mlp/train.py"),
                validation_status="passed",
                template=CODE_TASK_PROJECT_TEMPLATE,
                baseline_status="passed",
            )

            ctx.current_stage = Stage.CODE
            ctx.stage_dir().mkdir(parents=True)
            with patch(
                "simple_ar.experiment.code.prepare_code_task_experiment",
                return_value=fake_result,
            ):
                execute_code(ctx)

            script = read_text(run_dir / "06-code" / "experiment.py")
            self.assertIn("run_code_task_benchmark", script)
            self.assertIn("comparison_improved", script)
            meta = read_json(run_dir / "06-code" / "code_task_experiment.json")
            self.assertEqual(meta["template"], CODE_TASK_PROJECT_TEMPLATE)
            self.assertEqual(meta["baseline_status"], "passed")

    def test_code_task_project_design_can_generate_missing_task_file(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        repo_root = Path(__file__).resolve().parents[1]
        code_root = repo_root / "examples" / "full_pipeline_tiny_mlp" / "project"
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            ctx = Context(
                run_dir,
                "Upgrade a tiny MLP after literature review",
                config={
                    "experiment_template": CODE_TASK_PROJECT_TEMPLATE,
                    "code_task_code_root": str(code_root),
                    "code_task_benchmark_command": "python benchmark.py",
                    "code_task_primary_metric": "accuracy",
                    "use_llm": False,
                },
            )
            for stage_name, filename, text in (
                ("01-plan", "goal.md", "# Goal\n\nImprove a lightweight MLP baseline.\n"),
                ("01-plan", "problem.md", "# Problem\n\nFind a small local improvement.\n"),
                ("04-synthesize", "synthesis.md", "# Synthesis\n\nPrefer modest architecture tuning.\n"),
                ("04-synthesize", "hypothesis.md", "# Hypothesis\n\nA small source patch can improve validation accuracy.\n"),
            ):
                stage_dir = run_dir / stage_name
                stage_dir.mkdir(parents=True, exist_ok=True)
                (stage_dir / filename).write_text(text, encoding="utf-8")
            write_json(
                run_dir / "04-synthesize" / "synthesis_brief.json",
                {
                    "themes": ["Use feature scaling before classifier training."],
                    "gaps": ["Current implementation may underuse normalized features."],
                    "idea_candidates": [
                        {
                            "idea_id": "scaled-features",
                            "title": "Scaled feature training",
                            "hypothesis": "A small normalization step can improve validation accuracy.",
                            "proposed_change": "Add a bounded feature normalization step before training.",
                            "risks": ["Avoid changing benchmark.py."],
                        }
                    ],
                },
            )

            ctx.current_stage = Stage.DESIGN
            ctx.stage_dir().mkdir(parents=True)
            execute_design(ctx)

            generated_task = run_dir / "05-design" / "generated_code_task.md"
            self.assertTrue(generated_task.is_file())
            task_text = read_text(generated_task)
            self.assertIn("# Code Task", task_text)
            self.assertIn("python benchmark.py", task_text)
            self.assertIn("Research-to-Code Bridge", task_text)
            self.assertIn("Use feature scaling", task_text)
            plan = read_json(run_dir / "05-design" / "experiment_plan.json")
            self.assertEqual(plan["template"], CODE_TASK_PROJECT_TEMPLATE)
            self.assertEqual(plan["code_task"]["task_source"], "generated_from_research")
            self.assertEqual(
                plan["code_task"]["generated_task_file"],
                "05-design/generated_code_task.md",
            )
            self.assertEqual(plan["code_task"]["task_generation"]["mode"], "fallback")
            self.assertIn(
                "04-synthesize/synthesis_brief.json",
                plan["code_task"]["task_generation"]["source_artifacts"],
            )
            contract = read_json(run_dir / "05-design" / "experiment_contract.json")
            self.assertEqual(contract["task_kind"], "existing_project")
            self.assertEqual(contract["result_schema"]["primary_metric"], "accuracy")

    def test_code_task_project_design_can_merge_user_task_with_research_context(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        repo_root = Path(__file__).resolve().parents[1]
        code_root = repo_root / "examples" / "full_pipeline_tiny_mlp" / "project"
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            task_file = Path(tmp) / "task.md"
            task_file.write_text(
                "# User Task\n\nImprove validation accuracy without editing benchmark.py.\n",
                encoding="utf-8",
            )
            ctx = Context(
                run_dir,
                "Upgrade a tiny MLP after literature review",
                config={
                    "experiment_template": CODE_TASK_PROJECT_TEMPLATE,
                    "code_task_code_root": str(code_root),
                    "code_task_task_file": str(task_file),
                    "code_task_benchmark_command": "python benchmark.py",
                    "code_task_primary_metric": "accuracy",
                    "implementation_task_handoff": "merge",
                    "use_llm": False,
                },
            )
            for stage_name, filename, text in (
                ("01-plan", "goal.md", "# Goal\n\nImprove a lightweight MLP baseline.\n"),
                ("01-plan", "problem.md", "# Problem\n\nFind a small local improvement.\n"),
                ("04-synthesize", "synthesis.md", "# Synthesis\n\nPrefer modest architecture tuning.\n"),
                ("04-synthesize", "hypothesis.md", "# Hypothesis\n\nA small source patch can improve validation accuracy.\n"),
            ):
                stage_dir = run_dir / stage_name
                stage_dir.mkdir(parents=True, exist_ok=True)
                (stage_dir / filename).write_text(text, encoding="utf-8")

            ctx.current_stage = Stage.DESIGN
            ctx.stage_dir().mkdir(parents=True)
            execute_design(ctx)

            generated_task = run_dir / "05-design" / "generated_code_task.md"
            self.assertTrue(generated_task.is_file())
            task_text = read_text(generated_task)
            self.assertIn("## User Requirements", task_text)
            self.assertIn("without editing benchmark.py", task_text)
            self.assertIn("## Research-Derived Context", task_text)
            self.assertIn("small source patch can improve validation accuracy", task_text)
            plan = read_json(run_dir / "05-design" / "experiment_plan.json")
            self.assertEqual(plan["code_task"]["task_source"], "merged_user_and_research")
            self.assertEqual(
                plan["code_task"]["generated_task_file"],
                "05-design/generated_code_task.md",
            )
            self.assertEqual(plan["code_task"]["task_generation"]["mode"], "fallback_merge")

    def test_code_stage_stops_on_failed_experiment_contract(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            design_dir = run_dir / "05-design"
            design_dir.mkdir(parents=True)
            (design_dir / "experiment_plan.json").write_text(
                '{"template": "toy_text_classification"}',
                encoding="utf-8",
            )
            (design_dir / "contract_validation.json").write_text(
                '{"status": "failed", "errors": ["missing code_root"]}',
                encoding="utf-8",
            )
            ctx = Context(run_dir, "toy topic", current_stage=Stage.CODE)
            ctx.stage_dir().mkdir(parents=True)

            with self.assertRaises(RuntimeError) as raised:
                execute_code(ctx)

            self.assertIn("Experiment contract validation failed", str(raised.exception))
            self.assertIn("missing code_root", str(raised.exception))

    def test_read_stage_accepts_llm_screening_decisions(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            (run_dir / "01-plan").mkdir(parents=True)
            (run_dir / "01-plan" / "problem.md").write_text("# Problem\nStudy coding agents.\n", encoding="utf-8")
            search_dir = run_dir / "02-search"
            (search_dir / "planning").mkdir(parents=True)
            (search_dir / "documents").mkdir(parents=True)
            (search_dir / "research_index").mkdir(parents=True)
            (search_dir / "papers.jsonl").write_text(
                '{"id":"paper-1","title":"Relevant Coding Agent","source":"fixture","abstract":"coding agent benchmark"}\n'
                '{"id":"paper-2","title":"Unrelated Topic","source":"fixture","abstract":"unrelated"}\n',
                encoding="utf-8",
            )
            (search_dir / "planning" / "research_plan.json").write_text('{"query_plan": {}}', encoding="utf-8")
            (search_dir / "documents" / "documents.jsonl").write_text(
                '{"document_id":"paper-1","title":"Relevant Coding Agent","source":"fixture","abstract":"coding agent benchmark"}\n'
                '{"document_id":"paper-2","title":"Unrelated Topic","source":"fixture","abstract":"unrelated"}\n',
                encoding="utf-8",
            )
            (search_dir / "research_index" / "chunks.jsonl").write_text("", encoding="utf-8")
            ctx = Context(
                run_dir,
                "coding agents",
                config={"use_llm": True, "research_read_batch_size": 1, "research_read_workers": 2},
                current_stage=Stage.READ,
            )
            ctx.stage_dir().mkdir(parents=True)

            with patch("simple_ar.pipeline_stages.research._llm_client", return_value=_FakeReadClient()):
                execute_read(ctx)

            shortlist = read_jsonl(run_dir / "03-read" / "review" / "shortlist.jsonl")
            decisions = (run_dir / "03-read" / "review" / "screening_decisions.jsonl").read_text(encoding="utf-8")
            notes = read_json(run_dir / "03-read" / "paper_notes.json")
            self.assertIn("paper-1", decisions)
            self.assertIn("paper-2", decisions)
            self.assertIn("coarse_relevance_score", decisions)
            self.assertIn("synthesis_hint", decisions)
            self.assertIn("paper-1", str(shortlist))
            self.assertNotIn("paper-2", str(shortlist))
            self.assertEqual([row["paper_id"] for row in notes], ["paper-1"])
            self.assertFalse((run_dir / "03-read" / "cards" / "paper_cards.jsonl").exists())

    def test_read_stage_respects_empty_llm_shortlist(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            (run_dir / "01-plan").mkdir(parents=True)
            (run_dir / "01-plan" / "problem.md").write_text("# Problem\nStudy coding agents.\n", encoding="utf-8")
            search_dir = run_dir / "02-search"
            (search_dir / "planning").mkdir(parents=True)
            (search_dir / "documents").mkdir(parents=True)
            (search_dir / "research_index").mkdir(parents=True)
            (search_dir / "papers.jsonl").write_text(
                '{"id":"paper-1","title":"Unrelated A","source":"fixture","abstract":"biology"}\n'
                '{"id":"paper-2","title":"Unrelated B","source":"fixture","abstract":"chemistry"}\n',
                encoding="utf-8",
            )
            (search_dir / "planning" / "research_plan.json").write_text('{"query_plan": {}}', encoding="utf-8")
            (search_dir / "documents" / "documents.jsonl").write_text(
                '{"document_id":"paper-1","title":"Unrelated A","source":"fixture","abstract":"biology"}\n'
                '{"document_id":"paper-2","title":"Unrelated B","source":"fixture","abstract":"chemistry"}\n',
                encoding="utf-8",
            )
            (search_dir / "research_index" / "chunks.jsonl").write_text("", encoding="utf-8")
            ctx = Context(
                run_dir,
                "coding agents",
                config={"use_llm": True, "research_read_batch_size": 1},
                current_stage=Stage.READ,
            )
            ctx.stage_dir().mkdir(parents=True)

            with patch("simple_ar.pipeline_stages.research._llm_client", return_value=_FakeReadDropAllClient()):
                execute_read(ctx)

            shortlist = read_jsonl(run_dir / "03-read" / "review" / "shortlist.jsonl")
            notes = read_json(run_dir / "03-read" / "paper_notes.json")
            self.assertEqual(shortlist, [])
            self.assertEqual(notes, [])
            self.assertFalse((run_dir / "03-read" / "cards" / "paper_cards.jsonl").exists())


class _FakeReadClient:
    def ask_json(self, system: str, user: str, *, label: str = "") -> dict[str, object]:
        if label == "read-rerank":
            return {
                "ranked_papers": [
                    {
                        "paper_id": "paper-1",
                        "decision": "keep",
                        "reading_priority": 1,
                        "relevance_score": 5,
                        "quality_score": 3,
                        "evidence_role": "benchmark",
                        "reason": "Directly matches coding-agent evaluation.",
                        "synthesis_hint": "Use as the benchmark-oriented evidence anchor.",
                        "confidence": "medium",
                    },
                ]
            }
        return {}

    def ask_json_many(self, requests: list[object], *, max_workers: int) -> list[dict[str, object]]:
        labels = [getattr(request, "label", "") for request in requests]
        if labels and all(str(label).startswith("read-coarse-") for label in labels):
            results: list[dict[str, object]] = []
            for request in requests:
                user = getattr(request, "user", "")
                if "paper-1" in user:
                    results.append(
                        {
                            "decisions": [
                                {
                                    "paper_id": "paper-1",
                                    "decision": "keep",
                                    "coarse_relevance_score": 5,
                                    "likely_facet": "benchmark",
                                    "reason": "Abstract mentions coding-agent benchmark.",
                                    "confidence": "medium",
                                }
                            ]
                        }
                    )
                else:
                    results.append(
                        {
                            "decisions": [
                                {
                                    "paper_id": "paper-2",
                                    "decision": "drop",
                                    "coarse_relevance_score": 0,
                                    "likely_facet": "other",
                                    "reason": "Out of scope.",
                                    "confidence": "medium",
                                }
                            ]
                        }
                    )
            return results
        return [
            {
                "paper_id": "paper-1",
                "problem": "Study coding agents.",
                "method": "Benchmark metadata.",
                "limitation": "Thin metadata.",
                "relevance": "Relevant.",
            },
            {
                "paper_id": "paper-2",
                "problem": "Out of scope.",
                "method": "Unknown.",
                "limitation": "Not relevant.",
                "relevance": "Low.",
            },
        ][: len(requests)]


class _FakeReadDropAllClient:
    def ask_json(self, system: str, user: str, *, label: str = "") -> dict[str, object]:
        if label == "read-rerank":
            return {"ranked_papers": []}
        return {}

    def ask_json_many(self, requests: list[object], *, max_workers: int) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for request in requests:
            user = getattr(request, "user", "")
            paper_id = "paper-1" if "paper-1" in user else "paper-2"
            results.append(
                {
                    "decisions": [
                        {
                            "paper_id": paper_id,
                            "decision": "drop",
                            "coarse_relevance_score": 0,
                            "likely_facet": "other",
                            "reason": "Clearly out of scope.",
                            "confidence": "medium",
                        }
                    ]
                }
            )
        return results


if __name__ == "__main__":
    unittest.main()
