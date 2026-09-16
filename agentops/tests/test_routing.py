"""Capability profiles and deterministic agent routing."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentops.agent_adapter import adapter_for
from agentops.config import AgentConfig, AppConfig, load_config
from agentops.events import EventType
from agentops.registry import AgentRegistry, DetectedAgent
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
    @patch("agentops.registry.shutil.which")
    def test_registry_select_accepts_single_string_requirement(self, which, load_data):
        which.side_effect = lambda command: f"/bin/{command}"
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
    @patch("agentops.registry.shutil.which")
    def test_registry_select_matches_role_derived_task_capabilities(self, which, load_data):
        which.side_effect = lambda command: f"/bin/{command}"
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
            return RunResult(agent.config.name, ("fake",), 0, "done", "", 0.01, False, Path(f"{task_id}.log"))

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


if __name__ == "__main__":
    unittest.main()
