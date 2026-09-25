import io
import unittest
import sys
import tempfile
from pathlib import Path

from rich.console import Console

from simple_ar.cli.research_view import ResearchConsole
from simple_ar.core.process_output import ProcessOutput
from simple_ar.experiment.execution.backend import LocalExecutionBackend, RunRequest


class ProcessOutputTests(unittest.TestCase):
    def test_real_process_retains_raw_log_and_final_unterminated_line(self):
        with tempfile.TemporaryDirectory() as directory:
            events = []
            root = Path(directory)
            result = LocalExecutionBackend(message_callback=events.append).run(RunRequest(
                command=[sys.executable, "-c",
                         "import os; os.write(2,b'\\r10%\\r100%\\nwarning\\n'); os.write(1,b'accuracy: 0.5\\nlast')"],
                cwd=root, timeout_sec=10, output_dir=root / "logs",
            ))
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.metrics["accuracy"], 0.5)
            self.assertIn("stdout: last", events)
            self.assertEqual(next((root / "logs").glob("*/stderr.log")).read_bytes(),
                             b"\r10%\r100%\nwarning\n")

    def test_fragmented_crlf_progress_and_final_tail(self):
        events = []
        output = ProcessOutput(events.append)
        for chunk in ["first\r", "\n\r10", "%\r20%\r", "30%\nlast"]:
            output.feed("stderr", chunk)
        output.finish()
        self.assertEqual([str(e) for e in events], [
            "stderr: first", "stderr: 10%", "stderr: 20%",
            "stderr: 30%", "stderr: last",
        ])
        self.assertEqual([e.transient for e in events], [False, True, True, False, False])

    def test_streams_and_regular_lines_are_not_dropped(self):
        events = []
        output = ProcessOutput(events.append)
        output.feed("stdout", "par")
        output.feed("stderr", "warning\n")
        output.feed("stdout", "tial\na\nb\nc\nd\n")
        output.finish()
        self.assertEqual([str(e) for e in events], [
            "stderr: warning", "stdout: partial", "stdout: a",
            "stdout: b", "stdout: c", "stdout: d",
        ])

    def test_redirected_console_keeps_final_progress_and_errors(self):
        stream = io.StringIO()
        view = ResearchConsole(Console(file=stream, force_terminal=False))
        output = ProcessOutput(view.message)
        with view.action("experiment"):
            output.feed("stderr", "\r10%\r20%\r100%\nerror detail\n")
            output.finish()
        text = stream.getvalue()
        self.assertNotIn("10%", text)
        self.assertNotIn("20%", text)
        self.assertIn("100%", text)
        self.assertIn("error detail", text)

    def test_terminal_reuses_one_progress_row(self):
        view = ResearchConsole(Console(file=io.StringIO(), force_terminal=True, width=80))
        output = ProcessOutput(view.message)
        with view.action("experiment"):
            output.feed("stderr", "\r10%\r20%\r30%")
            self.assertEqual(len(view._process_tasks), 1)
            self.assertEqual(len(view._progress.tasks), 2)
            output.feed("stderr", "\n")
            self.assertEqual(view._process_tasks, {})
            output.finish()
        self.assertIsNone(view._progress)
