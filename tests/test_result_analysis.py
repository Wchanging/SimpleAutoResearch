from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.result_analysis import AnalysisContext, run_result_analysis


class FakeAnalysisClient:
    def __init__(self, payload: dict | str) -> None:
        self.payload = payload

    def ask(self, system: str, user: str, *, label: str = "") -> str:
        self.system = system
        self.user = user
        self.label = label
        if isinstance(self.payload, str):
            return self.payload
        import json

        return json.dumps(self.payload)

    def ask_json(self, system: str, user: str, *, label: str = "") -> dict:
        self.system = system
        self.user = user
        self.label = label
        if isinstance(self.payload, dict):
            return self.payload
        raise ValueError("not json")


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
        context = AnalysisContext(
            task_id="T3",
            metrics={"score": 1.0},
            criteria=[{"id": "c1", "task_category": "Code Execution", "weight": 1.0}],
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            run_result_analysis(context, output_dir=out)

            self.assertTrue((out / "analysis_context.json").is_file())
            self.assertTrue((out / "metric_summary.json").is_file())
            self.assertTrue((out / "rubric_coverage.json").is_file())
            self.assertTrue((out / "claims.json").is_file())
            self.assertTrue((out / "analysis_report.md").is_file())
            self.assertTrue((out / "analysis_audit.json").is_file())

    def test_invalid_llm_json_writes_raw_response_for_diagnosis(self) -> None:
        context = AnalysisContext(task_id="T4", metrics={"score": 1.0})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with self.assertRaises(ValueError):
                run_result_analysis(
                    context,
                    output_dir=out,
                    client=FakeAnalysisClient("# Markdown response\nnot json"),
                    use_llm=True,
                )

            self.assertTrue((out / "analysis_prompt.txt").is_file())
            self.assertTrue((out / "analysis_raw_response.txt").is_file())
            self.assertIn("Markdown response", (out / "analysis_raw_response.txt").read_text(encoding="utf-8"))

    def test_refuted_claim_and_dict_metric_refs_are_normalized(self) -> None:
        context = AnalysisContext(
            task_id="T5",
            expected_metrics=[{"name": "rmse", "direction": "minimize"}],
            metrics={"rmse": 2.0},
            criteria=[{"id": "r1", "task_category": "Result Analysis", "weight": 1.0}],
        )
        client = FakeAnalysisClient(
            {
                "summary": {
                    "method": "Compared two conditions.",
                    "results": "The hypothesis was refuted by RMSE.",
                    "limitations": "Small run.",
                    "reproduction_notes": "Run the provided command.",
                },
                "rubric_coverage": [
                    {
                        "category": "Result Analysis",
                        "leaf_count": 1,
                        "verdict": "supported",
                        "evidence": "Hypothesis is discussed.",
                    }
                ],
                "claims": [
                    {
                        "claim_id": "H1",
                        "claim": "Bagging beats boosting.",
                        "verdict": "refuted",
                        "metric_refs": [
                            {
                                "dataset_name": "d1",
                                "condition_name": "bagging",
                                "metric": "rmse",
                                "mean": 2.0,
                            }
                        ],
                        "evidence": ["rmse:d1:bagging was worse."],
                    }
                ],
                "analysis_audit": {},
            }
        )

        result = run_result_analysis(context, client=client, use_llm=True)

        self.assertEqual(result.claims[0].verdict, "unsupported")
        self.assertEqual(result.claims[0].metric_refs, ["rmse:d1:bagging"])
        self.assertIn("Rubric Coverage", result.readme_markdown)
        self.assertIn("Result Analysis", result.readme_markdown)

    def test_llm_rubric_coverage_keeps_deterministic_leaf_counts(self) -> None:
        context = AnalysisContext(
            task_id="T6",
            metrics={"score": 1.0},
            criteria=[
                {"id": "c1", "task_category": "Code Development", "weight": 2.0},
                {"id": "c2", "task_category": "Code Development", "weight": 3.0},
                {"id": "c3", "task_category": "Result Analysis", "weight": 5.0},
            ],
        )
        client = FakeAnalysisClient(
            {
                "summary": {
                    "method": "Implemented and analyzed.",
                    "results": "Metrics are present.",
                    "limitations": "Limited task.",
                    "reproduction_notes": "Run command.",
                },
                "rubric_coverage": [
                    {
                        "category": "Code Development",
                        "verdict": "partially_supported",
                        "evidence": "Implementation artifacts are present.",
                    }
                ],
                "claims": [],
                "analysis_audit": {},
            }
        )

        result = run_result_analysis(context, client=client, use_llm=True)

        coverage = {row["category"]: row for row in result.rubric_coverage}
        self.assertEqual(coverage["Code Development"]["leaf_count"], 2)
        self.assertEqual(coverage["Result Analysis"]["leaf_count"], 1)
        self.assertIn("| Code Development | 2 |", result.readme_markdown)

    def test_extracts_aggregate_rows_and_dict_hypothesis_verdicts(self) -> None:
        context = AnalysisContext(
            task_id="T7",
            hypotheses=[
                {"id": "H1", "statement": "Bagging should beat boosting on high-noise data."},
            ],
            expected_metrics=[{"name": "rmse", "direction": "minimize"}],
            metrics={"rmse": 2.0},
            project_results={
                "aggregates": [
                    {
                        "dataset": "friedman_high_noise",
                        "condition": "bagging",
                        "rmse_mean": 2.0,
                        "rmse_std": 0.1,
                        "n_seeds": 5,
                    },
                    {
                        "dataset": "friedman_high_noise",
                        "condition": "boosting",
                        "rmse_mean": 2.5,
                        "rmse_std": 0.2,
                        "n_seeds": 5,
                    },
                ],
                "hypothesis_verdicts": {
                    "H1": {
                        "supported": True,
                        "evidence": "Bagging has lower RMSE than boosting.",
                    }
                },
            },
        )

        result = run_result_analysis(context)

        rows = result.metric_summary["result_tables"]["primary_metric_rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["evidence_id"], "rmse:friedman_high_noise:bagging")
        self.assertEqual(result.claims[0].claim_id, "H1")
        self.assertEqual(result.claims[0].verdict, "supported")
        self.assertIn("Bagging should beat boosting", result.claims[0].claim)

    def test_extracts_summary_mean_std_rows(self) -> None:
        context = AnalysisContext(
            task_id="T8",
            expected_metrics=[{"name": "balanced_accuracy", "direction": "maximize"}],
            metrics={"balanced_accuracy": 0.8},
            project_results={
                "summaries": [
                    {
                        "dataset": "d1",
                        "condition": "smote",
                        "mean": {"balanced_accuracy": 0.8, "f1": 0.7},
                        "std": {"balanced_accuracy": 0.05, "f1": 0.04},
                        "n_seeds": 5,
                    }
                ]
            },
        )

        result = run_result_analysis(context)

        rows = result.metric_summary["result_tables"]["primary_metric_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dataset"], "d1")
        self.assertEqual(rows[0]["condition"], "smote")
        self.assertEqual(rows[0]["mean"], 0.8)
        self.assertEqual(rows[0]["std"], 0.05)
        self.assertEqual(rows[0]["count"], 5)


if __name__ == "__main__":
    unittest.main()
