from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.core.capabilities import (
    ArtifactStore,
    CapabilityResult,
    CapabilityRegistry,
)
from simple_ar.core.pipeline import Context
from simple_ar.core.session import BudgetState, SessionController


class CapabilityBoundaryTests(unittest.TestCase):
    def test_pipeline_context_exposes_run_relative_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Context(Path(tmp) / "run", "toy topic")

            ref = ctx.artifact_store.write_text("capability/result.txt", "ok\n")

            self.assertEqual(ref.path, "capability/result.txt")
            self.assertEqual(ctx.artifact_store.read_text(ref), "ok\n")

    def test_store_writes_relative_artifact_refs_without_hash_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(Path(tmp))
            ref = store.write_text(
                "stage/result.txt",
                "done\n",
                kind="result",
                schema="result.v1",
                producer="fixture",
            )

            self.assertEqual(ref.path, "stage/result.txt")
            self.assertEqual(ref.kind, "result")
            self.assertEqual(store.read_text(ref), "done\n")
            self.assertNotIn("sha256", ref.to_dict())
            self.assertNotIn("hash", ref.to_dict())

    def test_store_rejects_paths_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(Path(tmp))

            with self.assertRaises(ValueError):
                store.ref("../outside.json")

            with self.assertRaises(ValueError):
                store.new_attempt("../outside")

            with self.assertRaises(ValueError):
                store.new_attempt("nested/attempt")

    def test_missing_artifact_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(Path(tmp))
            ref = store.ref("missing.json", status="missing")

            self.assertFalse(store.exists(ref))
            with self.assertRaises(FileNotFoundError):
                store.require(ref)

    def test_capability_result_has_small_common_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(Path(tmp))
            ref = store.write_json("result.json", {"status": "ok"})
            manifest_ref = store.write_manifest((ref,), metadata={"profile": "fixture"})
            manifest_items = store.read_manifest()
            result = CapabilityResult(
                status="completed",
                artifacts=(ref,),
                diagnostics=("fixture completed",),
                usage={"calls": 0},
                provenance={"producer": "fixture"},
            )

            payload = result.to_dict()

            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["artifacts"][0]["path"], "result.json")
            self.assertEqual(payload["usage"]["calls"], 0)
            self.assertEqual(manifest_ref.kind, "manifest")
            self.assertEqual(manifest_items[0].path, "result.json")

    def test_attempt_isolated_from_parent_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = ArtifactStore(Path(tmp))
            parent.write_text("result.txt", "parent\n")

            child, manifest = parent.new_attempt(
                "attempt-001",
                parent_attempt="attempt-000",
                trigger="repair",
                profile="experiment",
                inputs=(parent.ref("result.txt", kind="result"),),
            )
            child.write_text("result.txt", "child\n")

            loaded = child.read_attempt_manifest()

            self.assertEqual(loaded.attempt_id, "attempt-001")
            self.assertEqual(loaded.parent_attempt, "attempt-000")
            self.assertEqual(loaded.trigger, "repair")
            self.assertEqual(loaded.inputs[0].path, "result.txt")
            self.assertEqual(parent.read_text("result.txt"), "parent\n")
            self.assertEqual(child.read_text("result.txt"), "child\n")
            self.assertTrue((Path(tmp) / "attempts" / "attempt-001" / "attempt_manifest.json").is_file())

    def test_registry_runs_explicit_handler_and_lists_names(self) -> None:
        registry = CapabilityRegistry()

        def fixture(value: str) -> CapabilityResult:
            return CapabilityResult(
                status="completed",
                diagnostics=(value,),
                provenance={"producer": "fixture"},
            )

        registry.register("  fixture  ", fixture)

        result = registry.run("fixture", "ok")

        self.assertEqual(registry.names(), ("fixture",))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.diagnostics, ("ok",))

    def test_registry_rejects_duplicates_unknown_names_and_bad_results(self) -> None:
        registry = CapabilityRegistry()

        def fixture() -> CapabilityResult:
            return CapabilityResult(status="completed")

        registry.register("fixture", fixture)
        with self.assertRaises(ValueError):
            registry.register("fixture", fixture)
        with self.assertRaises(KeyError):
            registry.resolve("missing")

        registry.register("bad", lambda: "not a result")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            registry.run("bad")

    def test_session_controller_persists_successful_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()

            def fixture() -> CapabilityResult:
                store = ArtifactStore(Path(tmp) / "attempts" / "attempt-001")
                ref = store.write_json("result.json", {"ok": True}, kind="result")
                return CapabilityResult(status="completed", artifacts=(ref,))

            registry.register("fixture", fixture)
            controller = SessionController.create(
                tmp,
                session_id="session-001",
                topic="offline fixture",
                registry=registry,
            )

            result, decision = controller.execute("fixture", attempt_id="attempt-001")
            loaded = SessionController.load(tmp, registry=registry)

            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            self.assertEqual(loaded.manifest.status, "completed")
            self.assertEqual(loaded.manifest.decisions[0].attempt_id, "attempt-001")
            self.assertEqual(loaded.manifest.budget.attempts, 1)

    def test_session_controller_blocks_repeated_no_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register(
                "broken",
                lambda: CapabilityResult(status="failed", diagnostics=("same failure",)),
            )
            controller = SessionController.create(
                tmp,
                session_id="session-002",
                topic="offline failure",
                registry=registry,
                budget=BudgetState(max_attempts=5, max_no_progress=2),
            )

            first, first_decision = controller.execute(
                "broken",
                attempt_id="attempt-001",
                trigger="initial",
            )
            second, second_decision = controller.execute(
                "broken",
                attempt_id="attempt-002",
                trigger="repair",
            )

            self.assertEqual(first.status, "failed")
            self.assertEqual(first_decision.action, "repair")
            self.assertEqual(second_decision.action, "block")
            self.assertEqual(controller.manifest.status, "blocked")
            self.assertEqual(controller.manifest.budget.no_progress, 2)
            self.assertEqual(controller.manifest.decisions[1].reason, "same failure Session budget exhausted.")
            self.assertTrue((Path(tmp) / "attempts" / "attempt-001" / "attempt_manifest.json").is_file())
            self.assertTrue((Path(tmp) / "attempts" / "attempt-002" / "attempt_manifest.json").is_file())

    def test_session_controller_does_not_implicitly_retry_or_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("partial", lambda: CapabilityResult(status="partial"))
            controller = SessionController.create(
                tmp,
                session_id="session-003",
                topic="offline partial",
                registry=registry,
                budget=BudgetState(max_attempts=2, max_no_progress=2),
            )

            controller.execute("partial", attempt_id="attempt-001", trigger="initial")

            self.assertEqual(controller.manifest.status, "running")
            with self.assertRaises(FileExistsError):
                controller.execute("partial", attempt_id="attempt-001", trigger="revise")
            self.assertEqual(controller.manifest.budget.attempts, 1)


if __name__ == "__main__":
    unittest.main()
