"""Safe prompt delivery to agent CLIs (upgrade U1).

AgentOps prompts are multi-line (role header, handoff block, review JSON
schema).  On POSIX they travel as one argv element, byte-exact.  On Windows
``shutil.which()`` frequently resolves an npm-installed CLI (opencode, codex,
copilot, kilo) to a ``.cmd`` shim.  Launching a batch file always goes through
``cmd.exe``; the shim forwards ``%*``, and cmd.exe ends the command line at the
first newline and re-parses ``" & | < > ^ %`` in what remains (see
multica-ai/multica#1718, NousResearch/hermes-agent#31419, and the Python
``subprocess`` documentation on batch files).  The agent then silently sees a
truncated prompt.

This module decides *how* a prompt reaches the child, in this order for a
Windows batch shim under ``prompt_transport: auto``:

1. bypass the shim: the native ``opencode.exe`` npm bundles next to it, or the
   node entry script a standard npm ``cmd-shim`` forwards to (run with
   ``node.exe`` directly, so argv is quoted by CreateProcess rules, not cmd);
2. argv through the shim, only when the prompt contains no CR/LF and no cmd.exe
   metacharacter (it would arrive unchanged);
3. stdin, when the agent declares support (``stdin_args``);
4. a prompt file, when the agent's args carry ``{prompt_file}``;
5. otherwise refuse with :class:`PromptTransportError`.  Never truncate.

Leaf module: pure decisions plus small filesystem reads through injectable
seams (``exists``, ``read_text``, ``which``) so the Windows matrix is testable
on Linux.  No subprocess, SQLite, or GUI I/O.
"""

from __future__ import annotations

import ntpath
import os
import platform as _platform_module
import posixpath
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class PromptTransport(StrEnum):
    AUTO = "auto"
    ARGV = "argv"
    STDIN = "stdin"
    FILE = "file"


PROMPT_TRANSPORTS: frozenset[str] = frozenset(item.value for item in PromptTransport)

PROMPT_PLACEHOLDER = "{prompt}"
PROMPT_FILE_PLACEHOLDER = "{prompt_file}"

#: Characters cmd.exe interprets (or truncates at) while forwarding ``%*``.
CMD_UNSAFE_CHARACTERS: frozenset[str] = frozenset('\r\n"&|<>^%')

_BATCH_SUFFIXES = (".cmd", ".bat")
_EXE_SUFFIXES = (".exe", ".com")
_NODE_SCRIPT_SUFFIXES = ("", ".js", ".cjs", ".mjs")

# Standard npm cmd-shim forwarding line, old and new styles:
#   "%_prog%"  "%dp0%\node_modules\opencode-ai\bin\opencode" %*
#   "%~dp0\node.exe"  "%~dp0\node_modules\pkg\cli.js" %*
#   "%dp0%\node_modules\pkg\bin\tool.exe"   %*
_SHIM_TARGET = re.compile(r'"%~?dp0%?\\([^"%]+)"\s+%\*')


class PromptTransportError(RuntimeError):
    """The prompt cannot reach this agent intact; nothing was spawned.

    The message deliberately reads as an environment problem for the failure
    classifier ("cannot spawn ... executable"), names the agent, the resolved
    executable, and the configuration fix.
    """


@dataclass(frozen=True)
class PromptDelivery:
    """How one prompt reaches one child process."""

    argv: tuple[str, ...]
    transport: str
    executable: str
    executable_kind: str
    stdin_bytes: bytes | None = None
    prompt_file: str | None = None
    #: Short, prompt-free explanation recorded with the run.
    resolution: str = "direct"

    def metadata(self) -> dict[str, object]:
        return {
            "prompt_transport": self.transport,
            "executable_kind": self.executable_kind,
            "executable_resolution": self.resolution,
        }


def executable_kind(executable: str) -> str:
    """``cmd`` for batch shims, ``exe`` for native binaries, else ``script``.

    An extension-less path (the POSIX norm) is executed by the OS loader and
    counts as ``exe``; ``.ps1``/``.js``/``.py`` and friends are ``script``.
    """
    suffix = _suffix(executable)
    if suffix in _BATCH_SUFFIXES:
        return "cmd"
    if suffix in _EXE_SUFFIXES or suffix == "":
        return "exe"
    return "script"


def is_cmd_safe(text: str) -> bool:
    """True when cmd.exe would forward ``text`` through ``%*`` unchanged."""
    return not any(character in CMD_UNSAFE_CHARACTERS for character in text)


