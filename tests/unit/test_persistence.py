from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from sheetpilot.app.persistence import PersistenceServices
from sheetpilot.core.exceptions import StorageError
from sheetpilot.core.plan_schema import (
    OperationPlan,
    OutputSettings,
    PlanStep,
    PrivacyMetadata,
    PrivacyMode,
    SourceReference,
    StepTarget,
    ValidationRule,
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
    TemplateBindingLocation,
    TemplateParameterBinding,
    TemplateParameterKind,
    ValidationResult,
    ValidationSummary,
)
from sheetpilot.storage.repositories import (
    JobHistoryRepository,
    SettingsRepository,
    ValidationSummaryRepository,
    WorkflowTemplateRepository,
)
from sheetpilot.storage.services import JobHistoryService, WorkflowTemplateService


def _plan(
    *,
    source_name: str = "client.csv",
    digest: str = "a" * 64,
    step_id: str = "clean-names",
    actions: list[dict[str, object]] | None = None,
    validations: list[ValidationRule] | None = None,
    privacy: PrivacyMetadata | None = None,
) -> OperationPlan:
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
                step_id=step_id,
                operation="text.clean",
                parameters={
                    "columns": ["Name"],
                    "actions": actions or [{"kind": "trim"}],
                },
                target=StepTarget(source_id=source_id, sheet="CSV", columns=["Name"]),
                explanation="Trim selected name values",
            )
        ],
        validations=validations or [],
        output=OutputSettings(
            output_name="cleaned",
            format="csv",
            preserve_formatting=False,
        ),
        privacy=privacy or PrivacyMetadata(),
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


def test_template_resets_one_job_ai_consent(database: Database) -> None:
    templates = WorkflowTemplateRepository(database)
    service = WorkflowTemplateService(
        build_default_registry(), templates, JobHistoryRepository(database)
    )
    plan = _plan(
        privacy=PrivacyMetadata(
            mode=PrivacyMode.AI_ASSISTED,
            metadata_upload_consent=True,
            raw_data_upload_consent=True,
        )
    )

    template = service.save_validated(plan, name="Privacy-safe cleanup")

    assert template.privacy == PrivacyMetadata()
    rebound = service.create_plan(
        template.workflow_id,
        {
            "source_1": SourceReference(
                file_name="new.csv",
                sha256="b" * 64,
                sheet_names=["CSV"],
            )
        },
    )
    assert rebound.privacy == PrivacyMetadata()
    with database.connect() as connection:
        stored = json.loads(
            str(
                connection.execute(
                    "SELECT plan_json FROM workflow_templates WHERE workflow_id = ?",
                    (str(template.workflow_id),),
                ).fetchone()[0]
            )
        )
    assert stored["privacy"] == {
        "mode": "local",
        "metadata_upload_consent": False,
        "raw_data_upload_consent": False,
    }


