"""Aggregate-only JSON audit reports and atomic publication."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sheetpilot.app.version import __version__
from sheetpilot.core.atomic_output import AtomicOutputReceipt, AtomicOutputWriter
from sheetpilot.core.backup_service import BackupReceipt
from sheetpilot.core.plan_runner import StepRunSummary
from sheetpilot.core.plan_schema import OperationPlan
from sheetpilot.core.preview_engine import SourceBinding
from sheetpilot.core.reconciliation import ReconciliationResult


class AuditModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceAudit(AuditModel):
    source_id: str
    file_name: str
    sha256: str
    size_bytes: int = Field(ge=0)


class OperationAudit(AuditModel):
    step_id: str
    operation: str
    metrics: dict[str, int | float | str]
    warning_count: int = Field(ge=0)


class BackupAudit(AuditModel):
    source_name: str
    backup_path: Path
    sha256: str


class OutputAudit(AuditModel):
    output_path: Path
    sha256: str
    size_bytes: int = Field(ge=0)


class AuditReport(AuditModel):
    schema_version: str = "1.0"
    application_version: str
    job_id: str
    job_name: str
    created_at: datetime
    completed_at: datetime
    privacy_mode: str
    sources: tuple[SourceAudit, ...]
    operations: tuple[OperationAudit, ...]
    backups: tuple[BackupAudit, ...]
    output: OutputAudit
    reconciliation: ReconciliationResult


def build_audit_report(
    plan: OperationPlan,
    bindings: tuple[SourceBinding, ...],
    steps: tuple[StepRunSummary, ...],
    backups: tuple[BackupReceipt, ...],
    output: AtomicOutputReceipt,
    reconciliation: ReconciliationResult,
) -> AuditReport:
    names = {source.source_id: source.file_name for source in plan.source_files}
    return AuditReport(
        application_version=__version__,
        job_id=str(plan.job_id),
        job_name=plan.job_name,
        created_at=plan.created_at,
        completed_at=datetime.now(UTC),
        privacy_mode=plan.privacy.mode.value,
        sources=tuple(
            SourceAudit(
                source_id=str(binding.source_id),
                file_name=names[binding.source_id],
                sha256=binding.fingerprint.sha256,
                size_bytes=binding.fingerprint.size_bytes,
            )
            for binding in bindings
        ),
        operations=tuple(
            OperationAudit(
                step_id=step.step_id,
                operation=step.operation,
                metrics=step.metrics,
                warning_count=len(step.warnings),
            )
            for step in steps
        ),
        backups=tuple(
            BackupAudit(
                source_name=backup.source_name,
                backup_path=backup.backup_path,
                sha256=backup.source_fingerprint.sha256,
            )
            for backup in backups
        ),
        output=OutputAudit(
            output_path=output.path,
            sha256=output.fingerprint.sha256,
            size_bytes=output.byte_count,
        ),
        reconciliation=reconciliation,
    )


def write_audit_report(
    report: AuditReport,
    destination: Path,
    atomic_writer: AtomicOutputWriter,
    *,
    job_id: UUID,
) -> AtomicOutputReceipt:
    def writer(path: Path) -> None:
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    def validator(path: Path) -> None:
        AuditReport.model_validate_json(path.read_text(encoding="utf-8"))

    return atomic_writer.write(
        destination,
        job_id=job_id,
        writer=writer,
        validator=validator,
    )