def _suffix(executable: str) -> str:
    name = ntpath.basename(executable) if ("\\" in executable or ":" in executable) else posixpath.basename(executable)
    _, dot, extension = name.rpartition(".")
    return f".{extension.lower()}" if dot and _ else ""


def _windows_arch_candidates(machine: str) -> tuple[str, ...]:
    lowered = machine.lower()
    if lowered in {"arm64", "aarch64"}:
        # Windows on ARM runs x64 binaries under emulation as a fallback.
        return ("arm64", "x64", "x64-baseline")
    return ("x64", "x64-baseline")


def resolve_opencode_native(
    shim: str,
    *,
    exists: Callable[[str], bool],
    machine: str,
) -> str | None:
    """The native ``opencode.exe`` npm installs next to ``opencode.cmd``.

    Layout (opencode-ai npm package, cf. multica-ai/multica#1718)::

        <prefix>\\opencode.cmd
        <prefix>\\node_modules\\opencode-ai\\node_modules\\opencode-windows-<arch>\\bin\\opencode.exe
    """
    base = ntpath.dirname(shim)
    for arch in _windows_arch_candidates(machine):
        candidate = ntpath.join(
            base, "node_modules", "opencode-ai", "node_modules",
            f"opencode-windows-{arch}", "bin", "opencode.exe",
        )
        if exists(candidate):
            return candidate
    return None


def resolve_npm_shim(
    shim: str,
    *,
    exists: Callable[[str], bool],
    read_text: Callable[[str], str],
    which: Callable[[str], str | None],
) -> tuple[str, ...] | None:
    """The argv prefix a standard npm ``cmd-shim`` would run, bypassing cmd.

    Returns ``(node.exe, <entry script>)`` or ``(<target>.exe,)``, or ``None``
    when the shim is not a recognised npm shim or its target is missing.
    """
    try:
        text = read_text(shim)
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    matches = _SHIM_TARGET.findall(text)
    if not matches:
        return None
    base = ntpath.dirname(shim)
    target = ntpath.normpath(ntpath.join(base, matches[-1]))
    if not exists(target):
        return None
    suffix = _suffix(target)
    if suffix in _EXE_SUFFIXES:
        return (target,)
    if suffix not in _NODE_SCRIPT_SUFFIXES:
        return None
    local_node = ntpath.join(base, "node.exe")
    node = local_node if exists(local_node) else which("node")
    # A node that is itself a batch shim (nvm-windows/volta style wrappers)
    # would reintroduce cmd.exe; only a native node binary bypasses it.
    if not node or executable_kind(node) != "exe":
        return None
    return (node, target)


def _substitute(args: tuple[str, ...], placeholder: str, value: str) -> tuple[str, ...]:
    return tuple(argument.replace(placeholder, value) for argument in args)


def _fix_hint(name: str) -> str:
    return (
        f"Fix: set agents.{name}.prompt_transport to \"stdin\" (with agents.{name}.stdin_args "
        f"if the CLI needs a marker such as \"-\"), or to \"file\" with a "
        f"{PROMPT_FILE_PLACEHOLDER} placeholder in agents.{name}.args, or point "
        f"agents.{name}.command at the native .exe instead of the shim."
    )


def _refuse(name: str, executable: str, why: str) -> PromptTransportError:
    return PromptTransportError(
        f"Prompt transport unavailable: cannot spawn agent '{name}' via executable "
        f"{executable} without corrupting the prompt: {why} cmd.exe ends a batch "
        f"shim's command line at the first newline and re-parses \" & | < > ^ %, so "
        f"the agent would receive a truncated prompt. {_fix_hint(name)}"
    )


