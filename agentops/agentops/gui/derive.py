"""Qt-free presentation derivations for the workspace and the dashboard.

Everything here works on the plain dicts the controller already returns
(``list_workflows``, ``list_worktree_refs``, ``list_recent_agent_runs``,
``list_recent_verification_runs``).
Nothing touches SQLite, git or Qt, so it is unit-testable anywhere.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta, timezone
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


# Agent-run outcomes the dashboard counts as "failed or timed out".
FAILED_RUN = frozenset({"failed", "timed_out", "terminated"})
# Verification outcomes that decide a pass rate. Cancelled and still-running
# runs (no overall_status yet) prove nothing either way, so they are left out;
# "unverified" (every check skipped) is never success, so it counts against.
_DECIDED_VERIFICATION = frozenset({"passed", "failed", "timed_out", "unverified"})


def run_failed(run: dict) -> bool:
    """An agent run that ended failed, timed out or terminated."""
    status = str(run.get("status") or "").lower()
    return status in FAILED_RUN or bool(run.get("timed_out")) or bool(run.get("terminated"))


def _stamp(record: dict, *keys: str) -> datetime | None:
    for key in keys:
        stamp = parse_ts(record.get(key))
        if stamp is not None:
            return stamp
    return None


def daily_series(
    runs: Iterable[dict], days: int = 14, now: datetime | None = None
) -> list[tuple[date, int, int]]:
    """Agent runs per calendar day: ``[(day, total, failed)]``, oldest first.

    Always ``days`` entries ending at ``now``'s date (in ``now``'s timezone),
    zeros included; a run is placed by ``started_at`` (``created_at`` when it
    never started) and runs outside the window are ignored.
    """
    now = now or datetime.now(timezone.utc)
    days = max(1, int(days))
    zone = now.tzinfo or timezone.utc
    last = now.astimezone(zone).date()
    first = last - timedelta(days=days - 1)
    totals: dict[date, list[int]] = {first + timedelta(days=i): [0, 0] for i in range(days)}
    for run in runs:
        stamp = _stamp(run, "started_at", "created_at")
        if stamp is None:
            continue
        bucket = totals.get(stamp.astimezone(zone).date())
        if bucket is not None:
            bucket[0] += 1
            bucket[1] += int(run_failed(run))
    return [(day, total, failed) for day, (total, failed) in totals.items()]


def _pct_delta(current: float, previous: float) -> int | None:
    """Whole-percent change, or None when there is no prior week to compare."""
    if not previous:
        return None
    return round((current - previous) / previous * 100)


def _pass_rate(runs: list[dict]) -> int | None:
    decided = [
        r for r in runs if str(r.get("overall_status") or "").lower() in _DECIDED_VERIFICATION
    ]
    if not decided:
        return None
    passed = sum(1 for r in decided if str(r.get("overall_status")).lower() == "passed")
    return round(passed / len(decided) * 100)


def week_stats(
    workflows: Iterable[dict],
    runs: Iterable[dict],
    verification_runs: Iterable[dict],
    attention_count: int,
    now: datetime | None = None,
) -> dict[str, dict[str, int | None]]:
    """This week (the last 7 days) against the 7 days before it.

    ``{"workflows", "pass_rate", "agent_time", "attention"}`` each map to
    ``{"value", "delta"}``: workflows created (delta in %), verification
    pass rate in % (delta in points), agent seconds (delta in %), and the
    attention count (no delta). A delta is None when the previous week has
    nothing to compare, which the view words as "no prior week".
    """
    now = now or datetime.now(timezone.utc)
    week = timedelta(days=7)

    def split(records: Iterable[dict], *keys: str) -> tuple[list[dict], list[dict]]:
        this: list[dict] = []
        prior: list[dict] = []
        for record in records:
            stamp = _stamp(record, *keys)
            if stamp is None or stamp > now:
                continue
            if now - stamp <= week:
                this.append(record)
            elif now - stamp <= 2 * week:
                prior.append(record)
        return this, prior

    wf_now, wf_prev = split(workflows, "created_at")
    run_now, run_prev = split(runs, "started_at", "created_at")
    ver_now, ver_prev = split(verification_runs, "started_at", "created_at")

    def seconds(records: list[dict]) -> int:
        return round(sum(float(r.get("duration_seconds") or 0) for r in records))

    rate_now, rate_prev = _pass_rate(ver_now), _pass_rate(ver_prev)
    time_now, time_prev = seconds(run_now), seconds(run_prev)
    return {
        "workflows": {"value": len(wf_now), "delta": _pct_delta(len(wf_now), len(wf_prev))},
        "pass_rate": {
            "value": rate_now,
            "delta": None if rate_now is None or rate_prev is None else rate_now - rate_prev,
        },
        "agent_time": {"value": time_now, "delta": _pct_delta(time_now, time_prev)},
        "attention": {"value": int(attention_count or 0), "delta": None},
    }


# Failure category and recommended action in words (FailureCategory and
# RepairAction values); anything else falls back to sentence case.
PROBLEM_WORDS = {
    "test_failure": "Tests failed",
    "timeout": "Agent timed out",
    "git_conflict": "Merge conflict",
    "lint_failure": "Lint failed",
    "typecheck_failure": "Type check failed",
    "build_failure": "Build failed",
    "verification_failure": "Verification failed",
    "review_rejection": "Review rejected",
    "usage_limit": "Usage limit hit",
    "dirty_worktree": "Worktree not clean",
}
NEXT_STEP_WORDS = {
    "retry_same_agent": "Retry same agent",
    "retry_different_agent": "Try another agent",
    "repair_implementation": "Repair implementation",
    "rerun_verification": "Rerun verification",
    "request_approval": "Ask for approval",
    "stop": "Resolve manually",
}


def _words(value: object, table: dict[str, str]) -> str:
    key = str(value or "").strip().lower()
    if key in table:
        return table[key]
    text = key.replace("_", " ")
    return text[:1].upper() + text[1:]


def attention_rows(
    failures: Iterable[dict], workflows: Iterable[dict], runs: Iterable[dict], now: datetime
) -> list[dict]:
    """Needs-attention table rows, every value joined from existing reads.

    The workflow comes from ``list_workflows`` by id, the agent from the run
    that failed (``agent_run_id``) or else the newest run of the same task.
    """
    titles = {str(w.get("id") or ""): str(w.get("description") or "") for w in workflows}
    by_id: dict[str, dict] = {}
    by_task: dict[str, dict] = {}
    for run in runs:  # newest first
        by_id.setdefault(str(run.get("id") or ""), run)
        by_task.setdefault(str(run.get("task_id") or ""), run)
    rows = []
    for failure in failures:
        workflow_id = str(failure.get("workflow_id") or "")
        run = by_id.get(str(failure.get("agent_run_id") or "")) or by_task.get(
            str(failure.get("task_id") or ""), {}
        )
        rows.append(
            {
                "workflow": titles.get(workflow_id) or workflow_id[:8] or "Unknown workflow",
                "problem": _words(failure.get("category"), PROBLEM_WORDS),
                "severity": str(failure.get("severity") or "").lower(),
                "agent": str(run.get("agent") or ""),
                "next_step": _words(failure.get("recommended_action"), NEXT_STEP_WORDS),
                "age": rel_age(failure.get("created_at"), now),
                "error": str(failure.get("primary_error") or ""),
                "created_at": str(failure.get("created_at") or ""),
            }
        )
    return rows


def agent_bars(runs: Iterable[dict], now: datetime, days: int = 14) -> list[tuple[str, int, int]]:
    """``[(agent, runs, ok %)]`` over the last ``days``, busiest first.

    A run counts as ok when it completed; failed, timed-out, terminated and
    cancelled runs do not. Runs still going count toward the total only.
    """
    totals: dict[str, list[int]] = {}
    for run in runs:
        stamp = _stamp(run, "started_at", "created_at")
        agent = str(run.get("agent") or "")
        if stamp is None or not agent or now - stamp > timedelta(days=days):
            continue
        bucket = totals.setdefault(agent, [0, 0])
        bucket[0] += 1
        bucket[1] += int(str(run.get("status") or "").lower() == "completed")
    ranked = sorted(totals.items(), key=lambda item: (-item[1][0], item[0]))
    return [(agent, total, round(ok / total * 100)) for agent, (total, ok) in ranked]


def event_icon(event_type: object, severity: object = "") -> tuple[str, str]:
    """(Lucide icon, theme colour token) for an activity-feed event."""
    kind = str(event_type or "").lower()
    if kind.startswith("verification."):
        return "shield-check", "danger" if kind.endswith("failed") else "success"
    if kind.endswith((".failed", ".conflict")) or str(severity or "").lower() == "error":
        return "circle-x", "danger"
    if kind.endswith(".started"):
        return "play", "info"
    if kind.endswith((".finished", ".completed")):
        return "circle-check", "success"
    if kind.endswith(".created"):
        return "plus", "text_muted"
    return "activity", "text_muted"


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
