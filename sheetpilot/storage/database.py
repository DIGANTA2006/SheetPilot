"""Small explicit SQLite migration layer."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS jobs (
        job_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        completed_at TEXT,
        source_metadata_json TEXT NOT NULL,
        output_metadata_json TEXT,
        workflow_id TEXT,
        step_count INTEGER NOT NULL DEFAULT 0,
        warning_count INTEGER NOT NULL DEFAULT 0,
        validation_result TEXT,
        backup_path TEXT,
        application_version TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS workflow_templates (
        workflow_id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        plan_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS validation_summaries (
        validation_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES jobs(job_id),
        summary_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    """
    ALTER TABLE jobs ADD COLUMN audit_metadata_json TEXT;
    ALTER TABLE jobs ADD COLUMN updated_at TEXT;
    ALTER TABLE workflow_templates ADD COLUMN description TEXT NOT NULL DEFAULT '';
    ALTER TABLE workflow_templates ADD COLUMN source_slot_count INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE settings ADD COLUMN updated_at TEXT;
    CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
    CREATE INDEX IF NOT EXISTS idx_jobs_workflow_id ON jobs(workflow_id);
    CREATE INDEX IF NOT EXISTS idx_validation_summaries_job_id
        ON validation_summaries(job_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_workflow_templates_updated_at
        ON workflow_templates(updated_at DESC);
    UPDATE jobs SET updated_at = created_at WHERE updated_at IS NULL;
    UPDATE settings SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL;
    """,
)


class Database:
    """Connection factory and idempotent migration runner."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            applied = {
                int(row[0])
                for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
            }
            for version, migration in enumerate(_MIGRATIONS, start=1):
                if version in applied:
                    continue
                connection.executescript(migration)
                connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))

    def table_names(self) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        return {str(row[0]) for row in rows}
