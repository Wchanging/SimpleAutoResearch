from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from examples.capability_package_minimal import register
from simple_ar.core import ArtifactStore, CapabilityRegistry, SessionController


class MinimalCapabilityPackageTests(unittest.TestCase):
    def test_package_writes_only_to_its_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = ArtifactStore(root).write_json("input.json", {"value": 3}, kind="input")
            registry = CapabilityRegistry()
            register(registry)
            controller = SessionController.create(
                root,
                session_id="minimal-package-success",
                topic="offline capability fixture",
                registry=registry,
            )

            result, decision = controller.execute(
                "minimal-copy",
                attempt_id="attempt-001",
                inputs=(source,),
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            self.assertEqual(
                controller.store.read_json("attempts/attempt-001/result.json"),
                {"input": {"value": 3}},
            )
            self.assertFalse((root / "result.json").exists())

    def test_package_reports_missing_input_without_fake_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            register(registry)
            controller = SessionController.create(
                tmp,
                session_id="minimal-package-failure",
                topic="offline capability fixture",
                registry=registry,
            )

            result, decision = controller.execute(
                "minimal-copy",
                attempt_id="attempt-001",
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(decision.action, "repair")
            self.assertFalse((Path(tmp) / "attempts" / "attempt-001" / "result.json").exists())


if __name__ == "__main__":
    unittest.main()
