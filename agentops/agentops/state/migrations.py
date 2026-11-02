"""Schema creation and the additive migrations (schema_migrations v1-v9)."""

from __future__ import annotations


from ..tasks import utc_now


class SchemaMixin:
    def _initialize(self) -> None:
        self._initialize_once()

    def _initialize_once(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY, description TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL REFERENCES workflows(id),
                description TEXT NOT NULL, role TEXT NOT NULL, assigned_agent TEXT,
                dependencies TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL,
                max_attempts INTEGER NOT NULL, result TEXT, verified INTEGER NOT NULL DEFAULT 0,
                verification_run_id TEXT, created_at TEXT NOT NULL,
                started_at TEXT, finished_at TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, task_id TEXT,
                kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            """
        )
        self._migrate_agent_runs()
        self._ensure_task_verification_columns()
        self._migrate_verification()
        self._migrate_failures()
        self._migrate_typed_events()
        self._migrate_artifacts()
        self._migrate_worktree_refs()
        self._migrate_hot_path_indexes()
        self._migrate_agent_cooldowns()
        self.connection.commit()

    def _migrate_agent_runs(self) -> None:
        """Apply additive AgentRun migrations without rewriting legacy tables."""

        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS agent_runs (
                id TEXT PRIMARY KEY,
                workflow_id TEXT REFERENCES workflows(id),
                task_id TEXT,
                attempt INTEGER NOT NULL DEFAULT 1,
                agent TEXT,
                executable TEXT,
                role TEXT,
                model TEXT,
                started_at TEXT,
                ended_at TEXT,
                duration_seconds REAL,
                status TEXT NOT NULL,
                exit_code INTEGER,
                cancelled INTEGER NOT NULL DEFAULT 0,
                timed_out INTEGER NOT NULL DEFAULT 0,
                terminated INTEGER NOT NULL DEFAULT 0,
                command TEXT,
                command_metadata TEXT,
                working_directory TEXT,
                worktree TEXT,
                prompt_metadata TEXT,
                stdout_path TEXT,
                stderr_path TEXT,
                log_path TEXT,
                files_changed TEXT,
                diff_stat TEXT,
                error TEXT,
                failure_classification TEXT,
                relationship TEXT NOT NULL DEFAULT 'root',
                parent_run_id TEXT REFERENCES agent_runs(id),
                retry_of TEXT REFERENCES agent_runs(id),
                repair_of TEXT REFERENCES agent_runs(id),
                structured_result TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

        # This branch also repairs databases created by an interrupted early
        # version of the migration.  All operations are additive.
        existing = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(agent_runs)").fetchall()
        }
        columns = {
            "id": "TEXT PRIMARY KEY",
            "workflow_id": "TEXT REFERENCES workflows(id)",
            "task_id": "TEXT",
            "attempt": "INTEGER NOT NULL DEFAULT 1",
            "agent": "TEXT",
            "executable": "TEXT",
            "role": "TEXT",
            "model": "TEXT",
            "started_at": "TEXT",
            "ended_at": "TEXT",
            "duration_seconds": "REAL",
            "status": "TEXT NOT NULL",
            "exit_code": "INTEGER",
            "cancelled": "INTEGER NOT NULL DEFAULT 0",
            "timed_out": "INTEGER NOT NULL DEFAULT 0",
            "terminated": "INTEGER NOT NULL DEFAULT 0",
            "command": "TEXT",
            "command_metadata": "TEXT",
            "working_directory": "TEXT",
            "worktree": "TEXT",
            "prompt_metadata": "TEXT",
            "stdout_path": "TEXT",
            "stderr_path": "TEXT",
            "log_path": "TEXT",
            "files_changed": "TEXT",
            "diff_stat": "TEXT",
            "error": "TEXT",
            "failure_classification": "TEXT",
            "relationship": "TEXT NOT NULL DEFAULT 'root'",
            "parent_run_id": "TEXT REFERENCES agent_runs(id)",
            "retry_of": "TEXT REFERENCES agent_runs(id)",
            "repair_of": "TEXT REFERENCES agent_runs(id)",
            "structured_result": "TEXT",
            "created_at": "TEXT NOT NULL",
            "updated_at": "TEXT NOT NULL",
        }
        for name, definition in columns.items():
            if name not in existing:
                self.connection.execute(f"ALTER TABLE agent_runs ADD COLUMN {name} {definition}")

        # Indexes come after the column repair: a partial table missing an
        # indexed column (workflow_id, task_id, status, created_at) would
        # otherwise fail to open with "no such column".
        self.connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_agent_runs_workflow
                ON agent_runs(workflow_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_agent_runs_task
                ON agent_runs(task_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_agent_runs_status
                ON agent_runs(status);
            """
        )

        migrated = self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 1"
        ).fetchone()
        if migrated is None:
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (1, utc_now()),
            )

    def _migrate_verification(self) -> None:
        """Apply additive Verification Kernel migrations without rewriting legacy tables."""

        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS verification_runs (
                id TEXT PRIMARY KEY,
                workflow_id TEXT REFERENCES workflows(id),
                task_id TEXT,
                profile_name TEXT NOT NULL,
                profile_snapshot TEXT,
                mode TEXT NOT NULL,
                concurrency INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL,
                source_agent_run_id TEXT REFERENCES agent_runs(id),
                started_at TEXT,
                ended_at TEXT,
                duration_seconds REAL,
                total_checks INTEGER NOT NULL DEFAULT 0,
                passed_checks INTEGER NOT NULL DEFAULT 0,
                failed_checks INTEGER NOT NULL DEFAULT 0,
                skipped_checks INTEGER NOT NULL DEFAULT 0,
                required_failures INTEGER NOT NULL DEFAULT 0,
                overall_status TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS verification_checks (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES verification_runs(id),
                workflow_id TEXT REFERENCES workflows(id),
                task_id TEXT,
                profile_name TEXT NOT NULL,
                name TEXT NOT NULL,
                check_class TEXT NOT NULL,
                command TEXT,
                working_directory TEXT,
                timeout_seconds INTEGER,
                required INTEGER NOT NULL DEFAULT 1,
                policy TEXT NOT NULL DEFAULT 'sequential',
                status TEXT NOT NULL,
                exit_code INTEGER,
                started_at TEXT,
                ended_at TEXT,
                duration_seconds REAL,
                stdout_path TEXT,
                stderr_path TEXT,
                failure_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(run_id, name)
            );
            CREATE TABLE IF NOT EXISTS verification_reports (
                id TEXT PRIMARY KEY,
                run_id TEXT UNIQUE NOT NULL REFERENCES verification_runs(id),
                workflow_id TEXT REFERENCES workflows(id),
                task_id TEXT,
                profile_name TEXT NOT NULL,
                total_checks INTEGER NOT NULL DEFAULT 0,
                passed_checks INTEGER NOT NULL DEFAULT 0,
                failed_checks INTEGER NOT NULL DEFAULT 0,
                skipped_checks INTEGER NOT NULL DEFAULT 0,
                required_failures INTEGER NOT NULL DEFAULT 0,
                duration_seconds REAL,
                overall_status TEXT NOT NULL,
                generated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_verification_runs_workflow
                ON verification_runs(workflow_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_verification_checks_run
                ON verification_checks(run_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_verification_reports_workflow
                ON verification_reports(workflow_id, generated_at);
            """
        )
        # Repair loop mirroring the agent_runs migration: databases created
        # by an interrupted early version of this migration gain any missing
        # columns additively instead of failing.
        expected_columns: dict[str, dict[str, str]] = {
            "verification_runs": {
                "profile_snapshot": "TEXT",
                "mode": "TEXT NOT NULL",
                "concurrency": "INTEGER NOT NULL DEFAULT 1",
                "source_agent_run_id": "TEXT REFERENCES agent_runs(id)",
                "started_at": "TEXT",
                "ended_at": "TEXT",
                "duration_seconds": "REAL",
                "total_checks": "INTEGER NOT NULL DEFAULT 0",
                "passed_checks": "INTEGER NOT NULL DEFAULT 0",
                "failed_checks": "INTEGER NOT NULL DEFAULT 0",
                "skipped_checks": "INTEGER NOT NULL DEFAULT 0",
                "required_failures": "INTEGER NOT NULL DEFAULT 0",
                "overall_status": "TEXT",
            },
            "verification_checks": {
                "working_directory": "TEXT",
                "timeout_seconds": "INTEGER",
                "required": "INTEGER NOT NULL DEFAULT 1",
                "policy": "TEXT NOT NULL DEFAULT 'sequential'",
                "started_at": "TEXT",
                "ended_at": "TEXT",
                "duration_seconds": "REAL",
                "stdout_path": "TEXT",
                "stderr_path": "TEXT",
                "failure_reason": "TEXT",
            },
        }
        for table, columns in expected_columns.items():
            existing = {
                row["name"]
                for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, definition in columns.items():
                if name not in existing:
                    self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        migrated = self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 2"
        ).fetchone()
        if migrated is None:
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (2, utc_now()),
            )

    def _ensure_task_verification_columns(self) -> None:
        existing = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        if "verified" not in existing:
            self.connection.execute(
                "ALTER TABLE tasks ADD COLUMN verified INTEGER NOT NULL DEFAULT 0"
            )
        if "verification_run_id" not in existing:
            self.connection.execute("ALTER TABLE tasks ADD COLUMN verification_run_id TEXT")

    # ------------------------------------------------------------------
    # Failure / recovery persistence (schema v3, additive)
    # ------------------------------------------------------------------

    def _migrate_failures(self) -> None:
        """Apply additive Failure Kernel migrations without rewriting tables."""

        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS failures (
                id TEXT PRIMARY KEY,
                workflow_id TEXT REFERENCES workflows(id),
                task_id TEXT,
                agent_run_id TEXT,
                source TEXT NOT NULL,
                category TEXT NOT NULL,
                severity TEXT NOT NULL,
                retryable INTEGER NOT NULL DEFAULT 0,
                repairable INTEGER NOT NULL DEFAULT 0,
                evidence TEXT,
                primary_error TEXT,
                verification_run_id TEXT,
                recommended_action TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1,
                repair_cycle INTEGER NOT NULL DEFAULT 0,
                recovery_state TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                structured_evidence TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_failures_workflow
                ON failures(workflow_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_failures_task
                ON failures(task_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_failures_category
                ON failures(category);
            """
        )
        migrated = self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 3"
        ).fetchone()
        if migrated is None:
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (3, utc_now()),
            )
        self._migrate_failure_evidence()

    def _migrate_failure_evidence(self) -> None:
        """Apply additive structured-evidence migration without rewriting tables."""

        existing = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(failures)").fetchall()
        }
        if "structured_evidence" not in existing:
            self.connection.execute("ALTER TABLE failures ADD COLUMN structured_evidence TEXT")
        migrated = self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 7"
        ).fetchone()
        if migrated is None:
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (7, utc_now()),
            )

    def _migrate_typed_events(self) -> None:
        """Apply additive versioned-timeline migrations without rewriting tables."""

        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS typed_events (
                id TEXT PRIMARY KEY,
                -- Run references are plain TEXT (no REFERENCES): events must
                -- record for workflows/runs that were never persisted
                -- (crash recovery, legacy imports), mirroring failures.
                workflow_id TEXT,
                task_id TEXT,
                agent_run_id TEXT,
                type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT,
                payload TEXT,
                schema_version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_typed_events_workflow
                ON typed_events(workflow_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_typed_events_task
                ON typed_events(task_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_typed_events_run
                ON typed_events(agent_run_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_typed_events_type
                ON typed_events(type, created_at);
            """
        )
        migrated = self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 4"
        ).fetchone()
        if migrated is None:
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (4, utc_now()),
            )

    def _migrate_artifacts(self) -> None:
        """Apply additive artifact-registry migrations without rewriting tables."""

        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                -- Plain TEXT refs (no REFERENCES): artifacts may describe
                -- runs/workflows recorded elsewhere or not at all.
                workflow_id TEXT,
                task_id TEXT,
                agent_run_id TEXT,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                rel_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                metadata TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_artifacts_workflow
                ON artifacts(workflow_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_artifacts_task
                ON artifacts(task_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_artifacts_kind
                ON artifacts(kind, created_at);
            """
        )
        migrated = self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 5"
        ).fetchone()
        if migrated is None:
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (5, utc_now()),
            )

    def _migrate_worktree_refs(self) -> None:
        """Phase 3: persisted worktree provenance (additive, idempotent)."""
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS worktree_refs (
                id TEXT PRIMARY KEY,
                -- Plain TEXT refs (no REFERENCES): refs must survive for
                -- workflows recorded in older DBs and crash-recovery paths.
                workflow_id TEXT NOT NULL,
                path TEXT NOT NULL,
                branch TEXT NOT NULL,
                base_branch TEXT NOT NULL,
                base_commit TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_worktree_refs_workflow
                ON worktree_refs(workflow_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_worktree_refs_path
                ON worktree_refs(path);
            """
        )
        migrated = self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 6"
        ).fetchone()
        if migrated is None:
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (6, utc_now()),
            )

    def _migrate_hot_path_indexes(self) -> None:
        """Index the task/workflow predicates used by the polling paths."""
        self.connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_workflow_created
                ON tasks(workflow_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_workflows_status
                ON workflows(status);
            """
        )
        migrated = self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 8"
        ).fetchone()
        if migrated is None:
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (8, utc_now()),
            )

    def _migrate_agent_cooldowns(self) -> None:
        """v9: per-agent usage-limit cooldowns (opt-in ``usage_limits``).

        One row per agent; a later limit hit replaces the row.  Rows are only
        written when the usage_limits config is enabled.
        """
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS agent_cooldowns (
                agent TEXT PRIMARY KEY,
                until_epoch REAL NOT NULL,
                until_at TEXT NOT NULL,
                reason TEXT,
                failure_id TEXT,
                workflow_id TEXT,
                task_id TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        migrated = self.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 9"
        ).fetchone()
        if migrated is None:
            self.connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (9, utc_now()),
            )
