"""A2 contract tests: adapters wrap AgentConfig without changing behavior."""

import unittest

from agentops.agent_adapter import (
    AgentAdapter,
    Capability,
    CliAdapter,
    adapter_for,
    capabilities_for_roles,
)
from agentops.config import AgentConfig


def _config(name="a", roles=("implementation",), args=("{prompt}",), **overrides) -> AgentConfig:
    values = {
        "name": name, "command": name, "args": tuple(args), "roles": tuple(roles),
    }
    values.update(overrides)
    return AgentConfig(**values)


class DerivationHonestyTests(unittest.TestCase):
    def test_roles_derive_baseline_only(self):
        self.assertEqual(capabilities_for_roles(("implementation",)), frozenset({Capability.CODING}))
        self.assertEqual(capabilities_for_roles(("debugging",)), frozenset({Capability.CODING}))
        self.assertEqual(capabilities_for_roles(("architecture",)), frozenset({Capability.PLANNING}))
        self.assertEqual(capabilities_for_roles(("review",)), frozenset({Capability.REVIEW}))
        self.assertEqual(
            capabilities_for_roles(("implementation", "architecture", "review")),
            frozenset({Capability.CODING, Capability.PLANNING, Capability.REVIEW}))

    def test_empty_roles_derive_all_three(self):
        # Mirrors the registry's empty-means-all gate; nothing fabricated.
        self.assertEqual(
            capabilities_for_roles(()),
            frozenset({Capability.CODING, Capability.PLANNING, Capability.REVIEW}))

    def test_no_undeclared_advanced_capabilities(self):
        adapter = adapter_for(_config())
        for advanced in (Capability.STRUCTURED_OUTPUT, Capability.STREAMING, Capability.MCP,
                         Capability.MODEL_SELECTION, Capability.READ_ONLY,
                         Capability.NON_INTERACTIVE):
            self.assertNotIn(advanced, adapter.capabilities())

    def test_roles_derive_task_capabilities(self):
        implementation = adapter_for(_config(roles=("implementation",)))
        for capability in (Capability.IMPLEMENTATION, Capability.REFACTORING, Capability.TESTING,
                           Capability.REPOSITORY_EXPLORATION):
            self.assertIn(capability, implementation.capabilities())
        reviewer = adapter_for(_config(roles=("review",)))
        for capability in (Capability.CODE_REVIEW, Capability.SECURITY_REVIEW, Capability.DOCUMENTATION):
            self.assertIn(capability, reviewer.capabilities())

    def test_declared_extras_apply(self):
        adapter = adapter_for(_config(), )
        self.assertEqual(adapter.extra_capabilities, ())
        adapter = CliAdapter(_config(), None, ("streaming", "telepathy"))
        self.assertIn(Capability.STREAMING, adapter.capabilities())
        self.assertEqual(adapter.extra_capabilities, ("telepathy",))

    def test_adapter_for_applies_config_capabilities(self):
        adapter = adapter_for(_config(roles=(), **{"capabilities": ("mcp",)}))
        self.assertIn(Capability.MCP, adapter.capabilities())


class SupportsGateTests(unittest.TestCase):
    def test_role_gate_matches_registry_semantics(self):
        adapter = adapter_for(_config(roles=("implementation", "review")))
        self.assertTrue(adapter.supports("implementation"))
        self.assertTrue(adapter.supports("review"))
        self.assertFalse(adapter.supports("architecture"))

    def test_empty_roles_support_everything(self):
        self.assertTrue(adapter_for(_config(roles=())).supports("anything"))

    def test_matches_enum_and_string(self):
        adapter = adapter_for(_config(roles=("implementation",)))
        self.assertTrue(adapter.matches(Capability.CODING))
        self.assertTrue(adapter.matches("coding"))
        self.assertFalse(adapter.matches(Capability.MCP))
        self.assertFalse(adapter.matches("telepathy"))
        extra = CliAdapter(_config(roles=()), None, ("telepathy",))
        self.assertTrue(extra.matches("telepathy"))

    def test_structural_protocol(self):
        self.assertIsInstance(adapter_for(_config()), AgentAdapter)


class BuildCommandParityTests(unittest.TestCase):
    def test_substitutes_prompt_and_prefers_executable(self):
        adapter = adapter_for(_config(name="pi", args=("--prompt", "{prompt}")), "/bin/pi")
        self.assertEqual(adapter.build_command("hi"), ("/bin/pi", "--prompt", "hi"))

    def test_falls_back_to_command_name(self):
        adapter = adapter_for(_config(name="ag", args=("{prompt}",)))
        self.assertEqual(adapter.build_command("hi"), ("ag", "hi"))

    def test_missing_placeholder_passes_through(self):
        adapter = adapter_for(_config(args=("--flag",)))
        self.assertEqual(adapter.build_command("hi"), ("a", "--flag"))

    def test_absolute_command_preserved(self):
        adapter = adapter_for(_config(command="/opt/x", args=("{prompt}",)))
        self.assertEqual(adapter.build_command("hi"), ("/opt/x", "hi"))

    def test_name_property(self):
        self.assertEqual(adapter_for(_config(name="zed")).name, "zed")


if __name__ == "__main__":
    unittest.main()
