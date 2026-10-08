from __future__ import annotations

import tempfile
import unittest
import os
import sys
from pathlib import Path
from unittest.mock import patch

from simple_ar.code_task.analysis.interfaces import (
    dependency_context,
    find_local_api_mismatches,
    order_file_specs,
    snippet_api_contract,
)
from simple_ar.code_task.analysis.entrypoints import analyze_entrypoint_debuggability
from simple_ar.code_task.analysis.resource_static import analyze_resource_risks
from simple_ar.code_task.generation.common import safe_relative_path, string_list
from simple_ar.code_task.generation.review import review_generated_project
from simple_ar.code_task.generation.writer import _response_self_reports_defect, write_generated_project
from simple_ar.code_task import initialize_code_task, review_code_task_changes, build_code_task_context_pack
from simple_ar.code_task.analysis.index import build_codebase_index
from simple_ar.code_task.analysis.repo_map import build_repo_map
from simple_ar.code_task.execution.environment import resolve_code_task_command
from simple_ar.core.artifacts import read_json, read_jsonl, write_json, write_text


class CodeTaskInterfaceTests(unittest.TestCase):
    def test_source_loader_attributes_are_not_missing_local_apis_but_typos_still_are(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / 'pkg').mkdir()
            (project / 'pkg/__init__.py').write_text('VALUE = 1\n')
            (project / 'pkg/implementation.py').write_text('VALUE = 2\n')
            (project / 'consumer.py').write_text(
                'import pkg as package\nimport pkg.implementation as local\n'
                'from pkg.implementation import __file__\n'
                'values = (local.__file__, local.__name__, local.__doc__, local.__package__, '
                'local.__loader__, local.__spec__, local.__cached__, local.__builtins__, '
                'local.__dict__, package.__path__)\n'
                'unknown = local.__invented_loader_flag__\n'
                'bad_package = local.__path__\n')
            findings = find_local_api_mismatches(project)
            self.assertEqual({row['missing_symbol'] for row in findings},
                             {'__invented_loader_flag__', '__path__'})

    def test_external_python_preserves_virtualenv_symlink_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "env" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
            executable.parent.mkdir(parents=True)
            try:
                executable.symlink_to(sys.executable)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"Symlink creation unavailable: {exc}")
            command = resolve_code_task_command(
                ["python", "-V"], env_mode="external", python_executable=executable,
            )
            self.assertEqual(command, [os.path.abspath(executable), "-V"])
            self.assertNotEqual(command[0], str(executable.resolve()))

    def test_generation_common_helpers_normalize_paths_and_lists(self) -> None:
        self.assertEqual(safe_relative_path("pkg\\runner.py"), "pkg/runner.py")
        self.assertEqual(safe_relative_path("../escape.py"), "")
        self.assertEqual(safe_relative_path("/absolute.py"), "absolute.py")
        self.assertEqual(string_list([" a ", "", 3], limit=2), ["a", "3"])

    def test_file_specs_are_ordered_dependencies_first(self) -> None:
        files = [
            {"path": "main.py", "dependencies": ["pkg/runner.py"]},
            {"path": "pkg/runner.py", "dependencies": ["pkg/data.py"]},
            {"path": "pkg/data.py", "dependencies": []},
        ]

        ordered = order_file_specs(files)

        self.assertEqual([row["path"] for row in ordered], ["pkg/data.py", "pkg/runner.py", "main.py"])

    def test_dependency_context_exposes_actual_generated_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(
                project / "pkg" / "data.py",
                "def load_text_classification_splits(config_path=None):\n    return [], {}\n",
            )

            context = dependency_context(
                project,
                {"path": "pkg/runner.py", "dependencies": ["pkg/data.py"]},
            )

            dependency = context["dependencies"][0]
            self.assertTrue(dependency["available"])
            self.assertIn("def load_text_classification_splits(config_path=None)", dependency["public_api"])
            self.assertNotIn("load_text_classification_dataset", str(context))

    def test_resource_static_flags_fit_inside_nested_loops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(
                project / "search.py",
                (
                    "def run(model, datasets, candidates):\n"
                    "    for dataset in datasets:\n"
                    "        for candidate in candidates:\n"
                    "            model.fit(dataset.X, dataset.y)\n"
                    "    return model\n"
                ),
            )

            analysis = analyze_resource_risks(project)

            self.assertGreaterEqual(analysis["nested_fit_call_count"], 1)
            self.assertGreaterEqual(analysis["max_fit_loop_depth"], 2)
            self.assertEqual(analysis["files"][0]["path"], "search.py")

    def test_writer_treats_runtime_output_paths_as_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "generated_project"
            artifacts = write_generated_project(
                project_dir=project,
                architecture_plan={
                    "files": [
                        {"path": "main.py", "kind": "source", "entrypoint": True},
                        {"path": "artifacts", "purpose": "Runtime output directory."},
                        {"path": "artifacts/results.json", "purpose": "Runtime evidence bundle."},
                        {"path": "submission/results", "purpose": "Submission metrics directory."},
                        {"path": "submission/results/metrics.json", "purpose": "Runtime metric mirror."},
                    ]
                },
                result_schema={"required_metrics": []},
                contract={},
                memory={},
                client=None,
                allow_fallback=True,
            )

            by_path = {row["path"]: row for row in artifacts["generated_files"]}
            self.assertTrue((project / "artifacts").is_dir())
            self.assertTrue((project / "artifacts" / "results.json").is_file())
            self.assertTrue((project / "submission" / "results").is_dir())
            self.assertEqual(by_path["artifacts"]["mode"], "deterministic_runtime_placeholder")
            self.assertEqual(by_path["artifacts/results.json"]["line_count"], 0)

    def test_writer_strips_single_markdown_fence_from_file_content(self) -> None:
        class FakeClient:
            def ask_json(self, _system: str, _prompt: str, **_kwargs: object) -> dict[str, str]:
                return {"content": "```python\nprint('score: 1.0')\n```", "summary": "Fenced but valid Python."}

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "generated_project"
            artifacts = write_generated_project(
                project_dir=project,
                architecture_plan={"files": [{"path": "main.py", "kind": "source", "entrypoint": True}]},
                result_schema={"required_metrics": ["score"]},
                contract={},
                memory={},
                client=FakeClient(),  # type: ignore[arg-type]
                allow_fallback=False,
                agent_step_dir=Path(tmp) / "steps",
            )

            self.assertEqual(artifacts["generated_files"][0]["mode"], "llm_repaired")
            self.assertEqual((project / "main.py").read_text(encoding="utf-8"), "print('score: 1.0')\n")

    def test_writer_records_invalid_file_generation_diagnostics(self) -> None:
        class FakeClient:
            def ask_json(self, _system: str, _prompt: str, **_kwargs: object) -> dict[str, str]:
                return {"content": "def broken(:\n    pass\n", "summary": "Invalid Python."}

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "generated_project"
            steps = Path(tmp) / "steps"
            with self.assertRaises(Exception):
                write_generated_project(
                    project_dir=project,
                    architecture_plan={"files": [{"path": "main.py", "kind": "source", "entrypoint": True}]},
                    result_schema={"required_metrics": ["score"]},
                    contract={},
                    memory={},
                    client=FakeClient(),  # type: ignore[arg-type]
                    retry_attempts=2,
                    allow_fallback=False,
                    agent_step_dir=steps,
                )

            failure = read_json(steps / "invalid_files" / "main.py" / "attempt-001.json")
            self.assertEqual(failure["validation"]["reason"], "syntax_error")
            self.assertTrue((steps / "invalid_files" / "main.py" / "attempt-001.py").is_file())

    def test_review_accepts_runtime_placeholders_as_runtime_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(project / "main.py", "def main():\n    print('score: 1.0')\n")
            (project / "artifacts").mkdir()
            write_text(project / "artifacts" / "results.json", "{}\n")

            review = review_generated_project(
                project_dir=project,
                code_artifacts={
                    "generated_files": [
                        {"path": "main.py", "kind": "source", "mode": "llm", "line_count": 2},
                        {"path": "artifacts", "kind": "runtime_dir", "mode": "deterministic_runtime_placeholder", "line_count": 0},
                        {
                            "path": "artifacts/results.json",
                            "kind": "output_placeholder",
                            "mode": "deterministic_runtime_placeholder",
                            "line_count": 0,
                        },
                    ]
                },
                result_schema={"primary_metric": "score", "required_metrics": ["score"]},
                resource_plan={"max_files": 8, "max_generated_lines": 200},
                use_llm=False,
            )

            self.assertNotIn("missing_file", {row["category"] for row in review["findings"]})

    def test_entrypoint_static_analysis_flags_suppressed_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(
                project / "main.py",
                (
                    "import sys\n\n"
                    "def main():\n"
                    "    try:\n"
                    "        raise RuntimeError('hidden')\n"
                    "    except Exception as exc:\n"
                    "        print(f'ERROR: {exc}', file=sys.stderr)\n"
                    "        return 1\n"
                ),
            )

            analysis = analyze_entrypoint_debuggability(project)

            self.assertEqual(len(analysis["findings"]), 1)
            self.assertEqual(analysis["findings"][0]["path"], "main.py")

    def test_valid_unicode_identifiers_survive_generation_review_and_repair_guards(self) -> None:
        import subprocess
        import sys
        from simple_ar.code_task.generation.writer import _validate_file_content
        from simple_ar.code_task.generation.generated_project_repair import _post_write_static_guard
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            source = "def 求和(数值):\n    return sum(数值)\n\ndef main():\n    print(求和([1, 2]))\n\nif __name__ == '__main__':\n    main()\n"
            write_text(
                project / "main.py",
                source,
            )
            self.assertTrue(_validate_file_content(source, filename="main.py").valid)
            self.assertEqual(_post_write_static_guard(target=project / "main.py", rel_path="main.py"), "")

            review = review_generated_project(
                project_dir=project,
                code_artifacts={"generated_files": [{"path": "main.py", "mode": "llm", "line_count": 5}]},
                result_schema={"primary_metric": "score", "required_metrics": ["score"]},
                resource_plan={"max_files": 4, "max_generated_lines": 200},
                use_llm=False,
            )

            self.assertFalse(any(row["severity"] == "blocking" for row in review["findings"]))
            executed = subprocess.run([sys.executable, str(project / "main.py")], capture_output=True, text=True, timeout=5)
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertEqual(executed.stdout.strip(), "3")
            self.assertFalse(_validate_file_content("def 求和(:", filename="main.py").valid)

    def test_writer_rejects_self_reported_defective_response(self) -> None:
        self.assertTrue(
            _response_self_reports_defect(
                {
                    "summary": (
                        "Created main.py. Note: the file content includes an import typo that should be "
                        "corrected before execution."
                    )
                }
            )
        )
        self.assertFalse(_response_self_reports_defect({"summary": "Corrected the import typo and verified the file."}))
        self.assertFalse(
            _response_self_reports_defect(
                {"summary": "Creates output directories before execution and writes measured metrics."}
            )
        )

    def test_review_blocks_entrypoint_that_suppresses_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(
                project / "main.py",
                (
                    "def main():\n"
                    "    try:\n"
                    "        print('score: 1.0')\n"
                    "    except Exception as exc:\n"
                    "        print(f'ERROR: {exc}')\n"
                    "        return 1\n"
                ),
            )

            review = review_generated_project(
                project_dir=project,
                code_artifacts={"generated_files": [{"path": "main.py", "mode": "llm", "line_count": 7}]},
                result_schema={"primary_metric": "score", "required_metrics": ["score"]},
                resource_plan={"max_files": 4, "max_generated_lines": 200},
                use_llm=False,
            )

            self.assertEqual(review["status"], "failed")
            self.assertTrue(
                any(row["category"] == "entrypoint_exception_suppresses_traceback" for row in review["findings"])
            )

    def test_review_accepts_entrypoint_that_preserves_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(
                project / "main.py",
                (
                    "import traceback\n\n"
                    "def main():\n"
                    "    try:\n"
                    "        print('score: 1.0')\n"
                    "    except Exception:\n"
                    "        traceback.print_exc()\n"
                    "        return 1\n"
                ),
            )

            review = review_generated_project(
                project_dir=project,
                code_artifacts={"generated_files": [{"path": "main.py", "mode": "llm", "line_count": 8}]},
                result_schema={"primary_metric": "score", "required_metrics": ["score"]},
                resource_plan={"max_files": 4, "max_generated_lines": 200},
                use_llm=False,
            )

            self.assertFalse(
                any(row["category"] == "entrypoint_exception_suppresses_traceback" for row in review["findings"])
            )

    def test_existing_context_snippets_expose_api_without_extra_repo_reads(self) -> None:
        contract = snippet_api_contract(
            [
                {
                    "path": "service.py",
                    "text": "class Service:\n    def run(self, value: int) -> str:\n        return str(value)\n",
                }
            ]
        )

        self.assertIn("class Service", contract["service.py"][0])
        self.assertTrue(any("Service.def run" in row for row in contract["service.py"]))

        from io import BytesIO
        from PIL import Image
        from simple_ar.code_task.analysis.context import (
            ensure_code_task_context_pack, load_latest_code_task_context_pack, read_source_snippets,
        )
        from simple_ar.code_task.analysis.source_context import requested_source_context
        from simple_ar.code_task.runtime.state import code_task_paths
        from simple_ar.core.artifacts import write_jsonl
        with tempfile.TemporaryDirectory() as folder:
            root, project = Path(folder), Path(folder) / 'project'
            (project / 'outputs').mkdir(parents=True)
            image = BytesIO()
            Image.new('RGB', (2, 2), 'red').save(image, format='PNG')
            (project / 'outputs/figure.png').write_bytes(image.getvalue())
            # A text extension must not bypass the content boundary either.
            (project / 'outputs/not_text.csv').write_bytes(image.getvalue())
            write_text(project / 'outputs/results.json', '{"score": 4}\n')
            write_text(project / 'outputs/values.csv', 'value\n4\n')
            diagrams = {'outputs/method.svg': '<svg xmlns="http://www.w3.org/2000/svg"><text>Method</text></svg>\n',
                        'outputs/method.dot': 'digraph method { input -> output; }\n',
                        'Dockerfile': 'FROM python:3.12\nCOPY analysis.py /app/\n',
                        'outputs/source.custom': 'A legitimate UTF-8 source: 方法\n'}
            for name, content in diagrams.items():
                write_text(project / name, content)
            text = 'LABEL = "alpha\u0085beta\u2028gamma\u2029delta"\n'
            write_text(project / 'analysis.py', text)
            (project / 'legacy.py').write_bytes('# coding: cp1252\nLABEL = "café"\n'.encode('cp1252'))
            task = root / 'task.md'
            write_text(task, 'Inspect analysis.py legacy.py outputs/figure.png outputs/results.json outputs/values.csv')
            initialize_code_task(run_dir=root / 'run', code_root=project, task_file=task,
                edit_scope_allowed_patterns=('analysis.py', 'legacy.py', 'outputs/**'))
            workspace = code_task_paths(root / 'run').workspace_dir
            index = build_codebase_index(workspace)
            self.assertIn('outputs/figure.png', [row['path'] for row in index['files']])
            selected = ['outputs/figure.png', 'outputs/not_text.csv', 'analysis.py', 'legacy.py',
                        'outputs/results.json', 'outputs/values.csv', *diagrams]
            snippets = read_source_snippets(workspace, selected, max_chars_per_file=2000)
            rows = {row['path']: row for row in snippets}
            self.assertNotIn('outputs/figure.png', rows)
            self.assertNotIn('outputs/not_text.csv', rows)
            self.assertEqual(rows['analysis.py']['text'], text)
            self.assertIn('café', rows['legacy.py']['text'])
            self.assertIn('outputs/results.json', rows)
            self.assertIn('outputs/values.csv', rows)
            for name, content in diagrams.items():
                self.assertEqual(rows[name]['text'], content)
            lookup = requested_source_context(workspace, index, {'files': selected}, supplied=[],
                max_files=8, max_chars=2000, max_total_chars=10000)
            self.assertFalse(any(row['path'].endswith('figure.png') or row['path'].endswith('not_text.csv')
                                 for row in lookup))
            pack = build_code_task_context_pack(root / 'run', max_files=8, max_source_chars_per_file=2000,
                max_total_chars=10000)
            self.assertNotIn('outputs/figure.png', pack.selected_files)
            saved = read_jsonl(pack.snippets_path)
            self.assertTrue(any(row['path'] == 'analysis.py' and '\u0085' in row['text'] for row in saved))
            # Simulate the pre-fix cached context. Refresh must retain it as
            # evidence, not mutate its snippets or reuse its prompt garbage.
            write_jsonl(pack.snippets_path, [*saved, {'path': 'outputs/figure.png',
                'text': image.getvalue().decode('utf-8', errors='replace')}])
            write_text(pack.prompt_context_path, 'Old binary source preview')
            old_bytes = pack.snippets_path.read_bytes()
            self.assertIsNone(load_latest_code_task_context_pack(root / 'run'))
            fresh = ensure_code_task_context_pack(root / 'run', query=task.read_text(), max_files=8,
                max_source_chars_per_file=2000)
            self.assertIsNotNone(fresh)
            self.assertNotEqual(fresh.context_pack_path, pack.context_pack_path)
            self.assertNotIn('outputs/figure.png', fresh.selected_files)
            # Cached editable vector/code sources remain eligible regardless
            # of their extension; they must not cause perpetual rebuilding.
            fresh_rows = list(fresh.snippets)
            for name, content in diagrams.items():
                if not any(row['path'] == name for row in fresh_rows):
                    fresh_rows.append({'path': name, 'text': content})
            write_jsonl(fresh.snippets_path, fresh_rows)
            self.assertEqual(load_latest_code_task_context_pack(root / 'run').context_pack_path,
                             fresh.context_pack_path)
            self.assertEqual(pack.snippets_path.read_bytes(), old_bytes)
            self.assertEqual(pack.prompt_context_path.read_text(), 'Old binary source preview')
            self.assertNotIn('Old binary source preview', fresh.prompt_context_path.read_text())

    def test_review_blocks_cross_file_api_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(project / "main.py", "from pkg.runner import run_experiment\n")
            write_text(project / "pkg" / "__init__.py", "")
            write_text(
                project / "pkg" / "data.py",
                "def load_text_classification_splits(config_path=None):\n    return [], {}\n",
            )
            write_text(
                project / "pkg" / "runner.py",
                (
                    "from . import data as data_module\n\n"
                    "def run_experiment():\n"
                    "    return data_module.load_text_classification_dataset()\n"
                ),
            )
            artifacts = {
                "generated_files": [
                    {"path": "main.py", "mode": "llm", "line_count": 1},
                    {"path": "pkg/__init__.py", "mode": "llm", "line_count": 1},
                    {"path": "pkg/data.py", "mode": "llm", "line_count": 2},
                    {"path": "pkg/runner.py", "mode": "llm", "line_count": 4},
                ]
            }

            mismatches = find_local_api_mismatches(project)
            review = review_generated_project(
                project_dir=project,
                code_artifacts=artifacts,
                result_schema={"primary_metric": "score", "required_metrics": ["score"]},
                resource_plan={"max_files": 8, "max_generated_lines": 200},
                use_llm=False,
            )

            self.assertEqual(mismatches[0]["missing_symbol"], "load_text_classification_dataset")
            self.assertEqual(review["status"], "failed")
            self.assertTrue(any(row["category"] == "missing_local_api" for row in review["findings"]))

    def test_star_reexports_and_loaded_submodules_are_not_missing_apis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(project / "lib" / "__init__.py", "from .util import *\n")
            write_text(project / "lib" / "util.py", "Thing = int\n")
            write_text(project / "lib" / "deep.py", "def run():\n    return 1\n")
            write_text(
                project / "caller.py",
                "import lib\nimport lib.deep\nfrom lib import Thing\n"
                "result = lib.deep.run()\nvalue = lib.Thing(1)\n",
            )
            self.assertEqual(find_local_api_mismatches(project), [])

            # Possible module bindings include branch-local imports/exports.
            # Python scope, not package names or task exceptions, owns this.
            write_text(project / "bridge.py",
                "try:\n    from collections.abc import Mapping as MappingAlias\n"
                "except ImportError:\n    MappingAlias = dict\n"
                "if True:\n    class BranchType:\n        private_member = 1\n"
                "    def branch_function():\n        private_local = 1\n"
                "else:\n    alternative = 2\n"
                "left, right = (1, 2)\nimport os.path\n")
            write_text(project / "dynamic.py", "def __getattr__(name):\n    return name\n")
            write_text(project / "conditional.py", "if True:\n    from lib.util import *\n")
            write_text(project / "consumer.py",
                "from bridge import MappingAlias, BranchType, branch_function, alternative, left, right, os\n"
                "from bridge import private_member, private_local, missing, path\n"
                "from dynamic import runtime_name\nfrom conditional import Thing\n")
            with patch('importlib.import_module', side_effect=AssertionError('Do not execute user code')):
                findings = find_local_api_mismatches(project)
            self.assertEqual({row['missing_symbol'] for row in findings},
                             {'private_member', 'private_local', 'missing', 'path'})

    def test_review_warns_when_planned_public_api_is_not_exported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(project / "main.py", "def main():\n    print('score: 1.0')\n")
            write_text(project / "pkg" / "__init__.py", "")
            write_text(project / "pkg" / "data.py", "def load_rows():\n    return []\n")

            review = review_generated_project(
                project_dir=project,
                code_artifacts={
                    "generated_files": [
                        {"path": "main.py", "mode": "llm", "line_count": 2},
                        {"path": "pkg/__init__.py", "mode": "llm", "line_count": 1},
                        {"path": "pkg/data.py", "mode": "llm", "line_count": 2},
                    ]
                },
                architecture_plan={
                    "files": [
                        {"path": "main.py", "public_api": ["main(argv=None)"]},
                        {"path": "pkg/data.py", "public_api": ["load_dataset(config)"]},
                    ]
                },
                result_schema={"primary_metric": "score", "required_metrics": ["score"]},
                resource_plan={"max_files": 8, "max_generated_lines": 200},
                use_llm=False,
            )

            self.assertTrue(any(row["category"] == "planned_api_not_exported" for row in review["findings"]))
            self.assertTrue(all(row["severity"] != "blocking" for row in review["findings"] if row["category"] == "planned_api_not_exported"))

    def test_review_does_not_block_defensive_placeholder_policy_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(
                project / "main.py",
                (
                    "def main():\n"
                    "    # Refusing to emit placeholder metrics keeps failed benchmark paths honest.\n"
                    "    print('accuracy: 0.91')\n"
                ),
            )

            review = review_generated_project(
                project_dir=project,
                code_artifacts={"generated_files": [{"path": "main.py", "mode": "llm", "line_count": 3}]},
                result_schema={"primary_metric": "accuracy", "required_metrics": ["accuracy"]},
                resource_plan={"max_files": 4, "max_generated_lines": 200},
                contract={"objective": "Run an experiment and evaluate metrics without placeholder values."},
                use_llm=False,
            )

            self.assertFalse(any(row["category"] == "placeholder_execution_path" for row in review["findings"]))

    def test_review_blocks_mixed_llm_and_core_fallback_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(project / "main.py", "def main():\n    return 0\n")
            write_text(project / "pkg" / "models.py", "# Reserved generated module.\n")
            review = review_generated_project(
                project_dir=project,
                code_artifacts={
                    "generated_files": [
                        {"path": "main.py", "mode": "llm", "line_count": 2},
                        {"path": "pkg/models.py", "mode": "fallback", "line_count": 1},
                    ]
                },
                result_schema={},
                resource_plan={"max_files": 8, "max_generated_lines": 200},
                use_llm=False,
            )

            self.assertEqual(review["status"], "failed")
            self.assertTrue(any(row["category"] == "mixed_generation_fallback" for row in review["findings"]))

    def test_review_blocks_missing_explicit_greenfield_task_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(project / "main.py", "def main():\n    print('best_score: 1.0')\n")
            write_text(project / "generated_experiment" / "__init__.py", "")
            write_text(
                project / "generated_experiment" / "runner.py",
                "def run_experiment():\n    return {'best_score': 1.0, 'task_count': 1.0}\n",
            )

            review = review_generated_project(
                project_dir=project,
                code_artifacts={
                    "generated_files": [
                        {"path": "main.py", "mode": "llm", "line_count": 2},
                        {"path": "generated_experiment/__init__.py", "mode": "llm", "line_count": 1},
                        {"path": "generated_experiment/runner.py", "mode": "llm", "line_count": 2},
                    ]
                },
                result_schema={"primary_metric": "best_score", "required_metrics": ["best_score", "task_count"]},
                resource_plan={"max_files": 16, "max_generated_lines": 2000},
                contract={
                    "objective": "Greenfield analysis suite",
                    "task": (
                        "Create README.md, self-check, prefer sample-lib/sample_lib when available, "
                        "at least two tasks, artifacts/results.json, artifacts/report.md, "
                        "and artifacts/condition_results.jsonl."
                    ),
                },
                dependency_advice={
                    "packages": [
                        {
                            "package": "sample-lib",
                            "import_name": "sample_lib",
                            "status": "installed",
                            "matched_terms": ["sample-lib", "sample_lib"],
                        }
                    ]
                },
                use_llm=False,
            )

            categories = {row["category"] for row in review["findings"]}
            self.assertEqual(review["status"], "failed")
            self.assertIn("missing_required_artifact", categories)
            self.assertIn("missing_artifact_writer", categories)
            self.assertIn("missing_cli_mode", categories)
            self.assertIn("missing_requested_dependency_path", categories)
            self.assertIn("insufficient_task_count", categories)

    def test_existing_project_review_blocks_changed_local_api_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            task = root / "task.md"
            write_text(source / "api.py", "def available():\n    return 1\n")
            write_text(source / "caller.py", "import api as api_module\n\ndef run():\n    return api_module.available()\n")
            write_text(task, "# Task\n\nUpdate the caller without breaking local interfaces.\n")
            run_dir = root / "run"
            initialized = initialize_code_task(
                run_dir=run_dir,
                code_root=source,
                task_file=task,
                benchmark_command="python caller.py",
            )
            write_text(
                initialized.workspace_dir / "caller.py",
                "import api as api_module\n\ndef run():\n    return api_module.missing()\n",
            )
            manifest = read_json(run_dir / "manifest.json")
            manifest["patch"] = {"status": "applied", "changed_files": ["caller.py"]}
            write_json(run_dir / "manifest.json", manifest)

            result = review_code_task_changes(run_dir, use_llm=False)
            report = read_json(result.report_path)

            self.assertEqual(result.status, "failed")
            self.assertTrue(any(row["category"] == "interface_compatibility" for row in report["findings"]))


