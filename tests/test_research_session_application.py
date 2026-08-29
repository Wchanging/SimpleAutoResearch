from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from simple_ar.app.research_brief import ResearchBriefSessionRequest
from simple_ar.app.research_session import (
    ResearchSessionRequest,
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

            self.assertEqual(result.status, "completed")
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
            self.assertEqual(manifest["status"], "completed")


if __name__ == "__main__":
    unittest.main()
