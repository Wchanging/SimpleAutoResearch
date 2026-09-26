import sys
import tempfile
import unittest
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from simple_ar.app.research_application import (
    ResearchApplicationServices,
    create_session,
)
from simple_ar.app.research_execution import (
    execution_request,
    execution_protocol,
    merge_execution_protocol,
    normalize_execution_config,
)
from simple_ar.code_task import initialize_code_task
from simple_ar.code_task.runtime.state import code_task_paths
from simple_ar.integrations.llm import LLMClient, LLMSettings
from simple_ar.research.task_plan import (
    TaskPlanResult,
    TaskPlanStep,
    TaskPlanRequest,
    append_research_followup,
    build_task_plan,
    default_task_steps,
)
from simple_ar.research.workflow_contracts import ResearchBrief


class TaskPlanTests(unittest.TestCase):
    def test_fixed_command_seed_is_preserved_without_inventing_pairs(self):
        from simple_ar.experiment.execution.backend import RunResult
        from simple_ar.experiment.execution.results import build_canonical_results
        for argv in (["measure.py", "--rng", "0"], ["measure.py", "--rng=0"]):
            config = {"command": argv, "seed_flag": "--rng",
                      "cwd": str(Path.cwd()), "timeout_sec": 5}
            normalized = normalize_execution_config(config)
            self.assertNotIn("pairs", normalized)
            self.assertEqual(execution_protocol(normalized)["seeds"], [0])
            self.assertEqual(normalize_execution_config(normalized), normalized)
            request = execution_request(normalized)
            self.assertEqual(request.run.command, argv)
            self.assertEqual(request.experiment_contract.comparison_conditions["seed"], 0)
            result = build_canonical_results(
                RunResult(0, False, "", "", command=argv),
                experiment_contract=request.experiment_contract.to_row(),
            )
            self.assertEqual(result["measurement"]["seed"], 0)
            self.assertEqual(result["measurement"]["protocol_status"], "incomplete")
        unbound = execution_request({"command": argv, "cwd": str(Path.cwd()), "timeout_sec": 5})
        self.assertIsNone(unbound.experiment_contract)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            execution_request({**config, "protocol": {"comparison_conditions": {"seed": 9}}})
        with self.assertRaisesRegex(ValueError, "repeated seed"):
            execution_request({**config, "command": ["measure.py", "--rng=0", "--rng", "1"]})

    def test_seed_expansion_replaces_existing_flag_instead_of_duplicating_it(self):
        normalized = normalize_execution_config({
            "command": ["measure.py", "--rng=0"], "seed_flag": "--rng", "seeds": [3],
            "cwd": str(Path.cwd()), "timeout_sec": 5,
        })
        request = execution_request(normalized)
        self.assertEqual(request.run.command, ["measure.py", "--rng=3"])
        self.assertEqual(request.experiment_contract.comparison_conditions["seed"], 3)

    def test_summary_capability_spelling_is_normalized_without_relaxing_boundaries(self) -> None:
        request = TaskPlanRequest(task_kind="survey", goal="Read papers", request_text="Read papers")
        class Client:
            def ask_json(self, *args, **kwargs):
                rows = default_task_steps(request)
                for row in rows:
                    if row["action"] == "summarize":
                        row["action"] = "summary"
                        row["capability"] = "invented_route"
                        row["state_name"] = "invented_state"
                return {"steps": rows}
        result = build_task_plan(replace(request, use_llm=True, llm_client=Client()))
        step = next(step for step in result.steps if step.action == "summarize")
        self.assertEqual((step.capability, step.state_name), ("summary", "summary"))
        self.assertEqual(TaskPlanResult.from_handoff_dict(result.to_handoff_dict()), result)
        corrupt = result.to_handoff_dict()
        corrupt["steps"][0]["capability"] = "invented_route"
        with self.assertRaisesRegex(ValueError, "boundary mismatch"):
            TaskPlanResult.from_handoff_dict(corrupt)
        rows = result.to_handoff_dict()
        rows["steps"][0]["action"] = "invented_action"
        with self.assertRaisesRegex(ValueError, "Unsupported task plan action"):
            TaskPlanResult.from_handoff_dict(rows)

    def test_compact_seed_protocol_is_explicit_and_budget_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {
                "command": [sys.executable, "measure.py"],
                "cwd": str(root),
                "timeout_sec": 5,
                "seed_flag": "--seed",
                "seeds": [0, 1],
                "baseline_policy": "skip",
            }
            normalized = normalize_execution_config(config)
            self.assertEqual([row["seed"] for row in normalized["pairs"]], [0, 1])
            self.assertEqual(normalized["pairs"][1]["candidate_command"][-2:], ["--seed", "1"])
            self.assertEqual(execution_protocol(config)["baseline_policy"], "skip")
            request = execution_request(normalized, pair_index=1)
            self.assertEqual(request.run.command[-2:], ["--seed", "1"])
            natural_only = normalize_execution_config({
                "command": config["command"], "cwd": config["cwd"], "timeout_sec": 5,
            }, task_text="Run two different seeds.")
            self.assertNotIn("pairs", natural_only)
            repeated = normalize_execution_config({
                **natural_only, "seeds": [0, 1], "seed_flag": "--seed",
                "protocol": {"comparison_conditions": {"split": "validation"}},
            })
            self.assertEqual(repeated["baseline_policy"], "skip")
            self.assertEqual(normalize_execution_config(repeated), repeated)
            nine = normalize_execution_config({
                **config, "seeds": list(range(9)),
            })
            self.assertEqual(len(nine["pairs"]), 9)
            with self.assertRaisesRegex(ValueError, "seed_flag"):
                normalize_execution_config({**config, "seed_flag": "", "seeds": [0, 1]})
            with self.assertRaisesRegex(ValueError, "run, skip or reuse"):
                normalize_execution_config({**config, "baseline_policy": "auto"})

    def test_resolved_run_materializes_a_baseline_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = {
                "command": [sys.executable, "measure.py"],
                "cwd": str(root),
                "timeout_sec": 5,
            }
            resolved = normalize_execution_config(merge_execution_protocol(
                raw, {"comparison_required": True, "baseline_policy": "run"},
            ))
            self.assertEqual(resolved["baseline"]["command"], raw["command"])
            result = build_task_plan(TaskPlanRequest(
                task_kind="research", goal="Measure the fixture", request_text="Measure the fixture",
                requested_outputs=("experiments",),
                config={"research_materials_only": True, "research_local_documents": ["note.md"]},
                execution=resolved, execution_protocol_accepted=True,
            ))
            actions = [step.action for step in result.steps]
            self.assertLess(actions.index("baseline"), actions.index("experiment"))
            self.assertEqual(execution_request(resolved, condition="baseline").run.label, "baseline")

    def test_model_can_omit_search_when_supplied_materials_satisfy_task(self) -> None:
        request = TaskPlanRequest(
            task_kind="survey", goal="Compare supplied notes", request_text="Only compare these notes",
            config={"research_local_documents": ["note.md"]},
        )
        class Client:
            def ask_json(self, *args, **kwargs):
                return {"steps": [row for row in default_task_steps(request) if row["action"] != "search"]}
        result = build_task_plan(replace(request, use_llm=True, llm_client=Client()))
        self.assertEqual(result.steps[0].action, "document_ingest")
        self.assertNotIn("input_names", result.steps[0].to_dict())

    def test_survey_rejects_dynamic_process_actions(self) -> None:
        request = TaskPlanRequest(task_kind="survey", goal="Read papers", request_text="Read papers")
        class Client:
            def ask_json(self, *args, **kwargs):
                return {"steps": default_task_steps(request) + [{"action": self.action}]}
        client = Client()
        for action in ("matrix_baseline_999", "matrix_candidate_0", "retest:1", "matrix_repair_1"):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "authorized protocol"):
                client.action = action
                build_task_plan(replace(request, use_llm=True, llm_client=client))

    def test_supplied_documents_finish_without_search_and_resume_without_reexecution(self) -> None:
        from simple_ar.app.research_application import load_session
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "evidence.md"
            paper.write_text("# Agent evaluation\nRepeated validation improves reliability. Evidence remains limited.\n", encoding="utf-8")
            app = create_session(
                ResearchBrief(request_text="Analyse only the supplied paper.",
                              requested_outputs=("research_summary",),
                              asset_requests=({"locator": str(paper), "role": "paper"},)),
                root=root / "session",
                services=ResearchApplicationServices(config={"research_materials_only": True}),
            )
            result = app.advance(max_actions=8)
            self.assertEqual(result.status, "completed", result.status_reason)
            self.assertNotIn("search", result.state_refs)
            self.assertNotIn("experiment", result.state_refs)
            self.assertIn("summary", result.state_refs)
            attempts = len(result.attempts)
            restored = load_session(root / "session")
            resumed = restored.advance(max_actions=8)
            self.assertEqual(len(resumed.attempts), attempts)
            text = (root / "session" / "outputs" / "research_summary.md").read_text(encoding="utf-8")
            self.assertIn("Search was not run", text)

    def test_deterministic_bug_plan_has_no_research_or_experiment_steps(self) -> None:
        request = TaskPlanRequest(
            task_kind="bug_fix",
            goal="Fix the scoped classifier bug.",
            request_text="Fix the scoped classifier bug.",
            requested_outputs=("bug_fix",),
            execution={"code_task": {"run_dir": "/prepared/run"}},
        )
        result = build_task_plan(request)

        self.assertEqual([step.action for step in result.steps], ["implement"])
        self.assertEqual({step.capability for step in result.steps}, {"implement"})
        self.assertEqual(result.mode, "deterministic")

    def test_llm_plan_is_validated_before_it_can_be_consumed(self) -> None:
        class Client:
            model = "fixture-task-planner"

            def ask_json(self, _system, _user, *, label="", **kwargs):
                self.label = label
                self.tokens = kwargs["max_output_tokens"]
                return {"steps": default_task_steps(self.request)}

        request = TaskPlanRequest(
            task_kind="survey",
            goal="Survey bounded agent evaluation.",
            request_text="Survey bounded agent evaluation.",
            requested_outputs=("research_summary",),
        )
        client = Client()
        client.request = request
        request = TaskPlanRequest(
            task_kind=request.task_kind,
            goal=request.goal,
            request_text=request.request_text,
            requested_outputs=request.requested_outputs,
            use_llm=True,
            llm_client=client,
        )
        result = build_task_plan(request)

        self.assertEqual(result.mode, "llm")
        self.assertEqual(result.model, "fixture-task-planner")
        self.assertEqual(client.label, "task-plan")
        self.assertIsNone(client.tokens)
        self.assertNotIn("experiment", [step.action for step in result.steps])
        from dataclasses import replace

        build_task_plan(replace(request, config={"research_task_planning_max_output_tokens": 2048}))
        self.assertEqual(client.tokens, 2048)

    def test_invalid_llm_sequence_is_corrected_without_silent_fallback(self) -> None:
        request = TaskPlanRequest(
            task_kind="research",
            goal="Compare a supplied classifier.",
            request_text="Compare a supplied classifier.",
            requested_outputs=("experiments",),
            hard_constraints=("Use only the supplied benchmark.",),
            preferences=("Keep the plan short.",),
            config={
                "research_materials_only": True,
                "research_local_documents": ["notes.md"],
            },
            execution={"command": [sys.executable, "benchmark.py"]},
        )

        class Client:
            model = "fixture-invalid-planner"
            calls = 0

            def ask_json(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    return {"steps": default_task_steps(request)}
                return {
                    "steps": [
                        {"action": "document_ingest"},
                        {"action": "read"},
                        {"action": "assess_ideas"},
                        {"action": "research_design"},
                    ]
                }

        result = build_task_plan(replace(request, use_llm=True, llm_client=Client()))

        self.assertEqual(result.mode, "llm")
        self.assertTrue(result.diagnostics)
        self.assertIn("Use only the supplied benchmark.", result.assumptions)
        actions = [step.action for step in result.steps]
        self.assertLess(actions.index("synthesize"), actions.index("assess_ideas"))
        self.assertLess(actions.index("assess_ideas"), actions.index("research_design"))

    def test_bug_plan_correction_is_bounded_and_records_rejected_proposals(self) -> None:
        request = TaskPlanRequest(task_kind="bug_fix", goal="Fix totals", request_text="Fix totals")
        class Client:
            calls = 0
            def ask_json(self, system, prompt, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    assert "Bug-fix plans" in prompt
                return {"steps": [{"action": "summarize", "capability": "wrong"}]}
        client = Client()
        trace = []
        with self.assertRaisesRegex(ValueError, "after one correction"):
            build_task_plan(replace(request, use_llm=True, llm_client=client), trace=trace)
        self.assertEqual(client.calls, 2)
        self.assertEqual(len(trace), 2)
        self.assertTrue(all(row["validation_error"] for row in trace))
        self.assertEqual(trace[0]["response"]["steps"][0]["capability"], "wrong")
        from types import SimpleNamespace
        from simple_ar.core import ArtifactStore
        from simple_ar.research.planning.capability import ResearchPlanRequest, run_research_plan_capability
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "after one correction"):
                run_research_plan_capability(
                    context=SimpleNamespace(store=ArtifactStore(Path(tmp))),
                    request=ResearchPlanRequest(
                        topic="Fix totals", task_plan_only=True,
                        task_plan_request=replace(request, use_llm=True, llm_client=Client()),
                    ),
                )
            saved = json.loads((Path(tmp) / "task_plan_proposals.json").read_text())
            self.assertEqual(len(saved["proposals"]), 2)
            self.assertFalse((Path(tmp) / "task_plan.json").exists())

    def test_bug_application_uses_code_task_without_literature_or_experiment(self) -> None:
        from tests.test_code_task import _FakeCodeTaskClient

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            (project / "spam_model.py").write_text(
                "def predict(text):\n"
                "    return 'spam' if 'win' in text.lower() else 'ham'\n",
                encoding="utf-8",
            )
            (project / "benchmark.py").write_text(
                "from spam_model import predict\n"
                "rows = [('win now', 'spam'), ('prize only', 'spam')]\n"
                "correct = sum(predict(text) == label for text, label in rows)\n"
                "print(f'accuracy: {correct / len(rows):.6f}')\n",
                encoding="utf-8",
            )
            execution = {
                "command": [sys.executable, "benchmark.py"],
                "cwd": str(project),
                "timeout_sec": 10,
                "code_task": {
                    "code_root": str(project),
                    "workspace_mode": "copy",
                    "allowed_patterns": ["spam_model.py"],
                    "protected_patterns": ["benchmark.py"],
                    "env_mode": "external",
                    "python_executable": sys.executable,
                    "approval_note": "Authorize this isolated bug patch.",
                },
            }
            app = create_session(
                ResearchBrief(
                    request_text="Fix the prize classification bug.",
                    intents=("bug_fix",),
                    requested_outputs=("bug_fix",),
                ),
                root=root / "session",
                services=ResearchApplicationServices(
                    llm_client=LLMClient(LLMSettings(api_key="test-key", api_mode="chat")),
                    config={"research_plan_mode": "deterministic", "execution": execution},
                    budget_limits={
                        "llm_requests": 12,
                        "total_tokens": 50_000,
                        "process_invocations": 1,
                        "process_wall_seconds": 20,
                    },
                ),
            )

            planned = app.advance()
            self.assertEqual(planned.next_action, "prepare_execution")
            planned = app.advance(max_actions=1)
            self.assertEqual(planned.next_action, "implement")
            resolved = app._effective_config()["execution"]
            self.assertEqual(set(resolved["code_task"]), {"run_dir", "approval_note"})
            from simple_ar.code_task.runtime.state import load_code_task_manifest
            manifest = load_code_task_manifest(Path(resolved["code_task"]["run_dir"]))
            self.assertEqual(manifest["environment"]["policy"]["mode"], "external")
            self.assertEqual(manifest["environment"]["policy"]["python_executable"], sys.executable)
            self.assertNotEqual(Path(resolved["cwd"]), project)
            self.assertIn("task_plan", planned.state_refs)
            self.assertNotIn("plan", planned.state_refs)
            fake = _FakeCodeTaskClient()
            payloads = [
                fake.ask_json("", "", label=label)
                for label in ("code-task-work-plan", "code-task-plan", "code-task-propose-edits")
            ] + [{"findings": []}] * 8
            responses = [
                {
                    "choices": [{"message": {"content": json.dumps(payload)}}],
                    "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
                }
                for payload in payloads
            ]
            with patch("simple_ar.integrations.llm._call_openai_sdk", side_effect=responses), patch.object(
                LLMClient, "for_task", return_value=fake,
            ):
                finished = app.advance()

            self.assertEqual(finished.status, "completed", finished.status_reason)
            implementation = app.controller.store.read_json(finished.state_refs["implementation"])
            self.assertEqual(implementation["status"], "validated")
            self.assertNotIn("search", finished.state_refs)
            self.assertNotIn("experiment", finished.state_refs)
            self.assertEqual(
                {attempt["capability"] for attempt in finished.attempts},
                {"plan", "prepare_execution", "implement"},
            )
            self.assertEqual(
                (Path(resolved["cwd"]) / "benchmark.py").read_text(),
                (project / "benchmark.py").read_text(),
            )

    def test_application_executes_an_optional_step_declared_by_the_accepted_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "agents.md"
            paper.write_text(
                "Validation improves reliable agent behavior and reports accuracy.\n",
                encoding="utf-8",
            )
            app = create_session(
                ResearchBrief(
                    request_text="Study reliable-agent validation.",
                    intents=("assessment",),
                    requested_outputs=("research_summary",),
                    asset_requests=({"locator": str(paper), "role": "paper"},),
                ),
                root=root / "session",
                services=ResearchApplicationServices(max_results=1, max_chunks=10),
            )

            finished = app.advance(max_actions=8)

            self.assertEqual(finished.status, "completed", finished.status_reason)
            self.assertIn("assessment", finished.state_refs)
            self.assertIn("idea_comparison", finished.state_refs)
            self.assertIn("assess_ideas", {attempt["capability"] for attempt in finished.attempts})
            assessment_step = next(
                row for row in finished.work_plan["accepted_plan"]["steps"]
                if row["action"] == "assess_ideas"
            )
            self.assertEqual(assessment_step["status"], "completed")

    def test_revision_followup_waits_for_implementation_state_before_measurement(self) -> None:
        plan = TaskPlanResult(
            status="accepted",
            mode="deterministic",
            task_kind="research",
            goal="Continue the bounded experiment.",
            steps=(
                TaskPlanStep(
                    step_id="design", action="research_design", capability="research_design",
                    state_name="design", problem_solved="", observation="",
                ),
            ),
        )

        extended = append_research_followup(plan, 1, action="revise_candidate", pair_count=1)
        rows = {step.action: step for step in extended.steps}

        self.assertEqual(
            rows["research_candidate:1_0"].condition,
            "after_success:implementation_r1",
        )


if __name__ == "__main__":
    unittest.main()
