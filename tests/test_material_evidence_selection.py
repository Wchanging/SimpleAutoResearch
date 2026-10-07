"""Material windows must preserve source results, relevance and exact locations."""
import copy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from simple_ar.core.capabilities import ArtifactRef
from simple_ar.research.contracts import DocumentRecord, DocumentSection, TextChunk
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.research.documents.sections import abstract_excerpt, build_document_sections
from simple_ar.research.store.retrieval import material_overview_views
from simple_ar.report.narrative import _prompt_handle_view
from simple_ar.report.projection import build_material_report_inputs


class MaterialEvidenceSelectionTests(unittest.TestCase):
    def chunk(self, identity, text, section="body", line=1):
        return TextChunk(chunk_id=identity, document_id="source", text=text,
                         source_path="source.txt", line_start=line,
                         metadata={"section": section, "extraction_status": "parsed"})

    def test_explicit_abstract_is_literal_not_byline_or_unheaded_note(self):
        text = "Authors and affiliations " * 90 + "\nABSTRACT\nA reported result.\n1 INTRODUCTION\nBackground."
        self.assertEqual(abstract_excerpt(text), "A reported result.")
        self.assertEqual(abstract_excerpt("An unheaded laboratory note."), "")
        self.assertEqual(abstract_excerpt("# Abstract\nResult plus condition.\n## Methods\nProcedure."), "Result plus condition.")

    def test_short_parsed_notes_are_body_but_provider_abstract_is_abstract(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.txt"
            path.write_text("# Notes\nUnheaded supplied observation.", encoding="utf-8")
            notes = DocumentRecord(document_id="notes", title="Notes", source="local_files",
                local_path=str(path), extraction_status="parsed")
            provider = DocumentRecord(document_id="paper", title="Paper", source="provider",
                abstract="Provider supplied abstract.")
            rows = build_document_sections([notes, provider])
            self.assertEqual([row.section for row in rows], ["body", "abstract"])

    def test_long_abstract_tail_and_task_relevant_body_reach_bounded_views(self):
        abstract = "Background " * 145 + "Tail result with fixed calibration and reported uncertainty."
        body = "Ordinary introduction " * 140 + "Uncertainty calibration depends on the observed population."
        chunks = [self.chunk("authors", "Author list " * 200, "front_matter"),
                  self.chunk("summary", abstract, "abstract", 5),
                  self.chunk("intro", "Unrelated introductory history.", "introduction", 8),
                  self.chunk("methods", body, "method", 12),
                  self.chunk("refs", "Uncertainty calibration references " * 200, "references", 20)]
        before = copy.deepcopy(chunks)
        rows = material_overview_views(chunks, "uncertainty calibration observed population")
        self.assertIn("Tail result", " ".join(row["text"] for row in rows))
        self.assertIn("depends on the observed population", " ".join(row["text"] for row in rows))
        self.assertNotIn("refs", [row["chunk_id"] for row in rows])
        self.assertNotIn("authors", [row["chunk_id"] for row in rows])
        self.assertLessEqual(len(rows), 6)
        self.assertLessEqual(sum(len(row["text"]) for row in rows), 7200)
        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        for row in rows:
            self.assertEqual(row["text"], by_id[row["chunk_id"]].text[row["character_start"]:row["character_end"]])
        self.assertEqual(chunks, before)

    def test_long_overview_is_honestly_partial_and_fallback_keeps_source(self):
        rows = material_overview_views([self.chunk("summary", "x" * 10000, "abstract", 2)], "")
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["truncated"] for row in rows))
        self.assertEqual(rows[-1]["character_end"], 4200)
        self.assertEqual(material_overview_views([], "topic"), [])
        refs = self.chunk("refs", "Only supplied references.", "references")
        self.assertEqual(material_overview_views([refs], "unmatched")[0]["text"], refs.text)
        for kwargs in ({"limit": 0}, {"max_chunk_chars": 0}, {"max_chars": 0}):
            with self.assertRaises(ValueError):
                material_overview_views([refs], "", **kwargs)

    def test_existing_chunk_spans_do_not_waste_window_slots_on_tiny_tails(self):
        chunks = [self.chunk("a", "x" * 1400, "abstract", 2),
                  self.chunk("b", "y" * 1273, "abstract", 3),
                  *[self.chunk(str(index), "Body " * 1000, "body", index + 4) for index in range(8)]]
        rows = material_overview_views(chunks, "Body")
        self.assertEqual([row["text"] for row in rows[:2]], [chunks[0].text, chunks[1].text])
        self.assertEqual(len(rows), 6)
        self.assertLessEqual(sum(len(row["text"]) for row in rows), 7200)
        tiny = material_overview_views(chunks, "Body", max_chars=2)
        self.assertLessEqual(sum(len(row["text"]) for row in tiny), 2)

    def test_unheaded_summary_before_methods_is_not_discarded_as_a_byline(self):
        chunks = [self.chunk("opening", "Authors. Reported diffusion results and sample ranking conditions.", "front_matter"),
                  self.chunk("method", "General implementation details.", "method", 12)]
        rows = material_overview_views(chunks, "diffusion results sample ranking")
        self.assertEqual(rows[0]["chunk_id"], "opening")
        self.assertIn("ranking conditions", rows[0]["text"])
        self.assertEqual(rows[0]["selection"], "task_lexical_match")

    def test_overview_distributes_ranked_windows_across_observed_sections(self):
        chunks = [self.chunk('abstract', 'Overview.', 'abstract', 2)]
        for index in range(5):
            chunk = self.chunk(f'experiment-{index}', 'Calibration coverage comparison experiment results.',
                               'experiments', index + 10)
            chunk.metadata['section_id'] = 'experiments'
            chunks.append(chunk)
        theory = self.chunk('theory', 'Calibration coverage requires exchangeability.', 'body', 20)
        theory.metadata['section_id'] = 'theory'
        chunks.append(theory)
        method = self.chunk('method', 'The calibration procedure estimates a threshold.', 'method', 30)
        method.metadata['section_id'] = 'method'
        chunks.append(method)
        before = copy.deepcopy(chunks)
        rows = material_overview_views(chunks, 'calibration coverage comparison experiment results', limit=4)
        self.assertEqual({row['chunk_id'] for row in rows}, {'abstract', 'experiment-0', 'theory', 'method'})
        self.assertLessEqual(sum(len(row['text']) for row in rows), 7200)
        self.assertEqual(chunks, before)
        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        for row in rows:
            self.assertEqual(row['text'], by_id[row['chunk_id']].text[row['character_start']:row['character_end']])

    def test_overview_covers_secondary_query_aspects_without_growing_budget(self):
        chunks = [self.chunk('quality', 'Accuracy quality precision recall.', 'results', 5),
                  self.chunk('repeat', 'Accuracy quality precision.', 'discussion', 10),
                  self.chunk('cost', 'Background ' * 200 + 'Latency memory constraints.', 'method', 15)]
        before = copy.deepcopy(chunks)
        rows = material_overview_views(chunks, 'accuracy quality precision recall latency memory',
            limit=2, max_chars=500, max_chunk_chars=250)
        self.assertEqual([row['chunk_id'] for row in rows], ['quality', 'cost'])
        self.assertIn('Latency memory', rows[1]['text'])
        self.assertLessEqual(sum(len(row['text']) for row in rows), 500)
        self.assertEqual(chunks, before)
        for row in rows:
            source = next(chunk for chunk in chunks if chunk.chunk_id == row['chunk_id'])
            self.assertEqual(row['text'], source.text[row['character_start']:row['character_end']])

    def test_overview_keeps_actual_hit_and_uses_spare_capacity_for_entry_context(self):
        entry = self.chunk('entry', 'Observations must be independent under the stated model.', 'method', 5)
        hit = self.chunk('interior', 'Calibration coverage comparison experiment results.', 'method', 10)
        entry.metadata['section_id'] = hit.metadata['section_id'] = 'method'
        rows = material_overview_views([entry, hit], 'calibration coverage comparison experiment results', limit=1)
        self.assertEqual(rows[0]['chunk_id'], 'interior')
        self.assertEqual(rows[0]['selection'], 'task_lexical_match')
        rows = material_overview_views([entry, hit], 'calibration coverage comparison experiment results', limit=2)
        self.assertEqual([row['chunk_id'] for row in rows], ['interior', 'entry'])
        self.assertIn('Observations must be independent', rows[1]['text'])
        self.assertEqual(rows[1]['selection'], 'source_order_fallback')
        hit = self.chunk('caption', 'Table 2: Calibration coverage comparison experiment results.', 'method', 10)
        hit.metadata['section_id'] = 'method'
        rows = material_overview_views([entry, hit], 'Table 2', limit=1)
        self.assertEqual(rows[0]['chunk_id'], 'caption')

    def test_projection_corrects_old_prefix_without_mutating_bundle_or_inventing_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            source = str(Path(directory) / "article.pdf")
            record = DocumentRecord(document_id="source", title="Supplied article", source="local_files",
                source_id=source, abstract="Authors and affiliations", extraction_status="parsed")
            summary = "Overview " * 170 + "Tail conclusion with a measured interval."
            section = DocumentSection(section_id="abstract", document_id="source", section="abstract",
                heading="ABSTRACT", text=summary, line_start=7)
            chunks = [self.chunk("summary", summary, "abstract", 7)]
            bundle = DocumentBundle(records=[record], fulltext_manifest={}, fulltext_extraction={}, sections=[section], chunks=chunks)
            original = copy.deepcopy(bundle.to_handoff_dict())
            context, _ = build_material_report_inputs(topic="measured interval", documents=bundle,
                documents_ref=ArtifactRef("attempts/ingest/documents.json"),
                assets=[SimpleNamespace(locator=source, role="paper")])
            handle = context.source_handles[0]
            self.assertTrue(handle.summary.startswith("Overview"))
            prompt = _prompt_handle_view(handle)
            self.assertIn("Tail conclusion", " ".join(row["text"] for row in prompt["metadata"]["evidence_passages"]))
            self.assertTrue(prompt["metadata"]["evidence_passages_truncated"])
            self.assertEqual(context.papers[0]["authors"], [])
            self.assertEqual(bundle.to_handoff_dict(), original)
            self.assertEqual(prompt["metadata"]["evidence_passages"][0]["selection"], "abstract_overview")

    def test_old_unheaded_prefix_is_not_used_as_paper_summary(self):
        record = DocumentRecord(document_id="source", title="Notes", source="local_files", source_id="note.txt",
            abstract="Old automatic prefix", extraction_status="parsed")
        section = DocumentSection(section_id="old", document_id="source", section="abstract",
            heading="Abstract", text="Old automatic prefix", line_start=1)
        bundle = DocumentBundle(records=[record], fulltext_manifest={}, fulltext_extraction={}, sections=[section],
                                chunks=[self.chunk("old", section.text, "abstract")])
        context, _ = build_material_report_inputs(topic="notes", documents=bundle,
            documents_ref=ArtifactRef("documents.json"), assets=[SimpleNamespace(locator="note.txt", role="paper")])
        self.assertEqual(context.source_handles[0].summary, "")
        self.assertNotEqual(context.source_handles[0].metadata["evidence_passages"][0]["selection"], "abstract_overview")


if __name__ == "__main__":
    unittest.main()
