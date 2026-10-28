"""Offscreen PNG snapshots of the AgentOps desktop GUI, against realistic seeded state.

Usage from the ``agentops/`` product directory (PowerShell)::

    $env:QT_QPA_PLATFORM="offscreen"; python scripts\\gui_snapshots.py --size 1440x900 --out .\\snap

Options: ``--size WxH`` (window size, default 1440x900), ``--out DIR`` (created if
missing), ``--scale 1.5`` (sets ``QT_SCALE_FACTOR`` before Qt starts, for HiDPI
captures).

What it does:

1. Seeds a throwaway workspace in a temp directory: ``demo-service`` (a real git
   repo with about 18 workflows spread over 14 days, one running, one failed, one
   pending, the rest settled, across codex/claude/opencode/kilo/copilot, plus one
   real managed worktree under ``.agentops/worktrees/`` on branch
   ``agentops/<slug>`` holding uncommitted edits, so a real diff exists),
   ``docs-site`` (a second small repo for multi-repo views), and ``empty`` (a bare
   ``git init`` for empty states). Rows go through the public ``StateStore`` API.
2. Opens the REAL ``MainWindow(AgentOpsController(), ...)`` with a temp settings
   file, so the developer's own settings are never read or written, and grabs every
   ``VIEW_SPECS`` view (row 0 selected where a table exists), the workflow control
   center for the running and the failed workflow, a toast, the command palette,
   the New Task dialog, the collapsed sidebar, and empty states.

Nothing here is product code. ``tests/gui_snapshots`` reuses ``seed`` and
``open_window`` for its pixel and label checks.
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
# Upper bound for the GUI worker pool to drain; slow CI runners need ~30 s at startup.
IDLE_TIMEOUT_MS = 120_000
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ----------------------------------------------------------------------------
# Seeding
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Seeded:
    """Paths and ids of a seeded snapshot workspace."""

    base: Path
    repo: Path
    second_repo: Path
    empty_repo: Path
    running_workflow: str
    failed_workflow: str
    pending_workflow: str
    worktree: Path


def _git(cwd: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env
    ).stdout


def _make_repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "snapshots@example.invalid")
    _git(path, "config", "user.name", "gui-snapshots")
    _git(path, "config", "commit.gpgsign", "false")
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text, encoding="utf-8")
    (path / ".gitignore").write_text(".agentops/\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "init")
    return path


TOKENS_PY = '''"""Session token helpers."""
import time

from auth.store import TokenStore

ACCESS_TTL = 15 * 60


def issue_session(user_id: str, store: TokenStore) -> dict:
    token = store.new_token(user_id)
    return {"session": token, "expires": time.time() + ACCESS_TTL}


def validate(token: str, store: TokenStore) -> str | None:
    record = store.get(token)
    if record is None:
        return None
    return record.user_id
'''

TOKENS_PY_NEW = '''"""Session token helpers."""
import time

import jwt

from auth.store import TokenStore

ACCESS_TTL = 15 * 60
REFRESH_TTL = 14 * 24 * 60 * 60


def issue_session(user_id: str, store: TokenStore, secret: str) -> dict:
    access = jwt.encode({"sub": user_id, "exp": time.time() + ACCESS_TTL}, secret)
    refresh = store.new_refresh_token(user_id, ttl=REFRESH_TTL)
    return {"access": access, "refresh": refresh, "expires_in": ACCESS_TTL}


def refresh(refresh_token: str, store: TokenStore, secret: str) -> dict | None:
    record = store.get_refresh(refresh_token)
    if record is None or record.expired():
        return None
    store.rotate(refresh_token)
    return issue_session(record.user_id, store, secret)


def validate(token: str, secret: str) -> str | None:
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    return claims["sub"]
'''

ROUTES_PY = """from flask import Blueprint

bp = Blueprint("auth", __name__)


@bp.post("/login")
def login():
    return {"ok": True}
"""

ROUTES_PY_NEW = """from flask import Blueprint, current_app, request

from auth import tokens

bp = Blueprint("auth", __name__)


@bp.post("/login")
def login():
    return {"ok": True}


@bp.post("/token/refresh")
def refresh_token():
    body = request.get_json(silent=True) or {}
    session = tokens.refresh(body.get("refresh", ""), current_app.store, current_app.secret)
    if session is None:
        return {"error": "invalid_refresh"}, 401
    return session
"""

TEST_AUTH_PY = "def test_login(client):\n    assert client.post('/login').ok\n"

TEST_AUTH_PY_NEW = (
    TEST_AUTH_PY
    + """

from auth import tokens


def test_refresh_rotates_token(store, secret):
    first = tokens.issue_session("u1", store, secret)
    second = tokens.refresh(first["refresh"], store, secret)
    assert second["refresh"] != first["refresh"]


def test_refresh_expired_token(client, expired_refresh):
    response = client.post("/token/refresh", json={"refresh": expired_refresh})
    assert response.status_code == 401
