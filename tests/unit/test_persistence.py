from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from sheetpilot.core.plan_schema import (
    OperationPlan,
    OutputSettings,
    PlanStep,
    PrivacyMetadata,
    SourceReference,
    StepTarget,
)
from sheetpilot.operations.registry import build_default_registry
from sheetpilot.operations.validation import (
    ValidationIssue,
    ValidationReport,
    ValidationSeverity,
)
from sheetpilot.storage.database import Database
from sheetpilot.storage.models import (
    FileHistoryMetadata,
    JobHistoryRecord,
    JobStatus,
    SettingKey,
    ValidationResult,
)
from sheetpilot.storage.repositories import (
    JobHistoryRepository,
    SettingsRepository,
    ValidationSummaryRepository,
    WorkflowTemplateRepository,
)
from sheetpilot.storage.services import JobHistoryService, WorkflowTemplateService


def _plan(*, source_name: str = "client.csv", digest: str = "a" * 64) -> OperationPlan:
    source_id = uuid4()
    return OperationPlan(
        job_name="Customer cleanup",
        source_files=[
            SourceReference(
                source_id=source_id,
                file_name=source_name,
                sha256=digest,
                sheet_names=["CSV"],
            )
        ],
        steps=[
            PlanStep(
                step_id="clean-names",
                operation="text.clean",
                parameters={"columns": ["Name"], "actions": [{"kind": "trim"}]},
                target=StepTarget(source_id=source_id, sheet="CSV", columns=["Name"]),
                explanation="Trim selected name values",
            )
        ],
        output=OutputSettings(
            output_name="cleaned",
            format="csv",
            preserve_formatting=False,
        ),
        privacy=PrivacyMetadata(),
    )


@pytest.fixture
def database(tmp_path: Path) -> Database:
    result = Database(tmp_path / "metadata.sqlite3")
    result.initialize()
    return result


def test_template_is_file_independent_and_instantiates_fresh_plan(database: Database) -> None:
    templates = WorkflowTemplateRepository(database)
    history = JobHistoryRepository(database)
    service = WorkflowTemplateService(build_default_registry(), templates, history)
    original = _plan(source_name="private-client.csv", digest="a" * 64)

    template = service.save_validated(
        original,
        name="Reusable cleanup",
        description="Trim a selected input sheet",
    )
    with database.connect() as connection:
        stored = str(
            connection.execute(
                "SELECT plan_json FROM workflow_templates WHERE workflow_id = ?",
                (str(template.workflow_id),),
            ).fetchone()[0]
        )
    assert "private-client.csv" not in stored
    assert "a" * 64 not in stored
    assert templates.list(search="reusable") == (template,)

    replacement = SourceReference(
        file_name="next-input.csv",
        sha256="b" * 64,
        sheet_names=["Data"],
    )
    repeated = service.create_plan(
        template.workflow_id,
        {"source_1": replacement},
        {
            "output_name": "next-cleaned",
            "clean_names_input_sheet": "Data",
            "clean_names_target_columns": ["Name"],
        },
    )
    assert repeated.job_id != original.job_id
    assert repeated.source_files == [replacement]
    assert repeated.steps[0].target.source_id == replacement.source_id
    assert repeated.steps[0].target.sheet == "Data"
    assert repeated.output.output_name == "next-cleaned"


def test_template_rejects_unknown_parameters_and_incomplete_source_slots(
    database: Database,
) -> None:
    templates = WorkflowTemplateRepository(database)
    service = WorkflowTemplateService(
        build_default_registry(), templates, JobHistoryRepository(database)
    )
    template = service.save_validated(_plan(), name="Cleanup")
    replacement = SourceReference(file_name="new.csv", sha256="b" * 64)

    with pytest.raises(ValueError, match="source slots"):
        template.instantiate({})
    with pytest.raises(ValueError, match="unknown workflow parameters"):
        template.instantiate({"source_1": replacement}, {"python": "ignored"})


def test_job_and_validation_history_are_aggregate_only(database: Database) -> None:
    history = JobHistoryRepository(database)
    validations = ValidationSummaryRepository(database)
    service = JobHistoryService(history, validations)
    plan = _plan()
    service.record_started(plan)
    report = ValidationReport(
        passed=False,
        checks_run=1,
        issues=(
            ValidationIssue(
                code="invalid_email",
                severity=ValidationSeverity.ERROR,
                message="secret.person@example.com is invalid",
                column="Email",
                affected_rows=2,
                representative_row_numbers=(2, 8),
            ),
        ),
    )
    summary = service.record_validation(plan.job_id, report)
    failed = service.record_failure(plan.job_id, warning_count=1)

    assert failed.status == JobStatus.FAILED
    assert failed.validation_result == ValidationResult.FAILED
    assert validations.list_for_job(plan.job_id) == (summary,)
    with database.connect() as connection:
        stored = str(
            connection.execute(
                "SELECT summary_json FROM validation_summaries WHERE job_id = ?",
                (str(plan.job_id),),
            ).fetchone()[0]
        )
    assert "secret.person@example.com" not in stored
    assert "representative_row_numbers" not in stored
    assert summary.issue_code_counts == {"invalid_email": 1}
    assert summary.affected_row_count == 2


