from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from simple_ar.core.process import ProcessSpec, run_process
from simple_ar.core.process import settle_process_record
from simple_ar.core.budget import BudgetLedger, BudgetExceededError, BudgetConflictError
from simple_ar.experiment.execution.backend import LocalExecutionBackend, RunRequest
from simple_ar.code_task import initialize_code_task, execute_code_task
from simple_ar.code_task.execution.runner import _run_command


class ProcessControlTests(unittest.TestCase):
    def test_experiment_and_code_task_share_one_physical_process_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = BudgetLedger({"process_invocations": 2, "process_wall_seconds": 20}, storage_path=root / "ledger.json")
            first = LocalExecutionBackend(budget_ledger=ledger).run(RunRequest(
                [sys.executable, "-c", "print('score: 1')"], root, 5,
                output_dir=root / "experiment", session_id="s", attempt_id="experiment-1",
            ))
            project = root / "project"
            project.mkdir()
            (project / "main.py").write_text("print('score: 2')\n", encoding="utf-8")
            task = root / "task.md"
            task.write_text("Measure the baseline.", encoding="utf-8")
            initialize_code_task(run_dir=root / "code-task", code_root=project,
                                 task_file=task, benchmark_command="python main.py")
            second = execute_code_task(root / "code-task", to_step="baseline", use_llm=False, timeout_sec=5,
                                       budget_ledger=ledger, session_id="s", attempt_id="baseline-2")
            self.assertEqual(second.stop_reason, "stop_point")
            self.assertEqual(ledger.remaining("process_invocations"), 0)
            self.assertEqual({entry.attempt_id for entry in ledger.entries}, {"experiment-1", "baseline-2"})
            self.assertTrue(all(entry.actual_source == "executor_wall_clock" for entry in ledger.entries))
            execute_code_task(root / "code-task", to_step="baseline", use_llm=False, timeout_sec=5,
                              budget_ledger=ledger, session_id="s", attempt_id="baseline-2")
            self.assertEqual(len(ledger.entries), 2)
            # Reconciliation repeats accounting, never the actual process.
            restored = BudgetLedger.load(root / "ledger.json")
            settle_process_record(first.process_record, restored)
            self.assertEqual(restored.remaining("process_invocations"), 0)
            interrupted = BudgetLedger({"process_invocations": 1, "process_wall_seconds": 5})
            interrupted.reserve(f"process-{first.process_record['invocation_id']}",
                                {"process_invocations": 1, "process_wall_seconds": 5})
            settle_process_record(first.process_record, interrupted)
            self.assertEqual(interrupted.entries[0].status, "settled")
            with self.assertRaises(BudgetExceededError):
                LocalExecutionBackend(budget_ledger=restored).run(RunRequest(
                    [sys.executable, "-c", "from pathlib import Path; Path('unexpected').touch()"],
                    root, 5, output_dir=root / "blocked",
                ))
            self.assertFalse((root / "unexpected").exists())

    def test_launch_failure_releases_budget_but_timeout_is_measured(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = BudgetLedger({"process_invocations": 1, "process_wall_seconds": 5})
            with self.assertRaises(OSError):
                run_process(ProcessSpec([str(root / "missing-executable")], root, 1,
                                        output_dir=root / "failed"), budget_ledger=ledger)
            self.assertEqual(ledger.remaining("process_invocations"), 1)
            spec = ProcessSpec([sys.executable, "-c", "import time; time.sleep(3)"], root, 0.5,
                               output_dir=root / "timeout")
            result = run_process(spec, budget_ledger=ledger)
            self.assertEqual(result.stop_reason, "timeout")
            self.assertEqual(ledger.remaining("process_invocations"), 0)
            with self.assertRaises(BudgetConflictError):
                run_process(spec, budget_ledger=ledger)

    def test_large_output_is_drained_with_bounded_logs_and_metric_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_process(ProcessSpec(
                [sys.executable, "-u", "-c", "import os; os.write(1, b'x'*300000); os.write(1, '\\n准确率: 0.75\\n'.encode()); os.write(2, b'warning\\n'); raise SystemExit(3)"],
                root, 5, output_dir=root / "process", log_bytes=4096, tail_bytes=128,
            ))
            self.assertEqual(result.returncode, 3)
            self.assertIn("准确率: 0.75", result.stdout)
            self.assertLessEqual(len(result.stdout.encode()), 128)
            self.assertEqual((root / "process/stdout.log").stat().st_size, 4096)
            self.assertGreater(result.record["streams"]["stdout"]["tail_bytes_discarded"], 290000)
            stored = json.loads((root / "process/invocation.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["returncode"], 3)

    def test_timeout_preserves_output_and_records_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_process(ProcessSpec(
                [sys.executable, "-u", "-c", "import time; print('started'); time.sleep(3)"],
                Path(tmp), 0.5,
            ))
            self.assertEqual(result.stop_reason, "timeout")
            self.assertIn("started", result.stdout)

    def test_callback_cancellation_and_error_end_the_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = ProcessSpec(
                [sys.executable, "-u", "-c", "import time; print('ready'); time.sleep(3)"],
                Path(tmp), 5,
            )
            cancel = threading.Event()
            result = run_process(spec, output_callback=lambda *_: cancel.set(), cancel=cancel)
            self.assertEqual(result.stop_reason, "cancelled")

            def broken_display(*_):
                raise RuntimeError("display failed")

            with self.assertRaisesRegex(RuntimeError, "display failed"):
                run_process(spec, output_callback=broken_display)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object integration")
    def test_timeout_terminates_a_real_child_process(self):
        # Retain a real process handle before termination to avoid PID reuse and
        # verify OS-observed exit, rather than merely checking a mocked kill call.
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handles = []

        def observe(name, chunk):
            if name == "stdout" and chunk.strip():
                handles.append(kernel.OpenProcess(0x100000, False, int(chunk.strip())))

        with tempfile.TemporaryDirectory() as tmp:
            code = "import subprocess, sys, time; p=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)']); print(p.pid, flush=True); time.sleep(3)"
            try:
                result = run_process(ProcessSpec([sys.executable, "-u", "-c", code], Path(tmp), 0.75), output_callback=observe)
                self.assertEqual(result.stop_reason, "timeout")
                self.assertTrue(handles and handles[0])
                self.assertEqual(kernel.WaitForSingleObject(handles[0], 2000), 0)
            finally:
                for handle in handles:
                    kernel.CloseHandle(handle)

    def test_code_task_progress_adapter_retains_final_progress_and_metrics(self):
        messages = []
        with tempfile.TemporaryDirectory() as tmp:
            code = "import os; os.write(1, b'progress 1/2\\rprogress 2/2\\r\\naccuracy: 0.75\\n')"
            result = _run_command(
                [sys.executable, "-u", "-c", code], cwd=Path(tmp), timeout_sec=5,
                stream_output="auto", output_callback=lambda name, text: messages.append(text),
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("progress 2/2", messages)
            self.assertIn("accuracy: 0.75", result.stdout)


if __name__ == "__main__":
    unittest.main()
