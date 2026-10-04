"""Workflow control-center projections for the desktop client.

Qt-free and pure: every field the control center renders is derived here from
the payloads ``AgentOpsController`` already returns - a workflow, its tasks,
agent runs, verification runs, failures, worktree provenance, and timeline
events. No SQLite, no git, no Qt, and no clock other than an explicitly
injected ``now``.

Two rules shape these projections:

* Truthful state beats decorative progress. A duration appears only when a
  record carries one, and no percentage is ever invented for a stage whose
  real progress nobody measured.
* Readiness is read, never re-derived. :func:`merge_readiness` consumes the
  controller's assessment of the READY contract, so this module cannot hold a
  weaker version of it than the engine.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .format import elide, format_duration, short_id

# Role -> stage label for the standard high-level flow. The engine builds
# exactly these roles, in this order (workflow.STANDARD_TASK_ROLES).
STANDARD_STAGES: tuple[tuple[str, str], ...] = (
    ("architecture", "PLAN"),
    ("implementation", "IMPLEMENT"),
    ("verification", "VERIFY"),
    ("review", "REVIEW"),
)
FINALIZE_LABEL = "FINALIZE"
FINALIZE_KEY = "finalize"

_STAGE_ROLES = {role for role, _label in STANDARD_STAGES}
_ROLE_LABELS = dict(STANDARD_STAGES)
_ACTIVE_RUN_STATUSES = ("running", "starting")
_MERGED_WORKFLOW_STATUSES = ("completed", "passed", "merged")
_CANCELLED_STATUSES = ("cancelled", "canceled")


# ---------------------------------------------------------------------------
# record helpers
# ---------------------------------------------------------------------------

def _text(value: object) -> str:
    return str(value or "").strip()


def _rows(value: object) -> list[dict]:
    """Keep dict records only; anything else is dropped instead of rendered."""
    if not isinstance(value, (list, tuple)):
        return []
    return [row for row in value if isinstance(row, dict)]


def _ordered(rows: list[dict], *fields: str) -> list[dict]:
    return sorted(rows, key=lambda row: tuple(_text(row.get(field)) for field in fields))


def _number(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _duration_total(rows: list[dict]) -> float | None:
    """Sum only the durations that were actually recorded."""
    recorded = [value for value in (_number(row.get("duration_seconds")) for row in rows)
                if value is not None]
    return round(sum(recorded), 1) if recorded else None


def _latest(rows: list[dict], *fields: str) -> dict | None:
    ordered = _ordered(rows, *fields)
    return ordered[-1] if ordered else None


def _parse_time(value: object) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def elapsed_since(value: object, now: datetime | None = None) -> float | None:
    """Seconds between a recorded timestamp and ``now``; None when unknowable."""
    started = _parse_time(value)
    if started is None:
        return None
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    seconds = (moment - started).total_seconds()
    return round(seconds, 1) if seconds >= 0 else None


# ---------------------------------------------------------------------------
# stage flow
# ---------------------------------------------------------------------------

def is_custom_dag(tasks: object) -> bool:
    """True when the workflow is not the standard four-role pipeline.

    Only the standard roles are collapsible into fixed stages; anything else
    (a custom DAG role, or a role the engine does not own) has to be shown as
    the dependency structure it actually is.
    """
    records = _rows(tasks)
    if not records:
        return False
    return any(_text(task.get("role")) not in _STAGE_ROLES for task in records)


def _group_state(group: list[dict], cancelled: bool) -> str:
    statuses = {_text(task.get("status")) for task in group}
    if "running" in statuses:
        return "running"
    if "failed" in statuses:
        return "failed"
    if "blocked" in statuses:
        return "blocked"
    if statuses and statuses <= {"passed"}:
        return "passed"
    if cancelled and statuses - {"passed"}:
        return "cancelled"
    return "pending"


def _runs_for(run_rows: list[dict], task_ids: set[str]) -> list[dict]:
    return [row for row in run_rows if _text(row.get("task_id")) in task_ids]


def _agent_of(group: list[dict], group_runs: list[dict]) -> str:
    for task in _ordered(group, "started_at", "created_at", "id"):
        agent = _text(task.get("assigned_agent"))
        if agent:
            return agent
    run = _latest(group_runs, "started_at", "created_at", "id")
    return _text(run.get("agent")) if run else ""


def _model_of(group_runs: list[dict]) -> str:
    for run in reversed(_ordered(group_runs, "started_at", "created_at", "id")):
        model = _text(run.get("model"))
        if model:
            return model
    return ""


def _verification_of(group: list[dict], verifications: list[dict]) -> str:
    task_ids = {_text(task.get("id")) for task in group}
    linked = [row for row in verifications if _text(row.get("task_id")) in task_ids]
    if linked:
        run = _latest(linked, "started_at", "created_at", "id")
        return _text(run.get("overall_status") or run.get("status")) or "unknown"
    if any(task.get("verified") for task in group):
        return "verified"
    return "not run"


def _failure_of(task_ids: set[str], failures: list[dict]) -> str:
    linked = [row for row in failures if _text(row.get("task_id")) in task_ids]
    if not linked:
        return ""
    failure = _latest(linked, "created_at", "id")
    category = _text(failure.get("category")).replace("_", " ")
    detail = elide(failure.get("primary_error"), 60)
    return f"{category}: {detail}" if detail else category


def _completed_at(group: list[dict]) -> str:
    stamps = [_text(task.get("finished_at")) for task in group if _text(task.get("finished_at"))]
    return max(stamps) if stamps else ""


def _stage(
    key: str,
    label: str,
    group: list[dict],
    run_rows: list[dict],
    verifications: list[dict],
    failures: list[dict],
    cancelled: bool,
    depends_on: tuple[str, ...] = (),
) -> dict[str, object]:
    task_ids = {_text(task.get("id")) for task in group}
    group_runs = _runs_for(run_rows, task_ids)
    statuses = [_text(task.get("status")) for task in group]
    duration = _duration_total(group_runs)
    return {
        "key": key,
        "label": label,
        "state": _group_state(group, cancelled),
        "task_status": " / ".join(status for status in statuses if status) or "no tasks",
        "task_count": len(group),
        "task_ids": [str(task.get("id")) for task in group],
        "agent": _agent_of(group, group_runs),
        "model": _model_of(group_runs),
        "duration_seconds": duration,
        "duration_text": format_duration(duration),
        "verification": _verification_of(group, verifications),
        "failure": _failure_of(task_ids, failures),
        "completed_at": _completed_at(group),
        "depends_on": list(depends_on),
    }


def _finalize_stage(workflow: dict[str, object], run_rows: list[dict],
                    cancelled: bool, stages_done: bool = False) -> dict[str, object]:
    status = _text(workflow.get("status")).lower()
    if status in _MERGED_WORKFLOW_STATUSES:
        state = "passed"
    elif status in _CANCELLED_STATUSES or cancelled:
        state = "cancelled"
    elif status == "failed":
        state = "failed"
    elif status == "running" and stages_done:
        # Finalize is running only once every task stage is behind it. A
        # running workflow with pending stages is still working a stage, and
        # claiming finalize is the current stage would point at the wrong one.
        state = "running"
    else:
        state = "pending"
    duration = _duration_total(run_rows)
    return {
        "key": FINALIZE_KEY,
        "label": FINALIZE_LABEL,
        "state": state,
        "task_status": status or "pending",
        "task_count": 0,
        "task_ids": [],
        "agent": "",
        "model": "",
        "duration_seconds": duration,
        "duration_text": format_duration(duration),
        "verification": "",
        "failure": "",
        "completed_at": _text(workflow.get("updated_at")),
        "depends_on": [],
    }


def build_stage_flow(
    workflow: object,
    tasks: object,
    runs: object = (),
    verifications: object = (),
    failures: object = (),
) -> list[dict[str, object]]:
    """Stage list for one workflow: fixed stages, or the real DAG when custom."""
    header = workflow if isinstance(workflow, dict) else {}
    task_rows = _rows(tasks)
    run_rows = _rows(runs)
    verification_rows = _rows(verifications)
    failure_rows = _rows(failures)
    cancelled = _text(header.get("status")).lower() in _CANCELLED_STATUSES

    if is_custom_dag(task_rows):
        by_id = {_text(task.get("id")): task for task in task_rows}
        stages = []
        for task in _ordered(task_rows, "created_at", "id"):
            key = _text(task.get("id"))
            label = _text(task.get("role")).replace("_", " ").title() or "Task"
            depends_on = tuple(
                dependency for dependency in (_text(d) for d in task.get("dependencies") or ())
                if dependency in by_id and dependency != key
            )
            stages.append(_stage(key, label, [task], run_rows, verification_rows,
                                 failure_rows, cancelled, depends_on))
        stages.append(_finalize_stage(header, run_rows, cancelled, _stages_done(stages)))
        return stages

    grouped: dict[str, list[dict]] = {}
    for task in task_rows:
        grouped.setdefault(_text(task.get("role")), []).append(task)
    stages = []
    previous = ""
    for role, label in STANDARD_STAGES:
        group = grouped.get(role)
        if not group:
            continue
        stages.append(_stage(role, label, group, run_rows, verification_rows,
                             failure_rows, cancelled, (previous,) if previous else ()))
        previous = role
    stages.append(_finalize_stage(header, run_rows, cancelled, _stages_done(stages)))
    return stages


def _stages_done(stages: list[dict]) -> bool:
    """True when no task stage is still pending, blocked, or running."""
    return all(stage["state"] not in ("pending", "blocked", "running")
               for stage in stages if stage["key"] != FINALIZE_KEY)


# ---------------------------------------------------------------------------
# live control room
# ---------------------------------------------------------------------------

def current_stage(stages: object) -> dict[str, object] | None:
    """The stage an operator should be looking at right now.

    A running stage wins. Failing that, the first stage still to come while
    the workflow is live; the last finished stage once it is not.
    """
    rows = _rows(stages)
    for stage in rows:
        if stage.get("state") == "running":
            return stage
    for stage in rows:
        if stage.get("state") in ("pending", "blocked"):
            return stage
    for stage in reversed(rows):
        if stage.get("state") in ("passed", "failed", "cancelled"):
            return stage
    return rows[-1] if rows else None


def next_stage(stages: object) -> dict[str, object] | None:
    rows = _rows(stages)
    for index, stage in enumerate(rows):
        if stage.get("state") in ("running", "pending", "blocked", "failed"):
            return rows[index + 1] if index + 1 < len(rows) else None
    return None


def _running_run(runs: object) -> dict | None:
    rows = _rows(runs)
    active = [row for row in rows if _text(row.get("status")) in _ACTIVE_RUN_STATUSES]
    return _latest(active, "started_at", "created_at", "id") or _latest(
        rows, "started_at", "created_at", "id"
    )


def _running_task(tasks: object, runs: object) -> dict | None:
    rows = _rows(tasks)
    running = [row for row in rows if _text(row.get("status")) == "running"]
    if running:
        return _latest(running, "started_at", "created_at", "id")
    run = _running_run(runs)
    task_id = _text(run.get("task_id")) if run else ""
    return next((row for row in rows if _text(row.get("id")) == task_id), None) if task_id else None


def build_live_panel(
    workflow: object,
    tasks: object,
    runs: object = (),
    verifications: object = (),
    failures: object = (),
    events: object = (),
    now: datetime | None = None,
) -> dict[str, object]:
    """Control-room projection for one workflow.

    Everything here is observed state: the running task, the agent run behind
    it, and the recorded clock. There is no estimated completion and no
    invented percentage.
    """
    header = workflow if isinstance(workflow, dict) else {}
    task_rows = _rows(tasks)
    run_rows = _rows(runs)
    stages = build_stage_flow(header, task_rows, run_rows, verifications, failures)
    status = _text(header.get("status")).lower()
    running = any(_text(task.get("status")) == "running" for task in task_rows) or any(
        _text(run.get("status")) in _ACTIVE_RUN_STATUSES for run in run_rows
    )
    task = _running_task(task_rows, run_rows)
    run = _running_run(run_rows)
    stage = current_stage(stages)
    upcoming = next_stage(stages)
    # Elapsed comes only from work that is actually in flight. The run may be a
    # finished one (shown for context), so its own status decides; the
    # workflow header's updated_at is never a fallback because it moves on every
    # write and would report time that never elapsed anywhere.
    in_flight = (
        str((run or {}).get("status")) in _ACTIVE_RUN_STATUSES
        or str((task or {}).get("status")) == "running"
    )
    elapsed = elapsed_since(
        (run or {}).get("started_at") or (task or {}).get("started_at")
        if (running and in_flight and (run or task)) else None,
        now,
    )
    return {
        "running": running,
        "status": status or "pending",
        "stage": (stage or {}).get("label") or "",
        "stage_key": (stage or {}).get("key") or "",
        "next_stage": (upcoming or {}).get("label") or "",
        "agent": _text((task or {}).get("assigned_agent")) or _text((run or {}).get("agent")),
        "agent_status": _text((run or {}).get("status")) or _text((task or {}).get("status")),
        "model": _model_of([run] if run else []),
        "elapsed_seconds": elapsed,
        "elapsed_text": format_duration(elapsed),
        "task_id": _text((task or {}).get("id")),
        "run_id": _text((run or {}).get("id")),
        "stages": stages,
        "activity": recent_activity(events),
    }


# ---------------------------------------------------------------------------
# activity
# ---------------------------------------------------------------------------

def _activity_line(event: dict) -> str:
    kind = _text(event.get("type")).replace(".", " ")
    message = elide(event.get("message"), 90)
    return f"{kind}  {message}".strip() if message else kind


def recent_activity(events: object, limit: int = 8) -> list[dict[str, object]]:
    """Newest-first timeline rows for one workflow, ready for a list widget."""
    rows = _rows(events)
    ordered = sorted(
        rows, key=lambda row: _text(row.get("timestamp")), reverse=True
    )[:max(0, int(limit))]
    return [
        {
            "timestamp": _text(event.get("timestamp")),
            "type": _text(event.get("type")),
            "severity": _text(event.get("severity")) or "info",
            "message": _text(event.get("message")),
            "text": _activity_line(event),
            "task_id": _text(event.get("task_id")),
            "run_id": _text(event.get("agent_run_id")),
        }
        for event in ordered
    ]


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def verification_totals(runs: object, checks: object = ()) -> dict[str, object]:
    """Count verification outcomes across runs and, when loaded, checks.

    Check rows win when they exist: a run's stored counter can lag its own
    check list, and the checks are the evidence.
    """
    check_rows = _rows(checks)
    if check_rows:
        passed = sum(1 for row in check_rows if _text(row.get("status")) == "passed")
        failed = sum(1 for row in check_rows if _text(row.get("status")) in ("failed", "timed_out"))
        skipped = sum(1 for row in check_rows if _text(row.get("status")) in ("skipped", "cancelled"))
        total = len(check_rows)
    else:
        run_rows = _rows(runs)
        total = sum(int(_number(row.get("total_checks")) or 0) for row in run_rows)
        passed = sum(int(_number(row.get("passed_checks")) or 0) for row in run_rows)
        failed = sum(int(_number(row.get("failed_checks")) or 0) for row in run_rows)
        skipped = sum(int(_number(row.get("skipped_checks")) or 0) for row in run_rows)
    lines = [f"{passed} passed"]
    lines += [f"{failed} failed"] if failed else []
    lines += [f"{skipped} skipped"] if skipped else []
    return {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": total,
        "summary": "  ".join(lines),
        "state": "failed" if failed else ("passed" if passed else "pending"),
    }


# ---------------------------------------------------------------------------
# failures
# ---------------------------------------------------------------------------

def failure_summary(failure: object, runs: object = (), tasks: object = ()) -> dict[str, object]:
    """Actionable view of one failure record.

    The raw record is never handed to a widget: only the fields an operator
    acts on, plus the human labels for the related run and task.
    """
    record = failure if isinstance(failure, dict) else {}
    run_id = _text(record.get("agent_run_id"))
    task_id = _text(record.get("task_id"))
    run = next((row for row in _rows(runs) if _text(row.get("id")) == run_id), None)
    task = next((row for row in _rows(tasks) if _text(row.get("id")) == task_id), None)
    structured = record.get("structured_evidence")
    evidence = _text(record.get("evidence"))
    if not evidence and isinstance(structured, dict):
        evidence = _text(structured.get("summary"))
    retryable = bool(record.get("retryable"))
    repairable = bool(record.get("repairable"))
    return {
        "id": _text(record.get("id")),
        "category": _text(record.get("category")).replace("_", " ") or "unknown",
        "severity": _text(record.get("severity")) or "unknown",
        "source": _text(record.get("source")),
        "error": elide(record.get("primary_error"), 400),
        "evidence": elide(evidence, 400),
        "retryable": retryable,
        "repairable": repairable,
        "action": _text(record.get("recommended_action")).replace("_", " ") or "none recorded",
        "recovery_state": _text(record.get("recovery_state")),
        "attempt": record.get("attempt"),
        "created_at": _text(record.get("created_at")),
        "task_id": task_id,
        "task_label": _text((task or {}).get("description")) or short_id(task_id),
        "run_id": run_id,
        "run_label": (
            f"{_text((run or {}).get('agent')) or 'agent'} run {short_id(run_id)}"
            if run else ""
        ),
    }


# ---------------------------------------------------------------------------
# worktree and merge readiness
# ---------------------------------------------------------------------------

def _is_managed_path(path: str) -> bool:
    """Managed worktrees live under the repository's ``.agentops/worktrees``."""
    if not path:
        return False
    return "/.agentops/worktrees/" in path.replace("\\", "/")


