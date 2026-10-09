import copy
from dataclasses import replace
import unittest

from simple_ar.research.contracts import TextChunk
from simple_ar.research.store.retrieval import rank_source_chunks, source_chunk_views


class SourceWindowRelevanceTests(unittest.TestCase):
    def view(self, text, query, limit=180):
        chunk = TextChunk(chunk_id='source#window', document_id='source', text=text,
                          source_path='paper.txt', page=3, line_start=12)
        before = copy.deepcopy(chunk)
        row = source_chunk_views([chunk], query=query, max_chars=limit)[0]
        self.assertEqual(row['text'], text[row['character_start']:row['character_end']])
        self.assertLessEqual(len(row['text']), limit)
        self.assertEqual(row['total_characters'], len(text))
        self.assertEqual(row['page'], 3)
        self.assertEqual(chunk, before)
        return row

    def test_nearby_conditions_outscore_isolated_rare_query_word(self):
        text = ('An isolated theorem is mentioned.\n' + 'Unrelated history. ' * 70
                + '\nExchangeability and bounded loss yield an expected risk guarantee.\n'
                + 'Unrelated details. ' * 70)
        row = self.view(text, 'theorem exchangeability bounded loss expected risk guarantee')
        for term in ('Exchangeability', 'bounded loss', 'expected risk guarantee'):
            self.assertIn(term, row['text'])
        self.assertNotIn('isolated theorem', row['text'])

    def test_repeated_background_does_not_beat_distinct_query_terms(self):
        text = 'coverage ' * 150 + '\nConditional coverage requires calibrated quantiles.\n' + 'context ' * 100
        row = self.view(text, 'conditional coverage calibrated quantiles')
        self.assertIn('requires calibrated quantiles', row['text'])

    def test_numbers_and_words_are_not_substring_anchors(self):
        text = 'brisk comparison in 2014. ' + 'Unrelated history. ' * 70
        row = self.view(text, 'risk 14')
        self.assertEqual(row['character_start'], 0)
        self.assertEqual(self.view(text, '14')['character_start'], 0)
        self.assertEqual(self.view(text, 'risk')['character_start'], 0)
        text += 'Risk at 14 units was observed. ' + 'context ' * 100
        row = self.view(text, 'risk 14')
        self.assertIn('Risk at 14 units', row['text'])
        self.assertNotIn('2014', row['text'])

    def test_original_unicode_offsets_survive_casefold_and_chinese_bigrams(self):
        text = 'Straße ' * 60 + '\nBOUNDed LOSS controls risk.\n' + 'other ' * 80
        row = self.view(text, 'bounded loss risk')
        self.assertIn('BOUNDed LOSS', row['text'])
        text = '背景材料。' * 100 + '交换性条件下风险控制具有保证。' + '其他材料。' * 100
        row = self.view(text, '风险控制保证', limit=40)
        self.assertIn('风险控制具有保证', row['text'])

    def test_explicit_caption_and_phrase_keep_navigation_priority(self):
        text = 'Figure 2 is discussed with risk loss conditions. ' * 30
        text += '\nFigure 2: Calibration under exchangeability.\n' + 'later ' * 90
        row = self.view(text, 'Figure 2 risk loss conditions')
        self.assertIn('Figure 2: Calibration', row['text'])
        text = 'risk ' * 200 + '\nexact local phrase answers the question.\n' + 'later ' * 90
        row = self.view(text, 'exact local phrase')
        self.assertIn('exact local phrase', row['text'])

    def test_empty_unknown_short_and_tiny_views_are_exact(self):
        self.assertEqual(self.view('short source', 'source')['text'], 'short source')
        for query in ('', 'unmatchedword', 'supercalifragilisticexpialidocious'):
            row = self.view('prefix ' * 50, query, limit=3)
            self.assertEqual(row['character_start'], 0)
        row = self.view('longword ' * 50, 'longword', limit=2)
        self.assertEqual(row['character_start'], 0)

    def test_retained_heading_routes_to_source_without_becoming_body_evidence(self):
        background = TextChunk(chunk_id='intro', document_id='source',
            text='The proofs are provided later.', metadata={'heading': 'Introduction'})
        proof = TextChunk(chunk_id='proof', document_id='source',
            text='Let the observations be exchangeable and the loss be bounded.',
            metadata={'heading': 'Proofs'})
        self.assertEqual(rank_source_chunks([background, proof], 'Proofs', limit=1), [proof])
        self.assertEqual(rank_source_chunks([proof, background], 'exchangeable bounded loss', limit=1), [proof])
        row = source_chunk_views([proof], query='Proofs', max_chars=100)[0]
        self.assertEqual(row['text'], proof.text)
        self.assertNotIn('Proofs', row['text'])
        for specific, query in (
            ('License permits redistribution with attribution and reuse.', 'dataset code open access conditions license reuse'),
            ('Memory requires eight gigabytes for initialization.', 'dataset code open access conditions memory initialization'),
        ):
            with self.subTest(query=query):
                introduction = replace(background, text='Dataset code open access conditions are introduced. '
                    'The overview describes research goals, comparison methods, categories, results, measurement, '
                    'calibration, processing, examples, interpretation, evaluation, testing and limitations.')
                statement = replace(proof, text=specific, metadata={})
                other = [TextChunk(chunk_id=f'other-{index}', document_id='source',
                    text=text) for index, text in enumerate(('Dataset code open access conditions are summarized.',
                        'Bibliographic publication record.', 'Changes in previous versions.'))]
                # A broad query has several legitimate aspects: the existing
                # two-hit lookup must retain the short specific condition too.
                self.assertIn(statement, rank_source_chunks([introduction, statement, *other], query, limit=2))
        decimal = replace(proof, text='Risk is bounded by 0.6.', metadata={})
        self.assertEqual(rank_source_chunks([replace(background, text='Risk is bounded by 0.60.'), decimal],
                                           '0.6', limit=1), [decimal])

    def test_numbered_object_keeps_following_conditions_without_crossing_source_or_section(self):
        caption = TextChunk(chunk_id='caption', document_id='source', source_path='retained.txt',
            text='Table 3: Reported accuracy comparison.', line_start=10,
            metadata={'section_id': 'evaluation'})
        condition = TextChunk(chunk_id='condition', document_id='source', source_path='retained.txt',
            text='Baseline measurements came from prior work, not paired local runs.', line_start=11,
            metadata={'section_id': 'evaluation'})
        related = TextChunk(chunk_id='related', document_id='source', source_path='retained.txt',
            text='Reported accuracy comparison across many configurations.', line_start=20,
            metadata={'section_id': 'discussion'})
        for label in ('Table 3', 'Figure 3', 'Theorem 3', 'Algorithm 3'):
            with self.subTest(label=label):
                current = replace(caption, text=label + ': Reported accuracy comparison.')
                chunks = [related, condition, current]  # Storage order is not source order.
                before = copy.deepcopy(chunks)
                selected = rank_source_chunks(chunks, label + ' reported accuracy comparison', limit=2)
                self.assertEqual(selected, [current, condition])
                views = source_chunk_views(selected, max_chars=100)
                self.assertLessEqual(sum(len(row['text']) for row in views), 100)
                self.assertEqual([row['chunk_id'] for row in views], ['caption', 'condition'])
                self.assertEqual(chunks, before)
                self.assertEqual(rank_source_chunks(chunks, label, limit=1), [current])
        for changed in (replace(condition, document_id='other'),
                        replace(condition, source_path='other.txt'),
                        replace(condition, metadata={'section_id': 'discussion'}),
                        replace(condition, metadata={})):
            with self.subTest(changed=changed):
                self.assertEqual(rank_source_chunks([caption, changed, related],
                    'Table 3 reported accuracy comparison', limit=2), [caption, related])
        self.assertEqual(rank_source_chunks([caption, condition, related],
            'reported accuracy comparison', limit=2), [caption, related])


if __name__ == '__main__':
    unittest.main()
