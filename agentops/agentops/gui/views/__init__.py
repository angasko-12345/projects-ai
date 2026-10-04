"""View registry: canonical navigation order and construction.

``VIEW_SPECS`` order defines the sidebar, ``Ctrl+1..0`` shortcuts, and the
command palette ordering. Agents never mutate this file; register new views
by appending a spec here.
"""

from __future__ import annotations

from .agents import AgentsView
from .artifacts import ArtifactsView
from .base import ViewContext
from .dashboard import DashboardView
from .failures import FailuresView
from .runs import RunsView
from .settings import SettingsView
from .tasks import TasksView
from .verification import VerificationView
from .worktrees import WorktreesView
from .workflows import WorkflowsView

VIEW_SPECS: tuple[tuple[str, str, type], ...] = (
    ("dashboard", "Dashboard", DashboardView),
    ("tasks", "Tasks", TasksView),
    ("workflows", "Workflows", WorkflowsView),
    ("agents", "Agents", AgentsView),
    ("runs", "Runs", RunsView),
    ("verification", "Verification", VerificationView),
    ("failures", "Failures", FailuresView),
    ("worktrees", "Worktrees", WorktreesView),
    ("artifacts", "Artifacts", ArtifactsView),
    ("settings", "Settings", SettingsView),
)


def create_views(context: ViewContext) -> dict[str, object]:
    """Instantiate every registered view against one shared context."""
    return {
        view_id: factory(context)  # type: ignore[operator]
        for view_id, _title, factory in VIEW_SPECS
    }


__all__ = ["VIEW_SPECS", "create_views"]