def _changed_files(runs: object) -> list[str]:
    names: list[str] = []
    for run in _ordered(_rows(runs), "started_at", "created_at", "id"):
        for name in run.get("files_changed") or ():
            text = _text(name)
            if text and text not in names:
                names.append(text)
    return names


def worktree_summary(
    ref: object,
    runs: object = (),
    readiness: object = None,
    inspected: object = None,
) -> dict[str, object]:
    """The workflow's isolated worktree: provenance, changes, merge state."""
    record = ref if isinstance(ref, dict) else {}
    info = inspected if isinstance(inspected, dict) else {}
    files = [name for name in info.get("files_changed") or () if _text(name)]
    changed = files or _changed_files(runs)
    path = _text(record.get("path")) or _text(info.get("path"))
    diff_stat = _text(info.get("diff_stat")) or next(
        (_text(run.get("diff_stat")) for run in reversed(_ordered(_rows(runs), "started_at", "id"))
         if _text(run.get("diff_stat"))), ""
    )
    return {
        "present": bool(record) or bool(path),
        "path": path,
        "branch": _text(record.get("branch")) or _text(info.get("branch")),
        "base_branch": _text(record.get("base_branch")),
        "base_commit": _text(record.get("base_commit")),
        "head": _text(info.get("head")),
        "managed": _is_managed_path(path),
        "changed_files": changed,
        "changed_count": len(changed),
        "diff_stat": diff_stat,
        "dirty": bool(_text(info.get("status"))),
        "readiness": merge_readiness(readiness, has_worktree=bool(path)),
    }


