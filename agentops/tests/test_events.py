"""Tests for the versioned event timeline, queries, and subscriptions."""

from __future__ import annotations

import threading
import unittest

from agentops.events import (
    EVENT_SCHEMA_VERSION,
    Event,
    EventBus,
    EventSeverity,
    EventType,
    coerce_event,
    event_from_dict,
)
from agentops.state import StateStore


class EventSchemaTests(unittest.TestCase):
    def test_all_spec_types_exist(self):
        expected = {
            "workflow.created", "workflow.started", "task.claimed", "task.started",
            "task.completed", "task.failed", "agent.selected", "agent.started",
            "agent.finished", "verification.started", "verification.completed",
            "verification.failed", "failure.classified", "repair.started",
            "repair.completed", "review.started", "review.completed",
            "policy.evaluated", "approval.requested", "approval.completed",
            "worktree.created", "worktree.cleaned", "merge.started",
            "merge.completed", "merge.conflict", "workflow.cancelled",
        }
        self.assertTrue(expected.issubset({member.value for member in EventType}))

    def test_round_trip(self):
        event = Event(
            workflow_id="w", task_id="t", agent_run_id="r",
            type=EventType.TASK_FAILED, severity=EventSeverity.ERROR,
            message="boom", payload={"exit": 1},
        )
        restored = coerce_event(event.to_dict())
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.type, EventType.TASK_FAILED)
        self.assertEqual(restored.payload, {"exit": 1})
        self.assertEqual(restored.schema_version, EVENT_SCHEMA_VERSION)

    def test_unknown_version_coerced(self):
        event, warnings = event_from_dict({"type": "task.failed", "schema_version": 99})
        self.assertEqual(event.schema_version, EVENT_SCHEMA_VERSION)
        self.assertTrue(any("schema_version" in warning for warning in warnings))

    def test_legacy_kind_becomes_note(self):
        event, warnings = event_from_dict({"type": "task-claimed", "message": "x"})
        self.assertEqual(event.type, EventType.NOTE)
        self.assertEqual(event.payload.get("legacy_type"), "task-claimed")
        self.assertTrue(warnings)

    def test_json_text_loads(self):
        event = Event(workflow_id="w", type=EventType.MERGE_CONFLICT)
        restored = coerce_event(__import__("json").dumps(event.to_dict()))
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.type, EventType.MERGE_CONFLICT)

    def test_garbage_never_raises(self):
        self.assertIsNone(coerce_event(None))
        self.assertIsNone(coerce_event("   "))
        self.assertIsNone(coerce_event("not json"))
        self.assertIsNone(coerce_event(12345))
        event, _ = event_from_dict(12345)  # type: ignore[arg-type]
        self.assertEqual(event.type, EventType.NOTE)

    def test_no_secrets_by_construction(self):
        event = Event(
            type=EventType.AGENT_FINISHED,
            message="done",
            payload={"exit_code": 0, "files": ["a.py"]},
        )
        blob = str(event.to_dict())
        for token in ("sk-", "ghp_", "password", "api_key", "secret"):
            self.assertNotIn(token, blob)


class EventBusTests(unittest.TestCase):
    def test_emit_without_subscribers(self):
        self.assertEqual(EventBus().emit(Event()), 0)

    def test_subscribe_and_filter(self):
        bus = EventBus()
        seen: list[Event] = []
        bus.subscribe(seen.append)
        failures: list[Event] = []
        bus.subscribe(failures.append, [EventType.TASK_FAILED])
        bus.emit(Event(type=EventType.TASK_STARTED))
        bus.emit(Event(type=EventType.TASK_FAILED))
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(failures), 1)

    def test_unsubscribe(self):
        bus = EventBus()
        seen: list[Event] = []
        key = bus.subscribe(seen.append)
        self.assertTrue(bus.unsubscribe(key))
        self.assertFalse(bus.unsubscribe(key))
        bus.emit(Event())
        self.assertEqual(seen, [])

    def test_failing_subscriber_isolated(self):
        def bad(_event: Event) -> None:
            raise RuntimeError("boom")

        bus = EventBus()
        seen: list[Event] = []
        bus.subscribe(bad)
        bus.subscribe(seen.append)
        self.assertEqual(bus.emit(Event()), 1)
        self.assertEqual(len(seen), 1)

    def test_error_handler_called(self):
        reported: list[BaseException] = []
        bus = EventBus(error_handler=reported.append)

        def bad(_event: Event) -> None:
            raise RuntimeError("boom")

        bus.subscribe(bad)
        self.assertEqual(bus.emit(Event()), 0)
        self.assertEqual(len(reported), 1)

    def test_reentrant_emit_drained(self):
        bus = EventBus()
        seen: list[Event] = []

        def nested(event: Event) -> None:
            seen.append(event)
            if event.message != "inner":
                bus.emit(Event(message="inner"))

        bus.subscribe(nested)
        self.assertEqual(bus.emit(Event(message="outer")), 2)
        self.assertEqual([event.message for event in seen], ["outer", "inner"])


