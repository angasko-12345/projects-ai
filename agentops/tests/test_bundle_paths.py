"""Regression tests for bundled-resource resolution.

The frozen executable unpacks its data files into ``sys._MEIPASS``. Nothing
that reads packaged data may fall back to the current working directory, or
``AgentOps.exe`` breaks the moment it is launched from somewhere else.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentops.config import DEFAULT_CONFIG_PATH, _load_data
from agentops.gui.tokens import ASSET_DIR
from agentops.paths import _is_bundled_config, bundle_root, resource_path

try:
    import yaml as _yaml
except ImportError:
    _yaml = None
HAS_YAML = _yaml is not None
_YAML_ERRORS = (
    (OSError, ValueError, ImportError) if _yaml is None else (OSError, ValueError, _yaml.YAMLError)
)


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


class ConfigShadowingTests(unittest.TestCase):
    """A decoy at the preferred filesystem location must not shadow the bundle.

    ``resource_path`` checks the directory next to the ``agentops`` package
    before falling back to ``importlib.resources``. A directory, an empty
    file, or a stale/unparseable file left at ``agents/agents.yaml`` is not a
    bundled config and must not be handed to callers.
    """

    def _resolve(self, populate) -> Path:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "agents" / "agents.yaml"
            target.parent.mkdir()
            populate(target)
            with mock.patch.object(sys, "_MEIPASS", None, create=True):
                with mock.patch("agentops.paths.bundle_root", return_value=Path(temp)):
                    return resource_path("agents", "agents.yaml")

    def _assert_real_bundle(self, resolved: Path) -> None:
        expected = Path(__file__).resolve().parent.parent / "agentops" / "agents" / "agents.yaml"
        self.assertTrue(resolved.is_file(), resolved)
        self.assertEqual(resolved, expected)

    def test_directory_does_not_shadow_bundled_config(self):
        self._assert_real_bundle(self._resolve(lambda target: target.mkdir()))

    def test_empty_file_does_not_shadow_bundled_config(self):
        self._assert_real_bundle(
            self._resolve(lambda target: target.write_text("", encoding="utf-8"))
        )

    def test_stale_unparseable_file_does_not_shadow_bundled_config(self):
        self._assert_real_bundle(
            self._resolve(lambda target: target.write_text('{"agents":', encoding="utf-8"))
        )


class ConfigPreferenceWinTests(unittest.TestCase):
    """A real, parseable ``agents/agents.yaml`` at the preferred filesystem
    location must win over the packaged copy (PR #21 review follow-up)."""

    SENTINEL = json.dumps(
        {"agents": {"sentinel-agent": {"command": "sentinel-cmd", "args": ["{prompt}"]}}}
    )

    def test_parseable_config_wins_over_packaged_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            preferred = Path(temp) / "agents" / "agents.yaml"
            preferred.parent.mkdir()
            preferred.write_text(self.SENTINEL, encoding="utf-8")
            with mock.patch.object(sys, "_MEIPASS", None, create=True):
                with mock.patch("agentops.paths.bundle_root", return_value=Path(temp)):
                    resolved = resource_path("agents", "agents.yaml")
                    self.assertEqual(resolved, preferred)
                    data = _load_data(resolved)
        self.assertIn("sentinel-agent", data["agents"])
        self.assertEqual(data["agents"]["sentinel-agent"]["command"], "sentinel-cmd")


class BundledConfigValidatorContractTests(unittest.TestCase):
    """Validation mirror: ``_is_bundled_config`` must agree with ``_load_data``.

    Contract (config.py ``_load_data``): JSON-valid text always parses;
    conventional YAML parses when PyYAML is installed. Both accept a mapping
    root only. ``resource_path`` uses ``_is_bundled_config`` as its shadowing
    guard, so a disagreement would make ``resource_path`` fall through to the
    packaged copy on exactly the files ``load_config`` would have accepted at
    the preferred path.
    """

    MALFORMED = '{"agents":'
    VALID_JSON = '{"agents": {"a": {"args": ["{prompt}"]}}}'
    CONVENTIONAL_YAML = "agents:\n  a:\n    command: yaml-cmd\n    args:\n      - '{prompt}'\n"

    def _write_case(self, populate) -> Path:
        temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp, ignore_errors=True)
        base = Path(temp) / "config"
        populate(base)
        return base

    def _rejections_agree(self, path: Path) -> None:
        self.assertFalse(_is_bundled_config(path))
        with self.assertRaises(_YAML_ERRORS):
            _load_data(path)

    def _acceptances_agree(self, path: Path, expected_root: dict) -> None:
        self.assertTrue(_is_bundled_config(path))
        self.assertEqual(_load_data(path), expected_root)

    def test_decoy_directory_rejected_by_both(self):
        self._rejections_agree(self._write_case(lambda base: base.mkdir()))

    def test_empty_file_rejected_by_both(self):
        self._rejections_agree(self._write_case(lambda base: base.write_text("", encoding="utf-8")))

    def test_malformed_file_rejected_by_both(self):
        self._rejections_agree(
            self._write_case(lambda base: base.write_text(self.MALFORMED, encoding="utf-8"))
        )

    def test_valid_json_config_accepted_by_both(self):
        self._acceptances_agree(
            self._write_case(lambda base: base.write_text(self.VALID_JSON, encoding="utf-8")),
            json.loads(self.VALID_JSON),
        )

    def test_conventional_yaml_matches_loader_contract(self):
        path = self._write_case(
            lambda base: base.write_text(self.CONVENTIONAL_YAML, encoding="utf-8")
        )
        if HAS_YAML:
            self._acceptances_agree(
                path, {"agents": {"a": {"command": "yaml-cmd", "args": ["{prompt}"]}}}
            )
        else:
            # Without PyYAML the loader's contract is JSON-only; the shadowing
            # guard must reject so the packaged copy is used instead of an
            # error-raising preferred path.
            self._rejections_agree(path)


class BundledResourceTests(unittest.TestCase):
    def test_default_config_exists_and_is_outside_cwd(self):
        self.assertTrue(DEFAULT_CONFIG_PATH.is_file(), DEFAULT_CONFIG_PATH)
        self.assertEqual(DEFAULT_CONFIG_PATH.name, "agents.yaml")
        self.assertNotEqual(Path(os.path.abspath(os.getcwd())), DEFAULT_CONFIG_PATH.parent)

    def test_gui_asset_exists(self):
        self.assertTrue((ASSET_DIR / "chevron-down.svg").is_file(), ASSET_DIR)

    def test_lucide_icon_and_licence_are_bundled_assets(self):
        # The .spec files glob gui/assets/*.svg and *.txt; one icon and the
        # ISC licence prove the Lucide set resolves through the same root.
        self.assertTrue((ASSET_DIR / "lucide-layout-dashboard.svg").is_file(), ASSET_DIR)
        self.assertTrue((ASSET_DIR / "LICENSE-lucide.txt").is_file(), ASSET_DIR)

    def test_both_resources_live_under_the_bundle_root(self):
        root = bundle_root()
        # ASSET_DIR is at agentops/gui/assets under the package root
        self.assertEqual(ASSET_DIR.parent.parent.parent, root)
        # DEFAULT_CONFIG_PATH is at agents/agents.yaml under the package root
        self.assertEqual(DEFAULT_CONFIG_PATH.parent.parent, root / "agentops")


if __name__ == "__main__":
    unittest.main()