def merge_readiness(readiness: object, has_worktree: bool = False) -> dict[str, object]:
    """Render the engine's READY assessment as a merge decision.

    ``readiness`` is the controller's own assessment (``ready`` plus reason
    strings). This function never decides readiness on its own; it only adds
    the one operational precondition the assessment does not cover - there has
    to be a worktree to merge from.
    """
    assessment = readiness if isinstance(readiness, dict) else {}
    reasons = [_text(reason) for reason in assessment.get("reasons") or () if _text(reason)]
    ready = bool(assessment.get("ready"))
    if ready and not has_worktree:
        ready = False
        reasons.append("no worktree to merge from")
    return {
        "ready": ready,
        "state": "ready" if ready else "blocked",
        "label": "Ready to merge" if ready else "Not ready to merge",
        "reasons": reasons,
        "summary": "ready" if ready else ("; ".join(reasons) or "not ready"),
    }


# ---------------------------------------------------------------------------
# task and run detail
# ---------------------------------------------------------------------------

def task_detail(task: object, runs: object = (), verifications: object = (),
                failures: object = (), artifacts: object = ()) -> dict[str, object]:
    """Everything one task owns, grouped for the detail panel."""
    record = task if isinstance(task, dict) else {}
    task_id = _text(record.get("id"))
    return {
        "id": task_id,
        "description": _text(record.get("description")),
        "role": _text(record.get("role")).replace("_", " "),
        "agent": _text(record.get("assigned_agent")),
        "status": _text(record.get("status")) or "pending",
        "attempts": f"{record.get('attempts', 0)} / {record.get('max_attempts', 0)}",
        "attempts_made": int(_number(record.get("attempts")) or 0),
        "dependencies": [str(item) for item in record.get("dependencies") or ()],
        "verified": bool(record.get("verified")),
        "verification": _verification_of([record], _rows(verifications)),
        "result": _text(record.get("result")),
        "created_at": _text(record.get("created_at")),
        "started_at": _text(record.get("started_at")),
        "finished_at": _text(record.get("finished_at")),
        "runs": [row for row in _rows(runs) if _text(row.get("task_id")) == task_id],
        "verifications": [row for row in _rows(verifications)
                          if _text(row.get("task_id")) == task_id],
        "failures": [row for row in _rows(failures) if _text(row.get("task_id")) == task_id],
        "artifacts": [row for row in _rows(artifacts) if _text(row.get("task_id")) == task_id],
    }


