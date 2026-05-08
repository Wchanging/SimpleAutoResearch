from __future__ import annotations

import unittest

from simple_ar.prompts import paper_note_user_prompt, report_user_prompt, synthesize_user_prompt


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
            evidence_snippets="[ev-1 | 07-run/results.json:1-3 | query=accuracy]\naccuracy: 0.75",
            citation_instruction="- [@paper-1] TITLE: \"Known Paper\"",
        )

        self.assertIn("report_markdown", prompt)
        self.assertIn("Do not write a References section", prompt)
        self.assertIn("[@paper_id]", prompt)
        self.assertIn("Do not report p-values", prompt)
        self.assertIn("Never invent", prompt)
        self.assertIn("Retrieved Evidence Snippets", prompt)
        self.assertIn("07-run/results.json:1-3", prompt)
        self.assertIn("Available Citation Keys", prompt)
        self.assertIn("[@paper-1]", prompt)

    def test_read_and_synthesis_prompts_accept_source_labelled_evidence(self) -> None:
        evidence = "[ev-1 | 02-search/papers.jsonl:1-1 | query=metadata]\nKnown paper row"

        read_prompt = paper_note_user_prompt('{"id": "paper-1"}', evidence_snippets=evidence)
        synth_prompt = synthesize_user_prompt(
            "# Notes\nKnown evidence.",
            '[{"paper_id": "paper-1"}]',
            evidence_snippets=evidence,
        )

        self.assertIn("Retrieved Evidence Snippets", read_prompt)
        self.assertIn("02-search/papers.jsonl:1-1", read_prompt)
        self.assertIn("Retrieved Evidence Snippets", synth_prompt)
        self.assertIn("trace", synth_prompt)


if __name__ == "__main__":
    unittest.main()