"""
)

# (description, agent, model, days_ago, outcome, files, insertions, deletions)
# fmt: off
SETTLED = [
    ("Fix N+1 query in order history", "codex", "gpt-5-codex", 13.2, "passed",
     ["orders/history.py", "tests/test_history.py"], 34, 12),
    ("Typecheck the payments module with mypy --strict", "claude", "sonnet", 12.6, "passed",
     ["payments/charge.py", "payments/refund.py", "mypy.ini"], 61, 40),
    ("Document the deploy runbook", "opencode", "kimi-k2", 11.9, "passed",
     ["docs/deploy.md"], 118, 3),
    ("Speed up Docker build with layer caching", "copilot", "gpt-5", 11.1, "timeout",
     ["Dockerfile"], 0, 0),
    ("Add healthcheck endpoint with dependency probes", "codex", "gpt-5-codex", 10.4, "passed",
     ["api/health.py", "tests/test_health.py"], 72, 0),
    ("Harden CSV export against formula injection", "claude", "sonnet", 9.3, "passed",
     ["exports/csv.py", "tests/test_csv.py"], 29, 6),
    ("Remove dead feature flags from settings", "opencode", "kimi-k2", 8.2, "passed",
     ["settings/flags.py", "settings/base.py"], 4, 57),
    ("Add pagination to /v1/orders", "codex", "gpt-5-codex", 7.1, "passed",
     ["api/orders.py", "tests/test_orders.py"], 48, 9),
    ("Cache /v1/products responses in Redis", "claude", "sonnet", 6.0, "verify_failed",
     ["api/products.py", "cache/redis.py"], 66, 4),
    ("Bump ruff and fix new lint findings", "kilo", "glm-4.6", 4.8, "passed",
     ["pyproject.toml", "api/orders.py", "orders/history.py"], 22, 22),
    ("Add OpenAPI schema validation to CI", "codex", "gpt-5-codex", 3.7, "passed",
     [".github/workflows/ci.yml", "tests/test_openapi.py"], 41, 2),
    ("Replace requests with httpx in the billing client", "claude", "sonnet", 2.6, "passed",
     ["billing/client.py", "tests/test_billing.py", "pyproject.toml"], 57, 49),
    ("Fix flaky test_cache_eviction under parallel pytest", "copilot", "gpt-5", 1.4, "passed",
     ["tests/test_cache.py"], 11, 7),
    ("Add request-id propagation to structured logs", "opencode", "kimi-k2", 0.6, "passed",
     ["logging/middleware.py", "logging/format.py", "tests/test_logging.py"], 39, 5),
]
# fmt: on

OK_CHECKS = [
    ("pytest", "tests", True, None),
    ("ruff", "lint", True, None),
    ("mypy", "type_checking", True, None),
]


class _Seeder:
    """Writes one repo's state through the public StateStore API only."""

    def __init__(self, repo: Path, rng: random.Random) -> None:
        from agentops.state import StateStore
        from agentops.verification_model import VerificationProfile, VerificationProfileMode

        self.repo = repo
        self.rng = rng
        self.state = StateStore(repo / ".agentops" / "state.sqlite")
        self.profile = VerificationProfile(
            name="default",
            mode=VerificationProfileMode.CONTINUE_ON_FAILURE,
            concurrency=1,
            default_timeout_seconds=300,
            checks=(),
        )
        self.ages: dict[str, float] = {}

    def close(self) -> None:
        self.state.close()

    # -- primitives -------------------------------------------------------
    def task(self, wf, desc, role, agent, status, attempts=1, result=None, deps=()):
        from agentops.tasks import Task, TaskStatus

        task = self.state.add_task(
            Task(desc, role, wf, assigned_agent=agent, max_attempts=3, dependencies=tuple(deps))
        )
        task.status = status
        task.attempts = attempts if status != TaskStatus.PENDING else 0
        task.result = result
        if status != TaskStatus.PENDING:
            task.started_at = task.created_at
        if status in (TaskStatus.PASSED, TaskStatus.FAILED):
            task.finished_at = task.created_at
        self.state.update_task(task)
        return task

    def run(
        self,
        wf,
        task,
        agent,
        model,
        role,
        status,
        duration,
        *,
        files=(),
        stat=None,
        error=None,
        attempt=1,
        exit_code=None,
    ):
        from agentops.agent_run import AgentRunContext, AgentRunOutcome, AgentRunStatus

        run = self.state.create_agent_run(
            AgentRunContext(
                agent=agent,
                model=model,
                role=role,
                workflow_id=wf,
                task_id=task.id,
                attempt=attempt,
                prompt=task.description,
            )
        )
        if status is not None:
            if exit_code is None:
                exit_code = 0 if status == AgentRunStatus.COMPLETED else 1
            self.state.finish_agent_run(
                run.id,
                AgentRunOutcome(
                    status=status,
                    exit_code=exit_code,
                    duration_seconds=duration,
                    files_changed=tuple(files),
                    diff_stat=stat,
                    error=error,
                ),
            )
        return run

    def verify(self, wf, task, source_run, checks, duration=21.6):
        from agentops.verification_model import (
            VerificationCheckStatus,
            VerificationReport,
            VerificationReportStatus,
            VerificationRunStatus,
        )

        vr = self.state.create_verification_run(
            wf, task.id, self.profile, source_agent_run_id=source_run.id
        )
        for name, check_class, ok, reason in checks:
            check = self.state.create_verification_check(
                vr.id,
                wf,
                task.id,
                self.profile.name,
                name,
                check_class,
                ("python", "-m", name),
                None,
                300,
                True,
                "sequential",
            )
            self.state.finish_verification_check(
                check.id,
                VerificationCheckStatus.PASSED if ok else VerificationCheckStatus.FAILED,
                0 if ok else 1,
                round(self.rng.uniform(1.5, 14.0), 1),
                failure_reason=None if ok else reason,
            )
        failed = sum(1 for check in checks if not check[2])
        overall = VerificationReportStatus.FAILED if failed else VerificationReportStatus.PASSED
        self.state.finish_verification_run(
            vr.id,
            VerificationRunStatus.FAILED if failed else VerificationRunStatus.COMPLETED,
            overall,
            len(checks),
            len(checks) - failed,
            failed,
            0,
            failed,
            duration,
        )
        self.state.create_verification_report(
            VerificationReport(
                id=str(uuid4()),
                run_id=vr.id,
                workflow_id=wf,
                task_id=task.id,
                profile_name=self.profile.name,
                total_checks=len(checks),
                passed_checks=len(checks) - failed,
                failed_checks=failed,
                required_failures=failed,
                duration_seconds=duration,
                overall_status=overall,
            )
        )
        return vr

    def failure(
        self,
        wf,
        task,
        run,
        source,
        category,
        severity,
        error,
        *,
        retryable=True,
        repairable=True,
        action,
        evidence=None,
        verification_run=None,
    ):
        from agentops.failure import Failure

        self.state.create_failure(
            Failure(
                id=f"fail-{uuid4().hex[:10]}",
                workflow_id=wf,
                task_id=task.id,
                agent_run_id=run.id,
                source=source,
                category=category,
                severity=severity,
                retryable=retryable,
                repairable=repairable,
                primary_error=error,
                evidence=evidence,
                recommended_action=action,
                verification_run_id=verification_run.id if verification_run else None,
            )
        )

    def stat_line(self, files, insertions, deletions) -> str:
        lines = [f" {name} | {self.rng.randint(3, 40)} {'+' * 6}{'-' * 2}" for name in files]
        lines.append(
            f" {len(files)} files changed, {insertions} insertions(+), {deletions} deletions(-)"
        )
        return "\n".join(lines) + "\n"

    # -- workflows --------------------------------------------------------
    def settled(self, desc, agent, model, days, outcome, files, insertions, deletions) -> str:
        from agentops.agent_run import AgentRunStatus
        from agentops.artifacts import Artifact, ArtifactKind
        from agentops.failure import FailureCategory, FailureSeverity, FailureSource, RepairAction
        from agentops.git import WorktreeRef
        from agentops.tasks import TaskStatus

        ok = outcome == "passed"
        wf = self.state.create_workflow(desc)
        plan = self.task(
            wf, f"Plan: {desc}", "architecture", agent, TaskStatus.PASSED, result="plan written"
        )
        impl = self.task(
            wf,
            f"Implement: {desc}",
            "implementation",
            agent,
            TaskStatus.PASSED if ok else TaskStatus.FAILED,
            attempts=1 if ok else 3,
            result="implemented" if ok else None,
            deps=[plan.id],
        )
        blocked = TaskStatus.PASSED if ok else TaskStatus.BLOCKED
        ver = self.task(
            wf, "Run verification profile", "verification", agent, blocked, deps=[impl.id]
        )
        rev = self.task(
            wf,
            "Review the change",
            "review",
            "claude",
            blocked,
            result="approved" if ok else None,
            deps=[ver.id],
        )
        self.run(
            wf,
            plan,
            agent,
            model,
            "architecture",
            AgentRunStatus.COMPLETED,
            self.rng.uniform(30, 90),
        )
        if outcome == "timeout":
            run = self.run(
                wf,
                impl,
                agent,
                model,
                "implementation",
                AgentRunStatus.TIMED_OUT,
                1800.0,
                error="agent exceeded 1800s timeout",
            )
            self.failure(
                wf,
                impl,
                run,
                FailureSource.AGENT,
                FailureCategory.TIMEOUT,
                FailureSeverity.MEDIUM,
                "Agent exceeded 1800s timeout",
                repairable=False,
                action=RepairAction.RETRY_SAME_AGENT,
            )
        else:
            run = self.run(
                wf,
                impl,
                agent,
                model,
                "implementation",
                AgentRunStatus.COMPLETED,
                self.rng.uniform(120, 420),
                files=files,
                stat=self.stat_line(files, insertions, deletions),
            )
            if ok:
                self.verify(wf, ver, run, OK_CHECKS)
                self.run(
                    wf,
                    rev,
                    "claude",
                    "sonnet",
                    "review",
                    AgentRunStatus.COMPLETED,
                    self.rng.uniform(40, 80),
                )
            else:
                vr = self.verify(
                    wf,
                    ver,
                    run,
                    [
                        (
                            "pytest",
                            "tests",
                            False,
                            "2 failed: test_products_cache_hit, test_cache_ttl",
                        ),
                        ("ruff", "lint", True, None),
                        ("mypy", "type_checking", True, None),
                    ],
                )
                self.failure(
                    wf,
                    impl,
                    run,
                    FailureSource.VERIFICATION,
                    FailureCategory.TEST_FAILURE,
                    FailureSeverity.HIGH,
                    "AssertionError: cache miss on second GET /v1/products",
                    action=RepairAction.REPAIR_IMPLEMENTATION,
                    verification_run=vr,
                )
        slug = "-".join(desc.lower().replace("/", " ").split()[:3])[:28].strip("-")
        self.state.record_worktree_ref(
            WorktreeRef(
                id="",
                workflow_id=wf,
                path=f".agentops/worktrees/{slug}-{wf[:4]}",
                branch=f"agentops/{slug}-{wf[:4]}",
                base_branch="main",
                base_commit="0" * 40,
            )
        )
        if ok:
            self.state.create_artifact_record(
                Artifact(
                    workflow_id=wf,
                    task_id=plan.id,
                    kind=ArtifactKind.PLANS,
                    name=f"plan-{wf[:6]}.md",
                )
            )
        self.state.refresh_workflow_status(wf)
        self.ages[wf] = days
        return wf

    def failed_upgrade(self) -> str:
        from agentops.agent_run import AgentRunStatus
        from agentops.failure import FailureCategory, FailureSeverity, FailureSource, RepairAction
        from agentops.tasks import TaskStatus

        wf = self.state.create_workflow("Upgrade Django 4.2 to 5.1 and fix deprecations")
        bump = self.task(
            wf,
            "Bump dependencies and fix imports",
            "implementation",
            "claude",
            TaskStatus.FAILED,
            attempts=3,
        )
        self.task(
            wf, "Run full test suite", "verification", "codex", TaskStatus.BLOCKED, deps=[bump.id]
        )
        timed_out = self.run(
            wf,
            bump,
            "claude",
            "sonnet",
            "implementation",
            AgentRunStatus.TIMED_OUT,
            1800.0,
            error="agent exceeded 1800s timeout",
        )
        conflict = self.run(
            wf,
            bump,
            "claude",
            "sonnet",
            "implementation",
            AgentRunStatus.FAILED,
            95.1,
            attempt=3,
            exit_code=128,
            error="merge conflict in settings/base.py",
        )
        self.failure(
            wf,
            bump,
            timed_out,
            FailureSource.AGENT,
            FailureCategory.TIMEOUT,
            FailureSeverity.MEDIUM,
            "Agent exceeded 1800s timeout",
            repairable=False,
            action=RepairAction.RETRY_SAME_AGENT,
        )
        self.failure(
            wf,
            bump,
            conflict,
            FailureSource.GIT,
            FailureCategory.GIT_CONFLICT,
            FailureSeverity.CRITICAL,
            "CONFLICT (content): Merge conflict in settings/base.py",
            retryable=False,
            action=RepairAction.STOP,
        )
        self.state.refresh_workflow_status(wf)
        self.ages[wf] = 0.35
        return wf

    def pending(self, desc: str, agent: str, days: float) -> str:
        from agentops.tasks import TaskStatus

        wf = self.state.create_workflow(desc)
        self.task(wf, desc, "implementation", agent, TaskStatus.PENDING)
        self.state.refresh_workflow_status(wf)
        self.ages[wf] = days
        return wf

    def running_jwt(self, worktree_root: Path, base_commit: str) -> tuple[str, Path, str]:
        from agentops.agent_run import AgentRunStatus
        from agentops.failure import FailureCategory, FailureSeverity, FailureSource, RepairAction
        from agentops.git import WorktreeRef
        from agentops.tasks import TaskStatus

        wf = self.state.create_workflow("Migrate auth sessions from cookies to JWT refresh tokens")
        design = self.task(
            wf,
            "Design JWT refresh flow",
            "architecture",
            "codex",
            TaskStatus.PASSED,
            result="Access tokens: 15 min HS256 JWT. Refresh tokens: opaque, 14 days, "
            "rotated on use, stored in TokenStore.",
        )
        impl = self.task(
            wf,
            "Implement refresh endpoint and token store",
            "implementation",
            "codex",
            TaskStatus.RUNNING,
            attempts=2,
            deps=[design.id],
        )
        self.task(
            wf,
            "Verify auth flow end to end",
            "verification",
            "codex",
            TaskStatus.PENDING,
            deps=[impl.id],
        )
        self.task(wf, "Security review", "review", "claude", TaskStatus.PENDING, deps=[impl.id])
        self.run(wf, design, "codex", "gpt-5-codex", "architecture", AgentRunStatus.COMPLETED, 66.0)
        files = ["auth/tokens.py", "auth/routes.py", "tests/test_auth.py"]
        first = self.run(
            wf,
            impl,
            "codex",
            "gpt-5-codex",
            "implementation",
            AgentRunStatus.FAILED,
            188.3,
            files=files,
            stat=self.stat_line(files, 41, 9),
            error="pytest exited 1: 3 failed, 41 passed",
        )
        vr = self.verify(
            wf,
            impl,
            first,
            [
                (
                    "pytest",
                    "tests",
                    False,
                    "3 failed, 41 passed (tests/test_auth.py::test_refresh_expired_token)",
                ),
                ("ruff", "lint", True, None),
                (
                    "mypy",
                    "type_checking",
                    False,
                    'auth/tokens.py:22: error: Item "None" has no attribute "user_id"',
                ),
            ],
        )
        self.failure(
            wf,
            impl,
            first,
            FailureSource.VERIFICATION,
            FailureCategory.TEST_FAILURE,
            FailureSeverity.HIGH,
            "AssertionError: expected 401, got 500 in test_refresh_expired_token",
            evidence="tests/test_auth.py::test_refresh_expired_token FAILED",
            action=RepairAction.RETRY_SAME_AGENT,
            verification_run=vr,
        )
        self.run(wf, impl, "codex", "gpt-5-codex", "implementation", None, None, attempt=2)
        slug = f"jwt-refresh-{wf[:4]}"
        branch = f"agentops/{slug}"
        worktree = worktree_root / slug
        self.state.record_worktree_ref(
            WorktreeRef(
                id="",
                workflow_id=wf,
                path=worktree.as_posix(),
                branch=branch,
                base_branch="main",
                base_commit=base_commit,
            )
        )
        self.state.refresh_workflow_status(wf)
        self.ages[wf] = 0.012
        return wf, worktree, branch

    # -- SEED-ONLY back-dating -------------------------------------------
    def backdate(self, now: datetime) -> None:
        """SEED-ONLY. StateStore stamps utc_now() and has no back-dating API, so this
        script rewrites timestamps with plain SQL so charts get a 14-day history.
        Each workflow starts ``age`` days ago and its rows follow by duration. This
        must never be copied into product code."""
        self.state.close()
        db = sqlite3.connect(self.repo / ".agentops" / "state.sqlite")
        db.row_factory = sqlite3.Row
        try:
            for wf, days in self.ages.items():
                self._backdate_workflow(db, wf, now - timedelta(days=days), now)
            db.commit()
        finally:
            db.close()
        from agentops.state import StateStore

        self.state = StateStore(self.repo / ".agentops" / "state.sqlite")

    @staticmethod
    def _backdate_workflow(db: sqlite3.Connection, wf: str, t0: datetime, now: datetime) -> None:
        iso = datetime.isoformat
        db.execute("UPDATE workflows SET created_at=? WHERE id=?", (iso(t0), wf))
        cursor = t0 + timedelta(seconds=4)
        windows: dict[str, list[datetime]] = {}
        runs = db.execute(
            "SELECT id, task_id, duration_seconds, status FROM agent_runs "
            "WHERE workflow_id=? ORDER BY rowid",
            (wf,),
        ).fetchall()
        for run in runs:
            if run["duration_seconds"] is None:  # in flight
                start = now - timedelta(seconds=131)
                db.execute(
                    "UPDATE agent_runs SET created_at=?, started_at=?, updated_at=? WHERE id=?",
                    (iso(start), iso(start), iso(start), run["id"]),
                )
                windows.setdefault(run["task_id"], [start, now])[1] = now
                continue
            start = cursor
            end = start + timedelta(seconds=float(run["duration_seconds"]))
            db.execute(
                "UPDATE agent_runs SET created_at=?, started_at=?, ended_at=?, updated_at=? "
                "WHERE id=?",
                (iso(start), iso(start), iso(end), iso(end), run["id"]),
            )
            windows.setdefault(run["task_id"], [start, end])[1] = end
            for vr in db.execute(
                "SELECT id, duration_seconds FROM verification_runs WHERE source_agent_run_id=?",
                (run["id"],),
            ).fetchall():
                vs = end + timedelta(seconds=2)
                ve = vs + timedelta(seconds=float(vr["duration_seconds"] or 20))
                stamps = (iso(vs), iso(vs), iso(ve), iso(ve))
                db.execute(
                    "UPDATE verification_runs SET created_at=?, started_at=?, ended_at=?, "
                    "updated_at=? WHERE id=?",
                    (*stamps, vr["id"]),
                )
                db.execute(
                    "UPDATE verification_checks SET created_at=?, started_at=?, ended_at=?, "
                    "updated_at=? WHERE run_id=?",
                    (*stamps, vr["id"]),
                )
                db.execute(
                    "UPDATE failures SET created_at=?, updated_at=? WHERE verification_run_id=?",
                    (iso(ve), iso(ve), vr["id"]),
                )
                db.execute(
                    "UPDATE verification_reports SET generated_at=? WHERE run_id=?",
                    (iso(ve), vr["id"]),
                )
                end = ve
            db.execute(
                "UPDATE failures SET created_at=?, updated_at=? "
                "WHERE agent_run_id=? AND verification_run_id IS NULL",
                (iso(end), iso(end), run["id"]),
            )
            cursor = end + timedelta(seconds=3)
        for task_id, (start, end) in windows.items():
            db.execute(
                "UPDATE tasks SET created_at=?, started_at=?, finished_at=CASE WHEN finished_at "
                "IS NULL THEN NULL ELSE ? END, updated_at=? WHERE id=?",
                (iso(t0), iso(start), iso(end), iso(end), task_id),
            )
        db.execute(
            "UPDATE tasks SET created_at=?, updated_at=? WHERE workflow_id=? AND started_at IS NULL",
            (iso(t0), iso(t0), wf),
        )
        last = iso(min(cursor, now))
        db.execute("UPDATE workflows SET updated_at=? WHERE id=?", (last, wf))
        db.execute("UPDATE artifacts SET created_at=? WHERE workflow_id=?", (last, wf))
        db.execute("UPDATE worktree_refs SET created_at=? WHERE workflow_id=?", (iso(t0), wf))
        events = db.execute(
            "SELECT id FROM events WHERE workflow_id=? ORDER BY rowid", (wf,)
        ).fetchall()
        span = max(1.0, (min(cursor, now) - t0).total_seconds())
        for index, event in enumerate(events):
            stamp = t0 + timedelta(seconds=span * (index + 1) / (len(events) + 1))
            db.execute("UPDATE events SET created_at=? WHERE id=?", (iso(stamp), event["id"]))

    def typed_events(self) -> None:
        """Typed timeline events at the back-dated times, with explicit timestamps,
        through the public record_typed_event API (the dashboard feed reads them)."""
        from agentops.events import Event, EventSeverity, EventType

        db = sqlite3.connect(self.repo / ".agentops" / "state.sqlite")
        db.row_factory = sqlite3.Row
        events: list[Event] = []
        try:
            for wf in db.execute(
                "SELECT id, description, created_at, updated_at, status FROM workflows"
            ):
                events.append(
                    Event(
                        timestamp=wf["created_at"],
                        workflow_id=wf["id"],
                        type=EventType.WORKFLOW_CREATED,
                        message=f"Workflow created: {wf['description']}",
                    )
                )
                if wf["status"] == "passed":
                    events.append(
                        Event(
                            timestamp=wf["updated_at"],
                            workflow_id=wf["id"],
                            type=EventType.MERGE_COMPLETED,
                            message=f"Merged into main: {wf['description']}",
                        )
                    )
            for run in db.execute(
                "SELECT id, workflow_id, task_id, agent, role, status, "
                "started_at, ended_at, error FROM agent_runs"
            ):
                common = {
                    "workflow_id": run["workflow_id"],
                    "task_id": run["task_id"],
                    "agent_run_id": run["id"],
                }
                events.append(
                    Event(
                        timestamp=run["started_at"],
                        type=EventType.AGENT_STARTED,
                        message=f"{run['agent']} started {run['role']}",
                        **common,
                    )
                )
                if run["ended_at"]:
                    bad = run["status"] in ("failed", "timed_out")
                    message = f"{run['agent']} {'failed' if bad else 'finished'} {run['role']}"
                    if bad and run["error"]:
                        message += f": {run['error']}"
                    events.append(
                        Event(
                            timestamp=run["ended_at"],
                            type=EventType.AGENT_FINISHED,
                            severity=EventSeverity.ERROR if bad else EventSeverity.INFO,
                            message=message,
                            **common,
                        )
                    )
            for vr in db.execute(
                "SELECT workflow_id, task_id, ended_at, passed_checks, "
                "total_checks, overall_status FROM verification_runs"
            ):
                ok = vr["overall_status"] == "passed"
                events.append(
                    Event(
                        timestamp=vr["ended_at"],
                        workflow_id=vr["workflow_id"],
                        task_id=vr["task_id"],
                        type=EventType.VERIFICATION_COMPLETED
                        if ok
                        else EventType.VERIFICATION_FAILED,
                        severity=EventSeverity.INFO if ok else EventSeverity.ERROR,
                        message=f"Verification {'passed' if ok else 'failed'}: "
                        f"{vr['passed_checks']}/{vr['total_checks']} checks",
                    )
                )
        finally:
            db.close()
        for event in sorted(events, key=lambda item: item.timestamp):
            self.state.record_typed_event(event)


