"""Qt-free presentation derivations for the workspace (thread list).

Everything here works on the plain dicts the controller already returns
(``list_workflows``, ``list_worktree_refs``, ``list_recent_agent_runs``).
Nothing touches SQLite, git or Qt, so it is unit-testable anywhere.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Settle rule (plan-v2 §3.8, presentation only): a workflow drops into the
# collapsed "Settled (n)" row once it is terminal and untouched this long.
# A constant, not a setting, until someone asks for one.
SETTLE_AFTER = timedelta(hours=24)
TERMINAL_WORKFLOW = frozenset({"passed", "failed", "cancelled"})

ROLE_LABEL = {
    "architecture": "Plan",
    "planning": "Plan",
    "implementation": "Implement",
    "verification": "Verify",
    "review": "Review",
    "debugging": "Debug",
}

# Active rows: what needs eyes first.
_ACTIVE_ORDER = {"running": 0, "failed": 1, "blocked": 2, "pending": 3}

_DIFF_SUMMARY = re.compile(
    r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?"
)


def parse_ts(value: object) -> datetime | None:
    """ISO-8601 text -> aware datetime (naive input is taken as UTC)."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def rel_age(value: object, now: datetime) -> str:
    """Compact age: ``now``, ``17m``, ``7h``, ``3d`` ("" when unknown)."""
    stamp = parse_ts(value)
    if stamp is None:
        return ""
    seconds = max(0, int((now - stamp).total_seconds()))
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def parse_diff_stat(text: object) -> tuple[int, int, int] | None:
    """The ``git diff --stat`` summary line -> (files, insertions, deletions).

    Only "N files changed, X insertions(+), Y deletions(-)" counts; per-file
    lines are ignored, and text without the summary line yields None.
    """
    if not isinstance(text, str):
        return None
    match = _DIFF_SUMMARY.search(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2) or 0), int(match.group(3) or 0)


def is_settled(workflow: dict, now: datetime, settle_after: timedelta = SETTLE_AFTER) -> bool:
    """Terminal and ``updated_at`` older than ``settle_after``."""
    if str(workflow.get("status") or "").lower() not in TERMINAL_WORKFLOW:
        return False
    updated = parse_ts(workflow.get("updated_at"))
    return updated is not None and now - updated > settle_after


def thread_items(
    workflows: Iterable[dict],
    worktree_refs: Iterable[dict],
    recent_runs: Iterable[dict],
    now: datetime,
    settle_after: timedelta = SETTLE_AFTER,
) -> tuple[list[dict], list[dict]]:
    """Thread-list rows as ``(active, settled)``.

    Joins the branch from the worktree refs and agent/role from the newest
    run of each workflow (``recent_runs`` is newest first), and adds
    ``progress`` as "passed/total" tasks. Every value traces to a payload
    field; a missing one stays empty for the delegate to word.
    """
    branch: dict[str, str] = {}
    for ref in worktree_refs:
        workflow_id = str(ref.get("workflow_id") or "")
        if workflow_id and ref.get("branch") and workflow_id not in branch:
            branch[workflow_id] = str(ref["branch"])
    newest: dict[str, dict] = {}
    for run in recent_runs:
        newest.setdefault(str(run.get("workflow_id") or ""), run)
    active: list[dict] = []
    settled: list[dict] = []
    for workflow in workflows:
        workflow_id = str(workflow.get("id") or "")
        if not workflow_id:
            continue
        run = newest.get(workflow_id, {})
        role = str(run.get("role") or "")
        counts = workflow.get("task_counts") or {}
        item = {
            "id": workflow_id,
            "title": str(workflow.get("description") or ""),
            "status": str(workflow.get("status") or "pending").lower(),
            "branch": branch.get(workflow_id, ""),
            "agent": str(run.get("agent") or ""),
            "role": ROLE_LABEL.get(role.lower(), role.replace("_", " ").capitalize()),
            "age": rel_age(workflow.get("updated_at") or workflow.get("created_at"), now),
            "progress": f"{int(counts.get('passed') or 0)}/{int(workflow.get('task_count') or 0)}",
        }
        (settled if is_settled(workflow, now, settle_after) else active).append(item)
    active.sort(key=lambda item: _ACTIVE_ORDER.get(item["status"], 9))  # stable: newest first
    return active, settled


def state_db_exists(repository: str) -> bool:
    return (Path(repository) / ".agentops" / "state.sqlite").is_file()


def pollable_repositories(
    recent: Iterable[str],
    active: str,
    state_pinned: bool,
    exists: Callable[[str], bool] = state_db_exists,
) -> list[str]:
    """The other recent repositories the thread list may read.

    Only those whose ``<repo>/.agentops/state.sqlite`` already exists, so
    browsing never creates a database; none when the controller pins one
    ``state_path`` (every repository would read that same database).
    """
    if state_pinned:
        return []
    out: list[str] = []
    for path in recent:
        cleaned = str(path or "").strip()
        if cleaned and cleaned != active and cleaned not in out and exists(cleaned):
            out.append(cleaned)
    return out
