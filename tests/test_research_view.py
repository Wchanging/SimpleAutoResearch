import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from rich.console import Console
from simple_ar.cli.research_view import ResearchConsole


class ResearchConsoleTests(unittest.TestCase):
    def test_decision_is_visible_once_and_updates_without_control_codes(self):
        stream = io.StringIO()
        display = ResearchConsole(Console(file=stream, force_terminal=False, width=120))
        decision = {"action": "supplement", "research_iteration": 0,
                    "remaining_authorized_rounds": 1, "decision_reason": "Check [accuracy] uncertainty."}
        view = SimpleNamespace(status="running", next_action="supplement_baseline:1",
                               status_reason="", work_plan={"research_decision": decision})
        display.state(view)
        display.state(view)
        self.assertEqual(stream.getvalue().count("Check [accuracy] uncertainty."), 1)
        decision.update(action="request_input", decision_reason="Confirm the comparison.")
        display.state(view)
        self.assertIn("Action: request_input", stream.getvalue())
        self.assertIn("remaining authorized rounds: 1", stream.getvalue())
        self.assertNotIn("\x1b", stream.getvalue())

    def test_real_process_streams_without_changing_captured_output(self):
        import sys
        import tempfile
        from pathlib import Path
        from simple_ar.experiment.execution.backend import LocalExecutionBackend, RunRequest
        messages = []
        with tempfile.TemporaryDirectory() as directory:
            result = LocalExecutionBackend(message_callback=messages.append).run(
                RunRequest([sys.executable, "-c", "print('epoch 1/1'); print('accuracy: 0.5')"],
                           Path(directory), 10))
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.metrics["accuracy"], 0.5)
        self.assertIn("epoch 1/1", result.stdout)
        self.assertTrue(any("epoch 1/1" in message for message in messages))

    def test_redirected_progress_keeps_messages_without_control_codes(self):
        stream = io.StringIO()
        display = ResearchConsole(Console(file=stream, force_terminal=False, width=100))
        with display.action("implement"):
            display.message("Generating edits for [model].py")
        output = stream.getvalue()
        self.assertIn("Generating edits for [model].py", output)
        self.assertIn("Action returned", output)
        self.assertNotIn("\x1b", output)

    def test_error_is_not_swallowed_or_rendered_as_completion(self):
        stream = io.StringIO()
        display = ResearchConsole(Console(file=stream, force_terminal=False))
        with self.assertRaisesRegex(ValueError, "provider unavailable"):
            with display.action("read"):
                raise ValueError("provider unavailable")
        self.assertIn("Interrupted/failed", stream.getvalue())
        self.assertNotIn("Action returned", stream.getvalue())

    def test_finish_navigates_latest_plan_artifacts_and_preserves_history(self):
        stream = io.StringIO()
        display = ResearchConsole(Console(file=stream, force_terminal=False, width=160))
        refs = {
            name: SimpleNamespace(path=f"attempts/{name}.json")
            for name in (
                "summary", "baseline", "implementation", "implementation_r1",
                "baseline_supplement_1", "experiment", "experiment_revision_1",
                "analysis", "analysis_r1", "decision",
            )
        }
        steps = [
            {"capability": "experiment", "action": "baseline", "state_name": "baseline"},
            {"capability": "implement", "action": "implement", "state_name": "implementation"},
            {"capability": "experiment", "action": "experiment", "state_name": "experiment"},
            {"capability": "analysis", "action": "analysis", "state_name": "analysis"},
            {"capability": "implement", "action": "implement", "state_name": "implementation_r1"},
            {"capability": "experiment", "action": "supplement_baseline:1", "state_name": "baseline_supplement_1"},
            {"capability": "experiment", "action": "experiment_revision_1", "state_name": "experiment_revision_1"},
            {"capability": "analysis", "action": "analysis", "state_name": "analysis_r1"},
        ]
        view = SimpleNamespace(
            session_root=Path("session"), status="completed", status_reason="", next_action=None,
            state_refs=refs, attempts=(), work_plan={"accepted_plan": {"steps": steps}},
        )

        display.finish(view)

        output = stream.getvalue()
        self.assertIn("implementation (initial)", output)
        self.assertIn("implementation (latest: implementation_r1)", output)
        self.assertIn("experiment (initial)", output)
        self.assertIn("experiment (latest: experiment_revision_1)", output)
        self.assertIn("baseline (latest: baseline_supplement_1)", output)
        self.assertIn("analysis (initial)", output)
        self.assertIn("analysis (latest: analysis_r1)", output)
        self.assertIn("decision (latest)", output)
        self.assertIn("session\\attempts\\implementation.json", output)
        self.assertIn("session\\attempts\\implementation_r1.json", output)
