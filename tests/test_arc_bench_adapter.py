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
    def test_manifest_only_task_excludes_rubric_criteria(self) -> None:
        adapter = load_adapter_module()
        manifest = {
            "id": "ML99",
            "title": "Manifest-only test",
            "synthesis": "A public task summary.",
            "hypotheses": [{"id": "H1", "statement": "The method improves the metric."}],
            "experiment_design": {
                "research_question": "Does the method help?",
                "conditions": [{"name": "baseline", "description": "Reference condition."}],
                "datasets": [{"name": "toy", "source": "local fixture"}],
                "metrics": [{"name": "score", "direction": "higher_is_better", "description": "Primary score."}],
            },
        }
        rubric = {
            "leaves": [
                {
                    "id": "hidden-leaf",
                    "task_category": "Code Development",
                    "requirements": "Use the hidden acceptance criterion.",
                }
            ]
        }
        options = adapter.PrepareOptions(
            arc_root=Path("arc"),
            topic="ML99",
            output_dir=Path("prepared"),
            simple_ar_output_root=Path("runs"),
            benchmark_command="python generated_project/main.py",
            generation_input="manifest_only",
        )

        task = adapter.render_task_markdown(manifest, rubric, options)

        self.assertIn("Does the method help?", task)
        self.assertIn("Reference condition.", task)
        self.assertNotIn("## Rubric Leaves", task)
        self.assertNotIn("hidden-leaf", task)
        self.assertNotIn("hidden acceptance criterion", task)
        self.assertNotIn("rubric", task.lower())

    def test_manifest_only_rejects_rubric_derived_contract(self) -> None:
        adapter = load_adapter_module()
        with self.assertRaises(SystemExit):
            adapter.validate_generation_input("manifest_only", include_contract=True)

    def test_manifest_only_analysis_context_excludes_rubric_criteria(self) -> None:
        adapter = load_adapter_module()
        context = adapter.build_analysis_context(
            manifest={"id": "ML99", "title": "Test", "experiment_design": {}},
            rubric={"id": "hidden-leaf", "requirements": "Hidden acceptance criterion."},
            metrics={},
            project_results={},
            run_dir=Path("run"),
            code_src=None,
            fallback_readme="",
            fallback_claims={},
            include_rubric_criteria=False,
        )

        self.assertEqual(context["criteria"], [])
        self.assertEqual(context["metadata"]["generation_input"], "manifest_only")

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

    def test_load_project_results_preserves_root_list(self) -> None:
        adapter = load_adapter_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            code_src = run_dir / "code_task" / "workspace" / "generated_project"
            artifact_dir = code_src / "artifacts"
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "results.json").write_text(
                '[{"dataset":"wine","condition":"baseline","metrics":{"accuracy":0.9}}]',
                encoding="utf-8",
            )

            results = adapter.load_project_results(run_dir, code_src)

            self.assertEqual(results["_artifact_root"], "list")
            self.assertEqual(len(results["records"]), 1)

    def test_strict_disagreement_adjudication_overrides_reviewer_average(self) -> None:
        adapter = load_adapter_module()
        leaves = [
            {"id": "leaf-a", "task_category": "Code Development", "weight": 1.0, "requirements": "A"},
            {"id": "leaf-b", "task_category": "Result Analysis", "weight": 1.0, "requirements": "B"},
        ]
        reviewers = [
            {
                "leaf_grades": [
                    {"id": "leaf-a", "score": 1.0, "reasoning": "A strong"},
                    {"id": "leaf-b", "score": 0.8, "reasoning": "B ok"},
                ]
            },
            {
                "leaf_grades": [
                    {"id": "leaf-a", "score": 0.4, "reasoning": "A weak"},
                    {"id": "leaf-b", "score": 0.6, "reasoning": "B ok-ish"},
                ]
            },
        ]
        disagreements = adapter.find_strict_disagreements(reviewers, leaves, threshold=0.2)
        combined = adapter.combine_strict_reviewer_grades(
            reviewers=reviewers,
            adjudication={
                "leaf_grades": [
                    {"id": "leaf-a", "score": 0.7, "reasoning": "Adjudicated", "category": "Code Development"}
                ]
            },
            leaves=leaves,
        )

        self.assertEqual([row["leaf_id"] for row in disagreements], ["leaf-a"])
        self.assertEqual(combined[0]["score"], 0.7)
        self.assertEqual(combined[0]["source_round"], "strict_adjudication")
        self.assertAlmostEqual(combined[1]["score"], 0.7)
        self.assertEqual(combined[1]["source_round"], "strict_reviewer_average")


if __name__ == "__main__":
    unittest.main()
