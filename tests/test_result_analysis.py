from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.result_analysis import AnalysisContext, run_result_analysis


class FakeAnalysisClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def ask_json(self, system: str, user: str, *, label: str = "") -> dict:
        self.system = system
        self.user = user
        self.label = label
        return self.payload


class ResultAnalysisTests(unittest.TestCase):
    def test_metric_summary_marks_missing_and_all_zero(self) -> None:
        context = AnalysisContext(
            task_id="T1",
            expected_metrics=[{"name": "accuracy", "direction": "maximize"}, {"name": "f1", "direction": "maximize"}],
            metrics={"accuracy": 0.0},
        )

        result = run_result_analysis(context)

        self.assertIn("f1", result.audit.missing_required_metrics)
        self.assertIn("all comparable metrics are zero", result.audit.weak_metric_signals)

    def test_llm_supported_claim_without_evidence_is_downgraded(self) -> None:
        context = AnalysisContext(
            task_id="T2",
            expected_metrics=[{"name": "rmse", "direction": "minimize"}],
            metrics={"rmse": 0.42},
        )
        client = FakeAnalysisClient(
            {
                "readme_markdown": "# Result\nLooks good.",
                "claims": [
                    {
                        "claim_id": "c1",
                        "claim": "The experiment is successful.",
                        "verdict": "supported",
                        "confidence": "high",
                    }
                ],
                "analysis_audit": {},
            }
        )

        result = run_result_analysis(context, client=client, use_llm=True)

        self.assertEqual(result.claims[0].verdict, "not_evaluated")
        self.assertIn("c1", result.audit.downgraded_claims)

    def test_writes_analysis_artifacts(self) -> None:
        context = AnalysisContext(task_id="T3", metrics={"score": 1.0})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            run_result_analysis(context, output_dir=out)

            self.assertTrue((out / "analysis_context.json").is_file())
            self.assertTrue((out / "metric_summary.json").is_file())
            self.assertTrue((out / "claims.json").is_file())
            self.assertTrue((out / "analysis_report.md").is_file())
            self.assertTrue((out / "analysis_audit.json").is_file())


if __name__ == "__main__":
    unittest.main()
