"""Bounded, deterministic repository inspection for routing hints.

Only file and directory names are inspected, plus a small allowlist of root
manifests whose contents are parsed with size caps.  Credential files such as
``.env`` are never opened.  The result is a leaf value consumed by
:class:`agentops.routing.AgentCapabilityResolver` as desired-capability hints;
nothing here performs SQLite, subprocess, or GUI I/O.
"""

from __future__ import annotations

import json
import os
import stat
import threading
import tomllib
from collections import OrderedDict, deque
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

#: Only these root-level files are ever opened, and each read stops at
#: ``max_manifest_bytes``.  Everything else is inspected by name only.
MANIFEST_NAMES: frozenset[str] = frozenset({
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "meson.build",
    "CMakeLists.txt",
    "Makefile",
    "tox.ini",
    "pytest.ini",
})

_SOURCE_LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".hh": "C++",
    ".scala": "Scala",
    ".sh": "Shell",
}

# Dependency/vendor directories add thousands of irrelevant entries; a hidden
# (dot-prefixed) name also skips, which covers .git, .venv, .agentops, .tox.
_SKIP_DIRECTORIES: frozenset[str] = frozenset({
    "node_modules",
    "__pycache__",
    "venv",
    ".venv",
    "site-packages",
    "target",
    "build",
    "dist",
    ".pytest_cache",
    ".mypy_cache",
    ".eggs",
})

_SIZE_SMALL_MAX = 100
_SIZE_MEDIUM_MAX = 1000

DEFAULT_MAX_FILES = 2000
DEFAULT_MAX_DEPTH = 6
DEFAULT_MANIFEST_BYTES = 65536
DEFAULT_CACHE_SIZE = 8

TEST_RUNNER_PYTEST = "pytest"
TEST_RUNNER_UNITTEST = "unittest"
TEST_RUNNER_GO = "go test"
TEST_RUNNER_CARGO = "cargo test"
TEST_RUNNER_NPM = "npm test"


class RepositorySize(StrEnum):
    EMPTY = "empty"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    UNKNOWN = "unknown"


def _is_skipped_directory(name: str) -> bool:
    return name.startswith(".") or name in _SKIP_DIRECTORIES


# O_NOFOLLOW/O_NONBLOCK do not exist on Windows; there the lstat check below
# is the guard (Windows has no FIFOs on ordinary filesystems).
_SAFE_OPEN_FLAGS = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


def _safe_opener(path: str, flags: int) -> int:
    return os.open(path, flags | _SAFE_OPEN_FLAGS)


def _read_capped(root: str, name: str, max_bytes: int) -> str:
    """Read a root manifest only when it is a regular, non-symlinked file.

    A symlink (possibly pointing outside the repository) or a FIFO/device
    named like a manifest could otherwise be followed or block forever, so
    both are refused with ``OSError`` and callers treat that as no signal.
    The post-open ``fstat`` closes the lstat/open race on POSIX.
    """
    path = os.path.join(root, name)
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise OSError(f"not a regular file: {name}")
    with open(path, "rb", opener=_safe_opener) as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError(f"not a regular file: {name}")
        return handle.read(max_bytes).decode("utf-8")


def _manifest_test_runner(
    root: str, manifest_names: tuple[str, ...], max_bytes: int
) -> str | None:
    """Read the allowlisted manifests only; unparseable content yields no signal.

    A pytest signal outranks the other runners, so the go/Cargo checks wait
    until the parsed manifests had their turn.
    """
    if "pytest.ini" in manifest_names:
        return TEST_RUNNER_PYTEST
    if "pyproject.toml" in manifest_names and _pyproject_declares_pytest(root, max_bytes):
        return TEST_RUNNER_PYTEST
    if "package.json" in manifest_names:
        try:
            scripts = json.loads(_read_capped(root, "package.json", max_bytes)).get("scripts")
        except (OSError, ValueError, UnicodeDecodeError):
            scripts = None
        if isinstance(scripts, Mapping) and scripts.get("test"):
            return TEST_RUNNER_NPM
    if "go.mod" in manifest_names:
        return TEST_RUNNER_GO
    if "Cargo.toml" in manifest_names:
        return TEST_RUNNER_CARGO
    return None


