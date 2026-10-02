"""Behavior checks for bounded inspection and conservative canonical cleanup."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from simple_ar.app.cleanup import CleanError, apply_clean_plan, build_clean_plan
from simple_ar.code_task.editing.scope import editable_context_files
from simple_ar.core.artifacts import write_json, write_text
from simple_ar.retrieval.chunking import build_artifact_chunks
from simple_ar.retrieval import chunking
from simple_ar.retrieval.index import MAX_SEARCH_FILE_BYTES, build_artifact_index
from simple_ar.retrieval.search import search_artifacts


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class StructureCleanupTests(unittest.TestCase):
    def setUp(self):
        TEST_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_analysis_projection_uses_domain_metric_normalization(self):
        from simple_ar.app.research_execution import analysis_contract_context
        context = analysis_contract_context(
            task_id="task", objective="compare", contract={}, design={},
            schema={"primary_metric": "loss", "required_metrics": ["loss", "accuracy"],
                    "metric_directions": {"loss": "min", "accuracy": "max"}},
            hard_constraints=[],
        )
        self.assertEqual(context["expected_metrics"], [
            {"name": "loss", "direction": "lower"},
            {"name": "accuracy", "direction": "higher"},
        ])

    def test_inspection_reads_previews_without_full_file_reads_or_hashes(self):
        write_text(self.root / "notes.unknown", "source evidence\n" * 1000)
        write_text(self.root / "log.txt", "observed result\n" * 1000)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded read")), \
             patch.object(Path, "read_text", side_effect=AssertionError("unbounded read")), \
             patch("simple_ar.retrieval.index._sha256", side_effect=AssertionError("unrequested hash")):
            result = build_artifact_index(self.root, write=False)
        self.assertEqual(len(result["artifacts"]), 2)
        self.assertTrue(all(row["sha256"] is None for row in result["artifacts"]))
        self.assertTrue(all(row["summary"] for row in result["artifacts"]))

    def test_hashes_remain_available_by_explicit_request(self):
        write_text(self.root / "notes.txt", "evidence")
        result = build_artifact_index(self.root, write=False, hash_files=True)
        self.assertEqual(len(result["artifacts"][0]["sha256"]), 64)

    def test_text_probe_keeps_utf8_split_at_preview_boundary(self):
        write_text(self.root / "notes.unknown", "a" * 2047 + "中文原文")
        index = build_artifact_index(self.root, write=False)
        self.assertEqual(index["artifacts"][0]["kind"], "text")

    def test_inspection_skips_symlinks_and_special_files(self):
        write_text(self.root / "source.txt", "evidence")
        try:
            (self.root / "alias.txt").symlink_to(self.root / "source.txt")
        except OSError:
            self.skipTest("Symlink creation unavailable")
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.root / "named-pipe")
        index = build_artifact_index(self.root, write=False)
        self.assertEqual([row["path"] for row in index["artifacts"]], ["source.txt"])

    def test_oversized_search_is_disclosed_not_silently_truncated(self):
        path = self.root / "large.log"
        with path.open("wb") as handle:
            handle.write(b"accuracy: 0.8\n")
            handle.seek(MAX_SEARCH_FILE_BYTES)
            handle.write(b"\n")
        write_text(self.root / "small.txt", "accuracy: 0.9")
        with patch("simple_ar.retrieval.chunking._read_lines", wraps=chunking._read_lines) as reader:
            result = search_artifacts(self.root, "accuracy", write=False)
        self.assertEqual(result["skipped_files"], [{
            "path": "large.log", "reason": "file_too_large", "bytes": MAX_SEARCH_FILE_BYTES + 1,
        }])
        self.assertEqual([call.args[0].name for call in reader.call_args_list], ["small.txt"])
        self.assertTrue(result["matches"])

    def test_read_only_chunking_does_not_write_an_implicit_index(self):
        write_text(self.root / "notes.txt", "source evidence")
        self.assertTrue(build_artifact_chunks(self.root, write=False))
        self.assertFalse((self.root / "artifact_index.json").exists())
        self.assertFalse((self.root / "artifact_chunks.jsonl").exists())
        with patch("simple_ar.retrieval.chunking.build_artifact_index", side_effect=AssertionError("explicit index ignored")):
            self.assertEqual(build_artifact_chunks(self.root, index={}, write=False), [])

    def test_canonical_cleanup_retains_referenced_fulltext_and_outputs(self):
        write_json(self.root / "session_manifest.json", {"schema_version": "session_manifest.v2"})
        write_json(self.root / "manifest.json", {"stages": []})
        write_json(self.root / "cache/literature/query.json", {
            "query": "topic", "source": "fixture", "limit": 2, "timestamp": 1, "papers": [],
        })
        for path in ("cache/literature/paper.txt", "cache/literature/paper.pdf", "documents/paper.txt",
                     "outputs/report.md", "attempts/read-0001/notes.json", "cache/literature/unknown.json"):
            write_text(self.root / path, "retained source or result")
        write_text(self.root / "artifact_index.json", "rebuildable")
        plan = build_clean_plan(self.root, all_caches=True)
        self.assertEqual({row.label for row in plan.targets}, {"cache/literature/query.json", "artifact_index.json"})
        apply_clean_plan(plan)
        self.assertTrue((self.root / "cache/literature/paper.txt").is_file())
        self.assertTrue((self.root / "attempts/read-0001/notes.json").is_file())
        self.assertTrue((self.root / "outputs/report.md").is_file())
        self.assertTrue((self.root / "session_manifest.json").is_file())
        self.assertTrue((self.root / "manifest.json").is_file())

    def test_default_cleanup_removes_metadata_but_preserves_indexes(self):
        write_json(self.root / "session_manifest.json", {"schema_version": "session_manifest.v2"})
        write_json(self.root / "cache/literature/query.json", {
            "query": "topic", "source": "fixture", "limit": 2, "timestamp": 1, "papers": [],
        })
        write_text(self.root / "artifact_index.json", "retained index")
        plan = build_clean_plan(self.root)
        self.assertEqual([row.label for row in plan.targets], ["cache/literature/query.json"])

    def test_unreadable_layout_blocks_cleanup_before_any_deletion(self):
        write_text(self.root / "session_manifest.json", "not JSON")
        write_text(self.root / "artifact_search_results.json", "retained")
        with self.assertRaises(CleanError):
            build_clean_plan(self.root)
        self.assertTrue((self.root / "artifact_search_results.json").exists())

    def test_uninspected_large_metadata_is_retained_without_full_read(self):
        from simple_ar.core.artifacts import read_json
        write_json(self.root / "session_manifest.json", {"schema_version": "session_manifest.v2"})
        path = self.root / "cache/literature/large.json"
        path.parent.mkdir(parents=True)
        with path.open("wb") as handle:
            handle.seek(1024 * 1024)
            handle.write(b"\n")
        with patch("simple_ar.app.cleanup.read_json", wraps=read_json) as reader:
            plan = build_clean_plan(self.root, all_caches=True)
        self.assertEqual([call.args[0].name for call in reader.call_args_list], ["session_manifest.json"])
        self.assertFalse(plan.targets)
        self.assertTrue(plan.skipped)
        self.assertTrue(path.is_file())

    def test_cleanup_does_not_follow_external_cache_symlink(self):
        write_json(self.root / "session_manifest.json", {"schema_version": "session_manifest.v2"})
        external = self.root / "other-session"
        external.mkdir()
        cache = self.root / "run/cache"
        cache.mkdir(parents=True)
        write_json(self.root / "run/session_manifest.json", {"schema_version": "session_manifest.v2"})
        try:
            (cache / "literature").symlink_to(external, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable")
        with self.assertRaises(CleanError):
            build_clean_plan(self.root / "run")
        self.assertTrue(external.is_dir())

    def test_editable_context_preserves_selection_order_and_protection(self):
        index = {"files": [
            {"path": "tests/test.py", "kind": "python"},
            {"path": "src/second.py", "kind": "python"},
            {"path": "src/first.py", "kind": "python"},
        ]}
        settings = {"allowed_patterns": ("src/**",), "protected_patterns": ("tests/**",), "max_files": 2}
        self.assertEqual(editable_context_files(index, ["src/first.py", "tests/test.py", "src/second.py"], **settings),
                         ["src/first.py", "src/second.py"])
        self.assertEqual(editable_context_files(index, ["tests/test.py"], **settings),
                         ["src/second.py", "src/first.py"])
        settings["max_files"] = 0
        self.assertEqual(editable_context_files(index, [], **settings), ["src/second.py"])


if __name__ == "__main__":
    unittest.main()
