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
from simple_ar.code_task.memory import (
    code_task_memory_paths,
    record_code_task_memory_event,
    record_edit_history,
    task_memory_context,
)
from simple_ar.code_task import execute_code_task, initialize_code_task
from simple_ar.code_task.review import review_code_task_changes
from simple_ar.core.artifacts import read_json, read_jsonl
from simple_ar.tools.mcp_server import MCPServerConfig, SimpleARMCPStdioServer
from simple_ar.tools import CommonToolGateway, ToolCall, default_tool_registry, export_mcp_tool_schemas, export_openai_tool_schemas


TEST_ROOT = Path(".tmp_tests") / "tool-agent-handoff"


class CommonToolLayerTests(unittest.TestCase):
    def test_default_registry_exports_real_read_only_schemas(self) -> None:
        registry = default_tool_registry()

        names = {spec.name for spec in registry.list_specs()}
        self.assertIn("read_code_task_memory", names)
        self.assertIn("search_code_task_code", names)
        self.assertIn("read_experiment_contract", names)
        self.assertIn("get_paper_brief", names)
        self.assertIn("run_experiment_command", names)

        openai_tools = export_openai_tool_schemas(registry)
        mcp_tools = export_mcp_tool_schemas(registry)
        openai_names = {tool["function"]["name"] for tool in openai_tools}  # type: ignore[index]
        mcp_names = {tool["name"] for tool in mcp_tools}

        self.assertIn("read_experiment_contract", openai_names)
        self.assertIn("read_code_task_memory", openai_names)
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

    def test_common_gateway_dispatches_code_task_memory_and_lookup_tools(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp)
            workspace = run_dir / "code_task" / "workspace"
            meta = run_dir / "code_task" / "meta"
            (workspace / "pkg").mkdir(parents=True)
            meta.mkdir(parents=True)
            (run_dir / "code_task" / "task.md").write_text("Improve classify_text.", encoding="utf-8")
            (workspace / "pkg" / "app.py").write_text(
                "def classify_text(text):\n    return 'positive' if 'good' in text else 'negative'\n",
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                '{"workflow": "code_task", "workspace": {"mode": "copy"}, "edit_scope": {"allowed_patterns": ["pkg/**"]}}',
                encoding="utf-8",
            )
            (meta / "codebase_index.json").write_text(
                """
{
  "schema_version": 1,
  "files": [
    {
      "path": "pkg/app.py",
      "kind": "python",
      "bytes": 80,
      "role_tags": ["python"],
      "access_role": "editable",
      "summary": "functions: classify_text(text); imports: none",
      "python": {
        "imports": [],
        "functions": [{"name": "classify_text", "line_start": 1, "line_end": 2, "args": ["text"]}],
        "classes": []
      }
    }
  ]
}
""".strip(),
                encoding="utf-8",
            )
            (meta / "repo_map.json").write_text(
                """
{
  "schema_version": 1,
  "symbols": [
    {
      "id": "pkg/app.py::classify_text",
      "path": "pkg/app.py",
      "kind": "function",
      "name": "classify_text",
      "qualified_name": "classify_text",
      "line_start": 1,
      "line_end": 2,
      "access_role": "editable"
    }
  ]
}
""".strip(),
                encoding="utf-8",
            )
            record_code_task_memory_event(
                run_dir,
                event_type="work_plan",
                summary="Plan targets pkg/app.py.",
                status="done",
                artifacts=["code_task/work_plan.md"],
            )
            record_edit_history(
                run_dir,
                changed_files=["pkg/app.py"],
                reason="Applied classifier threshold update.",
                proposal="code_task/meta/proposed_edits.json",
                patch_diff="code_task/patch.diff",
            )
            gateway = CommonToolGateway(
                run_dir,
                registry=default_tool_registry(include_report=False, include_experiment=False),
            )

            memory = gateway.call("read_code_task_memory")
            search = gateway.call("search_code_task_code", {"query": "classify_text"})
            symbol = gateway.call("find_code_task_symbol", {"query": "classify"})
            source = gateway.call("read_code_task_file_range", {"path": "pkg/app.py", "start_line": 1, "max_lines": 2})
            related = gateway.call("find_code_task_related_files", {"query": "classify text"})
            recent = gateway.call("list_code_task_recent_edits")

            self.assertEqual(memory.status, "ok")
            self.assertIn("Plan targets", memory.data["markdown"])
            self.assertEqual(search.status, "ok")
            self.assertEqual(search.data["matches"][0]["path"], "pkg/app.py")
            self.assertEqual(symbol.status, "ok")
            self.assertEqual(symbol.data["symbols"][0]["qualified_name"], "classify_text")
            self.assertEqual(source.status, "ok")
            self.assertIn("def classify_text", source.data["text"])
            self.assertEqual(related.status, "ok")
            self.assertEqual(related.data["files"][0]["path"], "pkg/app.py")
            self.assertEqual(recent.status, "ok")
            self.assertTrue(recent.data["edit_history"])

            package = build_code_task_handoff(run_dir, permission_policy=AgentPermissionPolicy.read_only())
            self.assertTrue((package.handoff_dir / "context" / "task_memory.md").is_file())
            schema = read_json(package.tool_schema_path)
            names = {tool["function"]["name"] for tool in schema["tools"]}
            self.assertIn("read_code_task_memory", names)
            self.assertNotIn("read_experiment_contract", names)

    def test_code_task_memory_current_status_ignores_nonblocking_review_noise(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp)
            (run_dir / "code_task").mkdir(parents=True)
            (run_dir / "code_task" / "task.md").write_text("Improve the project.", encoding="utf-8")

            memory = record_code_task_memory_event(
                run_dir,
                event_type="patched_run",
                summary="Patched benchmark passed.",
                status="passed",
            )
            self.assertEqual(memory.current_status, "Patched benchmark passed.")

            memory = record_code_task_memory_event(
                run_dir,
                event_type="review_finding",
                summary="Minor reviewer note.",
                status="warning",
                key="review-warning",
            )
            self.assertEqual(memory.current_status, "Patched benchmark passed.")

            memory = record_code_task_memory_event(
                run_dir,
                event_type="review_finding",
                summary="Blocking reviewer finding.",
                status="blocking",
                key="review-blocking",
            )
            self.assertEqual(memory.current_status, "Blocking reviewer finding.")

    def test_embedded_code_task_memory_lives_under_stage_code_memory(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            stage_dir = Path(tmp) / "06-code"
            run_dir = stage_dir / "code_task_run"
            (run_dir / "code_task").mkdir(parents=True)
            (run_dir / "code_task" / "task.md").write_text("Implement the experiment code.", encoding="utf-8")
            (run_dir / "manifest.json").write_text(
                '{"workflow": "code_task", "workspace": {"mode": "copy"}}',
                encoding="utf-8",
            )

            record_code_task_memory_event(
                run_dir,
                event_type="work_plan",
                summary="Plan generated project modules.",
                status="done",
                artifacts=["code_task/work_plan.md"],
            )

            paths = code_task_memory_paths(run_dir)
            self.assertEqual(paths.memory_dir, stage_dir / "memory")
            self.assertTrue((stage_dir / "memory" / "task_memory.json").is_file())
            self.assertFalse((run_dir / "code_task" / "memory" / "task_memory.json").exists())

    def test_code_task_memory_compacts_old_events_for_prompt_budget(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp)
            (run_dir / "code_task").mkdir()
            (run_dir / "code_task" / "task.md").write_text("Improve a medium-sized pipeline.", encoding="utf-8")
            (run_dir / "manifest.json").write_text(
                '{"workflow": "code_task", "workspace": {"mode": "copy"}, "edit_scope": {"allowed_patterns": ["src/**"]}}',
                encoding="utf-8",
            )

            for index in range(70):
                record_code_task_memory_event(
                    run_dir,
                    event_type="validation",
                    summary=f"Validation checkpoint {index} completed with reusable lesson {index}.",
                    status="done" if index % 3 else "failed",
                    artifacts=[f"code_task/run/checkpoint-{index}.json"],
                )
            paths = code_task_memory_paths(run_dir)
            memory = read_json(paths.task_memory_json)

            self.assertTrue(paths.compressed_memory_json.is_file())
            self.assertTrue(paths.compressed_memory_md.is_file())
            self.assertLess(len(memory["events"]), 60)
            context = task_memory_context(run_dir)
            self.assertIn("Long-Term Compressed Memory", context)
            self.assertIn("Validation checkpoint", context)

    def test_code_task_reviewer_writes_structured_findings_to_memory(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp)
            workspace = run_dir / "code_task" / "workspace"
            meta = run_dir / "code_task" / "meta"
            workspace.mkdir(parents=True)
            meta.mkdir(parents=True)
            (run_dir / "code_task" / "task.md").write_text("Keep changes inside pkg.", encoding="utf-8")
            (workspace / "outside.py").write_text("VALUE = 1\n", encoding="utf-8")
            (run_dir / "code_task" / "patch.diff").write_text(
                "--- a/outside.py\n+++ b/outside.py\n@@ -1 +1 @@\n-VALUE = 0\n+VALUE = 1\n",
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                '{"workflow": "code_task", "workspace": {"mode": "copy"}, "edit_scope": {"allowed_patterns": ["pkg/**"]}, "patch": {"status": "applied", "changed_files": ["outside.py"]}}',
                encoding="utf-8",
            )

            result = review_code_task_changes(run_dir, use_llm=False)
            findings = read_jsonl(code_task_memory_paths(run_dir).review_findings_jsonl)

            self.assertEqual(result.status, "failed")
            self.assertTrue(result.report_path.is_file())
            self.assertTrue(any(row["category"] == "scope" for row in findings))


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
            self.assertTrue((run_dir / "agent_outputs" / "code-task" / "output_snapshot.json").is_file())
            self.assertTrue((run_dir / "agent_outputs" / "code-task" / "normalized_outputs.json").is_file())
            self.assertTrue(summary["validation_required"])

    def test_ingest_agent_outputs_normalizes_patch_and_generated_files(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp)
            package = build_greenfield_handoff(run_dir, name="greenfield")
            (package.handoff_dir / "patch.diff").write_text(
                "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-old\n+new\n",
                encoding="utf-8",
            )
            (package.handoff_dir / "generated_files" / "pkg").mkdir(parents=True)
            (package.handoff_dir / "generated_files" / "pkg" / "main.py").write_text("print('ok')\n", encoding="utf-8")

            summary = ingest_agent_outputs(run_dir=run_dir, handoff_dir=package.handoff_dir)
            normalized = read_json(run_dir / "agent_outputs" / "greenfield" / "normalized_outputs.json")

            self.assertIn("src/app.py", normalized["changed_files"])
            self.assertIn("generated_files/pkg/main.py", normalized["changed_files"])
            self.assertIn("normalized_outputs", summary)

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
            root = Path(tmp)
            task_file = root / "task.md"
            task_file.write_text(
                "# Task\n\nGenerate a runnable project with accuracy and macro_f1 metrics.\n",
                encoding="utf-8",
            )
            run_dir = root / "greenfield-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=None,
                task_file=task_file,
                kind="greenfield",
                workspace_mode="empty",
                benchmark_command="python generated_project/main.py",
                primary_metric="accuracy",
                metric_directions={"accuracy": "higher_is_better", "macro_f1": "higher_is_better"},
            )

            result = execute_code_task(
                run_dir,
                use_llm=False,
                to_step="work-plan",
                max_generated_lines=200,
                implementation_provider="fake",
                implementation_agent_mode="handoff",
            )

            self.assertEqual(result.stop_reason, "stop_point")
            self.assertTrue((run_dir / "agent_handoff" / "code-task-greenfield-fake").is_dir())
            self.assertTrue((run_dir / "code_task" / "workspace" / "generated_project" / "main.py").is_file())
            backend = read_json(run_dir / "code_task" / "meta" / "code_backend.json")
            self.assertEqual(backend["backend"], "greenfield_agent")
            self.assertEqual(backend["provider"], "fake")
            self.assertEqual(backend["agent_mode"], "handoff")
            self.assertTrue((run_dir / "code_task" / "meta" / "review_report.json").is_file())

    def test_delegated_workspace_mode_fails_explicitly_until_runner_exists(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_file = root / "task.md"
            task_file.write_text("# Task\n\nGenerate a tiny project.\n", encoding="utf-8")
            run_dir = root / "greenfield-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=None,
                task_file=task_file,
                kind="greenfield",
                workspace_mode="empty",
                benchmark_command="python generated_project/main.py",
                primary_metric="accuracy",
            )
            with self.assertRaises(NotImplementedError):
                execute_code_task(
                    run_dir,
                    use_llm=False,
                    to_step="work-plan",
                    max_generated_lines=200,
                    implementation_provider="fake",
                    implementation_agent_mode="delegated_workspace",
                )
            self.assertTrue((run_dir / "code_task" / "meta" / "delegated_workspace_dry_run.json").is_file())

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
