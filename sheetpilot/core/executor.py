"""Fail-closed end-to-end job execution transaction."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

import polars as pl
from pydantic import BaseModel, ConfigDict

from sheetpilot.app.config import AppConfig
from sheetpilot.core.atomic_output import AtomicOutputReceipt, AtomicOutputWriter
from sheetpilot.core.audit_report import AuditReport, build_audit_report, write_audit_report
from sheetpilot.core.backup_service import BackupReceipt, BackupService
from sheetpilot.core.exceptions import InvalidPlanError, OutputFailureError
from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.core.plan_runner import DatasetKey, PlanRunner, strip_internal_columns
from sheetpilot.core.plan_schema import OperationPlan, OutputFormat
from sheetpilot.core.plan_validator import PlanValidator
from sheetpilot.core.preview_engine import (
    ExecutionApproval,
    PreviewResult,
    SourceBinding,
    apply_cell_rejections,
    digest_sources,
    digest_tables,
    load_plan_tables,
    validate_approval,
)
from sheetpilot.core.reconciliation import ReconciliationResult, reconcile
from sheetpilot.core.workspace import IsolatedWorkspace
from sheetpilot.engines.csv_engine import write_safe_csv
from sheetpilot.engines.formulas import FormulaKind, GeneratedFormulaSpec
from sheetpilot.engines.openpyxl_export import modify_workbook_copy
from sheetpilot.engines.tabular_io import validate_output_file
from sheetpilot.engines.xlsxwriter_engine import write_new_workbook
from sheetpilot.operations.calculations import (
    AgeAction,
    ArithmeticAction,
    CalculateColumnParameters,
    DateDifferenceAction,
    DateDifferenceUnit,
    GstAction,
    PercentageAction,
    QuantityPriceAction,
)
from sheetpilot.operations.validation import (
    TotalReconciliationRule,
    ValidationContext,
    ValidationReport,
    parse_quality_rules,
    validate_table,
)
from sheetpilot.security.hashing import verify_fingerprint
from sheetpilot.security.path_guard import safe_output_path


class ExecutionResult(BaseModel):
    """Durable completion evidence; partial results never instantiate this model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    output: AtomicOutputReceipt
    audit: AtomicOutputReceipt
    backups: tuple[BackupReceipt, ...]
    validation: ValidationReport
    reconciliation: ReconciliationResult
    audit_report: AuditReport


def _combine_validation_reports(*reports: ValidationReport) -> ValidationReport:
    """Combine in-plan and final validations into one fail-closed execution gate."""
    return ValidationReport(
        passed=all(report.passed and report.error_count == 0 for report in reports),
        checks_run=sum(report.checks_run for report in reports),
        issues=tuple(issue for report in reports for issue in report.issues),
    )


def _eligible_formula(action: object) -> GeneratedFormulaSpec | None:
    if isinstance(action, ArithmeticAction):
        columns = [operand.column for operand in action.operands]
        if any(column is None for column in columns):
            return None
        return GeneratedFormulaSpec(
            output_column=action.output,
            kind=FormulaKind(action.kind),
            operand_columns=[str(column) for column in columns],
        )
    if isinstance(action, PercentageAction):
        if action.numerator.column is None or action.denominator.column is None:
            return None
        return GeneratedFormulaSpec(
            output_column=action.output,
            kind=FormulaKind.PERCENTAGE,
            operand_columns=[action.numerator.column, action.denominator.column],
            number_format="0.00",
        )
    if isinstance(action, QuantityPriceAction):
        return GeneratedFormulaSpec(
            output_column=action.output,
            kind=FormulaKind.QUANTITY_PRICE,
            operand_columns=[action.quantity_column, action.price_column],
        )
    if isinstance(action, GstAction):
        return GeneratedFormulaSpec(
            output_column=action.output,
            kind=FormulaKind.GST,
            operand_columns=[action.base_column],
            rate_percent=action.rate_percent,
            include_base=action.include_base,
        )
    if isinstance(action, DateDifferenceAction):
        if action.unit != DateDifferenceUnit.DAYS:
            return None
        return GeneratedFormulaSpec(
            output_column=action.output,
            kind=FormulaKind.DATE_DIFFERENCE,
            operand_columns=[action.start_column, action.end_column],
        )
    if isinstance(action, AgeAction):
        return GeneratedFormulaSpec(
            output_column=action.output,
            kind=FormulaKind.AGE,
            operand_columns=[action.birth_date_column],
            as_of=action.as_of,
        )
    return None


