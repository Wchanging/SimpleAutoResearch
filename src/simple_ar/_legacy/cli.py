"""Compatibility alias for older CLI imports."""

from __future__ import annotations

from importlib import import_module
import sys

_main = import_module("simple_ar.cli.main")

sys.modules[__name__] = _main
