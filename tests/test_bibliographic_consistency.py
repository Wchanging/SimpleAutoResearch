"""Deterministic metadata consistency is not independent identity verification."""
import unittest
from simple_ar.literature.models import Paper, bibliographic_details
from simple_ar.literature.bibtex import papers_to_bibtex
from simple_ar.report.citations import references_markdown, citation_map_artifact
from simple_ar.report.projection import _paper_source_handles
from simple_ar.research.sources.capability import provided_materials_result
from simple_ar.research.documents.records import build_document_records
from simple_ar.research.contracts import SourcePlan
from simple_ar.report.agent import _prompt_handle_view
from simple_ar.report.audit import build_report_audit
from simple_ar.report.schema import ReportContext, ReportMemory


class BibliographicConsistencyTests(unittest.TestCase):
    def paper(self, **changes):
        values = dict(id='p', title='Recorded study', authors=['Recorded Author'], abstract='',
            published='2024-02-29', source='arxiv', source_id='2404.15018v1',
            url='https://arxiv.org/abs/2404.15018v1')
        return Paper(**{**values, **changes})

    def test_impossible_date_is_not_exported_as_a_known_year(self):
        for date in ('2023-02-29', '2024-02-30', '2024-13', '0000', '2024-00-01'):
            with self.subTest(date=date):
                paper = self.paper(published=date)
                details = bibliographic_details(paper)
                self.assertEqual(details['year'], '')
                self.assertTrue(details['consistency_issues'])
                self.assertNotIn('year =', papers_to_bibtex([paper]))
                self.assertEqual(paper.published, date)

    def test_partial_dates_and_unknown_dates_do_not_create_conflicts(self):
        for value, year in (('2024', '2024'), ('2024-02', '2024'),
                            ('2024-02-29', '2024'), ('in press', ''), (None, '')):
            with self.subTest(value=value):
                details = bibliographic_details(self.paper(published=value))
                self.assertEqual(details['year'], year)
                self.assertEqual(details['consistency_issues'], [])

    def test_arxiv_identity_and_explicit_version_conflicts_are_visible(self):
        for changes in ({'url': 'https://arxiv.org/abs/2404.99999v1'},
                        {'fulltext_url': 'https://arxiv.org/pdf/2404.15018v2.pdf'},
                        {'doi': '10.48550/arXiv.2404.99999'},
                        {'url': 'https://doi.org/10.48550/arXiv.2404.99999'},
                        {'fulltext_url': 'doi:10.48550/arXiv.2404.15018v2'}):
            with self.subTest(changes=changes):
                details = bibliographic_details(self.paper(**changes))
                self.assertTrue(details['consistency_issues'])
                self.assertEqual(details['verification_status'], 'not_independently_verified')

    def test_unpinned_versions_and_different_publication_years_are_not_guessed(self):
        for changes in ({'url': 'https://arxiv.org/abs/2404.15018'},
                        {'published': '2026-03-04'},
                        {'source_id': 'arXiv:hep-th/9901001v2',
                         'url': 'https://arxiv.org/pdf/hep-th/9901001v2'}):
            with self.subTest(changes=changes):
                self.assertEqual(bibliographic_details(self.paper(**changes))['consistency_issues'], [])

    def test_doi_wrappers_are_normalized_but_not_verified(self):
        for value in ('doi:10.1234/example', 'https://doi.org/10.1234/example'):
            with self.subTest(value=value):
                paper = self.paper(doi=value)
                details = bibliographic_details(paper)
                self.assertEqual(details['doi'], '10.1234/example')
                self.assertIn('doi = {10.1234/example}', papers_to_bibtex([paper]))
                self.assertEqual(paper.doi, value)
                self.assertEqual(details['verification_status'], 'not_independently_verified')

    def test_doi_subprefix_unicode_and_encoded_punctuation_are_not_rejected(self):
        for value, identifier in (('10.500.100/研究 example', '10.500.100/研究 example'),
            ('doi:10.1234/a%23b', '10.1234/a#b'),
            ('https://doi.org/10.1234/a%2Fb', '10.1234/a/b'),
            ('10.123456789012/example', '10.123456789012/example')):
            with self.subTest(value=value):
                details = bibliographic_details(self.paper(doi=value))
                self.assertEqual(details['doi'], identifier)
                self.assertEqual(details['consistency_issues'], [])

    def test_doi_link_conflict_is_not_silently_resolved(self):
        paper = self.paper(doi='10.1234/one', url='https://doi.org/10.1234/two')
        details = bibliographic_details(paper)
        self.assertTrue(details['consistency_issues'])
        self.assertEqual(details['doi'], paper.doi)
        self.assertEqual(details['url'], paper.url)
        same = self.paper(doi='10.1234/Example', url='https://doi.org/10.1234/example')
        self.assertEqual(bibliographic_details(same)['consistency_issues'], [])

    def test_unrecognized_doi_is_retained_only_as_raw_provenance(self):
        for value in ('unverified identifier', '10/example', '10.١٢٣٤/example', '10.1234/a\tb'):
            with self.subTest(value=value):
                paper = self.paper(doi=value)
                details = bibliographic_details(paper)
                self.assertEqual(details['doi'], '')
                self.assertTrue(details['consistency_issues'])
                self.assertNotIn('doi =', papers_to_bibtex([paper]))
                self.assertEqual(Paper.from_row(paper.to_row()).doi, paper.doi)

    def test_diagnostics_reach_prompts_and_all_reference_consumers(self):
        paper = self.paper(url='https://arxiv.org/abs/2404.15018v2')
        records = build_document_records(papers=[paper], source_plan=SourcePlan(queries=['test'], sources=['arxiv']))
        handle = _paper_source_handles(provided_materials_result(records))[0]
        details = bibliographic_details(paper)
        self.assertTrue(details['consistency_issues'])
        self.assertEqual(_prompt_handle_view(handle)['metadata']['bibliography']['consistency_issues'],
                         details['consistency_issues'])
        self.assertEqual(citation_map_artifact({'p': 1}, [paper])['entries'][0]['bibliography']['consistency_issues'],
                         details['consistency_issues'])
        for issue in details['consistency_issues']:
            self.assertIn(issue, references_markdown([paper]))
            self.assertIn(issue, papers_to_bibtex([paper]))

    def test_audit_warns_for_cited_conflicts_not_unused_or_missing_metadata(self):
        for paper, body, expected in ((self.paper(url='https://arxiv.org/abs/2404.15018v2'), 'Observation [@p].', 'warning'),
            (self.paper(url='https://arxiv.org/abs/2404.15018v2'), 'Other observation [@q].', 'passed'),
            (self.paper(authors=[], published=None, doi=None), 'Observation [@p].', 'passed')):
            with self.subTest(body=body, expected=expected):
                audit = build_report_audit(report=body, report_body=body,
                    context=ReportContext(topic='Study', report_mode='source_review',
                        papers=[paper.to_row(), self.paper(id='q').to_row()]), memory=ReportMemory())
                self.assertEqual(audit.status, expected)


if __name__ == '__main__':
    unittest.main()
