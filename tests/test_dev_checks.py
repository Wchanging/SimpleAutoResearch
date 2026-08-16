from __future__ import annotations

import sys
import unittest

from simple_ar.app.dev_checks import CHECK_GROUPS, build_unittest_command, parse_args


class DevChecksTests(unittest.TestCase):
    def test_check_groups_are_layered_and_documented(self) -> None:
        self.assertIn("quick", CHECK_GROUPS)
        self.assertIn("core", CHECK_GROUPS)
        self.assertIn("code-task", CHECK_GROUPS)
        self.assertIn("pipeline", CHECK_GROUPS)
        self.assertIn("research", CHECK_GROUPS)
        self.assertIn("all", CHECK_GROUPS)
        for group in CHECK_GROUPS.values():
            self.assertTrue(group.description)
            self.assertTrue(group.targets)

    def test_builds_unittest_command_for_group(self) -> None:
        command = build_unittest_command("code-task", verbose=True, failfast=True)

        self.assertEqual(command[:3], [sys.executable, "-m", "unittest"])
        self.assertIn("-v", command)
        self.assertIn("-f", command)
        self.assertIn("tests.test_code_task", command)

    def test_core_group_covers_capability_boundary_and_handoff_fixture(self) -> None:
        command = build_unittest_command("core")

        self.assertIn("tests.test_capabilities", command)
        self.assertIn("tests.test_capability_package_example", command)

    def test_full_group_uses_discover(self) -> None:
        command = build_unittest_command("all")

        self.assertEqual(command[:3], [sys.executable, "-m", "unittest"])
        self.assertEqual(command[3:], ["discover", "-s", "tests"])

    def test_default_group_is_optional(self) -> None:
        args = parse_args([])

        self.assertEqual(args.groups, [])
        self.assertFalse(args.list)


if __name__ == "__main__":
    unittest.main()
