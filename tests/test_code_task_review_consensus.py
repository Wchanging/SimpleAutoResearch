import unittest

from simple_ar.code_task.review import _corroborated_review_findings
from simple_ar.code_task.reviewing import build_review_artifact
from simple_ar.reviewing.schema import ReviewFinding


def finding(cluster: str, summary: str, *, category: str = "logic") -> ReviewFinding:
    return ReviewFinding(
        severity="warning", category=category, summary=summary,
        evidence=["code_task/patch.diff"], source=f"code-task.llm-reviewer.{cluster}",
    )


class ReviewConsensusTests(unittest.TestCase):
    def test_three_independent_matching_correctness_findings_block(self) -> None:
        rows = [
            finding("entrypoint", "New path is unreachable: no caller supplies the required argument."),
            finding("data_flow", "New path is unreachable: the active configuration omits the argument."),
            finding("metrics", "New path is unreachable: measured runs cannot enter the branch."),
        ]
        result = _corroborated_review_findings(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].severity, "blocking")
        self.assertEqual(result[0].source, "code-task.review-consensus")
        report = build_review_artifact(
            reviewer="code-task-reviewer", subject="post_apply", findings=[*rows, *result],
        )
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["summary"]["blocking_count"], 1)

    def test_single_source_unrelated_and_style_warnings_stay_advisory(self) -> None:
        self.assertEqual(_corroborated_review_findings([
            finding("entrypoint", "New path is unreachable: no caller."),
            finding("data_flow", "New path is unreachable: no caller."),
        ]), [])
        self.assertEqual(_corroborated_review_findings([
            finding("entrypoint", "New path is unreachable: no caller."),
            finding("entrypoint", "New path is unreachable: no caller."),
            finding("entrypoint", "New path is unreachable: no caller."),
        ]), [])
        self.assertEqual(_corroborated_review_findings([
            finding("entrypoint", "New path is unreachable: no caller."),
            finding("data_flow", "Argument order is wrong: values are swapped."),
            finding("metrics", "Validation command is absent: no run evidence."),
        ]), [])
        self.assertEqual(_corroborated_review_findings([
            finding(cluster, "Line wrapping is inconsistent: use the project style.", category="style")
            for cluster in ("entrypoint", "data_flow", "metrics")
        ]), [])


if __name__ == "__main__":
    unittest.main()