def prepare_prompt_delivery(
    *,
    name: str,
    executable: str,
    args: tuple[str, ...],
    prompt: str,
    transport: str = PromptTransport.AUTO,
    stdin_args: tuple[str, ...] | None = None,
    prompt_file: str | None = None,
    platform: str | None = None,
    machine: str | None = None,
    exists: Callable[[str], bool] | None = None,
    read_text: Callable[[str], str] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> PromptDelivery:
    """Decide argv/stdin/file delivery for one prompt; raise rather than truncate.

    ``prompt_file`` is the path the caller will write the prompt to if the
    file transport is chosen (it is only substituted, never written here).
    """
    current = platform or sys.platform
    exists = exists or os.path.isfile
    read_text = read_text or (lambda path: Path(path).read_text(encoding="utf-8", errors="strict"))
    which = which or shutil.which
    machine = machine if machine is not None else _platform_module.machine()
    requested = PromptTransport(transport)
    kind = executable_kind(executable)
    stdin_declared = stdin_args is not None
    file_declared = any(PROMPT_FILE_PLACEHOLDER in argument for argument in args)

    def argv_delivery(prefix: tuple[str, ...], resolution: str) -> PromptDelivery:
        return PromptDelivery(
            argv=(*prefix, *_substitute(args, PROMPT_PLACEHOLDER, prompt)),
            transport=PromptTransport.ARGV, executable=prefix[0],
            executable_kind=executable_kind(prefix[0]), resolution=resolution,
        )

    def stdin_delivery(prefix: tuple[str, ...], resolution: str) -> PromptDelivery:
        selected = stdin_args if stdin_args is not None else args
        if any(PROMPT_PLACEHOLDER in argument for argument in selected):
            raise ValueError(f"agents.{name}: stdin transport arguments must not contain {PROMPT_PLACEHOLDER}.")
        return PromptDelivery(
            argv=(*prefix, *selected),
            transport=PromptTransport.STDIN, executable=prefix[0],
            executable_kind=executable_kind(prefix[0]),
            stdin_bytes=prompt.encode("utf-8"), resolution=resolution,
        )

    def file_delivery(prefix: tuple[str, ...], resolution: str) -> PromptDelivery:
        if prompt_file is None:
            raise ValueError("file transport needs a prompt_file path from the caller.")
        return PromptDelivery(
            argv=(*prefix, *_substitute(args, PROMPT_FILE_PLACEHOLDER, prompt_file)),
            transport=PromptTransport.FILE, executable=prefix[0],
            executable_kind=executable_kind(prefix[0]),
            prompt_file=prompt_file, resolution=resolution,
        )

    batch_shim = current == "win32" and kind == "cmd"
    prompt_in_args = any(PROMPT_PLACEHOLDER in argument for argument in args)
    if requested is PromptTransport.AUTO and file_declared and not prompt_in_args:
        # The agent was configured to read its prompt from a file only.
        requested = PromptTransport.FILE

    if requested is PromptTransport.STDIN:
        # stdin args never carry the prompt, so the shim forwards them intact
        # (checked anyway: a metacharacter in a configured arg is still unsafe).
        delivery = stdin_delivery((executable,), "direct")
        if batch_shim and not all(is_cmd_safe(item) for item in delivery.argv[1:]):
            raise _refuse(name, executable, "a configured stdin argument contains a cmd.exe metacharacter.")
        return delivery
    if requested is PromptTransport.FILE:
        delivery = file_delivery((executable,), "direct")
        if batch_shim and not all(is_cmd_safe(item) for item in delivery.argv[1:]):
            raise _refuse(name, executable, "the prompt file path or an argument contains a cmd.exe metacharacter.")
        return delivery

    if not batch_shim:
        # POSIX, or a native/script executable on Windows: argv as before.
        return argv_delivery((executable,), "direct")

    # Windows batch shim, transport auto or argv: try to bypass cmd.exe first.
    if ntpath.splitext(ntpath.basename(executable))[0].lower() == "opencode":
        native = resolve_opencode_native(executable, exists=exists, machine=machine)
        if native is not None:
            return argv_delivery((native,), "opencode_native_exe")
    resolved = resolve_npm_shim(executable, exists=exists, read_text=read_text, which=which)
    if resolved is not None:
        return argv_delivery(resolved, "npm_shim_node_entry" if len(resolved) == 2 else "npm_shim_exe")

    through_shim = argv_delivery((executable,), "batch_shim")
    if all(is_cmd_safe(item) for item in through_shim.argv[1:]):
        return through_shim
    if requested is PromptTransport.ARGV:
        raise _refuse(
            name, executable,
            "prompt_transport is \"argv\", the shim could not be resolved to a native "
            "entry point, and the prompt contains a newline or a cmd.exe metacharacter.",
        )
    if stdin_declared:
        return stdin_delivery((executable,), "batch_shim")
    if file_declared and prompt_file is not None:
        return file_delivery((executable,), "batch_shim")
    raise _refuse(
        name, executable,
        "the shim could not be resolved to a native entry point, the prompt contains a "
        "newline or a cmd.exe metacharacter, and the agent declares neither stdin_args "
        f"nor a {PROMPT_FILE_PLACEHOLDER} argument.",
    )


__all__ = [
    "CMD_UNSAFE_CHARACTERS",
    "PROMPT_FILE_PLACEHOLDER",
    "PROMPT_PLACEHOLDER",
    "PROMPT_TRANSPORTS",
    "PromptDelivery",
    "PromptTransport",
    "PromptTransportError",
    "executable_kind",
    "is_cmd_safe",
    "prepare_prompt_delivery",
    "resolve_npm_shim",
    "resolve_opencode_native",
]
