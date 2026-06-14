from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ExperimentToolSpec:
    name: str
    description: str
    permission: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExperimentToolResult:
    name: str
    status: str
    data: dict[str, Any]
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