def seed(base: Path) -> Seeded:
    """Create the demo workspace under ``base`` and return what was made."""
    base = Path(base)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rng = random.Random(7)

    repo = _make_repo(
        base / "demo-service",
        {
            "README.md": "# demo-service\n",
            "auth/__init__.py": "",
            "auth/tokens.py": TOKENS_PY,
            "auth/routes.py": ROUTES_PY,
            "auth/store.py": "class TokenStore: ...\n",
            "tests/test_auth.py": TEST_AUTH_PY,
        },
    )
    base_commit = _git(repo, "rev-parse", "HEAD").strip()
    main = _Seeder(repo, rng)
    try:
        for row in SETTLED:
            main.settled(*row)
        failed = main.failed_upgrade()
        pending = main.pending("Write onboarding docs for the CLI", "opencode", 0.05)
        running, worktree, branch = main.running_jwt(repo / ".agentops" / "worktrees", base_commit)
        main.backdate(now)
        main.typed_events()
    finally:
        main.close()

    # A real managed worktree with real uncommitted edits, so the diff pane has data.
    _git(repo, "worktree", "add", "-q", "-b", branch, str(worktree))
    (worktree / "auth/tokens.py").write_text(TOKENS_PY_NEW, encoding="utf-8")
    (worktree / "auth/routes.py").write_text(ROUTES_PY_NEW, encoding="utf-8")
    (worktree / "tests/test_auth.py").write_text(TEST_AUTH_PY_NEW, encoding="utf-8")

    second = _make_repo(
        base / "docs-site", {"README.md": "# docs-site\n", "docs/index.md": "# Docs\n"}
    )
    docs = _Seeder(second, rng)
    try:
        docs.settled(
            "Generate API reference from OpenAPI spec",
            "opencode",
            "kimi-k2",
            5.5,
            "passed",
            ["docs/api.md", "scripts/gen_api.py"],
            80,
            12,
        )
        docs.settled(
            "Fix broken links found by lychee",
            "kilo",
            "glm-4.6",
            2.1,
            "passed",
            ["docs/index.md", "docs/deploy.md"],
            14,
            9,
        )
        docs.pending("Add dark-mode toggle to the docs theme", "copilot", 0.2)
        docs.backdate(now)
        docs.typed_events()
    finally:
        docs.close()

    empty = base / "empty"
    empty.mkdir(parents=True, exist_ok=True)
    _git(empty, "init", "-q", "-b", "main")
    return Seeded(base, repo, second, empty, running, failed, pending, worktree)