def _pyproject_declares_pytest(root: str, max_bytes: int) -> bool:
    try:
        tool = tomllib.loads(_read_capped(root, "pyproject.toml", max_bytes)).get("tool")
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    return isinstance(tool, Mapping) and bool(tool.get("pytest"))


def _observed_size(count: int, truncated: bool) -> RepositorySize:
    # A walk that hit its file cap cannot bound the true size, so it is
    # conservatively classified large rather than guessed smaller.
    if truncated:
        return RepositorySize.LARGE
    if count == 0:
        return RepositorySize.EMPTY
    if count <= _SIZE_SMALL_MAX:
        return RepositorySize.SMALL
    if count <= _SIZE_MEDIUM_MAX:
        return RepositorySize.MEDIUM
    return RepositorySize.LARGE


class RepoCharacteristics:
    """Small deterministic view of one repository tree.

    ``languages`` is ordered most common first (count descending, then name).
    """

    def __init__(
        self,
        languages: tuple[str, ...] = (),
        language_counts: Mapping[str, int] | None = None,
        manifests: tuple[str, ...] = (),
        test_runner: str | None = None,
        source_file_count: int = 0,
        size: RepositorySize | str = RepositorySize.UNKNOWN,
        truncated: bool = False,
    ):
        self.languages = tuple(languages)
        self.language_counts = {
            str(name): int(count) for name, count in (language_counts or {}).items()
        }
        self.manifests = tuple(manifests)
        self.test_runner = test_runner
        self.source_file_count = int(source_file_count)
        self.size = RepositorySize(size)
        self.truncated = bool(truncated)

    @classmethod
    def unknown(cls) -> RepoCharacteristics:
        return cls()

    def routing_flags(self) -> dict[str, object]:
        """Flags in the vocabulary ``AgentCapabilityResolver.required_capabilities`` reads.

        ``security_sensitive`` and ``documentation_required`` stay False: no
        safe deterministic signal for them exists in file names alone.
        """
        return {
            "requires_tests": self.test_runner is not None,
            "repository_exploration_required": (
                self.size is RepositorySize.LARGE or len(self.languages) >= 2
            ),
            "security_sensitive": False,
            "documentation_required": False,
        }

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RepoCharacteristics):
            return NotImplemented
        return (
            self.languages == other.languages
            and self.language_counts == other.language_counts
            and self.manifests == other.manifests
            and self.test_runner == other.test_runner
            and self.source_file_count == other.source_file_count
            and self.size == other.size
            and self.truncated == other.truncated
        )

    def __repr__(self) -> str:
        return (
            f"RepoCharacteristics(languages={self.languages!r}, "
            f"manifests={self.manifests!r}, test_runner={self.test_runner!r}, "
            f"source_file_count={self.source_file_count}, size={self.size.value!r}, "
            f"truncated={self.truncated!r})"
        )


def _list_sorted(directory: str | os.PathLike[str]) -> tuple[os.DirEntry, ...]:
    with os.scandir(directory) as iterator:
        return tuple(sorted(iterator, key=lambda entry: entry.name))


def inspect_repository(
    source: str | os.PathLike[str],
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_manifest_bytes: int = DEFAULT_MANIFEST_BYTES,
) -> RepoCharacteristics:
    """Inspect one directory tree; never raises.

    The walk is breadth-first over name-sorted entries, stops at
    ``max_depth`` directory levels and ``max_files`` files, and skips hidden
    and vendor directories entirely.
    """
    try:
        root = Path(str(source))
        queue: deque[tuple[str, tuple[os.DirEntry, ...]]] = deque()
        queue.append((str(root), _list_sorted(root)))
        return _scan_tree(
            root, queue, max_files=max_files, max_depth=max_depth,
            max_manifest_bytes=max_manifest_bytes,
        )
    except Exception:
        return RepoCharacteristics.unknown()


