from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.agent_backends import (
    AgentExecutionMode,
    AgentPermissionPolicy,
    AgentRunRequest,
    build_code_task_handoff,
    build_greenfield_handoff,
    create_agent_backend,
    ingest_agent_outputs,
    normalize_agent_mode,
)
from simple_ar.agent_backends.external_cli import CodexCliBackend
from simple_ar.core.artifacts import read_json
from simple_ar.experiment.coding.provider import implement_greenfield_project
from simple_ar.tools.mcp_server import MCPServerConfig, SimpleARMCPStdioServer
from simple_ar.tools import CommonToolGateway, ToolCall, default_tool_registry, export_mcp_tool_schemas, export_openai_tool_schemas


TEST_ROOT = Path(".tmp_tests") / "tool-agent-handoff"


class CommonToolLayerTests(unittest.TestCase):
    def test_default_registry_exports_real_read_only_schemas(self) -> None:
        registry = default_tool_registry()

        names = {spec.name for spec in registry.list_specs()}
        self.assertIn("read_experiment_contract", names)
        self.assertIn("get_paper_brief", names)
        self.assertIn("run_experiment_command", names)

        openai_tools = export_openai_tool_schemas(registry)
        mcp_tools = export_mcp_tool_schemas(registry)
        openai_names = {tool["function"]["name"] for tool in openai_tools}  # type: ignore[index]
        mcp_names = {tool["name"] for tool in mcp_tools}

        self.assertIn("read_experiment_contract", openai_names)
        self.assertIn("get_paper_brief", openai_names)
        self.assertNotIn("run_experiment_command", openai_names)
        self.assertEqual(openai_names, mcp_names)

    def test_common_gateway_dispatches_read_only_and_blocks_execution(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp)
            (run_dir / "05-design").mkdir()
            (run_dir / "07-run").mkdir()
            (run_dir / "05-design" / "experiment_contract.json").write_text(
                '{"contract_id": "exp-test"}',
                encoding="utf-8",
            )
            gateway = CommonToolGateway(run_dir, registry=default_tool_registry(include_report=False))

            result = gateway.call(ToolCall(tool_name="read_experiment_contract"))
            blocked = gateway.call(ToolCall(tool_name="run_experiment_command"))

            self.assertEqual(result.status, "ok")
            self.assertEqual(result.data["experiment_contract"]["contract_id"], "exp-test")
            self.assertEqual(blocked.status, "blocked")
            self.assertTrue((run_dir / "tools" / "tool_trace.jsonl").is_file())


