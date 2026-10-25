"""Bounded repo inspection feeding routing characteristics.

Covers the empty/unknown, mixed-language, inaccessible-path, consistency,
caching, and never-opens-credential-files contracts of
``agentops.repo_inspection``.
"""

from __future__ import annotations

import builtins
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agentops.repo_inspection import (
    MANIFEST_NAMES,
    RepositoryInspector,
    RepositorySize,
    inspect_repository,
)

_CREDENTIAL_NAMES = {".env", ".env.local", "credentials.json", "secrets.txt", "id_rsa"}


def _build_tree(root: Path, tree: dict[str, str]) -> Path:
    for name, content in tree.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


class InspectRepositoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def _tree(self, name: str = "tree") -> Path:
        path = self.base / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def test_empty_directory_is_empty_and_neutral(self):
        result = inspect_repository(self._tree())
        self.assertIs(result.size, RepositorySize.EMPTY)
        self.assertEqual(result.languages, ())
        self.assertEqual(result.manifests, ())
        self.assertIsNone(result.test_runner)
        self.assertEqual(result.source_file_count, 0)
        self.assertFalse(result.truncated)
        self.assertEqual(
            result.routing_flags(),
            {
                "requires_tests": False,
                "repository_exploration_required": False,
                "security_sensitive": False,
                "documentation_required": False,
            },
        )

    def test_missing_path_is_unknown_and_never_raises(self):
        result = inspect_repository(self._tree() / "does-not-exist")
        self.assertIs(result.size, RepositorySize.UNKNOWN)
        self.assertEqual(result.languages, ())
        self.assertIsNone(result.test_runner)

    def test_inaccessible_root_is_unknown_and_never_raises(self):
        with patch("agentops.repo_inspection.os.scandir", side_effect=PermissionError(13, "locked")):
            result = inspect_repository(self._tree())
        self.assertIs(result.size, RepositorySize.UNKNOWN)
        self.assertEqual(result.languages, ())

    def test_inaccessible_subdirectory_is_skipped_without_failing_the_walk(self):
        root = self._tree("flaky-root")
        _build_tree(root, {
            "src/main.py": "print('x')\n",
            "locked/vendor.js": "x",
        })

        real_scandir = os.scandir

        def flaky_scandir(directory, **kwargs):
            if os.path.basename(os.fspath(directory)) == "locked":
                raise PermissionError(13, "locked")
            return real_scandir(directory, **kwargs)

        with patch("agentops.repo_inspection.os.scandir", flaky_scandir):
            result = inspect_repository(root)
        self.assertEqual(result.languages, ("Python",))
        self.assertEqual(result.source_file_count, 1)
        self.assertIs(result.size, RepositorySize.SMALL)

    def test_mixed_language_repo_orders_by_count_then_name(self):
        root = self._tree("mixed")
        _build_tree(root, {
            "a.py": "x",
            "b.py": "x",
            "c.go": "x",
            "d.ts": "x",
        })
        result = inspect_repository(root)
        self.assertEqual(result.languages, ("Python", "Go", "TypeScript"))
        self.assertEqual(result.language_counts, {"Python": 2, "Go": 1, "TypeScript": 1})
        # A heterogeneous tree asks the router for an exploration-capable agent.
        self.assertTrue(result.routing_flags()["repository_exploration_required"])
        self.assertFalse(result.routing_flags()["requires_tests"])

    def test_equal_counts_break_ties_alphabetically(self):
        root = self._tree("tie")
        _build_tree(root, {"x.go": "}", "y.ts": "x", "z.rb": "x"})
        self.assertEqual(
            inspect_repository(root).languages, ("Go", "Ruby", "TypeScript")
        )

    def test_unknown_suffixes_count_toward_size_but_not_languages(self):
        root = self._tree("blobs")
        _build_tree(root, {"blob.txt": "x", "image.bin": "x", "data.csv": "x"})
        result = inspect_repository(root)
        self.assertEqual(result.languages, ())
        self.assertEqual(result.source_file_count, 3)
        self.assertIs(result.size, RepositorySize.SMALL)

    def test_hidden_and_vendor_directories_are_skipped(self):
        root = self._tree("skips")
        _build_tree(root, {
            "main.py": "x",
            "node_modules/left-pad/index.js": "x",
            ".git/config": "x",
            ".agentops/state.sqlite": "",
            ".venv/pyvenv.cfg": "x",
            "__pycache__/main.cpython-311.pyc": "x",
        })
        result = inspect_repository(root)
        self.assertEqual(result.languages, ("Python",))
        self.assertEqual(result.source_file_count, 1)

    def test_max_depth_bounds_the_walk(self):
        root = self._tree("deep")
        _build_tree(root, {"src/inner/file.py": "x"})
        shallow = inspect_repository(root, max_depth=1)
        deep = inspect_repository(root, max_depth=3)
        self.assertEqual(shallow.source_file_count, 0)
        self.assertIs(shallow.size, RepositorySize.EMPTY)
        self.assertEqual(deep.source_file_count, 1)

    def test_file_cap_marks_truncated_and_classifies_large(self):
        root = self._tree("capped")
        _build_tree(root, {f"f{index}.py": "x" for index in range(4)})
        uncapped = inspect_repository(root)
        self.assertFalse(uncapped.truncated)
        self.assertIs(uncapped.size, RepositorySize.SMALL)
        capped = inspect_repository(root, max_files=2)
        self.assertTrue(capped.truncated)
        self.assertEqual(capped.source_file_count, 2)
        self.assertIs(capped.size, RepositorySize.LARGE)
        self.assertTrue(capped.routing_flags()["repository_exploration_required"])

    def test_size_boundaries_without_truncation(self):
        root = self._tree("boundary")
        _build_tree(root, {f"s{index}.py": "x" for index in range(101)})
        self.assertIs(inspect_repository(root).size, RepositorySize.MEDIUM)
        for index in range(100):
            (root / f"s{index}.py").unlink()
        self.assertIs(inspect_repository(root).size, RepositorySize.SMALL)
        for index in range(1000):
            (root / f"t{index}.py").write_text("x", encoding="utf-8")
        result = inspect_repository(root)
        self.assertEqual(result.source_file_count, 1001)
        self.assertIs(result.size, RepositorySize.LARGE)


class ManifestReadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def _tree(self, name: str = "tree") -> Path:
        path = self.base / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _inspect_with_open_spy(self, root: Path):
        real_open = builtins.open
        opened: list[str] = []

        def spy_open(file, *args, **kwargs):
            opened.append(Path(os.fspath(file)).name)
            return real_open(file, *args, **kwargs)

        with patch("builtins.open", spy_open):
            result = inspect_repository(root)
        return result, opened

    def test_only_allowlisted_manifests_are_ever_opened(self):
        root = self._tree("privacy")
        _build_tree(root, {
            ".env": "SECRET=1\nAPI_KEY=anything\n",
            "secrets/credentials.txt": "SECRET=2\n",
            "id_rsa": "-----BEGIN FAKE-----\n",
            "package.json": '{"name": "x", "scripts": {"test": "jest"}}\n',
            "pyproject.toml": "[project]\nname = 'x'\n",
        })
        result, opened = self._inspect_with_open_spy(root)
        self.assertTrue(opened)
        self.assertTrue(set(opened) <= set(MANIFEST_NAMES), opened)
        self.assertFalse(set(opened) & _CREDENTIAL_NAMES)
        self.assertEqual(result.manifests, ("package.json", "pyproject.toml"))
        self.assertEqual(result.test_runner, "npm test")

    def test_env_files_stay_unread_and_unreported(self):
        root = self._tree("env-only")
        _build_tree(root, {".env": "TOKEN=hunter2\n"})
        result, opened = self._inspect_with_open_spy(root)
        self.assertFalse(opened)
        self.assertEqual(result.manifests, ())
        self.assertIsNone(result.test_runner)

    def test_unparseable_manifest_yields_no_signal_instead_of_raising(self):
        root = self._tree("broken-manifests")
        _build_tree(root, {
            "package.json": "<html>not json",
            "pyproject.toml": "☃ not toml",
        })
        result = inspect_repository(root)
        self.assertEqual(result.manifests, ("package.json", "pyproject.toml"))
        self.assertIsNone(result.test_runner)


class TestRunnerDetectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def _runner_for(self, tree: dict[str, str], name: str = "runner") -> str | None:
        root = self.base / name
        root.mkdir()
        _build_tree(root, tree)
        return inspect_repository(root).test_runner

    def test_conftest_anywhere_is_pytest(self):
        self.assertEqual(self._runner_for({"tests/conftest.py": ""}), "pytest")

    def test_pytest_ini_is_pytest(self):
        self.assertEqual(self._runner_for({"pytest.ini": "[pytest]\n"}), "pytest")

    def test_pyproject_pytest_section_is_pytest(self):
        self.assertEqual(
            self._runner_for({
                "pyproject.toml": "[tool.pytest.ini_options]\naddopts = '-q'\n",
            }),
            "pytest",
        )

    def test_pyproject_without_pytest_section_has_no_runner_signal(self):
        self.assertIsNone(self._runner_for({"pyproject.toml": "[project]\nname = 'x'\n"}))

    def test_package_json_test_script_is_npm(self):
        self.assertEqual(
            self._runner_for({"package.json": '{"scripts": {"test": "jest"}}'}),
            "npm test",
        )

    def test_package_json_without_test_script_has_no_runner_signal(self):
        self.assertIsNone(self._runner_for({"package.json": '{"name": "x"}'}))

    def test_go_module_is_go_test(self):
        self.assertEqual(self._runner_for({"go.mod": "module example.com/x\n"}), "go test")

    def test_rust_manifest_is_cargo_test(self):
        self.assertEqual(
            self._runner_for({"Cargo.toml": "[package]\nname = 'x'\n"}),
            "cargo test",
        )

    def test_python_tests_directory_without_pytest_signal_is_unittest(self):
        self.assertEqual(
            self._runner_for({"tests/test_something.py": "def test_x(): pass\n"}),
            "unittest",
        )

    def test_non_python_tests_directory_is_not_unittest(self):
        self.assertIsNone(self._runner_for({"tests/a.test.js": "test('x', () => {})\n"}))

    def test_conftest_outranks_go_module(self):
        self.assertEqual(
            self._runner_for({"go.mod": "module example.com/x\n", "conftest.py": ""}),
            "pytest",
        )

    def test_npm_test_outranks_python_tests_directory(self):
        self.assertEqual(
            self._runner_for({
                "package.json": '{"scripts": {"test": "jest"}}',
                "tests/test_x.py": "",
            }),
            "npm test",
        )


class ConsistencyAndCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "stable"
        self.root.mkdir()
        _build_tree(self.root, {
            "a.py": "x",
            "b.py": "x",
            "go.mod": "module example\n",
            "tests/conftest.py": "",
        })

    def test_repeated_inspections_are_identical(self):
        first = inspect_repository(self.root)
        second = inspect_repository(self.root)
        self.assertEqual(first, second)
        self.assertEqual(first.routing_flags(), second.routing_flags())
        self.assertTrue(first.routing_flags()["requires_tests"])
        self.assertEqual(first.test_runner, "pytest")

    def test_inspector_returns_the_cached_object_for_the_same_path(self):
        inspector = RepositoryInspector(cache_size=4)
        first = inspector.characteristics(self.root)
        second = inspector.characteristics(str(self.root / "."))
        self.assertIs(first, second)
        self.assertEqual(first.test_runner, "pytest")

    def test_inspector_cache_is_bounded(self):
        inspector = RepositoryInspector(cache_size=1)
        other = self.base / "other"
        other.mkdir()
        _build_tree(other, {"a.ts": "x"})

        cached = inspector.characteristics(self.root)
        inspector.characteristics(other)
        refreshed = inspector.characteristics(self.root)

        self.assertIsNot(cached, refreshed)
        self.assertEqual(cached, refreshed)

    def test_inspector_does_not_hold_its_lock_during_the_walk(self):
        inspector = RepositoryInspector()
        lock_states: list[bool] = []
        real_inspect = inspect_repository

        def spy_inspect(source, **kwargs):
            lock_states.append(inspector._lock.locked())
            return real_inspect(source, **kwargs)

        with patch("agentops.repo_inspection.inspect_repository", spy_inspect):
            first = inspector.characteristics(self.root)
        self.assertEqual(lock_states, [False])
        self.assertIs(inspector.characteristics(self.root), first)

    def test_inspector_never_raises_on_unresolvable_path(self):
        inspector = RepositoryInspector()
        with patch(
            "agentops.repo_inspection.Path.resolve",
            side_effect=OSError(13, "cannot resolve"),
        ):
            flags = inspector.routing_flags(self.root)
        self.assertEqual(
            flags,
            {
                "requires_tests": False,
                "repository_exploration_required": False,
                "security_sensitive": False,
                "documentation_required": False,
            },
        )
        with patch("builtins.open", side_effect=OSError(13, "locked")):
            self.assertIsNone(inspector.characteristics(self.root / "nope").test_runner)

    def test_manifest_allowlist_shapes_the_value(self):
        self.assertIn("pyproject.toml", MANIFEST_NAMES)
        self.assertFalse(_CREDENTIAL_NAMES & MANIFEST_NAMES)


if __name__ == "__main__":
    unittest.main()


class ManifestSymlinkSafetyTests(unittest.TestCase):
    """Root manifests are read only when they are regular, non-symlinked files."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "repo"
        self.root.mkdir()

    def _symlink_or_skip(self, target: Path, link: Path) -> None:
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks unavailable: {error}")

    def _run_with_deadline(self, call, fifo: Path):
        import threading

        results: list[object] = []
        worker = threading.Thread(target=lambda: results.append(call()), daemon=True)
        worker.start()
        worker.join(timeout=5)
        if worker.is_alive():
            # Unblock the hung reader so the daemon thread can finish.
            with open(fifo, "wb"):
                pass
            self.fail("inspection blocked on a FIFO-backed manifest")
        return results[0]

    @unittest.skipUnless(hasattr(os, "mkfifo"), "os.mkfifo unavailable (e.g. Windows)")
    def test_manifest_symlink_to_fifo_returns_promptly(self):
        fifo = self.base / "pipe"
        os.mkfifo(fifo)
        self._symlink_or_skip(fifo, self.root / "package.json")
        result = self._run_with_deadline(
            lambda: RepositoryInspector().characteristics(self.root), fifo,
        )
        self.assertEqual(result.manifests, ("package.json",))
        self.assertIsNone(result.test_runner)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "os.mkfifo unavailable (e.g. Windows)")
    def test_plain_fifo_manifest_returns_promptly(self):
        fifo = self.root / "pyproject.toml"
        os.mkfifo(fifo)
        result = self._run_with_deadline(lambda: inspect_repository(self.root), fifo)
        self.assertIsNone(result.test_runner)

    def test_manifest_symlink_to_outside_regular_file_is_not_read(self):
        outside = self.base / "outside.json"
        outside.write_text('{"scripts": {"test": "jest"}}\n', encoding="utf-8")
        self._symlink_or_skip(outside, self.root / "package.json")
        with patch("agentops.repo_inspection.open", create=True) as spy_open:
            spy_open.side_effect = builtins.open
            result = inspect_repository(self.root)
        self.assertEqual(spy_open.call_count, 0)
        self.assertIsNone(result.test_runner)

    def test_regular_manifest_is_still_read(self):
        (self.root / "package.json").write_text(
            '{"scripts": {"test": "jest"}}\n', encoding="utf-8",
        )
        self.assertEqual(inspect_repository(self.root).test_runner, "npm test")
