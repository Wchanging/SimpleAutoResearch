from __future__ import annotations

import tempfile
import unittest

from simple_ar.core import (
    BudgetState,
    CapabilityContext,
    CapabilityRegistry,
    CapabilityResult,
    SessionController,
    SessionManifest,
    SessionStep,
    TransitionPolicy,
    TransitionRequest,
    classify_failure,
    lifecycle_profile_names,
    resolve_lifecycle_profile,
    run_session_plan,
)


class SessionTransitionTests(unittest.TestCase):
    def test_failure_classification_is_deterministic(self) -> None:
        self.assertEqual(classify_failure("failed", ("HTTP 503",)), "transient")
        self.assertEqual(classify_failure("failed", ("missing required field",)), "schema")
        self.assertEqual(classify_failure("failed", ("timeout while running",)), "resource")
        self.assertEqual(classify_failure("failed", ("metric below target",)), "metric")
        self.assertEqual(classify_failure("failed", ("unsupported claim",)), "evidence")
        self.assertEqual(classify_failure("failed", ("review found incoherence",)), "quality")
        self.assertEqual(classify_failure("failed", ("traceback in runner",)), "runtime")
        self.assertEqual(classify_failure("completed"), "none")

    def test_policy_rejects_unlisted_jump(self) -> None:
        decision = TransitionPolicy().decide(
            TransitionRequest(
                source="plan",
                result_status="completed",
                target="report",
            )
        )

        self.assertEqual(decision.action, "block")
        self.assertIsNone(decision.target)
        self.assertIn("not allowed", decision.reason)

    def test_semantic_signal_uses_allowlisted_target(self) -> None:
        decision = TransitionPolicy().decide(
            TransitionRequest(
                source="run",
                result_status="completed",
                target="design",
                hypothesis_supported=False,
                expected_delta="replace unsupported hypothesis",
            )
        )

        self.assertEqual(decision.action, "revise")
        self.assertEqual(decision.target, "design")
        self.assertEqual(decision.expected_delta, "replace unsupported hypothesis")

    def test_builtin_lifecycle_profile_definitions_are_stable(self) -> None:
        survey = resolve_lifecycle_profile("survey")
        self.assertIsNotNone(survey)
        self.assertEqual(
            lifecycle_profile_names(),
            ("research_brief", "survey", "experiment", "paper_audit", "full_research"),
        )
        assert survey is not None
        self.assertEqual(
            survey.capabilities,
            ("plan", "search", "document_ingest", "read", "synthesize", "report"),
        )
        self.assertFalse(survey.allows("code"))

    def test_builtin_profile_paths_match_transition_recipe(self) -> None:
        for profile_name in lifecycle_profile_names():
            profile = resolve_lifecycle_profile(profile_name)
            assert profile is not None
            for source, target in zip(profile.capabilities, profile.capabilities[1:]):
                decision = TransitionPolicy().decide(
                    TransitionRequest(
                        source=source,
                        result_status="completed",
                        target=target,
                    )
                )
                self.assertNotEqual(
                    decision.action,
                    "block",
                    f"{profile_name}: {source} -> {target}",
                )

    def test_full_research_profile_default_budget_covers_named_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()

            def handler(**_: object) -> CapabilityResult:
                return CapabilityResult(status="completed")

            profile = resolve_lifecycle_profile("full_research")
            assert profile is not None
            for capability in profile.capabilities:
                registry.register(capability, handler)

            controller = SessionController.create(
                tmp,
                session_id="full-research-budget",
                topic="default profile budget",
                registry=registry,
                profile="full_research",
            )
            outcomes = run_session_plan(
                controller,
                [
                    SessionStep(capability, f"attempt-{index:02d}")
                    for index, capability in enumerate(profile.capabilities, start=1)
                ],
            )

            self.assertEqual(len(outcomes), len(profile.capabilities))
            self.assertEqual(controller.manifest.status, "completed")
            self.assertEqual(
                controller.manifest.budget.max_attempts,
                len(profile.capabilities) + 2,
            )

    def test_missing_available_output_is_visible_at_session_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()

            def handler(*, context: CapabilityContext) -> CapabilityResult:
                return CapabilityResult(
                    status="completed",
                    artifacts=(context.store.ref("missing.json", kind="result"),),
                )

            registry.register("probe", handler)
            controller = SessionController.create(
                tmp,
                session_id="missing-output",
                topic="declared output validation",
                registry=registry,
            )

            result, decision = controller.execute("probe", attempt_id="attempt-001")

            self.assertEqual(result.status, "partial")
            self.assertEqual(decision.action, "revise")
            self.assertEqual(result.artifacts[0].status, "missing")
            self.assertIn("missing.json", result.diagnostics[0])
            attempt = controller.store.read_attempt_manifest(
                "attempts/attempt-001/attempt_manifest.json"
            )
            self.assertEqual(attempt.outputs[0].status, "missing")

    def test_full_research_allows_synthesis_to_experiment_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            calls: list[str] = []

            def handler(*, context: CapabilityContext) -> CapabilityResult:
                calls.append(context.attempt.capability or "")
                return CapabilityResult(status="completed")

            registry.register("synthesize", handler)
            registry.register("experiment", handler)
            controller = SessionController.create(
                tmp,
                session_id="synthesis-experiment-route",
                topic="composable experiment route",
                profile="full_research",
                registry=registry,
            )

            first, first_decision = controller.execute(
                "synthesize",
                attempt_id="attempt-001",
                next_capability="experiment",
            )
            second, second_decision = controller.execute(
                "experiment",
                attempt_id="attempt-002",
            )

            self.assertEqual(first.status, "completed")
            self.assertEqual(first_decision.action, "accept")
            self.assertEqual(second.status, "completed")
            self.assertEqual(second_decision.action, "accept")
            self.assertEqual(calls, ["synthesize", "experiment"])

    def test_canonical_analysis_capability_connects_from_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()

            def handler(**_: object) -> CapabilityResult:
                return CapabilityResult(status="completed")

            registry.register("run", handler)
            registry.register("analysis", handler)
            controller = SessionController.create(
                tmp,
                session_id="canonical-analysis-route",
                topic="canonical analysis capability",
                profile="full_research",
                registry=registry,
            )

            _, run_decision = controller.execute(
                "run",
                attempt_id="attempt-001",
                next_capability="analysis",
            )
            _, analysis_decision = controller.execute(
                "analysis",
                attempt_id="attempt-002",
            )

            self.assertEqual(run_decision.action, "accept")
            self.assertEqual(run_decision.next_capability, "analysis")
            self.assertEqual(analysis_decision.action, "accept")
            self.assertEqual(controller.manifest.status, "completed")

    def test_legacy_analyze_alias_remains_allowed(self) -> None:
        experiment = resolve_lifecycle_profile("experiment")
        full = resolve_lifecycle_profile("full_research")
        assert experiment is not None
        assert full is not None

        self.assertTrue(experiment.allows("analyze"))
        self.assertTrue(full.allows("analyze"))
        self.assertEqual(
            TransitionPolicy().decide(
                TransitionRequest(
                    source="run",
                    result_status="completed",
                    target="analyze",
                )
            ).action,
            "accept",
        )

    def test_all_builtin_profiles_execute_their_caller_owned_fixture_paths(self) -> None:
        for profile_name in lifecycle_profile_names():
            with self.subTest(profile=profile_name), tempfile.TemporaryDirectory() as tmp:
                registry = CapabilityRegistry()

                def handler(**_: object) -> CapabilityResult:
                    return CapabilityResult(status="completed")

                profile = resolve_lifecycle_profile(profile_name)
                assert profile is not None
                for capability in profile.capabilities:
                    registry.register(capability, handler)

                controller = SessionController.create(
                    tmp,
                    session_id=f"fixture-{profile_name}",
                    topic="profile fixture",
                    registry=registry,
                    profile=profile_name,
                )
                outcomes = run_session_plan(
                    controller,
                    [
                        SessionStep(capability, f"attempt-{index:02d}")
                        for index, capability in enumerate(profile.capabilities, start=1)
                    ],
                )

                self.assertEqual(len(outcomes), len(profile.capabilities))
                self.assertTrue(all(result.status == "completed" for result, _ in outcomes))
                self.assertEqual(controller.manifest.status, "completed")

    def test_profiles_allow_only_named_composite_capability_aliases(self) -> None:
        brief = resolve_lifecycle_profile("research_brief")
        experiment = resolve_lifecycle_profile("experiment")
        full = resolve_lifecycle_profile("full_research")
        assert brief is not None
        assert experiment is not None
        assert full is not None

        self.assertTrue(brief.allows("research_brief"))
        self.assertFalse(brief.allows("report_audit"))
        self.assertTrue(experiment.allows("experiment"))
        self.assertTrue(experiment.allows("analysis"))
        self.assertTrue(experiment.allows("report_audit"))
        self.assertTrue(full.allows("report_audit"))
        self.assertEqual(
            TransitionPolicy().decide(
                TransitionRequest(
                    source="experiment",
                    result_status="completed",
                    target="analysis",
                )
            ).action,
            "accept",
        )
        self.assertEqual(
            TransitionPolicy().decide(
                TransitionRequest(
                    source="report",
                    result_status="completed",
                    target="report_audit",
                )
            ).action,
            "accept",
        )
        self.assertEqual(
            TransitionPolicy().decide(
                TransitionRequest(
                    source="report_audit",
                    result_status="failed",
                    target="report",
                )
            ).action,
            "repair",
        )

    def test_controller_enforces_known_profile_but_keeps_legacy_profile_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("read", lambda **_: CapabilityResult(status="completed"))
            registry.register("report", lambda **_: CapabilityResult(status="completed"))
            survey = SessionController.create(
                tmp,
                session_id="survey-session",
                topic="profile scope",
                registry=registry,
                profile="survey",
            )

            allowed = survey.plan_transition(
                TransitionRequest(source="read", result_status="completed", target="report")
            )
            rejected = survey.plan_transition(
                TransitionRequest(
                    source="synthesize", result_status="completed", target="design"
                )
            )
            self.assertEqual(allowed.action, "accept")
            self.assertEqual(allowed.target, "report")
            self.assertEqual(rejected.action, "block")
            self.assertIn("lifecycle profile survey", rejected.reason)
            self.assertEqual(
                survey.allowed_targets("synthesize"),
                ("search", "read", "synthesize", "report"),
            )

            with tempfile.TemporaryDirectory() as legacy_tmp:
                legacy = SessionController.create(
                    legacy_tmp,
                    session_id="legacy-profile-session",
                    topic="legacy profile",
                    registry=registry,
                    profile="custom-old-profile",
                )
                self.assertEqual(
                    legacy.plan_transition(
                        TransitionRequest(
                            source="read", result_status="completed", target="report"
                        )
                    ).action,
                    "accept",
                )

    def test_controller_rejects_out_of_scope_execution_before_running_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("design", lambda **_: CapabilityResult(status="completed"))
            controller = SessionController.create(
                tmp,
                session_id="scoped-execution",
                topic="profile scope",
                registry=registry,
                profile="survey",
            )

            with self.assertRaisesRegex(ValueError, "cannot override session profile"):
                controller.execute(
                    "design", attempt_id="attempt-001", profile="legacy-override"
                )
            self.assertEqual(controller.list_attempts(), ())

    def test_controller_does_not_allow_attempt_profile_to_escape_session_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls: list[str] = []

            def handler(*, context: CapabilityContext) -> CapabilityResult:
                calls.append(context.attempt.attempt_id)
                return CapabilityResult(status="completed")

            registry = CapabilityRegistry()
            registry.register("read", handler)
            controller = SessionController.create(
                tmp,
                session_id="profile-override",
                topic="profile consistency",
                registry=registry,
                profile="survey",
            )

            with self.assertRaisesRegex(ValueError, "cannot override session profile"):
                controller.execute(
                    "read",
                    attempt_id="attempt-001",
                    profile="experiment",
                )

            self.assertEqual(calls, [])
            self.assertEqual(controller.list_attempts(), ())

    def test_controller_rejects_invalid_target_before_running_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls: list[str] = []
            registry = CapabilityRegistry()

            def run(**_: object) -> CapabilityResult:
                calls.append("called")
                return CapabilityResult(status="completed")

            registry.register("plan", run)
            controller = SessionController.create(
                tmp,
                session_id="preflight-target",
                topic="preflight",
                registry=registry,
            )

            with self.assertRaisesRegex(ValueError, "not allowed"):
                controller.execute(
                    "plan",
                    attempt_id="attempt-001",
                    next_capability="report",
                )

            self.assertEqual(calls, [])
            self.assertEqual(controller.list_attempts(), ())

    def test_controller_rejects_illegal_next_capability_before_running_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls: list[str] = []

            def run(*, context: CapabilityContext) -> CapabilityResult:
                calls.append(context.attempt.capability or "")
                return CapabilityResult(status="completed")

            registry = CapabilityRegistry()
            registry.register("plan", run)
            registry.register("report", run)
            controller = SessionController.create(
                tmp,
                session_id="preflight-actual-capability",
                topic="actual transition",
                registry=registry,
                profile="full_research",
            )

            controller.execute(
                "plan",
                attempt_id="attempt-001",
                next_capability="search",
            )
            with self.assertRaisesRegex(ValueError, "plan -> report"):
                controller.execute("report", attempt_id="attempt-002")

            self.assertEqual(calls, ["plan"])
            self.assertEqual(len(controller.list_attempts()), 1)

    def test_status_snapshot_is_compact_and_domain_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("plan", lambda **_: CapabilityResult(status="completed"))
            registry.register("search", lambda **_: CapabilityResult(status="completed"))
            controller = SessionController.create(
                tmp,
                session_id="status-snapshot",
                topic="snapshot",
                registry=registry,
            )

            controller.execute("plan", attempt_id="attempt-001", next_capability="search")
            snapshot = controller.status_snapshot("plan")

            self.assertEqual(snapshot["schema_version"], "session_status.v1")
            self.assertEqual(snapshot["attempt_count"], 1)
            self.assertEqual(snapshot["running_attempts"], 0)
            self.assertEqual(snapshot["completed_attempts"], 1)
            self.assertEqual(snapshot["failed_attempts"], 0)
            self.assertEqual(snapshot["allowed_targets"], ["search", "plan"])
            self.assertEqual(snapshot["last_decision"]["next_capability"], "search")
            self.assertNotIn("artifacts", snapshot)

    def test_handler_observes_durable_running_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()

            def inspect_running_state(*, context: CapabilityContext) -> CapabilityResult:
                attempt_store = context.store
                attempt = attempt_store.read_attempt_manifest()
                if context.input_store is None:
                    raise AssertionError("session input store is required")
                session = context.input_store.read_json("session_manifest.json")
                self.assertEqual(context.attempt.status, "running")
                self.assertEqual(attempt.status, "running")
                self.assertEqual(session["status"], "running")
                self.assertEqual(session["current_attempt"], "attempt-001")
                self.assertEqual(attempt.capability, "inspect-running")
                return CapabilityResult(status="completed")

            registry.register("inspect-running", inspect_running_state)
            controller = SessionController.create(
                tmp,
                session_id="durable-running",
                topic="durable state",
                registry=registry,
            )

            result, _ = controller.execute("inspect-running", attempt_id="attempt-001")

            self.assertEqual(result.status, "completed")
            self.assertEqual(controller.status_snapshot()["running_attempts"], 0)

    def test_process_interrupt_leaves_attempt_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()

            def interrupt(**_: object) -> CapabilityResult:
                raise KeyboardInterrupt()

            registry.register("interrupt", interrupt)
            controller = SessionController.create(
                tmp,
                session_id="process-interrupt",
                topic="interrupt state",
                registry=registry,
            )

            with self.assertRaises(KeyboardInterrupt):
                controller.execute("interrupt", attempt_id="attempt-001")

            resumed = SessionController.load(tmp, registry=registry)
            snapshot = resumed.status_snapshot()
            self.assertEqual(snapshot["status"], "running")
            self.assertEqual(snapshot["running_attempts"], 1)
            self.assertEqual(snapshot["current_attempt"], "attempt-001")
            with self.assertRaisesRegex(RuntimeError, "recover_interrupted"):
                resumed.execute("interrupt", attempt_id="attempt-002")
            self.assertFalse(
                (resumed.store.root / "attempts" / "attempt-002").exists()
            )
            self.assertEqual(
                snapshot["active_attempts"],
                [
                    {
                        "attempt_id": "attempt-001",
                        "capability": "interrupt",
                        "updated_at": snapshot["active_attempts"][0]["updated_at"],
                    }
                ],
            )
            self.assertEqual(
                resumed.store.read_attempt_manifest("attempts/attempt-001/attempt_manifest.json").status,
                "running",
            )

    def test_explicit_interrupted_recovery_closes_attempt_without_retrying(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()

            def interrupt(**_: object) -> CapabilityResult:
                raise KeyboardInterrupt()

            registry.register("interrupt", interrupt)
            controller = SessionController.create(
                tmp,
                session_id="explicit-interrupt-recovery",
                topic="interrupt recovery",
                registry=registry,
            )

            with self.assertRaises(KeyboardInterrupt):
                controller.execute("interrupt", attempt_id="attempt-001")

            resumed = SessionController.load(tmp, registry=registry)
            result, decision = resumed.recover_interrupted(
                reason="The worker process ended before returning a capability result."
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(decision.action, "repair")
            self.assertEqual(decision.failure_kind, "runtime")
            self.assertEqual(decision.next_capability, "interrupt")
            self.assertEqual(resumed.status_snapshot()["running_attempts"], 0)
            self.assertEqual(resumed.status_snapshot()["failed_attempts"], 1)
            self.assertEqual(
                resumed.store.read_attempt_manifest(
                    "attempts/attempt-001/attempt_manifest.json"
                ).status,
                "failed",
            )
            self.assertEqual(
                resumed.store.read_capability_result(
                    "attempts/attempt-001/capability_result.json"
                ).diagnostics,
                ("The worker process ended before returning a capability result.",),
            )

    def test_explicit_session_plan_preflights_and_completes_profile_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()

            def handler(*, context: CapabilityContext, marker: str) -> CapabilityResult:
                output = context.store.write_text(
                    f"{context.attempt.capability}.txt",
                    marker,
                    kind="fixture_output",
                )
                return CapabilityResult(status="completed", artifacts=(output,))

            for name in ("plan", "search", "read", "synthesize"):
                registry.register(name, handler)
            controller = SessionController.create(
                tmp,
                session_id="explicit-plan",
                topic="plan fixture",
                profile="research_brief",
                registry=registry,
                budget=BudgetState(max_attempts=4),
            )

            outcomes = run_session_plan(
                controller,
                [
                    SessionStep("plan", "attempt-001", handler_kwargs={"marker": "p"}),
                    SessionStep("search", "attempt-002", handler_kwargs={"marker": "s"}),
                    SessionStep("read", "attempt-003", handler_kwargs={"marker": "r"}),
                    SessionStep("synthesize", "attempt-004", handler_kwargs={"marker": "y"}),
                ],
            )

            self.assertEqual(len(outcomes), 4)
            self.assertEqual(controller.manifest.status, "completed")
            self.assertEqual([item[1].next_capability for item in outcomes], [
                "search",
                "read",
                "synthesize",
                None,
            ])
            self.assertEqual(
                controller.store.read_attempt_manifest(
                    "attempts/attempt-004/attempt_manifest.json"
                ).parent_attempt,
                "attempt-003",
            )

    def test_explicit_session_plan_stops_after_non_accept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            calls: list[str] = []

            def first(*, context: CapabilityContext) -> CapabilityResult:
                calls.append(context.attempt.capability)
                return CapabilityResult(status="failed", diagnostics=("fixture failure",))

            def second(*, context: CapabilityContext) -> CapabilityResult:
                calls.append(context.attempt.capability)
                return CapabilityResult(status="completed")

            registry.register("plan", first)
            registry.register("search", second)
            controller = SessionController.create(
                tmp,
                session_id="stopped-plan",
                topic="stopped plan",
                registry=registry,
            )

            outcomes = run_session_plan(
                controller,
                [SessionStep("plan", "attempt-001"), SessionStep("search", "attempt-002")],
            )

            self.assertEqual(len(outcomes), 1)
            self.assertEqual(calls, ["plan"])
            self.assertEqual(outcomes[0][1].action, "repair")

    def test_explicit_session_plan_rejects_attempt_conflicts_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls: list[str] = []

            def handler(*, context: CapabilityContext) -> CapabilityResult:
                calls.append(context.attempt.attempt_id)
                return CapabilityResult(status="completed")

            registry = CapabilityRegistry()
            registry.register("plan", handler)
            controller = SessionController.create(
                tmp,
                session_id="explicit-plan-conflict",
                topic="plan conflict",
                registry=registry,
            )

            with self.assertRaisesRegex(ValueError, "must be unique"):
                run_session_plan(
                    controller,
                    [SessionStep("plan", "attempt-001"), SessionStep("plan", "attempt-001")],
                )
            self.assertEqual(calls, [])
            self.assertEqual(controller.list_attempts(), ())

            controller.execute("plan", attempt_id="attempt-001")
            with self.assertRaisesRegex(ValueError, "already exist"):
                run_session_plan(controller, [SessionStep("plan", "attempt-001")])
            self.assertEqual(calls, ["attempt-001"])

    def test_resumed_session_plan_preflights_from_current_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            calls: list[str] = []

            def handler(*, context: CapabilityContext) -> CapabilityResult:
                calls.append(context.attempt.capability or "")
                return CapabilityResult(status="completed")

            registry.register("plan", handler)
            registry.register("report", handler)
            controller = SessionController.create(
                tmp,
                session_id="resumed-plan-preflight",
                topic="resume route",
                registry=registry,
            )
            controller.execute("plan", attempt_id="attempt-001")

            with self.assertRaisesRegex(ValueError, "not allowed"):
                run_session_plan(
                    controller,
                    [SessionStep("report", "attempt-002")],
                )

            self.assertEqual(calls, ["plan"])
            self.assertEqual(
                [item.attempt_id for item in controller.list_attempts()],
                ["attempt-001"],
            )

    def test_session_plan_can_create_an_explicit_bounded_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()

            def handler(*, context: CapabilityContext) -> CapabilityResult:
                return CapabilityResult(status="completed")

            registry.register("plan", handler)
            registry.register("search", handler)
            controller = SessionController.create(
                tmp,
                session_id="session-plan-branch",
                topic="bounded branch",
                registry=registry,
            )
            controller.execute("plan", attempt_id="attempt-001", next_capability="search")

            outcomes = run_session_plan(
                controller,
                [
                    SessionStep(
                        "search",
                        "attempt-002",
                        parent_attempt_id="attempt-001",
                    )
                ],
            )

            self.assertEqual(len(outcomes), 1)
            self.assertEqual(outcomes[0][1].action, "accept")
            self.assertEqual(
                controller.store.read_attempt_manifest(
                    "attempts/attempt-002/attempt_manifest.json"
                ).parent_attempt,
                "attempt-001",
            )
            self.assertEqual(controller.manifest.status, "completed")

    def test_explicit_session_plan_passes_transition_signals_to_controller(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()

            def handler(*, context: CapabilityContext) -> CapabilityResult:
                return CapabilityResult(status="completed")

            registry.register("read", handler)
            registry.register("synthesize", handler)
            controller = SessionController.create(
                tmp,
                session_id="explicit-plan-signals",
                topic="transition signals",
                profile="research_brief",
                registry=registry,
            )

            outcomes = run_session_plan(
                controller,
                [
                    SessionStep(
                        "read",
                        "attempt-001",
                        evidence_sufficient=False,
                    ),
                    SessionStep("synthesize", "attempt-002"),
                ],
            )

            self.assertEqual(len(outcomes), 1)
            self.assertEqual(outcomes[0][1].action, "revise")
            self.assertEqual(outcomes[0][1].next_capability, "synthesize")
            self.assertEqual(controller.manifest.status, "running")

    def test_controller_records_transition_and_lists_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("plan", lambda **_: CapabilityResult(status="completed"))
            registry.register("search", lambda **_: CapabilityResult(status="completed"))
            controller = SessionController.create(
                tmp,
                session_id="session-transition",
                topic="offline transition",
                registry=registry,
                budget=BudgetState(max_attempts=3),
            )

            _, first = controller.execute(
                "plan",
                attempt_id="attempt-001",
                next_capability="search",
                expected_delta="retrieve evidence",
            )
            _, second = controller.execute("search", attempt_id="attempt-002")

            self.assertEqual(first.action, "accept")
            self.assertEqual(first.next_capability, "search")
            self.assertEqual(first.failure_kind, "none")
            self.assertEqual(first.budget_attempts, 1)
            self.assertEqual(first.budget_no_progress, 0)
            self.assertEqual(second.action, "accept")
            self.assertEqual(controller.manifest.status, "completed")
            self.assertEqual([item.attempt_id for item in controller.list_attempts()], [
                "attempt-001",
                "attempt-002",
            ])

    def test_controller_normalizes_capability_and_attempt_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()

            def handler(*, context: CapabilityContext) -> CapabilityResult:
                self.assertEqual(context.attempt.capability, "plan")
                self.assertEqual(context.attempt.attempt_id, "attempt-001")
                return CapabilityResult(status="completed")

            registry.register("plan", handler)
            controller = SessionController.create(
                tmp,
                session_id="normalized-metadata",
                topic="normalized metadata",
                registry=registry,
            )

            result, _ = controller.execute(
                "  plan  ",
                attempt_id="  attempt-001  ",
            )

            self.assertEqual(result.status, "completed")
            attempt = controller.list_attempts()[0]
            self.assertEqual(attempt.capability, "plan")
            self.assertEqual(attempt.attempt_id, "attempt-001")

    def test_attempt_output_refs_are_explicit_session_root_handoffs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()

            def produce(*, context: CapabilityContext) -> CapabilityResult:
                output = context.store.write_text("result.txt", "from-first\n", kind="result")
                return CapabilityResult(status="completed", artifacts=(output,))

            def consume(*, context: CapabilityContext) -> CapabilityResult:
                if len(context.inputs) != 1:
                    return CapabilityResult(status="failed", diagnostics=("missing handoff",))
                value = context.read_input_text(context.inputs[0])
                output = context.store.write_text("consumed.txt", value, kind="result")
                return CapabilityResult(status="completed", artifacts=(output,))

            registry.register("plan", produce)
            registry.register("search", consume)
            controller = SessionController.create(
                tmp,
                session_id="explicit-handoff",
                topic="attempt output handoff",
                registry=registry,
            )

            controller.execute("plan", attempt_id="attempt-001", next_capability="search")
            refs = controller.attempt_output_refs("attempt-001")

            self.assertEqual(refs[0].path, "attempts/attempt-001/result.txt")
            self.assertEqual(controller.store.read_text(refs[0]), "from-first\n")
            result, decision = controller.execute(
                "search",
                attempt_id="attempt-002",
                inputs=refs,
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            self.assertEqual(
                controller.store.read_text("attempts/attempt-002/consumed.txt"),
                "from-first\n",
            )

    def test_missing_input_is_rejected_before_attempt_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            calls: list[str] = []

            def handler(**_: object) -> CapabilityResult:
                calls.append("called")
                return CapabilityResult(status="completed")

            registry.register("search", handler)
            controller = SessionController.create(
                tmp,
                session_id="missing-input",
                topic="preflight handoff",
                registry=registry,
            )

            with self.assertRaisesRegex(FileNotFoundError, "does not exist"):
                controller.execute(
                    "search",
                    attempt_id="attempt-001",
                    inputs=(controller.store.ref("missing.json", kind="input"),),
                )

            self.assertEqual(calls, [])
            self.assertEqual(controller.list_attempts(), ())
            self.assertEqual(controller.manifest.budget.attempts, 0)

    def test_unregistered_capability_is_rejected_before_attempt_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = SessionController.create(
                tmp,
                session_id="missing-capability",
                topic="preflight handler",
                registry=CapabilityRegistry(),
            )

            with self.assertRaisesRegex(KeyError, "Unknown capability"):
                controller.execute("missing", attempt_id="attempt-001")

            self.assertEqual(controller.list_attempts(), ())
            self.assertEqual(controller.manifest.budget.attempts, 0)

    def test_attempt_output_ref_selects_one_declared_domain_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()

            def handler(*, context: CapabilityContext) -> CapabilityResult:
                result = context.store.write_json(
                    "results.json",
                    {"score": 1.0},
                    kind="experiment_result",
                    schema="results.v1",
                )
                alternate = context.store.write_json(
                    "results-summary.json",
                    {"score": 1.0},
                    kind="experiment_result",
                    schema="results-summary.v1",
                )
                context.store.write_text(
                    "execution/stdout.txt",
                    "score: 1.0\n",
                    kind="execution_log",
                    schema="text.v1",
                )
                return CapabilityResult(
                    status="completed",
                    artifacts=(result, alternate),
                )

            registry.register("experiment", handler)
            controller = SessionController.create(
                tmp,
                session_id="output-ref",
                topic="output ref",
                registry=registry,
            )

            controller.execute("experiment", attempt_id="attempt-001")
            result_ref = controller.attempt_output_ref(
                "attempt-001",
                kind="experiment_result",
                schema="results.v1",
            )

            self.assertEqual(result_ref.path, "attempts/attempt-001/results.json")
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                controller.attempt_output_ref("attempt-001", kind="experiment_result")
            with self.assertRaisesRegex(KeyError, "no output"):
                controller.attempt_output_ref("attempt-001", kind="missing")

    def test_attempt_output_refs_require_a_persisted_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = SessionController.create(
                tmp,
                session_id="missing-handoff",
                topic="missing attempt",
                registry=CapabilityRegistry(),
            )

            with self.assertRaisesRegex(ValueError, "attempt id is required"):
                controller.attempt_output_refs()
            with self.assertRaisesRegex(KeyError, "Unknown attempt"):
                controller.attempt_output_refs("attempt-404")

    def test_old_manifest_shape_keeps_default_recipe(self) -> None:
        manifest = SessionManifest.from_dict(
            {
                "session_id": "legacy-session",
                "topic": "legacy topic",
                "status": "created",
                "budget": {},
            }
        )

        self.assertEqual(manifest.transition_recipe, "research-v1")
        self.assertEqual(manifest.decisions, [])

    def test_session_manifest_keeps_legacy_positional_field_order(self) -> None:
        manifest = SessionManifest(
            "legacy-session",
            "legacy topic",
            None,
            "created",
            None,
            BudgetState(),
            [],
            "created-at",
            "updated-at",
        )

        self.assertEqual(manifest.status, "created")
        self.assertEqual(manifest.created_at, "created-at")
        self.assertEqual(manifest.transition_recipe, "research-v1")


if __name__ == "__main__":
    unittest.main()