def run_detail(run: object) -> dict[str, object]:
    """Everything one agent run owns, including its structured result."""
    record = run if isinstance(run, dict) else {}
    files = [_text(name) for name in record.get("files_changed") or () if _text(name)]
    return {
        "id": _text(record.get("id")),
        "agent": _text(record.get("agent")),
        "model": _text(record.get("model")),
        "role": _text(record.get("role")).replace("_", " "),
        "status": _text(record.get("status")) or "unknown",
        "attempt": record.get("attempt"),
        "exit_code": record.get("exit_code"),
        "duration_text": format_duration(record.get("duration_seconds")),
        "started_at": _text(record.get("started_at")),
        "ended_at": _text(record.get("ended_at")),
        "worktree": _text(record.get("worktree")),
        "files_changed": files,
        "diff_stat": _text(record.get("diff_stat")),
        "log_path": _text(record.get("log_path")),
        "error": _text(record.get("error")),
        "relationship": _text(record.get("relationship")),
        "structured_result": record.get("structured_result"),
    }


# ---------------------------------------------------------------------------
# cancellation
# ---------------------------------------------------------------------------

def cancellation_state(
    live: object,
    cancel_requested: bool = False,
    operation_active: bool = False,
) -> dict[str, object]:
    """What the Cancel affordance may do, right now.

    Cancellation is only offered while work is actually in flight, the action
    locks itself once requested, and a cancelled run is never reported as a
    success: the state is carried through to the surface verbatim.
    """
    panel = live if isinstance(live, dict) else {}
    running = bool(panel.get("running")) or operation_active
    cancelled = _text(panel.get("status")) in _CANCELLED_STATUSES
    cancellable = running and not cancel_requested
    return {
        "running": running,
        "cancelled": cancelled,
        "cancel_requested": bool(cancel_requested),
        "cancellable": cancellable,
        "conflicts_active": bool(cancellable),
        "state": ("cancelling" if cancel_requested else
                  "cancelled" if cancelled else "running" if running else "idle"),
        "label": ("Cancelling" if cancel_requested else
                  "Cancelled" if cancelled else
                  "Running" if running else "Idle"),
    }