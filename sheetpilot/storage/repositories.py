"""Parameterized SQLite repositories for templates, settings, and aggregate history."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ValidationError

from sheetpilot.core.exceptions import StorageError
from sheetpilot.storage.database import Database
from sheetpilot.storage.models import (
    DEFAULT_SETTINGS,
    JobHistoryRecord,
    JobStatus,
    SettingKey,
    SettingRecord,
    SettingValue,
    ValidationSummary,
    WorkflowTemplate,
    utc_now,
)


def _parse_model_json[ModelT: BaseModel](
    model_type: type[ModelT], payload: str, *, record_type: str
) -> ModelT:
    try:
        return model_type.model_validate_json(payload)
    except (ValidationError, ValueError, TypeError) as error:
        raise StorageError(f"Stored {record_type} metadata is invalid.") from error


def _validate_page(limit: int, offset: int) -> None:
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError("list pagination requires limit 1..500 and a non-negative offset")


class WorkflowTemplateRepository:
    """CRUD for file-independent, validated workflow blueprints."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save(self, template: WorkflowTemplate) -> WorkflowTemplate:
        payload = template.model_dump_json()
        try:
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO workflow_templates(
                        workflow_id, name, plan_json, created_at, updated_at,
                        description, source_slot_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(workflow_id) DO UPDATE SET
                        name = excluded.name,
                        plan_json = excluded.plan_json,
                        updated_at = excluded.updated_at,
                        description = excluded.description,
                        source_slot_count = excluded.source_slot_count
                    """,
                    (
                        str(template.workflow_id),
                        template.name,
                        payload,
                        template.created_at.isoformat(),
                        template.updated_at.isoformat(),
                        template.description,
                        len(template.source_slots),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise StorageError("A workflow template with that name already exists.") from error
        return template

    def get(self, workflow_id: UUID) -> WorkflowTemplate | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT plan_json FROM workflow_templates WHERE workflow_id = ?",
                (str(workflow_id),),
            ).fetchone()
        if row is None:
            return None
        return _parse_model_json(
            WorkflowTemplate, str(row["plan_json"]), record_type="workflow template"
        )

    def get_required(self, workflow_id: UUID) -> WorkflowTemplate:
        template = self.get(workflow_id)
        if template is None:
            raise StorageError("The requested workflow template does not exist.")
        return template

    def list(
        self,
        *,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[WorkflowTemplate, ...]:
        _validate_page(limit, offset)
        parameters: list[object] = []
        if search is not None and search.strip():
            parameters.append(search.strip())
            query = """SELECT plan_json FROM workflow_templates
                WHERE instr(lower(name || ' ' || description), lower(?)) > 0
                ORDER BY updated_at DESC, name ASC LIMIT ? OFFSET ?"""
        else:
            query = """SELECT plan_json FROM workflow_templates
                ORDER BY updated_at DESC, name ASC LIMIT ? OFFSET ?"""
        parameters.extend((limit, offset))
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(
            _parse_model_json(
                WorkflowTemplate, str(row["plan_json"]), record_type="workflow template"
            )
            for row in rows
        )

    def delete(self, workflow_id: UUID) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute(
                """DELETE FROM workflow_templates WHERE workflow_id = ?
                    AND NOT EXISTS (
                        SELECT 1 FROM jobs WHERE jobs.workflow_id = workflow_templates.workflow_id
                    )""",
                (str(workflow_id),),
            )
            if cursor.rowcount == 1:
                return True
            exists = connection.execute(
                "SELECT 1 FROM workflow_templates WHERE workflow_id = ?", (str(workflow_id),)
            ).fetchone()
            if exists is not None:
                raise StorageError("A workflow linked to job history cannot be deleted.")
            return False


class JobHistoryRepository:
    """Job lifecycle metadata without client paths, rows, or cell values."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save(self, record: JobHistoryRecord) -> JobHistoryRecord:
        source_json = json.dumps(
            [item.model_dump(mode="json") for item in record.source_files],
            separators=(",", ":"),
        )
        output_json = record.output_file.model_dump_json() if record.output_file else None
        audit_json = record.audit.model_dump_json() if record.audit else None
        try:
            with self.database.connect() as connection:
                existing_row = connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (str(record.job_id),)
                ).fetchone()
                if existing_row is not None:
                    existing = self._row_to_record(existing_row)
                    if existing == record:
                        return existing
                    self._validate_update(existing, record)
                connection.execute(
                    """
                    INSERT INTO jobs(
                        job_id, name, status, created_at, completed_at,
                        source_metadata_json, output_metadata_json, workflow_id,
                        step_count, warning_count, validation_result, backup_path,
                        application_version, audit_metadata_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        name = excluded.name,
                        status = excluded.status,
                        completed_at = excluded.completed_at,
                        source_metadata_json = excluded.source_metadata_json,
                        output_metadata_json = excluded.output_metadata_json,
                        workflow_id = excluded.workflow_id,
                        step_count = excluded.step_count,
                        warning_count = excluded.warning_count,
                        validation_result = excluded.validation_result,
                        backup_path = excluded.backup_path,
                        application_version = excluded.application_version,
                        audit_metadata_json = excluded.audit_metadata_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(record.job_id),
                        record.name,
                        record.status.value,
                        record.created_at.isoformat(),
                        record.completed_at.isoformat() if record.completed_at else None,
                        source_json,
                        output_json,
                        str(record.workflow_id) if record.workflow_id else None,
                        record.step_count,
                        record.warning_count,
                        record.validation_result.value,
                        record.backup_path,
                        record.application_version,
                        audit_json,
                        record.updated_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise StorageError("Job history metadata could not be stored.") from error
        return record

    def link_workflow(self, job_id: UUID, workflow_id: UUID) -> JobHistoryRecord:
        """Link a saved workflow without reopening otherwise immutable success evidence."""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
            if row is None:
                raise StorageError("The requested job history record does not exist.")
            current = self._row_to_record(row)
            if current.status != JobStatus.SUCCEEDED:
                raise StorageError("Only a successful job can be linked to a saved workflow.")
            if current.workflow_id is not None:
                if current.workflow_id == workflow_id:
                    return current
                raise StorageError("This job is already linked to a different saved workflow.")
            updated_at = utc_now()
            cursor = connection.execute(
                """UPDATE jobs SET workflow_id = ?, updated_at = ?
                    WHERE job_id = ? AND status = ? AND workflow_id IS NULL
                    AND EXISTS (
                        SELECT 1 FROM workflow_templates WHERE workflow_id = ?
                    )""",
                (
                    str(workflow_id),
                    updated_at.isoformat(),
                    str(job_id),
                    JobStatus.SUCCEEDED.value,
                    str(workflow_id),
                ),
            )
            if cursor.rowcount != 1:
                template_exists = connection.execute(
                    "SELECT 1 FROM workflow_templates WHERE workflow_id = ?",
                    (str(workflow_id),),
                ).fetchone()
                if template_exists is None:
                    raise StorageError("The requested workflow template does not exist.")
                raise StorageError("Job history changed before the workflow could be linked.")
            updated_row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
            if updated_row is None:
                raise StorageError("The linked job history record could not be reloaded.")
            return self._row_to_record(updated_row)

    def get(self, job_id: UUID) -> JobHistoryRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def get_required(self, job_id: UUID) -> JobHistoryRecord:
        record = self.get(job_id)
        if record is None:
            raise StorageError("The requested job history record does not exist.")
        return record

    def list(
        self,
        *,
        status: JobStatus | None = None,
        workflow_id: UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[JobHistoryRecord, ...]:
        _validate_page(limit, offset)
        parameters: list[object]
        if status is not None and workflow_id is not None:
            query = """SELECT * FROM jobs WHERE status = ? AND workflow_id = ?
                ORDER BY created_at DESC, job_id ASC LIMIT ? OFFSET ?"""
            parameters = [status.value, str(workflow_id)]
        elif status is not None:
            query = """SELECT * FROM jobs WHERE status = ?
                ORDER BY created_at DESC, job_id ASC LIMIT ? OFFSET ?"""
            parameters = [status.value]
        elif workflow_id is not None:
            query = """SELECT * FROM jobs WHERE workflow_id = ?
                ORDER BY created_at DESC, job_id ASC LIMIT ? OFFSET ?"""
            parameters = [str(workflow_id)]
        else:
            query = """SELECT * FROM jobs
                ORDER BY created_at DESC, job_id ASC LIMIT ? OFFSET ?"""
            parameters = []
        parameters.extend((limit, offset))
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def prune_completed_before(self, cutoff: datetime) -> int:
        """Delete terminal history and its summaries; active jobs are never pruned."""
        terminal = tuple(
            status.value for status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)
        )
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT job_id FROM jobs
                    WHERE completed_at IS NOT NULL AND completed_at < ?
                    AND status IN (?, ?, ?)""",
                (cutoff.isoformat(), *terminal),
            ).fetchall()
            identifiers = [str(row["job_id"]) for row in rows]
            for identifier in identifiers:
                connection.execute(
                    "DELETE FROM validation_summaries WHERE job_id = ?", (identifier,)
                )
                connection.execute("DELETE FROM jobs WHERE job_id = ?", (identifier,))
        return len(identifiers)

    @staticmethod
    def _validate_update(
        existing: JobHistoryRecord,
        proposed: JobHistoryRecord,
    ) -> None:
        immutable_fields = (
            "name",
            "created_at",
            "source_files",
            "workflow_id",
            "step_count",
            "application_version",
        )
        if any(getattr(existing, field) != getattr(proposed, field) for field in immutable_fields):
            raise StorageError("Immutable job history identity metadata cannot be changed.")

        terminal = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
        if existing.status in terminal:
            raise StorageError("Terminal job history records cannot be overwritten.")

        allowed: dict[JobStatus, set[JobStatus]] = {
            JobStatus.QUEUED: {
                JobStatus.QUEUED,
                JobStatus.ANALYSING,
                JobStatus.AWAITING_APPROVAL,
                JobStatus.RUNNING,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            },
            JobStatus.ANALYSING: {
                JobStatus.ANALYSING,
                JobStatus.AWAITING_APPROVAL,
                JobStatus.RUNNING,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            },
            JobStatus.AWAITING_APPROVAL: {
                JobStatus.AWAITING_APPROVAL,
                JobStatus.RUNNING,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            },
            JobStatus.RUNNING: {
                JobStatus.RUNNING,
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            },
        }
        if proposed.status not in allowed[existing.status]:
            raise StorageError(
                f"Invalid job history transition: {existing.status.value} -> "
                f"{proposed.status.value}."
            )
        if proposed.updated_at <= existing.updated_at:
            raise StorageError("Stale job history metadata cannot overwrite a newer record.")

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> JobHistoryRecord:
        try:
            source_files = json.loads(str(row["source_metadata_json"]))
            output = (
                json.loads(str(row["output_metadata_json"]))
                if row["output_metadata_json"]
                else None
            )
            audit = (
                json.loads(str(row["audit_metadata_json"])) if row["audit_metadata_json"] else None
            )
            return JobHistoryRecord.model_validate(
                {
                    "job_id": row["job_id"],
                    "name": row["name"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "completed_at": row["completed_at"],
                    "source_files": source_files,
                    "output_file": output,
                    "workflow_id": row["workflow_id"],
                    "step_count": row["step_count"],
                    "warning_count": row["warning_count"],
                    "validation_result": row["validation_result"] or "not_run",
                    "backup_path": row["backup_path"],
                    "audit": audit,
                    "application_version": row["application_version"],
                    "updated_at": row["updated_at"] or row["created_at"],
                }
            )
        except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as error:
            raise StorageError("Stored job history metadata is invalid.") from error


class ValidationSummaryRepository:
    """Persistence for aggregate validation results linked to jobs."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def add(self, summary: ValidationSummary) -> ValidationSummary:
        try:
            with self.database.connect() as connection:
                connection.execute(
                    """INSERT INTO validation_summaries(
                        validation_id, job_id, summary_json, created_at
                    ) VALUES (?, ?, ?, ?)""",
                    (
                        str(summary.validation_id),
                        str(summary.job_id),
                        summary.model_dump_json(),
                        summary.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise StorageError("Validation history could not be stored for this job.") from error
        return summary

    def list_for_job(self, job_id: UUID) -> tuple[ValidationSummary, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT summary_json FROM validation_summaries
                    WHERE job_id = ? ORDER BY created_at ASC, validation_id ASC""",
                (str(job_id),),
            ).fetchall()
        return tuple(
            _parse_model_json(
                ValidationSummary, str(row["summary_json"]), record_type="validation summary"
            )
            for row in rows
        )

    def list_recent(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ValidationSummary, ...]:
        """List aggregate reports across jobs, newest first, for validation history UI."""
        _validate_page(limit, offset)
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT summary_json FROM validation_summaries
                    ORDER BY created_at DESC, validation_id ASC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return tuple(
            _parse_model_json(
                ValidationSummary, str(row["summary_json"]), record_type="validation summary"
            )
            for row in rows
        )


