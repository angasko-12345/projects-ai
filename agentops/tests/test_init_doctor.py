"""U2: ``agentops init``, ``agentops doctor``, neutral defaults, config precedence.

Scratch repositories are built under ``tempfile`` with real ``git``; the
Windows-specific matrix (``.cmd`` shims, CreateProcess resolution, the
Microsoft Store python alias) runs on every OS through the ``platform`` /
``which`` / ``run`` seams.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentops.cli import main
from agentops.config import (
    REPO_CONFIG_RELATIVE,
    find_repository_root,
    load_config,
    resolve_config_source,
    user_config_path,
)
from agentops.doctor import BLOCK, OK, SCHEMA, WARN, resolve_spawnable, run_doctor
from agentops.init_config import PLACEHOLDER_CHECK_PREFIX, build_config, detect_stack, run_init
from agentops.logging import LogManager
from agentops.state import StateStore
from agentops.verification_kernel import VerificationKernel
from agentops.verification_model import VerificationReportStatus

FLEET_CONFIG = Path(__file__).resolve().parents[1] / "examples" / "hacir-fleet.yaml"
HAS_GIT = shutil.which("git") is not None
HAS_PYTHON = shutil.which("python") is not None

PASSING_UNITTEST = (
    "import unittest\n\n\nclass Smoke(unittest.TestCase):\n"
    "    def test_ok(self):\n        self.assertEqual(1 + 1, 2)\n"
)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=AgentOps Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )


def _make_repo(root: Path, files: dict[str, str], commit: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if commit:
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "init", "--allow-empty")
    return root


def _verify(root: Path, config_path: Path | None, logs: Path):
    """Run the configured default profile for real (no fakes below the process)."""
    config = load_config(config_path)
    state = StateStore(":memory:")
    try:
        kernel = VerificationKernel(
            profiles=config.verification_profiles,
            default_profile=config.default_verification_profile,
            logs=LogManager(logs),
            state=state,
            systemd_run_enabled=False,
        )
        return asyncio.run(kernel.run_verification(None, None, root))
    finally:
        state.close()


def _which_from(mapping: dict[str, str]):
    return lambda name: mapping.get(name)


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _scripted_git(
    *,
    toplevel: str | None,
    head: bool = True,
    branch: str | None = "main",
    status: str = "",
    modules: frozenset[str] = frozenset({"unittest"}),
):
    """A ``run`` seam answering the doctor's git and module probes."""

    def run(command, cwd, timeout):
        argv = list(command)
        if argv[1:2] == ["--version"]:
            return _Completed(0, "git version 2.45.0.windows.1\n")
        if argv[1:3] == ["rev-parse", "--show-toplevel"]:
            return (
                _Completed(0, f"{toplevel}\n")
                if toplevel
                else _Completed(128, "", "not a git repo")
            )
        if argv[1:3] == ["rev-parse", "--verify"]:
            return _Completed(0 if head else 1, "abc\n" if head else "")
        if argv[1:2] == ["symbolic-ref"]:
            return _Completed(0, f"{branch}\n") if branch else _Completed(1)
        if argv[1:2] == ["status"]:
            return _Completed(0, status)
        if argv[1:2] == ["-c"]:
            return _Completed(0 if argv[-1] in modules else 3)
        return _Completed(0, "1.0.0\n")

    return run


