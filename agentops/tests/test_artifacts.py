"""Tests for the artifact store, registry persistence, and retention."""

from __future__ import annotations

import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from agentops.artifacts import (
    Artifact,
    ArtifactError,
    ArtifactIntegrityError,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactMissingError,
    ArtifactStore,
)
from agentops.state import StateStore


class ArtifactStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ArtifactStore(Path(self.tmp.name) / "artifacts")

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_read_round_trip_with_hash(self):
        artifact = self.store.write(
            "plan text", kind=ArtifactKind.PLANS, name="plan",
            workflow_id="w", task_id="t",
        )
        self.assertEqual(artifact.kind, ArtifactKind.PLANS)
        self.assertEqual(len(artifact.sha256), 64)
        self.assertGreater(artifact.size_bytes, 0)
        self.assertEqual(self.store.read(artifact).decode(), "plan text")
        self.assertIn("plan text", self.store.read_text(artifact))

    def test_secret_redaction(self):
        artifact = self.store.write("api_key=supersecretvalue", kind=ArtifactKind.CUSTOM)
        content = self.store.read(artifact).decode()
        self.assertNotIn("supersecretvalue", content)
        self.assertIn("[REDACTED]", content)
        self.assertTrue(artifact.metadata.redacted)

    def test_traversal_rejected(self):
        artifact = self.store.write("x", name="ok")
        with self.assertRaises(ArtifactError):
            self.store.read(Artifact(
                id=artifact.id, kind=artifact.kind, name="evil",
                rel_path="../../evil", sha256=artifact.sha256,
            ))
        with self.assertRaises(ArtifactError):
            self.store.read(Artifact(
                id=artifact.id, kind=artifact.kind, name="evil",
                rel_path="/etc/passwd", sha256=artifact.sha256,
            ))

    @unittest.skipIf(os.name == "nt", "POSIX symlinks need privileges on Windows")
    def test_symlink_escape_rejected(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        link = self.store.root / "plans" / "global"
        link.mkdir(parents=True, exist_ok=True)
        (link / "link").symlink_to(outside)
        with self.assertRaises(ArtifactError):
            self.store.read(Artifact(
                id="x", kind=ArtifactKind.PLANS, name="link",
                rel_path="plans/global/link", sha256="",
            ))

    def test_crash_window_leaves_orphan_without_row(self):
        # Simulate a crash between file write and metadata insert: the
        # file exists, no DB row does, and the orphan is discoverable.
        artifact = self.store.write("orphan", name="crash", workflow_id="w")
        self.assertEqual(self.store.find_orphans([]), [artifact.rel_path])
        self.assertEqual(self.store.find_orphans([artifact.rel_path]), [])
        self.assertIn(artifact.rel_path, self.store.scan_files())

    def test_missing_and_integrity(self):
        artifact = self.store.write("x", name="gone")
        Path(self.store.root / artifact.rel_path).unlink()
        with self.assertRaises(ArtifactMissingError):
            self.store.read(artifact)
        artifact2 = self.store.write("y", name="tampered")
        Path(self.store.root / artifact2.rel_path).write_bytes(b"changed")
        with self.assertRaises(ArtifactIntegrityError):
            self.store.read(artifact2)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not enforced on Windows")
    def test_safe_permissions(self):
        artifact = self.store.write("x", name="perms")
        mode = stat.S_IMODE(os.stat(self.store.root / artifact.rel_path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_delete_and_prune(self):
        first = self.store.write("1", name="a", workflow_id="w")
        second = self.store.write("2", name="b", workflow_id="w")
        third = self.store.write("3", name="c", workflow_id="w")
        self.assertTrue(self.store.delete(first))
        self.assertFalse(self.store.delete(first))
        deleted = self.store.prune([first, second, third], keep_last_n=1)
        self.assertEqual(deleted, [second.id])

    def test_concurrent_writes(self):
        results: list[Artifact] = []
        errors: list[Exception] = []

        def worker(index: int) -> None:
            try:
                results.append(self.store.write(f"content-{index}", name=f"n-{index}"))
            except Exception as error:  # pragma: no cover
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 20)
        self.assertEqual(len({item.rel_path for item in results}), 20)

    def test_metadata_codec(self):
        metadata = ArtifactMetadata(content_type="text/plain", redacted=True, extra={"a": 1})
        restored = ArtifactMetadata.from_dict(metadata.to_dict())
        self.assertEqual(restored, metadata)
        self.assertEqual(ArtifactMetadata.from_dict(None), ArtifactMetadata())
        self.assertEqual(ArtifactMetadata.from_dict({"x": 1}).extra, {})


class ArtifactRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = StateStore(":memory:")
        self.store = ArtifactStore(Path(self.tmp.name) / "artifacts")

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def test_record_get_list_delete(self):
        artifact = self.store.write(
            "diff", kind=ArtifactKind.DIFFS, name="fix",
            workflow_id="w", task_id="t", agent_run_id="r",
        )
        self.state.create_artifact_record(artifact)
        fetched = self.state.get_artifact(artifact.id)
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched.sha256, artifact.sha256)
        self.assertEqual(fetched.metadata.content_type, "text/plain; charset=utf-8")
        self.assertEqual(len(self.state.list_artifacts(workflow_id="w")), 1)
        self.assertEqual(len(self.state.list_artifacts(task_id="t")), 1)
        self.assertEqual(len(self.state.list_artifacts(kind=ArtifactKind.DIFFS)), 1)
        self.assertEqual(len(self.state.list_artifacts(kind=ArtifactKind.PLANS)), 0)
        self.assertTrue(self.state.delete_artifact_record(artifact.id))
        self.assertIsNone(self.state.get_artifact(artifact.id))
        self.assertFalse(self.state.delete_artifact_record(artifact.id))

    def test_pagination(self):
        for index in range(5):
            item = self.store.write(f"{index}", name=f"n{index}", workflow_id="w")
            self.state.create_artifact_record(item)
        self.assertEqual(len(self.state.list_artifacts(workflow_id="w", limit=2, offset=2)), 2)
        for bad in ("x", -1, None):
            with self.assertRaises(ValueError):
                self.state.list_artifacts(limit=bad)  # type: ignore[arg-type]
            with self.assertRaises(ValueError):
                self.state.list_artifacts(offset=bad)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
