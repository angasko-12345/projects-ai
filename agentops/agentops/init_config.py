"""``agentops init``: detect a repository's stack and write a starter config.

Upgrade U2. The bundled ``agents.yaml`` is deliberately repo-neutral; this
module writes ``<repo>/.agentops/config.yaml`` with a verification profile
built from evidence found in *that* repository (test framework markers,
``package.json`` scripts, ``go.mod``, ``Cargo.toml``). It never invents a
check: when nothing is detectable it writes a clearly marked placeholder
check that fails when run and that ``agentops doctor`` reports as a blocker,
so an unconfigured repository can never reach a vacuous PASS.

Detection reads files only, through plain ``pathlib``; the one external
lookup (``which``) is an injectable seam so the Windows ``.cmd`` matrix is
testable on Linux. No subprocess, SQLite, or GUI I/O happens here.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import REPO_CONFIG_RELATIVE, _load_data

#: Name prefix of the placeholder check written when no stack is detected.
PLACEHOLDER_CHECK_PREFIX = "TODO-configure-verification"

#: npm's own placeholder for a missing test script.
_NPM_DEFAULT_TEST = "no test specified"

_PYTHON_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "tox.ini", "Pipfile")
_PYTEST_FILES = ("pytest.ini", "conftest.py", "tests/conftest.py", "test/conftest.py")
_TEST_DIRS = ("tests", "test")


@dataclass(frozen=True)
class DetectedCheck:
    name: str
    command: tuple[str, ...]
    check_class: str = "tests"
    required: bool = True
    timeout_seconds: int = 900
    evidence: str = ""

    def to_config(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "class": self.check_class,
            "command": list(self.command),
            "timeout_seconds": self.timeout_seconds,
            "required": self.required,
            "policy": "sequential",
        }


@dataclass
class Detection:
    stacks: list[str] = field(default_factory=list)
    checks: list[DetectedCheck] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def placeholder(self) -> bool:
        return not self.checks


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _tool(name: str, platform: str, which: Callable[[str], str | None]) -> str:
    """The argv[0] the verification runtime can spawn for ``name``.

    Checks are spawned without a shell (``create_subprocess_exec``). On
    Windows, CreateProcess only appends ``.exe`` to a bare name, so ``npm``
    (really ``npm.cmd``) would fail with "file not found". When the tool
    resolves to a batch shim there, write the explicit ``.cmd`` name instead.
    """
    if platform != "win32":
        return name
    resolved = which(name)
    if resolved and resolved.lower().endswith((".cmd", ".bat")):
        return name + Path(resolved).suffix.lower()
    return name


def _python_uses_pytest(root: Path) -> str | None:
    for relative in _PYTEST_FILES:
        if (root / relative).is_file():
            return relative
    pyproject = _read(root / "pyproject.toml")
    if "[tool.pytest" in pyproject:
        return "pyproject.toml [tool.pytest]"
    if "[tool:pytest]" in _read(root / "setup.cfg"):
        return "setup.cfg [tool:pytest]"
    if re.search(r"^\[pytest\]", _read(root / "tox.ini"), re.MULTILINE):
        return "tox.ini [pytest]"
    if re.search(r"""["']pytest\b""", pyproject):
        return "pyproject.toml dependency"
    for requirements in sorted(root.glob("requirements*.txt")):
        if re.search(r"^\s*pytest\b", _read(requirements), re.MULTILINE | re.IGNORECASE):
            return requirements.name
    return None


def _python_test_dir(root: Path) -> str | None:
    for name in _TEST_DIRS:
        directory = root / name
        if directory.is_dir() and any(directory.rglob("test*.py")):
            return name
    return None


def _detect_python(root: Path, detection: Detection) -> None:
    markers = [name for name in _PYTHON_MARKERS if (root / name).is_file()]
    test_dir = _python_test_dir(root)
    if not markers and test_dir is None:
        return
    detection.stacks.append("python")
    pytest_evidence = _python_uses_pytest(root)
    if pytest_evidence:
        detection.checks.append(DetectedCheck(
            "pytest", ("python", "-m", "pytest", "-q"),
            evidence=f"pytest configured ({pytest_evidence})",
        ))
        return
    if test_dir is not None:
        detection.checks.append(DetectedCheck(
            "unittest", ("python", "-m", "unittest", "discover", "-s", test_dir),
            evidence=f"unittest-style tests under {test_dir}/",
        ))
        return
    detection.notes.append(
        "Python project found but no tests directory or pytest configuration; "
        "add a verification check by hand.")


def _node_runner(root: Path) -> tuple[str, str]:
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm", "pnpm-lock.yaml"
    if (root / "yarn.lock").is_file():
        return "yarn", "yarn.lock"
    if (root / "bun.lockb").is_file() or (root / "bun.lock").is_file():
        return "bun", "bun lockfile"
    return "npm", "package.json"


def _detect_node(root: Path, detection: Detection, platform: str,
                 which: Callable[[str], str | None]) -> None:
    package = root / "package.json"
    if not package.is_file():
        return
    detection.stacks.append("node")
    try:
        data = json.loads(_read(package))
    except ValueError:
        detection.notes.append("package.json is not valid JSON; no Node checks written.")
        return
    scripts = data.get("scripts") if isinstance(data, dict) else None
    scripts = scripts if isinstance(scripts, dict) else {}
    runner, why = _node_runner(root)
    tool = _tool(runner, platform, which)
    test_script = scripts.get("test")
    if isinstance(test_script, str) and test_script.strip() and _NPM_DEFAULT_TEST not in test_script:
        command = (tool, "run", "test") if runner == "bun" else (tool, "test")
        detection.checks.append(DetectedCheck(
            "node-test", command, evidence=f"package.json scripts.test via {runner} ({why})"))
    else:
        detection.notes.append("package.json has no real \"test\" script; no Node test check written.")
    for script, check_class in (("lint", "lint"), ("typecheck", "type_checking")):
        if isinstance(scripts.get(script), str) and scripts[script].strip():
            detection.checks.append(DetectedCheck(
                f"node-{script}", (tool, "run", script), check_class=check_class, required=False,
                timeout_seconds=600, evidence=f"package.json scripts.{script}"))
    if not (root / "node_modules").is_dir():
        detection.notes.append(
            "node_modules is missing here; install dependencies before running "
            "AgentOps (fresh worktrees are created from HEAD).")


