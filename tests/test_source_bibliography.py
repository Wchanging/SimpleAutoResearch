"""Source-local bibliography adoption, material writing and saved recovery."""
import copy
import unittest
import json

from unittest.mock import Mock, patch
from simple_ar.literature.models import Paper, bibliographic_details
from simple_ar.research.contracts import DocumentRecord, DocumentSection, TextChunk
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.research.evidence.bibliography import apply_bibliographic_note, front_matter_view
from simple_ar.research.evidence.reader import ReadRequest, ReadResult, read_documents
from simple_ar.core.capabilities import ArtifactRef
from simple_ar.report.projection import attach_report_read_evidence, _paper_source_handles, build_material_report_inputs, apply_report_bibliography, bibliography_planning_views
from simple_ar.report.schema import ReportContext, ReportMemory, ReportRuntimeConfig, ReportSectionPlan, SourceHandle
from simple_ar.report.citations import references_markdown
from simple_ar.literature.bibtex import papers_to_bibtex
from simple_ar.research.sources.capability import provided_materials_result
from simple_ar.research.evidence.screening import _paper_screening_record
from types import SimpleNamespace
from simple_ar.integrations.llm import LLMError
from simple_ar.report.agent import run_report_agent, _outline_planner_prompt
from simple_ar.report.templates import load_report_template_bundle
from simple_ar.report.tool_gateway import ReportToolGateway


