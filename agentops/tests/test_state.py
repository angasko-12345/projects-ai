import unittest

from agentops.state import StateStore
from agentops.tasks import Task, TaskStatus


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.state = StateStore(":memory:")
        self.workflow_id = self.state.create_workflow("test workflow")

    def tearDown(self):
        self.state.close()

    def test_ready_tasks_follow_dependencies(self):
        first = self.state.add_task(Task("first", "implementation", self.workflow_id))
        second = self.state.add_task(Task("second", "review", self.workflow_id, dependencies=(first.id,)))
        self.assertEqual([task.id for task in self.state.ready_tasks(self.workflow_id)], [first.id])
        first.status = TaskStatus.PASSED
        self.state.update_task(first)
        self.assertEqual([task.id for task in self.state.ready_tasks(self.workflow_id)], [second.id])

    def test_failed_dependency_blocks_dependent_task(self):
        first = self.state.add_task(Task("first", "implementation", self.workflow_id))
        second = self.state.add_task(Task("second", "review", self.workflow_id, dependencies=(first.id,)))
        first.status = TaskStatus.FAILED
        self.state.update_task(first)
        self.assertEqual(self.state.ready_tasks(self.workflow_id), [])
        self.assertEqual(self.state.get_task(second.id).status, TaskStatus.BLOCKED)

    def test_events_list_in_insertion_order(self):
        self.state.event(self.workflow_id, None, "first", "one")
        self.state.event(self.workflow_id, None, "second", "two")
        kinds = [row["kind"] for row in self.state.list_events(self.workflow_id)]
        self.assertEqual(kinds, ["first", "second"])
        # Phase 1: legacy events are plain dicts, never sqlite3.Row.
        self.assertIsInstance(self.state.list_events(self.workflow_id)[0], dict)

    def test_workflows_list_newest_first_with_pagination(self):
        second = self.state.create_workflow("second workflow")
        third = self.state.create_workflow("third workflow")
        self.assertEqual(self.state.count_workflows(), 3)
        fetched = self.state.get_workflow(second)
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched.id, second)
        self.assertEqual(fetched.description, "second workflow")
        self.assertIsNone(self.state.get_workflow("missing-workflow"))
        page = self.state.list_workflows(limit=2, offset=0)
        self.assertEqual([w.id for w in page], [third, second])
        page = self.state.list_workflows(limit=2, offset=2)
        self.assertEqual([w.id for w in page], [self.workflow_id])
        latest = self.state.latest_workflow()
        assert latest is not None
        self.assertEqual(latest.id, third)
        with self.assertRaises(ValueError):
            self.state.list_workflows(limit=-1)
