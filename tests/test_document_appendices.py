from pathlib import Path
import tempfile
import unittest

from simple_ar.research.contracts import DocumentRecord
from simple_ar.research.documents.sections import build_document_sections
from simple_ar.research.store.chunking import build_text_chunks
from simple_ar.research.evidence.reader import select_reading_chunks


class DocumentAppendixTests(unittest.TestCase):
    def test_html_blocks_not_inline_nodes_own_sections_and_front_matter(self):
        from simple_ar.research.documents.extractors import LocalDocumentParser
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'study.html'
            path.write_text('<title>A structured study</title>'
                '<nav><a>Abstract</a><a>1 Introduction</a></nav>'
                '<form>Report an issue: <label>Title:</label></form>'
                '<div>arXiv:<span>2401.12345v2</span> [cs.LG] 2 May 2025</div>'
                '<h1>A <em>structured</em> study</h1><p>Ada Example and Ben Example</p>'
                '<h2>Abstract</h2><p>A summary.</p>'
                '<h2><span>1</span> Introduction</h2>'
                '<p>A method compares <a>Model A</a> with <b>Model B</b> under shared conditions.</p>'
                '<h2><span>2</span> How the comparison was performed</h2>'
                '<p>These are matched datasets.</p>'
                '<h2><span>B.1</span> Additional <em>baselines</em></h2>'
                '<p>Version <span>1</span> is classification-only.</p>'
                '<table><tr><th>Method</th><th>Score</th></tr>'
                '<tr><td>A</td><td>0.42</td></tr></table>'
                '<script>Invisible results.</script>', encoding='utf-8')
            parsed = LocalDocumentParser().parse(path)
            self.assertIn('arXiv:2401.12345v2 [cs.LG] 2 May 2025', parsed.text)
            self.assertIn('A structured study', parsed.text)
            self.assertIn('1 Introduction', parsed.text)
            self.assertIn('A method compares Model A with Model B under shared conditions.', parsed.text)
            self.assertIn('Method Score', parsed.text)
            self.assertIn('A 0.42', parsed.text)
            self.assertNotIn('Report an issue', parsed.text)
            self.assertNotIn('Invisible results', parsed.text)
            rows = self.sections(parsed.text)
            front = next(row for row in rows if row.section == 'front_matter')
            self.assertIn('Ada Example and Ben Example', front.text)
            self.assertIn('2401.12345v2', front.text)
            comparison = next(row for row in rows if row.heading == 'How the comparison was performed')
            self.assertEqual(comparison.text, 'These are matched datasets.')
            appendix = next(row for row in rows if row.heading == 'Additional baselines')
            self.assertIn('Version 1 is classification-only.', appendix.text)

    def test_html_inline_fragments_do_not_invent_word_or_numeric_boundaries(self):
        from simple_ar.research.documents.extractors import _html_to_text
        text = _html_to_text('<p>pre<em>train</em> on <span>10</span>,000 rows; '
                            'loss = x<sup>2</sup>.</p><p>Other <b>conditions</b> apply.</p>')
        self.assertIn('pretrain on 10,000 rows; loss = x2.', text)
        self.assertIn('Other conditions apply.', text)
        self.assertNotIn('10\n', text)

    def test_explicit_sentence_case_headings_exclude_fenced_examples(self):
        text = ('# Study\nAda Example\n## What changes under distribution shift\nEvidence.\n'
                '```markdown\n## A heading in the example\nMethods\n```\n'
                '~~~\n## Another code heading\n~~~~\n'
                '## Further observations\nMore evidence.\n')
        rows = self.sections(text)
        self.assertEqual([row.heading for row in rows],
            ['Front matter', 'What changes under distribution shift', 'Further observations'])
        self.assertIn('A heading in the example', rows[1].text)
        self.assertIn('Another code heading', rows[1].text)

    def test_supplied_html_builds_chunks_without_remote_fulltext_permission(self):
        from unittest.mock import patch
        from simple_ar.research.contracts import SourcePlan
        from simple_ar.research.documents.ingest import build_document_bundle, DocumentBundle
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / 'paper.HTML'
            original = '<h1>Original study</h1><h2>Methods</h2><p>Evaluation uses matched observations.</p>'
            paper.write_text(original, encoding='utf-8')
            with patch('urllib.request.urlopen', side_effect=AssertionError('No remote fetch is authorized')):
                bundle = build_document_bundle(papers=[], source_plan=SourcePlan(queries=['supplied paper'],
                    sources=['local_files'], local_documents=[str(paper)], require_fulltext=False,
                    allow_pdf_download=False), cache_dir=None, extraction_dir=root / 'extracted')
            self.assertEqual(bundle.records[0].extraction_status, 'parsed')
            self.assertEqual(bundle.records[0].parser, 'basic_html')
            self.assertTrue(bundle.chunks)
            self.assertIn('Evaluation uses matched observations.', '\n'.join(row.text for row in bundle.chunks))
            self.assertNotIn('<p>', '\n'.join(row.text for row in bundle.chunks))
            self.assertEqual(bundle.fulltext_manifest['fetch_attempt_count'], 0)
            self.assertEqual(paper.read_text(encoding='utf-8'), original)
            restored = DocumentBundle.from_handoff_dict(bundle.to_handoff_dict())
            self.assertEqual(restored.to_handoff_dict(), bundle.to_handoff_dict())

    def sections(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'paper.txt'
            path.write_text(text, encoding='utf-8')
            return build_document_sections([DocumentRecord(document_id='paper', title='Paper',
                source='local_files', local_path=str(path), extraction_status='parsed')])

    def test_mixed_case_letter_appendices_are_not_bibliography(self):
        text = ('Introduction\nMain discussion.\nReferences\nSmith. Study of a method.\n'
                'A. Additional Experiments\nObserved validation results.\n'
                'A.1. Estimation of conditional quantiles\nEstimator conditions.\n'
                'B. Proofs\nExchangeability proof details.\n')
        rows = self.sections(text)
        references = next(row for row in rows if row.section == 'references')
        self.assertNotIn('Observed validation', references.text)
        self.assertNotIn('proof details', references.text)
        self.assertEqual([row.heading for row in rows[-3:]],
            ['Additional Experiments', 'Estimation of conditional quantiles', 'Proofs'])
        self.assertTrue(all(row.section != 'references' for row in rows[-3:]))
        for row in rows:
            self.assertEqual(row.text, '\n'.join(text.splitlines()[row.line_start - 1:row.line_end]).strip())

    def test_non_dotted_and_explicit_appendices_preserve_source_text(self):
        for heading in ('A Monotonizing non-monotone risks', 'A. Sampling with model version 2', 'Appendix B',
                        'Appendix B: Proof details', '## Appendix C Additional comparisons',
                        'Supplementary Information'):
            with self.subTest(heading=heading):
                rows = self.sections('References\nBibliography entry.\n' + heading + '\nEvidence after bibliography.\n')
                self.assertEqual(len(rows), 2)
                self.assertNotEqual(rows[1].section, 'references')
                self.assertEqual(rows[1].text, 'Evidence after bibliography.')

    def test_reference_initials_sentences_and_table_rows_are_not_boundaries(self):
        text = ('References\nA. Smith, B. Jones. A study.\n'
                'A. Inductive confidence machines for regression. In\n'
                'A final step of triangle inequality implies the result:\n'
                'A. Method 0.31 0.42\nA. 2024 Results\n1 A numbered prose sentence.\n')
        rows = self.sections(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].section, 'references')
        self.assertIn('Inductive confidence', rows[0].text)

    def test_appendix_cross_references_do_not_split_normal_prose(self):
        text = ('Experiments\nObserved measurements.\n'
                'Appendix A.2 discusses the estimation using either\n'
                'a network or local regression.\n'
                'Appendix A.5 directly compares the proposed method with\n'
                'another approach.\nAppendix B contains the proofs.\n')
        rows = self.sections(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].section, 'experiments')
        self.assertIn('Appendix A.2 discusses', rows[0].text)

    def test_appendix_theorem_is_reachable_by_normal_reading_selection(self):
        rows = self.sections('Introduction\nGeneral background.\nReferences\nCitations.\n'
                             'B. Proofs\nA bounded exchangeable loss controls expected risk.\n')
        chunks = build_text_chunks([], sections=rows)
        selected = select_reading_chunks(tuple(chunks), max_chunks=2,
                                         focus='bounded exchangeable expected risk')
        self.assertTrue(any('bounded exchangeable loss' in chunk.text for chunk in selected))
        self.assertFalse(any(chunk.metadata.get('section') == 'references' for chunk in selected))

    def test_numbered_title_case_sections_do_not_inherit_previous_labels(self):
        text = ('1 Introduction\nMotivation.\n2 Theory\nExchangeable loss assumptions.\n'
                '2.1 Guarantees Under Distribution Shift\nShift qualifications.\n'
                '3 Applications\nApplication results.\n4 Conclusions\nTakeaways.\n')
        rows = self.sections(text)
        self.assertEqual([row.heading for row in rows],
            ['Introduction', 'Theory', 'Guarantees Under Distribution Shift', 'Applications', 'Conclusions'])
        self.assertEqual(rows[1].text, 'Exchangeable loss assumptions.')
        for row in rows:
            self.assertEqual(row.text, '\n'.join(text.splitlines()[row.line_start - 1:row.line_end]).strip())

    def test_numbered_sentences_table_values_and_citations_remain_body(self):
        text = ('Methods\n1 A numbered prose sentence.\n2 Keep the changes small\n'
                '3 Method Name 0.31 0.42\n2024 A Report Title\n'
                '4 A Citation, With Authors\n5 A Claim!\n')
        rows = self.sections(text)
        self.assertEqual(len(rows), 1)
        self.assertIn('Keep the changes small', rows[0].text)


if __name__ == '__main__':
    unittest.main()