# ----------------------------------------------------------------------------
# Capture
# ----------------------------------------------------------------------------


def configure_app():
    """The process QApplication, set up the way ``agentops.gui.app.run`` does."""
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from agentops.gui.tokens import DARK, apply_theme

    app = QApplication.instance() or QApplication([])
    if sys.platform != "win32":
        # Linux/macOS have no Segoe UI; substitute so metrics stay close to Windows.
        QFont.insertSubstitution(DARK.font_family, os.environ.get("AO_FONT_SUB", "DejaVu Sans"))
    # Same Fusion style, palette, font and stylesheet as agentops.gui.app.run.
    apply_theme(app, DARK)
    return app


def pump(milliseconds: int = 300) -> None:
    from PySide6.QtTest import QTest

    QTest.qWait(milliseconds)


@contextmanager
def no_blocking_dialogs() -> Iterator[None]:
    """QMessageBox.question answers Yes, so nothing modal blocks an offscreen run."""
    from unittest.mock import patch

    from PySide6.QtWidgets import QMessageBox

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        yield


def open_window(repos: list[Path], settings_file: Path, width: int, height: int):
    """The real MainWindow on ``repos[0]`` (the rest are recent repos)."""
    from agentops.gui.settings import AppSettings
    from agentops.gui.shell import MainWindow
    from agentops.gui_controller import AgentOpsController

    settings = AppSettings()
    for repo in reversed(repos):
        settings.touch_repository(str(repo))
    window = MainWindow(AgentOpsController(), settings=settings, settings_file=settings_file)
    track_bridge(window)
    window.resize(width, height)
    window.show()
    pump(1200)
    return window