class SourceBibliographyTests(unittest.TestCase):
    def test_local_filename_placeholder_is_not_presented_as_known_paper_title(self):
        local = DocumentRecord('doc', 'input paper', 'local_files', source_id='/private/input_paper.pdf')
        self.assertEqual(_paper_screening_record(local.to_row(), 1)['title_role'],
                         'filename_placeholder_not_publication_title')
        supplied = {**local.to_row(), 'title': 'Actual user title'}
        self.assertEqual(_paper_screening_record(supplied, 1)['title_role'], 'supplied_metadata')
        provider = {**local.to_row(), 'metadata': {'paper_id': 'provider'}}
        self.assertEqual(_paper_screening_record(provider, 1)['title_role'], 'supplied_metadata')

    def setUp(self):
        self.record = DocumentRecord('doc', 'input paper', 'local_files', source_id='/private/input_paper.pdf',
            url='/private/input_paper.pdf', extraction_status='parsed')
        self.header = DocumentSection('front', 'doc', 'front_matter', 'Front matter',
            'A source title\nAda Example, Grace Example\n2025-04-03\nhttps://example.org/study\nDOI 10.1234/study')
        self.paper = provided_materials_result([self.record]).papers[0].to_row()
        self.fields = [{'field': field, 'value': value, 'section_id': 'front', 'quote': quote, 'complete': True}
            for field, value, quote in (
                ('title', 'A source title', 'A source title'),
                ('authors', ['Ada Example', 'Grace Example'], 'Ada Example, Grace Example'),
                ('published', '2025-04-03', '2025-04-03'),
                ('url', 'https://example.org/study', 'https://example.org/study'),
                ('doi', '10.1234/study', 'DOI 10.1234/study'))]
        self.note = {'paper_id': 'doc', 'title': 'A source title', 'bibliographic_fields': self.fields}

    def project(self, **kwargs):
        return apply_bibliographic_note(kwargs.get('paper', self.paper), kwargs.get('record', self.record),
            kwargs.get('section', self.header), kwargs.get('note', self.note))

    def test_same_source_fields_fill_missing_values_with_locators_and_no_identity_claim(self):
        before = copy.deepcopy(self.paper)
        row, origins = self.project()
        self.assertEqual(row['authors'], ['Ada Example', 'Grace Example'])
        self.assertEqual(bibliographic_details(Paper.from_row(row))['missing_fields'], [])
        self.assertEqual(set(origins), {'title', 'authors', 'published', 'url', 'doi'})
        self.assertEqual(origins['doi']['section_id'], 'front')
        self.assertEqual(bibliographic_details(Paper.from_row(row))['verification_status'], 'not_independently_verified')
        self.assertEqual(self.paper, before)

    def test_source_local_public_url_is_not_mistaken_for_private_path(self):
        row, _ = self.project()
        self.assertEqual(bibliographic_details(Paper.from_row(row))['url'], 'https://example.org/study')
        self.assertEqual(bibliographic_details(Paper.from_row(self.paper))['url'], '')

    def test_provider_and_recorded_fields_are_not_overwritten(self):
        row, _ = self.project(paper={**self.paper, 'title': 'User title', 'authors': ['User Author'], 'published': '2024',
            'url': 'https://example.org/original', 'doi': '10.1234/original'})
        self.assertEqual(row['title'], 'User title')
        self.assertEqual(row['authors'], ['User Author'])
        self.assertEqual(row['published'], '2024')
        self.assertEqual(row['doi'], '10.1234/original')
        self.assertEqual(row['url'], 'https://example.org/original')
        record = DocumentRecord('doc', 'input paper', 'local_files', metadata={'paper_id': 'provider'})
        self.assertEqual(self.project(record=record), (self.paper, {}))

    def test_other_document_or_non_front_matter_is_not_citation_evidence(self):
        for section in (DocumentSection('front', 'other', 'front_matter', '', self.header.text),
                        DocumentSection('front', 'doc', 'references', '', self.header.text), None):
            self.assertEqual(self.project(section=section), (self.paper, {}))

    def test_bad_quotes_and_values_are_ignored_without_discarding_good_fields(self):
        for changes in ({'quote': 'Invented'}, {'section_id': 'other'}, {'value': ['Ada Example', 'Invented']},
                        {'value': ['Ada Example', 'Ada Example']}, {'value': 'Ada Example'}):
            fields = copy.deepcopy(self.fields)
            fields[1].update(changes)
            row, origins = self.project(note={**self.note, 'bibliographic_fields': fields})
            self.assertEqual(row['authors'], [])
            self.assertEqual(row['doi'], '10.1234/study')
            self.assertNotIn('authors', origins)

    def test_partial_byline_stays_explicit(self):
        fields = copy.deepcopy(self.fields)
        fields[1].pop('complete')
        row, _ = self.project(note={**self.note, 'bibliographic_fields': fields})
        self.assertTrue(any('completeness is not established' in message for message in row['bibliographic_notes']))

    def test_incremental_projection_preserves_notes_without_repeating_extraction_caveat(self):
        title_note = {'bibliographic_fields': [self.fields[0]]}
        first, _ = self.project(note=title_note)
        first['bibliographic_notes'].insert(0, 'User-supplied limitation.')
        before = copy.deepcopy(first)
        second, origins = self.project(paper=first, note=self.note)
        self.assertEqual(first, before)
        self.assertNotIn('title', origins)
        self.assertIn('authors', origins)
        self.assertEqual(len(second['bibliographic_notes']), len(set(second['bibliographic_notes'])))
        self.assertEqual(second['bibliographic_notes'][0], 'User-supplied limitation.')
        repeated, origins = self.project(paper=second)
        self.assertEqual(repeated, second)
        self.assertEqual(origins, {})

    def test_explicit_registry_stamp_maps_locator_without_inferring_year_or_changing_identity(self):
        for identifier in ('2306.12345v2', 'hep-th/9901001v3'):
            section = DocumentSection('front', 'doc', 'front_matter', '', f'arXiv:{identifier} [category]\nA source title')
            row, origins = self.project(section=section, note={'title': 'A source title'})
            self.assertEqual(row['url'], f'https://arxiv.org/abs/{identifier}')
            self.assertEqual(row['published'], None)
            self.assertEqual(row['source_id'], '/private/input_paper.pdf')
            self.assertEqual(origins['url']['quote'], f'arXiv:{identifier}')
        for text in ('Named 2306.12345.pdf', 'Discusses arXiv:2306.12345 inside prose.',
                     'arXiv:2306.12345v1\narXiv:2306.12345v2', 'arXiv:not-an-id'):
            row, _ = self.project(section=DocumentSection('front', 'doc', 'front_matter', '', text), note={})
            self.assertEqual(row['url'], self.paper['url'])

    def test_malformed_optional_field_does_not_break_an_otherwise_valid_read(self):
        row, _ = self.project(note={**self.note, 'bibliographic_fields': [{'field': [], 'quote': 'A source title'}]})
        self.assertEqual(row['title'], 'A source title')

    def test_unread_tail_and_invalid_date_are_not_adopted(self):
        section = DocumentSection('front', 'doc', 'front_matter', '', 'x' * 12000 + '\n2025-04-03\nA source title')
        self.assertEqual(self.project(section=section), (self.paper, {}))
        fields = [{'field': 'published', 'value': '2025-99-99', 'quote': '2025-99-99', 'section_id': 'front'}]
        section = DocumentSection('front', 'doc', 'front_matter', '', '2025-99-99')
        self.assertEqual(self.project(section=section, note={'bibliographic_fields': fields}), (self.paper, {}))
        for label in ('Draft version', 'Received', 'Accepted', 'Revised', 'Submitted', 'Updated', 'Copyright'):
            for quote in ('2025-04-03', f'{label}: 2025-04-03'):
                with self.subTest(label=label, quote=quote):
                    section = DocumentSection('front', 'doc', 'front_matter', '', f'{label}: 2025-04-03')
                    fields = [{'field': 'published', 'value': '2025-04-03',
                               'quote': quote, 'section_id': 'front'}]
                    self.assertEqual(self.project(section=section, note={'bibliographic_fields': fields}), (self.paper, {}))
        section = DocumentSection('front', 'doc', 'front_matter', '',
                                  'Received: 2025-04-03\nPublished online: 2025-04-03')
        # Use the publication occurrence when the same calendar date has both roles.
        fields[0]['quote'] = 'Published online: 2025-04-03'
        row, _ = self.project(section=section, note={'bibliographic_fields': fields})
        self.assertEqual(row['published'], '2025-04-03')

    def test_fields_roundtrip_and_reach_shared_references_and_bibtex(self):
        bundle = DocumentBundle([self.record], {}, {}, [self.header], [])
        read = ReadResult(status='completed', bundle=bundle, paper_notes=(self.note,))
        restored = ReadResult.from_handoff_dict(read.to_handoff_dict(), bundle=bundle)
        handles = _paper_source_handles(provided_materials_result([self.record]))
        context = ReportContext(topic='Review', report_mode='research_only', papers=[self.paper], source_handles=handles)
        projected, memory = attach_report_read_evidence(context, ReportMemory(source_handles=handles),
            documents=bundle, read=restored, read_ref=ArtifactRef('read.json'))
        paper = Paper.from_row(projected.papers[0])
        self.assertIn('Ada Example, Grace Example (2025)', references_markdown([paper]))
        self.assertIn('author = {Ada Example and Grace Example}', papers_to_bibtex([paper]))
        self.assertEqual(memory.source_handles[0].metadata['bibliographic_sources']['published']['quote'], '2025-04-03')

    def test_existing_read_call_receives_front_matter_without_extra_model_request(self):
        client = Mock()
        client.ask_json_many.return_value = [self.note]
        bundle = DocumentBundle([self.record], {}, {}, [self.header], [])
        read = read_documents(ReadRequest(bundle=bundle, use_llm=True, llm_client=client,
            config={'read_screening': 'deterministic'}))
        self.assertEqual(client.ask_json_many.call_count, 1)
        self.assertIn('https://example.org/study', client.ask_json_many.call_args.args[0][0].user)
        self.assertEqual(read.paper_notes[0]['bibliographic_fields'], self.fields)
        self.assertFalse(front_matter_view(self.header)['truncated'])


