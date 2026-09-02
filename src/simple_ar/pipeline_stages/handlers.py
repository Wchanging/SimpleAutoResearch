"""Compatibility alias for the canonical legacy-stage registry."""

from __future__ import annotations

import sys

from simple_ar.pipeline_stages import registry as _registry

sys.modules[__name__] = _registry