class EventStoreTests(unittest.TestCase):
    def setUp(self):
        self.state = StateStore(":memory:")

    def tearDown(self):
        self.state.close()

    def test_record_and_query(self):
        event = self.state.record_typed_event(Event(
            workflow_id="w", task_id="t", type=EventType.TASK_FAILED,
            severity=EventSeverity.ERROR, message="x", payload={"k": "v"},
        ))
        found = self.state.query_events(workflow_id="w")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].id, event.id)
        self.assertEqual(found[0].payload, {"k": "v"})

    def test_chronological_pagination_and_filters(self):
        for index in range(5):
            self.state.record_typed_event(Event(
                workflow_id="w", task_id="t1" if index % 2 else "t2",
                agent_run_id="r1" if index < 3 else "r2",
                type=EventType.TASK_STARTED if index % 2 else EventType.TASK_FAILED,
            ))
        self.assertEqual(len(self.state.query_events(workflow_id="w")), 5)
        self.assertEqual(len(self.state.query_events(task_id="t1")), 2)
        self.assertEqual(len(self.state.query_events(agent_run_id="r2")), 2)
        self.assertEqual(
            len(self.state.query_events(event_type=EventType.TASK_FAILED)), 3
        )
        page = self.state.query_events(workflow_id="w", limit=2, offset=2)
        self.assertEqual(len(page), 2)
        stamps = [event.timestamp for event in self.state.query_events(workflow_id="w")]
        self.assertEqual(stamps, sorted(stamps))

    def test_bus_notified_on_write(self):
        bus = EventBus()
        seen: list[Event] = []
        bus.subscribe(seen.append)
        state = StateStore(":memory:", event_bus=bus)
        try:
            state.record_typed_event(Event(workflow_id="w", type=EventType.WORKFLOW_STARTED))
            state.event("w", None, "legacy-kind", "detail")
        finally:
            state.close()
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1].type, EventType.NOTE)

    def test_legacy_events_still_work(self):
        self.state.event("w", "t", "task-claimed", "detail")
        rows = self.state.list_events("w")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "task-claimed")

    def test_typed_events_mirror_into_legacy_table(self):
        self.state.record_typed_event(Event(
            workflow_id="w", task_id="t",
            type=EventType.TASK_FAILED, message="boom",
        ))
        rows = self.state.list_events("w")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "task.failed")
        self.assertEqual(rows[0]["detail"], "boom")
        # Global events (no workflow) cannot mirror into the NOT NULL
        # legacy column but must still persist in the typed timeline.
        self.state.record_typed_event(Event(type=EventType.NOTE, message="g"))
        self.assertEqual(len(self.state.query_events()), 2)
        self.assertEqual(len(self.state.list_events("w")), 1)

    def test_invalid_pagination_raises(self):
        for bad in ("x", -1, None):
            with self.assertRaises(ValueError):
                self.state.query_events(limit=bad)  # type: ignore[arg-type]
            with self.assertRaises(ValueError):
                self.state.query_events(offset=bad)  # type: ignore[arg-type]

    def test_nonserializable_payload_coerced(self):
        class Hostile:
            def __repr__(self) -> str:
                raise RuntimeError("boom")

        event = self.state.record_typed_event(Event(
            workflow_id="w",
            type=EventType.TASK_FAILED,
            payload={"items": {1, 2}, "blob": b"x", "obj": Hostile()},  # type: ignore[dict-item]
        ))
        found = self.state.query_events(workflow_id="w")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].id, event.id)
        self.assertIsInstance(found[0].payload, dict)

    def test_concurrent_migration_single_version(self):
        import tempfile
        from pathlib import Path as _Path

        errors: list[Exception] = []

        with tempfile.TemporaryDirectory() as directory:
            path = str(_Path(directory) / "state.sqlite")

            def opener() -> None:
                import traceback as _traceback

                store = None
                try:
                    store = StateStore(path)
                    store.query_events(limit=1)
                except Exception as error:  # pragma: no cover
                    errors.append(f"{error!r}\n{_traceback.format_exc()[-1500:]}")
                finally:
                    try:
                        if store is not None:
                            store.close()
                    except Exception:  # pragma: no cover
                        pass

            threads = [threading.Thread(target=opener) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            store = StateStore(path)
            try:
                versions = [
                    row[0]
                    for row in store.connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall()
                ]
            finally:
                store.close()
            self.assertEqual(versions, [1, 2, 3, 4, 5])


if __name__ == "__main__":
    unittest.main()