def test_generated_template_parameters_are_safe_typed_and_rebind_nested_values(
    database: Database,
) -> None:
    plan = _plan(
        step_id="123-Clean",
        actions=[
            {
                "kind": "mapping_replace",
                "mapping": {"Old": "Original"},
            }
        ],
        validations=[
            ValidationRule(
                name="allowed_values",
                parameters={"column": "Status", "values": ["Open", "Closed"]},
            )
        ],
    )
    source_id = plan.source_files[0].source_id
    plan.steps.append(
        PlanStep(
            step_id="123_Clean",
            operation="table.split_by_category",
            parameters={
                "category_column": "Category",
                "table_prefix": "",
                "include_blank": True,
                "drop_category_column": False,
                "max_tables": 20,
            },
            target=StepTarget(source_id=source_id, sheet="CSV", columns=["Category"]),
            explanation="Split the table by an approved grouping column",
        )
    )
    service = WorkflowTemplateService(
        build_default_registry(),
        WorkflowTemplateRepository(database),
        JobHistoryRepository(database),
    )

    template = service.save_validated(plan, name="Parameterized cleanup")

    keys = [parameter.key for parameter in template.parameters]
    assert len(keys) == len(set(keys))
    assert all(re.fullmatch(r"[a-z][a-z0-9_]{0,79}", key) for key in keys)
    output_parameter = next(
        parameter
        for parameter in template.parameters
        if parameter.kind == TemplateParameterKind.OUTPUT_NAME
    )
    mapping_parameter = next(
        parameter
        for parameter in template.parameters
        if parameter.kind == TemplateParameterKind.MAPPING
    )
    grouping_parameter = next(
        parameter
        for parameter in template.parameters
        if parameter.binding.location == TemplateBindingLocation.STEP_PARAMETER
        and parameter.binding.step_id == "123_Clean"
    )
    validation_column = next(
        parameter
        for parameter in template.parameters
        if parameter.binding.location == TemplateBindingLocation.VALIDATION_PARAMETER
        and parameter.kind == TemplateParameterKind.COLUMN
    )
    validation_values = next(
        parameter
        for parameter in template.parameters
        if parameter.binding.location == TemplateBindingLocation.VALIDATION_PARAMETER
        and parameter.kind == TemplateParameterKind.JSON
    )
    assert output_parameter.default_value == "cleaned"
    sheet_defaults = {
        parameter.default_value
        for parameter in template.parameters
        if parameter.binding.location == TemplateBindingLocation.STEP_SHEET
    }
    target_defaults = [
        parameter.default_value
        for parameter in template.parameters
        if parameter.binding.location == TemplateBindingLocation.STEP_COLUMNS
    ]
    assert sheet_defaults == {"CSV"}
    assert ["Name"] in target_defaults
    assert ["Category"] in target_defaults
    assert mapping_parameter.default_value == {"Old": "Original"}
    assert mapping_parameter.binding.parameter_path == ("actions", 0, "mapping")
    assert grouping_parameter.default_value == "Category"
    assert validation_column.default_value == "Status"
    assert validation_values.default_value == ["Open", "Closed"]

    repeated = service.create_plan(
        template.workflow_id,
        {
            "source_1": SourceReference(
                file_name="replacement.csv",
                sha256="c" * 64,
                sheet_names=["CSV"],
            )
        },
        {
            output_parameter.key: "repeated-output",
            mapping_parameter.key: {"New": "Replacement"},
            grouping_parameter.key: "District",
            validation_column.key: "State",
            validation_values.key: ["Active", "Closed"],
        },
    )
    assert repeated.output.output_name == "repeated-output"
    assert repeated.steps[0].parameters["actions"][0]["mapping"] == {"New": "Replacement"}
    assert repeated.steps[1].parameters["category_column"] == "District"
    assert repeated.validations[0].parameters == {
        "column": "State",
        "values": ["Active", "Closed"],
    }


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

    with pytest.raises(ValidationError, match="between 0 and 999"):
        TemplateParameterBinding(
            location=TemplateBindingLocation.STEP_PARAMETER,
            step_id="clean-names",
            parameter_path=("actions", 1000, "mapping"),
        )


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


def test_configured_history_retention_is_applied_by_maintenance(database: Database) -> None:
    services = PersistenceServices.build(database, build_default_registry())
    services.settings.set(SettingKey.HISTORY_RETENTION_DAYS, 5)
    now = datetime.now(UTC)
    old = now - timedelta(days=10)
    old_id = uuid4()
    recent_id = uuid4()
    for job_id, completed_at in ((old_id, old), (recent_id, now - timedelta(days=1))):
        services.history.save(
            JobHistoryRecord(
                job_id=job_id,
                name="Retention test",
                status=JobStatus.CANCELLED,
                created_at=completed_at,
                completed_at=completed_at,
                source_files=(FileHistoryMetadata(file_name="input.csv", sha256="d" * 64),),
                application_version="0.1.0",
            )
        )

    assert services.prune_expired_history(now=now) == 1
    assert services.history.get(old_id) is None
    assert services.history.get(recent_id) is not None


def test_job_history_rejects_regressions_and_terminal_overwrites(database: Database) -> None:
    repository = JobHistoryRepository(database)
    created_at = datetime.now(UTC) - timedelta(minutes=2)
    running = JobHistoryRecord(
        job_id=uuid4(),
        name="Guarded history",
        status=JobStatus.RUNNING,
        created_at=created_at,
        source_files=(FileHistoryMetadata(file_name="input.csv", sha256="d" * 64),),
        application_version="0.1.0",
        updated_at=created_at + timedelta(seconds=1),
    )
    repository.save(running)
    regressed_payload = running.model_dump(mode="python")
    regressed_payload.update(
        status=JobStatus.AWAITING_APPROVAL,
        updated_at=created_at + timedelta(seconds=2),
    )
    with pytest.raises(StorageError, match="running -> awaiting_approval"):
        repository.save(JobHistoryRecord.model_validate(regressed_payload))

    succeeded_payload = running.model_dump(mode="python")
    succeeded_payload.update(
        status=JobStatus.SUCCEEDED,
        completed_at=created_at + timedelta(seconds=3),
        output_file=FileHistoryMetadata(file_name="output.csv", sha256="e" * 64),
        updated_at=created_at + timedelta(seconds=3),
    )
    succeeded = repository.save(JobHistoryRecord.model_validate(succeeded_payload))
    assert repository.save(succeeded) == succeeded

    overwritten_payload = succeeded.model_dump(mode="python")
    overwritten_payload.update(
        warning_count=99,
        updated_at=created_at + timedelta(seconds=4),
    )
    with pytest.raises(StorageError, match="Terminal job history"):
        repository.save(JobHistoryRecord.model_validate(overwritten_payload))
    assert repository.get(succeeded.job_id) == succeeded