@unittest.skipUnless(HAS_GIT and HAS_PYTHON, "needs git and python on PATH")
class NeutralDefaultsRegressionTests(unittest.TestCase):
    """V1: the bundled config used to FAIL verification on every foreign repo."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.repo = _make_repo(self.base / "foreign", {"tests/test_smoke.py": PASSING_UNITTEST})

    def tearDown(self):
        self._tmp.cleanup()

    def test_bundled_default_passes_verification_on_a_foreign_repo(self):
        report = _verify(self.repo, None, self.base / "logs")
        self.assertEqual(report.overall_status, VerificationReportStatus.PASSED, report.transcript)

    def test_old_fleet_profile_cannot_pass_on_a_foreign_repo(self):
        # Documents the bug the neutral default fixes: the fleet profile pins
        # working_directory "agentops", so it is never PASSED elsewhere.
        report = _verify(self.repo, FLEET_CONFIG, self.base / "logs")
        self.assertNotEqual(report.overall_status, VerificationReportStatus.PASSED)

    def test_doctor_flags_the_old_fleet_profile_on_a_foreign_repo(self):
        report = run_doctor(self.repo, FLEET_CONFIG, home=self.base / "home", environ={})
        blocked = {item.id for item in report.findings if item.status == BLOCK}
        self.assertIn("check:agentops-tests", blocked)


@unittest.skipUnless(HAS_GIT, "needs git on PATH")
class InitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    @unittest.skipUnless(HAS_PYTHON, "needs python on PATH")
    def test_init_python_unittest_repo_writes_a_profile_that_passes(self):
        repo = _make_repo(
            self.base / "py",
            {
                "pyproject.toml": "[project]\nname = 'demo'\nversion = '0.1'\n",
                "tests/test_smoke.py": PASSING_UNITTEST,
            },
        )
        result = run_init(repo)
        self.assertTrue(result.written)
        self.assertEqual(result.path, repo / REPO_CONFIG_RELATIVE)
        config = load_config(result.path)
        (check,) = config.verification_profiles["standard"].checks
        self.assertEqual(check.command, ("python", "-m", "unittest", "discover", "-s", "tests"))
        self.assertFalse(config.systemd_run_enabled)
        report = _verify(repo, result.path, self.base / "logs")
        self.assertEqual(report.overall_status, VerificationReportStatus.PASSED, report.transcript)

    def test_init_python_pytest_repo_uses_pytest(self):
        for files, evidence in (
            ({"pyproject.toml": "[tool.pytest.ini_options]\naddopts = '-q'\n"}, "[tool.pytest]"),
            (
                {"setup.py": "", "conftest.py": "", "tests/test_x.py": "def test_x():\n    pass\n"},
                "conftest.py",
            ),
            (
                {"requirements-dev.txt": "pytest>=8\n", "tests/test_x.py": ""},
                "requirements-dev.txt",
            ),
        ):
            with self.subTest(evidence=evidence):
                root = self.base / f"pytest-{len(os.listdir(self.base))}"
                root.mkdir()
                for relative, text in files.items():
                    (root / relative).parent.mkdir(parents=True, exist_ok=True)
                    (root / relative).write_text(text, encoding="utf-8")
                detection = detect_stack(root)
                self.assertEqual(detection.stacks, ["python"])
                (check,) = detection.checks
                self.assertEqual(check.command, ("python", "-m", "pytest", "-q"))
                self.assertIn(evidence, check.evidence)

    def test_init_node_repo_uses_package_scripts(self):
        repo = _make_repo(
            self.base / "node",
            {
                "package.json": json.dumps(
                    {
                        "name": "demo",
                        "scripts": {"test": 'node -e "process.exit(0)"', "lint": "eslint ."},
                    }
                ),
            },
        )
        detection = detect_stack(repo, platform="linux", which=lambda name: f"/usr/bin/{name}")
        self.assertEqual(detection.stacks, ["node"])
        test, lint = detection.checks
        self.assertEqual((test.command, test.required), (("npm", "test"), True))
        self.assertEqual(
            (lint.command, lint.required, lint.check_class), (("npm", "run", "lint"), False, "lint")
        )
        self.assertTrue(any("node_modules" in note for note in detection.notes))

    def test_init_node_on_windows_writes_spawnable_cmd_names(self):
        repo = self.base / "nodewin"
        repo.mkdir()
        (repo / "package.json").write_text(
            json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8"
        )
        (repo / "pnpm-lock.yaml").write_text("", encoding="utf-8")
        detection = detect_stack(repo, platform="win32", which=lambda name: f"C:\\npm\\{name}.CMD")
        self.assertEqual(detection.checks[0].command, ("pnpm.cmd", "test"))

    def test_init_skips_npm_placeholder_test_script(self):
        repo = self.base / "nodeinit"
        repo.mkdir()
        (repo / "package.json").write_text(
            json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}),
            encoding="utf-8",
        )
        detection = detect_stack(repo, platform="linux", which=lambda name: None)
        self.assertEqual(detection.checks, [])
        self.assertTrue(detection.placeholder)

    @unittest.skipUnless(shutil.which("npm") and shutil.which("node"), "needs npm and node on PATH")
    def test_init_node_repo_profile_passes_for_real(self):
        repo = _make_repo(
            self.base / "nodereal",
            {
                "package.json": json.dumps(
                    {
                        "name": "demo",
                        "version": "1.0.0",
                        "scripts": {"test": 'node -e "process.exit(0)"'},
                    }
                ),
            },
        )
        (repo / "node_modules").mkdir()
        result = run_init(repo)
        report = _verify(repo, result.path, self.base / "logs")
        self.assertEqual(report.overall_status, VerificationReportStatus.PASSED, report.transcript)

    @unittest.skipUnless(HAS_PYTHON, "needs python on PATH")
    def test_init_with_nothing_detectable_writes_a_failing_placeholder(self):
        repo = _make_repo(self.base / "empty", {"README.md": "hello\n"})
        result = run_init(repo)
        config = load_config(result.path)
        (check,) = config.verification_profiles["standard"].checks
        self.assertTrue(check.name.startswith(PLACEHOLDER_CHECK_PREFIX))
        # Never a vacuous pass: the placeholder fails if it is ever run.
        report = _verify(repo, result.path, self.base / "logs")
        self.assertEqual(report.overall_status, VerificationReportStatus.FAILED)

    def test_init_never_overwrites_without_force(self):
        repo = _make_repo(self.base / "keep", {"tests/test_a.py": PASSING_UNITTEST})
        target = repo / REPO_CONFIG_RELATIVE
        target.parent.mkdir(parents=True)
        target.write_text('{"agents": {}}', encoding="utf-8")
        result = run_init(repo)
        self.assertFalse(result.written)
        self.assertIn("--force", result.message)
        self.assertEqual(target.read_text(encoding="utf-8"), '{"agents": {}}')
        forced = run_init(repo, force=True)
        self.assertTrue(forced.written)
        self.assertIn("unittest", target.read_text(encoding="utf-8"))

    def test_review_prefers_a_different_installed_cli(self):
        installed = {"kilo": "/bin/kilo", "codex": "/bin/codex"}
        data, _, found = build_config(self.base, which=_which_from(installed))
        self.assertEqual(sorted(found), ["codex", "kilo"])
        self.assertEqual(data["role_preferences"]["implementation"][0], "kilo")
        self.assertEqual(data["role_preferences"]["review"][0], "codex")

    def test_cli_init_writes_excludes_and_refuses_second_run(self):
        repo = _make_repo(self.base / "cli", {"tests/test_a.py": PASSING_UNITTEST})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["init", "--cwd", str(repo)]), 0)
        self.assertTrue((repo / REPO_CONFIG_RELATIVE).is_file())
        # .agentops/ stays out of git status, so the merge gate's clean-base
        # check is unaffected by init.
        self.assertEqual(_git(repo, "status", "--porcelain").stdout.strip(), "")
        with contextlib.redirect_stdout(io.StringIO()) as second:
            self.assertEqual(main(["init", "--cwd", str(repo)]), 1)
        self.assertIn("--force", second.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(main(["init", "--cwd", str(repo), "--print"]), 0)
        self.assertIn("standard", json.loads(printed.getvalue())["verification"]["profiles"])

    def test_cli_init_outside_git_fails_cleanly(self):
        plain = self.base / "plain"
        plain.mkdir()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["init", "--cwd", str(plain)]), 1)
        self.assertIn("Git repository", out.getvalue())
        self.assertFalse((plain / ".agentops").exists())


class ConfigPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.repo = self.base / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / "src").mkdir()
        self.home = self.base / "home"

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"agents": {}}', encoding="utf-8")
        return path

    def test_precedence_flag_repo_user_bundled(self):
        env: dict[str, str] = {}
        source = resolve_config_source(
            None, self.repo / "src", platform="linux", environ=env, home=self.home
        )
        self.assertEqual((source.source, source.path), ("bundled", None))
        self.assertEqual(source.repository_root, self.repo.resolve())
        user = self._write(self.home / ".config" / "agentops" / "agents.yaml")
        source = resolve_config_source(
            None, self.repo / "src", platform="linux", environ=env, home=self.home
        )
        self.assertEqual((source.source, source.path), ("user", user))
        repo_config = self._write(self.repo / REPO_CONFIG_RELATIVE)
        source = resolve_config_source(
            None, self.repo / "src", platform="linux", environ=env, home=self.home
        )
        self.assertEqual((source.source, source.path), ("repo", repo_config.resolve()))
        flag = self._write(self.base / "explicit.yaml")
        source = resolve_config_source(
            flag, self.repo / "src", platform="linux", environ=env, home=self.home
        )
        self.assertEqual((source.source, source.path), ("flag", flag))

    def test_user_config_locations(self):
        self.assertEqual(
            user_config_path("win32", {"APPDATA": "C:\\Users\\h\\AppData\\Roaming"}),
            Path("C:\\Users\\h\\AppData\\Roaming") / "AgentOps" / "agents.yaml",
        )
        self.assertIsNone(user_config_path("win32", {}))
        self.assertEqual(
            user_config_path("linux", {"XDG_CONFIG_HOME": "/x"}), Path("/x/agentops/agents.yaml")
        )
        self.assertEqual(
            user_config_path("linux", {}, home=Path("/h")), Path("/h/.config/agentops/agents.yaml")
        )

    def test_repository_root_walks_up_to_dot_git(self):
        self.assertEqual(find_repository_root(self.repo / "src"), self.repo.resolve())


class DoctorSeamTests(unittest.TestCase):
    """Windows-first doctor logic, exercised on any OS through seams."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.repo = self.base / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_a.py").write_text(PASSING_UNITTEST, encoding="utf-8")
        self.config = self.base / "config.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _config(self, agents: dict, checks: list[dict]) -> Path:
        self.config.write_text(
            json.dumps(
                {
                    "agents": agents,
                    "verification": {
                        "default_profile": "standard",
                        "profiles": {"standard": {"checks": checks}},
                    },
                }
            ),
            encoding="utf-8",
        )
        return self.config

    def _doctor(self, which: dict[str, str], platform: str = "linux", **kwargs):
        run = kwargs.pop("run", None) or _scripted_git(toplevel=str(self.repo))
        return run_doctor(
            self.repo,
            self.config,
            platform=platform,
            which=_which_from(which),
            run=run,
            environ={},
            home=self.base / "home",
            **kwargs,
        )

    @staticmethod
    def _status(report, finding_id: str) -> str | None:
        return next((item.status for item in report.findings if item.id == finding_id), None)

    UNITTEST_CHECK = {
        "name": "tests",
        "class": "tests",
        "command": ["python", "-m", "unittest", "discover", "-s", "tests"],
    }

    def test_healthy_setup_passes(self):
        self._config(
            {
                "kilo": {"command": "kilo", "args": ["run", "{prompt}"]},
                "codex": {"command": "codex", "args": ["exec", "{prompt}"]},
            },
            [self.UNITTEST_CHECK],
        )
        report = self._doctor(
            {
                "git": "/usr/bin/git",
                "kilo": "/usr/bin/kilo",
                "codex": "/usr/bin/codex",
                "python": "/usr/bin/python",
            }
        )
        self.assertTrue(report.ok, report.render_text())
        self.assertEqual(report.counts[BLOCK], 0)
        kilo = next(agent for agent in report.agents if agent.name == "kilo")
        self.assertEqual((kilo.found, kilo.kind, kilo.transport), (True, "exe", "argv"))
        self.assertIn("Result: PASS", report.render_text())

    def test_json_schema_is_stable(self):
        self._config({"kilo": {"command": "kilo"}}, [self.UNITTEST_CHECK])
        data = self._doctor(
            {"git": "/usr/bin/git", "kilo": "/usr/bin/kilo", "python": "/usr/bin/python"}
        ).to_json()
        self.assertEqual(data["schema"], SCHEMA)
        self.assertEqual(
            set(data),
            {
                "schema",
                "ok",
                "target",
                "repository_root",
                "platform",
                "config",
                "counts",
                "agents",
                "checks",
            },
        )
        self.assertEqual(set(data["counts"]), {OK, WARN, BLOCK})
        self.assertEqual(set(data["config"]), {"source", "path"})
        self.assertEqual(set(data["checks"][0]), {"id", "status", "message", "fix"})
        self.assertEqual(
            set(data["agents"][0]),
            {
                "name",
                "enabled",
                "command",
                "found",
                "path",
                "kind",
                "transport",
                "resolution",
                "status",
                "detail",
                "roles",
            },
        )
        json.dumps(data)

    def test_broken_setups_block(self):
        self._config({"kilo": {"command": "kilo"}}, [self.UNITTEST_CHECK])
        cases = {
            "no git": ({"kilo": "/k", "python": "/p"}, {}, "git"),
            "not a repo": (
                {"git": "/g", "kilo": "/k", "python": "/p"},
                {"run": _scripted_git(toplevel=None)},
                "repository",
            ),
            "no commits": (
                {"git": "/g", "kilo": "/k", "python": "/p"},
                {"run": _scripted_git(toplevel=str(self.repo), head=False)},
                "repository",
            ),
            "detached": (
                {"git": "/g", "kilo": "/k", "python": "/p"},
                {"run": _scripted_git(toplevel=str(self.repo), branch=None)},
                "repository",
            ),
            "no agents": ({"git": "/g", "python": "/p"}, {}, "agents"),
            "no python": ({"git": "/g", "kilo": "/k"}, {}, "check:tests"),
            "module missing": (
                {"git": "/g", "kilo": "/k", "python": "/p"},
                {"run": _scripted_git(toplevel=str(self.repo), modules=frozenset())},
                "check:tests",
            ),
        }
        for label, (which, extra, finding) in cases.items():
            with self.subTest(label):
                report = self._doctor(which, **extra)
                self.assertFalse(report.ok)
                self.assertEqual(self._status(report, finding), BLOCK, report.render_text())

    def test_invalid_config_blocks(self):
        self.config.write_text("{not json", encoding="utf-8")
        report = self._doctor({"git": "/g"})
        self.assertEqual(self._status(report, "config"), BLOCK)

    def test_dirty_base_and_single_agent_warn(self):
        self._config({"kilo": {"command": "kilo"}}, [self.UNITTEST_CHECK])
        report = self._doctor(
            {"git": "/g", "kilo": "/k", "python": "/p"},
            run=_scripted_git(toplevel=str(self.repo), status=" M src/app.py\n?? .agentops/\n"),
        )
        self.assertTrue(report.ok)
        self.assertEqual(self._status(report, "worktree-clean"), WARN)
        self.assertIn(
            "1 uncommitted", next(f.message for f in report.findings if f.id == "worktree-clean")
        )
        self.assertEqual(self._status(report, "review-independence"), WARN)

    def test_placeholder_and_missing_start_dir_block(self):
        self._config(
            {"kilo": {"command": "kilo"}},
            [
                {
                    "name": f"{PLACEHOLDER_CHECK_PREFIX}",
                    "class": "custom",
                    "command": ["python", "-c", "x"],
                },
                {
                    "name": "other",
                    "class": "tests",
                    "command": ["python", "-m", "unittest", "discover", "-s", "missing"],
                },
            ],
        )
        report = self._doctor({"git": "/g", "kilo": "/k", "python": "/p"})
        self.assertEqual(self._status(report, f"check:{PLACEHOLDER_CHECK_PREFIX}"), BLOCK)
        self.assertEqual(self._status(report, "check:other"), BLOCK)

    def test_windows_bare_npm_is_not_spawnable_but_npm_cmd_is(self):
        (self.repo / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8"
        )
        (self.repo / "node_modules").mkdir()
        which = {
            "git": "C:\\Git\\cmd\\git.exe",
            "kilo": "C:\\bin\\kilo.exe",
            "npm": "C:\\Program Files\\nodejs\\npm.CMD",
            "npm.cmd": "C:\\Program Files\\nodejs\\npm.CMD",
        }
        for command, expected in ((["npm", "test"], BLOCK), (["npm.cmd", "test"], OK)):
            with self.subTest(command=command):
                self._config(
                    {"kilo": {"command": "kilo"}},
                    [{"name": "node", "class": "tests", "command": command}],
                )
                report = self._doctor(which, platform="win32")
                self.assertEqual(self._status(report, "check:node"), expected, report.render_text())
        path, problem, fix = resolve_spawnable("npm", platform="win32", which=_which_from(which))
        self.assertIsNone(path)
        self.assertIn("CreateProcess", problem)
        self.assertIn("npm.cmd", fix)

    def test_windows_store_python_alias_warns(self):
        self._config({"kilo": {"command": "kilo"}}, [self.UNITTEST_CHECK])
        which = {
            "git": "C:\\Git\\git.exe",
            "kilo": "C:\\bin\\kilo.exe",
            "python": "C:\\Users\\h\\AppData\\Local\\Microsoft\\WindowsApps\\python.exe",
        }
        report = self._doctor(which, platform="win32")
        self.assertEqual(self._status(report, "check:tests"), WARN)

    def test_windows_agent_transport_modes(self):
        shim = "C:\\npm\\opencode.cmd"
        native = "C:\\npm\\node_modules\\opencode-ai\\node_modules\\opencode-windows-x64\\bin\\opencode.exe"
        self._config(
            {
                "opencode": {"command": "opencode", "args": ["run", "{prompt}"]},
                "kilo": {"command": "kilo", "args": ["run", "{prompt}"]},
                "codex": {
                    "command": "codex",
                    "args": ["exec", "{prompt}"],
                    "stdin_args": ["exec", "-"],
                },
            },
            [self.UNITTEST_CHECK],
        )
        which = {
            "git": "C:\\Git\\git.exe",
            "python": "C:\\Python312\\python.exe",
            "opencode": shim,
            "kilo": "C:\\npm\\kilo.cmd",
            "codex": "C:\\npm\\codex.cmd",
        }
        report = self._doctor(
            which,
            platform="win32",
            machine="AMD64",
            exists=lambda path: path == native,
            read_text=lambda path: "@echo off\r\nrem not an npm shim\r\n",
        )
        agents = {agent.name: agent for agent in report.agents}
        self.assertEqual(
            (agents["opencode"].transport, agents["opencode"].resolution),
            ("argv", "opencode_native_exe"),
        )
        self.assertEqual(agents["opencode"].kind, "cmd")
        self.assertEqual(agents["codex"].transport, "stdin")
        self.assertEqual((agents["kilo"].transport, agents["kilo"].status), ("refused", WARN))
        self.assertEqual(self._status(report, "agent:kilo"), WARN)
        # Two usable agents remain, so the run is not blocked.
        self.assertTrue(report.ok, report.render_text())

    def test_systemd_enabled_without_systemd_run_blocks_on_linux_only(self):
        data = {
            "agents": {"kilo": {"command": "kilo"}},
            "runtime": {"systemd_run_enabled": True},
            "verification": {
                "default_profile": "standard",
                "profiles": {"standard": {"checks": [self.UNITTEST_CHECK]}},
            },
        }
        self.config.write_text(json.dumps(data), encoding="utf-8")
        which = {"git": "/g", "kilo": "/k", "python": "/p"}
        self.assertEqual(self._status(self._doctor(which), "systemd"), BLOCK)
        self.assertEqual(self._status(self._doctor(which, platform="win32"), "systemd"), OK)


