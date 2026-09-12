from __future__ import annotations

import json
import tempfile
import unittest

from legacy_session_fixture import historical_session
from pathlib import Path

from simple_ar.app.research_session import (
    ResearchSessionError,
    load_research_session_result,
)


class ResearchSessionApplicationTests(unittest.TestCase):
    def test_historical_outputs_restore_without_executing_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = historical_session(root / "session")

            self.assertEqual(result.status, "ready_for_report")
            self.assertEqual(result.next_capability, "report")
            self.assertEqual(result.execution["status"], "passed")
            self.assertEqual(result.analysis.status, "passed")

            self.assertEqual(
                [attempt.capability for attempt in result.attempts],
                [
                    "analysis",
                    "research_design",
                    "document_ingest",
                    "experiment",
                    "plan",
                    "read",
                    "search",
                    "synthesize",
                ],
            )
            self.assertEqual(
                [decision.action for decision in result.decisions],
                ["accept"],
            )
            self.assertTrue(
                (root / "session" / "attempts" / "read-001" / "read_result.json").is_file()
            )
            self.assertTrue(
                (root / "session" / "attempts" / "synthesize-001" / "synthesis_result.json").is_file()
            )
            self.assertTrue(
                (root / "session" / "attempts" / "design-001" / "research_design.json").is_file()
            )
            self.assertIsNotNone(result.design)
            self.assertIsNotNone(result.design_ref)
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
            self.assertEqual(restored.design_ref, result.design_ref)
            self.assertEqual(restored.design, result.design)
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

    def test_failed_execution_reads_recorded_handoff_without_retrying(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = historical_session(root / "session", failed=True)

            self.assertNotEqual(result.status, "ready_for_report")
            self.assertEqual(result.next_capability, "experiment")
            self.assertEqual(
                [attempt.attempt_id for attempt in result.attempts].count(
                    "experiment-001"
                ),
                1,
            )



if __name__ == "__main__":
    unittest.main()
