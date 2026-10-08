from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from simple_ar.cli.parser import build_parser
from simple_ar.cli.research_config import research_defaults
from simple_ar.cli.start import prepare_start
from simple_ar.code_task.runtime.config import load_code_task_init_options, load_code_task_execute_options


class StartTests(unittest.TestCase):
    def test_document_urls_preserve_literal_input_and_require_confirmed_chat_acquisition(self):
        url = 'https://example.test/paper'
        args = build_parser().parse_args(['start', '--kind', 'reproduction', '--goal', 'Inspect paper',
            '--document', url, '--document', 'local-paper.md', '--prepare-only'])
        self.assertEqual(args.document, [url, Path('local-paper.md')])
        with patch('sys.stdin.isatty', return_value=False), patch('simple_ar.cli.start.print_line'), \
             patch('simple_ar.research.documents.fulltext.urllib.request.urlopen') as fetch:
            with self.assertRaisesRegex(ValueError, 'Paper URLs require reproduction --chat'):
                prepare_start(args)
        fetch.assert_not_called()

    def test_registered_code_revision_preserves_scope_checker_policy_and_original(self):
        from simple_ar.core.capabilities import CapabilityRegistry
        from simple_ar.core.session import SessionController
        from simple_ar.code_task.orchestration.workflow import initialize_code_task
        from simple_ar.cli.start import reuse_session_materials, session_materials
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'
            source.mkdir()
            (source / 'main.py').write_text('VALUE = 1\n')
            task = root / 'task.md'
            task.write_text('Fixture project, no execution during preparation.')
            session = SessionController.create(root / 'old', session_id='old', topic='Fixture', registry=CapabilityRegistry())
            initialized = initialize_code_task(run_dir=session.store.root / 'project_run', code_root=source,
                task_file=task, benchmark_command='python main.py',
                edit_scope_allowed_patterns=('main.py',), edit_scope_protected_patterns=('gold/**',))
            session.manifest.state_refs['implementation'] = session.store.write_json('implementation.json',
                {'status': 'validated', 'workspace_dir': str(initialized.workspace_dir), 'code_task_run_dir': str(initialized.run_dir)})
            session.manifest.state_refs['preparation'] = session.store.write_json('prepared.json',
                {'execution': {'cwd': str(initialized.workspace_dir), 'code_task': {'budget_profile': 'large', 'allow_large_edits': True}}},
                kind='prepared_execution')
            session.save()
            before = session.store.resolve('session_manifest.json').read_bytes()
            self.assertEqual(session_materials(session.store.root)['code_project'], [initialized.workspace_dir.resolve()])
            args = build_parser().parse_args(['start', '--from-session', str(session.store.root), '--reuse', 'code_project',
                '--goal', 'Change VALUE to two', '--output-root', str(root / 'new'), '--prepare-only'])
            reuse_session_materials(args)
            self.assertEqual(args.allow, ['main.py'])
            self.assertEqual(args.validate, 'python main.py')
            with patch('sys.stdin.isatty', return_value=False), patch('simple_ar.cli.start.print_line'):
                config = prepare_start(args)
            settings = tomllib.loads((config.parent / 'code_task.toml').read_text())
            self.assertIn('gold/**', settings['edit_scope']['protected_patterns'])
            self.assertEqual(settings['execute']['budget_profile'], 'large')
            self.assertTrue(settings['execute']['allow_large_edits'])
            self.assertEqual((initialized.workspace_dir / 'main.py').read_text(), 'VALUE = 1\n')
            self.assertEqual(session.store.resolve('session_manifest.json').read_bytes(), before)
            conflict = build_parser().parse_args(['start', '--from-session', str(session.store.root), '--reuse', 'code_project', '--allow', '**'])
            with self.assertRaisesRegex(ValueError, 'do not override'):
                reuse_session_materials(conflict)
            # Repeating measurement uses the saved protocol and existing files;
            # creation-only initial_files must never enter the new configuration.
            session.manifest.state_refs['preparation'] = session.store.write_json('prepared.json',
                {'execution': {'cwd': str(initialized.workspace_dir), 'command': ['python', 'main.py', '--measure'],
                    'result_schema': {'required_metrics': ['count'], 'primary_metric': 'count',
                                      'output_files': {'counts': 'counts.json'},
                                      'metric_sources': {'count': {'output': 'counts', 'path': ['count']}}}, 'timeout_sec': 45,
                    'protocol': {'hypothesis': 'Check published count', 'dataset': 'Fixture data',
                                 'expected_outcome': 'Compare the recorded count'},
                    'code_task': {'budget_profile': 'large', 'allow_large_edits': True}}},
                kind='prepared_execution')
            session.save()
            retained = session.store.resolve('session_manifest.json').read_bytes()
            paper = root / 'paper.md'
            paper.write_text('Supplied paper and fixed comparison scope.')
            repeat = build_parser().parse_args(['start', '--kind', 'reproduction', '--from-session',
                str(session.store.root), '--reuse', 'code_project', '--goal', 'Check readiness and repeat the fixed measurement',
                '--document', str(paper), '--output-root', str(root / 'repeat'), '--prepare-only', '--yes'])
            with patch('sys.stdin.isatty', return_value=False), patch('simple_ar.cli.start.print_line'), patch('subprocess.run') as process:
                repeated = prepare_start(repeat)
            process.assert_not_called()
            parsed = tomllib.loads(repeated.read_text())
            self.assertNotIn('initial_files', parsed['execution'])
            self.assertEqual(parsed['execution']['command'][1:], ['main.py', '--measure'])
            self.assertEqual(parsed['execution']['protocol']['dataset'], 'Fixture data')
            self.assertEqual(parsed['execution']['timeout_sec'], 45)
            self.assertEqual(parsed['execution']['metric_sources'], {'count': {'output': 'counts', 'path': ['count']}})
            repeat_code = tomllib.loads((repeated.parent / 'code_task.toml').read_text())
            self.assertEqual(parsed['execution']['command'][0], repeat_code['environment']['python'])
            self.assertEqual(repeat_code['edit_scope']['allowed_patterns'], ['main.py'])
            self.assertIn('gold/**', repeat_code['edit_scope']['protected_patterns'])
            self.assertEqual(repeat_code['benchmark']['command'], 'python main.py')
            self.assertEqual(session.store.resolve('session_manifest.json').read_bytes(), retained)

    def test_figure_prepare_only_without_data_uses_canonical_large_code_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            goal = "Draw a conceptual inputs-to-model-to-evidence method with an uncertainty branch"
            with patch("simple_ar.result_analysis.script_project.preview_table_source") as preview, \
                    patch("subprocess.run") as process:
                config = self.prepare("--kind", "figure", "--goal", goal,
                                      "--output-root", str(root / "tasks"), "--prepare-only")
            preview.assert_not_called()
            process.assert_not_called()
            defaults = research_defaults(["research-session", "--config", str(config)])
            self.assertEqual(defaults["task_kind"], "bug_fix")
            source = config.parent / "source"
            self.assertEqual(list((source / "data").iterdir()), [])
            self.assertEqual(list(source.rglob("*.csv")), [])
            compile((source / "analysis.py").read_bytes(), "analysis.py", "exec", dont_inherit=True)
            self.assertIn("No dataset was supplied", (source / "README.md").read_text(encoding="utf-8"))
            self.assertIn(goal, (source / "README.md").read_text(encoding="utf-8"))
            self.assertIn("figure.svg", (source / "tests/verify_delivery.py").read_text(encoding="utf-8"))
            self.assertIn("embedded raster", (source / "tests/verify_delivery.py").read_text(encoding="utf-8"))
            self.assertFalse((source / "outputs").exists())
            options = load_code_task_init_options(config_path=defaults["code_task_config"])
            execution = load_code_task_execute_options(config_path=defaults["code_task_config"])
            self.assertEqual(Path(options.code_root), source.resolve())
            self.assertEqual(options.edit_scope_allowed_patterns, ("analysis.py", "src/**", "outputs/**"))
            self.assertIn("data/**", options.edit_scope_protected_patterns)
            self.assertIn("tests/**", options.edit_scope_protected_patterns)
            self.assertEqual(execution.budget_profile, "large")
            self.assertTrue(execution.allow_large_edits)
            self.assertEqual(execution.baseline_policy, "skip")
            self.assertEqual(defaults["data_path"], [str((source / "data").resolve())])

    def test_script_project_diagram_entry_reuses_scaffold_and_checks_vectors(self):
        import json
        from simple_ar.result_analysis import script_project
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "measurements.csv"
            data.write_text("stage,value\ninput,1\n", encoding="utf-8")
            goal = "  Draw inputs → model → evidence.\nKeep the uncertainty branch.  "
            for name, source, diagram in (("concept", None, True), ("combined", data, True),
                                          ("analysis", data, False)):
                with self.subTest(mode=name), patch.object(script_project, "preview_table_source",
                        wraps=script_project.preview_table_source) as preview, \
                        patch("subprocess.run") as process:
                    project, command = script_project.prepare_script_project(root / name, source, goal, diagram=diagram)
                    self.assertEqual(command, ("python", "tests/verify_delivery.py"))
                    self.assertEqual(script_project.PROTECTED_PATTERNS, ("data/**", "tests/**"))
                    self.assertTrue((project / "README.md").read_text(encoding="utf-8").startswith(goal + "\n"))
                    self.assertFalse((project / "outputs").exists())
                    process.assert_not_called()  # Preparation never executes the scaffold.
                    if source is None:
                        preview.assert_not_called()
                        self.assertEqual(list((project / "data").iterdir()), [])
                        self.assertIn("No dataset was supplied", (project / "README.md").read_text())
                    else:
                        preview.assert_called_once()
                        self.assertEqual((project / "data/input.csv").read_bytes(), data.read_bytes())
                    checker = project / "tests/verify_delivery.py"
                    if not diagram:
                        outputs = project / "outputs"
                        outputs.mkdir()
                        (outputs / "results.json").write_text('{"count": 2}')
                        (outputs / "report.md").write_text("Two observations; no figure requested.")
                        namespace = {"__file__": str(checker), "__name__": "delivery_checker_test"}
                        exec(compile(checker.read_text(), str(checker), "exec"), namespace)
                        with contextlib.redirect_stdout(io.StringIO()):
                            namespace["main"]()
                        (outputs / "custom.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
                        with contextlib.redirect_stdout(io.StringIO()):
                            namespace["main"]()
                        (outputs / "custom.svg").write_text("not svg")
                        from xml.etree.ElementTree import ParseError
                        with self.assertRaises(ParseError):
                            namespace["main"]()
                        continue
                    readme = (project / "README.md").read_text(encoding="utf-8")
                    self.assertIn("Do not invent measured values", readme)
                    self.assertIn("svg.fonttype='none'", readme)
                    outputs = project / "outputs"
                    outputs.mkdir()
                    from PIL import Image
                    Image.new("RGB", (2, 2), "white").save(outputs / "figure.png")
                    (outputs / "results.json").write_text(json.dumps({"components": ["model"],
                        "relationships": [], "input_sources": ["user goal"]}), encoding="utf-8")
                    (outputs / "report.md").write_text("Conceptual method; no measured performance.")
                    namespace = {"__file__": str(checker), "__name__": "delivery_checker_test"}
                    exec(compile(checker.read_text(), str(checker), "exec"), namespace)
                    valid = '<svg xmlns="http://www.w3.org/2000/svg"><text x="1" y="1">Model</text><path d="M0 0 L1 1"/></svg>'
                    (outputs / "figure.svg").write_text(valid)
                    with contextlib.redirect_stdout(io.StringIO()):
                        namespace["main"]()
                    self.assertEqual(process.call_args.args[0][1], str(project / "analysis.py"))
                    for invalid in (valid.replace("</svg>", '<image href="data:image/png;base64,AA=="/></svg>'),
                                    '<svg xmlns="http://www.w3.org/2000/svg"/>'):
                        (outputs / "figure.svg").write_text(invalid)
                        with self.assertRaisesRegex(ValueError, "editable|text, paths or shapes"):
                            namespace["main"]()
            missing = root / "missing-data"
            with self.assertRaisesRegex(ValueError, "only for diagram=True"):
                script_project.prepare_script_project(missing, None, goal)
            self.assertFalse(missing.exists())

    def test_scripted_analysis_reuses_code_task_and_protects_inputs_and_checker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "measurements.csv"
            data.write_text("method,cost,quality\na,1,3\nb,2,4\n", encoding="utf-8")
            config = self.prepare("--kind", "data_analysis", "--scripted", "--data-file", str(data),
                "--goal", "Draw a cost/performance trade-off with an inset", "--observation-unit", "one supplied method summary", "--output-root", str(root / "tasks"), "--prepare-only")
            defaults = research_defaults(["research-session", "--config", str(config)])
            self.assertEqual(defaults["task_kind"], "bug_fix")
            settings = tomllib.loads((config.parent / "code_task.toml").read_text())
            self.assertEqual(settings["edit_scope"]["allowed_patterns"], ["analysis.py", "src/**", "outputs/**"])
            self.assertIn("tests/**", settings["edit_scope"]["protected_patterns"])
            self.assertEqual((config.parent / "source/data/input.csv").read_bytes(), data.read_bytes())
            self.assertIn("Never infer pairing", (config.parent / "task.md").read_text())
            self.assertIn("one supplied method summary", (config.parent / "task.md").read_text())
            self.assertFalse((config.parent / "source/outputs").exists())

    def test_saved_delivery_reuse_keeps_draft_evidence_and_old_session(self):
        from simple_ar.core.capabilities import CapabilityRegistry
        from simple_ar.core.session import SessionController
        from simple_ar.cli.start import session_materials
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = SessionController.create(root / "original", session_id="original",
                topic="Measured comparison", registry=CapabilityRegistry())
            ref = session.store.write_json("delivery/report.json", {"status": "completed"})
            session.store.write_text("delivery/report_body.md", "# Comparison\nA draft.")
            session.store.write_json("delivery/report_experiment_evidence.json", {"measured": True})
            session.store.write_json("delivery/citation_map.json", {"schema_version": "citation_map.v1", "entries": []})
            session.manifest.state_refs["report"] = ref
            documents = session.store.write_json("sources/document_bundle.json", {
                "schema_version": "document_bundle.v1", "documents": [], "chunks": []})
            session.manifest.state_refs["documents"] = documents
            current_documents = session.store.write_json("sources/after-reading.json", {
                "schema_version": "document_bundle.v1", "documents": [], "chunks": []},
                kind="document_bundle", schema="document_bundle.v1")
            session.store.write_json("writing/report_inputs.json", {"sources": [current_documents.to_dict()]})
            session.manifest.state_refs["writer"] = session.store.write_json("writing/writer.json", {
                "input_snapshot": {"path": "report_inputs.json"}})
            code = session.store.write_json("implementation/code_analysis/analysis.json", {"schema_version": "code_analysis.v1"})
            implementation = session.store.write_json("implementation/implementation.json", {
                "status": "validated", "artifact_refs": {"code_analysis": {"path": "code_analysis/analysis.json"}},
                "workspace_dir": str(root / "retired/code_task/workspace"),
                "code_task_run_dir": str(root / "retired")})
            session.manifest.state_refs["implementation"] = implementation
            session.save()
            original = (session.store.root / "session_manifest.json").read_bytes()
            files = session_materials(session.store.root)["report"]
            self.assertEqual(session_materials(session.store.root)["code_analysis"], [session.store.resolve(code)])
            self.assertNotIn("code_project", session_materials(session.store.root))
            with self.assertRaisesRegex(ValueError, "No current reusable code_project"):
                self.prepare("--from-session", str(session.store.root), "--reuse", "code_project",
                    "--goal", "Revise the retired project", "--prepare-only")
            self.assertEqual(len(files), 4)
            self.assertIn(session.store.resolve(current_documents).resolve(), files)
            self.assertNotIn(session.store.resolve(documents).resolve(), files)
            config = self.prepare("--from-session", str(session.store.root), "--reuse", "report",
                "--goal", "Reorganize the argument and replace the chapter structure", "--output-root", str(root / "new"), "--prepare-only")
            defaults = research_defaults(["research-session", "--config", str(config)])
            self.assertEqual(defaults["task_kind"], "writing")
            self.assertEqual(defaults["report_outline_strategy"], "adaptive")
            self.assertEqual(defaults["report_draft_scope"], "document")
            self.assertIn("Reorganize the argument", config.read_text())
            self.assertEqual(set(defaults["material"]), {str(path) for path in files})
            self.assertEqual((session.store.root / "session_manifest.json").read_bytes(), original)
            with self.assertRaisesRegex(ValueError, "No current reusable"):
                self.prepare("--from-session", str(session.store.root), "--reuse", "data_analysis",
                    "--goal", "Explain", "--prepare-only")

    def test_table_options_share_owner_defaults_and_choices(self):
        from simple_ar.cli.research_config import FIELDS, data_options_supplied, setup_option_contract
        from simple_ar.result_analysis.table import TABLE_CHOICES, TableSpec
        parser = build_parser()
        args = parser.parse_args(["start", "--kind", "survey"])
        self.assertFalse(data_options_supplied(args))
        for name, default in TableSpec.defaults().items():
            destination = FIELDS["analysis"][name][0]
            self.assertEqual(getattr(args, destination), default)
        contract = setup_option_contract('data_analysis')
        for name, choices in TABLE_CHOICES.items():
            destination = FIELDS["analysis"][name][0]
            if destination in contract:
                self.assertEqual(set(contract[destination]['allowed_values']), set(choices))
        for flag, value in (("--data-mode", "values"), ("--data-max-mb", "0"),
                            ("--data-association", "pearson"), ("--group-column", "group")):
            with self.subTest(flag=flag):
                self.assertTrue(data_options_supplied(parser.parse_args(["start", flag, value])))

    def test_new_guided_survey_plans_document_from_evidence_without_changing_existing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.md'
            source.write_text('A supplied source, not a measured experiment.')
            config = self.prepare('--kind', 'survey', '--goal', 'Compare supplied evidence',
                '--document', str(source), '--sources', 'materials', '--output-root', str(root / 'runs'), '--prepare-only')
            defaults = research_defaults(['research-session', '--config', str(config)])
            self.assertEqual(defaults['report_outline_strategy'], 'adaptive')
            self.assertTrue(defaults['report_document_review'])
            self.assertEqual(defaults['report_draft_scope'], 'document')
            self.assertEqual(defaults['report_review_scope'], 'document')
            self.assertNotIn('command_argv', defaults)
            old = root / 'old.toml'
            old.write_text('[task]\ngoal="Review"\nkind="survey"\noutputs=["report"]\n[report]\noutline_strategy="template"\n')
            old_bytes = old.read_bytes()
            self.assertEqual(research_defaults(['research-session', '--config', str(old)])['report_outline_strategy'], 'template')
            self.assertEqual(old.read_bytes(), old_bytes)

    def test_reproduction_data_paths_use_common_configuration_and_cwd_preparation(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / 'paper.md'
            paper.write_text('A supplied conclusion.')
            data = root / 'data'
            data.mkdir()
            common = ['--kind', 'reproduction', '--goal', 'Check conclusion', '--document', str(paper),
                      '--cwd', str(root), '--data-path', str(data), '--data-path', str(data),
                      '--hypothesis', 'Declared claim', '--dataset', 'Explicit directory; split unspecified',
                      '--expected-outcome', 'Compare score', '--metric', 'score',
                      '--output-root', str(root / 'runs'), '--prepare-only']
            config = self.prepare(*common, '--command', 'python', 'run.py')
            defaults = research_defaults(['research-session', '--config', str(config)])
            self.assertEqual(defaults['data_path'], [str(data.resolve())])
            self.assertEqual(defaults['command_argv'], ['python', 'run.py'])
            facts = json.loads((config.parent / 'preparation.json').read_text())
            self.assertEqual(facts['data_paths'][0]['kind'], 'directory')
            self.assertIn('size does not establish', ' '.join(facts['limitations']))
            with self.assertRaisesRegex(ValueError, 'not found'):
                self.prepare(*common, '--data-path', str(root / 'absent'), '--command', 'python', 'run.py')
            with self.assertRaisesRegex(ValueError, 'requires --kind reproduction'):
                self.prepare('--kind', 'survey', '--goal', 'Survey', '--data-path', str(data), '--prepare-only')

    def test_default_writing_template_matches_supplied_material_not_failed_experiment(self):
        from simple_ar.report.schema import ReportRuntimeConfig
        from simple_ar.report.templates import load_report_template_bundle
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = root / "notes.md"
            material.write_text("# Notes\nSupplied observations, not a locally executed experiment.\n", encoding="utf-8")
            config = self.prepare("--kind", "writing", "--goal", "Explain notes", "--material", str(material),
                                  "--output-root", str(root / "runs"), "--prepare-only")
            defaults = research_defaults(["research-session", "--config", str(config)])
            self.assertEqual(defaults["report_template"], "material_report")
            self.assertEqual(defaults["report_draft_scope"], "document")
            self.assertEqual(defaults["report_review_scope"], "document")
            for genre in ("analysis_report", "source_review"):
                generated = self.prepare("--kind", "writing", "--goal", "Explain notes",
                    "--material", str(material), "--template", genre,
                    "--output-root", str(root / genre), "--prepare-only")
                settings = tomllib.loads(generated.read_text())["report"]
                self.assertEqual(settings["draft_scope"], "document")
                self.assertEqual(settings["review_scope"], "document")
            self.assertEqual(defaults["report_outline_strategy"], "adaptive")
            for name in ("", "auto", "material_report"):
                bundle = load_report_template_bundle(report_mode="supplied_materials", config=ReportRuntimeConfig(template=name))
                self.assertEqual(bundle.name, "material_report")
                self.assertIn("## Findings", bundle.template_markdown)
                self.assertTrue(bundle.criteria_markdown.strip())
            # Explicit existing choices and experimental fallback retain their meaning.
            self.assertEqual(load_report_template_bundle(report_mode="supplied_materials",
                config=ReportRuntimeConfig(template="analysis_report")).name, "analysis_report")
            self.assertEqual(load_report_template_bundle(report_mode="experiment",
                config=ReportRuntimeConfig(template="auto")).name, "experiment")

    def test_data_configuration_and_resume_hint_do_not_require_a_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data.csv"
            source.write_text("value\n1\n2\n", encoding="utf-8")
            args = build_parser().parse_args(["start", "--kind", "data_analysis", "--goal", "Describe",
                "--data-file", str(source), "--value-column", "value", "--observation-unit", "one row",
                "--model", "unused-model", "--output-root", str(root / "runs"), "--prepare-only"])
            with patch("sys.stdin.isatty", return_value=False), patch("simple_ar.cli.start.print_line") as output:
                config = prepare_start(args)
            self.assertEqual(research_defaults(["research-session", "--config", str(config)])["model"], "")
            resume_hint = next(call.args[0] for call in output.call_args_list if "resume its printed path" in call.args[0])
            self.assertNotIn("--model", resume_hint)
            self.assertIn("research-session --session-root PATH", resume_hint)

    def test_guided_custom_writing_template_keeps_its_topology(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = root / "notes.md"
            material.write_text("Supplied notes", encoding="utf-8")
            custom = root / "material_report.md"
            custom.write_text("# Custom\n## Findings\nUse supplied notes.\n## Conclusion\nExplain scope.", encoding="utf-8")
            config = self.prepare("--kind", "writing", "--goal", "Explain", "--material", str(material),
                                  "--template", str(custom), "--output-root", str(root / "runs"), "--prepare-only")
            settings = tomllib.loads(config.read_text(encoding="utf-8"))["report"]
            self.assertEqual(settings["template"], str(custom.resolve()))
            self.assertNotIn("outline_strategy", settings)
            self.assertNotIn("draft_scope", settings)
            self.assertNotIn("review_scope", settings)

    def test_resume_hint_preserves_the_selected_model(self):
        with tempfile.TemporaryDirectory() as directory:
            for model in ("env", "custom-model"):
                with self.subTest(model=model):
                    args = build_parser().parse_args(["start", "--kind", "survey", "--goal", "Review",
                        "--sources", "search", "--model", model, "--output-root", directory, "--prepare-only"])
                    with patch("sys.stdin.isatty", return_value=False), patch("simple_ar.cli.start.print_line") as output:
                        config = prepare_start(args)
                    self.assertEqual(research_defaults(["research-session", "--config", str(config)])["model"], model)
                    resume_hint = next(call.args[0] for call in output.call_args_list if "resume its printed path" in call.args[0])
                    self.assertIn(f"--model {model}", resume_hint)

    def test_data_setup_reports_columns_and_rejects_bad_shape_before_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data.csv"
            output = root / "runs"
            args = ["--kind", "data_analysis", "--goal", "Describe values",
                    "--data-file", str(source), "--value-column", "loss",
                    "--observation-unit", "one run", "--output-root", str(output), "--prepare-only"]
            source.write_text("value,group\n0.2,A\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Available columns.*value.*group"):
                self.prepare(*args)
            self.assertFalse(output.exists())
            source.write_text("loss,group\n0.2,A,extra\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expected 2"):
                self.prepare(*args)
            self.assertFalse(output.exists())
            source.write_bytes(b"loss\n\xff\n")
            with self.assertRaisesRegex(ValueError, "UTF-8"):
                self.prepare(*args)
            self.assertFalse(output.exists())

    def test_data_prepare_does_not_measure_or_infer_numeric_column_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data.csv"
            source.write_text("loss\nnot-a-number\n", encoding="utf-8")
            config = self.prepare("--kind", "data_analysis", "--goal", "Describe",
                                  "--data-file", str(source), "--value-column", "loss",
                                  "--observation-unit", "one row", "--output-root", str(root / "runs"),
                                  "--prepare-only")
            self.assertTrue(config.is_file())
            self.assertEqual(research_defaults(["research-session", "--config", str(config)])["value_column"], ["loss"])

    def test_shared_coordinate_start_roundtrips_explicit_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "paired.csv"
            source.write_text("step,A,B\n0,1,2\n1,2,3\n", encoding="utf-8")
            config = self.prepare("--kind", "data_analysis", "--goal", "Compare supplied coordinates",
                                  "--data-file", str(source), "--value-column", "A", "--value-column", "B",
                                  "--observation-unit", "one paired point", "--data-mode", "values",
                                  "--data-plot", "scatter", "--x-column", "step", "--value-unit", "points",
                                  "--series-layout", "shared", "--output-root", str(root / "runs"), "--prepare-only")
            defaults = research_defaults(["research-session", "--config", str(config)])
            self.assertEqual(defaults["series_layout"], "shared")
            self.assertEqual(defaults["value_column"], ["A", "B"])
            self.assertEqual(defaults["data_plot"], "scatter")

    def test_writing_accepts_parser_supported_html_in_both_material_roles(self):
        from simple_ar.cli.research_config import validate_session_arguments
        from simple_ar.research.documents.extractors import LocalDocumentParser
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for role in ("--document", "--material"):
                for suffix in (".html", ".htm", ".HTML"):
                    with self.subTest(role=role, suffix=suffix):
                        material = root / ("source" + suffix)
                        text = "<html><body><h1>Published source</h1><p>Original conditions remain readable.</p></body></html>"
                        material.write_text(text, encoding="utf-8")
                        config = self.prepare("--kind", "writing", "--goal", "Compare the supplied sources",
                            role, str(material), "--output-root", str(root / "runs"), "--prepare-only")
                        values = research_defaults(["research-session", "--config", str(config)])
                        self.assertEqual(values["outputs"], ["report"])
                        self.assertEqual(values["local_document" if role == "--document" else "material"], [str(material.resolve())])
                        args = build_parser(research_defaults=values).parse_args(["research-session", "--config", str(config)])
                        validate_session_arguments(args)
                        self.assertIn("Original conditions remain readable", LocalDocumentParser().parse(material).text)
                        self.assertEqual(material.read_text(encoding="utf-8"), text)

    def test_writing_roundtrips_material_role_template_and_no_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = root / "results with spaces.markdown"
            material.write_text("# Results\nExternal result, not independently verified.\n", encoding="utf-8")
            config = self.prepare("--kind", "writing", "--goal", "Write an honest paper draft",
                                  "--material", str(material), "--template", "experiment",
                                  "--output-root", str(root / "runs"), "--prepare-only")
            defaults = research_defaults(["research-session", "--config", str(config)])
            self.assertEqual(defaults["task_kind"], "writing")
            self.assertEqual(defaults["material"], [str(material.resolve())])
            self.assertEqual(defaults["report_template"], "experiment")
            self.assertEqual(defaults["report_outline_strategy"], "adaptive")
            self.assertTrue(defaults["report_document_review"])
            self.assertEqual(defaults["outputs"], ["report"])
            self.assertNotIn("command_argv", defaults)
            self.assertNotIn("total_tokens", defaults)

    def test_writing_invalid_scope_does_not_save_a_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = root / "notes.md"
            material.write_text("Notes", encoding="utf-8")
            invalid = root / 'invalid.json'
            invalid.write_text('{"unfinished":', encoding='utf-8')
            for extra in (["--sources", "search"], ["--document", str(material)], ["--fulltext"],
                          ["--command", "python", "run.py"], ["--material", str(invalid)]):
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    self.prepare("--kind", "writing", "--goal", "Write from material", "--material", str(material),
                                 "--output-root", str(root / "runs"), "--prepare-only", *extra)
            self.assertFalse((root / "runs").exists())

    def test_writing_checks_template_before_saving_or_calling_a_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = root / "notes.md"
            material.write_text("Notes", encoding="utf-8")
            empty_template = root / "empty.md"
            empty_template.write_text("", encoding="utf-8")
            for template in ("experimnt", str(root / "missing.md"), str(empty_template)):
                with self.subTest(template=template), self.assertRaisesRegex(ValueError, "Invalid writing template"):
                    self.prepare("--kind", "writing", "--goal", "Draft", "--material", str(material),
                                 "--template", template, "--output-root", str(root / "runs"), "--prepare-only")
            self.assertFalse((root / "runs").exists())

    def prepare(self, *arguments):
        args = build_parser().parse_args(["start", *arguments])
        with patch("sys.stdin.isatty", return_value=False), contextlib.redirect_stdout(io.StringIO()):
            return prepare_start(args)

    def test_local_survey_roundtrips_canonical_config_without_api_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "材料 one's source.md"
            paper.write_text("# A source\n", encoding="utf-8")
            config = self.prepare("--kind", "survey", "--goal", '比较 "A" 和 B\n说明局限',
                                  "--document", str(paper), "--sources", "materials",
                                  "--output-root", str(root / "runs"), "--prepare-only")
            defaults = research_defaults(["research-session", "--config", str(config)])
            self.assertEqual(defaults["task_kind"], "survey")
            self.assertEqual(defaults["local_document"], [str(paper.resolve())])
            self.assertTrue(defaults["research_materials_only"])
            self.assertEqual(defaults["output_root"], str((config.parent / "sessions").resolve()))
            self.assertEqual(defaults["model"], "env")
            self.assertNotIn("total_tokens", defaults)
            self.assertNotIn("execution_details", defaults)

    def test_code_inputs_use_existing_loader_and_protect_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project with spaces"
            project.mkdir()
            config = self.prepare("--kind", "bug_fix", "--goal", "Fix the rounding error",
                                  "--project", str(project), "--allow", "src/**",
                                  "--validate", "python -m unittest discover -s tests",
                                  "--output-root", str(root / "runs"), "--prepare-only")
            defaults = research_defaults(["research-session", "--config", str(config)])
            code_config = Path(defaults["code_task_config"])
            options = load_code_task_init_options(config_path=code_config)
            execution = load_code_task_execute_options(config_path=code_config)
            self.assertEqual(Path(options.code_root), project.resolve())
            self.assertEqual(options.workspace_mode, "auto")
            self.assertEqual(options.env_mode, "current")
            self.assertEqual(options.edit_scope_allowed_patterns, ("src/**",))
            self.assertIn("tests/**", options.edit_scope_protected_patterns)
            self.assertTrue(execution.use_llm)
            self.assertEqual(execution.baseline_policy, "skip")
            self.assertEqual(Path(options.task_file).read_text(encoding="utf-8"), "Fix the rounding error\n")
            self.assertEqual(list(project.iterdir()), [])
            interpreter = root / "environment with spaces" / "python"
            interpreter.parent.mkdir()
            interpreter.touch()  # Input check only; prepare-only must not execute it.
            external = self.prepare("--kind", "bug_fix", "--goal", "Fix with the existing environment",
                "--project", str(project), "--project-python", str(interpreter), "--allow", "src/**",
                "--validate", "python -m unittest", "--output-root", str(root / "external-runs"), "--prepare-only")
            external_defaults = research_defaults(["research-session", "--config", str(external)])
            external_options = load_code_task_init_options(config_path=external_defaults["code_task_config"])
            self.assertEqual(external_options.env_mode, "external")
            self.assertEqual(Path(external_options.python_executable), interpreter.absolute())
            self.assertEqual(external_options.edit_scope_allowed_patterns, options.edit_scope_allowed_patterns)
            self.assertEqual(external_options.edit_scope_protected_patterns, options.edit_scope_protected_patterns)
            self.assertEqual(list(project.iterdir()), [])
            if os.name != "nt":
                alias = interpreter.parent / "venv-python"
                alias.symlink_to(interpreter)
                linked = self.prepare("--kind", "bug_fix", "--goal", "Use the chosen venv",
                    "--project", str(project), "--project-python", str(alias), "--allow", "src/**",
                    "--validate", "python -m unittest", "--output-root", str(root / "linked-runs"), "--prepare-only")
                linked_defaults = research_defaults(["research-session", "--config", str(linked)])
                linked_options = load_code_task_init_options(config_path=linked_defaults["code_task_config"])
                self.assertEqual(Path(linked_options.python_executable), alias.absolute())
                self.assertNotEqual(Path(linked_options.python_executable), alias.resolve())
            for flags, expected in ((["--kind", "writing", "--project-python", str(interpreter)], "requires --kind bug_fix"),
                                    (["--kind", "bug_fix", "--project-python", str(root / "missing")], "not found")):
                with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, expected):
                    self.prepare(*flags, "--prepare-only", "--output-root", str(root / "refused"))
                self.assertFalse((root / "refused").exists())

    @unittest.skipUnless(shutil.which("git"), "Git is required for workspace isolation")
    def test_guided_code_workspace_keeps_large_committed_source_and_dirty_changes(self):
        from simple_ar.code_task.workspace.modes import WorkspaceSpec, create_workspace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            source = project / "generated.py"
            source.write_text("# generated source\n" + "# retained\n" * 190000, encoding="utf-8")
            for command in (("init",), ("config", "user.name", "Workspace Test"),
                            ("config", "user.email", "test@example.invalid"),
                            ("add", "."), ("commit", "-m", "fixture")):
                subprocess.run(["git", "-C", str(project), *command],
                               check=True, capture_output=True)
            config = self.prepare("--kind", "bug_fix", "--goal", "Keep project behavior",
                                  "--project", str(project), "--allow", "*.py",
                                  "--validate", "python -m unittest", "--output-root", str(root / "runs"),
                                  "--prepare-only")
            code_config = Path(research_defaults(["research-session", "--config", str(config)])["code_task_config"])
            options = load_code_task_init_options(config_path=code_config)
            clean = create_workspace(WorkspaceSpec(code_root=project, task_dir=root / "clean",
                                                   mode=options.workspace_mode,
                                                   max_file_bytes=options.max_file_bytes))
            self.assertEqual(clean.mode, "git_worktree")
            self.assertGreater(source.stat().st_size, options.max_file_bytes)
            self.assertEqual((clean.project_root / source.name).stat().st_size, source.stat().st_size)
            changed = project / "user_changes.py"
            changed.write_text("VALUE = 7\n", encoding="utf-8")
            dirty = create_workspace(WorkspaceSpec(code_root=project, task_dir=root / "dirty",
                                                   mode=options.workspace_mode,
                                                   max_file_bytes=options.max_file_bytes))
            self.assertEqual(dirty.mode, "copy")
            self.assertIn("uncommitted", dirty.fallback_reason)
            self.assertEqual((dirty.project_root / changed.name).read_text(), changed.read_text())
            self.assertTrue(any(item["path"] == source.name and item["reason"] == "file_too_large"
                                for item in dirty.copy_report.skipped))
            self.assertEqual(changed.read_text(), "VALUE = 7\n")

    def test_fulltext_is_explicit_and_maps_to_existing_retrieval_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            for flags, expected in (([], False), (["--fulltext"], True)):
                config = self.prepare("--kind", "survey", "--goal", "Compare methods", "--sources", "search",
                                      "--output-root", directory, "--prepare-only", *flags)
                values = research_defaults(["research-session", "--config", str(config)])
                self.assertEqual(values["research_use_fulltext"], expected)
                self.assertEqual(values["research_allow_pdf_download"], expected)
                self.assertEqual(values["research_keep_raw_pdf"], expected)
                if expected:
                    self.assertEqual(values["research_max_fulltext_documents"], 6)
                    self.assertEqual(values["research_max_pdf_mb"], 20)
                else:
                    self.assertNotIn("research_max_fulltext_documents", values)
                    self.assertNotIn("research_max_pdf_mb", values)
            with self.assertRaisesRegex(ValueError, "bug_fix does not consume"):
                self.prepare("--kind", "bug_fix", "--goal", "Fix", "--fulltext", "--prepare-only")

    def test_explicit_report_source_bound_roundtrips_without_affecting_other_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.prepare("--kind", "survey", "--goal", "Compare evidence across fields",
                                  "--sources", "search", "--max-cited-sources", "3",
                                  "--output-root", directory, "--prepare-only")
            values = research_defaults(["research-session", "--config", str(config)])
            self.assertEqual(values["report_max_cited_sources"], 3)
            with self.assertRaisesRegex(ValueError, "positive"):
                self.prepare("--kind", "survey", "--goal", "Compare evidence", "--sources", "search",
                             "--max-cited-sources", "0", "--output-root", directory, "--prepare-only")

    def test_bad_inputs_create_no_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "runs"
            for extra in (
                ["--kind", "survey", "--sources", "materials"],
                ["--kind", "survey", "--sources", "search", "--allow", "src/**"],
                ["--kind", "bug_fix", "--project", directory, "--validate", "python tests.py", "--allow", "../*"],
                ["--kind", "survey"],
            ):
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    self.prepare("--goal", "task", "--prepare-only", "--output-root", str(root), *extra)
                self.assertFalse(root.exists())

    def test_noninteractive_requires_execution_confirmation_but_keeps_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "--yes"):
                self.prepare("--kind", "survey", "--goal", "task", "--sources", "search", "--output-root", directory)
            configs = list(Path(directory).glob("*/research.toml"))
            self.assertEqual(len(configs), 1)
            self.assertEqual(tomllib.loads(configs[0].read_text())["task"]["goal"], "task")

    def test_interactive_decline_saves_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(["start", "--output-root", directory])
            with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=[
                "survey", "Review this direction", "", "search", "n",
            ]), contextlib.redirect_stdout(io.StringIO()):
                self.assertIsNone(prepare_start(args))
            self.assertEqual(len(list(Path(directory).glob("*/research.toml"))), 1)

    def test_explicit_source_scope_never_prompts_for_optional_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(["start", "--kind", "survey", "--goal", "task",
                "--sources", "search", "--yes", "--output-root", directory])
            with patch("sys.stdin.isatty", return_value=True), patch("builtins.input") as prompt, \
                    contextlib.redirect_stdout(io.StringIO()):
                config = prepare_start(args)
            self.assertTrue(config.is_file())
            prompt.assert_not_called()

    def test_interactive_number_selects_existing_writing_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            material = Path(directory) / "notes.md"
            material.write_text("# Supplied observations\n", encoding="utf-8")
            args = build_parser().parse_args(["start", "--material", str(material),
                "--goal", "Summarize supplied notes", "--prepare-only", "--output-root", directory])
            output = io.StringIO()
            with patch("sys.stdin.isatty", return_value=True), \
                    patch("builtins.input", side_effect=["4"]), contextlib.redirect_stdout(output):
                config = prepare_start(args)
            self.assertEqual(tomllib.loads(config.read_text())["task"]["kind"], "writing")
            self.assertIn("no API needed", output.getvalue())
            self.assertIn("confirmed command and environment", output.getvalue())

    def test_invalid_interactive_selection_creates_no_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "output"
            args = build_parser().parse_args(["start", "--output-root", str(root)])
            with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="99"), \
                    contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "Choose"):
                prepare_start(args)
            self.assertFalse(root.exists())

    def test_main_dispatches_only_to_existing_session_cli(self):
        from simple_ar.cli.main import main
        with tempfile.TemporaryDirectory() as directory, patch("sys.stdin.isatty", return_value=False), \
                patch("simple_ar.cli.main._print_research_session") as run, contextlib.redirect_stdout(io.StringIO()):
            main(["start", "--kind", "survey", "--goal", "task", "--sources", "search",
                  "--output-root", directory, "--yes"])
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0].task_kind, "survey")
            self.assertEqual(run.call_args.args[0].outputs, ["report"])

    def test_prepared_reproduction_uses_existing_config_and_preserves_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "source with spaces.md"
            paper.write_text("# Published conclusion\n", encoding="utf-8")
            config = self.prepare("--kind", "reproduction", "--goal", "Check a published conclusion",
                "--document", str(paper), "--hypothesis", "Published claim, not a new method",
                "--dataset", "User-prepared adapted data", "--expected-outcome", "Compare coverage with 0.9",
                "--metric", "coverage", "--metric", "mc_error", "--cwd", directory, "--timeout-sec", "12",
                "--output-files", '{"raw":"measurements.json","setup":"summary.json"}',
                "--metric-sources", '{"coverage":{"output":"raw","path":["scores",0,"coverage"]}}',
                "--max-cited-sources", "1", "--output-root", str(root / "runs"), "--prepare-only",
                "--command", "python", "a script.py", "--label", "one value")
            values = research_defaults(["research-session", "--config", str(config)])
            self.assertEqual(values["task_kind"], "reproduction")
            self.assertEqual(values["outputs"], ["experiments", "report"])
            self.assertEqual(values["command_argv"], ["python", "a script.py", "--label", "one value"])
            self.assertEqual(values["cwd"], str(root.resolve()))
            self.assertEqual(values["execution_details"]["baseline_policy"], "skip")
            self.assertEqual(values["execution_details"]["protocol"]["dataset"], "User-prepared adapted data")
            self.assertEqual(values["process_invocations"], 1)
            self.assertEqual(values["process_wall_seconds"], 12)
            self.assertEqual(values["report_template"], "reproduction")
            self.assertEqual(values["report_draft_scope"], "document")
            self.assertEqual(values["report_review_scope"], "document")
            self.assertEqual(values["report_outline_strategy"], "adaptive")
            self.assertEqual(values["execution_details"]["output_files"], {"raw": "measurements.json", "setup": "summary.json"})
            self.assertEqual(values["execution_details"]["metric_sources"], {"coverage": {"output": "raw", "path": ["scores", 0, "coverage"]}})
            self.assertTrue(values["report_document_review"])
            self.assertEqual(values["report_max_cited_sources"], 1)
            self.assertNotIn("total_tokens", values)
            self.assertFalse(values["research_allow_pdf_download"])
            self.assertFalse((config.parent / "code_task.toml").exists())
            adapter = self.prepare("--kind", "reproduction", "--goal", "Export author results without changing the method",
                "--document", str(paper), "--hypothesis", "Published claim", "--dataset", "Fixed data",
                "--expected-outcome", "Measure coverage", "--metric", "coverage", "--project", directory,
                "--data-path", directory, "--allow", "adapter.py", "--validate", "python tests/check_adapter.py",
                "--output-root", str(root / "adapted-runs"), "--prepare-only", "--command", "python", "adapter.py")
            prepared = research_defaults(["research-session", "--config", str(adapter)])
            self.assertEqual(prepared["command_argv"], ["python", "adapter.py"])
            self.assertEqual(prepared["process_invocations"], 3)
            self.assertEqual(load_code_task_init_options(config_path=adapter.parent / "code_task.toml").benchmark_command,
                             "python tests/check_adapter.py")
            self.assertIn("do not alter methods", (adapter.parent / "task.md").read_text())

    def test_invalid_reproduction_and_cross_function_options_create_no_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "paper.md"
            paper.write_text("source", encoding="utf-8")
            output = Path(directory) / "runs"
            base = ["--kind", "reproduction", "--goal", "Check", "--document", str(paper),
                "--hypothesis", "Claim", "--dataset", "Data", "--expected-outcome", "Criterion",
                "--metric", "coverage", "--output-root", str(output), "--prepare-only"]
            for flags in (["--sources", "search"], ["--fulltext"], ["--timeout-sec", "0"],
                          ["--cwd", str(Path(directory) / "missing")], ["--metric", "coverage"],
                          ["--project", str(Path(directory) / "missing-project")],
                          ["--output-files", '{"raw":"../outside.json"}'], ["--output-files", '[]']):
                with self.subTest(flags=flags), self.assertRaises(ValueError):
                    self.prepare(*base, *flags, "--command", "python", "run.py")
                self.assertFalse(output.exists())
            for command in ([], [""]):
                with self.subTest(command=command), self.assertRaises(ValueError):
                    self.prepare(*base, "--command", *command)
                self.assertFalse(output.exists())
            with self.assertRaisesRegex(ValueError, "Missing input"):
                self.prepare("--kind", "reproduction", "--goal", "Check", "--document", str(paper),
                    "--output-root", str(output), "--prepare-only")
            with self.assertRaisesRegex(ValueError, "require --kind reproduction"):
                self.prepare("--kind", "survey", "--goal", "Read", "--sources", "search", "--prepare-only",
                    "--output-root", str(output), "--command", "python", "run.py")
            self.assertFalse(output.exists())

    def test_interactive_reproduction_asks_for_protocol_not_a_research_algorithm(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "paper.md"
            paper.write_text("source", encoding="utf-8")
            args = build_parser().parse_args(["start", "--kind", "reproduction", "--goal", "Check",
                "--document", str(paper), "--output-root", directory, "--prepare-only"])
            with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=[
                    "Published claim", "Existing data", "Value near 0.9", '["python", "run.py"]', "coverage",
                    ]), contextlib.redirect_stdout(io.StringIO()):
                config = prepare_start(args)
            values = research_defaults(["research-session", "--config", str(config)])
            self.assertEqual(values["command_argv"], ["python", "run.py"])
            self.assertEqual(values["timeout_sec"], 300)
            self.assertEqual(values["metric"], ["coverage"])
            self.assertTrue(values["research_materials_only"])


if __name__ == "__main__":
    unittest.main()