class SettingsRepository:
    """Allowlisted application settings; credentials are intentionally unsupported."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def set(self, key: SettingKey, value: SettingValue) -> SettingRecord:
        record = SettingRecord(key=key, value=value)
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value_json = excluded.value_json,
                        updated_at = excluded.updated_at""",
                (
                    record.key.value,
                    json.dumps(record.value, separators=(",", ":")),
                    record.updated_at.isoformat(),
                ),
            )
        return record

    def get(self, key: SettingKey) -> SettingValue | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT value_json, updated_at FROM settings WHERE key = ?", (key.value,)
            ).fetchone()
        if row is None:
            return DEFAULT_SETTINGS.get(key)
        try:
            value = json.loads(str(row["value_json"]))
            record = SettingRecord(
                key=key,
                value=value,
                updated_at=row["updated_at"],
            )
        except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as error:
            raise StorageError("Stored application setting is invalid.") from error
        return record.value

    def all(self) -> dict[SettingKey, SettingValue]:
        values = dict(DEFAULT_SETTINGS)
        with self.database.connect() as connection:
            rows = connection.execute("SELECT key, value_json, updated_at FROM settings").fetchall()
        for row in rows:
            try:
                key = SettingKey(str(row["key"]))
                record = SettingRecord(
                    key=key,
                    value=json.loads(str(row["value_json"])),
                    updated_at=row["updated_at"],
                )
            except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as error:
                raise StorageError("Stored application setting is invalid.") from error
            values[key] = record.value
        return values

    def delete(self, key: SettingKey) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute("DELETE FROM settings WHERE key = ?", (key.value,))
        return cursor.rowcount == 1
