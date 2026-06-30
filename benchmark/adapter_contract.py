from __future__ import annotations

"""Shared manifest helpers for benchmark adapters.

This module intentionally lives under ``benchmark/`` instead of ``src/simple_ar``.
Adapters can use it to keep prepared/run/submission/judge metadata consistent
without making the SimpleAutoResearch core aware of any specific benchmark.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class AdapterManifest:
    suite: str
    operation: str
    status: str
    inputs: Mapping[str, Any] = field(default_factory=dict)
    outputs: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "simple_ar_benchmark_adapter.v1"
    written_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "suite": self.suite,
            "operation": self.operation,
            "status": self.status,
            "written_at": self.written_at,
            "inputs": normalize_manifest_value(self.inputs),
            "outputs": normalize_manifest_value(self.outputs),
            "metadata": normalize_manifest_value(self.metadata),
        }


def build_adapter_manifest(
    *,
    suite: str,
    operation: str,
    status: str,
    inputs: Mapping[str, Any] | None = None,
    outputs: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    schema_version: str = "simple_ar_benchmark_adapter.v1",
) -> dict[str, Any]:
    return AdapterManifest(
        suite=suite,
        operation=operation,
        status=status,
        inputs=inputs or {},
        outputs=outputs or {},
        metadata=metadata or {},
        schema_version=schema_version,
    ).to_dict()


def normalize_manifest_value(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        return {str(key): normalize_manifest_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_manifest_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
