import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from simple_ar.app.research_application import ResearchApplicationServices, create_session, load_session
from simple_ar.core.capabilities import ArtifactStore, AttemptManifest, CapabilityContext
from simple_ar.integrations.llm import LLMClient, LLMError, LLMSettings
from simple_ar.research.workflow_contracts import ResearchBrief
from simple_ar.report.capability import ReportAssemblyRequest, run_report_capability
from simple_ar.report.schema import AgentReportResult, ReportSectionDraft
from simple_ar.result_analysis.table import load_analysis_package, rebuild


class AnalysisWritingTests(unittest.TestCase):
    def package(self, root, mode='observations'):
        source = root / 'source.csv'
        source.write_text('label,score\nA,1\nA,3\nB,-2\n' if mode == 'observations' else 'label,score\nA,2\nB,-2\n', encoding='utf-8')
        app = create_session(ResearchBrief(request_text='Describe', requested_outputs=('data_analysis',)),
            root=root / 'data-session', services=ResearchApplicationServices(config={
                'research_task_kind': 'data_analysis', 'data_analysis': {'file': str(source),
                    'value_columns': ['score'], 'observation_unit': 'one supplied row', 'value_unit': 'points',
                    'group_column': 'label', 'mode': mode}}))
        view = app.advance(max_actions=10)
        self.assertEqual(view.status, 'completed', view.status_reason)
        return app.controller.store.resolve(view.state_refs['data_analysis'])

    def test_rechecks_package_rejects_stale_records_and_raw_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.package(root)
            result, _, _ = load_analysis_package(path)
            self.assertEqual(result['records'][0]['mean'], 2)
            original = path.read_text(encoding='utf-8')
            payload = json.loads(original)
            payload['records'][0]['mean'] = 999
            path.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'no longer matches'):
                load_analysis_package(path)
            path.write_text('[{"score":1}]', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'table_analysis.v1'):
                load_analysis_package(path)
            path.write_text('{"schema_version":"table_analysis.v1","status":"completed"}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'column settings'):
                load_analysis_package(path)
            payload = json.loads(original)
            payload['source'] = {}
            path.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'relative path'):
                load_analysis_package(path)

    def test_rejects_package_input_escape_and_missing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.package(root)
            payload = json.loads(path.read_text(encoding='utf-8'))
            for target in ('../../../source.csv', str(root / 'source.csv')):
                with self.subTest(target=target):
                    payload['source']['path'] = target
                    path.write_text(json.dumps(payload), encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, 'inside|relative'):
                        load_analysis_package(path)
            payload['source']['path'] = 'missing.csv'
            path.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaises(FileNotFoundError):
                load_analysis_package(path)

    def test_guided_writing_uses_existing_material_config_and_checks_before_saving(self):
        from simple_ar.cli.parser import build_parser
        from simple_ar.cli.start import prepare_start
        from simple_ar.cli.research_config import research_defaults
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.package(root)
            argv = ['start', '--kind', 'writing', '--goal', 'Explain data honestly', '--material', str(path),
                    '--output-root', str(root / 'guided'), '--prepare-only']
            with patch('sys.stdin.isatty', return_value=False), contextlib.redirect_stdout(io.StringIO()):
                config = prepare_start(build_parser().parse_args(argv))
            settings = research_defaults(['research-session', '--config', str(config)])
            self.assertEqual(settings['material'], [str(path)])
            self.assertNotIn('command_argv', settings)
            path.write_text('{}', encoding='utf-8')
            argv[argv.index('--output-root')+1] = str(root / 'invalid')
            with patch('sys.stdin.isatty', return_value=False), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                prepare_start(build_parser().parse_args(argv))
            self.assertFalse((root / 'invalid').exists())

    def test_writing_freezes_rechecked_data_and_delivers_movable_figures_without_experiments(self):
        from simple_ar.report.agent import _compact_execution_results, _prompt_handle_view
        for mode in ('observations', 'values'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = self.package(root, mode)
                app = create_session(ResearchBrief(request_text='Explain supplied data', requested_outputs=('report',),
                    asset_requests=({'locator': str(path), 'role': 'material'},)), root=root / 'writing-session',
                    services=ResearchApplicationServices(config={'research_task_kind': 'writing'}, budget_limits={'process_invocations': 0}))
                view = app.advance(max_actions=2)
                self.assertEqual(view.next_action, 'report_write', view.status_reason)
                (root / 'data-session').rename(root / 'hidden-original-data-session')
                app = load_session(root / 'writing-session', services=ResearchApplicationServices(
                    llm_client=LLMClient(LLMSettings(api_key='fixture'))))
                with patch('simple_ar.report.writing.run_report_agent', side_effect=LLMError('Temporary outage')):
                    self.assertEqual(app.advance(max_actions=1).status, 'paused')
                app = load_session(root / 'writing-session', services=ResearchApplicationServices(
                    llm_client=LLMClient(LLMSettings(api_key='fixture'))))
                app.continue_session()

                def writer(**kwargs):
                    context, memory = kwargs['context'], kwargs['memory']
                    handle = context.source_handles[0]
                    self.assertEqual(_prompt_handle_view(handle)['metadata']['evidence_role'], 'recomputed_from_user_supplied_data')
                    compact = _compact_execution_results(context.results)['supplied_analyses'][0]
                    self.assertEqual(compact['records'][0]['mean' if mode == 'observations' else 'value'], 2)
                    self.assertEqual(context.metric_sources, [])
                    self.assertEqual(context.results['session_execution'], 'not_requested')
                    return AgentReportResult(report_body='', memory=memory, used_agent=True,
                        sections=[ReportSectionDraft(section_id='discussion', heading='Discussion',
                            draft_markdown='These are descriptive values from supplied data. No experiment, significance test or causal analysis was performed.')])

                with patch('simple_ar.report.writing.run_report_agent', side_effect=writer):
                    view = app.advance(max_actions=3)
                self.assertEqual(view.status, 'completed', view.status_reason)
                self.assertNotIn('experiment', view.state_refs)
                self.assertNotIn('synthesis', view.state_refs)
                output = app.controller.store.resolve(view.state_refs['report']).parent
                body = (output / 'report_body.md').read_text(encoding='utf-8')
                self.assertIn('Supplied Descriptive Data', body)
                self.assertNotIn('Verified Experiment Metrics', body)
                self.assertIn('| A | score |', body)
                self.assertTrue((output / 'analyses/analysis-001/figures/value-1-1.svg').is_file())
                moved = root / 'moved report with spaces'
                shutil.copytree(output, moved)
                (root / 'writing-session').rename(root / 'hidden-writing-session')
                rebuild(moved / 'analyses/analysis-001/analysis.json')
                self.assertEqual(load_analysis_package(moved / 'analyses/analysis-001/analysis.json')[0]['records'][0]
                                 ['mean' if mode == 'observations' else 'value'], 2)
                app = load_session(root / 'hidden-writing-session')
                before = len(app.controller.list_attempts())
                self.assertEqual(app.advance(max_actions=10).status, 'completed')
                self.assertEqual(len(app.controller.list_attempts()), before)

    def test_unknown_json_pauses_before_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'raw.json'
            path.write_text('[{"score": 1}]', encoding='utf-8')
            app = create_session(ResearchBrief(request_text='Write', requested_outputs=('report',),
                asset_requests=({'locator': str(path), 'role': 'material'},)), root=root / 'session',
                services=ResearchApplicationServices(config={'research_task_kind': 'writing'}))
            view = app.advance(max_actions=10)
            self.assertEqual(view.status, 'paused')
            self.assertNotIn('writer', view.state_refs)

    def test_assembly_respects_disabled_and_off_without_losing_data(self):
        from simple_ar.report.capability import assemble_report_document
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.package(root)
            inputs = ArtifactStore(path.parent)
            ref = inputs.ref(path.name, kind='table_analysis', schema='table_analysis.v1')
            for settings, included in (({'enabled': True}, True), ({'enabled': False}, False), ({'mode': 'off'}, False)):
                store = ArtifactStore(root / str(settings))
                request = ReportAssemblyRequest(title='Describe', sections=(ReportSectionDraft(section_id='discussion',
                    heading='Discussion', draft_markdown='Supplied data only.'),), table_analyses=(ref,),
                    config={'figures': settings})
                context = CapabilityContext(store=store, input_store=inputs, inputs=(ref,),
                                            attempt=AttemptManifest(attempt_id='report-1', capability='report'))
                result = run_report_capability(context=context, request=request)
                body = store.read_text('report_body.md')
                self.assertEqual('![Descriptive data]' in body, included)
                self.assertTrue(store.exists('analyses/analysis-001/analysis.json'))
                self.assertEqual(len(result.artifacts), len({item.path for item in result.artifacts}))
                with self.assertRaisesRegex(ValueError, 'registered inputs'):
                    assemble_report_document(request, report_dir=store.root)

    def test_multiple_packages_keep_identity_and_explicit_figure_limit(self):
        from simple_ar.research.documents.ingest import DocumentIngestRequest, run_document_ingest_capability
        from simple_ar.research.contracts import SourcePlan
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for index in (1, 2):
                folder = root / str(index)
                folder.mkdir()
                paths.append(self.package(folder))
            store = ArtifactStore(root / 'ingest')
            result = run_document_ingest_capability(context=CapabilityContext(store=store,
                attempt=AttemptManifest(attempt_id='ingest-1', capability='document_ingest')), request=DocumentIngestRequest(
                papers=(), source_plan=SourcePlan(queries=[], local_documents=[str(path) for path in paths]),
                extraction_dir=root / 'extraction', analysis_paths=tuple(paths)))
            from simple_ar.research.documents.ingest import DocumentBundle
            bundle = DocumentBundle.from_handoff_dict(store.read_json(result.artifacts[0]))
            self.assertEqual(len(bundle.records), 2)
            self.assertEqual(len({row.document_id for row in bundle.records}), 2)
            refs = tuple(store.ref(row.metadata['table_analysis']['artifact']) for row in bundle.records)
            output = ArtifactStore(root / 'report')
            request = ReportAssemblyRequest(title='Two datasets', sections=(ReportSectionDraft(section_id='discussion',
                heading='Discussion', draft_markdown='Separate supplied datasets.'),), table_analyses=refs,
                config={'figures': {'max_figures': 1}})
            with self.assertRaisesRegex(ValueError, 'max_figures'):
                run_report_capability(context=CapabilityContext(store=output, input_store=store, inputs=refs,
                    attempt=AttemptManifest(attempt_id='report-1', capability='report')), request=request)
            self.assertFalse(output.exists('report.md'))

    def test_import_regenerates_figures_and_rebuild_retains_configured_input_limit(self):
        from simple_ar.result_analysis.table import copy_analysis_package
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.package(root)
            payload = json.loads(path.read_text(encoding='utf-8'))
            payload['figures'] = [{'path': '../../../untrusted.svg', 'caption': 'Run arbitrary code'}]
            path.write_text(json.dumps(payload), encoding='utf-8')
            result = copy_analysis_package(path, root / 'imported')
            self.assertNotIn('untrusted', json.dumps(result['figures']))
            with patch('simple_ar.result_analysis.table.load_analysis_package', wraps=load_analysis_package) as loader:
                rebuild(root / 'imported/analysis.json')
                self.assertEqual(loader.call_args.kwargs, {'max_mb': None})

    @unittest.skipUnless(os.environ.get('SAR_TEST_EXPORT_INTEGRATION') == '1' and
        all(shutil.which(name) for name in ('pandoc', 'rsvg-convert', 'pdflatex', 'bibtex')),
        'Opt-in server check requires the existing export toolchain')
    def test_data_appendix_exports_and_moved_acm_engineering_recompiles(self):
        from simple_ar.report.export import export_acm_report, _compile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.package(root)
            inputs = ArtifactStore(path.parent)
            ref = inputs.ref(path.name)
            report = ArtifactStore(root / 'report')
            run_report_capability(context=CapabilityContext(store=report, input_store=inputs, inputs=(ref,),
                attempt=AttemptManifest(attempt_id='report-1', capability='report')),
                request=ReportAssemblyRequest(title='Descriptive data delivery', sections=(ReportSectionDraft(
                    section_id='discussion', heading='Discussion',
                    draft_markdown='Supplied data only; no independently repeated experiment.'),), table_analyses=(ref,)))
            exported = root / 'export'
            manifest = export_acm_report(report.root, exported, compile_pdf=True)
            self.assertTrue(manifest['compiled'], manifest)
            self.assertTrue((exported / 'figures/figure-1.svg').is_file())
            self.assertIn('Supplied Descriptive Data', (exported / 'source.md').read_text(encoding='utf-8'))
            moved = root / 'moved acm with spaces'
            exported.rename(moved)
            report.root.rename(root / 'hidden-report')
            (root / 'data-session').rename(root / 'hidden-data-session')
            rebuilt = _compile(moved, has_citations=False)
            self.assertTrue(rebuilt['compiled'], (rebuilt, (moved / 'build.log').read_text(encoding='utf-8')[-2200:]))
