"""Regression tests for bundled-resource resolution.

The frozen executable unpacks its data files into ``sys._MEIPASS``. Nothing
that reads packaged data may fall back to the current working directory, or
``AgentOps.exe`` breaks the moment it is launched from somewhere else.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentops.config import DEFAULT_CONFIG_PATH
from agentops.gui.tokens import ASSET_DIR
from agentops.paths import bundle_root, resource_path


class BundleRootTests(unittest.TestCase):
    def test_source_checkout_uses_package_parent(self):
        with mock.patch.object(sys, "_MEIPASS", None, create=True):
            with mock.patch.object(sys, "frozen", False, create=True):
                self.assertEqual(
                    bundle_root(),
                    Path(__file__).resolve().parent.parent,
                )

    def test_frozen_build_uses_meipass(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(sys, "_MEIPASS", temp, create=True):
                self.assertEqual(bundle_root(), Path(temp))
                self.assertEqual(
                    resource_path("agents", "agents.yaml"),
                    Path(temp) / "agents" / "agents.yaml",
                )

    def test_ignores_current_working_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(sys, "_MEIPASS", temp, create=True):
                first = resource_path("agents", "agents.yaml")
                original = os.getcwd()
                try:
                    os.chdir(temp)
                    self.assertEqual(first, resource_path("agents", "agents.yaml"))
                finally:
                    os.chdir(original)

    def test_empty_meipass_falls_back_to_package(self):
        with mock.patch.object(sys, "_MEIPASS", "", create=True):
            self.assertNotEqual(bundle_root(), Path(""))


class BundledResourceTests(unittest.TestCase):
    def test_default_config_exists_and_is_outside_cwd(self):
        self.assertTrue(DEFAULT_CONFIG_PATH.is_file(), DEFAULT_CONFIG_PATH)
        self.assertEqual(DEFAULT_CONFIG_PATH.name, "agents.yaml")
        self.assertNotEqual(Path(os.path.abspath(os.getcwd())), DEFAULT_CONFIG_PATH.parent)

    def test_gui_asset_exists(self):
        self.assertTrue((ASSET_DIR / "chevron-down.svg").is_file(), ASSET_DIR)

    def test_both_resources_live_under_the_bundle_root(self):
        root = bundle_root()
        # ASSET_DIR is at agentops/gui/assets under the package root
        self.assertEqual(ASSET_DIR.parent.parent.parent, root)
        # DEFAULT_CONFIG_PATH is at agents/agents.yaml under the package root
        self.assertEqual(DEFAULT_CONFIG_PATH.parent.parent, root / "agentops")


if __name__ == "__main__":
    unittest.main()