from __future__ import annotations

import json
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

from simple_ar.app.research_experiment import (
    ResearchExperimentSessionRequest,
    run_research_experiment_session,
)
from simple_ar.cli import main
from simple_ar.research.contracts import ResearchExperimentContract
from simple_ar.research.synthesis import SynthesisResult


class ResearchExperimentApplicationTests(unittest.TestCase):
    def test_synthesis_handoff_runs_and_analyzes_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthesis_file = _write_synthesis(root)
            result = run_research_experiment_session(
                ResearchExperimentSessionRequest(
                    topic="reliable agents",
                    session_root=root / "session",
                    synthesis_file=synthesis_file,
                    command=(sys.executable, "-c", "print('accuracy: 0.75')"),
                    cwd=root,
                    timeout_sec=5,
                    result_schema={
                        "primary_metric": "accuracy",
                        "required_metrics": ["accuracy"],
                        "metric_directions": {"accuracy": "higher"},
                    },
                )
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.execution["status"], "passed")
            self.assertEqual(result.analysis.status, "passed")
            self.assertEqual(
                [attempt.capability for attempt in result.attempts],
                ["analysis", "experiment"],
            )
            self.assertEqual(
                [decision.action for decision in result.decisions],
                ["accept", "accept"],
            )
            self.assertTrue(result.execution_path.is_file())
            self.assertTrue(result.analysis_path.is_file())
            self.assertTrue(
                (root / "session" / "inputs" / "synthesis.json").is_file()
            )

    def test_failed_execution_is_retained_for_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_research_experiment_session(
                ResearchExperimentSessionRequest(
                    topic="reliable agents",
                    session_root=root / "session",
                    synthesis_file=_write_synthesis(root),
                    command=(
                        sys.executable,
                        "-c",
                        "print('accuracy: 0.25'); raise SystemExit(2)",
                    ),
                    cwd=root,
                    timeout_sec=5,
                    result_schema={"primary_metric": "accuracy"},
                )
            )

            self.assertEqual(result.execution["status"], "failed")
            self.assertTrue(result.analysis_path.is_file())
            self.assertEqual(result.decisions[0].action, "repair")
            analysis_payload = json.loads(result.analysis_path.read_text(encoding="utf-8"))
            self.assertEqual(analysis_payload["execution_status"], "failed")

    def test_cli_passes_command_and_metric_options_to_the_application(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthesis_file = _write_synthesis(root)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main(
                    [
                        "research-experiment",
                        "--topic",
                        "reliable agents",
                        "--synthesis-file",
                        str(synthesis_file),
                        "--output-root",
                        str(root / "runs"),
                        "--cwd",
                        str(root),
                        "--primary-metric",
                        "accuracy",
                        "--metric-direction",
                        "accuracy=higher",
                        "--command",
                        sys.executable,
                        "-c",
                        "print('accuracy: 0.75')",
                    ]
                )

            self.assertIn("Status: completed", output.getvalue())
            sessions = list((root / "runs").iterdir())
            self.assertEqual(len(sessions), 1)


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


if __name__ == "__main__":
    unittest.main()
