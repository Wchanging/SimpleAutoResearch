from __future__ import annotations

import importlib
import unittest


class PublicApiTests(unittest.TestCase):
    def test_documented_package_exports_resolve(self) -> None:
        for module_name in ("simple_ar.core", "simple_ar.research", "simple_ar.report"):
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                for name in module.__all__:
                    with self.subTest(name=name):
                        self.assertIsNotNone(getattr(module, name))

    def test_legacy_module_paths_remain_aliases(self) -> None:
        legacy_cli = importlib.import_module("simple_ar._legacy.cli")
        current_cli = importlib.import_module("simple_ar.cli.main")
        legacy_handlers = importlib.import_module("simple_ar._legacy.stage_handlers")
        current_handlers = importlib.import_module("simple_ar.pipeline_stages.handlers")

        self.assertIs(legacy_cli, current_cli)
        self.assertIs(legacy_handlers, current_handlers)


if __name__ == "__main__":
    unittest.main()
