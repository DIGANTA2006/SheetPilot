"""Cohesive services for reusable workflows and privacy-safe job lifecycle history."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any
from uuid import UUID

from sheetpilot.app.version import __version__
from sheetpilot.core.executor import ExecutionResult
from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.core.plan_schema import OperationPlan, SourceReference
from sheetpilot.core.plan_validator import PlanValidator
from sheetpilot.operations.validation import (
    ValidationReport,
    ValidationSeverity,
)
from sheetpilot.storage.models import (
    AuditHistoryMetadata,
    FileHistoryMetadata,
    JobHistoryRecord,
    JobStatus,
    ValidationResult,
    ValidationSummary,
    WorkflowParameter,
    WorkflowTemplate,
    utc_now,
)
from sheetpilot.storage.repositories import (
    JobHistoryRepository,
    ValidationSummaryRepository,
    WorkflowTemplateRepository,
)


class WorkflowTemplateService:
    """Save and instantiate only plans accepted by the operation registry."""

    def __init__(
        self,
        registry: OperationRegistry,
        templates: WorkflowTemplateRepository,
        history: JobHistoryRepository,
    ) -> None:
        self.registry = registry
        self.templates = templates
        self.history = history

    def save_validated(
        self,
        plan: OperationPlan,
        *,
        name: str,
        description: str = "",
        parameters: tuple[WorkflowParameter, ...] | None = None,
    ) -> WorkflowTemplate:
        PlanValidator(self.registry).validate(plan)
        template = WorkflowTemplate.from_plan(
            plan,
            name=name,
            description=description,
            parameters=parameters,
        )
        return self.templates.save(template)

    def create_plan(
        self,
        workflow_id: UUID,
        sources: dict[str, SourceReference],
        parameter_values: dict[str, Any] | None = None,
        *,
        job_name: str | None = None,
    ) -> OperationPlan:
        template = self.templates.get_required(workflow_id)
        plan = template.instantiate(sources, parameter_values, job_name=job_name)
        PlanValidator(self.registry).validate(plan)
        return plan

    def repeat_job(
        self,
        job_id: UUID,
        sources: dict[str, SourceReference],
        parameter_values: dict[str, Any] | None = None,
        *,
        job_name: str | None = None,
    ) -> OperationPlan:
        previous = self.history.get_required(job_id)
        if previous.workflow_id is None:
            raise ValueError("This job was not created from a saved workflow.")
        return self.create_plan(
            previous.workflow_id,
            sources,
            parameter_values,
            job_name=job_name or f"{previous.name} repeat",
        )


class JobHistoryService:
    """Record aggregate execution evidence and validation history."""

    def __init__(
        self,
        history: JobHistoryRepository,
        validations: ValidationSummaryRepository,
    ) -> None:
        self.history = history
        self.validations = validations

    def record_started(
        self,
        plan: OperationPlan,
        *,
        workflow_id: UUID | None = None,
        status: JobStatus = JobStatus.RUNNING,
    ) -> JobHistoryRecord:
        if status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
            raise ValueError("record_started requires a non-terminal status")
        record = JobHistoryRecord(
            job_id=plan.job_id,
            name=plan.job_name,
            status=status,
            created_at=plan.created_at,
            source_files=tuple(
                FileHistoryMetadata(file_name=source.file_name, sha256=source.sha256)
                for source in plan.source_files
            ),
            workflow_id=workflow_id,
            step_count=sum(step.enabled for step in plan.steps),
            application_version=__version__,
        )
        return self.history.save(record)

    def record_success(self, result: ExecutionResult) -> JobHistoryRecord:
        previous = self.history.get_required(result.job_id)
        reconciliation = result.reconciliation
        if reconciliation.status.value == "passed":
            validation = ValidationResult.PASSED
        elif reconciliation.status.value == "passed_with_warnings":
            validation = ValidationResult.PASSED_WITH_WARNINGS
        else:
            validation = ValidationResult.FAILED
        report = result.audit_report
        updated = self._updated_record(
            previous,
            status=JobStatus.SUCCEEDED,
            completed_at=report.completed_at,
            output_file=FileHistoryMetadata(
                file_name=result.output.path.name,
                sha256=result.output.fingerprint.sha256,
            ),
            warning_count=reconciliation.warning_count,
            validation_result=validation,
            backup_path=str(result.backups[0].backup_path) if result.backups else None,
            audit=AuditHistoryMetadata(
                file_name=result.audit.path.name,
                sha256=result.audit.fingerprint.sha256,
            ),
            updated_at=utc_now(),
        )
        return self.history.save(updated)

    def record_failure(
        self,
        job_id: UUID,
        *,
        validation_result: ValidationResult = ValidationResult.FAILED,
        warning_count: int = 0,
        completed_at: datetime | None = None,
    ) -> JobHistoryRecord:
        previous = self.history.get_required(job_id)
        updated = self._updated_record(
            previous,
            status=JobStatus.FAILED,
            completed_at=completed_at or utc_now(),
            output_file=None,
            warning_count=warning_count,
            validation_result=validation_result,
            updated_at=utc_now(),
        )
        return self.history.save(updated)

    def record_cancelled(
        self, job_id: UUID, *, completed_at: datetime | None = None
    ) -> JobHistoryRecord:
        previous = self.history.get_required(job_id)
        updated = self._updated_record(
            previous,
            status=JobStatus.CANCELLED,
            completed_at=completed_at or utc_now(),
            output_file=None,
            updated_at=utc_now(),
        )
        return self.history.save(updated)

    def record_validation(self, job_id: UUID, report: ValidationReport) -> ValidationSummary:
        self.history.get_required(job_id)
        code_counts = Counter(issue.code for issue in report.issues)
        summary = ValidationSummary(
            job_id=job_id,
            passed=report.passed,
            checks_run=report.checks_run,
            error_count=report.error_count,
            warning_count=sum(
                issue.severity == ValidationSeverity.WARNING for issue in report.issues
            ),
            affected_row_count=sum(issue.affected_rows for issue in report.issues),
            issue_code_counts=dict(code_counts),
        )
        return self.validations.add(summary)

    @staticmethod
    def _updated_record(record: JobHistoryRecord, **updates: object) -> JobHistoryRecord:
        payload = record.model_dump(mode="python")
        payload.update(updates)
        return JobHistoryRecord.model_validate(payload)
