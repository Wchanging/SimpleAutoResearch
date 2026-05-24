"""Source connector wrappers for the research evidence engine."""

from simple_ar.research.connectors.arxiv import ArxivConnector
from simple_ar.research.connectors.local_files import LocalFileConnector
from simple_ar.research.connectors.openalex import OpenAlexConnector

__all__ = ["ArxivConnector", "LocalFileConnector", "OpenAlexConnector"]
