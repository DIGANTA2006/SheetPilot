"""Aggregate row, sheet, validation, and total reconciliation."""

from __future__ import annotations

import math
from enum import StrEnum

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from sheetpilot.core.change_models import ChangeKind, ChangeRecord
from sheetpilot.core.plan_runner import ROW_ID_COLUMN, DatasetKey, strip_internal_columns
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


def _tracked_rows(
    tables: dict[DatasetKey, pl.DataFrame],
) -> dict[tuple[DatasetKey, int], dict[str, object]] | None:
    tracked: dict[tuple[DatasetKey, int], dict[str, object]] = {}
    for key, frame in tables.items():
        if ROW_ID_COLUMN not in frame.columns:
            return None
        for row in frame.iter_rows(named=True):
            row_id = row[ROW_ID_COLUMN]
            if isinstance(row_id, bool) or not isinstance(row_id, int):
                return None
            identity = (key, int(row_id))
            if identity in tracked:
                return None
            tracked[identity] = {
                column: value for column, value in row.items() if column != ROW_ID_COLUMN
            }
    return tracked


def _value_equal(left: object, right: object) -> bool:
    if left is None and right is None:
        return True
    if (
        isinstance(left, float)
        and isinstance(right, float)
        and math.isnan(left)
        and math.isnan(right)
    ):
        return True
    try:
        return bool(left == right)
    except (TypeError, ValueError):
        return repr(left) == repr(right)


def _row_equal(left: dict[str, object], right: dict[str, object]) -> bool:
    return left.keys() == right.keys() and all(
        _value_equal(left[column], right[column]) for column in left
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
    sampled_changed_rows = {
        (change.source_id, change.sheet, change.internal_row_id)
        for change in changes
        if change.kind == ChangeKind.CELL_CHANGED
    }
    original_tracked = _tracked_rows(original)
    final_tracked = _tracked_rows(final)
    if original_tracked is not None and final_tracked is not None:
        original_ids = set(original_tracked)
        final_ids = set(final_tracked)
        rows_removed = len(original_ids - final_ids)
        rows_added = len(final_ids - original_ids)
        changed_rows = sum(
            not _row_equal(original_tracked[identity], final_tracked[identity])
            for identity in original_ids & final_ids
        )
    else:
        rows_removed = max(0, original_rows - final_rows)
        rows_added = max(0, final_rows - original_rows)
        changed_rows = len(sampled_changed_rows)
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
        rows_changed=changed_rows,
        rows_removed=rows_removed,
        rows_added=rows_added,
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
