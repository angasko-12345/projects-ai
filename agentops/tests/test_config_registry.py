import unittest
from unittest.mock import patch

from agentops.config import load_config
from agentops.registry import AgentRegistry


class ConfigAndRegistryTests(unittest.TestCase):
    def test_loads_default_config_with_claude_variants(self):
        config = load_config()
        self.assertIn("claude", config.agents)
        self.assertIn("fcc-claude", config.agents)

    @patch("agentops.registry.shutil.which")
    def test_detects_installed_agents_and_falls_back_by_role(self, which):
        which.side_effect = lambda command: "C:/bin/opencode" if command == "opencode" else None
        registry = AgentRegistry(load_config())
        self.assertTrue(registry.detect()["opencode"].available)
        self.assertEqual(registry.select("implementation").config.name, "opencode")

    @patch("agentops.config._load_data")
    def test_rejects_invalid_configuration(self, load_data):
        load_data.return_value = {"agents": {"bad": {"command": "x", "args": "nope"}}}
        with self.assertRaises(ValueError):
            load_config()

    @patch("agentops.config._load_data")
    def test_agent_enabled_flag(self, load_data):
        load_data.return_value = {"agents": {
            "on": {"command": "on"},
            "off": {"command": "off", "enabled": False},
        }}
        config = load_config()
        self.assertTrue(config.agents["on"].enabled)
        self.assertFalse(config.agents["off"].enabled)
        load_data.return_value = {"agents": {"bad": {"command": "x", "enabled": "yes"}}}
        with self.assertRaises(ValueError):
            load_config()

    @patch("agentops.config._load_data")
    def test_agent_capabilities_parsed_and_default_empty(self, load_data):
        load_data.return_value = {"agents": {
            "a": {"command": "a", "capabilities": ["coding", " streaming ", "coding"]},
            "b": {"command": "b"},
        }}
        config = load_config()
        self.assertEqual(config.agents["a"].capabilities, ("coding", "streaming"))
        self.assertEqual(config.agents["b"].capabilities, ())

    @patch("agentops.config._load_data")
    def test_agent_capabilities_reject_non_strings(self, load_data):
        load_data.return_value = {"agents": {"bad": {"command": "x", "capabilities": "coding"}}}
        with self.assertRaises(ValueError):
            load_config()
        load_data.return_value = {"agents": {"bad": {"command": "x", "capabilities": ["coding", 42]}}}
        with self.assertRaises(ValueError):
            load_config()
        load_data.return_value = {"agents": {"bad": {"command": "x", "capabilities": [""]}}}
        with self.assertRaises(ValueError):
            load_config()

    @patch("agentops.config._load_data")
    def test_unknown_capability_warns_but_passes_through(self, load_data):
        load_data.return_value = {"agents": {"a": {"command": "a", "capabilities": ["telepathy"]}}}
        with self.assertWarns(UserWarning):
            config = load_config()
        self.assertEqual(config.agents["a"].capabilities, ("telepathy",))

    @patch("agentops.registry.shutil.which")
    def test_select_without_requirements_ignores_capabilities(self, which):
        which.side_effect = lambda command: f"C:/bin/{command}"
        registry = AgentRegistry(load_config())
        selected = registry.select("implementation")
        self.assertIsNotNone(selected)
        # Same result with an empty requirement tuple (legacy path).
        self.assertEqual(registry.select("implementation", required_capabilities=()), selected)

    @patch("agentops.registry.shutil.which")
    def test_select_filters_by_required_capabilities(self, which):
        which.side_effect = lambda command: f"C:/bin/{command}"
        registry = AgentRegistry(load_config())
        selected = registry.select("implementation", required_capabilities=("coding",))
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertIn("implementation", selected.config.roles)
        self.assertIsNone(registry.select("implementation", required_capabilities=("mcp",)))
        self.assertIsNone(registry.select("implementation", required_capabilities=("telepathy",)))

    @patch("agentops.config._load_data")
    @patch("agentops.registry.shutil.which")
    def test_declared_capability_unlocks_selection(self, which, load_data):
        which.side_effect = lambda command: f"C:/bin/{command}"
        load_data.return_value = {"agents": {
            "plain": {"command": "plain", "roles": ["implementation"]},
            "rich": {"command": "rich", "roles": ["implementation"],
                       "capabilities": ["mcp"]},
        }, "role_preferences": {"implementation": ["plain", "rich"]}}
        registry = AgentRegistry(load_config())
        selected = registry.select("implementation", required_capabilities=("mcp",))
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.config.name, "rich")

    @patch("agentops.registry.shutil.which")
    def test_adapter_accessor_and_unknown_name(self, which):
        which.side_effect = lambda command: f"C:/bin/{command}"
        registry = AgentRegistry(load_config())
        adapter = registry.adapter("opencode")
        self.assertEqual(adapter.name, "opencode")
        self.assertTrue(adapter.supports("implementation"))
        with self.assertRaises(KeyError):
            registry.adapter("no-such-agent")

    @patch("agentops.registry.shutil.which")
    def test_disabled_agents_are_never_selected(self, which):
        which.side_effect = lambda command: f"C:/bin/{command}"
        registry = AgentRegistry(load_config())
        detected = registry.detect()
        self.assertFalse(detected["claude"].available)
        self.assertIsNone(detected["claude"].executable)
        self.assertNotIn("claude", [call.args[0] for call in which.mock_calls])
        for role in ("architecture", "implementation", "debugging", "review"):
            selected = registry.select(role)
            if selected is not None:
                self.assertNotEqual(selected.config.name, "claude")