class MaterialBibliographyTests(unittest.TestCase):
    def inputs(self):
        record = DocumentRecord(document_id="doc", title="paper input", source="local_files",
            source_id="/private/paper_input.pdf", extraction_status="parsed")
        header = "A supplied study\nAda Example, Bo Example\narXiv:2501.12345v2 5 Feb 2025"
        front = DocumentSection("front", "doc", "front_matter", "Front matter", header, line_start=1)
        bundle = DocumentBundle([record], {}, {}, [front],
            [TextChunk("body", "doc", "Observed results with qualifications.", metadata={"section": "results"})])
        context, memory = build_material_report_inputs(topic="Explain these results", documents=bundle,
            documents_ref=ArtifactRef("documents.json"), assets=[SimpleNamespace(locator=record.source_id, role="paper")])
        memory.section_plan = [ReportSectionPlan(section_id="body", heading="Results", goal="Explain", evidence_handles=[context.source_handles[0].handle])]
        fields = [
            {"field": "title", "value": "A supplied study", "quote": "A supplied study", "section_id": "front"},
            {"field": "authors", "value": ["Ada Example", "Bo Example"], "quote": "Ada Example, Bo Example", "section_id": "front", "complete": True},
            {"field": "published", "value": "2025", "quote": "5 Feb 2025", "section_id": "front"},
        ]
        note = {"paper_id": "doc", "bibliographic_fields": fields}
        response = {"title": "Observed results", "sections": [
            {"heading": "Results", "goal": "Explain", "evidence_handles": [context.source_handles[0].handle]},
            {"heading": "Limits", "goal": "Qualify", "evidence_handles": [context.source_handles[0].handle]},
        ], "source_notes": [note]}
        return context, memory, bundle, note, response

    def test_observed_identifier_is_usable_without_a_model_or_filename_guess(self):
        context, _, bundle, _, _ = self.inputs()
        paper = Paper.from_row(context.papers[0])
        self.assertEqual(paper.url, "https://arxiv.org/abs/2501.12345v2")
        self.assertEqual(paper.title, "paper input")
        self.assertEqual(paper.authors, [])
        self.assertIsNone(paper.published)
        self.assertIsNone(bundle.records[0].url)

    def test_projection_assembly_and_bibtex_use_the_same_accepted_fields(self):
        context, memory, bundle, note, _ = self.inputs()
        original = copy.deepcopy((context.model_dump(), memory.model_dump(), bundle.to_handoff_dict()))
        memory.source_handles.append(SourceHandle(handle="extra", kind="material"))
        projected, saved = apply_report_bibliography(context, memory, documents=bundle, notes=[note])
        paper = Paper.from_row(projected.papers[0])
        self.assertEqual(paper.authors, ["Ada Example", "Bo Example"])
        self.assertEqual(projected.source_handles[0].title, "A supplied study")
        self.assertEqual(saved.source_handles[-1].handle, "extra")
        self.assertIn("Ada Example, Bo Example (2025)", references_markdown([paper]))
        self.assertIn("title = {A supplied study}", papers_to_bibtex([paper]))
        self.assertEqual(projected.source_handles[0].metadata["authors"], paper.authors)
        self.assertEqual(context.model_dump(), original[0])
        self.assertEqual(bundle.to_handoff_dict(), original[2])
        self.assertEqual(projected.source_handles[0].metadata["bibliography"]["verification_status"], "not_independently_verified")

    def test_foreign_unsupported_and_recorded_fields_cannot_be_promoted(self):
        context, memory, bundle, note, _ = self.inputs()
        bad = copy.deepcopy(note)
        bad["bibliographic_fields"][0]["value"] = "Another study"
        bad["bibliographic_fields"][1]["section_id"] = "foreign"
        bad["bibliographic_fields"][2]["value"] = "2024"
        projected, _ = apply_report_bibliography(context, memory, documents=bundle, notes=[bad])
        self.assertEqual(projected.papers, context.papers)
        context.papers[0]["authors"] = ["Recorded author"]
        projected, _ = apply_report_bibliography(context, memory, documents=bundle, notes=[note])
        self.assertEqual(projected.papers[0]["authors"], ["Recorded author"])

    def test_front_matter_is_bounded_and_only_registered_papers_are_requested(self):
        context, _, bundle, _, _ = self.inputs()
        bundle.sections[0] = DocumentSection("front", "doc", "front_matter", "Front matter", "x" * 30000)
        views = bibliography_planning_views(context, bundle)
        self.assertEqual(len(views[0]["text"]), 12000)
        self.assertTrue(views[0]["truncated"])
        self.assertIn("title", views[0]["missing_fields"])
        self.assertIn("published", views[0]["missing_fields"])
        self.assertNotIn("year", views[0]["missing_fields"])
        self.assertEqual(bibliography_planning_views(context, None), [])
        context.papers = []
        self.assertEqual(bibliography_planning_views(context, bundle), [])

    def test_survey_planning_also_uses_one_source_and_output_object(self):
        context, memory, bundle, _, _ = self.inputs()
        config = ReportRuntimeConfig(template="survey", outline_strategy="adaptive")
        template = load_report_template_bundle(report_mode="research_only", config=config)
        prompt = _outline_planner_prompt(context=context, memory=memory, config=config,
            template=template, source_front_matter=bibliography_planning_views(context, bundle))
        payload = json.loads(prompt.split("\n\n", 1)[1])
        self.assertIn("source_notes", payload["output_schema"])
        self.assertIn("sections", payload["output_schema"])
        self.assertEqual(payload["source_front_matter"][0]["paper_id"], "doc")

    def test_planner_fields_survive_first_writer_failure_without_a_second_plan(self):
        context, memory, bundle, note, response = self.inputs()
        config = ReportRuntimeConfig(template="source_review", outline_strategy="adaptive", max_review_iterations=0)
        template = load_report_template_bundle(report_mode=context.report_mode, config=config)
        gateway = ReportToolGateway(context, documents=bundle)
        gateway.call_counts["get_paper_brief"] = 2
        client, checkpoints = Mock(), []
        client.ask_json.return_value = response
        with patch("simple_ar.report.agent._draft_section_with_recovery", side_effect=LLMError("First writer outage")):
            with self.assertRaises(LLMError):
                run_report_agent(client=client, context=context, memory=memory, template=template,
                    config=config, gateway=gateway, checkpoint_sink=checkpoints.append)
        self.assertEqual(client.ask_json.call_count, 1)
        self.assertIn("source_front_matter", client.ask_json.call_args.args[1])
        prompt = client.ask_json.call_args.args[1]
        payload = json.loads(prompt.split("\n\n", 1)[1])
        self.assertIn("source_notes", payload["output_schema"])
        self.assertIn("sections", payload["output_schema"])
        self.assertEqual(payload["source_front_matter"][0]["paper_id"], "doc")
        saved = checkpoints[-1]
        self.assertEqual(saved["memory"]["outline_planning"]["source_notes"], [note])
        self.assertEqual(saved["sections"], [])
        self.assertEqual(context.papers[0]["title"], "paper input")
        self.assertEqual(gateway.context.papers[0]["title"], "A supplied study")
        self.assertEqual(gateway.call_counts["get_paper_brief"], 2)
        labels = []
        class ResumeClient:
            def ask_json(self, system, prompt, *, label="", **unused):
                labels.append(label)
                self_test.assertIn("A supplied study", prompt)
                if "reviewer" in label:
                    return {"verdict": "pass"}
                view = json.JSONDecoder().raw_decode(prompt[prompt.index("{"):])[0]
                return {"section_id": view["section"]["section_id"], "draft_markdown": "A bounded source result."}
        self_test = self
        with patch("simple_ar.report.agent._maybe_adapt_outline", side_effect=AssertionError("No second plan")):
            resumed = run_report_agent(client=ResumeClient(), context=context, memory=memory, template=template,
                config=config, gateway=ReportToolGateway(context, documents=bundle), completed_checkpoint=saved)
        self.assertEqual(len(resumed.sections), 2)
        self.assertTrue(all("outline" not in label for label in labels))
