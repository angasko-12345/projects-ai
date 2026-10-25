"""Capability profiles and deterministic agent routing."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from agentops.config import AgentConfig, AppConfig, load_config
from agentops.events import EventType
from agentops.registry import AgentHealth,  AgentRegistry, DetectedAgent
from agentops.routing import (
    AgentCapabilityResolver,
    AgentProfile,
    AgentRouter,
    RoutingDecision,
)
from agentops.runner import RunResult
from agentops.state import StateStore
from agentops.tasks import Task
from agentops.workflow import WorkflowEngine


def _config(
    name: str,
    roles: tuple[str, ...] = ("implementation",),
    *,
    enabled: bool = True,
    capabilities: tuple[str, ...] = (),
    priority: int = 0,
    display_name: str | None = None,
    metadata: dict[str, object] | None = None,
) -> AgentConfig:
    return AgentConfig(
        name=name,
        command=name,
        args=("{prompt}",),
        roles=roles,
        enabled=enabled,
        capabilities=capabilities,
        priority=priority,
        display_name=display_name,
        metadata=metadata or {},
    )


def _detected(config: AgentConfig, available: bool = True) -> DetectedAgent:
    return DetectedAgent(config, available, f"/bin/{config.name}" if available else None, "1.2.3")


def _profiles(*detected: DetectedAgent) -> tuple[AgentProfile, ...]:
    resolver = AgentCapabilityResolver()
    return tuple(resolver.profile(item) for item in detected)



def _registry_with(name):
    return AppConfig({name: _config(name, ("implementation",))},
                     {"implementation": (name,)})


class AgentCapabilityResolverTests(unittest.TestCase):
    def setUp(self):
        self.resolver = AgentCapabilityResolver()

    def test_profile_contains_requested_fields_and_derived_capabilities(self):
        config = _config(
            "builder",
            ("implementation", "debugging"),
            capabilities=("security review",),
            priority=4,
            display_name="Builder",
            metadata={"owner": "platform"},
        )
        profile = self.resolver.profile(_detected(config))

        self.assertEqual(profile.identifier, "builder")
        self.assertEqual(profile.display_name, "Builder")
        self.assertEqual(profile.executable_path, "/bin/builder")
        self.assertEqual(profile.detected_version, "1.2.3")
        self.assertTrue(profile.availability)
        self.assertEqual(profile.roles, ("implementation", "debugging"))
        self.assertIn("implementation", profile.capabilities)
        self.assertIn("debugging", profile.capabilities)
        self.assertIn("testing", profile.capabilities)
        self.assertIn("repository exploration", profile.capabilities)
        self.assertIn("security review", profile.capabilities)
        self.assertTrue(profile.supported_structured_output)
        self.assertTrue(profile.cancellation_support)
        self.assertTrue(profile.timeout_support)
        self.assertTrue(profile.noninteractive_support)
        self.assertEqual(profile.configured_priority, 4)
        self.assertEqual(profile.metadata["owner"], "platform")

    def test_resolver_matches_known_aliases_and_declared_extras(self):
        config = _config("rich", capabilities=("mcp", "telepathy"))
        profile = self.resolver.profile(_detected(config))
        self.assertTrue(self.resolver.matches(profile, ("implementation", "mcp", "telepathy")))
        self.assertFalse(self.resolver.matches(profile, ("security review",)))

    def test_role_requirements_are_deterministic_and_task_aware(self):
        required = self.resolver.required_capabilities(
            "implementation", "Refactor the service and add tests", {"security_sensitive": True}
        )
        self.assertIn("implementation", required)
        self.assertIn("refactoring", required)
        self.assertIn("testing", required)
        self.assertIn("security review", required)
        self.assertEqual(required, tuple(sorted(required)))

    def test_declared_legacy_capabilities_expand_to_task_capabilities(self):
        coding = self.resolver.profile(_detected(_config(
            "coder", ("architecture",), capabilities=("coding",)
        )))
        self.assertIn("implementation", coding.capabilities)
        self.assertIn("refactoring", coding.capabilities)
        self.assertIn("testing", coding.capabilities)
        reviewing = self.resolver.profile(_detected(_config(
            "reviewer", ("architecture",), capabilities=("review",)
        )))
        self.assertIn("code review", reviewing.capabilities)
        self.assertIn("security review", reviewing.capabilities)


class AgentRouterTests(unittest.TestCase):
    def setUp(self):
        self.resolver = AgentCapabilityResolver()
        self.preferred = _detected(_config("preferred", ("implementation",), priority=9))
        self.fallback = _detected(_config("fallback", ("implementation",), priority=1))
        self.reviewer = _detected(_config("reviewer", ("review",), capabilities=("code review",)))
        self.profiles = _profiles(self.preferred, self.fallback, self.reviewer)

    def test_capability_matching_and_deterministic_scoring(self):
        router = AgentRouter(resolver=self.resolver)
        first = router.route(
            role="implementation",
            task_description="Implement a feature",
            required_capabilities=("implementation",),
            available_agents=self.profiles,
            user_preferences=("preferred",),
        )
        second = router.route(
            role="implementation",
            task_description="Implement a feature",
            required_capabilities=("implementation",),
            available_agents=self.profiles,
            user_preferences=("preferred",),
        )
        self.assertEqual(first.selected_agent.identifier, "preferred")
        self.assertEqual(first.score, second.score)
        self.assertGreater(first.score, 0)

    def test_explicit_user_preference_beats_configured_priority_and_history(self):
        router = AgentRouter(resolver=self.resolver)
        decision = router.route(
            role="implementation",
            task_description="Implement",
            required_capabilities=("implementation",),
            available_agents=self.profiles,
            user_preferences=("preferred",),
            historical_performance={"fallback": {"success_rate": 1.0}, "preferred": {"success_rate": 0.1}},
        )
        self.assertEqual(decision.selected_agent.identifier, "preferred")
        self.assertIn("preferred agent", " ".join(decision.reasons).lower())

    def test_unavailable_preferred_agent_falls_back(self):
        unavailable = _detected(_config("preferred", ("implementation",)), available=False)
        profiles = _profiles(unavailable, self.fallback)
        decision = AgentRouter(resolver=self.resolver).route(
            role="implementation",
            task_description="Implement",
            required_capabilities=("implementation",),
            available_agents=profiles,
            user_preferences=("preferred", "fallback"),
        )
        self.assertEqual(decision.selected_agent.identifier, "fallback")
        self.assertTrue(any("unavailable" in item.reasons[0].lower() for item in decision.rejected_candidates))

    def test_disabled_agent_is_rejected(self):
        disabled = _detected(_config("disabled", ("implementation",), enabled=False))
        decision = AgentRouter(resolver=self.resolver).route(
            role="implementation",
            task_description="Implement",
            required_capabilities=("implementation",),
            available_agents=_profiles(disabled, self.fallback),
            user_preferences=("disabled", "fallback"),
        )
        self.assertEqual(decision.selected_agent.identifier, "fallback")
        self.assertTrue(any("disabled" in item.reasons[0].lower() for item in decision.rejected_candidates))

    def test_missing_required_capability_is_explainable(self):
        decision = AgentRouter(resolver=self.resolver).route(
            role="implementation",
            task_description="Implement",
            required_capabilities=("security review",),
            available_agents=(self.preferred,),
        )
        self.assertIsNone(decision.selected_agent)
        self.assertEqual(len(decision.rejected_candidates), 1)
        self.assertTrue(any("security review" in reason for reason in decision.rejected_candidates[0].reasons))
        self.assertTrue(decision.constraints)

    def test_decision_exposes_alternatives_reasons_rejections_and_constraints(self):
        decision = AgentRouter(resolver=self.resolver).route(
            role="implementation",
            task_description="Implement",
            required_capabilities=("implementation",),
            available_agents=self.profiles,
            user_preferences=("preferred", "fallback"),
        )
        self.assertIsInstance(decision, RoutingDecision)
        self.assertEqual(decision.selected_identifier, "preferred")
        self.assertTrue(decision.alternatives)
        self.assertTrue(decision.reasons)
        self.assertTrue(decision.rejected_candidates)
        self.assertTrue(decision.constraints)
        payload = decision.to_dict()
        self.assertEqual(payload["selected_agent"], "preferred")
        self.assertIn("reasons", payload)

    def test_routing_can_be_disabled_and_preserves_static_preference_fallback(self):
        decision = AgentRouter(resolver=self.resolver, enabled=False).route(
            role="implementation",
            task_description="Implement",
            required_capabilities=("implementation", "missing"),
            available_agents=self.profiles,
            user_preferences=("preferred", "fallback"),
        )
        self.assertEqual(decision.selected_agent.identifier, "preferred")
        self.assertEqual(decision.routing_mode, "static")
        self.assertTrue(any("routing disabled" in reason.lower() for reason in decision.reasons))

    def test_inferred_task_fit_scores_but_does_not_gate_selection(self):
        plain = _detected(_config("plain", ("implementation",)))
        documented = _detected(_config("documented", ("implementation",), capabilities=("documentation",)))
        plain_profile, documented_profile = _profiles(plain, documented)
        decision = AgentRouter(resolver=self.resolver).route(
            role="implementation",
            task_description="Implement and document the new endpoint",
            available_agents=(plain_profile, documented_profile),
        )
        self.assertEqual(decision.selected_agent.identifier, "documented")
        self.assertIn("task fit: documentation", decision.reasons)
        self.assertFalse(decision.rejected_candidates)

    def test_historical_performance_breaks_priority_ties(self):
        first = _detected(_config("first"))
        second = _detected(_config("second"))
        decision = AgentRouter(resolver=self.resolver).route(
            role="implementation",
            task_description="Implement",
            available_agents=_profiles(first, second),
            historical_performance={"first": 0.0, "second": 1.0},
        )
        self.assertEqual(decision.selected_agent.identifier, "second")

    def test_excluded_candidate_after_previous_attempt(self):
        decision = AgentRouter(resolver=self.resolver).route(
            role="implementation",
            task_description="Implement",
            available_agents=self.profiles,
            user_preferences=("preferred", "fallback"),
            excluded=("preferred",),
        )
        self.assertEqual(decision.selected_agent.identifier, "fallback")
        self.assertTrue(any("excluded" in reason.lower() for reason in decision.rejected_candidates[0].reasons))

    def test_single_profile_is_accepted_without_iteration(self):
        decision = AgentRouter(resolver=self.resolver).route(
            role="implementation",
            task_description="Implement",
            required_capabilities=("implementation",),
            available_agents=self.profiles[0],
            user_preferences=("preferred",),
        )
        self.assertEqual(decision.selected_agent.identifier, "preferred")

    def test_registry_mapping_and_single_string_requirement(self):
        mapping = {profile.identifier: profile for profile in self.profiles}
        disabled_decision = AgentRouter(resolver=self.resolver, enabled=False).route(
            role="implementation",
            task_description="Implement",
            required_capabilities="implementation",
            available_agents=mapping,
            user_preferences=("preferred", "fallback"),
            excluded="preferred",
        )
        self.assertEqual(disabled_decision.selected_agent.identifier, "fallback")
        enabled_decision = AgentRouter(resolver=self.resolver).route(
            role="implementation",
            task_description="Implement",
            required_capabilities="implementation",
            available_agents=mapping,
            user_preferences=("preferred", "fallback"),
        )
        self.assertEqual(enabled_decision.selected_agent.identifier, "preferred")


    @patch("agentops.config._load_data")
    @patch("agentops.registry.subprocess.run")
    @patch("agentops.registry.shutil.which")
    def test_registry_select_accepts_single_string_requirement(self, which, run, load_data):
        which.side_effect = lambda command: f"/bin/{command}"
        run.return_value = MagicMock(stdout="1.0.0\n", stderr="", returncode=0)
        load_data.return_value = {
            "agents": {
                "plain": {"command": "plain", "roles": ["implementation"]},
                "rich": {"command": "rich", "roles": ["implementation"], "capabilities": ["mcp"]},
            },
            "role_preferences": {"implementation": ["plain", "rich"]},
        }
        registry = AgentRegistry(load_config())
        selected = registry.select("implementation", required_capabilities="mcp")
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.config.name, "rich")

    @patch("agentops.config._load_data")
    @patch("agentops.registry.subprocess.run")
    @patch("agentops.registry.shutil.which")
    def test_registry_select_matches_role_derived_task_capabilities(self, which, run, load_data):
        which.side_effect = lambda command: f"/bin/{command}"
        run.return_value = MagicMock(stdout="1.0.0\n", stderr="", returncode=0)
        load_data.return_value = {
            "agents": {
                "coder": {"command": "coder", "roles": ["implementation"]},
                "reviewer": {"command": "reviewer", "roles": ["review"]},
            },
            "role_preferences": {"implementation": ["coder", "reviewer"]},
        }
        registry = AgentRegistry(load_config())
        selected = registry.select("implementation", required_capabilities=("implementation",))
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.config.name, "coder")
        self.assertIsNone(registry.select("implementation", required_capabilities=("security review",)))


class ConfigRoutingTests(unittest.TestCase):
    @patch("agentops.config._load_data")
    def test_loads_profile_metadata_and_routing_switch(self, load_data):
        load_data.return_value = {
            "agents": {
                "pi": {
                    "command": "pi",
                    "display_name": "Pi",
                    "priority": 3,
                    "metadata": {"owner": "platform"},
                    "version_command": ["pi", "--version"],
                    "structured_output": True,
                },
            },
            "role_preferences": {"implementation": ["pi"]},
            "runtime": {"routing": {"enabled": False}},
        }
        config = load_config()
        self.assertEqual(config.agents["pi"].display_name, "Pi")
        self.assertEqual(config.agents["pi"].priority, 3)
        self.assertEqual(config.agents["pi"].metadata, {"owner": "platform"})
        self.assertEqual(config.agents["pi"].version_command, ("pi", "--version"))
        self.assertTrue(config.agents["pi"].structured_output)
        self.assertFalse(config.routing_enabled)

    @patch("agentops.config._load_data")
    def test_rejects_non_integer_priority(self, load_data):
        load_data.return_value = {"agents": {"bad": {"command": "bad", "priority": 1.5}}}
        with self.assertRaises(ValueError):
            load_config()


class RegistryProfileTests(unittest.TestCase):
    @patch("agentops.registry.shutil.which")
    @patch("agentops.registry.subprocess.run")
    def test_non_ascii_version_output_never_breaks_detection(self, run, which):
        which.return_value = "/bin/pi"
        run.side_effect = UnicodeDecodeError("cp1252", b"\xff", 0, 1, "invalid start byte")
        registry = AgentRegistry(AppConfig(
            {"pi": _config("pi", ("implementation",))},
            {"implementation": ("pi",)},
        ))
        detected = registry.detect()["pi"]
        self.assertTrue(detected.available)
        self.assertEqual(detected.executable, "/bin/pi")
        self.assertIsNone(detected.version)

    @patch("agentops.registry.shutil.which")
    @patch("agentops.registry.subprocess.run")
    def test_registry_builds_profiles_for_installed_agents(self, run, which):
        which.return_value = "/bin/pi"
        run.return_value = MagicMock(stdout="pi 9.9.9\n", stderr="")
        config = AppConfig(
            {"pi": _config("pi", ("implementation",))},
            {"implementation": ("pi",)},
        )
        registry = AgentRegistry(config)
        profiles = registry.profiles()
        self.assertEqual(profiles["pi"].identifier, "pi")
        self.assertEqual(profiles["pi"].detected_version, "pi 9.9.9")
        self.assertIn("implementation", profiles["pi"].capabilities)


class WorkflowRoutingTests(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(
            {
                "first": _config("first", ("architecture", "implementation", "review", "debugging")),
                "second": _config("second", ("architecture", "implementation", "review", "debugging")),
            },
            {
                role: ("first", "second")
                for role in ("architecture", "implementation", "review", "debugging")
            },
            (("test",),),
            max_attempts=1,
            concurrency=1,
        )
        self.state = StateStore(":memory:")
        self.registry = AgentRegistry(self.config)
        self.runner = MagicMock()

        async def run_agent(agent, prompt, directory, task_id, cancel_event=None):
            # Review tasks must satisfy the structured verdict contract
            # (Issue #12); every other role answers with plain text.
            role = self.state.get_task(task_id).role
            review_verdict = (
                '{"schema_version":1,"verdict":"approve","blocking":[],'
                '"non_blocking":[],"confidence":0.99}'
            )
            task_text = review_verdict if role == "review" else "done"
            return RunResult(agent.config.name, ("fake",), 0, task_text, "", 0.01, False, Path(f"{task_id}.log"))

        self.runner.run_agent = run_agent
        self.verifier = MagicMock()

    def tearDown(self):
        self.state.close()

    @patch("agentops.registry.shutil.which")
    @patch("agentops.registry.subprocess.run")
    def test_workflow_persists_routing_decision_event(self, run, which):
        which.side_effect = lambda command: f"/bin/{command}"
        run.return_value = MagicMock(stdout="1.0.0\n", stderr="")
        passed = MagicMock(succeeded=True, output="tests passed")
        self.verifier.run = MagicMock(return_value=asyncio.sleep(0, result=[passed]))
        engine = WorkflowEngine(self.config, self.state, self.registry, self.runner, self.verifier)
        result = asyncio.run(engine.run_high_level("Add routing", Path.cwd()))
        self.assertTrue(result.ready)
        events = self.state.query_events(event_type=EventType.ROUTING_DECISION)
        self.assertTrue(events)
        payload = events[0].payload
        self.assertEqual(payload["selected_agent"], "first")
        self.assertIn("reasons", payload)

    @patch("agentops.registry.shutil.which")
    @patch("agentops.registry.subprocess.run")
    def test_workflow_inspects_repository_off_the_event_loop_thread(self, run, which):
        import threading

        which.side_effect = lambda command: f"/bin/{command}"
        run.return_value = MagicMock(stdout="1.0.0\n", stderr="")
        passed = MagicMock(succeeded=True, output="tests passed")
        self.verifier.run = MagicMock(return_value=asyncio.sleep(0, result=[passed]))
        engine = WorkflowEngine(self.config, self.state, self.registry, self.runner, self.verifier)
        real_flags = engine.repo_inspector.routing_flags
        threads: list[threading.Thread] = []

        def recording_flags(source):
            threads.append(threading.current_thread())
            return real_flags(source)

        engine.repo_inspector.routing_flags = recording_flags
        loop_thread = threading.current_thread()
        result = asyncio.run(engine.run_high_level("Add routing", Path.cwd()))
        self.assertTrue(result.ready)
        self.assertTrue(threads, "workflow selection must inspect the repository")
        self.assertTrue(all(thread is not loop_thread for thread in threads), threads)

    @patch("agentops.registry.shutil.which")
    @patch("agentops.registry.subprocess.run")
    def test_workflow_routes_when_repository_characteristics_raises(self, run, which):
        # Before inspection moved to asyncio.to_thread, a raising
        # _repository_characteristics was caught by _select_agent's router
        # guard; it must still never fail the task.
        which.side_effect = lambda command: f"/bin/{command}"
        run.return_value = MagicMock(stdout="1.0.0\n", stderr="")
        passed = MagicMock(succeeded=True, output="tests passed")
        self.verifier.run = MagicMock(side_effect=lambda *a, **k: asyncio.sleep(0, result=[passed]))
        engine = WorkflowEngine(self.config, self.state, self.registry, self.runner, self.verifier)
        engine._repository_characteristics = MagicMock(side_effect=RuntimeError("inspection exploded"))
        result = asyncio.run(engine.run_high_level("Add routing", Path.cwd()))
        self.assertTrue(result.ready)

    def test_stale_registry_mapping_records_execution_fallback(self):
        workflow_id = self.state.create_workflow("stale mapping")
        task = self.state.add_task(Task("Implement", "implementation", workflow_id, max_attempts=1))
        preferred_detected = _detected(_config("preferred", ("implementation",)))
        fallback_detected = _detected(_config("fallback", ("implementation",)))
        preferred, fallback = _profiles(preferred_detected, fallback_detected)
        registry = MagicMock()
        registry.profiles.return_value = {"preferred": preferred, "fallback": fallback}
        registry.get.side_effect = KeyError("preferred")
        registry.select.return_value = fallback_detected
        self.config = AppConfig(
            self.config.agents,
            {**self.config.role_preferences, "implementation": ("preferred", "fallback")},
            self.config.verification_commands,
            max_attempts=1,
            concurrency=1,
        )
        engine = WorkflowEngine(self.config, self.state, registry, self.runner, self.verifier)
        selected = engine._select_agent(task, set())
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.config.name, "fallback")
        events = self.state.query_events(event_type=EventType.ROUTING_DECISION)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["selected_agent"], "preferred")
        self.assertEqual(events[0].payload["executed_agent"], "fallback")

    @patch("agentops.registry.shutil.which")
    @patch("agentops.registry.subprocess.run")
    def test_disabled_workflow_uses_static_selection_without_routing_event(self, run, which):
        which.side_effect = lambda command: f"/bin/{command}"
        run.return_value = MagicMock(stdout="1.0.0\n", stderr="")
        self.config = AppConfig(
            self.config.agents,
            self.config.role_preferences,
            self.config.verification_commands,
            max_attempts=1,
            concurrency=1,
            routing_enabled=False,
        )
        passed = MagicMock(succeeded=True, output="tests passed")
        self.verifier.run = MagicMock(return_value=asyncio.sleep(0, result=[passed]))
        engine = WorkflowEngine(self.config, self.state, self.registry, self.runner, self.verifier)
        self.assertIsNone(engine.router)
        result = asyncio.run(engine.run_high_level("Add routing", Path.cwd()))
        self.assertTrue(result.ready)
        self.assertEqual(self.state.query_events(event_type=EventType.ROUTING_DECISION), [])


class RepositoryCharacteristicRoutingTests(unittest.TestCase):
    """Repository characteristic flags are real scoring input, not dead kwargs.

    ``workflow.py`` used to pass ``repository_characteristics={}`` forever, so
    the mapping ``AgentCapabilityResolver.required_capabilities`` reads was
    always empty.  These tests pin the resolved behavior end to end.
    """

    def setUp(self):
        self.resolver = AgentCapabilityResolver()
        # Equal on role fit, priority, and history, so the identifier
        # tie-breaker alone decides when no repo signal applies.
        plain = _detected(_config("plain", ("architecture",)))
        tester = _detected(_config("tester", ("architecture", "implementation")))
        self.profiles = _profiles(plain, tester)

    def _route(self, **overrides):
        kwargs = dict(
            role="architecture",
            task_description="Design the module layout",
            available_agents=self.profiles,
            repository_characteristics={},
        )
        kwargs.update(overrides)
        return AgentRouter(resolver=self.resolver).route(**kwargs)

    def test_test_runner_signal_changes_the_selection(self):
        without_signal = self._route()
        with_signal = self._route(repository_characteristics={"requires_tests": True})
        # Without a signal both candidates score identically and the stable
        # identifier tie-break picks "plain".
        self.assertEqual(without_signal.selected_agent.identifier, "plain")
        self.assertEqual("tester", with_signal.selected_agent.identifier)
        self.assertIn("task fit: testing", with_signal.reasons)
        self.assertNotIn("task fit: testing", without_signal.reasons)

    def test_exploration_signal_is_a_reasoned_change_for_large_repos(self):
        decision = self._route(repository_characteristics={"repository_exploration_required": True})
        self.assertIn("task fit: repository exploration", decision.reasons)
        self.assertEqual("tester", decision.selected_agent.identifier)

    def test_explicit_user_preference_beats_derived_signals(self):
        decision = self._route(
            repository_characteristics={"requires_tests": True, "repository_exploration_required": True},
            user_preferences=("plain",),
        )
        self.assertEqual("plain", decision.selected_agent.identifier)

    def test_required_capabilities_stay_hard_gates_despite_repo_signals(self):
        decision = self._route(
            required_capabilities=("security review",),
            repository_characteristics={"requires_tests": True},
        )
        self.assertIsNone(decision.selected_agent)
        self.assertEqual(
            {item.identifier for item in decision.rejected_candidates},
            {"plain", "tester"},
            "repo signals must not resurrect candidates a hard gate rejects",
        )
        self.assertTrue(
            any(
                "missing required capabilities" in " ".join(item.reasons).lower()
                for item in decision.rejected_candidates
            )
        )

    def test_disabled_router_static_path_ignores_characteristics(self):
        decision = AgentRouter(resolver=self.resolver, enabled=False).route(
            role="architecture",
            task_description="Design",
            repository_characteristics={"requires_tests": True},
            available_agents=self.profiles,
            user_preferences=("tester", "plain"),
        )
        self.assertEqual(decision.routing_mode, "static")
        self.assertEqual("tester", decision.selected_agent.identifier)
        self.assertTrue(any("routing disabled" in reason.lower() for reason in decision.reasons))

    def test_repo_characteristics_bridge_into_resolver_requirements(self):
        from agentops.repo_inspection import RepoCharacteristics

        required = self.resolver.required_capabilities(
            "architecture", "Design the module layout",
            RepoCharacteristics(test_runner="pytest").routing_flags(),
        )
        self.assertIn("testing", required)
        self.assertEqual(required, tuple(sorted(required)))


class WorkflowRepositoryCharacteristicTests(unittest.TestCase):
    """The engine derives flags from the working directory per selection."""

    def setUp(self):
        self.config = AppConfig(
            {"first": _config("first", ("implementation",))},
            {"implementation": ("first",)},
            max_attempts=1,
            concurrency=1,
        )
        self.state = StateStore(":memory:")
        self.addCleanup(self.state.close)
        self.runner = MagicMock()
        self.verifier = MagicMock()

    def _engine(self):
        detected = _detected(_config("first", ("implementation",)))
        profile = _profiles(detected)[0]
        registry = MagicMock()
        registry.profiles.return_value = {"first": profile}
        registry.get.return_value = detected
        spy_router = AgentRouter()
        real_route = spy_router.route
        calls: list[dict] = []

        def spy_route(**kwargs):
            calls.append(kwargs)
            return real_route(**kwargs)

        spy_router.route = spy_route
        engine = WorkflowEngine(
            self.config, self.state, registry, self.runner, self.verifier,
            router=spy_router,
        )
        return engine, calls

    def _implementation_task(self) -> Task:
        workflow_id = self.state.create_workflow("characteristics")
        return self.state.add_task(Task("Implement", "implementation", workflow_id, max_attempts=1))

    def test_engine_passes_derived_flags_for_the_working_directory(self):
        with TemporaryDirectory() as directory:
            (Path(directory) / "conftest.py").write_text("", encoding="utf-8")
            engine, calls = self._engine()
            selected = engine._select_agent(self._implementation_task(), set(), Path(directory))
        self.assertIsNotNone(selected)
        assert calls, "the engine must route through the spy"
        flags = calls[0]["repository_characteristics"]
        self.assertTrue(flags["requires_tests"])
        self.assertFalse(flags["security_sensitive"])

    def test_engine_without_working_directory_keeps_empty_characteristics(self):
        engine, calls = self._engine()
        engine._select_agent(self._implementation_task(), set())
        self.assertEqual(calls[0]["repository_characteristics"], {})

    def test_precomputed_characteristics_are_used_without_rescanning(self):
        engine, calls = self._engine()
        precomputed = {"requires_tests": True, "repository_exploration_required": False,
                       "security_sensitive": False, "documentation_required": False}
        with patch.object(
            engine.repo_inspector, "routing_flags",
            side_effect=AssertionError("must not rescan"),
        ):
            engine._select_agent(self._implementation_task(), set(), Path.cwd(), precomputed)
        self.assertEqual(calls[0]["repository_characteristics"], precomputed)

    def test_inspection_failure_degrades_to_empty_characteristics(self):
        engine, calls = self._engine()
        with patch.object(
            engine.repo_inspector, "routing_flags",
            side_effect=RuntimeError("inspector exploded"),
        ):
            selected = engine._select_agent(self._implementation_task(), set(), Path.cwd())
        self.assertIsNotNone(selected, "selection survives an inspection failure")
        self.assertEqual(calls[0]["repository_characteristics"], {})


class AgentHealthTests(unittest.TestCase):
    """`ONLINE` used to mean only "shutil.which found the binary", which reported
    agents as selectable that could not run: codex answered --version while its
    quota was exhausted until 2026-10-12, and fcc-claude answered while its proxy
    on 127.0.0.1:8082 was dead. Both were dispatched to; both failed at runtime.
    """

    @patch("agentops.registry.subprocess.run")
    @patch("agentops.registry.shutil.which")
    def test_healthy_probe_is_available(self, which, run):
        which.return_value = "/bin/ok"
        run.return_value = MagicMock(stdout="1.0.0\n", stderr="", returncode=0)
        agent = AgentRegistry(_registry_with("ok")).detect()["ok"]
        self.assertIs(agent.health, AgentHealth.AVAILABLE)
        self.assertEqual(agent.status, "ONLINE")
        self.assertTrue(agent.available)

    @patch("agentops.registry.subprocess.run")
    @patch("agentops.registry.shutil.which")
    def test_nonzero_probe_is_unhealthy_and_not_selectable(self, which, run):
        which.return_value = "/bin/dead"
        run.return_value = MagicMock(stdout="", stderr="proxy unreachable", returncode=1)
        registry = AgentRegistry(_registry_with("dead"))
        agent = registry.detect()["dead"]
        self.assertIs(agent.health, AgentHealth.UNHEALTHY)
        self.assertEqual(agent.status, "UNHEALTHY")
        self.assertFalse(agent.available, "an unhealthy agent must not be selectable")
        self.assertIsNone(registry.select("implementation"))
        self.assertIn("exited 1", agent.health_detail)

    @patch("agentops.registry.shutil.which")
    def test_missing_binary_is_discovered_not_offline(self, which):
        which.return_value = None
        agent = AgentRegistry(_registry_with("gone")).detect()["gone"]
        self.assertIs(agent.health, AgentHealth.DISCOVERED)
        self.assertEqual(agent.status, "MISSING")
        self.assertFalse(agent.available)

    @patch("agentops.registry.shutil.which")
    def test_disabled_agent_is_blocked(self, which):
        which.return_value = "/bin/off"
        config = AppConfig({"off": _config("off", ("implementation",), enabled=False)},
                           {"implementation": ("off",)})
        agent = AgentRegistry(config).detect()["off"]
        self.assertIs(agent.health, AgentHealth.BLOCKED)
        self.assertEqual(agent.status, "DISABLED")


if __name__ == "__main__":
    unittest.main()
