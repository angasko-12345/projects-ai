"""``agentops doctor``: read-only, Windows-first readiness checks.

Upgrade U2. Answers "will ``agentops task`` work in this repository on this
machine?" before any agent is spawned:

* Python and Git present; the target is a Git repository with a commit, on a
  branch (not detached), and a clean base the merge gate will accept;
* which configuration file won (``--config`` > repo > user > bundled) and that
  it validates;
* every enabled agent CLI found on PATH, its executable kind (``exe`` /
  ``cmd`` shim / ``script``) and the prompt transport it will get under the
  U1 rules (``agentops/prompt_transport.py``), or the reason it would be
  refused; at least one usable agent for implementation and for review;
* every check of the default verification profile runnable here: the
  executable is spawnable *without a shell* (on Windows a bare ``npm`` is not,
  CreateProcess only appends ``.exe``), ``python -m <module>`` importable,
  unittest start directories and npm scripts present, and no placeholder
  check left by ``agentops init``;
* ``.agentops`` writable and systemd-run available when enabled.

Doctor never writes anything. Every external effect goes through injectable
seams (``which``, ``run``, ``platform``, ``environ``) so the Windows matrix is
tested on Linux. Status values: ``ok``, ``warn``, ``block``; any ``block``
makes the command exit 1.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import AppConfig, ConfigSource, load_config, resolve_config_source
from .init_config import PLACEHOLDER_CHECK_PREFIX
from .prompt_transport import (
    PROMPT_FILE_PLACEHOLDER,
    PromptTransportError,
    executable_kind,
    is_cmd_safe,
    prepare_prompt_delivery,
)
from .verification_kernel import _inapplicable_reason

SCHEMA = "agentops.doctor/v1"
OK, WARN, BLOCK = "ok", "warn", "block"

#: A representative AgentOps prompt: multi-line, with the characters cmd.exe
#: truncates at or re-parses. Used only to decide the transport; never sent.
SAMPLE_PROMPT = (
    'You are the implementation agent.\nReturn {"verdict": "pass"} & stop | exit > log ^ 100%\n'
)

_REVIEW_ROLES = ("implementation", "review")
_NODE_RUNNERS = {"npm", "pnpm", "yarn", "bun"}

Runner = Callable[[Sequence[str], Path | None, float], "subprocess.CompletedProcess[str]"]


@dataclass
class Finding:
    id: str
    status: str
    message: str
    fix: str | None = None


@dataclass
class AgentReport:
    name: str
    enabled: bool
    command: str
    found: bool = False
    path: str | None = None
    kind: str | None = None
    transport: str | None = None
    resolution: str | None = None
    status: str = OK
    detail: str | None = None
    roles: list[str] = field(default_factory=list)


@dataclass
class DoctorReport:
    target: str
    repository_root: str | None
    config_source: str
    config_path: str | None
    platform: str
    findings: list[Finding] = field(default_factory=list)
    agents: list[AgentReport] = field(default_factory=list)

    def add(self, id: str, status: str, message: str, fix: str | None = None) -> None:
        self.findings.append(Finding(id, status, message, fix))

    @property
    def counts(self) -> dict[str, int]:
        counts = {OK: 0, WARN: 0, BLOCK: 0}
        for item in self.findings:
            counts[item.status] += 1
        return counts

    @property
    def ok(self) -> bool:
        return self.counts[BLOCK] == 0

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "ok": self.ok,
            "target": self.target,
            "repository_root": self.repository_root,
            "platform": self.platform,
            "config": {"source": self.config_source, "path": self.config_path},
            "counts": self.counts,
            "agents": [asdict(agent) for agent in self.agents],
            "checks": [asdict(item) for item in self.findings],
        }

    def render_text(self) -> str:
        labels = {OK: "[ OK ]", WARN: "[WARN]", BLOCK: "[FAIL]"}
        lines = [f"AgentOps doctor - target: {self.target}"]
        for item in self.findings:
            lines.append(f"{labels[item.status]} {item.id}: {item.message}")
            if item.fix and item.status != OK:
                lines.append(f"       fix: {item.fix}")
        if self.agents:
            lines.append("")
            lines.append("Agents:")
            for agent in self.agents:
                if not agent.enabled:
                    state = "disabled"
                elif not agent.found:
                    state = "not on PATH"
                else:
                    state = f"{agent.kind}, transport={agent.transport} ({agent.resolution})"
                    if agent.status != OK:
                        state = f"{labels[agent.status]} {state}; {agent.detail}"
                lines.append(f"  {agent.name:<14} {agent.path or agent.command}  [{state}]")
        counts = self.counts
        verdict = "PASS" if self.ok else "FAIL"
        lines.append("")
        lines.append(
            f"Result: {verdict} - {counts[BLOCK]} blocking, {counts[WARN]} warning(s), {counts[OK]} ok"
        )
        return "\n".join(lines)


def _default_run(
    command: Sequence[str], cwd: Path | None, timeout: float
) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "check": False,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(list(command), cwd=str(cwd) if cwd else None, **kwargs)


def _suffix(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return ("." + base.rsplit(".", 1)[1].lower()) if "." in base else ""


def resolve_spawnable(
    argv0: str,
    *,
    platform: str,
    which: Callable[[str], str | None],
    cwd: Path | None = None,
) -> tuple[str | None, str | None, str | None]:
    """How the verification runtime (no shell) would resolve ``argv0``.

    Returns ``(path, problem, fix)``; ``path`` is None when it cannot spawn.
    """
    if any(separator in argv0 for separator in ("/", "\\")):
        candidate = Path(argv0)
        if not candidate.is_absolute() and cwd is not None:
            candidate = cwd / candidate
        if candidate.exists():
            return str(candidate), None, None
        return None, f"'{argv0}' does not exist", "fix the path in the check command"
    resolved = which(argv0)
    if resolved is None:
        return None, f"'{argv0}' was not found on PATH", f"install {argv0} or put it on PATH"
    if platform == "win32":
        suffix = _suffix(argv0)
        resolved_suffix = _suffix(resolved)
        if suffix == "" and resolved_suffix in (".cmd", ".bat"):
            return (
                None,
                (
                    f"'{argv0}' resolves to {resolved}, a batch shim; checks run without a shell and "
                    "CreateProcess only appends .exe, so it would fail with 'file not found'"
                ),
                f"use '{argv0}{resolved_suffix}' as the command (agentops init does this)",
            )
    return resolved, None, None


def _python_like(argv0: str) -> bool:
    stem = argv0.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe",):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem in {"python", "python3", "py"} or stem.startswith("python3.")


def _check_git(
    report: DoctorReport, target: Path, which: Callable[[str], str | None], run: Runner
) -> Path | None:
    git = which("git")
    if git is None:
        report.add(
            "git",
            BLOCK,
            "git was not found on PATH",
            "install Git for Windows (https://git-scm.com) and reopen the terminal",
        )
        return None
    try:
        version = run([git, "--version"], target, 15)
        report.add("git", OK, (version.stdout or "").strip() or f"found at {git}")
        top = run([git, "rev-parse", "--show-toplevel"], target, 15)
    except (OSError, subprocess.SubprocessError) as error:
        report.add("git", BLOCK, f"git could not run: {error}")
        return None
    if top.returncode != 0:
        report.add(
            "repository",
            BLOCK,
            f"{target} is not inside a Git repository",
            "run 'git init' and commit once, or pass --cwd <repo>",
        )
        return None
    root = Path(top.stdout.strip())
    head = run([git, "rev-parse", "--verify", "-q", "HEAD"], root, 15)
    if head.returncode != 0:
        report.add(
            "repository",
            BLOCK,
            f"{root} has no commits yet",
            "commit once: worktrees are created from HEAD",
        )
        return root
    branch = run([git, "symbolic-ref", "-q", "--short", "HEAD"], root, 15)
    if branch.returncode != 0:
        report.add(
            "repository",
            BLOCK,
            f"{root} is on a detached HEAD",
            "check out a branch: AgentOps merges verified work into the current branch",
        )
    else:
        report.add("repository", OK, f"{root} on branch {branch.stdout.strip()}")
    status = run([git, "status", "--porcelain", "--untracked-files=normal"], root, 30)
    dirty = [
        line
        for line in (status.stdout or "").splitlines()
        if line.strip() and not line[3:].strip().strip('"').startswith(".agentops")
    ]
    if status.returncode != 0:
        report.add("worktree-clean", WARN, "could not read git status")
    elif dirty:
        report.add(
            "worktree-clean",
            WARN,
            f"base worktree has {len(dirty)} uncommitted change(s); merges into a dirty base are refused",
            "commit or stash your changes before 'agentops task'",
        )
    else:
        report.add("worktree-clean", OK, "base worktree is clean")
    return root


def _check_agents(
    report: DoctorReport,
    config: AppConfig,
    *,
    platform: str,
    which: Callable[[str], str | None],
    run: Runner,
    probe: bool,
    machine: str | None,
    exists: Callable[[str], bool] | None,
    read_text: Callable[[str], str] | None,
) -> None:
    usable: set[str] = set()
    for name, agent in config.agents.items():
        entry = AgentReport(name, agent.enabled, agent.command, roles=list(agent.roles))
        report.agents.append(entry)
        if not agent.enabled:
            entry.status = "disabled"
            continue
        path = which(agent.command)
        if path is None:
            entry.status = "missing"
            continue
        entry.found, entry.path, entry.kind = True, path, executable_kind(path)
        try:
            delivery = prepare_prompt_delivery(
                name=name,
                executable=path,
                args=agent.args,
                prompt=SAMPLE_PROMPT,
                transport=agent.prompt_transport,
                stdin_args=agent.stdin_args,
                prompt_file="agentops-prompt.txt"
                if any(PROMPT_FILE_PLACEHOLDER in item for item in agent.args)
                else None,
                platform=platform,
                machine=machine,
                exists=exists,
                read_text=read_text,
                which=which,
            )
        except PromptTransportError as error:
            entry.status, entry.transport, entry.detail = WARN, "refused", str(error)
            report.add(
                f"agent:{name}",
                WARN,
                f"{path} would be refused: a multi-line prompt cannot pass through this shim intact",
                f'set agents.{name}.prompt_transport to "stdin" with stdin_args, or point '
                f"agents.{name}.command at the native .exe",
            )
            continue
        except ValueError as error:
            entry.status, entry.transport, entry.detail = WARN, "invalid", str(error)
            report.add(f"agent:{name}", WARN, f"prompt transport is misconfigured: {error}")
            continue
        entry.transport, entry.resolution = str(delivery.transport), delivery.resolution
        if probe:
            command = agent.version_command or (path, "--version")
            command = tuple(item.replace("{command}", path) for item in command)
            try:
                completed = run(command, None, 15)
                healthy = completed.returncode == 0
                detail = None if healthy else f"version probe exited {completed.returncode}"
            except subprocess.TimeoutExpired:
                healthy, detail = False, "version probe timed out"
            except (OSError, subprocess.SubprocessError) as error:
                healthy, detail = False, f"version probe failed: {error}"
            if not healthy:
                entry.status, entry.detail = WARN, detail
                report.add(f"agent:{name}", WARN, f"{path}: {detail}")
                continue
        usable.add(name)
    if not any(agent.found for agent in report.agents):
        report.add(
            "agents",
            BLOCK,
            "no enabled agent CLI from the config was found on PATH",
            "install one (e.g. 'npm i -g opencode-ai') or enable/adjust agents in the config",
        )
        return
    if usable:
        report.add("agents", OK, f"{len(usable)} usable agent CLI(s): {', '.join(sorted(usable))}")
    for role in _REVIEW_ROLES:
        candidates = [
            n for n in usable if not config.agents[n].roles or role in config.agents[n].roles
        ]
        if not candidates:
            report.add(
                f"role:{role}",
                BLOCK,
                f"no usable agent can take the {role} role",
                "install or fix one of the agents listed below",
            )
    implementers = [
        n
        for n in usable
        if not config.agents[n].roles or "implementation" in config.agents[n].roles
    ]
    reviewers = [
        n for n in usable if not config.agents[n].roles or "review" in config.agents[n].roles
    ]
    if implementers and reviewers and len(set(implementers) | set(reviewers)) < 2:
        report.add(
            "review-independence",
            WARN,
            "only one usable agent: it would review its own work",
            "install a second CLI so the reviewer differs from the implementer",
        )


def _package_scripts(directory: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((directory / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    scripts = data.get("scripts") if isinstance(data, dict) else None
    return scripts if isinstance(scripts, dict) else {}


def _check_verification(
    report: DoctorReport,
    config: AppConfig,
    root: Path,
    *,
    platform: str,
    which: Callable[[str], str | None],
    run: Runner,
) -> None:
    profile_name = config.default_verification_profile
    if not profile_name:
        if config.verification_commands:
            report.add(
                "verification",
                WARN,
                "no verification profile; only legacy verification.commands",
                "run 'agentops init' to write a profile for this repository",
            )
        else:
            report.add(
                "verification",
                BLOCK,
                "no verification configured: nothing could ever mark work verified",
                "run 'agentops init'",
            )
        return
    profile = config.verification_profiles[profile_name]
    report.add(
        "verification", OK, f"default profile '{profile_name}' with {len(profile.checks)} check(s)"
    )
    for spec in profile.checks:
        severity = BLOCK if spec.required else WARN
        check_id = f"check:{spec.name}"
        if spec.name.startswith(PLACEHOLDER_CHECK_PREFIX):
            report.add(
                check_id,
                BLOCK,
                "placeholder check written by 'agentops init': no verification configured",
                "edit the config and replace it with this repository's test command",
            )
            continue
        directory = (root / spec.working_directory).resolve() if spec.working_directory else root
        if not directory.is_dir():
            report.add(
                check_id,
                severity,
                f"working_directory '{spec.working_directory}' does not exist in {root}",
                "remove working_directory or point it at a real folder (run 'agentops init')",
            )
            continue
        argv0 = spec.command[0]
        path, problem, fix = resolve_spawnable(argv0, platform=platform, which=which, cwd=directory)
        if path is None:
            report.add(check_id, severity, f"{' '.join(spec.command)}: {problem}", fix)
            continue
        if (
            platform == "win32"
            and _suffix(path) in (".cmd", ".bat")
            and not all(is_cmd_safe(item) for item in spec.command[1:])
        ):
            report.add(
                check_id,
                severity,
                "arguments contain cmd.exe metacharacters but run through a batch shim",
                "simplify the arguments or call the native executable",
            )
            continue
        if platform == "win32" and "\\windowsapps\\" in path.lower() and _python_like(argv0):
            report.add(
                check_id,
                WARN,
                f"{argv0} resolves to {path}, possibly the Microsoft Store alias",
                "install Python from python.org or disable the App execution alias",
            )
        inapplicable = _inapplicable_reason(spec.command, directory)
        if inapplicable:
            report.add(
                check_id,
                severity,
                f"would be skipped as inapplicable ({inapplicable}); a skipped required check never PASSES",
                "run 'agentops init' to detect this repository's tests",
            )
            continue
        if _python_like(argv0) and len(spec.command) > 2 and spec.command[1] == "-m":
            module = spec.command[2]
            probe = (
                "import importlib.util, sys; "
                "sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 3)"
            )
            try:
                completed = run([path, "-c", probe, module], directory, 30)
            except (OSError, subprocess.SubprocessError) as error:
                report.add(check_id, severity, f"could not run {path}: {error}")
                continue
            if completed.returncode != 0:
                report.add(
                    check_id,
                    severity,
                    f"{argv0} cannot import module '{module}'",
                    f"install it in that interpreter: {argv0} -m pip install {module}",
                )
                continue
        base_name = argv0.replace("\\", "/").rsplit("/", 1)[-1]
        runner = base_name.rsplit(".", 1)[0] if _suffix(base_name) else base_name
        if runner.lower() in _NODE_RUNNERS and len(spec.command) > 1:
            script = (
                spec.command[2]
                if spec.command[1] == "run" and len(spec.command) > 2
                else spec.command[1]
            )
            scripts = _package_scripts(directory)
            if scripts is None:
                report.add(check_id, severity, f"no readable package.json in {directory}")
                continue
            if script in {"test", "lint", "typecheck", "build"} or spec.command[1] == "run":
                if script not in scripts:
                    report.add(
                        check_id,
                        severity,
                        f"package.json has no '{script}' script",
                        "add the script or remove the check",
                    )
                    continue
            if not (directory / "node_modules").is_dir():
                report.add(
                    check_id,
                    WARN,
                    f"{directory} has no node_modules; '{' '.join(spec.command)}' will likely fail",
                    f"run '{runner} install' first",
                )
                continue
        report.add(check_id, OK, f"{' '.join(spec.command)} -> {path}")


def run_doctor(
    target: str | Path | None = None,
    explicit_config: str | Path | None = None,
    *,
    platform: str | None = None,
    which: Callable[[str], str | None] | None = None,
    run: Runner | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    probe: bool = False,
    machine: str | None = None,
    exists: Callable[[str], bool] | None = None,
    read_text: Callable[[str], str] | None = None,
) -> DoctorReport:
    platform = platform or sys.platform
    which = which or shutil.which
    run = run or _default_run
    target_path = Path(target) if target is not None else Path.cwd()
    try:
        target_path = target_path.resolve()
    except OSError:
        pass
    source: ConfigSource = resolve_config_source(
        explicit_config, target_path, platform=platform, environ=environ, home=home
    )
    report = DoctorReport(
        str(target_path), None, source.source, str(source.path) if source.path else None, platform
    )
    version = sys.version_info
    if version >= (3, 11):
        report.add("python", OK, f"Python {version.major}.{version.minor}.{version.micro}")
    else:
        report.add(
            "python",
            BLOCK,
            f"Python {version.major}.{version.minor} is too old",
            "use Python 3.11+",
        )
    root = _check_git(report, target_path, which, run)
    report.repository_root = str(root) if root else None
    try:
        config = load_config(source.path)
    except Exception as error:  # noqa: BLE001 - PyYAML errors are not ValueError
        report.add(
            "config",
            BLOCK,
            f"{source.describe()} is invalid: {error}",
            "fix the file, or regenerate it with 'agentops init --force'",
        )
        return report
    report.add("config", OK, f"using {source.describe()}")
    if source.source == "bundled" and root is not None:
        report.add(
            "config-scope",
            WARN,
            "no repository config: the neutral bundled defaults apply",
            "run 'agentops init' to write checks for this repository",
        )
    _check_agents(
        report,
        config,
        platform=platform,
        which=which,
        run=run,
        probe=probe,
        machine=machine,
        exists=exists,
        read_text=read_text,
    )
    if root is not None:
        _check_verification(report, config, root, platform=platform, which=which, run=run)
        state = root / ".agentops"
        writable_target = state if state.exists() else root
        if os.access(writable_target, os.W_OK):
            report.add("state-dir", OK, f"{state} is writable")
        else:
            report.add(
                "state-dir", BLOCK, f"{writable_target} is not writable", "fix folder permissions"
            )
    if config.systemd_run_enabled:
        if platform != "linux":
            report.add("systemd", OK, "systemd_run_enabled is ignored on this platform")
        elif which("systemd-run") is None:
            report.add(
                "systemd",
                BLOCK,
                "systemd_run_enabled is true but systemd-run is not installed",
                "set runtime.systemd_run_enabled to false",
            )
        else:
            report.add("systemd", OK, "systemd-run found (scope support is probed at spawn time)")
    return report


__all__ = ["SCHEMA", "AgentReport", "DoctorReport", "Finding", "resolve_spawnable", "run_doctor"]
