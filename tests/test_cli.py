from __future__ import annotations

import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rich.console import Console

from simple_ar.core.artifacts import read_json, write_json
from simple_ar.cli import _resume_config, main
from simple_ar.cli.parser import build_parser
from simple_ar.cli.code_task_view import confirm_review_gate, render_execute_message
from simple_ar.core.reporting import style_progress_message


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class CliTests(unittest.TestCase):
    def test_research_session_parser_accepts_shared_cache_dir(self) -> None:
        args = build_parser().parse_args(
            [
                "research-session",
                "--topic",
                "reliable agents",
                "--cache-dir",
                "shared-cache",
                "--no-report",
                "--command",
                sys.executable,
                "-c",
                "print('accuracy: 0.9')",
            ]
        )

        self.assertEqual(args.cache_dir, "shared-cache")
        self.assertTrue(args.no_report)
        self.assertEqual(args.command, "research-session")

    def test_research_session_continue_parser_keeps_revised_command(self) -> None:
        args = build_parser().parse_args(
            [
                "research-session-continue",
                "--session-root",
                "runs/session",
                "--primary-metric",
                "accuracy",
                "--metric-direction",
                "accuracy=higher",
                "--command",
                sys.executable,
                "-c",
                "print('accuracy: 0.9')",
            ]
        )

        self.assertEqual(args.command, "research-session-continue")
        self.assertEqual(args.parent_attempt_id, "experiment-001")
        self.assertEqual(
            args.command_argv,
            [sys.executable, "-c", "print('accuracy: 0.9')"],
        )

    def test_code_task_execute_messages_use_shared_rich_styles(self) -> None:
        self.assertEqual(style_progress_message("LLM usage greenfield-file-main.py: 1 input"), "gold1")
        self.assertEqual(
            style_progress_message("Dependency advice: missing optional packages: torch."),
            "bright_yellow",
        )

        stream = io.StringIO()
        console = Console(
            file=stream,
            force_terminal=True,
            color_system="standard",
            highlight=False,
            soft_wrap=True,
            emoji=False,
        )
        render_execute_message("LLM usage greenfield-file-main.py: 1 input", console=console)

        output = stream.getvalue()
        self.assertIn("LLM usage greenfield-file-main.py", output)
        self.assertIn("\x1b[", output)

    def test_review_gate_without_input_stops_cleanly(self) -> None:
        class FakeConsole:
            def __init__(self) -> None:
                self.messages: list[str] = []

            def input(self, _prompt: str) -> str:
                raise EOFError

            def print(self, message: object) -> None:
                self.messages.append(str(message))

        console = FakeConsole()

        self.assertFalse(confirm_review_gate("Approve?", console=console))
        self.assertTrue(confirm_review_gate("Approve?", console=console, assume_yes=True))
        self.assertTrue(any("No interactive input" in message for message in console.messages))

    def test_resume_uses_pipeline_state_and_status_reports_progress(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            output_root = Path(tmp) / "runs"

            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "run",
                        "--topic",
                        "toy topic",
                        "--to-stage",
                        "plan",
                        "--output-root",
                        str(output_root),
                        "--no-llm",
                        "--offline-search",
                        "--quiet",
                    ]
                )

            run_dir = next(output_root.iterdir())
            state = read_json(run_dir / "pipeline_state.json")
            self.assertEqual(state["next_stage"], "search")

            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "resume",
                        str(run_dir),
                        "--to-stage",
                        "search",
                        "--no-llm",
                        "--offline-search",
                        "--quiet",
                    ]
                )

            state = read_json(run_dir / "pipeline_state.json")
            self.assertEqual(state["last_stage"], "search")
            self.assertEqual(state["next_stage"], "read")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["status", str(run_dir)])

            status_text = stdout.getvalue()
            self.assertIn("Pipeline: done", status_text)
            self.assertIn("01 plan: done", status_text)
            self.assertIn("02 search: done", status_text)
            self.assertIn("03 read: pending", status_text)

    def test_status_reports_persisted_research_session_checkpoint(self) -> None:
        from simple_ar.core.capabilities import CapabilityRegistry
        from simple_ar.core.session import SessionController

        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            session_root = Path(tmp) / "session"
            SessionController.create(
                session_root,
                session_id="session-status-test",
                topic="checkpoint topic",
                profile="research_brief",
                registry=CapabilityRegistry(),
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["status", str(session_root)])

            status_text = stdout.getvalue()
            self.assertIn("Session: ", status_text)
            self.assertIn("Topic: checkpoint topic", status_text)
            self.assertIn("Profile: research_brief", status_text)
            self.assertIn("Status: created", status_text)
            self.assertIn("Attempts:\n- none", status_text)

    def test_status_reports_ready_for_report_handoff(self) -> None:
        from simple_ar.core.capabilities import CapabilityContext, CapabilityRegistry, CapabilityResult
        from simple_ar.core.session import SessionController

        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            session_root = Path(tmp) / "session"
            registry = CapabilityRegistry()

            def complete(*, context: CapabilityContext) -> CapabilityResult:
                return CapabilityResult(status="completed")

            registry.register("analysis", complete)
            controller = SessionController.create(
                session_root,
                session_id="session-handoff-test",
                topic="handoff topic",
                profile="full_research",
                registry=registry,
            )
            controller.execute(
                "analysis",
                attempt_id="analysis-001",
                next_capability="report",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["status", str(session_root)])

            self.assertIn("Handoff: ready_for_report (next=report)", stdout.getvalue())

    def test_status_reports_explicit_failure_continuation(self) -> None:
        from simple_ar.core.capabilities import CapabilityContext, CapabilityRegistry, CapabilityResult
        from simple_ar.core.session import SessionController

        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            session_root = Path(tmp) / "session"
            registry = CapabilityRegistry()

            def fail(*, context: CapabilityContext) -> CapabilityResult:
                del context
                return CapabilityResult(
                    status="failed",
                    diagnostics=("temporary provider failure",),
                )

            registry.register("analysis", fail)
            controller = SessionController.create(
                session_root,
                session_id="session-failure-status-test",
                topic="failure status topic",
                registry=registry,
            )
            controller.execute(
                "analysis",
                attempt_id="analysis-001",
                next_capability="analysis",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["status", str(session_root)])

            status_text = stdout.getvalue()
            self.assertIn("Status: running", status_text)
            self.assertIn("Continuation: explicit repair -> analysis", status_text)
            self.assertIn("0 running", status_text)

    def test_research_only_report_run_skips_experiment_stages(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            output_root = Path(tmp) / "runs"

            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "run",
                        "--topic",
                        "toy topic",
                        "--to-stage",
                        "report",
                        "--output-root",
                        str(output_root),
                        "--report-mode",
                        "research_only",
                        "--no-llm",
                        "--offline-search",
                        "--quiet",
                    ]
                )

            run_dir = next(output_root.iterdir())
            self.assertTrue((run_dir / "04-synthesize" / "stage_meta.json").is_file())
            self.assertTrue((run_dir / "08-report" / "report.md").is_file())
            self.assertTrue((run_dir / "08-report" / "citation_map.json").is_file())
            self.assertFalse((run_dir / "05-design" / "stage_meta.json").exists())
            self.assertFalse((run_dir / "06-code" / "stage_meta.json").exists())
            self.assertFalse((run_dir / "07-run" / "stage_meta.json").exists())

    def test_research_report_cli_reads_existing_session_without_rerunning_it(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            paper = root / "reliable_agents.md"
            paper.write_text(
                "# Results\n\nThe fixture reports accuracy: 0.75.\n",
                encoding="utf-8",
            )
            output_root = root / "sessions"
            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "research-session",
                        "--topic",
                        "reliable agents",
                        "--local-document",
                        str(paper),
                        "--output-root",
                        str(output_root),
                        "--cwd",
                        str(root),
                        "--primary-metric",
                        "accuracy",
                        "--metric-direction",
                        "accuracy=higher",
                        "--command",
                        sys.executable,
                        "-c",
                        "print('accuracy: 0.75')",
                    ]
                )
            session_root = next(output_root.iterdir())
            fake_report = SimpleNamespace(
                session_root=session_root,
                status="completed",
                report_ref=SimpleNamespace(path="attempts/report-001/report.md"),
                audit_ref=SimpleNamespace(path="attempts/report-audit-001/report_audit.json"),
            )
            stdout = io.StringIO()
            with (
                patch("simple_ar.cli.main._optional_research_llm_client", return_value=object()),
                patch(
                    "simple_ar.app.research_report.run_research_session_report_agent",
                    return_value=fake_report,
                ) as runner,
                contextlib.redirect_stdout(stdout),
            ):
                main(
                    [
                        "research-report",
                        "--session-root",
                        str(session_root),
                        "--model",
                        "gpt-5.4",
                    ]
                )

            runner.assert_called_once()
            self.assertIn("Status: completed", stdout.getvalue())
            self.assertIn(
                str(session_root / "attempts" / "report-001" / "report.md"),
                stdout.getvalue(),
            )

    def test_research_session_cli_can_append_report_in_one_explicit_flow(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            paper = root / "reliable_agents.md"
            paper.write_text(
                "# Results\n\nThe fixture reports accuracy: 0.75.\n",
                encoding="utf-8",
            )
            output_root = root / "sessions"
            session = SimpleNamespace(
                session_root=output_root / "research-session",
                status="ready_for_report",
                plan=SimpleNamespace(query_plan=SimpleNamespace(planner="fixture")),
                brief=SimpleNamespace(generation_mode="deterministic"),
                search=SimpleNamespace(papers=[object()]),
                documents=SimpleNamespace(records=[object()]),
                execution_ref=SimpleNamespace(path="attempts/experiment-001/results.json"),
                analysis_ref=SimpleNamespace(path="attempts/analysis-001/analysis.json"),
            )
            report = SimpleNamespace(
                session_root=session.session_root,
                status="completed",
                report_ref=SimpleNamespace(path="attempts/report-001/report.md"),
                audit_ref=SimpleNamespace(path="attempts/report-audit-001/report_audit.json"),
            )
            with (
                patch("simple_ar.cli.main._optional_research_llm_client", return_value=object()),
                patch(
                    "simple_ar.app.research_session.run_research_session",
                    return_value=session,
                ) as session_runner,
                patch(
                    "simple_ar.app.research_report.run_research_session_report_agent",
                    return_value=report,
                ) as report_runner,
                patch(
                    "simple_ar.report.templates.load_report_template_bundle",
                    return_value=object(),
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                main(
                    [
                        "research-session",
                        "--topic",
                        "reliable agents",
                        "--local-document",
                        str(paper),
                        "--output-root",
                        str(output_root),
                        "--cwd",
                        str(root),
                        "--model",
                        "gpt-5.4",
                        "--with-report",
                        "--report-reviewer",
                        "disabled",
                        "--max-review-iterations",
                        "0",
                        "--command",
                        sys.executable,
                        "-c",
                        "print('accuracy: 0.75')",
                    ]
                )

            session_runner.assert_called_once()
            report_runner.assert_called_once()

    def test_research_session_cli_defaults_to_report_with_model(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            paper = root / "reliable_agents.md"
            paper.write_text(
                "# Results\n\nThe fixture reports accuracy: 0.75.\n",
                encoding="utf-8",
            )
            output_root = root / "sessions"
            session = SimpleNamespace(
                session_root=output_root / "research-session",
                status="ready_for_report",
                plan=SimpleNamespace(query_plan=SimpleNamespace(planner="fixture")),
                brief=SimpleNamespace(generation_mode="llm"),
                search=SimpleNamespace(papers=[object()]),
                documents=SimpleNamespace(records=[object()]),
                execution_ref=SimpleNamespace(path="attempts/experiment-001/results.json"),
                analysis_ref=SimpleNamespace(path="attempts/analysis-001/analysis.json"),
            )
            report = SimpleNamespace(
                session_root=session.session_root,
                status="completed",
                report_ref=SimpleNamespace(path="attempts/report-001/report.md"),
                audit_ref=SimpleNamespace(path="attempts/report-audit-001/report_audit.json"),
            )
            with (
                patch("simple_ar.cli.main._optional_research_llm_client", return_value=object()),
                patch(
                    "simple_ar.app.research_session.run_research_session",
                    return_value=session,
                ) as session_runner,
                patch(
                    "simple_ar.app.research_report.run_research_session_report_agent",
                    return_value=report,
                ) as report_runner,
                patch(
                    "simple_ar.report.templates.load_report_template_bundle",
                    return_value=object(),
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                main(
                    [
                        "research-session",
                        "--topic",
                        "reliable agents",
                        "--local-document",
                        str(paper),
                        "--output-root",
                        str(output_root),
                        "--cwd",
                        str(root),
                        "--model",
                        "gpt-5.4",
                        "--command",
                        sys.executable,
                        "-c",
                        "print('accuracy: 0.75')",
                    ]
                )

            session_runner.assert_called_once()
            report_runner.assert_called_once()

    def test_research_session_cli_builds_code_task_request_from_config(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            task_file = root / "task.md"
            task_file.write_text("Improve the fixture.", encoding="utf-8")
            config = root / "code_task.toml"
            config.write_text(
                "[code_task]\n"
                f'code_root = "{project.as_posix()}"\n'
                f'task_file = "{task_file.as_posix()}"\n'
                "[benchmark]\n"
                'command = "python benchmark.py"\n'
                'primary_metric = "accuracy"\n'
                "[benchmark.metric_directions]\n"
                'accuracy = "higher"\n'
                "[execute]\n"
                "use_llm = true\n"
                "timeout_sec = 7\n"
                'baseline_policy = "skip"\n',
                encoding="utf-8",
            )
            fake_result = SimpleNamespace(
                session_root=root / "session",
                status="ready_for_report",
                plan=SimpleNamespace(query_plan=SimpleNamespace(planner="fixture")),
                brief=SimpleNamespace(generation_mode="llm"),
                search=SimpleNamespace(papers=[]),
                documents=SimpleNamespace(records=[]),
                execution_ref=SimpleNamespace(path="attempts/experiment-001/results.json"),
                analysis_ref=SimpleNamespace(path="attempts/analysis-001/analysis.json"),
            )
            with (
                patch("simple_ar.cli.main._optional_research_llm_client", return_value=object()),
                patch(
                    "simple_ar.app.research_session.run_research_session",
                    return_value=fake_result,
                ) as runner,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                main(
                    [
                        "research-session",
                        "--topic",
                        "fixture research",
                        "--output-root",
                        str(root / "sessions"),
                        "--cwd",
                        str(root),
                        "--model",
                        "gpt-5.4",
                        "--no-report",
                        "--code-task-config",
                        str(config),
                    ]
                )

            request = runner.call_args.args[0]
            self.assertEqual(request.command, ())
            self.assertEqual(request.code_task_spec.code_root, project)
            self.assertEqual(request.code_task_spec.task_file, task_file)
            self.assertEqual(request.timeout_sec, 7)
            self.assertEqual(request.baseline_policy, "skip")
            self.assertEqual(request.code_task_model, "gpt-5.4")

    def test_research_code_task_cli_builds_request_from_existing_config(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            task_file = root / "task.md"
            task_file.write_text("Improve the fixture.", encoding="utf-8")
            synthesis_file = root / "synthesis.json"
            synthesis_file.write_text("{}", encoding="utf-8")
            config = root / "code_task.toml"
            config.write_text(
                "[code_task]\n"
                f'code_root = "{project.as_posix()}"\n'
                f'task_file = "{task_file.as_posix()}"\n'
                "[benchmark]\n"
                'command = "python benchmark.py"\n'
                'primary_metric = "accuracy"\n'
                "[benchmark.metric_directions]\n"
                'accuracy = "higher"\n'
                "[execute]\n"
                "use_llm = true\n"
                "timeout_sec = 7\n"
                'baseline_policy = "skip"\n',
                encoding="utf-8",
            )
            fake_result = SimpleNamespace(
                session_root=root / "session",
                status="partial",
                execution_path=root / "session" / "execution.json",
                analysis_path=root / "session" / "analysis.json",
            )
            with patch(
                "simple_ar.app.research_code_task.run_research_code_task_session",
                return_value=fake_result,
            ) as runner:
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    with self.assertRaises(SystemExit) as raised:
                        main(
                            [
                                "research-code-task",
                                "--topic",
                                "fixture research",
                                "--synthesis-file",
                                str(synthesis_file),
                                "--code-task-config",
                                str(config),
                                "--output-root",
                                str(root / "runs"),
                            ]
                        )

            request = runner.call_args.args[0]
            self.assertEqual(request.topic, "fixture research")
            self.assertEqual(request.spec.code_root, project)
            self.assertEqual(request.spec.task_file, task_file)
            self.assertEqual(request.timeout_sec, 7)
            self.assertEqual(request.baseline_policy, "skip")
            self.assertIn("Status: partial", stdout.getvalue())
            self.assertIn("ended with status 'partial'", str(raised.exception))

    def test_research_code_task_cli_can_append_report_after_one_candidate(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            task_file = root / "task.md"
            task_file.write_text("Improve the fixture.", encoding="utf-8")
            synthesis_file = root / "synthesis.json"
            synthesis_file.write_text("{}", encoding="utf-8")
            config = root / "code_task.toml"
            config.write_text(
                "[code_task]\n"
                f'code_root = "{project.as_posix()}"\n'
                f'task_file = "{task_file.as_posix()}"\n'
                "[benchmark]\n"
                'command = "python benchmark.py"\n'
                'primary_metric = "accuracy"\n'
                "[execute]\n"
                "use_llm = true\n",
                encoding="utf-8",
            )
            session = SimpleNamespace(
                session_root=root / "session",
                status="completed",
                execution_path=root / "session" / "execution.json",
                analysis_path=root / "session" / "analysis.json",
            )
            report = SimpleNamespace(
                session_root=session.session_root,
                status="completed",
                report_ref=SimpleNamespace(path="attempts/report-001/report.md"),
                audit_ref=SimpleNamespace(path="attempts/report-audit-001/report_audit.json"),
            )
            with (
                patch("simple_ar.cli.main._optional_research_llm_client", return_value=object()),
                patch(
                    "simple_ar.app.research_code_task.run_research_code_task_session",
                    return_value=session,
                ) as runner,
                patch(
                    "simple_ar.app.research_code_task_report.run_research_code_task_report_agent",
                    return_value=report,
                ) as report_runner,
                patch(
                    "simple_ar.report.templates.load_report_template_bundle",
                    return_value=object(),
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                main(
                    [
                        "research-code-task",
                        "--topic",
                        "fixture research",
                        "--synthesis-file",
                        str(synthesis_file),
                        "--code-task-config",
                        str(config),
                        "--output-root",
                        str(root / "runs"),
                        "--model",
                        "gpt-5.4",
                        "--with-report",
                    ]
                )

            self.assertEqual(runner.call_args.kwargs["next_capability"], "report")
            report_runner.assert_called_once()

    def test_research_code_task_cli_rejects_non_positive_timeout_override(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            task_file = root / "task.md"
            task_file.write_text("Improve the fixture.", encoding="utf-8")
            synthesis_file = root / "synthesis.json"
            synthesis_file.write_text("{}", encoding="utf-8")
            config = root / "code_task.toml"
            config.write_text(
                "[code_task]\n"
                f'code_root = "{project.as_posix()}"\n'
                f'task_file = "{task_file.as_posix()}"\n'
                "[benchmark]\n"
                'command = "python benchmark.py"\n'
                'primary_metric = "accuracy"\n'
                "[benchmark.metric_directions]\n"
                'accuracy = "higher"\n'
                "[execute]\n"
                "use_llm = true\n"
                "timeout_sec = 7\n",
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "research-code-task",
                        "--topic",
                        "fixture research",
                        "--synthesis-file",
                        str(synthesis_file),
                        "--code-task-config",
                        str(config),
                        "--timeout-sec",
                        "0",
                    ]
                )
            self.assertIn("must be positive", str(raised.exception))

    def test_inspect_and_search_artifacts_commands_write_retrieval_files(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            output_root = Path(tmp) / "runs"

            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "run",
                        "--topic",
                        "toy topic",
                        "--to-stage",
                        "plan",
                        "--output-root",
                        str(output_root),
                        "--no-llm",
                        "--offline-search",
                        "--quiet",
                    ]
                )

            run_dir = next(output_root.iterdir())

            inspect_stdout = io.StringIO()
            with contextlib.redirect_stdout(inspect_stdout):
                main(["inspect", str(run_dir)])

            self.assertIn("Artifacts:", inspect_stdout.getvalue())
            self.assertTrue((run_dir / "artifact_index.json").is_file())

            search_stdout = io.StringIO()
            with contextlib.redirect_stdout(search_stdout):
                main(["search-artifacts", str(run_dir), "research", "--top-k", "2"])

            self.assertIn("Matches:", search_stdout.getvalue())
            self.assertIn("Operational metadata included: False", search_stdout.getvalue())
            self.assertTrue((run_dir / "artifact_chunks.jsonl").is_file())
            self.assertTrue((run_dir / "artifact_search_results.json").is_file())

    def test_clean_removes_rebuildable_run_caches_and_shared_index_rows(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "clean-me"
            documents_dir = run_dir / "02-search" / "documents"
            cache_dir = documents_dir / "fulltext_cache"
            text_dir = documents_dir / "extracted_text"
            index_dir = run_dir / "02-search" / "research_index"
            cache_dir.mkdir(parents=True)
            text_dir.mkdir(parents=True)
            index_dir.mkdir(parents=True)
            (cache_dir / "paper.pdf").write_bytes(b"%PDF fake")
            (text_dir / "paper.txt").write_text("parsed paper text", encoding="utf-8")
            (documents_dir / "fulltext_extraction.json").write_text("{}\n", encoding="utf-8")
            (run_dir / "02-search" / "papers.jsonl").write_text("{}\n", encoding="utf-8")
            (index_dir / "chunks.jsonl").write_text("{}\n", encoding="utf-8")

            sqlite_path = root / ".simple_ar_cache" / "research_index" / "sqlite_fts.db"
            sqlite_path.parent.mkdir(parents=True)
            conn = sqlite3.connect(sqlite_path)
            conn.execute("CREATE TABLE chunks(run_id TEXT, text TEXT)")
            conn.execute("INSERT INTO chunks VALUES ('clean-me', 'delete')")
            conn.execute("INSERT INTO chunks VALUES ('other-run', 'keep')")
            conn.commit()
            conn.close()
            write_json(
                index_dir / "index_meta.json",
                {
                    "store": {"run_id": "clean-me"},
                    "sqlite_fts": {"status": "ready", "path": str(sqlite_path)},
                },
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["clean", str(run_dir), "--yes"])

            self.assertFalse(cache_dir.exists())
            self.assertFalse(text_dir.exists())
            self.assertTrue((documents_dir / "fulltext_extraction.json").exists())
            self.assertTrue((run_dir / "02-search" / "papers.jsonl").exists())
            self.assertTrue((index_dir / "chunks.jsonl").exists())
            index_meta = read_json(index_dir / "index_meta.json")
            self.assertEqual(index_meta["sqlite_fts"]["status"], "cleaned")
            conn = sqlite3.connect(sqlite_path)
            rows = conn.execute("SELECT run_id FROM chunks ORDER BY run_id").fetchall()
            conn.close()
            self.assertEqual(rows, [("other-run",)])
            self.assertIn("Will delete", stdout.getvalue())
            self.assertIn("Will keep", stdout.getvalue())

    def test_clean_all_caches_removes_every_rebuildable_cache(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "clean-all"
            documents_dir = run_dir / "02-search" / "documents"
            cache_dir = documents_dir / "fulltext_cache"
            text_dir = documents_dir / "extracted_text"
            index_dir = run_dir / "02-search" / "research_index"
            code_meta = run_dir / "code_task" / "meta"
            context_dir = run_dir / "code_task" / "context_packs" / "context-001"
            report_dir = run_dir / "08-report"
            for path in (cache_dir, text_dir, index_dir, code_meta, context_dir, report_dir):
                path.mkdir(parents=True)
            (cache_dir / "paper.pdf").write_bytes(b"%PDF fake")
            (text_dir / "paper.txt").write_text("parsed paper text", encoding="utf-8")
            (documents_dir / "fulltext_extraction.json").write_text("{}\n", encoding="utf-8")
            (run_dir / "02-search" / "papers.jsonl").write_text("{}\n", encoding="utf-8")
            (index_dir / "chunks.jsonl").write_text("{}\n", encoding="utf-8")
            (run_dir / "artifact_index.json").write_text("{}\n", encoding="utf-8")
            (run_dir / "artifact_chunks.jsonl").write_text("{}\n", encoding="utf-8")
            (run_dir / "artifact_search_results.json").write_text("{}\n", encoding="utf-8")
            (code_meta / "codebase_index.json").write_text("{}\n", encoding="utf-8")
            (code_meta / "repo_map.json").write_text("{}\n", encoding="utf-8")
            (code_meta / "repo_map_summary.md").write_text("# Repo Map\n", encoding="utf-8")
            (code_meta / "locate_results.json").write_text("{}\n", encoding="utf-8")
            (code_meta / "locate_results.md").write_text("# Locate\n", encoding="utf-8")
            (context_dir / "context_pack.json").write_text("{}\n", encoding="utf-8")
            (report_dir / "report.md").write_text("# Report\n", encoding="utf-8")

            sqlite_path = root / ".simple_ar_cache" / "research_index" / "sqlite_fts.db"
            sqlite_path.parent.mkdir(parents=True)
            conn = sqlite3.connect(sqlite_path)
            conn.execute("CREATE TABLE chunks(run_id TEXT, text TEXT)")
            conn.execute("INSERT INTO chunks VALUES ('clean-all', 'delete')")
            conn.execute("INSERT INTO chunks VALUES ('other-run', 'keep')")
            conn.commit()
            conn.close()
            write_json(
                index_dir / "index_meta.json",
                {
                    "store": {"run_id": "clean-all"},
                    "sqlite_fts": {"status": "ready", "path": str(sqlite_path)},
                },
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["clean", str(run_dir), "--all-caches", "--yes"])

            self.assertFalse(cache_dir.exists())
            self.assertFalse(text_dir.exists())
            self.assertFalse(index_dir.exists())
            self.assertFalse((run_dir / "artifact_index.json").exists())
            self.assertFalse((run_dir / "artifact_chunks.jsonl").exists())
            self.assertFalse((run_dir / "artifact_search_results.json").exists())
            self.assertFalse((code_meta / "codebase_index.json").exists())
            self.assertFalse((code_meta / "repo_map.json").exists())
            self.assertFalse((code_meta / "repo_map_summary.md").exists())
            self.assertFalse((code_meta / "locate_results.json").exists())
            self.assertFalse((code_meta / "locate_results.md").exists())
            self.assertFalse((run_dir / "code_task" / "context_packs").exists())
            self.assertTrue((documents_dir / "fulltext_extraction.json").exists())
            self.assertTrue((run_dir / "02-search" / "papers.jsonl").exists())
            self.assertTrue((report_dir / "report.md").exists())
            conn = sqlite3.connect(sqlite_path)
            rows = conn.execute("SELECT run_id FROM chunks ORDER BY run_id").fetchall()
            conn.close()
            self.assertEqual(rows, [("other-run",)])
            self.assertIn("All-cache cleanup is enabled", stdout.getvalue())
            self.assertIn("Deleted shared SQLite index rows: 1", stdout.getvalue())

    def test_clean_shared_index_clears_cross_run_index_store(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            index_root = root / ".simple_ar_cache" / "research_index"
            lancedb_dir = index_root / "lancedb"
            index_root.mkdir(parents=True)
            lancedb_dir.mkdir()
            sqlite_path = index_root / "sqlite_fts.db"
            conn = sqlite3.connect(sqlite_path)
            conn.execute("CREATE TABLE chunks(run_id TEXT, text TEXT)")
            conn.execute("INSERT INTO chunks VALUES ('run-a', 'delete')")
            conn.commit()
            conn.close()
            (lancedb_dir / "table.lance").write_text("fake lancedb data", encoding="utf-8")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["clean", "--shared-index", "--index-root", str(index_root), "--yes"])

            self.assertTrue(index_root.exists())
            self.assertEqual(list(index_root.iterdir()), [])
            self.assertIn("Shared-index cleanup is enabled", stdout.getvalue())
            self.assertIn("Cleaned targets: 2", stdout.getvalue())

    def test_clean_shared_cache_clears_index_and_literature_cache(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            cache_root = root / ".simple_ar_cache"
            index_root = cache_root / "research_index"
            literature_root = cache_root / "literature"
            agent_archive_root = cache_root / "agent_handoff_archives"
            index_root.mkdir(parents=True)
            literature_root.mkdir(parents=True)
            agent_archive_root.mkdir(parents=True)
            (index_root / "chunks.sqlite").write_text("index", encoding="utf-8")
            (literature_root / "cached-provider-response.json").write_text("{}", encoding="utf-8")
            (agent_archive_root / "old-handoff" / "stderr.txt").parent.mkdir()
            (agent_archive_root / "old-handoff" / "stderr.txt").write_text("old failure", encoding="utf-8")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(
                    [
                        "clean",
                        "--shared-cache",
                        "--index-root",
                        str(index_root),
                        "--literature-cache-root",
                        str(literature_root),
                        "--yes",
                    ]
                )

            self.assertFalse(index_root.exists())
            self.assertFalse(literature_root.exists())
            self.assertFalse(agent_archive_root.exists())
            output = stdout.getvalue()
            self.assertIn("Shared-cache cleanup is enabled", output)
            self.assertIn("literature", output)
            self.assertIn("agent_handoff_archives", output)

    def test_resume_config_preserves_saved_values_without_cli_overrides(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            write_json(
                run_dir / "config_snapshot.json",
                {
                    "mode": "offline",
                    "model": "saved-model",
                    "llm_max_workers": 2,
                    "max_papers": 3,
                    "experiment_template": "llm_code_task_toy_spam",
                    "experiment_timeout_sec": 60,
                    "use_llm": False,
                    "use_arxiv": False,
                    "allow_fixture_fallback": True,
                    "strict_search": False,
                    "use_retrieval": True,
                    "retrieval_top_k": 7,
                },
            )
            args = SimpleNamespace(
                to_stage="report",
                model=None,
                llm_workers=None,
                max_papers=None,
                search_query=None,
                experiment_template=None,
                experiment_timeout=None,
                retrieval_top_k=None,
                report_mode=None,
                no_llm=False,
                offline_search=False,
                allow_fixture_fallback=False,
                strict_search=False,
                no_retrieval=False,
            )

            config = _resume_config(run_dir, args, "report")

            self.assertEqual(config["experiment_template"], "llm_code_task_toy_spam")
            self.assertEqual(config["experiment_timeout_sec"], 60)
            self.assertEqual(config["retrieval_top_k"], 7)
            self.assertEqual(config["use_llm"], False)
            self.assertEqual(config["use_arxiv"], False)

            args.experiment_timeout = 15
            args.no_retrieval = True
            overridden = _resume_config(run_dir, args, "report")
            self.assertEqual(overridden["experiment_timeout_sec"], 15)
            self.assertEqual(overridden["use_retrieval"], False)

    def test_run_config_can_drive_code_task_project_design(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            output_root = root / "configured_runs"
            config_path = root / "pipeline.toml"
            config_path.write_text(
                "\n".join(
                    [
                        "[run]",
                        'topic = "configured tiny digits"',
                        f'output_root = "{output_root.as_posix()}"',
                        'to_stage = "design"',
                        "",
                        "[llm]",
                        "enabled = false",
                        "",
                        "[search]",
                        "offline = true",
                        "max_papers = 1",
                        "",
                        "[experiment]",
                        'template = "code_task_project"',
                        "timeout = 11",
                        "",
                        "[code_task]",
                        f'code_root = "{(repo_root / "examples" / "full_pipeline_tiny_mlp" / "project").as_posix()}"',
                        f'task_file = "{(repo_root / "examples" / "code_tasks" / "tasks" / "improve_tiny_digits_mlp.md").as_posix()}"',
                        'name = "configured-pipeline-task"',
                        "",
                        "[benchmark]",
                        'command = "python benchmark.py"',
                        'primary_metric = "accuracy"',
                        "",
                        "[benchmark.metric_directions]",
                        'accuracy = "higher"',
                        "",
                        "[workspace]",
                        'mode = "copy"',
                        'include = ["src/**", "benchmark.py"]',
                        'exclude = ["data/**"]',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with contextlib.redirect_stdout(io.StringIO()):
                main(["run", "--config", str(config_path), "--quiet"])

            run_dir = next(output_root.iterdir())
            snapshot = read_json(run_dir / "config_snapshot.json")
            self.assertEqual(snapshot["experiment_template"], "code_task_project")
            self.assertEqual(snapshot["experiment_timeout_sec"], 11)
            self.assertEqual(snapshot["use_llm"], False)
            self.assertEqual(snapshot["use_arxiv"], False)
            self.assertEqual(snapshot["code_task_config"], str(config_path))

            plan = read_json(run_dir / "05-design" / "experiment_plan.json")
            self.assertEqual(plan["template"], "code_task_project")
            self.assertEqual(plan["code_task"]["benchmark_command"], "python benchmark.py")
            self.assertEqual(plan["code_task"]["primary_metric"], "accuracy")
            self.assertEqual(plan["code_task"]["workspace_mode"], "copy")
            self.assertEqual(plan["code_task"]["workspace_include"], ["src/**", "benchmark.py"])
            self.assertEqual(plan["code_task"]["workspace_exclude"], ["data/**"])

    def test_code_task_init_git_worktree_error_gives_next_steps(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "no_git_project"
            code_root.mkdir()
            task_file = root / "task.md"
            task_file.write_text("# Task\n\nImprove this project.\n", encoding="utf-8")

            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--workspace-mode",
                        "git_worktree",
                        "--output-root",
                        str(root / "runs"),
                    ]
                )

            message = str(raised.exception)
            self.assertIn("Could not initialize code task", message)
            self.assertIn("git_worktree quick checklist", message)
            self.assertIn("--workspace-mode copy", message)

    def test_code_task_init_missing_task_file_gives_path_hint(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "project"
            code_root.mkdir()

            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(root / "missing-task.md"),
                        "--output-root",
                        str(root / "runs"),
                    ]
                )

            message = str(raised.exception)
            self.assertIn("Check the task file path", message)
            self.assertIn("[code_task].task_file", message)


if __name__ == "__main__":
    unittest.main()
