from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.core.artifacts import read_json
from simple_ar.code_task import (
    ExternalAgentAdapterSpec,
    ExternalAgentDisabledError,
    ExternalAgentEditorBackend,
    ExternalAgentPermissionPolicy,
    build_external_agent_invocation_plan,
    is_blocked_external_agent_read_path,
    normalize_external_agent_provider,
)
from simple_ar.code_task.editing.editor import EditRequest, EditorContext, EditorSafetyPolicy


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class ExternalAgentEditorTests(unittest.TestCase):
    def test_provider_aliases_and_blocked_read_paths(self) -> None:
        self.assertEqual(normalize_external_agent_provider("claude"), "claude_code")
        self.assertEqual(normalize_external_agent_provider("open-code"), "opencode")
        self.assertTrue(is_blocked_external_agent_read_path(".env"))
        self.assertTrue(is_blocked_external_agent_read_path("config/secret.yaml"))
        self.assertTrue(is_blocked_external_agent_read_path("$HOME/.ssh/id_rsa"))
        self.assertFalse(is_blocked_external_agent_read_path("src/model.py"))

    def test_invocation_plan_is_reviewable_and_non_executing(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_dir = root / "code_task"
            workspace = task_dir / "workspace"
            meta = task_dir / "meta"
            workspace.mkdir(parents=True)
            meta.mkdir(parents=True)
            request = EditRequest(
                context=EditorContext(
                    run_dir=root,
                    task_dir=task_dir,
                    workspace_dir=workspace,
                    meta_dir=meta,
                    manifest={"workflow": "code_task"},
                    task_text="Improve a model.",
                ),
                safety=EditorSafetyPolicy(
                    protected_patterns=("tests/**",),
                    blocked_read_patterns=("private/**",),
                ),
            )
            spec = ExternalAgentAdapterSpec(
                provider="claude",
                model="sonnet",
                permissions=ExternalAgentPermissionPolicy(
                    allow_shell_commands=False,
                    allow_network=False,
                ),
            )

            plan = build_external_agent_invocation_plan(request, spec)

            self.assertEqual(plan.backend, "external_agent")
            self.assertEqual(plan.provider, "claude_code")
            self.assertEqual(plan.status, "disabled")
            self.assertEqual(plan.cwd, "code_task/workspace")
            self.assertIn("private/**", plan.blocked_read_patterns)
            self.assertFalse(plan.permissions["allow_shell_commands"])
            self.assertFalse(plan.permissions["allow_network"])
            self.assertIn("--allowed-tools", plan.command_preview)
            self.assertIn("Read Edit Write", plan.command_preview)

    def test_external_backend_writes_plan_then_refuses_execution(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_dir = root / "code_task"
            workspace = task_dir / "workspace"
            meta = task_dir / "meta"
            workspace.mkdir(parents=True)
            meta.mkdir(parents=True)
            request = EditRequest(
                context=EditorContext(
                    run_dir=root,
                    task_dir=task_dir,
                    workspace_dir=workspace,
                    meta_dir=meta,
                    manifest={"workflow": "code_task"},
                ),
                safety=EditorSafetyPolicy(protected_patterns=("tests/**",)),
            )
            backend = ExternalAgentEditorBackend(ExternalAgentAdapterSpec(provider="codex"))

            with self.assertRaises(ExternalAgentDisabledError):
                backend.propose(request)

            plan = read_json(meta / "external_agent_invocation_plan.json")
            self.assertEqual(plan["backend"], "external_agent")
            self.assertEqual(plan["provider"], "codex")
            self.assertEqual(plan["status"], "disabled")
            self.assertIn("external_agent.diff", plan["diff_path"])


if __name__ == "__main__":
    unittest.main()
