from __future__ import annotations

import unittest

from simple_ar.prompts import report_user_prompt


class PromptTests(unittest.TestCase):
    def test_report_prompt_enforces_evidence_and_citation_boundaries(self) -> None:
        prompt = report_user_prompt(
            topic="agent simulation",
            goal_markdown="# Goal\nStudy agents.",
            problem_markdown="# Problem\nHow should agents be evaluated?",
            search_meta_json='{"source": "arxiv", "status": "ok"}',
            papers_json='[{"id": "paper-1", "title": "Known Paper"}]',
            synthesis_markdown="# Synthesis\nKnown evidence.",
            hypothesis_markdown="# Hypothesis\nA testable claim.",
            experiment_plan_json='{"template": "toy_text_classification"}',
            results_json='{"metrics": {"accuracy": 0.75}}',
        )

        self.assertIn("report_markdown", prompt)
        self.assertIn("Do not write a References section", prompt)
        self.assertIn("[@paper_id]", prompt)
        self.assertIn("Do not report p-values", prompt)
        self.assertIn("Never invent", prompt)


if __name__ == "__main__":
    unittest.main()
