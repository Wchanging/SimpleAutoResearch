import tempfile
import unittest
import json
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from simple_ar.cli.parser import build_parser
from simple_ar.cli.research_config import research_defaults


class ResearchConfigTests(unittest.TestCase):
    def test_toml_interaction_and_decision_reply_reach_the_canonical_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.toml"
            path.write_text(
                '[task]\ngoal="Review a bounded result"\n'
                '[research]\ninteraction="assisted"\n'
                '[continuation]\ndecision_id="0123456789abcdef"\n'
                'decision_response="revise"\ndecision_guidance="Keep the comparison local"\n',
                encoding="utf-8",
            )
            explicit = set()
            defaults = research_defaults(
                ["research-session", "--config", str(path)],
                explicit_destinations=explicit,
            )
            args = build_parser(research_defaults=defaults).parse_args(
                ["research-session", "--config", str(path)],
            )
            self.assertEqual(args.interaction, "assisted")
            self.assertEqual(args.decision_id, "0123456789abcdef")
            self.assertEqual(args.decision_response, "revise")
            self.assertEqual(args.decision_guidance, "Keep the comparison local")
            self.assertTrue({"interaction", "decision_id", "decision_response", "decision_guidance"} <= explicit)

    def test_config_accepts_compact_seed_protocol_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.toml"
            (Path(directory) / "paper.md").write_text(
                "# Seed fixture\nA small supplied document supports a bounded comparison.\n",
                encoding="utf-8",
            )
            (Path(directory) / "measure.py").write_text(
                "import sys\nfrom pathlib import Path\n"
                "with Path('calls.txt').open('a') as handle: handle.write(sys.argv[-1] + '\\n')\n"
                "print('accuracy:', 0.5 + int(sys.argv[-1]) / 10)\n",
                encoding="utf-8",
            )
            path.write_text(
                '[task]\ngoal="Run two different seeds for the seed fixture"\noutputs=["experiments"]\noutput_root="out"\n'
                '[model]\nname=""\n[research]\nproviders=["local_files"]\nmaterials_only=true\n'
                '[assets]\npapers=["paper.md"]\n[budget]\nprocess_invocations=2\nprocess_wall_seconds=20\n'
                f'[execution]\ncommand={json.dumps([sys.executable, "measure.py"])}\ncwd="."\n'
                'timeout_sec=5\nseed_count=2\nseed_flag="--seed"\nbaseline_policy="skip"\n',
                encoding="utf-8",
            )
            defaults = research_defaults(["research-session", "--config", str(path)])
            self.assertEqual(defaults["execution_details"]["seed_count"], 2)
            self.assertEqual(defaults["execution_details"]["seed_flag"], "--seed")
            self.assertEqual(defaults["execution_details"]["baseline_policy"], "skip")
            from simple_ar.cli.main import main
            from simple_ar.app.research_application import load_session
            with redirect_stdout(io.StringIO()):
                main(["research-session", "--config", str(path), "--interaction", "autonomous"])
            session = next((Path(directory) / "out").iterdir())
            self.assertEqual(load_session(session).view().status, "completed")
            self.assertEqual((Path(directory) / "calls.txt").read_text(encoding="utf-8").splitlines(), ["0", "1"])

    def test_config_pair_protocol_real_processes_and_completed_resume(self):
        from simple_ar.cli.main import main
        from simple_ar.app.research_application import load_session
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "paper.md").write_text("# Replay learning\nMemory stores previous examples.", encoding="utf-8")
            (root / "measure.py").write_text(
                'import sys\nfrom pathlib import Path\np=Path("calls.txt")\n'
                'with p.open("a") as f: f.write(" ".join(sys.argv[1:])+"\\n")\n'
                'print("accuracy: 0.7" if sys.argv[1]=="candidate" else "accuracy: 0.6")\n', encoding="utf-8")
            source = ('[task]\ngoal="Replay learning"\noutputs=["experiments"]\noutput_root="out"\n'
                      '[model]\nname=""\n[research]\nproviders=["local_files"]\n[assets]\npapers=["paper.md"]\n'
                      '[budget]\nprocess_invocations=4\nprocess_wall_seconds=80\n'
                      '[execution]\ncwd="."\ntimeout_sec=10\nprimary_metric="accuracy"\n'
                      '[execution.protocol]\ncontract_id="fixture-pairs"\n'
                      'dataset_refs=[{asset_id="fixture"}]\nsplit_spec={name="fixed"}\n'
                      'metric_specs=[{name="accuracy",unit="fraction"}]\ncomparison_conditions={name="fixed"}\n'
                      'protected_assets=[{asset_id="evaluator",path="measure.py"}]\n')
            for seed in (0, 1):
                source += '\n[[execution.pairs]]\nseed=' + str(seed) + '\n'
                for role in ("baseline", "candidate"):
                    source += role + '_command=' + json.dumps([sys.executable, "measure.py", role, str(seed)]) + '\n'
            path = root / "research.toml"
            path.write_text(source, encoding="utf-8")
            argv = ["research-session", "--config", str(path)]
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(SystemExit, "paused"):
                main(argv)
            session = next((root / "out").iterdir())
            pending = load_session(session).view()
            self.assertEqual(pending.work_plan["interaction"]["mode"], "checkpoints")
            gate = pending.work_plan["interaction"]["decision"]
            self.assertEqual(gate["stage"], "execution_protocol")
            self.assertFalse((root / "calls.txt").exists())
            with redirect_stdout(io.StringIO()):
                main(argv + ["--session-root", str(session), "--decision-id", gate["id"],
                             "--decision-response", "accept"])
            app = load_session(session)
            self.assertEqual(app.view().status, "completed")
            calls = (root / "calls.txt").read_text().splitlines()
            self.assertCountEqual(calls, ["baseline 0", "baseline 1", "candidate 0", "candidate 1"])
            results = [json.loads(p.read_text()) for p in session.glob("attempts/**/results.json")]
            measured = [r for r in results if "measurement" in r]
            self.assertEqual(len(measured), 4)
            self.assertTrue(all(r["measurement"]["asset_integrity"]["status"] == "observed_unchanged" for r in measured))
            attempts = app.view().attempts
            with redirect_stdout(io.StringIO()):
                main(argv + ["--session-root", str(session)])
            self.assertEqual(load_session(session).view().attempts, attempts)
            self.assertEqual((root / "calls.txt").read_text().splitlines(), calls)

    def test_resume_attaches_execution_without_research_replay_or_budget_reset(self):
        from simple_ar.cli.main import main
        from simple_ar.app.research_application import load_session
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "paper.md").write_text("# Replay learning\nMemory stores previous examples for classification.", encoding="utf-8")
            (root / "measure.py").write_text('from pathlib import Path\np = Path("calls.txt")\np.write_text(p.read_text() + "x" if p.exists() else "x")\nprint("accuracy: 0.8")\n', encoding="utf-8")
            path = root / "research.toml"
            path.write_text('[task]\ngoal="Replay learning"\noutputs=["experiments"]\noutput_root="out"\n'
                            '[model]\nname=""\n[research]\nproviders=["local_files"]\n[assets]\npapers=["paper.md"]\n'
                            '[budget]\ntotal_tokens=123456\nprocess_invocations=2\nprocess_wall_seconds=60\n', encoding="utf-8")
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(SystemExit, "paused"):
                main(["research-session", "--config", str(path)])
            session = next((root / "out").iterdir())
            before = load_session(session).view()
            original_inputs = {
                key: (session / before.state_refs[key].path).read_bytes()
                for key in ("brief", "brief_markdown", "assets")
            }
            path.write_text(path.read_text(encoding="utf-8").replace("123456", "999999") +
                            '\n[execution]\ncommand=[' + json.dumps(sys.executable) + ', "measure.py"]\ncwd="."\ntimeout_sec=20\nprimary_metric="accuracy"\n', encoding="utf-8")
            argv = ["research-session", "--config", str(path), "--session-root", str(session)]
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(SystemExit, "paused"):
                main(argv)
            gate = load_session(session).view().work_plan["interaction"]["decision"]
            self.assertEqual(gate["stage"], "execution_protocol")
            self.assertFalse((root / "calls.txt").exists())
            with redirect_stdout(io.StringIO()):
                main(argv + ["--decision-id", gate["id"], "--decision-response", "accept"])
            app = load_session(session)
            after = app.view()
            self.assertEqual(after.status, "completed")
            for key, ref in before.state_refs.items():
                if key not in {"brief", "assets", "brief_markdown", "runtime_config", "diagnostics", "task_plan", "design", "research_design"}:
                    self.assertEqual(after.state_refs[key], ref)
            for key, content in original_inputs.items():
                self.assertEqual((session / before.state_refs[key].path).read_bytes(), content)
                self.assertNotEqual(after.state_refs[key], before.state_refs[key])
            old_brief = json.loads(original_inputs["brief"])
            new_brief = json.loads((session / after.state_refs["brief"].path).read_text(encoding="utf-8"))
            self.assertEqual(new_brief["revision"], old_brief["revision"] + 1)
            self.assertEqual(new_brief["parent_revision"], old_brief["revision"])
            self.assertEqual(
                {key: value for key, value in new_brief.items() if key not in {"revision", "parent_revision"}},
                {key: value for key, value in old_brief.items() if key not in {"revision", "parent_revision"}},
            )
            self.assertNotEqual(after.state_refs["task_plan"], before.state_refs["task_plan"])
            self.assertEqual(app.budget_ledger.limits["total_tokens"], 123456)
            self.assertEqual((root / "calls.txt").read_text(), "x")
            with redirect_stdout(io.StringIO()):
                main(argv)
            self.assertEqual(load_session(session).view().attempts, after.attempts)
            self.assertEqual((root / "calls.txt").read_text(), "x")

    def test_toml_continuation_adds_material_and_replays_authorization_once(self):
        from simple_ar.cli.main import main
        from simple_ar.app.research_application import load_session
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            papers = [root / f"paper-{index}.md" for index in range(1, 4)]
            for index, paper in enumerate(papers, start=1):
                paper.write_text(f"# Fixture {index}\nA local supplied source on small-memory learning.\n", encoding="utf-8")
            config = root / "research.toml"

            def write_config(selected_papers, *, continuation=True):
                paper_rows = ", ".join(json.dumps(str(path)) for path in selected_papers)
                continuation_config = (
                    '[continuation]\nauthorization_id="review-20260924-1"\n'
                    'reason="Bounded continuation after review."\nadditional_attempts=2\nadditional_no_progress=1\n'
                    'remaining={total_tokens=200, llm_requests=3}\n'
                ) if continuation else ""
                config.write_text(
                    '[task]\ngoal="Summarize the supplied local sources"\noutputs=["summary"]\noutput_root="out"\n'
                    '[model]\nname=""\n[research]\nproviders=["local_files"]\nmaterials_only=true\n'
                    f'[assets]\npapers=[{paper_rows}]\n'
                    '[budget]\ntotal_tokens=100\nllm_requests=2\nprocess_invocations=0\nprocess_wall_seconds=0\n'
                    + continuation_config,
                    encoding="utf-8",
                )

            write_config(papers[:1], continuation=False)
            with redirect_stdout(io.StringIO()):
                main(["research-session", "--config", str(config)])
            session = next((root / "out").iterdir())
            initial = load_session(session)
            initial_view = initial.view()
            original_attempts = initial_view.attempts
            initial_cap = initial.controller.manifest.budget.max_attempts
            from simple_ar.core.budget import BudgetError, BudgetLedger
            initial.budget_ledger.reserve("historical-token-use", {"total_tokens": 7})
            initial.budget_ledger.settle("historical-token-use", {"total_tokens": 7})
            historical_entry = initial.budget_ledger.entries[0].to_dict()

            write_config(papers[:2])
            argv = ["research-session", "--config", str(config), "--session-root", str(session)]
            original_authorize = BudgetLedger.authorize_remaining

            def fail_ledger_once(ledger, *args, **kwargs):
                if kwargs.get("authorization_id") == "review-20260924-1":
                    raise BudgetError("injected manifest-ledger gap")
                return original_authorize(ledger, *args, **kwargs)

            with patch.object(BudgetLedger, "authorize_remaining", new=fail_ledger_once):
                with redirect_stdout(io.StringIO()), self.assertRaisesRegex(
                    SystemExit, "injected manifest-ledger gap",
                ):
                    main(argv)
            after_manifest_only = load_session(session)
            self.assertEqual(after_manifest_only.controller.manifest.budget.max_attempts, initial_cap + 2)
            self.assertEqual(after_manifest_only.budget_ledger.limits["total_tokens"], 100)
            self.assertEqual(after_manifest_only.budget_ledger.entries[0].to_dict(), historical_entry)
            self.assertEqual(after_manifest_only.view().attempts, original_attempts)

            from simple_ar.core.session import SessionController
            with patch.object(
                SessionController, "continue_with_revision",
                side_effect=RuntimeError("injected session continuation failure"),
            ):
                with redirect_stdout(io.StringIO()), self.assertRaisesRegex(
                    SystemExit, "injected session continuation failure",
                ):
                    main(argv)
            after_ledger_only = load_session(session)
            self.assertEqual(after_ledger_only.view().status, "completed")
            self.assertEqual(after_ledger_only.controller.manifest.budget.max_attempts, initial_cap + 2)
            self.assertEqual(after_ledger_only.budget_ledger.limits["total_tokens"], 200)
            self.assertEqual(after_ledger_only.budget_ledger.entries[0].to_dict(), historical_entry)
            self.assertEqual(after_ledger_only.view().attempts, original_attempts)

            with redirect_stdout(io.StringIO()):
                main(argv)
            first_resume = load_session(session)
            first_view = first_resume.view()
            self.assertEqual(first_view.status, "completed")
            self.assertGreater(len(first_view.attempts), len(original_attempts))
            self.assertEqual(first_resume.controller.manifest.budget.attempts, len(first_view.attempts))
            self.assertEqual(first_resume.controller.manifest.budget.max_attempts, initial_cap + 2)
            self.assertEqual(first_resume.budget_ledger.limits["total_tokens"], 200)
            self.assertEqual(first_resume.budget_ledger.limits["llm_requests"], 3)
            self.assertEqual(len(first_resume._local_documents()), 2)

            with redirect_stdout(io.StringIO()):
                main(argv)
            exact_replay = load_session(session)
            self.assertEqual(exact_replay.view().attempts, first_view.attempts)
            self.assertEqual(exact_replay.controller.manifest.budget.max_attempts, initial_cap + 2)
            self.assertEqual(exact_replay.budget_ledger.limits["total_tokens"], 200)

            write_config(papers)
            with redirect_stdout(io.StringIO()):
                main(argv)
            replayed = load_session(session)
            self.assertEqual(replayed.view().status, "completed")
            self.assertEqual(replayed.controller.manifest.budget.max_attempts, initial_cap + 2)
            self.assertEqual(replayed.controller.manifest.budget.continuation_authorizations[
                "review-20260924-1"]["resource_allowances"], {"total_tokens": 200, "llm_requests": 3})
            self.assertEqual(len(replayed._local_documents()), 3)

    def test_config_runs_local_summary_and_persists_budget_without_processes(self):
        from simple_ar.cli.main import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "paper.md").write_text("# Replay learning\nA small memory stores previous examples for classification.", encoding="utf-8")
            path = root / "research.toml"
            path.write_text('[task]\ngoal="Replay learning"\noutputs=["summary"]\noutput_root="out"\n'
                            '[model]\nname=""\n[research]\nproviders=["local_files"]\nuse_fulltext=false\nallow_pdf_download=false\nkeep_raw_pdf=false\n[assets]\npapers=["paper.md"]\n'
                            '[budget]\ntotal_tokens=123456\nprocess_invocations=0\nprocess_wall_seconds=0\n', encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                main(["research-session", "--config", str(path)])
            ledger_path = next((root / "out").glob("*/budget_ledger.json"))
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            self.assertEqual(ledger["limits"]["total_tokens"], 123456)
            self.assertEqual(ledger["entries"], [])
            runtime = json.loads((ledger_path.parent / "inputs/runtime_config.json").read_text())
            for name in ("research_use_fulltext", "research_allow_pdf_download", "research_keep_raw_pdf"):
                self.assertIs(runtime["config"][name], False)
            self.assertTrue((ledger_path.parent / "outputs/research_summary.md").is_file())
            path.write_text(path.read_text(encoding="utf-8").replace('outputs=["summary"]', 'outputs=["experiments"]'), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaisesRegex(SystemExit, "status 'paused'"):
                main(["research-session", "--config", str(path)])
            self.assertIn("Implementation: preparation required", output.getvalue())
            self.assertIn("Next action: none", output.getvalue())
            self.assertIn("Provide execution settings", output.getvalue())

    def test_file_paths_and_explicit_cli_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.toml"
            path.write_text('[task]\ngoal="Continual learning"\noutputs=["experiments","report"]\noutput_root="runs"\n'
                            '[budget]\ntotal_tokens=2000000\n[research]\nproviders=["arxiv"]\n'
                            '[assets]\npapers=["paper.md"]\n', encoding="utf-8")
            argv = ["research-session", "--config", str(path), "--total-tokens", "3000000", "--provider", "openalex"]
            args = build_parser(research_defaults=research_defaults(argv)).parse_args(argv)
            self.assertEqual(args.topic, "Continual learning")
            self.assertEqual(args.model, "env")
            self.assertEqual(args.total_tokens, 3000000)
            self.assertEqual(args.providers, ["openalex"])
            self.assertEqual(args.local_document, [str((Path(directory) / "paper.md").resolve())])
            self.assertEqual(args.output_root, str((Path(directory) / "runs").resolve()))
            self.assertEqual(args.outputs, ["experiments", "report"])
            self.assertIsNone(args.command_argv)
            self.assertEqual(args.max_chunks, 300)

    def test_research_iteration_setting_targets_the_consumed_cli_option(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.toml"
            path.write_text(
                '[task]\ngoal="Bounded rounds"\n[research]\nmax_iterations=3\n',
                encoding="utf-8",
            )
            explicit = set()
            argv = ["research-session", "--config", str(path)]
            defaults = research_defaults(argv, explicit_destinations=explicit)
            args = build_parser(research_defaults=defaults).parse_args(argv)
            self.assertEqual(args.max_research_iterations, 3)
            self.assertIn("max_research_iterations", explicit)

    def test_config_can_record_an_explicit_idea_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.toml"
            path.write_text(
                '[task]\ngoal="Continual learning"\noutputs=["experiments"]\n'
                'selected_idea_id="idea-replay"\n',
                encoding="utf-8",
            )
            argv = ["research-session", "--config", str(path)]
            defaults = research_defaults(argv)
            self.assertEqual(defaults["selected_idea_id"], "idea-replay")
            args = build_parser(research_defaults=defaults).parse_args(argv)
            self.assertEqual(args.selected_idea_id, "idea-replay")

    def test_resume_applies_report_only_changes_and_rejects_unapplied_research_changes(self):
        from simple_ar.cli.main import main
        from simple_ar.app.research_application import ResearchApplication, load_session

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "paper.md"
            paper.write_text("# Local evidence\nA small supplied source for a bounded summary.\n", encoding="utf-8")
            config = root / "research.toml"

            def write_config(*, max_results=10, reviewer="llm", outputs=("summary",), model=""):
                config.write_text(
                    '[task]\ngoal="Summarize local evidence"\n'
                    f'outputs={json.dumps(list(outputs))}\noutput_root="out"\n'
                    f'[model]\nname="{model}"\n'
                    '[research]\nproviders=["local_files"]\nmaterials_only=true\n'
                    f'max_results={max_results}\n'
                    f'[assets]\npapers=[{json.dumps(str(paper))}]\n'
                    f'[report]\nreviewer="{reviewer}"\n',
                    encoding="utf-8",
                )

            write_config()
            with redirect_stdout(io.StringIO()):
                main(["research-session", "--config", str(config)])
            session = next((root / "out").iterdir())
            before = load_session(session).view()

            write_config(max_results=11)
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(SystemExit, "max_results.*not applied"):
                main(["research-session", "--config", str(config), "--session-root", str(session)])
            unchanged = load_session(session).view()
            self.assertEqual(unchanged.state_refs, before.state_refs)
            self.assertEqual(unchanged.attempts, before.attempts)

            write_config(reviewer="disabled")
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(
                SystemExit, "Report settings do not request a report deliverable",
            ):
                main(["research-session", "--config", str(config), "--session-root", str(session)])
            no_report_change = load_session(session).view()
            self.assertEqual(no_report_change.state_refs, before.state_refs)
            self.assertEqual(no_report_change.attempts, before.attempts)

            report_app = load_session(session)
            report_app.request_report()
            report_app.controller.pause("test checkpoint before report writing")
            before_report = load_session(session).view()

            write_config(
                reviewer="disabled", outputs=("summary", "report"), model="scripted",
            )
            with (
                patch.object(ResearchApplication, "advance", side_effect=RuntimeError("stop before report work")),
                patch("simple_ar.cli.main._optional_research_llm_client", return_value=object()),
                redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(SystemExit, "stop before report work"),
            ):
                main(["research-session", "--config", str(config), "--session-root", str(session)])
            refreshed = load_session(session)
            self.assertEqual(refreshed.services.config["report"]["reviewer"], "disabled")
            self.assertEqual(refreshed.view().state_refs["summary"], before_report.state_refs["summary"])
            self.assertEqual(refreshed.view().attempts, before_report.attempts)

    def test_unknown_or_invalid_values_do_not_silently_use_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.toml"
            for source in ('[budget]\ntotal_token=100', '[budget]\ntotal_tokens=true',
                           '[task]\noutputs=["anything"]', '[model]\napi_key="secret"',
                           '[execution]\npairs=["not a pair"]', '[execution]\nprotocol="not a table"',
                           '[research]\nuse_fulltext="true"',
                           '[[execution.pairs]]\nseed=0\nbaseline_command=[]\ncandidate_command=["python"]'):
                with self.subTest(source=source):
                    path.write_text(source, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        research_defaults(["research-session", "--config", str(path)])

    def test_external_command_config_is_not_a_research_config(self):
        self.assertEqual(research_defaults(["research-session", "--topic", "test", "--command",
                                           "python", "train.py", "--config", "missing.toml"]), {})

    def test_templates_parse_with_same_defaults(self):
        from simple_ar.app.research_application import ResearchApplicationServices

        defaults = build_parser().parse_args(["research-session", "--topic", "A research goal"])
        self.assertIsNone(defaults.total_tokens)
        self.assertIsNone(defaults.llm_requests)
        self.assertEqual(ResearchApplicationServices().budget_limits,
                         {"total_tokens": None, "llm_requests": None})
        root = Path(__file__).resolve().parents[1]
        for name, path in (("survey", "examples/survey/research.toml"),
                           ("advanced", "tests/fixtures/research_config/advanced.toml"),
                           ("continual", "examples/continual_learning/research.toml")):
            argv = ["research-session", "--config", str(root / path)]
            args = build_parser(research_defaults=research_defaults(argv)).parse_args(argv)
            self.assertTrue(args.topic)
            self.assertEqual(args.max_output_tokens, 8192)
            self.assertIsNone(args.total_tokens)
            self.assertIsNone(args.llm_requests)
            if name == "advanced":
                self.assertTrue(args.research_use_fulltext)
                self.assertTrue(args.research_allow_pdf_download)
                self.assertTrue(args.research_keep_raw_pdf)

    def test_report_figures_are_forwarded_as_existing_report_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.toml"
            path.write_text(
                '[task]\ngoal="Measured report"\noutputs=["experiments", "report"]\n'
                '[report]\nmax_section_tokens=0\n[report.figures]\nenabled=true\nmax_figures=2\n',
                encoding="utf-8",
            )
            defaults = research_defaults(["research-session", "--config", str(path)])
            self.assertEqual(defaults["report_figures"], {"enabled": True, "max_figures": 2})
            self.assertEqual(defaults["max_section_tokens"], 0)
