"""Compatibility alias for the frozen legacy research-stage implementation.

The canonical V2.8 path is implemented under ``simple_ar.app`` and
``simple_ar.research``.  The old ``run``/``resume`` commands still need their
historical artifact projection, so the implementation is kept privately under
``simple_ar._legacy`` until its consumers have a replacement and regression
coverage.
"""

from __future__ import annotations

from importlib import import_module
import sys


sys.modules[__name__] = import_module("simple_ar._legacy.research_stages")
