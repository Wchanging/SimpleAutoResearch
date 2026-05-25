"""Source connector wrappers for the research evidence engine."""

from simple_ar.research.connectors.arxiv import ArxivConnector
from simple_ar.research.connectors.local_files import LocalFileConnector
from simple_ar.research.connectors.openalex import OpenAlexConnector
from simple_ar.research.connectors.semantic_scholar import SemanticScholarConnector

__all__ = ["ArxivConnector", "LocalFileConnector", "OpenAlexConnector", "SemanticScholarConnector"]
