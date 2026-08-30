from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from simple_ar.app.research_brief import ResearchBriefSessionRequest
from simple_ar.app.research_session import (
    ResearchSessionRequest,
    ResearchSessionError,
    load_research_session_result,
    run_research_session,
)


class ResearchSessionApplicationTests(unittest.TestCase):
    def test_literature_to_execution_and_analysis_stays_in_one_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "reliable_agents.md"
            paper.write_text(
                "# Method\n\nValidation improves reliable agent behavior.\n\n"
                "# Results\n\nThe fixture reports accuracy: 0.75.\n",
                encoding="utf-8",
            )

            result = run_research_session(
                ResearchSessionRequest(
                    brief=ResearchBriefSessionRequest(
                        topic="reliable agents",
                        session_root=root / "session",
                        local_documents=(paper,),
                        max_results=2,
                        max_chunks=20,
                    ),
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

            self.assertEqual(result.status, "ready_for_report")
            self.assertEqual(result.next_capability, "report")
            self.assertEqual(result.execution["status"], "passed")
            self.assertEqual(result.analysis.status, "passed")
            self.assertEqual(
                [attempt.capability for attempt in result.attempts],
                [
                    "analysis",
                    "research_brief",
                    "document_ingest",
                    "experiment",
                    "plan",
                    "search",
                ],
            )
            self.assertEqual(
                [decision.action for decision in result.decisions],
                ["accept"] * 6,
            )
            self.assertTrue(
                (root / "session" / "attempts" / "brief-001" / "research_brief.json").is_file()
            )
            self.assertTrue(str(result.execution_ref.path).startswith("attempts/"))
            self.assertTrue(str(result.analysis_ref.path).startswith("attempts/"))
            manifest = json.loads(
                (root / "session" / "session_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["profile"], "full_research")
            self.assertEqual(manifest["status"], "running")

            restored = load_research_session_result(root / "session")
            self.assertEqual(restored.status, result.status)
            self.assertEqual(restored.brief_ref, result.brief_ref)
            self.assertEqual(restored.execution_ref, result.execution_ref)
            self.assertEqual(restored.analysis_ref, result.analysis_ref)
            self.assertEqual(restored.execution, result.execution)
            self.assertEqual(restored.analysis, result.analysis)
            self.assertEqual(restored.attempts, result.attempts)
            self.assertEqual(restored.decisions, result.decisions)

    def test_restore_rejects_a_missing_typed_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "missing-session"
            with self.assertRaises(ResearchSessionError):
                load_research_session_result(root)


if __name__ == "__main__":
    unittest.main()