class LocalImportTests(unittest.TestCase):
    """Static local-interface discovery without importing the project."""

    def project(self, directory):
        root = Path(directory) / 'project'
        root.mkdir()
        sources = {
            'src/acme/__init__.py': '',
            'src/acme/entry.py': 'from .worker import transform\nfrom . import settings\nimport numpy\n',
            'src/acme/worker.py': 'from . import hidden\ndef transform(x):\n    return x\n',
            'src/acme/settings.py': 'SCALE = 1\n',
            'src/acme/hidden.py': 'VALUE = 2\n',
            'noise.py': 'def entry_transform():\n    return "noise"\n',
            'src.py': 'import acme.worker as worker\n',
        }
        for name, content in sources.items():
            write_text(root / name, content)
        return root

    def test_exact_relative_references_do_not_import_project_or_external_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.project(directory)
            with patch('importlib.import_module', side_effect=AssertionError('No imports')):
                index = build_codebase_index(root)
                mapping = build_repo_map(index)
            entry = next(row for row in index['files'] if row['path'] == 'src/acme/entry.py')
            self.assertEqual(entry['python']['imports'], ['numpy', 'worker'])  # Historical top-level summary unchanged.
            refs = entry['python']['import_references']
            self.assertIn({'module': 'worker', 'level': 1, 'names': ['transform']}, refs)
            rows = {row['path']: row for row in mapping['files']}
            self.assertEqual(rows['src/acme/entry.py']['local_import_paths'],
                ['src/acme/worker.py', 'src/acme/__init__.py', 'src/acme/settings.py'])
            self.assertEqual(rows['src.py']['local_import_paths'], ['src/acme/worker.py'])
            self.assertNotIn('local_import_paths', rows['noise.py'])

    def test_ambiguous_root_src_modules_and_escape_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.project(directory)
            write_text(root / 'acme/worker.py', 'VALUE = "alternative"\n')
            write_text(root / 'acme/escape.py', 'from ...outside import value\n')
            rows = {row['path']: row for row in build_repo_map(build_codebase_index(root))['files']}
            self.assertNotIn('local_import_paths', rows['src.py'])
            self.assertNotIn('local_import_paths', rows['acme/escape.py'])
            self.assertIn('src/acme/worker.py', rows['src/acme/entry.py'].get('local_import_paths', []))
            self.assertNotIn('acme/worker.py', rows['src/acme/entry.py'].get('local_import_paths', []))

    def test_legacy_index_keeps_no_guessed_association(self):
        with tempfile.TemporaryDirectory() as directory:
            index = build_codebase_index(self.project(directory))
            for row in index['files']:
                row.get('python', {}).pop('import_references', None)
            self.assertFalse(any(row.get('local_import_paths') for row in build_repo_map(index)['files']))

    def test_explicit_targets_required_evidence_then_one_hop_reading_with_original_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            root = self.project(directory)
            write_text(root / 'conditions.toml', 'scale = 1\n')
            write_text(directory / 'task.md', 'Fix entry_transform in noise.py')
            run = directory / 'run'
            initialize_code_task(run_dir=run, code_root=root, task_file=directory / 'task.md',
                benchmark_command='python -m unittest', edit_scope_allowed_patterns=('*.py',),
                edit_scope_protected_patterns=('src/acme/worker.py',))
            pack = build_code_task_context_pack(run, max_files=3,
                preferred_paths=('src/acme/entry.py',), max_total_chars=300, max_source_chars_per_file=100)
            self.assertEqual(pack.selected_files, ('src/acme/entry.py', 'src/acme/worker.py', 'src/acme/__init__.py'))
            rows = read_jsonl(pack.snippets_path)
            self.assertEqual(rows[1]['access_role'], 'read_only_evidence')
            self.assertTrue(any('static local import' in reason for reason in rows[1]['reasons']))
            self.assertNotIn('src/acme/hidden.py', pack.selected_files)  # No transitive expansion.
            self.assertLessEqual(sum(row['chars'] for row in rows), 300)


if __name__ == "__main__":
    unittest.main()