@unittest.skipUnless(
    HAS_GIT and HAS_PYTHON and sys.platform != "win32",
    "real-PATH doctor run uses a POSIX shell-script fake agent",
)
class DoctorEndToEndTests(unittest.TestCase):
    """Real git, real PATH lookup, real module probe: init then doctor."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        for name in ("kilo", "codex"):
            script = self.bin / name
            script.write_text("#!/bin/sh\necho 1.0.0\n", encoding="utf-8")
            script.chmod(0o755)

    def tearDown(self):
        self._tmp.cleanup()

    def _cli(self, *argv: str) -> tuple[int, str]:
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "XDG_CONFIG_HOME": str(self.base / "xdg"),
        }
        with patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()) as out:
            code = main(list(argv))
        return code, out.getvalue()

    def test_init_then_doctor_is_healthy(self):
        repo = _make_repo(self.base / "repo", {"tests/test_a.py": PASSING_UNITTEST})
        self.assertEqual(self._cli("init", "--cwd", str(repo))[0], 0)
        code, text = self._cli("doctor", "--cwd", str(repo), "--json", "--probe")
        data = json.loads(text)
        self.assertEqual(code, 0, text)
        self.assertTrue(data["ok"])
        self.assertEqual(data["config"]["source"], "repo")
        found = sorted(agent["name"] for agent in data["agents"] if agent["found"])
        self.assertEqual(found, ["codex", "kilo"])

    def test_doctor_on_broken_setup_exits_nonzero(self):
        plain = self.base / "plain"
        plain.mkdir()
        code, text = self._cli("doctor", "--cwd", str(plain))
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] repository", text)
        self.assertIn("Result: FAIL", text)


if __name__ == "__main__":
    unittest.main()