def track_bridge(window) -> None:
    """Count the window's in-flight ``ControllerBridge`` jobs (snapshot-side only).

    Every view load goes through one shared 4-worker pool, and only the newest
    request per view key may deliver. On a slow runner the pool backs up (each
    controller read opens a ``StateStore``; the dashboard summary alone runs
    several), so a fixed per-view wait captures "Loading..." tables. Waiting
    for this counter to reach zero waits for the real work instead.
    """
    bridge = window._bridge
    original = bridge.submit
    bridge.snapshot_in_flight = 0
    lock = threading.Lock()

    def submit(token, work):
        def tracked():
            try:
                return work()
            finally:
                with lock:
                    bridge.snapshot_in_flight -= 1

        with lock:
            bridge.snapshot_in_flight += 1
        try:
            original(token, tracked)
        except BaseException:
            with lock:
                bridge.snapshot_in_flight -= 1
            raise

    bridge.submit = submit


def in_flight(window) -> int:
    """Bridge jobs still running or queued (0 once ``wait_until_idle`` returned)."""
    return int(getattr(window._bridge, "snapshot_in_flight", 0))


def wait_until_idle(window, timeout_ms: int = IDLE_TIMEOUT_MS) -> None:
    """Pump until no bridge job is in flight, then deliver the queued signals.

    Raises instead of returning early: a capture taken while loads are still
    queued shows empty tables and silently passes the label/pixel checks.
    """
    bridge = window._bridge
    waited = 0
    while getattr(bridge, "snapshot_in_flight", 0) > 0:
        if waited >= timeout_ms:
            raise TimeoutError(
                f"{bridge.snapshot_in_flight} GUI worker job(s) still running after "
                f"{timeout_ms / 1000:.0f} s; the views would be captured half-loaded"
            )
        pump(50)
        waited += 50
    pump(50)  # result signals are queued to the GUI thread; let them land


