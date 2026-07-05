from __future__ import annotations

"""Registry metadata for deterministic code-task analyzers.

The analyzers themselves live near the code they inspect.  This registry keeps
their purpose, scope, and cost visible without forcing review/repair to grow a
new orchestration layer.
"""

from pathlib import Path
from typing import Any

from simple_ar.core.artifacts import write_json


ANALYZER_REGISTRY_SCHEMA_VERSION = "code_task_analyzer_registry.v1"


def default_analyzer_registry() -> dict[str, Any]:
    analyzers = [
        _analyzer("python_compile", "syntax/import preflight", "whole_project", "low"),
        _analyzer("local_api_contract", "local module exports and imports", "whole_project", "low"),
        _analyzer("return_contract", "producer return shape versus consumer expectations", "whole_project", "low"),
        _analyzer("entrypoint_debuggability", "entrypoint traceback preservation", "entrypoints", "low"),
        _analyzer("resource_static", "nested loops/search/resource-risk patterns", "whole_project", "low"),
        _analyzer("artifact_contract", "required output artifact writer visibility", "task_contract", "low"),
        _analyzer("metric_contract", "required metric visibility and placeholder risks", "task_contract", "low"),
        _analyzer("task_acceptance", "explicit task requirement acceptance checks", "task_contract", "low"),
        _analyzer("domain_profile_optional", "task-triggered domain-specific static checks", "optional_profile", "low"),
    ]
    return {
        "schema_version": ANALYZER_REGISTRY_SCHEMA_VERSION,
        "policy": "deterministic analyzers are benchmark-agnostic and triggered by project/contract signals",
        "analyzers": analyzers,
    }


def write_analyzer_registry(path: Path) -> Path:
    write_json(path, default_analyzer_registry())
    return path


def _analyzer(analyzer_id: str, trigger: str, scope: str, cost: str) -> dict[str, str]:
    return {
        "id": analyzer_id,
        "trigger": trigger,
        "scope": scope,
        "cost_level": cost,
    }
