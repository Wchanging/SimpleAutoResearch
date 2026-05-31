from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AppConfigSnapshot(BaseModel):
    """Serializable runtime configuration captured with one run."""

    model_config = ConfigDict(extra="allow")

    values: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_runtime_config(cls, config: dict[str, object]) -> "AppConfigSnapshot":
        return cls(values=dict(config))

    def to_runtime_config(self) -> dict[str, object]:
        return dict(self.values)

