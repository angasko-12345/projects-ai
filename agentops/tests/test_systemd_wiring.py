"""runtime.systemd_run_enabled must reach every process owner the app builds.

The library default is off, so a front end that forgets to pass the configured
value silently drops cgroup isolation. These tests pin the wiring for the GUI
controller and the CLI, which build their runners independently.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentops.cli import CliServices, main as cli_main
from agentops.config import AgentConfig, AppConfig
from agentops.gui_controller import AgentOpsController


def _config(enabled: bool) -> AppConfig:
    roles = ("architecture", "implementation", "review", "debugging")
    config = AppConfig({"fb": AgentConfig("fb", "fake", ("{prompt}",), roles)},
                       {role: ("fb",) for role in roles}, ())
    return replace(config, systemd_run_enabled=enabled)


class GuiControllerWiringTests(unittest.TestCase):
    def _controller(self, enabled: bool, root: Path) -> AgentOpsController:
        controller = AgentOpsController()
        controller._load_config = lambda: _config(enabled)
        controller._state_path = lambda _root: root / "state.sqlite"
        return controller

    def test_agent_run_passes_configured_value(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                controller = self._controller(enabled, root)
                with patch("agentops.gui_controller.AgentRegistry"), \
                        patch("agentops.gui_controller.AgentRunner") as runner_cls, \
                        patch("agentops.gui_controller.asyncio.run",
                              side_effect=RuntimeError("stop after wiring")):
                    controller._run_agent_operation("fb", "prompt", root,
                                                    threading.Event(), lambda _e: None)
                self.assertEqual(runner_cls.call_args.kwargs["systemd_run_enabled"], enabled)

    def test_task_run_passes_configured_value_to_every_owner(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                controller = self._controller(enabled, root)
                with patch("agentops.gui_controller.AgentRunner") as runner_cls, \
                        patch("agentops.gui_controller.Verifier") as verifier_cls, \
                        patch("agentops.gui_controller.VerificationKernel") as kernel_cls, \
                        patch("agentops.gui_controller.WorkflowEngine"), \
                        patch("agentops.gui_controller.GitWorktreeManager") as manager_cls:
                    manager_cls.return_value.create.side_effect = RuntimeError("stop after wiring")
                    controller._run_task_operation("task", root, threading.Event(),
                                                   lambda _e: None)
                for factory in (runner_cls, verifier_cls, kernel_cls):
                    self.assertEqual(factory.call_args.kwargs["systemd_run_enabled"], enabled,
                                     factory)


class CliWiringTests(unittest.TestCase):
    def test_task_command_passes_configured_value_to_every_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            runner, verifier, kernel = MagicMock(), MagicMock(), MagicMock()
            manager = MagicMock()
            manager.return_value.create.side_effect = RuntimeError("stop after wiring")
            services = CliServices(
                config=_config(True), state_store=MagicMock(), log_manager=MagicMock(),
                agent_registry=MagicMock(), agent_runner=runner, verifier=verifier,
                verification_kernel=kernel, workflow_engine=MagicMock(),
                worktree_manager=manager)
            with patch("agentops.cli.Path.cwd", return_value=Path(directory)):
                try:
                    cli_main(["task", "do it", "--cwd", directory], services=services)
                except RuntimeError:
                    pass
            for factory in (runner, verifier, kernel):
                self.assertTrue(factory.called, factory)
                self.assertIs(factory.call_args.kwargs["systemd_run_enabled"], True)


if __name__ == "__main__":
    unittest.main()
