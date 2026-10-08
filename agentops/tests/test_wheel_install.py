"""Regression test for wheel install: default config must be findable after pip install."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class WheelInstallTests(unittest.TestCase):
    """Test that the default agents.yaml is accessible after wheel installation."""

    @classmethod
    def setUpClass(cls):
        """Build the wheel once for all tests."""
        cls.project_root = Path(__file__).resolve().parent.parent
        cls.dist_dir = cls.project_root / "dist"
        cls.dist_dir.mkdir(exist_ok=True)

        # Build wheel
        result = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(cls.dist_dir), "."],
            cwd=cls.project_root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise unittest.SkipTest(f"Wheel build failed: {result.stderr}")

        # Find the built wheel
        wheels = list(cls.dist_dir.glob("agentops-*.whl"))
        if not wheels:
            raise unittest.SkipTest("No wheel found in dist/")
        cls.wheel_path = wheels[0]

    def test_wheel_contains_agents_yaml(self):
        """Verify the wheel package includes agents/agents.yaml."""
        import zipfile

        with zipfile.ZipFile(self.wheel_path, "r") as zf:
            # Check for agents.yaml in the agentops.agents package
            agent_files = [n for n in zf.namelist() if n.endswith("agents.yaml")]
            self.assertTrue(agent_files, "agents.yaml not found in wheel")
            # Should be at agentops/agents/agents.yaml
            self.assertIn("agentops/agents/agents.yaml", agent_files)

    def test_wheel_contains_gui_assets(self):
        """Verify the wheel package includes GUI assets."""
        import zipfile

        with zipfile.ZipFile(self.wheel_path, "r") as zf:
            asset_files = [n for n in zf.namelist() if n.endswith("chevron-down.svg")]
            self.assertTrue(asset_files, "chevron-down.svg not found in wheel")
            self.assertIn("agentops/gui/assets/chevron-down.svg", asset_files)

    def test_clean_install_loads_default_config(self):
        """Install wheel in a clean venv and verify default config loads."""
        with tempfile.TemporaryDirectory() as tmpdir:
            venv_dir = Path(tmpdir) / "venv"
            # Create virtual environment
            result = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, f"venv creation failed: {result.stderr}")

            # Determine python executable in venv
            if sys.platform == "win32":
                python_exe = venv_dir / "Scripts" / "python.exe"
            else:
                python_exe = venv_dir / "bin" / "python"

            # Install the wheel
            result = subprocess.run(
                [str(python_exe), "-m", "pip", "install", str(self.wheel_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, f"pip install failed: {result.stderr}")

            # Run agentops config loading from outside source tree
            test_script = """
import sys
sys.path.insert(0, '/nonexistent')  # Ensure we don't accidentally use source tree
from agentops.config import load_config
config = load_config()
print('SUCCESS: Config loaded with', len(config.agents), 'agents')
print('Agents:', ', '.join(sorted(config.agents.keys())))
assert 'opencode' in config.agents
assert 'kilo' in config.agents
assert config.systemd_run_enabled is True
"""
            result = subprocess.run(
                [str(python_exe), "-c", test_script],
                cwd="/tmp",  # Run from a different directory
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, f"Config load failed: {result.stderr}")
            self.assertIn("SUCCESS: Config loaded", result.stdout)
            self.assertIn("opencode", result.stdout)
            self.assertIn("kilo", result.stdout)

    def test_agentops_agents_command_works(self):
        """Test that 'agentops agents' CLI command works after install."""
        with tempfile.TemporaryDirectory() as tmpdir:
            venv_dir = Path(tmpdir) / "venv"
            result = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, f"venv creation failed: {result.stderr}")

            if sys.platform == "win32":
                python_exe = venv_dir / "Scripts" / "python.exe"
                agentops_exe = venv_dir / "Scripts" / "agentops.exe"
            else:
                python_exe = venv_dir / "bin" / "python"
                agentops_exe = venv_dir / "bin" / "agentops"

            result = subprocess.run(
                [str(python_exe), "-m", "pip", "install", str(self.wheel_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, f"pip install failed: {result.stderr}")

            # Run 'agentops agents' from outside source tree
            result = subprocess.run(
                [str(agentops_exe), "agents"],
                cwd="/tmp",
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, f"agentops agents failed: {result.stderr}")
            self.assertIn("opencode", result.stdout)
            self.assertIn("kilo", result.stdout)


if __name__ == "__main__":
    unittest.main()