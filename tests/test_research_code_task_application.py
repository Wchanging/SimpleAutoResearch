from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simple_ar.core import CapabilityResult
from simple_ar.app.research_code_task import (
    ResearchCodeTaskSessionError,
    ResearchCodeTaskSessionRequest,
    load_research_code_task_session_result,
    run_research_code_task_session,
)
from simple_ar.app.research_code_task_report import (
    ResearchCodeTaskReportRequest,
    build_code_task_report_inputs,
    run_research_code_task_report_agent,
    run_research_code_task_report_session,
)
from simple_ar.experiment.code_task_bridge import (
    CODE_TASK_PROJECT_TEMPLATE,
    CodeTaskExperimentResult,
    CodeTaskExperimentSpec,
)
from simple_ar.research.contracts import ResearchExperimentContract
from simple_ar.research.synthesis import SynthesisResult
from simple_ar.report.schema import (
    ReportRuntimeConfig,
    ReportSectionDraft,
    ReportTemplateBundle,
)


class ResearchCodeTaskApplicationTests(unittest.TestCase):
    def test_failed_design_surfaces_capability_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def failed_design(*, context: object, request: object) -> CapabilityResult:
                del context, request
                return CapabilityResult(
                    status="failed",
                    diagnostics=("design backend unavailable",),
                )

            with patch(
                "simple_ar.app.research_code_task.run_research_design_capability",
                failed_design,
            ):
                with self.assertRaisesRegex(
                    ResearchCodeTaskSessionError,
                    "design backend unavailable",
                ):
                    run_research_code_task_session(
                        _request(root, _write_synthesis(root))
                    )

    def test_existing_code_task_backend_reaches_result_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthesis_file = _write_synthesis(root)
            with patch(
                "simple_ar.app.research_code_task.prepare_code_task_experiment",
                side_effect=_fake_prepare,
            ):
                result = run_research_code_task_session(
                    _request(root, synthesis_file)
                )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.execution["status"], "passed")
            self.assertEqual(result.analysis.status, "passed")
            self.assertTrue(result.execution_path.is_file())
            self.assertTrue(result.analysis_path.is_file())
            self.assertTrue(
                (result.session_root / "attempts" / "experiment-001" / "code_task_run").is_dir()
            )
            self.assertEqual(
                [decision.action for decision in result.decisions],
                ["accept", "accept", "accept"],
            )
            self.assertIsNotNone(result.design)
            self.assertEqual(result.design_ref.path, "attempts/design-001/research_design.json")

            restored = load_research_code_task_session_result(result.session_root)
            self.assertEqual(restored.status, result.status)
            self.assertEqual(restored.topic, result.topic)
            self.assertEqual(restored.synthesis, result.synthesis)
            self.assertEqual(restored.execution, result.execution)
            self.assertEqual(restored.analysis, result.analysis)
            self.assertEqual(restored.source_ref, result.source_ref)
            self.assertEqual(restored.execution_ref, result.execution_ref)
            self.assertEqual(restored.analysis_ref, result.analysis_ref)
            self.assertEqual(restored.attempts, result.attempts)
            self.assertEqual(restored.decisions, result.decisions)

            execution = json.loads(result.execution_path.read_text(encoding="utf-8"))
            self.assertEqual(execution["metrics"]["accuracy"], 0.8)
            self.assertEqual(execution["baseline"]["metrics"]["accuracy"], 0.7)
            self.assertEqual(execution["comparisons"][0]["verdict"], "improved")

    def test_existing_task_receives_the_selected_research_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthesis_file = _write_synthesis(root)
            source_task = root / "task.md"
            source_task.write_text(
                "# Original task\n\nKeep the public benchmark command.\n",
                encoding="utf-8",
            )
            request = _request(root, synthesis_file)
            request = replace(
                request,
                spec=replace(request.spec, task_file=source_task),
            )
            with patch(
                "simple_ar.app.research_code_task.prepare_code_task_experiment",
                side_effect=_fake_prepare,
            ) as prepare:
                run_research_code_task_session(request)

            received_spec = prepare.call_args.kwargs["spec"]
            materialized = received_spec.task_file.read_text(encoding="utf-8")
            self.assertIn("# Original task", materialized)
            self.assertIn("## Research handoff", materialized)
            self.assertIn("Validation improves reliable agent accuracy.", materialized)
            self.assertIn("add validation", materialized)

    def test_prepared_execution_boundary_overrides_literature_context_in_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthesis_file = _write_synthesis(root)
            payload = json.loads(synthesis_file.read_text(encoding="utf-8"))
            payload["execution_context"] = (
                "Dataset: sklearn.datasets.load_digits. Benchmark: python benchmark.py. "
                "The prepared project is authoritative."
            )
            synthesis_file.write_text(json.dumps(payload), encoding="utf-8")
            source_task = root / "task.md"
            source_task.write_text("# Original task\n", encoding="utf-8")
            request = replace(
                _request(root, synthesis_file),
                spec=replace(_request(root, synthesis_file).spec, task_file=source_task),
            )
            with patch(
                "simple_ar.app.research_code_task.prepare_code_task_experiment",
                side_effect=_fake_prepare,
            ) as prepare:
                run_research_code_task_session(request)

            materialized = prepare.call_args.kwargs["spec"].task_file.read_text(encoding="utf-8")
            self.assertIn("Prepared experiment boundary (authoritative)", materialized)
            self.assertIn("sklearn.datasets.load_digits", materialized)
            self.assertNotIn("- Baseline: baseline", materialized)

    def test_backend_failure_is_retained_as_canonical_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthesis_file = _write_synthesis(root)
            with patch(
                "simple_ar.app.research_code_task.prepare_code_task_experiment",
                side_effect=RuntimeError("candidate generation failed"),
            ):
                result = run_research_code_task_session(
                    _request(root, synthesis_file)
                )

            self.assertEqual(result.execution["status"], "failed")
            self.assertIn(result.status, {"failed", "incomplete"})
            self.assertTrue(
                (result.session_root / "attempts" / "experiment-001" / "code_task_error.json").is_file()
            )
            error = json.loads(
                (
                    result.session_root
                    / "attempts"
                    / "experiment-001"
                    / "code_task_error.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(error["error_type"], "RuntimeError")
            self.assertIn("candidate generation failed", error["message"])
            self.assertEqual(result.decisions[1].action, "repair")

    def test_restore_rejects_a_missing_code_task_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ResearchCodeTaskSessionError):
                load_research_code_task_session_result(Path(tmp) / "missing-session")

    def test_restored_report_continuation_does_not_rerun_code_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthesis_file = _write_synthesis(root)
            with patch(
                "simple_ar.app.research_code_task.prepare_code_task_experiment",
                side_effect=_fake_prepare,
            ) as prepare:
                session = run_research_code_task_session(
                    _request(root, synthesis_file),
                    next_capability="report",
                )
                restored = load_research_code_task_session_result(session.session_root)
                with patch(
                    "simple_ar.app.research_code_task_report.run_research_report_agent_session",
                    return_value="report-result",
                ) as continue_report:
                    result = run_research_code_task_report_agent(
                        restored,
                        title="Reliable agents experiment",
                        template=ReportTemplateBundle(
                            name="experiment",
                            mode="experiment",
                            template_path="template.md",
                            criteria_path="criteria.md",
                            template_markdown="# Results",
                            criteria_markdown="Use evidence.",
                        ),
                        config=ReportRuntimeConfig(reviewer="disabled"),
                        client=object(),
                    )

            self.assertEqual(result, "report-result")
            prepare.assert_called_once()
            continue_report.assert_called_once()
            self.assertEqual(
                continue_report.call_args.kwargs["source_refs"],
                (restored.execution_ref, restored.analysis_ref),
            )

    def test_report_continuation_rejects_incomplete_code_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthesis_file = _write_synthesis(root)
            with patch(
                "simple_ar.app.research_code_task.prepare_code_task_experiment",
                side_effect=_fake_prepare_failed,
            ):
                session = run_research_code_task_session(
                    _request(root, synthesis_file),
                    next_capability="report",
                )
            with self.assertRaisesRegex(
                ResearchCodeTaskSessionError,
                "not ready for a formal report",
            ):
                run_research_code_task_report_agent(
                    session,
                    title="Reliable agents experiment",
                    template=ReportTemplateBundle(
                        name="experiment",
                        mode="experiment",
                        template_path="template.md",
                        criteria_path="criteria.md",
                        template_markdown="# Results",
                        criteria_markdown="Use evidence.",
                    ),
                    config=ReportRuntimeConfig(reviewer="disabled"),
                    client=object(),
                )

    def test_code_task_evidence_reaches_generic_report_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthesis_file = _write_synthesis(root)
            with patch(
                "simple_ar.app.research_code_task.prepare_code_task_experiment",
                side_effect=_fake_prepare,
            ):
                session = run_research_code_task_session(
                    _request(root, synthesis_file)
                )
                context, memory = build_code_task_report_inputs(session)
                report = run_research_code_task_report_session(
                    ResearchCodeTaskReportRequest(
                        code_task=_request(root / "report", synthesis_file),
                        title="Reliable agents experiment",
                        sections=(
                            ReportSectionDraft(
                                section_id="results",
                                heading="Results",
                                draft_markdown=(
                                    "The candidate improved accuracy from 0.7 to 0.8."
                                ),
                            ),
                        ),
                    )
                )

            self.assertEqual(context.topic, "reliable agents")
            self.assertEqual(len(memory.metric_sources), 3)
            self.assertTrue(
                any(
                    item.label == "comparison_delta" and item.name == "accuracy"
                    for item in memory.metric_sources
                )
            )
            self.assertEqual(report.status, "completed")
            self.assertEqual(report.audit.metric_audit.status, "passed")
            self.assertTrue(
                (
                    root
                    / "report"
                    / "session"
                    / "attempts"
                    / "report-001"
                    / "report.md"
                ).is_file()
            )


def _request(root: Path, synthesis_file: Path) -> ResearchCodeTaskSessionRequest:
    return ResearchCodeTaskSessionRequest(
        topic="reliable agents",
        session_root=root / "session",
        synthesis_file=synthesis_file,
        spec=CodeTaskExperimentSpec(
            template=CODE_TASK_PROJECT_TEMPLATE,
            code_root=root / "project",
            task_file=None,
            benchmark_command="python benchmark.py",
            primary_metric="accuracy",
            metric_directions={"accuracy": "higher_is_better"},
        ),
        model="test-model",
        timeout_sec=5,
    )


def _write_synthesis(root: Path) -> Path:
    contract = ResearchExperimentContract(
        contract_id="contract-1",
        hypothesis="Validation improves reliable agent accuracy.",
        baseline="baseline",
        dataset="fixture",
        metrics=["accuracy"],
        proposed_change="add validation",
    )
    synthesis = SynthesisResult(
        status="ready",
        gap_summary="The fixture leaves room for validation.",
        ideas=(),
        novelty_checks=(),
        experiment_contract=contract,
    )
    path = root / "synthesis_result.json"
    path.write_text(
        json.dumps(synthesis.to_handoff_dict(), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _fake_prepare(
    *,
    code_task_run_dir: Path,
    spec: CodeTaskExperimentSpec,
    model: str | None,
    use_llm: bool,
    timeout_sec: int,
    baseline_policy: str,
    baseline_metrics_file: Path | None,
) -> CodeTaskExperimentResult:
    del model, use_llm, timeout_sec, baseline_policy, baseline_metrics_file
    root = code_task_run_dir / "code_task"
    baseline = root / "run" / "baseline"
    patched = root / "run" / "patched"
    baseline.mkdir(parents=True)
    patched.mkdir(parents=True)
    report = {
        "schema_version": 1,
        "status": "passed",
        "label": "{label}",
        "command": ["python", "benchmark.py"],
        "cwd": str(spec.code_root),
        "returncode": 0,
        "timed_out": False,
        "duration_sec": 0.2,
        "metric_values": {"accuracy": 0.7},
    }
    (baseline / "execution_report.json").write_text(
        json.dumps({**report, "label": "baseline"}), encoding="utf-8"
    )
    (baseline / "metrics.json").write_text(json.dumps({"accuracy": 0.7}), encoding="utf-8")
    (patched / "execution_report.json").write_text(
        json.dumps({**report, "label": "patched", "metric_values": {"accuracy": 0.8}}),
        encoding="utf-8",
    )
    (patched / "metrics.json").write_text(json.dumps({"accuracy": 0.8}), encoding="utf-8")
    (root / "run" / "comparison.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "ready",
                "verdict": "improved",
                "metrics": [
                    {
                        "name": "accuracy",
                        "baseline": 0.7,
                        "patched": 0.8,
                        "delta": 0.1,
                        "interpretation": "improved",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    summary = root / "summary.md"
    summary.write_text("# Code-task summary\n", encoding="utf-8")
    return CodeTaskExperimentResult(
        code_task_run_dir=code_task_run_dir,
        workspace_dir=spec.code_root,
        patch_plan_path=root / "patch_plan.md",
        proposed_edits_path=root / "proposed_edits.json",
        patch_diff_path=root / "patch.diff",
        validation_report_path=root / "validation_report.json",
        plan_mode="llm",
        edit_mode="llm",
        edit_count=1,
        changed_files=("src/model.py",),
        validation_status="passed",
        template=spec.template,
        baseline_status="passed",
        work_plan_item_count=2,
        work_item_id="improve-model",
        summary_path=summary,
    )


def _fake_prepare_failed(
    **kwargs: object,
) -> CodeTaskExperimentResult:
    result = _fake_prepare(**kwargs)  # type: ignore[arg-type]
    report_path = (
        result.code_task_run_dir
        / "code_task"
        / "run"
        / "patched"
        / "execution_report.json"
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["status"] = "failed"
    payload["returncode"] = 1
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    return result


if __name__ == "__main__":
    unittest.main()
