"""Scaffold smoke tests (stdlib unittest only, no third-party imports)."""
import importlib
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestScaffold(unittest.TestCase):
    def test_packages_importable(self):
        for pkg in ("agent", "environment", "interface", "training"):
            self.assertIsNotNone(importlib.util.find_spec(pkg), f"cannot import {pkg}")

    def test_config_loads(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("pyyaml not installed yet")
        from configs import load_config

        cfg = load_config(ROOT / "configs" / "default.yaml")
        for key in ("env", "agent", "training", "logging"):
            self.assertIn(key, cfg)

    def test_logger_writes_file(self):
        import tempfile

        from training.logger import setup_logging

        with tempfile.TemporaryDirectory() as tmp:
            log = setup_logging(log_dir=tmp, level="INFO")
            try:
                log.info("scaffold probe")
                for handler in log.handlers:
                    handler.flush()
                self.assertTrue(Path(tmp, "universal-game-agent.log").exists())
            finally:
                # Release the file handle so Windows can delete the temp dir.
                for handler in log.handlers[:]:
                    log.removeHandler(handler)
                    handler.close()


if __name__ == "__main__":
    unittest.main()
