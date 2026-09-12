from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from simple_ar.core import (
    CapabilityContext,
    CapabilityRegistry,
    CapabilityResult,
    SessionBusyError,
    SessionController,
    SessionFileLock,
)


class SessionLockTests(unittest.TestCase):
    def test_lock_is_non_blocking_and_releases_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".session.lock"
            first = SessionFileLock(path)
            second = SessionFileLock(path)

            with first:
                with self.assertRaises(SessionBusyError):
                    second.acquire()

            owner = SessionFileLock.read_owner(path)
            self.assertIsNotNone(owner)
            assert owner is not None
            self.assertEqual(owner["pid"], os.getpid())
            second.acquire()
            second.release()

    def test_controller_holds_lock_during_physical_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            controller = SessionController.create(
                tmp,
                session_id="lock-session",
                topic="lock boundary",
                registry=registry,
            )
            other = SessionController.load(tmp, registry=registry)

            def handler(**_: object) -> CapabilityResult:
                with self.assertRaises(SessionBusyError):
                    other.pause("must not mutate while another attempt runs")
                return CapabilityResult(status="completed")

            registry.register("probe", handler)
            result = controller.execute_attempt("probe", attempt_id="probe-001")

            self.assertEqual(result.status, "completed")
            self.assertEqual(controller.manifest.status, "running")
            # A failed lock acquisition must not corrupt the re-entrant depth;
            # otherwise this controller could skip the OS lock on its next
            # mutation.
            self.assertEqual(other._lock_depth, 0)

    def test_result_on_disk_is_reconciled_without_running_handler_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls: list[str] = []
            registry = CapabilityRegistry()

            def handler(*, context: CapabilityContext) -> CapabilityResult:
                calls.append(context.attempt.attempt_id)
                return CapabilityResult(status="completed")

            registry.register("probe", handler)
            controller = SessionController.create(
                tmp,
                session_id="reconcile-session",
                topic="result recovery",
                registry=registry,
            )
            attempt_store, attempt = controller.store.new_attempt(
                "probe-0001",
                capability="probe",
            )
            running_attempt = replace(attempt, status="running")
            attempt_store.write_attempt_manifest(running_attempt)
            controller.manifest.status = "running"
            controller.manifest.current_attempt = "probe-0001"
            controller.save()
            attempt_store.write_capability_result(CapabilityResult(status="completed"))

            resumed = SessionController.load(tmp, registry=registry)
            result = resumed.reconcile_attempt()

            self.assertEqual(result.status, "completed")
            self.assertEqual(calls, [])
            self.assertEqual(resumed.manifest.budget.attempts, 1)
            self.assertEqual(resumed.manifest.budget.recorded_attempts, ["probe-0001"])
            self.assertEqual(resumed.list_attempts()[0].status, "completed")
            resumed.reconcile_attempt("probe-0001")
            self.assertEqual(resumed.manifest.budget.attempts, 1)

    def test_stale_writer_cannot_reset_persisted_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("probe", lambda **_: CapabilityResult(status="completed"))
            first = SessionController.create(tmp, session_id="test", topic="test", registry=registry)
            stale = SessionController.load(tmp, registry=registry)
            first.execute_attempt("probe")
            with self.assertRaisesRegex(RuntimeError, "reload"):
                stale.pause("stale writer")
            reloaded = SessionController.load(tmp, registry=registry)
            reloaded.pause("fresh writer")
            self.assertEqual(reloaded.manifest.budget.attempts, 1)


if __name__ == "__main__":
    unittest.main()