def _formula_specs(
    plan: OperationPlan,
    registry: OperationRegistry,
    tables: dict[DatasetKey, pl.DataFrame],
) -> dict[DatasetKey, list[GeneratedFormulaSpec]]:
    if not plan.output.retain_calculation_formulas:
        return {}
    result: dict[DatasetKey, list[GeneratedFormulaSpec]] = {}
    for step in plan.steps:
        if not step.enabled or step.operation != "calculate.column":
            continue
        typed = registry.validate_parameters(step.operation, step.parameters)
        if not isinstance(typed, CalculateColumnParameters):
            continue
        spec = _eligible_formula(typed.action)
        if spec is None:
            continue
        candidates = [key for key in tables if key.source_id == step.target.source_id]
        key = (
            DatasetKey(step.target.source_id, step.target.sheet)
            if step.target.sheet is not None
            else candidates[0]
        )
        result.setdefault(key, []).append(spec)
    return result


def _export_tables(
    plan: OperationPlan,
    tables: dict[DatasetKey, pl.DataFrame],
    formulas: dict[DatasetKey, list[GeneratedFormulaSpec]],
) -> tuple[dict[str, pl.DataFrame], dict[str, list[GeneratedFormulaSpec]]]:
    source_names = {source.source_id: Path(source.file_name).stem for source in plan.source_files}
    multiple_sources = len(source_names) > 1
    output: dict[str, pl.DataFrame] = {}
    formula_output: dict[str, list[GeneratedFormulaSpec]] = {}
    claimed_names: set[str] = set()
    for key in sorted(tables):
        base_name = (
            f"{source_names[key.source_id]} - {key.sheet}" if multiple_sources else key.sheet
        )
        name = base_name
        sequence = 2
        while name.casefold() in claimed_names:
            name = f"{base_name} ({sequence})"
            sequence += 1
        claimed_names.add(name.casefold())
        frame = strip_internal_columns(tables[key])
        eligible: list[GeneratedFormulaSpec] = []
        formula_outputs = {spec.output_column for spec in formulas.get(key, [])}
        for spec in formulas.get(key, []):
            if all(column not in formula_outputs for column in spec.operand_columns):
                eligible.append(spec)
        if eligible:
            frame = frame.drop([spec.output_column for spec in eligible])
            formula_output[name] = eligible
        output[name] = frame
    return (output, formula_output)


def _primary_key(plan: OperationPlan, tables: dict[DatasetKey, pl.DataFrame]) -> DatasetKey:
    enabled = [step for step in plan.steps if step.enabled]
    if enabled:
        step = enabled[-1]
        candidates = [key for key in tables if key.source_id == step.target.source_id]
        if step.target.sheet is not None:
            requested = DatasetKey(step.target.source_id, step.target.sheet)
            if requested in tables:
                return requested
        if len(candidates) == 1:
            return candidates[0]
    return sorted(tables)[0]


