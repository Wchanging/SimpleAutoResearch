from __future__ import annotations

from typing import Any

from simple_ar.legacy import cli as _legacy


main = _legacy.main


def __getattr__(name: str) -> Any:
    return getattr(_legacy, name)

