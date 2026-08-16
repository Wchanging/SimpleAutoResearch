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


if __name__ == "__main__":
    unittest.main()