def close_window(window) -> None:
    with no_blocking_dialogs():
        window.close()
        window.deleteLater()
        pump(100)


def view_table(window, view_id: str):
    """The view's main ``TablePanel`` (``_table`` on list views, ``_list`` on
    Workflows), or None for views without one (dashboard, settings)."""
    view = window._views[view_id]
    for name in ("_table", "_list"):
        table = getattr(view, name, None)
        if hasattr(table, "proxy") and hasattr(table, "select_row_index"):
            return table
    return None


def table_state(window, view_id: str) -> tuple[int, str]:
    """(row count, empty-state text) of the view's table; (-1, "") without one."""
    table = view_table(window, view_id)
    if table is None:
        return -1, ""
    empty = getattr(table, "_empty", None)
    text = ""
    if empty is not None:
        heading = getattr(empty, "_heading", None)
        message = getattr(empty, "_message", None)
        text = " / ".join(w.text() for w in (heading, message) if w is not None and w.text())
    return table.proxy.rowCount(), text


def selected_row(window, view_id: str) -> dict | None:
    """The row selected in the view's table, or None."""
    table = view_table(window, view_id)
    return table.selected_row() if table is not None else None


def select_first_row(window, view_id: str) -> None:
    """Select row 0 in the view's table (rows are loaded: call after wait_until_idle)."""
    table = view_table(window, view_id)
    if table is not None and table.proxy.rowCount():
        table.select_row_index(0)
        pump(100)