def _detect_go(root: Path, detection: Detection) -> None:
    if (root / "go.mod").is_file():
        detection.stacks.append("go")
        detection.checks.append(DetectedCheck("go-test", ("go", "test", "./..."), evidence="go.mod"))


def _detect_rust(root: Path, detection: Detection) -> None:
    if (root / "Cargo.toml").is_file():
        detection.stacks.append("rust")
        detection.checks.append(DetectedCheck("cargo-test", ("cargo", "test"), evidence="Cargo.toml"))


def detect_stack(
    root: str | Path,
    *,
    platform: str | None = None,
    which: Callable[[str], str | None] | None = None,
) -> Detection:
    """Evidence-only stack detection at the repository root."""
    root = Path(root)
    platform = platform or sys.platform
    which = which or shutil.which
    detection = Detection()
    _detect_python(root, detection)
    _detect_node(root, detection, platform, which)
    _detect_go(root, detection)
    _detect_rust(root, detection)
    return detection


def _placeholder_check() -> dict[str, Any]:
    # Runs and FAILS (exit 1) if ever executed, so an unconfigured repo can
    # never pass verification; doctor BLOCKs on the name before that happens.
    return {
        "name": f"{PLACEHOLDER_CHECK_PREFIX}",
        "class": "custom",
        "command": [
            "python", "-c",
            "import sys; sys.exit('AgentOps: no verification configured. "
            "Edit .agentops/config.yaml and replace the TODO check.')",
        ],
        "timeout_seconds": 60,
        "required": True,
        "policy": "sequential",
    }


def choose_agents(
    agents: dict[str, Any],
    preferences: dict[str, list[str]],
    which: Callable[[str], str | None],
) -> tuple[dict[str, list[str]], list[str]]:
    """Reorder role preferences so installed CLIs come first and, when at least
    two are installed, review prefers a *different* CLI than implementation."""
    installed = [
        name for name, agent in agents.items()
        if agent.get("enabled", True) and isinstance(agent.get("command"), str) and which(agent["command"])
    ]
    ordered: dict[str, list[str]] = {}
    for role, names in preferences.items():
        present = [name for name in names if name in installed]
        missing = [name for name in names if name not in installed]
        ordered[role] = present + missing
    implementation = ordered.get("implementation", [])
    review = ordered.get("review", [])
    if implementation and len([n for n in review if n in installed]) >= 2 and review and review[0] == implementation[0]:
        first = review.pop(0)
        # Keep the implementer as the reviewer's fallback, right after the
        # first different installed CLI.
        index = next((i for i, n in enumerate(review) if n in installed), 0)
        review.insert(index + 1, first)
        ordered["review"] = review
    return ordered, installed


def build_config(
    root: str | Path,
    *,
    platform: str | None = None,
    which: Callable[[str], str | None] | None = None,
    base: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Detection, list[str]]:
    """The starter config for ``root`` plus what was detected and the installed CLIs."""
    which = which or shutil.which
    detection = detect_stack(root, platform=platform, which=which)
    data = copy.deepcopy(base if base is not None else _load_data(None))
    checks = [check.to_config() for check in detection.checks] or [_placeholder_check()]
    legacy = [list(check.command) for check in detection.checks if check.required]
    data["verification"] = {
        "commands": legacy,
        "default_profile": "standard",
        "profiles": {
            "standard": {
                "mode": "continue_on_failure",
                "concurrency": 1,
                "default_timeout_seconds": 900,
                "checks": checks,
            }
        },
    }
    if not legacy:
        data["verification"].pop("commands")
    preferences, installed = choose_agents(
        data.get("agents", {}), data.get("role_preferences", {}), which)
    data["role_preferences"] = preferences
    runtime = data.setdefault("runtime", {})
    runtime["systemd_run_enabled"] = False
    return data, detection, installed


@dataclass(frozen=True)
class InitResult:
    path: Path
    written: bool
    text: str
    detection: Detection
    installed_agents: tuple[str, ...]
    message: str


def run_init(
    root: str | Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    platform: str | None = None,
    which: Callable[[str], str | None] | None = None,
) -> InitResult:
    """Write ``<root>/.agentops/config.yaml``; refuse to overwrite unless ``force``."""
    root = Path(root)
    target = root / REPO_CONFIG_RELATIVE
    data, detection, installed = build_config(root, platform=platform, which=which)
    text = json.dumps(data, indent=4) + "\n"
    if dry_run:
        return InitResult(target, False, text, detection, tuple(installed), "dry run: nothing written")
    if target.exists() and not force:
        return InitResult(
            target, False, text, detection, tuple(installed),
            f"{target} already exists; re-run with --force to overwrite it.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return InitResult(target, True, text, detection, tuple(installed), f"wrote {target}")


__all__ = [
    "PLACEHOLDER_CHECK_PREFIX",
    "DetectedCheck",
    "Detection",
    "InitResult",
    "build_config",
    "choose_agents",
    "detect_stack",
    "run_init",
]