def test_successful_job_can_be_linked_once_to_saved_workflow(database: Database) -> None:
    history = JobHistoryRepository(database)
    validations = ValidationSummaryRepository(database)
    templates = WorkflowTemplateRepository(database)
    template_service = WorkflowTemplateService(build_default_registry(), templates, history)
    template = template_service.save_validated(_plan(), name="Linked cleanup")
    completed_at = datetime.now(UTC)
    succeeded = history.save(
        JobHistoryRecord(
            job_id=uuid4(),
            name="Completed cleanup",
            status=JobStatus.SUCCEEDED,
            created_at=completed_at - timedelta(minutes=1),
            completed_at=completed_at,
            source_files=(FileHistoryMetadata(file_name="input.csv", sha256="f" * 64),),
            output_file=FileHistoryMetadata(file_name="output.csv", sha256="a" * 64),
            application_version="0.1.0",
        )
    )
    service = JobHistoryService(history, validations)

    linked = service.link_workflow(succeeded.job_id, template.workflow_id)

    assert linked.workflow_id == template.workflow_id
    assert service.link_workflow(succeeded.job_id, template.workflow_id) == linked
    with pytest.raises(StorageError, match="different saved workflow"):
        service.link_workflow(succeeded.job_id, uuid4())
    with pytest.raises(StorageError, match="linked to job history"):
        templates.delete(template.workflow_id)
    assert templates.get(template.workflow_id) == template


def test_global_validation_history_is_newest_first_and_paginated(database: Database) -> None:
    history = JobHistoryRepository(database)
    validations = ValidationSummaryRepository(database)
    service = JobHistoryService(history, validations)
    now = datetime.now(UTC)
    job_ids = (uuid4(), uuid4())
    for index, job_id in enumerate(job_ids):
        history.save(
            JobHistoryRecord(
                job_id=job_id,
                name=f"Validation job {index}",
                status=JobStatus.RUNNING,
                created_at=now - timedelta(minutes=index + 1),
                source_files=(
                    FileHistoryMetadata(file_name=f"input-{index}.csv", sha256=f"{index + 1}" * 64),
                ),
                application_version="0.1.0",
            )
        )
    older = validations.add(
        ValidationSummary(
            job_id=job_ids[0],
            passed=True,
            checks_run=1,
            error_count=0,
            warning_count=0,
            affected_row_count=0,
            created_at=now - timedelta(seconds=2),
        )
    )
    newer = validations.add(
        ValidationSummary(
            job_id=job_ids[1],
            passed=False,
            checks_run=2,
            error_count=1,
            warning_count=0,
            affected_row_count=3,
            created_at=now - timedelta(seconds=1),
        )
    )

    assert validations.list_recent() == (newer, older)
    assert service.recent_validations(limit=1) == (newer,)
    assert validations.list_recent(limit=1, offset=1) == (older,)
    with pytest.raises(ValueError, match="pagination"):
        validations.list_recent(limit=0)


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


def test_second_migration_resumes_a_partially_upgraded_version_one_database(
    tmp_path: Path,
) -> None:
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
        ALTER TABLE jobs ADD COLUMN audit_metadata_json TEXT;
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
    assert versions == {1, 2, 3}
    assert {"audit_metadata_json", "updated_at"} <= job_columns


def test_privacy_migration_scrubs_legacy_template_consent(database: Database) -> None:
    templates = WorkflowTemplateRepository(database)
    service = WorkflowTemplateService(
        build_default_registry(), templates, JobHistoryRepository(database)
    )
    template = service.save_validated(_plan(), name="Legacy privacy")
    with database.connect() as connection:
        serialized = str(
            connection.execute(
                "SELECT plan_json FROM workflow_templates WHERE workflow_id = ?",
                (str(template.workflow_id),),
            ).fetchone()[0]
        )
        payload = json.loads(serialized)
        payload["privacy"] = {
            "mode": "ai_assisted",
            "metadata_upload_consent": True,
            "raw_data_upload_consent": True,
        }
        connection.execute(
            "UPDATE workflow_templates SET plan_json = ? WHERE workflow_id = ?",
            (json.dumps(payload), str(template.workflow_id)),
        )
        connection.execute("DELETE FROM schema_migrations WHERE version = 3")

    database.initialize()

    migrated = templates.get_required(template.workflow_id)
    assert migrated.privacy == PrivacyMetadata()
    with database.connect() as connection:
        stored = str(
            connection.execute(
                "SELECT plan_json FROM workflow_templates WHERE workflow_id = ?",
                (str(template.workflow_id),),
            ).fetchone()[0]
        )
    assert '"raw_data_upload_consent":true' not in stored.replace(" ", "").casefold()


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
