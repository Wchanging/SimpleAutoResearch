import unittest
from examples.continual_learning.run_mammoth import summarize
from simple_ar.experiment.metrics import parse_metric_lines
from simple_ar.report.projection import metric_sources_from_execution
from simple_ar.report.schema import ReportContext, ReportToolCall
from simple_ar.report.tool_gateway import ReportToolGateway


class ContinualMeasurementsTests(unittest.TestCase):
    def test_actual_matrix_and_units_exclude_unseen_task_padding(self):
        matrix, metrics = summarize([[80, 0, 0], [60, 90, 0], [50, 70, 85]])
        self.assertEqual(matrix, [[0.8], [0.6, 0.9], [0.5, 0.7, 0.85]])
        self.assertAlmostEqual(metrics["accuracy"], 2.05 / 3)
        self.assertAlmostEqual(metrics["forgetting"], 0.25)
        self.assertAlmostEqual(metrics["backward_transfer"], -0.25)

    def test_missing_measurements_are_not_zero_or_fake_success(self):
        for rows in ([], [[80], []], [[float("nan")]], [[101]]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                summarize(rows)
        self.assertNotIn("forgetting", summarize([[80]])[1])

    def test_task_measurements_reach_existing_report_tool_with_provenance(self):
        # Fixture evidence tests the data path, not actual training or LLM writing.
        _, metrics = summarize([[80], [60, 90]])
        captured = "\n".join(f"METRIC {name}={value}" for name, value in metrics.items())
        result = {"metrics": parse_metric_lines(captured)}
        sources = metric_sources_from_execution(result, artifact="attempts/experiment/results.json")
        gateway = ReportToolGateway(ReportContext(
            topic="Continual learning", report_mode="experiment", results=result, metric_sources=sources,
        ))
        response = gateway.call(ReportToolCall(tool_name="get_metric_source", arguments={
            "metric_id": "metric:candidate:accuracy_after_task_2_on_task_1",
        }))
        self.assertEqual(response.content["value"], 0.6)
        self.assertEqual(response.content["artifact"], "attempts/experiment/results.json")
        self.assertNotIn("accuracy_after_task_1_on_task_2", result["metrics"])
