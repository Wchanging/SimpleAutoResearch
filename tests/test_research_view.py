import io
import unittest
from rich.console import Console
from simple_ar.cli.research_view import ResearchConsole


class ResearchConsoleTests(unittest.TestCase):
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
