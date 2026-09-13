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
