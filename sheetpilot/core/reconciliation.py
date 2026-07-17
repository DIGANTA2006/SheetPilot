"""Aggregate row, sheet, validation, and total reconciliation."""

from __future__ import annotations

from enum import StrEnum

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from sheetpilot.core.change_models import ChangeKind, ChangeRecord
from sheetpilot.core.plan_runner import DatasetKey, strip_internal_columns
from sheetpilot.operations.validation import ValidationReport


class ReconciliationStatus(StrEnum):
    PASSED = "passed"
    PASSED_WITH_WARNINGS = "passed_with_warnings"
    FAILED = "failed"


class TotalComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sheet: str
    column: str
    original_total: float
    final_total: float
    difference: float


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    original_row_count: int = Field(ge=0)
    final_row_count: int = Field(ge=0)
    rows_changed: int = Field(ge=0)
    rows_removed: int = Field(ge=0)
    rows_added: int = Field(ge=0)
    duplicates_removed: int = Field(ge=0)
    invalid_records_found: int = Field(ge=0)
    missing_required_values: int = Field(ge=0)
    sheets_created: tuple[str, ...]
    sheets_modified: tuple[str, ...]
    formulas_added: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    total_changes: int = Field(ge=0)
    total_comparisons: tuple[TotalComparison, ...]
    status: ReconciliationStatus


def _frame_equal(left: pl.DataFrame, right: pl.DataFrame) -> bool:
    left_clean = strip_internal_columns(left)
    right_clean = strip_internal_columns(right)
    return left_clean.columns == right_clean.columns and left_clean.equals(
        right_clean, null_equal=True
    )


def reconcile(
    original: dict[DatasetKey, pl.DataFrame],
    final: dict[DatasetKey, pl.DataFrame],
    *,
    changes: tuple[ChangeRecord, ...],
    total_change_count: int,
    validation: ValidationReport,
    operation_metrics: tuple[dict[str, int | float | str], ...] = (),
    warnings: tuple[str, ...] = (),
    formula_count: int = 0,
    total_columns: tuple[str, ...] = (),
) -> ReconciliationResult:
    original_rows = sum(frame.height for frame in original.values())
    final_rows = sum(frame.height for frame in final.values())
    changed_rows = {
        (change.source_id, change.sheet, change.internal_row_id)
        for change in changes
        if change.kind == ChangeKind.CELL_CHANGED
    }
    duplicate_count = sum(
        int(metrics.get(key, 0))
        for metrics in operation_metrics
        for key in ("removed_or_moved_rows", "merged_rows")
    )
    invalid_records = sum(issue.affected_rows for issue in validation.issues)
    missing_required = sum(
        issue.affected_rows for issue in validation.issues if issue.code == "missing_required"
    )
    created_keys = sorted(set(final) - set(original))
    modified_keys = sorted(
        key for key in set(original) & set(final) if not _frame_equal(original[key], final[key])
    )
    totals: list[TotalComparison] = []
    for key in sorted(set(original) & set(final)):
        before = strip_internal_columns(original[key])
        after = strip_internal_columns(final[key])
        for column in total_columns:
            if column not in before.columns or column not in after.columns:
                continue
            before_total = before.get_column(column).cast(pl.Float64, strict=False).sum() or 0
            after_total = after.get_column(column).cast(pl.Float64, strict=False).sum() or 0
            totals.append(
                TotalComparison(
                    sheet=key.sheet,
                    column=column,
                    original_total=float(before_total),
                    final_total=float(after_total),
                    difference=float(after_total) - float(before_total),
                )
            )
    warning_count = len(warnings) + sum(
        issue.severity.value == "warning" for issue in validation.issues
    )
    if not validation.passed:
        status = ReconciliationStatus.FAILED
    elif warning_count:
        status = ReconciliationStatus.PASSED_WITH_WARNINGS
    else:
        status = ReconciliationStatus.PASSED
    return ReconciliationResult(
        original_row_count=original_rows,
        final_row_count=final_rows,
        rows_changed=len(changed_rows),
        rows_removed=max(0, original_rows - final_rows),
        rows_added=max(0, final_rows - original_rows),
        duplicates_removed=duplicate_count,
        invalid_records_found=invalid_records,
        missing_required_values=missing_required,
        sheets_created=tuple(key.sheet for key in created_keys),
        sheets_modified=tuple(key.sheet for key in modified_keys),
        formulas_added=formula_count,
        warning_count=warning_count,
        total_changes=total_change_count,
        total_comparisons=tuple(totals),
        status=status,
    )
