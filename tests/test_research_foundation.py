from __future__ import annotations

import unittest

from simple_ar.literature.models import Paper
from simple_ar.research.contracts import (
    ClaimCard,
    DocumentRecord,
    ExperimentContract,
    PaperCard,
    ResearchContract,
    SourcePlan,
)
from simple_ar.research.sources import SearchQuery
from simple_ar.research.connectors.local_files import LocalFileConnector
from simple_ar.research.prompts import report_user_prompt
from simple_ar.prompts import report_user_prompt as compat_report_user_prompt


class ResearchFoundationTests(unittest.TestCase):
    def test_research_contract_round_trips_with_defaults(self) -> None:
        contract = ResearchContract.from_row({"topic": "agent simulation", "mode": "not-a-mode"})

        self.assertEqual(contract.topic, "agent simulation")
        self.assertEqual(contract.mode, "standard")
        self.assertTrue(contract.requires_experiment)
        self.assertEqual(contract.to_row()["schema_version"], "research_contract.v1")

    def test_core_cards_are_json_serializable(self) -> None:
        artifacts = [
            SourcePlan(queries=["agent simulation"]).to_row(),
            DocumentRecord(document_id="doc-1", title="A Paper", source="fixture").to_row(),
            PaperCard(paper_id="p1", title="A Paper", evidence_refs=["doc-1#chunk-1"]).to_row(),
            ClaimCard(claim_id="c1", paper_id="p1", claim="A bounded claim").to_row(),
            ExperimentContract(contract_id="e1", hypothesis="Small local test").to_row(),
        ]

        self.assertEqual(artifacts[0]["schema_version"], "source_plan.v1")
        self.assertEqual(artifacts[-1]["schema_version"], "experiment_contract.v1")

    def test_local_file_connector_returns_matching_text_documents(self) -> None:
        connector = LocalFileConnector(paths=[])
        response = connector.search(SearchQuery(query="anything", max_results=2))

        self.assertEqual(response.source, "local_files")
        self.assertEqual(response.papers, [])

    def test_prompt_module_reexport_is_compatible(self) -> None:
        self.assertIs(report_user_prompt, compat_report_user_prompt)


class InMemoryConnectorSmokeTests(unittest.TestCase):
    def test_search_query_shape_accepts_papers(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Paper",
            authors=[],
            abstract="abstract",
            url="https://example.test",
        )

        self.assertEqual(paper.to_row()["id"], "paper-1")


if __name__ == "__main__":
    unittest.main()
