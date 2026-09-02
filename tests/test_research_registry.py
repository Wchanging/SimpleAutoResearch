from __future__ import annotations

import unittest

from simple_ar.core import CapabilityRegistry
from simple_ar.research import (
    register_research_capabilities,
    research_capability_names,
)


class ResearchCapabilityRegistryTests(unittest.TestCase):
    def test_registry_names_are_stable_and_registration_is_explicit(self) -> None:
        self.assertEqual(
            research_capability_names(),
            (
                "plan",
                "search",
                "document_ingest",
                "read",
                "synthesize",
                "research_design",
                "experiment",
                "analysis",
                "analyze",
                "report",
                "report_audit",
            ),
        )

        registry = CapabilityRegistry()
        registered = register_research_capabilities(
            registry,
            names=("read", "synthesize"),
        )

        self.assertEqual(registered, ("read", "synthesize"))
        self.assertEqual(registry.names(), ("read", "synthesize"))

    def test_unknown_or_duplicate_names_do_not_partially_register(self) -> None:
        registry = CapabilityRegistry()

        with self.assertRaises(ValueError):
            register_research_capabilities(registry, names=("read", "missing"))
        self.assertEqual(registry.names(), ())

        register_research_capabilities(registry, names=("read",))
        with self.assertRaises(ValueError):
            register_research_capabilities(registry, names=("read", "synthesize"))
        self.assertEqual(registry.names(), ("read",))

        second = CapabilityRegistry()
        register_research_capabilities(second, names=("synthesize",))
        with self.assertRaises(ValueError):
            register_research_capabilities(second, names=("read", "synthesize"))
        self.assertEqual(second.names(), ("synthesize",))

    def test_single_name_and_all_adapters_load_without_partial_registration(self) -> None:
        single = CapabilityRegistry()
        self.assertEqual(
            register_research_capabilities(single, names="read"),
            ("read",),
        )
        self.assertEqual(single.names(), ("read",))

        complete = CapabilityRegistry()
        self.assertEqual(
            register_research_capabilities(complete),
            research_capability_names(),
        )
        self.assertEqual(complete.names(), tuple(sorted(research_capability_names())))

    def test_replace_is_forwarded_to_registry(self) -> None:
        registry = CapabilityRegistry()
        register_research_capabilities(registry, names=("read",))

        replacement = lambda **_: None  # type: ignore[return-value]
        registry.register("read", replacement, replace=True)
        self.assertIs(registry.resolve("read"), replacement)

    def test_legacy_analyze_name_resolves_to_the_canonical_handler(self) -> None:
        registry = CapabilityRegistry()
        register_research_capabilities(registry, names=("analysis", "analyze"))

        self.assertIs(registry.resolve("analysis"), registry.resolve("analyze"))


if __name__ == "__main__":
    unittest.main()
