"""First-class workflow artifacts for AgentOps.

Large file content lives outside SQLite under a contained store root;
SQLite (see :mod:`state`) holds only metadata.  Every artifact carries a
SHA-256 content hash verified on read.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from .logging import redact_text as redact_store_text
from .tasks import utc_now


class ArtifactKind(StrEnum):
    """Supported artifact families."""

    PLANS = "plans"
    DIFFS = "diffs"
    PATCHES = "patches"
    EXECUTION_LOGS = "execution_logs"
    VERIFICATION_REPORTS = "verification_reports"
    REVIEWS = "reviews"
    FAILURE_ANALYSIS = "failure_analysis"
    RISK_REPORTS = "risk_reports"
    WORKFLOW_SUMMARIES = "workflow_summaries"
    CUSTOM = "custom"


class ArtifactError(RuntimeError):
    """Base error for artifact store failures."""


class ArtifactMissingError(ArtifactError, FileNotFoundError):
    """Raised when the artifact file is absent from the store."""


class ArtifactIntegrityError(ArtifactError):
    """Raised when file content does not match the recorded hash."""


@dataclass(frozen=True)
class ArtifactMetadata:
    """Descriptive metadata stored alongside an artifact reference."""

    content_type: str = "application/octet-stream"
    redacted: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any] | None) -> ArtifactMetadata:
        if not isinstance(data, dict):
            return ArtifactMetadata()
        try:
            content_type = str(data.get("content_type") or "application/octet-stream")[:128]
        except Exception:
            content_type = "application/octet-stream"
        try:
            extra = dict(data["extra"]) if isinstance(data.get("extra"), dict) else {}
        except Exception:
            extra = {}
        return ArtifactMetadata(
            content_type=content_type,
            redacted=bool(data.get("redacted", False)),
            extra=extra,
        )


@dataclass(frozen=True)
class Artifact:
    """Reference to one stored workflow artifact."""

    id: str = field(default_factory=lambda: str(uuid4()))
    workflow_id: str | None = None
    task_id: str | None = None
    agent_run_id: str | None = None
    kind: ArtifactKind = ArtifactKind.CUSTOM
    name: str = "artifact"
    rel_path: str = ""
    sha256: str = ""
    size_bytes: int = 0
    metadata: ArtifactMetadata = field(default_factory=ArtifactMetadata)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value if isinstance(self.kind, ArtifactKind) else str(self.kind)
        data["metadata"] = self.metadata.to_dict() if isinstance(self.metadata, ArtifactMetadata) else {}
        return data


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value or "").strip("-") or "artifact"


def _restrict(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


class ArtifactStore:
    """Contained file store with hashing, retention, and safe permissions."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        _restrict(self.root, 0o700)
        self._lock = threading.RLock()

    def _contained(self, rel_path: str | Path) -> Path:
        candidate = self.root / Path(str(rel_path or ""))
        try:
            # resolve() follows symlinks, so a symlink pointing outside the
            # root is rejected just like a ../ traversal.
            resolved = candidate.resolve()
            resolved.relative_to(self.root.resolve())
        except (OSError, ValueError):
            raise ArtifactError(f"artifact path escapes store root: {rel_path!r}")
        return resolved

    def write(
        self,
        content: bytes | str,
        *,
        kind: ArtifactKind | str = ArtifactKind.CUSTOM,
        name: str = "artifact",
        workflow_id: str | None = None,
        task_id: str | None = None,
        agent_run_id: str | None = None,
        content_type: str | None = None,
        redact_text: bool = True,
    ) -> Artifact:
        """Store content and return its reference. Text is redacted by default.

        The recorded sha256 covers the stored (post-redaction) bytes, so it
        verifies store integrity — not equality with the caller's original.

        Crash semantics: the file is written to a temporary sibling and
        atomically renamed, so a crash never leaves a partial file behind.
        A crash between this call and the separately-committed metadata row
        can still leave an orphan file (visible via ``scan_files`` /
        ``find_orphans``); filesystem and SQLite cannot share one
        transaction, so callers should record metadata promptly and prune
        orphans on a schedule.
        """
        with self._lock:
            try:
                family = kind if isinstance(kind, ArtifactKind) else ArtifactKind(str(kind))
            except (ValueError, TypeError):
                family = ArtifactKind.CUSTOM
            if isinstance(content, str):
                text = redact_store_text(content) if redact_text else content
                payload = text.encode("utf-8", errors="replace")
                redacted = bool(redact_text)
                guessed = "text/plain; charset=utf-8"
            else:
                payload = bytes(content)
                redacted = False
                guessed = "application/octet-stream"
            digest = hashlib.sha256(payload).hexdigest()
            scope = _safe_name(workflow_id or "global")
            directory = self.root / _safe_name(family.value) / scope
            directory.mkdir(parents=True, exist_ok=True)
            _restrict(directory, 0o700)
            filename = f"{_safe_name(name)}-{uuid4().hex[:12]}"
            path = directory / filename
            staging = directory / f"{filename}.tmp-{uuid4().hex[:8]}"
            staging.write_bytes(payload)
            _restrict(staging, 0o600)
            os.replace(staging, path)
            _restrict(path, 0o600)
            try:
                rel = path.resolve().relative_to(self.root.resolve()).as_posix()
            except (OSError, ValueError):
                raise ArtifactError("stored artifact path escapes store root")
            return Artifact(
                workflow_id=workflow_id,
                task_id=task_id,
                agent_run_id=agent_run_id,
                kind=family,
                name=_safe_name(name),
                rel_path=rel,
                sha256=digest,
                size_bytes=len(payload),
                metadata=ArtifactMetadata(
                    content_type=content_type or guessed,
                    redacted=redacted,
                ),
            )

    def read(self, artifact: Artifact) -> bytes:
        """Return verified content; hash mismatch or absence raises."""
        with self._lock:
            path = self._contained(artifact.rel_path)
            try:
                payload = path.read_bytes()
            except FileNotFoundError:
                raise ArtifactMissingError(f"artifact file missing: {artifact.rel_path!r}")
            except OSError as error:
                raise ArtifactError(f"cannot read artifact {artifact.rel_path!r}: {error}")
            if artifact.sha256 and hashlib.sha256(payload).hexdigest() != artifact.sha256:
                raise ArtifactIntegrityError(f"hash mismatch for artifact {artifact.id!r}")
            return payload

    def read_text(self, artifact: Artifact, max_bytes: int = 1 << 20) -> str:
        payload = self.read(artifact)
        return payload[:max_bytes].decode("utf-8", errors="replace")

    def delete(self, artifact: Artifact) -> bool:
        """Remove the file; True when a file was removed, False when absent.

        Removes only the file; pair with ``StateStore.delete_artifact_record``
        to drop the metadata row (the CLI prune path does both).
        """
        with self._lock:
            try:
                path = self._contained(artifact.rel_path)
            except ArtifactError:
                return False
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            except OSError:
                return False
            return True

    def scan_files(self) -> list[str]:
        """List all file paths in the store, relative to the root (POSIX)."""
        with self._lock:
            try:
                root = self.root.resolve()
            except OSError:
                return []
            found: list[str] = []
            for path in sorted(root.rglob("*")):
                try:
                    if path.is_file():
                        found.append(path.relative_to(root).as_posix())
                except (OSError, ValueError):
                    continue
            return found

    def find_orphans(self, known_rel_paths: list[str] | set[str]) -> list[str]:
        """Return stored files with no metadata row (crash-window residue)."""
        known = set(known_rel_paths)
        return [path for path in self.scan_files() if path not in known]

    def prune(self, candidates: list[Artifact], *, keep_last_n: int = 0) -> list[str]:
        """Delete all but the newest `keep_last_n` artifacts. Returns deleted ids."""
        with self._lock:
            ordered = sorted(candidates, key=lambda item: (item.created_at, item.id))
            doomed = ordered[: max(0, len(ordered) - max(0, keep_last_n))]
            deleted: list[str] = []
            for artifact in doomed:
                if self.delete(artifact):
                    deleted.append(artifact.id)
            return deleted


__all__ = [
    "Artifact",
    "ArtifactError",
    "ArtifactIntegrityError",
    "ArtifactKind",
    "ArtifactMetadata",
    "ArtifactMissingError",
    "ArtifactStore",
]