class AgentHandoffTests(unittest.TestCase):
    def test_agent_mode_aliases_and_provider_defaults_are_explicit(self) -> None:
        self.assertEqual(normalize_agent_mode("", provider="local"), AgentExecutionMode.MODEL)
        self.assertEqual(normalize_agent_mode("", provider="codex"), AgentExecutionMode.HANDOFF)
        self.assertEqual(
            normalize_agent_mode("delegated-workspace", provider="codex"),
            AgentExecutionMode.DELEGATED_WORKSPACE,
        )

    def test_code_task_handoff_writes_workspace_scoped_package(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp)
            (run_dir / "code_task").mkdir()
            (run_dir / "code_task" / "task.md").write_text("Improve the model.", encoding="utf-8")

            package = build_code_task_handoff(run_dir, permission_policy=AgentPermissionPolicy.read_only())

            self.assertTrue(package.instructions_path.is_file())
            self.assertTrue(package.tool_schema_path.is_file())
            self.assertTrue(package.permission_policy_path.is_file())
            self.assertTrue(package.artifact_handles_path.is_file())
            self.assertTrue(package.expected_outputs_path.is_file())
            instructions = package.instructions_path.read_text(encoding="utf-8")
            self.assertIn("Code Task Handoff", instructions)
            self.assertIn("Default policy is read-only", instructions)
            policy = read_json(package.permission_policy_path)
            self.assertFalse(policy["allow_file_write"])
            artifacts = read_json(package.artifact_handles_path)
            self.assertTrue(any(item["path"] == "code_task/task.md" and item["exists"] for item in artifacts["artifacts"]))

    def test_ingest_agent_outputs_collects_untrusted_artifacts(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp)
            package = build_code_task_handoff(run_dir, task_text="Write a review.")
            (package.handoff_dir / "review.md").write_text("# Review\n", encoding="utf-8")
            (package.handoff_dir / "agent_result.json").write_text('{"status": "ok"}\n', encoding="utf-8")

            summary = ingest_agent_outputs(run_dir=run_dir, handoff_dir=package.handoff_dir)

            self.assertEqual(summary["status"], "ok")
            self.assertTrue((run_dir / "agent_outputs" / "code-task" / "review.md").is_file())
            self.assertTrue((run_dir / "agent_outputs" / "code-task" / "ingestion.json").is_file())
            self.assertTrue(summary["validation_required"])

    def test_fake_backend_produces_greenfield_generated_files(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp)
            package = build_greenfield_handoff(
                run_dir,
                permission_policy=AgentPermissionPolicy(allow_file_write=True),
            )
            backend = create_agent_backend("fake")

            result = backend.run(
                AgentRunRequest(
                    provider="fake",
                    run_dir=run_dir,
                    handoff_dir=package.handoff_dir,
                    metadata={"mode": "greenfield"},
                )
            )
            summary = ingest_agent_outputs(run_dir=run_dir, handoff_dir=package.handoff_dir)

            self.assertTrue(result.ok)
            self.assertEqual(summary["status"], "ok")
            self.assertTrue((package.handoff_dir / "generated_files" / "main.py").is_file())
            self.assertTrue((run_dir / "agent_outputs" / "greenfield" / "generated_files" / "main.py").is_file())

    def test_greenfield_handoff_archives_stale_outputs_before_rerun(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp)
            first = build_greenfield_handoff(run_dir, name="greenfield-codex")
            (first.handoff_dir / "stderr.txt").write_text("stale model error", encoding="utf-8")
            (first.handoff_dir / "generated_files").mkdir()

            second = build_greenfield_handoff(run_dir, name="greenfield-codex")

            self.assertEqual(first.handoff_dir, second.handoff_dir)
            self.assertFalse((second.handoff_dir / "stderr.txt").exists())
            run_key = f"{run_dir.parent.name}-{run_dir.name}".lower()
            archives = list((Path(".simple_ar_cache") / "agent_handoff_archives" / run_key).glob("greenfield-codex-*"))
            self.assertEqual(len(archives), 1)
            self.assertTrue((archives[0] / "stderr.txt").is_file())

    def test_greenfield_handoff_relocates_legacy_sibling_archives(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp)
            legacy = run_dir / "agent_handoff" / "archives" / "greenfield-codex-old"
            legacy.mkdir(parents=True)
            (legacy / "stderr.txt").write_text("old failure", encoding="utf-8")

            build_greenfield_handoff(run_dir, name="greenfield-codex")

            self.assertFalse((run_dir / "agent_handoff" / "archives").exists())
            run_key = f"{run_dir.parent.name}-{run_dir.name}".lower()
            migrated = list((Path(".simple_ar_cache") / "agent_handoff_archives" / run_key).glob("legacy-archives-*"))
            self.assertEqual(len(migrated), 1)
            self.assertTrue((migrated[0] / "greenfield-codex-old" / "stderr.txt").is_file())

    def test_greenfield_fake_provider_still_uses_internal_review_boundary(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            stage_dir = Path(tmp) / "06-code"
            stage_dir.mkdir(parents=True)
            result = implement_greenfield_project(
                stage_dir=stage_dir,
                contract={"contract_id": "fake-greenfield", "generation_plan": {"files_per_batch": 2}},
                result_schema={
                    "metrics": [
                        {"name": "accuracy"},
                        {"name": "macro_f1"},
                        {"name": "train_time_sec"},
                        {"name": "inference_time_ms"},
                        {"name": "parameter_count"},
                    ]
                },
                resource_plan={"max_generated_lines": 200},
                dependency_plan={"install_allowed": False},
                domain_profile={},
                implementation_provider="fake",
                agent_mode="handoff",
                allow_external_agent=False,
            )

            self.assertTrue((stage_dir / "agent_handoff").exists() or (stage_dir.parent / "agent_handoff").exists())
            self.assertTrue((result.project_dir / "main.py").is_file())
            backend = read_json(result.code_backend_path)
            self.assertEqual(backend["backend"], "greenfield_agent")
            self.assertEqual(backend["provider"], "fake")
            self.assertEqual(backend["agent_mode"], "handoff")
            self.assertTrue(result.code_review_path.is_file())

    def test_delegated_workspace_mode_fails_explicitly_until_runner_exists(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            stage_dir = Path(tmp) / "06-code"
            stage_dir.mkdir(parents=True)
            with self.assertRaises(NotImplementedError):
                implement_greenfield_project(
                    stage_dir=stage_dir,
                    contract={"contract_id": "delegated-greenfield"},
                    result_schema={"metrics": [{"name": "accuracy"}]},
                    resource_plan={"max_generated_lines": 200},
                    dependency_plan={"install_allowed": False},
                    domain_profile={},
                    implementation_provider="fake",
                    agent_mode="delegated_workspace",
                    allow_external_agent=False,
                )

    def test_codex_cli_backend_constructs_without_mutating_read_only_name(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp)
            handoff_dir = run_dir / "agent_handoff" / "greenfield-codex"
            handoff_dir.mkdir(parents=True)
            backend = CodexCliBackend(binary="codex", model="gpt-5-mini", enabled=False)
            request = AgentRunRequest(provider="codex", run_dir=run_dir, handoff_dir=handoff_dir)

            command = backend.preview_command(request)

            self.assertEqual(backend.name, "codex")
            self.assertIn(Path(command[0]).name.lower(), {"codex", "codex.cmd", "codex.exe"})
            self.assertEqual(command[1:5], ["exec", "--sandbox", "workspace-write", "--skip-git-repo-check"])
            self.assertIn("gpt-5-mini", command)

    def test_codex_cli_backend_omits_model_flag_when_model_is_empty(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp)
            handoff_dir = run_dir / "agent_handoff" / "greenfield-codex"
            handoff_dir.mkdir(parents=True)
            backend = CodexCliBackend(binary="codex", model="", enabled=False)
            request = AgentRunRequest(provider="codex", run_dir=run_dir, handoff_dir=handoff_dir)

            command = backend.preview_command(request)

            self.assertNotIn("-m", command)


class MCPServerTests(unittest.TestCase):
    def test_mcp_server_lists_and_calls_real_experiment_tools(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp)
            (run_dir / "05-design").mkdir()
            (run_dir / "06-code" / "generated_project").mkdir(parents=True)
            (run_dir / "07-run").mkdir()
            (run_dir / "05-design" / "experiment_contract.json").write_text(
                '{"contract_id": "mcp-test"}',
                encoding="utf-8",
            )
            (run_dir / "05-design" / "result_schema.json").write_text(
                '{"primary_metric": "accuracy", "required_metrics": ["accuracy"]}',
                encoding="utf-8",
            )
            (run_dir / "06-code" / "generated_project" / "main.py").write_text(
                "def run_experiment():\n    return {'accuracy': 0.9}\n",
                encoding="utf-8",
            )
            (run_dir / "07-run" / "results.json").write_text(
                '{"metrics": {"accuracy": 0.9}}',
                encoding="utf-8",
            )
            server = SimpleARMCPStdioServer(MCPServerConfig(run_dir=run_dir))

            listed = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            called = server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "search_generated_code", "arguments": {"query": "run_experiment"}},
                }
            )

            self.assertIsNotNone(listed)
            self.assertIn("read_experiment_contract", {tool["name"] for tool in listed["result"]["tools"]})  # type: ignore[index]
            self.assertIsNotNone(called)
            self.assertFalse(called["result"]["isError"])  # type: ignore[index]
            content = called["result"]["structuredContent"]  # type: ignore[index]
            self.assertEqual(content["status"], "ok")
            self.assertTrue(content["data"]["matches"])


if __name__ == "__main__":
    unittest.main()
