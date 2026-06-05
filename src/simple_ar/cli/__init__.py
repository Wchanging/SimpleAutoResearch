from __future__ import annotations

from importlib import import_module
from typing import Any

_main = import_module("simple_ar.cli.main")


main = _main.main


def __getattr__(name: str) -> Any:
    return getattr(_main, name)