def _scan_tree(
    root: Path,
    queue: deque[tuple[str, tuple[os.DirEntry, ...]]],
    *,
    max_files: int,
    max_depth: int,
    max_manifest_bytes: int,
) -> RepoCharacteristics:
    language_counts: dict[str, int] = {}
    manifests: list[str] = []
    conftest_seen = False
    python_test_dir_seen = False
    counted_files = 0
    truncated = False
    root_str = str(root)
    depth_of = {root_str: 1}

    while queue:
        directory, entries = queue.popleft()
        depth = depth_of[directory]
        child_dirs: list[str] = []
        for entry in entries:
            name = entry.name
            if entry.is_dir(follow_symlinks=False):
                if depth >= max_depth or _is_skipped_directory(name):
                    continue
                child_dirs.append(entry.path)
                continue
            if counted_files >= max_files:
                truncated = True
                break
            counted_files += 1
            if directory == root_str and name in MANIFEST_NAMES:
                manifests.append(name)
            if name == "conftest.py":
                conftest_seen = True
            language = _SOURCE_LANGUAGE_BY_SUFFIX.get(os.path.splitext(name)[1].lower())
            if language is not None:
                language_counts[language] = language_counts.get(language, 0) + 1
                if depth > 1 and os.path.basename(directory).lower() in {"tests", "test"}:
                    python_test_dir_seen = True
        if truncated:
            break
        for child in child_dirs:
            try:
                depth_of[child] = depth + 1
                queue.append((child, _list_sorted(child)))
            except OSError:
                # A directory that cannot be listed just contributes nothing.
                continue

    manifest_test_runner: str | None = None
    if manifests:
        manifest_test_runner = _manifest_test_runner(root_str, tuple(manifests), max_manifest_bytes)

    if conftest_seen or manifest_test_runner == TEST_RUNNER_PYTEST:
        test_runner = TEST_RUNNER_PYTEST
    elif manifest_test_runner is not None:
        test_runner = manifest_test_runner
    elif python_test_dir_seen:
        test_runner = TEST_RUNNER_UNITTEST
    else:
        test_runner = None

    languages = tuple(
        name
        for name, _ in sorted(language_counts.items(), key=lambda item: (-item[1], item[0]))
    )
    return RepoCharacteristics(
        languages=languages,
        language_counts=language_counts,
        manifests=tuple(manifests),
        test_runner=test_runner,
        source_file_count=counted_files,
        size=_observed_size(counted_files, truncated),
        truncated=truncated,
    )


class RepositoryInspector:
    """Caches bounded inspections per resolved path, so a workflow scans once."""

    def __init__(
        self,
        *,
        max_files: int = DEFAULT_MAX_FILES,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_manifest_bytes: int = DEFAULT_MANIFEST_BYTES,
        cache_size: int = DEFAULT_CACHE_SIZE,
    ):
        self._max_files = max_files
        self._max_depth = max_depth
        self._max_manifest_bytes = max_manifest_bytes
        self._cache_size = max(1, int(cache_size))
        self._cache: OrderedDict[str, RepoCharacteristics] = OrderedDict()
        self._lock = threading.Lock()

    def characteristics(self, source: str | os.PathLike[str]) -> RepoCharacteristics:
        """Inspect one path, returning the identical cached object on repeat calls."""
        key = self._cache_key(source)
        if key is None:
            return RepoCharacteristics.unknown()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
        # The filesystem walk runs outside the lock so one slow tree cannot
        # stall every other caller.  Two concurrent misses on the same key may
        # both scan; the first stored value wins so callers still share one
        # object.
        value = inspect_repository(
            source,
            max_files=self._max_files,
            max_depth=self._max_depth,
            max_manifest_bytes=self._max_manifest_bytes,
        )
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
            self._cache[key] = value
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return value

    def routing_flags(self, source: str | os.PathLike[str]) -> dict[str, object]:
        """Never raises: routing hints must not be able to break selection."""
        return self.characteristics(source).routing_flags()

    @staticmethod
    def _cache_key(source: str | os.PathLike[str]) -> str | None:
        try:
            return str(Path(str(source)).resolve())
        except OSError:
            return None


__all__ = [
    "MANIFEST_NAMES",
    "RepositoryInspector",
    "RepoCharacteristics",
    "RepositorySize",
    "inspect_repository",
]
