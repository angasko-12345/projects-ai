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
