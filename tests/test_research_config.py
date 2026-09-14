import tempfile
import unittest
import json
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

from simple_ar.cli.parser import build_parser
from simple_ar.cli.research_config import research_defaults


class ResearchConfigTests(unittest.TestCase):
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
            with redirect_stdout(io.StringIO()):
                main(argv)
            session = next((root / "out").iterdir())
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
            path.write_text(path.read_text(encoding="utf-8").replace("123456", "999999") +
                            '\n[execution]\ncommand=[' + json.dumps(sys.executable) + ', "measure.py"]\ncwd="."\ntimeout_sec=20\nprimary_metric="accuracy"\n', encoding="utf-8")
            argv = ["research-session", "--config", str(path), "--session-root", str(session)]
            with redirect_stdout(io.StringIO()):
                main(argv)
            app = load_session(session)
            after = app.view()
            self.assertEqual(after.status, "completed")
            for key, ref in before.state_refs.items():
                if key not in {"runtime_config", "diagnostics"}:
                    self.assertEqual(after.state_refs[key], ref)
            self.assertEqual(app.budget_ledger.limits["total_tokens"], 123456)
            self.assertEqual((root / "calls.txt").read_text(), "x")
            with redirect_stdout(io.StringIO()):
                main(argv)
            self.assertEqual(load_session(session).view().attempts, after.attempts)
            self.assertEqual((root / "calls.txt").read_text(), "x")

    def test_config_runs_local_summary_and_persists_budget_without_processes(self):
        from simple_ar.cli.main import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "paper.md").write_text("# Replay learning\nA small memory stores previous examples for classification.", encoding="utf-8")
            path = root / "research.toml"
            path.write_text('[task]\ngoal="Replay learning"\noutputs=["summary"]\noutput_root="out"\n'
                            '[model]\nname=""\n[research]\nproviders=["local_files"]\n[assets]\npapers=["paper.md"]\n'
                            '[budget]\ntotal_tokens=123456\nprocess_invocations=0\nprocess_wall_seconds=0\n', encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                main(["research-session", "--config", str(path)])
            ledger_path = next((root / "out").glob("*/budget_ledger.json"))
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            self.assertEqual(ledger["limits"]["total_tokens"], 123456)
            self.assertEqual(ledger["entries"], [])
            self.assertTrue((ledger_path.parent / "outputs/research_summary.md").is_file())
            path.write_text(path.read_text(encoding="utf-8").replace('outputs=["summary"]', 'outputs=["experiments"]'), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaisesRegex(SystemExit, "status 'paused'"):
                main(["research-session", "--config", str(path)])
            self.assertIn("Implementation: preparation required", output.getvalue())
            self.assertIn("Next action: experiment", output.getvalue())

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

    def test_unknown_or_invalid_values_do_not_silently_use_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.toml"
            for source in ('[budget]\ntotal_token=100', '[budget]\ntotal_tokens=true',
                           '[task]\noutputs=["anything"]', '[model]\napi_key="secret"',
                           '[execution]\npairs=["not a pair"]', '[execution]\nprotocol="not a table"',
                           '[[execution.pairs]]\nseed=0\nbaseline_command=[]\ncandidate_command=["python"]'):
                with self.subTest(source=source):
                    path.write_text(source, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        research_defaults(["research-session", "--config", str(path)])

    def test_external_command_config_is_not_a_research_config(self):
        self.assertEqual(research_defaults(["research-session", "--topic", "test", "--command",
                                           "python", "train.py", "--config", "missing.toml"]), {})

    def test_templates_parse_with_same_defaults(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("minimal", "advanced"):
            argv = ["research-session", "--config", str(root / "examples/research_config" / f"{name}.toml")]
            args = build_parser(research_defaults=research_defaults(argv)).parse_args(argv)
            self.assertTrue(args.topic)
            self.assertEqual(args.max_output_tokens, 8192)
