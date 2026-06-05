"""Compatibility alias for older stage handler imports."""

from __future__ import annotations

import sys

from simple_ar.pipeline_stages import handlers as _handlers

sys.modules[__name__] = _handlers
