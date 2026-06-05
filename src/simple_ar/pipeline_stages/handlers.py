"""Compatibility aggregation for pipeline stage handlers.

Real stage implementations live in ``research``, ``experiment``, and ``report``.
This module keeps older internal patch/import paths working while avoiding a
monolithic handler implementation.
"""

from __future__ import annotations

from simple_ar.pipeline_stages.experiment import execute_code, execute_design, execute_run
from simple_ar.pipeline_stages.registry import HANDLERS
from simple_ar.pipeline_stages.report import execute_report
from simple_ar.pipeline_stages.research import execute_plan, execute_read, execute_search, execute_synthesize

__all__ = [
    "HANDLERS",
    "execute_code",
    "execute_design",
    "execute_plan",
    "execute_read",
    "execute_report",
    "execute_run",
    "execute_search",
    "execute_synthesize",
]
