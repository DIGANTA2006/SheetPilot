"""Small explicit SQLite migration layer."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

Migration = Callable[[sqlite3.Connection], None]


def _execute_statements(connection: sqlite3.Connection, statements: tuple[str, ...]) -> None:
    for statement in statements:
        connection.execute(statement)


def _migration_1(connection: sqlite3.Connection) -> None:
    _execute_statements(
        connection,
        (
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS jobs (
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
            )""",
            """CREATE TABLE IF NOT EXISTS workflow_templates (
                workflow_id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                plan_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS validation_summaries (
                validation_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(job_id),
                summary_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
        ),
    )


_TABLE_INFO_QUERIES = {
    "jobs": "PRAGMA table_info(jobs)",
    "workflow_templates": "PRAGMA table_info(workflow_templates)",
    "settings": "PRAGMA table_info(settings)",
}

_COLUMN_ADDITIONS = {
    ("jobs", "audit_metadata_json"): "ALTER TABLE jobs ADD COLUMN audit_metadata_json TEXT",
    ("jobs", "updated_at"): "ALTER TABLE jobs ADD COLUMN updated_at TEXT",
    (
        "workflow_templates",
        "description",
    ): "ALTER TABLE workflow_templates ADD COLUMN description TEXT NOT NULL DEFAULT ''",
    (
        "workflow_templates",
        "source_slot_count",
    ): """ALTER TABLE workflow_templates ADD COLUMN source_slot_count
        INTEGER NOT NULL DEFAULT 0""",
    ("settings", "updated_at"): "ALTER TABLE settings ADD COLUMN updated_at TEXT",
}


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    query = _TABLE_INFO_QUERIES.get(table)
    if query is None:
        raise ValueError("migration requested an unknown metadata table")
    return {str(row[1]) for row in connection.execute(query)}


def _add_column_if_missing(
    connection: sqlite3.Connection,
    table: str,
    column: str,
) -> None:
    if column not in _columns(connection, table):
        statement = _COLUMN_ADDITIONS.get((table, column))
        if statement is None:
            raise ValueError("migration requested an unknown metadata column")
        connection.execute(statement)


def _migration_2(connection: sqlite3.Connection) -> None:
    """Upgrade metadata tables safely, including after a legacy partial migration."""
    _add_column_if_missing(connection, "jobs", "audit_metadata_json")
    _add_column_if_missing(connection, "jobs", "updated_at")
    _add_column_if_missing(connection, "workflow_templates", "description")
    _add_column_if_missing(connection, "workflow_templates", "source_slot_count")
    _add_column_if_missing(connection, "settings", "updated_at")
    _execute_statements(
        connection,
        (
            "CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_workflow_id ON jobs(workflow_id)",
            """CREATE INDEX IF NOT EXISTS idx_validation_summaries_job_id
                ON validation_summaries(job_id, created_at DESC)""",
            """CREATE INDEX IF NOT EXISTS idx_workflow_templates_updated_at
                ON workflow_templates(updated_at DESC)""",
            "UPDATE jobs SET updated_at = created_at WHERE updated_at IS NULL",
            "UPDATE settings SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL",
        ),
    )


def _migration_3(connection: sqlite3.Connection) -> None:
    """Remove one-job upload permissions accidentally retained by legacy templates."""
    rows = connection.execute("SELECT workflow_id, plan_json FROM workflow_templates").fetchall()
    safe_privacy = {
        "mode": "local",
        "metadata_upload_consent": False,
        "raw_data_upload_consent": False,
    }
    for workflow_id, serialized in rows:
        try:
            payload = json.loads(str(serialized))
        except json.JSONDecodeError as error:
            raise sqlite3.DatabaseError(
                "Cannot safely migrate invalid workflow template metadata."
            ) from error
        if not isinstance(payload, dict):
            raise sqlite3.DatabaseError("Cannot safely migrate invalid workflow template metadata.")
        if payload.get("privacy") == safe_privacy:
            continue
        payload["privacy"] = safe_privacy
        connection.execute(
            "UPDATE workflow_templates SET plan_json = ? WHERE workflow_id = ?",
            (json.dumps(payload, separators=(",", ":")), str(workflow_id)),
        )


_MIGRATIONS: tuple[Migration, ...] = (_migration_1, _migration_2, _migration_3)


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
            for version, migrate in enumerate(_MIGRATIONS, start=1):
                if version in applied:
                    continue
                migrate(connection)
                connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))

    def table_names(self) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        return {str(row[0]) for row in rows}