def iter_views(window, *, settle_ms: int = 900) -> Iterator[str]:
    """Visit every VIEW_SPECS view (row 0 selected) and yield its id while shown.

    Each step waits for the bridge to go idle rather than for a fixed time, so
    the view is captured with its rows and detail pane loaded however slow the
    machine is (see ``track_bridge``).
    """
    from agentops.gui.views import VIEW_SPECS

    for view_id, _title, _factory in VIEW_SPECS:  # prime async loads (git, artifacts)
        window.navigate(view_id)
        pump(300)
    wait_until_idle(window)
    for view_id, _title, _factory in VIEW_SPECS:
        window.navigate(view_id)  # re-navigating refreshes: the newest load must land
        pump(settle_ms)
        wait_until_idle(window)
        if view_id != "dashboard":
            select_first_row(window, view_id)
            wait_until_idle(window)  # detail-pane loads for the selected row
        wait_until_settled(window)
        yield view_id


def _layout_signature(window) -> tuple:
    """Visible labels with their text and geometry: changes while async loads land."""
    from PySide6.QtWidgets import QLabel

    return tuple(
        (label.text(), label.width(), label.height())
        for label in window.findChildren(QLabel)
        if label.isVisible()
    )


def wait_until_settled(
    window, *, step_ms: int = 150, stable_polls: int = 3, timeout_ms: int = 5000
):
    """Pump until the visible labels stop changing for ``stable_polls`` polls in a
    row, so slow machines capture the loaded view rather than a half-loaded one."""
    previous, stable, waited = _layout_signature(window), 0, 0
    while stable < stable_polls and waited < timeout_ms:
        pump(step_ms)
        waited += step_ms
        current = _layout_signature(window)
        stable = stable + 1 if current == previous else 0
        previous = current


