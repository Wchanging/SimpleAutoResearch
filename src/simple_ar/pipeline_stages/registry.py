"""Frozen 8-stage compatibility registry.

New V2.8 work belongs in the capability-oriented research-session path. This
registry remains only for the legacy ``run``/``resume`` commands and their
historical artifact layout.
"""

from simple_ar.pipeline_stages.experiment import execute_code, execute_design, execute_run
from simple_ar.pipeline_stages.report import execute_report
from simple_ar.pipeline_stages.research import (
    execute_plan,
    execute_read,
    execute_search,
    execute_synthesize,
)

HANDLERS = {
    1: execute_plan,
    2: execute_search,
    3: execute_read,
    4: execute_synthesize,
    5: execute_design,
    6: execute_code,
    7: execute_run,
    8: execute_report,
}

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