class JobExecutor:
    """Execute an approved preview through backup, workspace, validation, and commit."""

    def __init__(self, config: AppConfig, registry: OperationRegistry) -> None:
        self.config = config
        self.registry = registry

    def execute(
        self,
        plan: OperationPlan,
        bindings: tuple[SourceBinding, ...],
        preview: PreviewResult,
        approval: ExecutionApproval,
        output_directory: Path,
    ) -> ExecutionResult:
        PlanValidator(self.registry).validate(plan)
        validate_approval(plan, preview, approval)
        current_plan_digest = hashlib.sha256(plan.model_dump_json().encode("utf-8")).hexdigest()
        if (
            current_plan_digest != preview.plan_digest
            or digest_sources(bindings) != preview.source_digest
        ):
            raise InvalidPlanError("The plan or source bindings changed after preview.")
        for binding in bindings:
            verify_fingerprint(binding.path, binding.fingerprint)

        backup_service = BackupService(self.config.backup_dir)
        backups = tuple(
            backup_service.create_backup(
                binding.path,
                job_id=plan.job_id,
                expected=binding.fingerprint,
            )
            for binding in bindings
        )
        approved_root = output_directory.resolve()
        failed_root = approved_root / "SheetPilot Failed"
        atomic_writer = AtomicOutputWriter(approved_root, failed_root)
        output_path = safe_output_path(
            approved_root,
            plan.output.output_name,
            extension=plan.output.format.value,
            protected_paths=tuple(binding.path for binding in bindings),
        )
        audit_path = safe_output_path(
            approved_root,
            f"{output_path.stem}.audit",
            extension="json",
            protected_paths=tuple(binding.path for binding in bindings),
        )

        with IsolatedWorkspace(self.config.temp_dir, plan.job_id) as workspace:
            working_paths: dict[UUID, Path] = {}
            for binding in bindings:
                working_paths[binding.source_id] = workspace.copy_source(
                    binding.path,
                    binding.fingerprint,
                    identity=str(binding.source_id),
                )
            original_tables = load_plan_tables(plan, bindings, path_overrides=working_paths)
            run = PlanRunner(self.registry).run(plan, original_tables)
            if digest_tables(run.tables) != preview.result_digest:
                raise InvalidPlanError(
                    "Deterministic re-execution did not match the approved preview."
                )
            final_tables = apply_cell_rejections(run, preview, approval)

            rules = parse_quality_rules(
                [{"kind": rule.name, **rule.parameters} for rule in plan.validations]
            )
            primary = _primary_key(plan, final_tables)
            top_level_validation = (
                validate_table(
                    strip_internal_columns(final_tables[primary]),
                    rules,
                    ValidationContext(
                        original_headers=tuple(
                            strip_internal_columns(original_tables[primary]).columns
                        )
                        if primary in original_tables
                        else None
                    ),
                )
                if rules
                else ValidationReport(passed=True, checks_run=0, issues=())
            )
            validation = _combine_validation_reports(
                *run.validation_reports,
                top_level_validation,
            )
            accepted_changes = tuple(
                change
                for change in run.changes
                if change.change_id not in approval.rejected_change_ids
            )
            warnings = tuple(warning for step in run.steps for warning in step.warnings)
            formulas = _formula_specs(plan, self.registry, final_tables)
            total_columns = tuple(
                rule.column for rule in rules if isinstance(rule, TotalReconciliationRule)
            )
            reconciliation = reconcile(
                run.original_tables,
                final_tables,
                changes=accepted_changes,
                total_change_count=run.total_change_count - len(approval.rejected_change_ids),
                validation=validation,
                operation_metrics=tuple(step.metrics for step in run.steps),
                warnings=warnings,
                formula_count=sum(len(specs) for specs in formulas.values()),
                total_columns=total_columns,
            )
            output_tables, output_formulas = _export_tables(plan, final_tables, formulas)

            def verify_sources() -> None:
                for source_binding in bindings:
                    verify_fingerprint(source_binding.path, source_binding.fingerprint)

            def output_writer(stage: Path) -> None:
                if plan.output.format == OutputFormat.CSV:
                    if len(output_tables) != 1:
                        raise InvalidPlanError("CSV output supports exactly one result table.")
                    write_safe_csv(next(iter(output_tables.values())), stage)
                elif (
                    plan.output.preserve_formatting
                    and len(bindings) == 1
                    and working_paths[bindings[0].source_id].suffix.casefold() == ".xlsx"
                ):
                    source_id = bindings[0].source_id
                    replacements = {
                        key.sheet: strip_internal_columns(frame)
                        for key, frame in final_tables.items()
                        if key.source_id == source_id
                    }
                    formula_by_sheet = {
                        key.sheet: specs
                        for key, specs in formulas.items()
                        if key.source_id == source_id
                    }
                    for key, specs in formulas.items():
                        if key.source_id == source_id:
                            replacements[key.sheet] = replacements[key.sheet].drop(
                                [spec.output_column for spec in specs]
                            )
                    modify_workbook_copy(
                        working_paths[source_id],
                        stage,
                        replacements=replacements,
                        formula_specs=formula_by_sheet,
                    )
                else:
                    write_new_workbook(
                        output_tables,
                        stage,
                        formula_specs=output_formulas,
                    )

            if not validation.passed:
                failed_job_root = failed_root / str(plan.job_id)
                failed_writer = AtomicOutputWriter(
                    failed_job_root,
                    failed_job_root / "Rejected",
                )
                failed_path = safe_output_path(
                    failed_job_root,
                    f"{output_path.stem}.validation_failed",
                    extension=plan.output.format.value,
                )
                failed_writer.write(
                    failed_path,
                    job_id=plan.job_id,
                    writer=output_writer,
                    validator=lambda path: validate_output_file(path, self.config.limits),
                    pre_commit=verify_sources,
                )
                raise OutputFailureError(
                    "Validation failed; the output was retained only as a failed artifact."
                )

            output_receipt = atomic_writer.write(
                output_path,
                job_id=plan.job_id,
                writer=output_writer,
                validator=lambda path: validate_output_file(path, self.config.limits),
                pre_commit=verify_sources,
            )

        audit_report = build_audit_report(
            plan,
            bindings,
            run.steps,
            backups,
            output_receipt,
            reconciliation,
        )
        try:
            audit_receipt = write_audit_report(
                audit_report,
                audit_path,
                atomic_writer,
                job_id=plan.job_id,
            )
        except BaseException as error:
            atomic_writer.quarantine_committed(output_receipt.path, plan.job_id)
            raise OutputFailureError(
                "The audit report failed; the output was retained as a failed artifact."
            ) from error
        return ExecutionResult(
            job_id=plan.job_id,
            output=output_receipt,
            audit=audit_receipt,
            backups=backups,
            validation=validation,
            reconciliation=reconciliation,
            audit_report=audit_report,
        )
