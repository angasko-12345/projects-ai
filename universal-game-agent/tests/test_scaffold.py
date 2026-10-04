"""Scaffold smoke tests (stdlib unittest only, no third-party imports)."""
try:
    from . import _bootstrap
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap

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
        for key in ("env", "model", "ppo", "curiosity", "eval", "logging"):
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


class TestLoggingValidation(unittest.TestCase):
    def test_unknown_level_rejected(self):
        from training.logger import setup_logging

        with self.assertRaises(ValueError):
            setup_logging(level="DEBG")

    def test_known_levels_accepted(self):
        import logging
        import tempfile

        from training.logger import setup_logging

        for level in ("debug", "INFO", "Warning"):
            with tempfile.TemporaryDirectory() as tmp:
                log = setup_logging(log_dir=tmp, level=level)
                try:
                    self.assertEqual(log.level, getattr(logging, level.upper()))
                finally:
                    for handler in log.handlers[:]:
                        log.removeHandler(handler)
                        handler.close()


class TestOptionalDependencyGates(unittest.TestCase):
    """A gated test module must still import when the dependency is absent.

    ``tests/_bootstrap.py`` puts the product on ``sys.path``, and the
    torch-dependent modules gate their imports on ``try: import torch`` so
    their tests skip on a machine without it. A reference to a guarded name
    outside that ``try`` (a class statement, a module-level ``getattr``) is a
    ``NameError``, not an ``ImportError``, so it escapes the guard and unittest
    reports the whole module as an error - masking every real result on that
    box with tests that were never going to run anyway.
    """

    GATED_MODULES = (
        "tests.test_ppo_objectives",
        "tests.test_external_experiment_orchestration",
    )

    def test_gated_test_modules_import_without_torch(self):
        for name in self.GATED_MODULES:
            with self.subTest(module=name):
                importlib.import_module(name)


if __name__ == "__main__":
    unittest.main()
