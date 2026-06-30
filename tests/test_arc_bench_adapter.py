from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from benchmark.adapter_contract import build_adapter_manifest


def load_adapter_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "benchmark" / "arc_bench" / "adapter.py"
    spec = importlib.util.spec_from_file_location("arc_bench_adapter_for_tests", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load ARC adapter module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ArcBenchAdapterTests(unittest.TestCase):
    def test_adapter_manifest_normalizes_paths_without_core_coupling(self) -> None:
        manifest = build_adapter_manifest(
            suite="demo",
            operation="prepare",
            status="prepared",
            inputs={"root": Path("bench/root")},
            outputs={"files": [Path("out/task.md")]},
            metadata={"topic": "T01"},
        )

        self.assertEqual(manifest["schema_version"], "simple_ar_benchmark_adapter.v1")
        self.assertEqual(manifest["suite"], "demo")
        self.assertEqual(manifest["inputs"]["root"], "bench/root")
        self.assertEqual(manifest["outputs"]["files"], ["out/task.md"])

    def test_loads_workspace_artifacts_next_to_generated_project(self) -> None:
        adapter = load_adapter_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            code_src = run_dir / "code_task" / "workspace" / "generated_project"
            artifact_dir = run_dir / "code_task" / "workspace" / "artifacts"
            code_src.mkdir(parents=True)
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "results.json").write_text(
                json.dumps({"aggregates": [{"dataset": "d1", "condition": "c1", "score_mean": 0.5}]}),
                encoding="utf-8",
            )
            (artifact_dir / "report.md").write_text("# Real Report\n\nMeasured table.", encoding="utf-8")
            (code_src / "README.md").write_text("# Project Readme\n\nNo results.", encoding="utf-8")

            results = adapter.load_project_results(run_dir, code_src)
            writeup = adapter.extract_project_writeup(run_dir, code_src)

            self.assertEqual(results["aggregates"][0]["dataset"], "d1")
            self.assertIn("artifacts", results["_artifact_source"])
            self.assertIn("Real Report", writeup)
            self.assertNotIn("Project Readme", writeup)


if __name__ == "__main__":
    unittest.main()
