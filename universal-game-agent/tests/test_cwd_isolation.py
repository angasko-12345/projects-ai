"""Regression: checkpoint-writing tests must leave the caller's CWD untouched.

ROOT-034 DBG-07: four tests relied on the default
``PPOConfig.checkpoint_dir == "checkpoints"`` and wrote ~2 MB of
``ppo_final.pt`` artifacts into whichever directory the suite was launched
from. This runs exactly those tests with the CWD pointed at an empty scratch
directory and requires it to stay empty, so re-polluting the repository or
the developer's shell directory fails the suite.
"""
try:
    from . import _bootstrap
except ImportError:  # run as script or discovered top-level: no package context
    import _bootstrap

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

# The four tests the ROOT-034 audit found writing checkpoints into the CWD.
CHECKPOINT_WRITING_TESTS = (
    "tests.test_diagnostics.TestDiagnostics.test_termination_vs_truncation_counts",
    "tests.test_diagnostics.TestDiagnostics.test_component_means",
    "tests.test_diagnostics.TestDiagnostics.test_history_keys_present",
    "tests.test_external_training.TestExternalTraining.test_short_train_and_eval",
)


class TestCheckpointTestsStayOutOfCwd(unittest.TestCase):
    def test_running_them_from_a_scratch_cwd_writes_nothing(self):
        suite = unittest.defaultTestLoader.loadTestsFromNames(CHECKPOINT_WRITING_TESTS)
        with tempfile.TemporaryDirectory() as scratch:
            previous = os.getcwd()
            os.chdir(scratch)
            stdout = io.StringIO()
            runner_out = io.StringIO()
            try:
                with redirect_stdout(stdout):  # trainer prints update lines
                    result = unittest.TextTestRunner(stream=runner_out).run(suite)
            finally:
                os.chdir(previous)
            leftovers = sorted(os.listdir(scratch))
        self.assertTrue(result.wasSuccessful(),
                        "inner run failed:\n" + runner_out.getvalue() + stdout.getvalue())
        self.assertEqual(leftovers, [],
                         "checkpoint artifacts leaked into the caller's CWD")


if __name__ == "__main__":
    unittest.main()
