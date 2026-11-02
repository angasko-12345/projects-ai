"""Local secret scan, security-role detection and role-scope loading."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..security import scan_many


def _is_security_role(role: object) -> bool:
    """Whether a task role is the optional security review."""
    normalized = " ".join(
        str(role or "").strip().lower().replace("_", " ").replace("-", " ").split()
    )
    return normalized in {"security", "security review"}


def _local_security_scan(working_directory: str | Path) -> tuple[list, bool]:
    """Redacted secret findings for the worktree diff.

    Returns ``(findings, completed)``. ``completed`` is False when the scan
    could not actually run -- git unavailable, a git error, a timeout, or an
    internal failure -- so a caller can tell "no findings" apart from
    "no scan" instead of reading a failed scan as a clean change. Never raises.
    """
    try:
        root = str(working_directory)
        parts: list[tuple[str, str | None]] = []
        completed = True
        try:
            diff = subprocess.run(
                ("git", "diff", "HEAD", "--", "."),
                cwd=root,
                capture_output=True,
                text=True,
                timeout=15,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if diff.returncode == 0:
                if (diff.stdout or "").strip():
                    parts.append(("diff", diff.stdout))
            else:
                completed = False
        except Exception:
            completed = False
        try:
            status = subprocess.run(
                ("git", "status", "--porcelain", "--", "."),
                cwd=root,
                capture_output=True,
                text=True,
                timeout=15,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if status.returncode == 0:
                shown = 0
                for line in (status.stdout or "").splitlines():
                    if not line.startswith("??") or shown >= 20:
                        continue
                    shown += 1
                    name = line[2:].strip().strip('"')
                    try:
                        candidate = Path(root, name)
                        if candidate.stat().st_size > 100 * 1024:
                            continue
                        parts.append(
                            (name, candidate.read_text(encoding="utf-8", errors="replace"))
                        )
                    except (OSError, ValueError):
                        continue
            else:
                completed = False
        except Exception:
            completed = False
        if not parts:
            return [], completed
        return scan_many(parts), completed
    except Exception:
        return [], False


_ROLE_SCOPE_MAX_CHARS = 4000


def _role_scope_text(role: object, working_directory: str | Path) -> str:
    """Role contract from the target repository, when present.

    Reads ``roles/<role>.md`` under the working directory (trying the raw,
    hyphenated, and underscored role spellings). Missing files and read
    errors yield ``""`` so the built-in prompt contract applies; role files
    are never required for normal operation. Separators in the role name
    are rejected outright. Never raises.
    """
    try:
        normalized = str(role or "").strip().lower()
        if not normalized:
            return ""
        variants: list[str] = []
        for variant in (normalized, normalized.replace(" ", "-"), normalized.replace(" ", "_")):
            if (
                variant
                and "/" not in variant
                and "\\" not in variant
                and variant not in {".", ".."}
            ):
                if variant not in variants:
                    variants.append(variant)
        for variant in variants:
            try:
                candidate = Path(str(working_directory), "roles", f"{variant}.md")
                text = candidate.read_text(encoding="utf-8", errors="replace").strip()
            except (OSError, ValueError):
                continue
            if text:
                return text[:_ROLE_SCOPE_MAX_CHARS]
        return ""
    except Exception:
        return ""
