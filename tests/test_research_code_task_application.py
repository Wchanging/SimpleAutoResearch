"""Historical segmented CodeTask readers; execution uses ResearchApplication."""

from pathlib import Path
import tempfile
import unittest

from tests.legacy_session_fixture import historical_session
from simple_ar.core.artifacts import read_json, write_json
from simple_ar.app.research_code_task import (
    ResearchCodeTaskSessionError,
    load_research_code_task_session_result,
)
from simple_ar.app.research_code_task_report import build_code_task_report_inputs


def code_task_history(root: Path, *, failed: bool = False):
    historical_session(root, failed=failed)
    synthesis = read_json(root / "attempts/synthesize-001/synthesis_result.json")
    write_json(root / "inputs/code_task_input.json", {
        "schema_version": "research_code_task_input.v1",
        "topic": "reliable agents", "synthesis": synthesis,
    })
    path = root / "attempts/experiment-001/attempt_manifest.json"
    attempt = read_json(path)
    attempt["inputs"] = [{
        "path": "inputs/code_task_input.json", "kind": "research_input",
        "schema": "research_code_task_input.v1",
    }]
    write_json(path, attempt)


class ResearchCodeTaskApplicationTests(unittest.TestCase):
    def test_history_and_report_projection_do_not_execute_or_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_task_history(root)
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            session = load_research_code_task_session_result(root)
            context, memory = build_code_task_report_inputs(session)
            self.assertEqual(session.status, "completed")
            self.assertEqual(session.execution["metrics"]["accuracy"], 0.75)
            self.assertTrue(any(m.name == "accuracy" and m.value == 0.75 for m in context.metric_sources))
            self.assertTrue(any(h.kind == "analysis" for h in memory.source_handles))
            after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)

    def test_failed_history_does_not_become_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_task_history(root, failed=True)
            result = load_research_code_task_session_result(root)
            self.assertEqual(result.execution["status"], "failed")
            self.assertNotEqual(result.status, "completed")

    def test_reader_rejects_missing_declared_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_task_history(root)
            (root / "inputs/code_task_input.json").unlink()
            with self.assertRaises(ResearchCodeTaskSessionError):
                load_research_code_task_session_result(root)


if __name__ == "__main__":
    unittest.main()
