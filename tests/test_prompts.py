from __future__ import annotations

import unittest

from simple_ar.research.prompts import (
    paper_note_user_prompt,
    report_user_prompt,
    research_planner_user_prompt,
    synthesize_user_prompt,
)


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
        self.assertIn("llm_code_task_toy_spam", prompt)
        self.assertIn("changed-file count", prompt)
        self.assertIn("exact metric keys", prompt)
        self.assertIn("benchmark passed after an LLM-proposed patch", prompt)
        self.assertIn("promising direction", prompt)
        self.assertIn("Retrieved Evidence Snippets", prompt)
        self.assertIn("07-run/results.json:1-3", prompt)
        self.assertIn("Available Citation Keys", prompt)
        self.assertIn("[@paper-1]", prompt)
        self.assertIn("include at least one body citation", prompt)

    def test_research_only_report_prompt_uses_survey_structure(self) -> None:
        prompt = report_user_prompt(
            topic="agent simulation",
            goal_markdown="# Goal\nReview agents.",
            problem_markdown="# Problem\nWhat themes appear?",
            search_meta_json='{"source": "openalex", "status": "ok"}',
            papers_json='[{"id": "paper-1", "title": "Known Paper"}]',
            synthesis_markdown="# Synthesis\nKnown themes.",
            hypothesis_markdown="# Hypothesis\nA later benchmark is needed.",
            experiment_plan_json="{}",
            results_json="{}",
            report_mode="research_only",
        )

        self.assertIn("## Search Scope", prompt)
        self.assertIn("## Thematic Synthesis", prompt)
        self.assertIn("## Approach Patterns", prompt)
        self.assertIn("## Open Questions", prompt)
        self.assertIn("literature-only survey-style report", prompt)
        self.assertIn("Do not include Method, Experiments, or Results sections", prompt)
        self.assertIn("fixture placeholders", prompt)

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

    def test_research_planner_prompt_requests_short_source_queries(self) -> None:
        prompt = research_planner_user_prompt(
            topic="multi-agent coding",
            problem_markdown="# Problem\nStudy coding agents.",
            seed_queries_json='["multi-agent coding agents"]',
            required_facets_json='["method", "benchmark"]',
            max_queries=6,
            max_rounds=2,
            mode="standard",
        )

        self.assertIn("title_keywords", prompt)
        self.assertIn("abstract_keywords", prompt)
        self.assertIn("arXiv", prompt)
        self.assertIn("paper title and abstract fields", prompt)
        self.assertIn("not browser questions", prompt)


if __name__ == "__main__":
    unittest.main()
