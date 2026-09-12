from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from simple_ar.app.research_application import ResearchApplicationServices, load_session
from simple_ar.app.session_migration import import_legacy_session
from simple_ar.core import ArtifactStore
from simple_ar.research.workflow_contracts import ResearchBrief


class SessionMigrationTests(unittest.TestCase):
    def _write_legacy_session(self, root: Path) -> None:
        store = ArtifactStore(root)
        search_ref = store.write_json(
            "state/search.json",
            {"schema_version": "old_search.v1", "papers": [{"title": "A paper"}]},
            kind="search",
            schema="old_search.v1",
            producer="legacy",
        )
        brief_ref = store.write_json(
            "state/brief.json",
            ResearchBrief(
                request_text="Study calibration",
                objective="Study calibration under a small budget",
                requested_outputs=("report",),
            ).to_dict(),
            kind="brief",
            schema="research_brief_input.v1",
            producer="legacy",
        )
        store.write_json(
            "session_manifest.json",
            {
                "schema_version": "session_manifest.v1",
                "session_id": "legacy-session",
                "topic": "Study calibration",
                "status": "created",
                "state_refs": {
                    "search": search_ref.to_dict(),
                    "brief": brief_ref.to_dict(),
                },
                "budget": {},
                "decisions": [],
            },
            kind="session",
            schema="session_manifest.v1",
            producer="legacy",
        )

    def test_import_creates_successor_and_preserves_legacy_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy = base / "legacy"
            successor = base / "successor"
            self._write_legacy_session(legacy)
            before = {
                path.relative_to(legacy): path.read_bytes()
                for path in legacy.rglob("*")
                if path.is_file()
            }

            result = import_legacy_session(
                legacy,
                successor,
                artifact_names=("search", "not_present"),
                services=ResearchApplicationServices(max_results=1),
            )

            self.assertEqual(result.parent_session, "legacy-session")
            self.assertEqual(result.brief_source, "legacy_brief")
            self.assertEqual(result.budget_status, "unknown_not_imported")
            self.assertEqual([item.name for item in result.imported], ["search"])
            self.assertEqual([item.name for item in result.skipped], ["not_present"])
            self.assertTrue((successor / result.imported[0].destination_ref.path).is_file())

            restored = load_session(successor)
            self.assertEqual(restored.controller.manifest.parent_session, "legacy-session")
            self.assertEqual(restored.view().parent_session, "legacy-session")
            self.assertIn("legacy_import", restored.controller.manifest.state_refs)
            self.assertEqual(restored.brief.objective, "Study calibration under a small budget")
            self.assertEqual(restored.brief.requested_outputs, ("report",))
            self.assertEqual(restored.assets, ())

            after = {
                path.relative_to(legacy): path.read_bytes()
                for path in legacy.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)

    def test_import_does_not_place_legacy_paths_in_canonical_state_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy = base / "legacy"
            successor = base / "successor"
            self._write_legacy_session(legacy)

            import_legacy_session(legacy, successor, artifact_names=("search",))
            manifest = ArtifactStore(successor).read_json("session_manifest.json")
            refs = manifest["state_refs"]
            for ref in refs.values():
                self.assertFalse(Path(ref["path"]).is_absolute())
            self.assertEqual(manifest["parent_session"], "legacy-session")


if __name__ == "__main__":
    unittest.main()