def dismiss_toasts(window) -> None:
    """Dismiss open toast cards so they do not linger into later shots."""
    from PySide6.QtWidgets import QFrame

    for card in window.findChildren(QFrame):
        if type(card).__name__ == "_Toast" and hasattr(card, "dismiss"):
            card.dismiss()
    pump(600)


def grab_with_overlays(widget, overlays=()):
    """``widget.grab()`` with separate top-level popups (the command palette is a
    Tool window, which ``grab()`` of the main window does not include) painted
    over it at their on-screen positions."""
    from PySide6.QtGui import QPainter

    pixmap = widget.grab()
    visible = [overlay for overlay in overlays if overlay.isVisible()]
    if visible:
        painter = QPainter(pixmap)
        for overlay in visible:
            painter.drawPixmap(
                widget.mapFromGlobal(overlay.mapToGlobal(overlay.rect().topLeft())), overlay.grab()
            )
        painter.end()
    return pixmap


def capture(seeded: Seeded, out: Path, width: int, height: int) -> list[Path]:
    """Write every snapshot PNG into ``out`` and return the paths."""
    from agentops.gui.new_task import NewTaskDialog

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    tag = f"{width}x{height}"
    written: list[Path] = []

    def shot(widget, name: str, overlays=()) -> None:
        path = out / f"{name}-{tag}.png"
        if not grab_with_overlays(widget, overlays).save(str(path)):
            raise RuntimeError(f"could not write {path}")
        written.append(path)
        print(path)

    settings_dir = Path(tempfile.mkdtemp(prefix="agentops-snap-settings-"))
    with no_blocking_dialogs():
        window = open_window(
            [seeded.repo, seeded.second_repo], settings_dir / "a.json", width, height
        )
        try:
            for view_id in iter_views(window):
                shot(window, view_id)

            window.open_task(seeded.running_workflow)
            pump(1200)
            wait_until_idle(window)
            shot(window, "workflow-running")
            window.open_task(seeded.failed_workflow)
            pump(1200)
            wait_until_idle(window)
            shot(window, "workflow-failed")
            workspace = window._views["workflows"]
            if not workspace.pane_open():  # below 1280px: the pane over the thread list
                workspace._pane_toggle.click()
                pump(900)
                wait_until_idle(window)
                shot(window, "workflow-diff")
                workspace._pane_toggle.click()
                pump(300)

            window.navigate("dashboard")
            pump(600)
            window.toast(
                "Verification failed", "pytest: 3 failed, 41 passed in auth/tokens.py", "error"
            )
            window.toast("Task started", "Migrate auth sessions from cookies to JWT", "info")
            pump(500)
            shot(window, "toast")
            dismiss_toasts(window)

            window.open_palette()
            pump(500)
            shot(window, "palette", overlays=[window._palette])
            window._palette.hide()
            pump(200)

            dialog = NewTaskDialog(window._views_ctx, window)
            dialog.show()
            pump(900)
            wait_until_idle(window)
            shot(dialog, "dialog-new-task")
            dialog.close()
            dialog.deleteLater()

            window.toggle_sidebar()
            pump(400)
            shot(window, "sidebar-collapsed")
            window.toggle_sidebar()
            pump(200)
        finally:
            close_window(window)

        empty = open_window([seeded.empty_repo], settings_dir / "b.json", width, height)
        try:
            for view_id in ("dashboard", "tasks", "failures"):
                empty.navigate(view_id)
                pump(900)
                wait_until_idle(empty)
                shot(empty, f"empty-{view_id}")
        finally:
            close_window(empty)
    return written


def _parse_size(text: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in text.lower().split("x"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected WxH, got {text!r}") from error
    if width < 200 or height < 200:
        raise argparse.ArgumentTypeError("size must be at least 200x200")
    return width, height


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--size", type=_parse_size, default=(1440, 900), help="WxH")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument("--scale", type=float, default=1.0, help="QT_SCALE_FACTOR, e.g. 1.5")
    parser.add_argument(
        "--keep-seed", action="store_true", help="keep the seeded temp workspace and print its path"
    )
    args = parser.parse_args(argv)

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if args.scale != 1.0:
        os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    configure_app()

    base = Path(tempfile.mkdtemp(prefix="agentops-snap-"))
    seeded = seed(base)
    width, height = args.size
    written = capture(seeded, args.out, width, height)
    if args.keep_seed:
        print(f"seed kept at {base}")
    else:
        import shutil

        shutil.rmtree(base, ignore_errors=True)
    print(f"wrote {len(written)} snapshots to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
