from __future__ import annotations

import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, create_autospec, patch

from rich.console import Console

from simple_ar.core.artifacts import read_json, write_json
from simple_ar.core import ArtifactStore
from simple_ar.cli import main
from simple_ar.cli.parser import build_parser
from simple_ar.cli.code_task_view import confirm_review_gate, render_execute_message
from simple_ar.core.reporting import style_progress_message


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class CliTests(unittest.TestCase):
    def test_research_report_does_not_rewrite_or_execute_a_historical_session(self):
        from tests.legacy_session_fixture import historical_session
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "session"
            historical_session(root)
            before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            with patch("simple_ar.cli.main._optional_research_llm_client", return_value=object()):
                with self.assertRaisesRegex(SystemExit, "No legacy workflow was executed"):
                    main(["research-report", "--session-root", str(root), "--model", "scripted"])
            after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(before, after)

    def test_segmented_code_task_report_retires_before_loading_config(self):
        with self.assertRaisesRegex(SystemExit, "research-session"):
            main(["research-code-task", "--topic", "task", "--synthesis-file", "missing.json",
                  "--code-task-config", "missing.toml", "--with-report"])

    def test_historical_stage_status_is_readable_without_running_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {"topic": "Historical research", "stages": [
                {"stage_number": 1, "stage": "plan", "status": "done", "outputs": ["problem.md"]}
            ]}
            write_json(root / "manifest.json", manifest)
            write_json(root / "pipeline_state.json", {
                "status": "failed", "last_stage": "search", "next_stage": "read",
            })
            write_json(root / "state.json", {
                "schema_version": "workspace_state.v1", "run_id": "archived",
                "topic": "Historical research", "search": {"status": "failed"},
                "artifact_aliases": {"papers.jsonl": "02-search/papers.jsonl"},
            })
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main(["status", str(root)])
            self.assertIn("Historical research", output.getvalue())
            self.assertIn("01 plan:", output.getvalue())
            self.assertIn("Pipeline: failed (last=search, next=read)", output.getvalue())
            self.assertEqual(read_json(root / "manifest.json"), manifest)
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)

    def test_retired_pipeline_commands_do_not_read_config_or_write_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for command in ("run", "resume", "research-code-task", "research-experiment"):
                with self.subTest(command=command), self.assertRaisesRegex(SystemExit, "retired.*research-session"):
                    main([command, "--config", str(root / "missing.toml"),
                          "--output-root", str(root / "runs")])
            self.assertEqual(list(root.iterdir()), [])

    def test_research_session_parser_allows_literature_only_mode(self) -> None:
        args = build_parser().parse_args(
            ["research-session", "--topic", "reliable agents", "--no-report"]
        )

        self.assertIsNone(args.command_argv)
        self.assertIsNone(args.code_task_config)

    def test_research_session_cli_without_command_stays_literature_only(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            session_root = root / "sessions" / "research-session"
            final_view = SimpleNamespace(
                session_root=session_root,
                status="completed",
                status_reason="",
                next_action=None,
                state_refs={"summary": SimpleNamespace(path="outputs/research_summary.md")},
                attempts=(),
            )
            app = MagicMock()
            app.services = SimpleNamespace(max_attempts=5)
            app.view.side_effect = [
                SimpleNamespace(
                    session_root=session_root, status="running", status_reason="",
                    next_action="plan", state_refs={}, attempts=(),
                ),
                final_view,
            ]
            app.advance.return_value = final_view
            stdout = io.StringIO()
            with (
                patch("simple_ar.cli.main._optional_research_llm_client", return_value=None),
                patch("simple_ar.app.research_application.create_session", return_value=app) as creator,
                contextlib.redirect_stdout(stdout),
            ):
                main([
                    "research-session",
                    "--topic", "reliable agents",
                    "--output-root", str(root / "sessions"),
                    "--no-report",
                ])

            brief = creator.call_args.args[0]
            services = creator.call_args.kwargs["services"]
            self.assertEqual(brief.requested_outputs, ("summary",))
            self.assertEqual(brief.intents, ("research",))
            self.assertNotIn("execution", services.config)
            self.assertEqual(services.budget_limits["process_invocations"], 0)
            self.assertIn("summary: ", stdout.getvalue())

    def test_research_session_cli_without_command_with_model_requests_survey_report(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            session_root = root / "sessions" / "research-session"
            final_view = SimpleNamespace(
                session_root=session_root,
                status="completed",
                status_reason="",
                next_action=None,
                state_refs={"report": SimpleNamespace(path="attempts/report/report.md")},
                attempts=(),
            )
            app = MagicMock()
            app.services = SimpleNamespace(max_attempts=5)
            app.view.side_effect = [
                SimpleNamespace(
                    session_root=session_root, status="running", status_reason="",
                    next_action="plan", state_refs={}, attempts=(),
                ),
                final_view,
            ]
            app.advance.return_value = final_view
            with (
                patch("simple_ar.cli.main._optional_research_llm_client", return_value=object()),
                patch("simple_ar.app.research_application.create_session", return_value=app) as creator,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                main([
                    "research-session",
                    "--topic", "reliable agents",
                    "--output-root", str(root / "sessions"),
                ])

            brief = creator.call_args.args[0]
            services = creator.call_args.kwargs["services"]
            self.assertEqual(brief.requested_outputs, ("report",))
            self.assertEqual(services.config["report"]["mode"], "research_only")
            self.assertEqual(services.config["report"]["template"], "survey")
            self.assertEqual(services.budget_limits["process_wall_seconds"], 0)

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
        self.assertIsNone(args.parent_attempt_id)
        self.assertEqual(
            args.command_argv,
            [sys.executable, "-c", "print('accuracy: 0.9')"],
        )

    def test_canonical_recovery_cli_uses_application_retry_boundary(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp) / "session"
            root.mkdir()
            (root / "session_manifest.json").write_text("{}", encoding="utf-8")
            view = SimpleNamespace(
                session_root=root,
                status="completed",
                next_action=None,
                state_refs={
                    "experiment": SimpleNamespace(
                        path="attempts/experiment-0002/experiment_result.json"
                    )
                },
            )
            app = MagicMock()
            app.retry_experiment.return_value = view
            stdout = io.StringIO()
            with patch(
                "simple_ar.app.research_application.load_session", return_value=app
            ), contextlib.redirect_stdout(stdout):
                main([
                    "research-session-continue",
                    "--session-root",
                    str(root),
                    "--cwd",
                    str(root),
                    "--command",
                    sys.executable,
                    "-c",
                    "print('accuracy: 0.9')",
                ])

            call = app.retry_experiment.call_args
            self.assertEqual(call.kwargs["command"], (sys.executable, "-c", "print('accuracy: 0.9')"))
            self.assertIsNone(call.kwargs["parent_attempt_id"])
            self.assertIn("Status: completed", stdout.getvalue())
            self.assertIn("experiment-0002", stdout.getvalue())

    def test_legacy_session_continue_does_not_execute_or_rewrite_history(self) -> None:
        from tests.legacy_session_fixture import historical_session

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "legacy"
            historical_session(root, failed=True)
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with self.assertRaises(SystemExit):
                main([
                    "research-session-continue", "--session-root", str(root),
                    "--cwd", str(root), "--command", sys.executable, "-c",
                    "from pathlib import Path; Path('unexpected-execution').touch()",
                ])
            after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(after, before)

    def test_research_session_migrate_parser_accepts_explicit_artifacts(self) -> None:
        args = build_parser().parse_args(
            [
                "research-session-migrate",
                "--source-root",
                "runs/legacy",
                "--destination-root",
                "runs/successor",
                "--artifact",
                "search",
                "--requested-output",
                "report",
            ]
        )

        self.assertEqual(args.command, "research-session-migrate")
        self.assertEqual(args.artifact_names, ["search"])
        self.assertEqual(args.requested_outputs, ["report"])

    def test_research_session_migrate_cli_reports_new_successor(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            legacy = root / "legacy"
            successor = root / "successor"
            store = ArtifactStore(legacy)
            search_ref = store.write_json(
                "state/search.json",
                {"legacy": True},
                kind="search",
                schema="legacy_search.v1",
                producer="legacy",
            )
            store.write_json(
                "session_manifest.json",
                {
                    "schema_version": "session_manifest.v1",
                    "session_id": "legacy-cli-session",
                    "topic": "CLI migration",
                    "status": "created",
                    "state_refs": {"search": search_ref.to_dict()},
                    "budget": {},
                    "decisions": [],
                },
                kind="session",
                schema="session_manifest.v1",
                producer="legacy",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(
                    [
                        "research-session-migrate",
                        "--source-root",
                        str(legacy),
                        "--destination-root",
                        str(successor),
                        "--artifact",
                        "search",
                    ]
                )

            self.assertIn("Successor session:", stdout.getvalue())
            self.assertIn("Historical budget: unknown_not_imported", stdout.getvalue())
            manifest = ArtifactStore(successor).read_json("session_manifest.json")
            self.assertEqual(manifest["parent_session"], "legacy-cli-session")
            self.assertTrue(
                (successor / "compatibility" / "imported" / "01-search.json").is_file()
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
        from simple_ar.core import ArtifactStore

        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            session_root = Path(tmp) / "session"
            # Recorded legacy shape: status inspection must not require a
            # functioning old workflow executor to reconstruct history.
            ArtifactStore(session_root).write_json("session_manifest.json", {
                "schema_version": "session_manifest.v1",
                "session_id": "historical-status",
                "topic": "historical handoff",
                "status": "running",
                "budget": {"attempts": 1},
                "decisions": [{
                    "capability": "analysis", "attempt_id": "analysis-001",
                    "action": "accept", "result_status": "completed",
                    "next_capability": "report",
                }],
            })
            original = (session_root / "session_manifest.json").read_bytes()

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["status", str(session_root)])
            self.assertEqual((session_root / "session_manifest.json").read_bytes(), original)

            self.assertIn("Handoff: ready_for_report (next=report)", stdout.getvalue())

    def test_status_reports_explicit_failure_continuation(self) -> None:
        from simple_ar.core import ArtifactStore

        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            session_root = Path(tmp) / "session"
            # Recorded legacy shape: status inspection must not require a
            # functioning old workflow executor to reconstruct history.
            ArtifactStore(session_root).write_json("session_manifest.json", {
                "schema_version": "session_manifest.v1",
                "session_id": "historical-status",
                "topic": "historical handoff",
                "status": "running",
                "budget": {"attempts": 1},
                "decisions": [{
                    "capability": "analysis", "attempt_id": "analysis-001",
                    "action": "repair", "result_status": "failed",
                    "next_capability": "analysis",
                }],
            })
            original = (session_root / "session_manifest.json").read_bytes()

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["status", str(session_root)])
            self.assertEqual((session_root / "session_manifest.json").read_bytes(), original)

            status_text = stdout.getvalue()
            self.assertIn("Status: running", status_text)
            self.assertIn("Continuation: explicit repair -> analysis", status_text)
            self.assertIn("0 running", status_text)


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
            final_view = SimpleNamespace(
                session_root=session_root,
                status="completed",
                status_reason="",
                next_action=None,
                state_refs={
                    "report": SimpleNamespace(path="attempts/report-001/report.md"),
                    "report_audit": SimpleNamespace(path="attempts/report-audit-001/report_audit.json"),
                },
                attempts=(),
            )
            app = MagicMock()
            app.brief = SimpleNamespace(requested_outputs=("experiments",))
            app.services = SimpleNamespace(max_attempts=4, config={})
            app.controller = SimpleNamespace(
                manifest=SimpleNamespace(budget=SimpleNamespace(max_attempts=4)),
            )
            app.view.return_value = SimpleNamespace(
                session_root=session_root,
                status="completed",
                status_reason="",
                next_action=None,
                state_refs={},
                attempts=(),
            )
            app.request_report.return_value = SimpleNamespace(
                session_root=session_root,
                status="running",
                status_reason="",
                next_action="report_write",
                state_refs={},
                attempts=(),
            )
            app.advance.return_value = final_view
            stdout = io.StringIO()
            with (
                patch("simple_ar.cli.main._optional_research_llm_client", return_value=object()),
                patch(
                    "simple_ar.app.research_application.load_session",
                    return_value=app,
                ) as loader,
                contextlib.redirect_stdout(stdout),
            ):
                main(
                    [
                        "research-report",
                        "--session-root",
                        str(session_root),
                        "--model",
                        "gpt-5.4",
                        "--template",
                        "survey",
                    ]
                )

            loader.assert_called_once()
            app.request_report.assert_called_once_with(
                report_config={"template": "survey"},
                reason="Apply explicit report settings and rebuild only report deliverables from existing evidence.",
            )
            app.advance.assert_called_once_with(max_actions=1)
            self.assertIn("Status: completed", stdout.getvalue())
            self.assertIn(
                str(session_root / "attempts" / "report-001" / "report.md"),
                stdout.getvalue(),
            )

    def test_report_resume_uses_real_interface_and_persisted_attempt_limit(self):
        from simple_ar.app.research_application import ResearchApplication

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "session"
            app = create_autospec(ResearchApplication, instance=True)
            app.brief = SimpleNamespace(objective="A bounded report", requested_outputs=("report",))
            app.services = SimpleNamespace(
                max_attempts=1, llm_client=SimpleNamespace(model="scripted"), config={},
            )
            app.controller = SimpleNamespace(
                manifest=SimpleNamespace(budget=SimpleNamespace(max_attempts=3)),
            )
            paused = SimpleNamespace(
                session_root=root, status="paused", status_reason="retry report",
                next_action="report_write", state_refs={},
            )
            running = SimpleNamespace(
                session_root=root, status="running", status_reason="",
                next_action="report_write", state_refs={},
            )
            completed = SimpleNamespace(
                session_root=root, status="completed", status_reason="",
                next_action=None,
                state_refs={"report": SimpleNamespace(path="attempts/report-001/report.md")},
            )
            app.view.return_value = paused
            app.continue_session.return_value = running
            app.advance.side_effect = [running] * 9 + [completed]
            with (
                patch("simple_ar.cli.main._optional_research_llm_client", return_value=object()),
                patch("simple_ar.app.research_application.load_session", return_value=app),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                main(["research-report", "--session-root", str(root), "--model", "scripted"])
            app.continue_session.assert_called_once_with(
                reason="Resume the canonical report lifecycle within the persisted session budget.",
            )
            self.assertEqual(app.advance.call_count, 10)

    def test_exhausted_report_resume_shows_bounded_authorization_entrypoint(self):
        from simple_ar.app.research_application import ResearchApplication

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "session"
            app = create_autospec(ResearchApplication, instance=True)
            app.brief = SimpleNamespace(objective="A bounded report", requested_outputs=("report",))
            app.services = SimpleNamespace(config={}, llm_client=SimpleNamespace(model="scripted"))
            app.controller = SimpleNamespace(
                manifest=SimpleNamespace(
                    budget=SimpleNamespace(
                        attempts=5, max_attempts=5, no_progress=3, max_no_progress=3,
                    ),
                    session_id="session-123", revision=7,
                ),
                store=SimpleNamespace(root=root),
            )
            app.budget_ledger = SimpleNamespace(limits={}, remaining=lambda _name: None)
            app.view.return_value = SimpleNamespace(
                session_root=root, status="paused", next_action="report_write", state_refs={},
            )
            app.continue_session.side_effect = RuntimeError("Session budget is exhausted.")
            output = io.StringIO()
            with (
                patch("simple_ar.cli.main._optional_research_llm_client", return_value=object()),
                patch("simple_ar.app.research_application.load_session", return_value=app),
                contextlib.redirect_stdout(output),
            ):
                with self.assertRaisesRegex(
                    SystemExit, "--additional-attempts 3 --additional-no-progress 1",
                ) as raised:
                    main(["research-report", "--session-root", str(root), "--model", "scripted"])
            self.assertIn("simple-ar research-session", str(raised.exception))
            self.assertIn("--topic 'A bounded report'", str(raised.exception))
            self.assertNotIn("allow_no_progress_exhausted", str(raised.exception))

    def test_completed_report_authorization_refresh_and_recovery_without_processes(self):
        from dataclasses import replace
        from simple_ar.app.research_application import create_session, load_session, ResearchApplicationServices
        from simple_ar.research.workflow_contracts import ResearchBrief
        from simple_ar.report.schema import AgentReportResult, ReportSectionDraft

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "paper.md"
            paper.write_text("# Calibration\nCalibration evidence is limited.\n", encoding="utf-8")
            session = root / "session"
            app = create_session(ResearchBrief(
                request_text="Review calibration.", objective="Review calibration.",
                requested_outputs=("report",),
                asset_requests=({"locator": str(paper), "role": "paper"},),
            ), root=session, services=ResearchApplicationServices(
                max_results=1, max_attempts=9,
                budget_limits={"llm_requests": 10, "total_tokens": 100,
                               "process_invocations": 0, "process_wall_seconds": 0},
            ))
            self.assertEqual(app.advance(max_actions=6).next_action, "report_write")
            app.services = replace(app.services, llm_client=object())

            def writer(**kwargs):
                paper_id = kwargs["context"].papers[0]["id"]
                return AgentReportResult(report_body="", memory=kwargs["memory"], used_agent=True,
                    sections=[ReportSectionDraft(section_id="review", heading="Evidence",
                        draft_markdown=f"Calibration evidence is limited [@{paper_id}].",
                        used_sources=[paper_id])])

            with patch("simple_ar.report.writing.run_report_agent", side_effect=writer):
                initial = app.advance(max_actions=3)
            self.assertEqual(initial.status, "completed")
            self.assertEqual(initial.budget["attempts"], 9)
            old_report = session / initial.state_refs["report"].path
            old_body = old_report.read_bytes()
            resume = ["research-session", "--session-root", str(session), "--topic", "Review calibration."]
            authorize = resume + ["--authorization-id", "report-refresh-1",
                "--authorization-reason", "One report refresh and one failed attempt.", "--additional-attempts", "4"]
            report = ["research-report", "--session-root", str(session), "--model", "scripted"]
            with patch("simple_ar.cli.main._optional_research_llm_client", return_value=object()), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main(report + ["--refresh"])
                self.assertIn("--additional-attempts 3", str(raised.exception))
                self.assertNotIn("process_invocations=", str(raised.exception))
                self.assertNotIn("process_wall_seconds=", str(raised.exception))
                self.assertIn("then repeat", str(raised.exception))
                self.assertEqual(load_session(session).view().state_refs, initial.state_refs)

                resources = resume + ["--authorization-id", "report-resources-1",
                    "--authorization-reason", "Replenish tokens before authorizing attempts.",
                    "--authorize-remaining", "total_tokens=200"]
                main(resources)  # Authorization need not enable execution immediately.
                main(resources)
                still_completed = load_session(session)
                self.assertEqual(still_completed.view().attempts, initial.attempts)
                self.assertTrue(still_completed.controller.manifest.budget.exhausted())
                self.assertEqual(still_completed.budget_ledger.limits["total_tokens"], 200)

                for _ in range(2):
                    main(authorize)
                    view = load_session(session).view()
                    self.assertEqual((view.status, view.revision, view.attempts, view.state_refs),
                                     (initial.status, initial.revision, initial.attempts, initial.state_refs))
                    self.assertEqual(view.budget["max_attempts"], 13)
                with self.assertRaisesRegex(SystemExit, "different terms"):
                    main(authorize[:-1] + ["5"])

                with patch("simple_ar.report.writing.run_report_agent", side_effect=RuntimeError("fixture interruption")):
                    with self.assertRaises(SystemExit):
                        main(report + ["--refresh"])
                self.assertEqual(load_session(session).view().status, "paused")
                with patch("simple_ar.report.writing.run_report_agent", side_effect=writer):
                    main(report)
                completed = load_session(session).view()
                self.assertEqual(completed.status, "completed")
                self.assertEqual(completed.budget["attempts"], 13)
                self.assertNotEqual(completed.state_refs["report"], initial.state_refs["report"])
                main(authorize)  # Replay remains safe even after all four attempts were spent.
                main(report)
            restored = load_session(session)
            self.assertEqual(restored.view().attempts, completed.attempts)
            self.assertEqual(restored.view().state_refs["read"], initial.state_refs["read"])
            self.assertEqual(old_report.read_bytes(), old_body)
            self.assertEqual(restored.budget_ledger.limits["process_invocations"], 0)
            self.assertEqual(restored.budget_ledger.limits["process_wall_seconds"], 0)
            self.assertFalse(any(row["capability"] == "experiment" for row in completed.attempts))

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
            session_root = output_root / "research-session"
            final_view = SimpleNamespace(
                session_root=session_root,
                status="completed",
                status_reason="",
                next_action=None,
                state_refs={
                    "report": SimpleNamespace(path="attempts/report-001/report.md"),
                    "report_audit": SimpleNamespace(path="attempts/report-audit-001/report_audit.json"),
                },
                attempts=(),
            )
            app = MagicMock()
            app.services = SimpleNamespace(max_attempts=5)
            app.view.side_effect = [
                SimpleNamespace(
                    session_root=session_root, status="running", status_reason="",
                    next_action="plan", state_refs={}, attempts=(),
                ),
                final_view,
            ]
            app.advance.return_value = final_view
            with (
                patch("simple_ar.cli.main._optional_research_llm_client", return_value=object()),
                patch(
                    "simple_ar.app.research_application.create_session",
                    return_value=app,
                ) as creator,
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

            creator.assert_called_once()
            self.assertEqual(creator.call_args.args[0].requested_outputs, ("experiments", "report"))
            app.advance.assert_called_once_with(max_actions=1)

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
            session_root = output_root / "research-session"
            final_view = SimpleNamespace(
                session_root=session_root,
                status="completed",
                status_reason="",
                next_action=None,
                state_refs={
                    "report": SimpleNamespace(path="attempts/report-001/report.md"),
                    "report_audit": SimpleNamespace(path="attempts/report-audit-001/report_audit.json"),
                },
                attempts=(),
            )
            app = MagicMock()
            app.services = SimpleNamespace(max_attempts=5)
            app.view.side_effect = [
                SimpleNamespace(
                    session_root=session_root, status="running", status_reason="",
                    next_action="plan", state_refs={}, attempts=(),
                ),
                final_view,
            ]
            app.advance.return_value = final_view
            with (
                patch("simple_ar.cli.main._optional_research_llm_client", return_value=object()),
                patch(
                    "simple_ar.app.research_application.create_session",
                    return_value=app,
                ) as creator,
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

            creator.assert_called_once()
            self.assertEqual(creator.call_args.args[0].requested_outputs, ("experiments", "report"))
            app.advance.assert_called_once_with(max_actions=1)

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
            app = MagicMock()
            app.services = SimpleNamespace(max_attempts=5)
            app.view.return_value = SimpleNamespace(
                session_root=root / "session", status="completed", status_reason="",
                next_action=None, state_refs={}, attempts=(),
            )
            app.advance.return_value = app.view.return_value
            with (
                patch("simple_ar.cli.main._optional_research_llm_client", return_value=object()),
                patch(
                    "simple_ar.app.research_application.create_session",
                    return_value=app,
                ) as creator,
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

            brief = creator.call_args.args[0]
            services = creator.call_args.kwargs["services"]
            execution = services.config["execution"]
            self.assertEqual(brief.requested_outputs, ("experiments",))
            self.assertIn("Improve the fixture.", brief.request_text)
            self.assertEqual(execution["cwd"], str(project.resolve()))
            self.assertEqual(execution["timeout_sec"], 7)
            self.assertEqual(execution["code_task"]["code_root"], str(project.resolve()))
            self.assertEqual(execution["code_task"]["max_repairs"], 0)




    def test_inspect_and_search_artifacts_commands_write_retrieval_files(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "historical-run"
            (run_dir / "01-plan").mkdir(parents=True)
            (run_dir / "01-plan" / "problem.md").write_text(
                "# Research problem\n\nStudy reliable research agents.\n", encoding="utf-8"
            )

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