def test_repeat_job_uses_linked_template_and_new_sources(database: Database) -> None:
    templates = WorkflowTemplateRepository(database)
    history = JobHistoryRepository(database)
    service = WorkflowTemplateService(build_default_registry(), templates, history)
    plan = _plan()
    template = service.save_validated(plan, name="Repeatable cleanup")
    history.save(
        JobHistoryRecord(
            job_id=plan.job_id,
            name=plan.job_name,
            status=JobStatus.CANCELLED,
            created_at=plan.created_at,
            completed_at=datetime.now(UTC),
            source_files=(FileHistoryMetadata(file_name="client.csv", sha256="a" * 64),),
            workflow_id=template.workflow_id,
            step_count=1,
            application_version="0.1.0",
        )
    )
    replacement = SourceReference(file_name="replacement.csv", sha256="c" * 64)

    repeated = service.repeat_job(plan.job_id, {"source_1": replacement})

    assert repeated.job_id != plan.job_id
    assert repeated.job_name == "Customer cleanup repeat"
    assert repeated.source_files[0].file_name == "replacement.csv"


def test_job_history_filters_and_prunes_only_terminal_records(database: Database) -> None:
    repository = JobHistoryRepository(database)
    now = datetime.now(UTC)
    old = now - timedelta(days=10)
    terminal_id = uuid4()
    active_id = uuid4()
    common = {
        "name": "History test",
        "created_at": old,
        "source_files": (FileHistoryMetadata(file_name="input.csv", sha256="d" * 64),),
        "application_version": "0.1.0",
    }
    repository.save(
        JobHistoryRecord(
            job_id=terminal_id,
            status=JobStatus.CANCELLED,
            completed_at=old,
            **common,
        )
    )
    repository.save(JobHistoryRecord(job_id=active_id, status=JobStatus.RUNNING, **common))

    assert [item.job_id for item in repository.list(status=JobStatus.RUNNING)] == [active_id]
    assert repository.prune_completed_before(now - timedelta(days=1)) == 1
    assert repository.get(terminal_id) is None
    assert repository.get(active_id) is not None


def test_settings_are_allowlisted_and_typed(database: Database) -> None:
    settings = SettingsRepository(database)
    assert settings.get(SettingKey.LOCAL_ONLY) is True
    settings.set(SettingKey.HISTORY_RETENTION_DAYS, 90)
    settings.set(SettingKey.DEFAULT_OUTPUT_DIRECTORY, "C:\\Exports")

    assert settings.get(SettingKey.HISTORY_RETENTION_DAYS) == 90
    assert settings.all()[SettingKey.DEFAULT_OUTPUT_DIRECTORY] == "C:\\Exports"
    with pytest.raises(ValidationError):
        settings.set(SettingKey.RECENT_JOB_LIMIT, 0)


def test_file_history_rejects_paths() -> None:
    with pytest.raises(ValidationError, match="file name, not a path"):
        FileHistoryMetadata(file_name="C:\\Clients\\input.csv", sha256="a" * 64)


def test_second_migration_upgrades_a_version_one_database(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO schema_migrations(version) VALUES (1);
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL, completed_at TEXT, source_metadata_json TEXT NOT NULL,
            output_metadata_json TEXT, workflow_id TEXT, step_count INTEGER NOT NULL DEFAULT 0,
            warning_count INTEGER NOT NULL DEFAULT 0, validation_result TEXT, backup_path TEXT,
            application_version TEXT NOT NULL
        );
        CREATE TABLE workflow_templates (
            workflow_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, plan_json TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
        CREATE TABLE validation_summaries (
            validation_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(job_id),
            summary_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    connection.close()

    database = Database(path)
    database.initialize()
    database.initialize()

    with database.connect() as migrated:
        versions = {
            int(row[0]) for row in migrated.execute("SELECT version FROM schema_migrations")
        }
        job_columns = {str(row[1]) for row in migrated.execute("PRAGMA table_info(jobs)")}
    assert versions == {1, 2}
    assert {"audit_metadata_json", "updated_at"} <= job_columns


def test_successful_job_requires_output_metadata() -> None:
    with pytest.raises(ValidationError, match="successful jobs require output"):
        JobHistoryRecord(
            job_id=UUID(int=0),
            name="Invalid success",
            status=JobStatus.SUCCEEDED,
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            source_files=(FileHistoryMetadata(file_name="input.csv", sha256="e" * 64),),
            application_version="0.1.0",
        )
