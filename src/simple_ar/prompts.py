"""Backward-compatible prompt exports.

Research-facing prompts now live in ``simple_ar.research.prompts`` so V2.3 can
grow the Retrieval & Evidence Engine without turning this module into a large
mixed-domain prompt registry. Existing imports keep working through these
re-exports.
"""

from simple_ar.research.prompts import (
    CODE_TASK_DESIGN_SYSTEM,
    PLAN_SYSTEM,
    READ_SYSTEM,
    RESEARCH_PLANNER_SYSTEM,
    REPORT_SYSTEM,
    SYNTHESIZE_SYSTEM,
    code_task_design_user_prompt,
    paper_note_user_prompt,
    plan_user_prompt,
    read_user_prompt,
    report_user_prompt,
    research_planner_user_prompt,
    synthesize_user_prompt,
)

__all__ = [
    "CODE_TASK_DESIGN_SYSTEM",
    "PLAN_SYSTEM",
    "READ_SYSTEM",
    "RESEARCH_PLANNER_SYSTEM",
    "REPORT_SYSTEM",
    "SYNTHESIZE_SYSTEM",
    "code_task_design_user_prompt",
    "paper_note_user_prompt",
    "plan_user_prompt",
    "read_user_prompt",
    "report_user_prompt",
    "research_planner_user_prompt",
    "synthesize_user_prompt",
]
