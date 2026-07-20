"""Deterministic in-memory plan application and change capture."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import polars as pl

from sheetpilot.core.change_models import ChangeKind, ChangeRecord
from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.core.plan_schema import OperationPlan, PlanStep
from sheetpilot.core.plan_validator import PlanValidator
from sheetpilot.operations.merging import MergeTablesOperation, MergeTablesParameters
from sheetpilot.operations.tabular import TableOperationResult, validate_table_columns
from sheetpilot.operations.validation import ValidationReport

ROW_ID_COLUMN = "_sheetpilot_preview_row_id"
_INTERNAL_COLUMN_PREFIX = "_sheetpilot_"


@dataclass(frozen=True, order=True)
class DatasetKey:
    source_id: UUID
    sheet: str


@dataclass(frozen=True)
class StepRunSummary:
    step_id: str
    operation: str
    metrics: dict[str, int | float | str]
    warnings: tuple[str, ...]
    change_count: int


@dataclass(frozen=True)
class PlanRunResult:
    original_tables: dict[DatasetKey, pl.DataFrame]
    tables: dict[DatasetKey, pl.DataFrame]
    changes: tuple[ChangeRecord, ...]
    total_change_count: int
    steps: tuple[StepRunSummary, ...]
    validation_reports: tuple[ValidationReport, ...]


def _with_row_ids(frame: pl.DataFrame, *, offset: int = 1) -> pl.DataFrame:
    reserved = [
        column for column in frame.columns if column.casefold().startswith(_INTERNAL_COLUMN_PREFIX)
    ]
    if reserved:
        raise InvalidPlanError(f"Input contains a reserved column: {reserved[0]}")
    return frame.with_row_index(ROW_ID_COLUMN, offset=offset).with_columns(
        pl.col(ROW_ID_COLUMN).cast(pl.UInt64)
    )


def strip_internal_columns(frame: pl.DataFrame) -> pl.DataFrame:
    internal = [
        column for column in frame.columns if column.casefold().startswith(_INTERNAL_COLUMN_PREFIX)
    ]
    return frame.drop(internal) if internal else frame


def _validate_plan_table(frame: pl.DataFrame) -> None:
    validate_table_columns(frame, excluded_prefixes=(_INTERNAL_COLUMN_PREFIX,))


def _same(left: Any, right: Any) -> bool:
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


def _change_id(
    step: PlanStep,
    key: DatasetKey,
    kind: ChangeKind,
    row_id: int | None,
    column: str | None,
    original: Any,
    proposed: Any,
) -> str:
    payload = "|".join(
        (
            step.step_id,
            str(key.source_id),
            key.sheet,
            kind.value,
            str(row_id),
            str(column),
            repr(original),
            repr(proposed),
        )
    )
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


class _ChangeCollector:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.total = 0
        self.records: list[ChangeRecord] = []

    def add(
        self,
        *,
        kind: ChangeKind,
        step: PlanStep,
        key: DatasetKey,
        file_name: str,
        row_id: int | None,
        row_number: int | None,
        column: str | None,
        original: Any,
        proposed: Any,
        reason: str,
    ) -> None:
        self.total += 1
        if len(self.records) >= self.limit:
            return
        self.records.append(
            ChangeRecord(
                change_id=_change_id(step, key, kind, row_id, column, original, proposed),
                kind=kind,
                source_id=key.source_id,
                file_name=file_name,
                sheet=key.sheet,
                row=row_number,
                internal_row_id=row_id,
                column=column,
                original_value=original,
                proposed_value=proposed,
                reason=reason,
                risk_level=step.risk_level,
                step_id=step.step_id,
            )
        )


def _rows_by_id(frame: pl.DataFrame) -> tuple[dict[int, dict[str, Any]], list[int]]:
    if ROW_ID_COLUMN not in frame.columns:
        frame = _with_row_ids(frame)
    rows: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for row in frame.iter_rows(named=True):
        row_id = int(row[ROW_ID_COLUMN])
        if row_id in rows:
            raise InvalidPlanError("Stable row metadata contains duplicate identifiers.")
        rows[row_id] = row
        order.append(row_id)
    return (rows, order)


def _contains_reserved_name(value: Any) -> bool:
    if isinstance(value, str):
        return value == ROW_ID_COLUMN
    if isinstance(value, dict):
        return any(
            _contains_reserved_name(key) or _contains_reserved_name(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_reserved_name(item) for item in value)
    return False


def _validated_row_ids(frame: pl.DataFrame) -> list[int]:
    values = frame.get_column(ROW_ID_COLUMN).to_list()
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in values):
        raise InvalidPlanError("Stable row metadata is invalid.")
    return [int(value) for value in values]


def _repair_duplicate_row_ids(frame: pl.DataFrame, *, next_row_id: int) -> tuple[pl.DataFrame, int]:
    """Keep the first identity and allocate fresh IDs for copied row instances."""
    values = _validated_row_ids(frame)
    if values:
        next_row_id = max(next_row_id, max(values) + 1)
    seen: set[int] = set()
    repaired: list[int] = []
    changed = False
    for value in values:
        if value in seen:
            value = next_row_id
            next_row_id += 1
            changed = True
        seen.add(value)
        repaired.append(value)
    if changed:
        frame = frame.with_columns(pl.Series(ROW_ID_COLUMN, repaired, dtype=pl.UInt64))
    return (frame, next_row_id)


def _capture_diff(
    before: pl.DataFrame,
    after: pl.DataFrame,
    *,
    step: PlanStep,
    key: DatasetKey,
    file_name: str,
    reason: str,
    collector: _ChangeCollector,
) -> None:
    before_rows, before_order = _rows_by_id(before)
    after_rows, after_order = _rows_by_id(after)
    before_positions = {row_id: index + 2 for index, row_id in enumerate(before_order)}
    after_positions = {row_id: index + 2 for index, row_id in enumerate(after_order)}
    before_ids, after_ids = set(before_rows), set(after_rows)
    for row_id in sorted(before_ids - after_ids):
        original = {
            column: value
            for column, value in before_rows[row_id].items()
            if column != ROW_ID_COLUMN
        }
        collector.add(
            kind=ChangeKind.ROW_DELETED,
            step=step,
            key=key,
            file_name=file_name,
            row_id=row_id,
            row_number=before_positions[row_id],
            column=None,
            original=original,
            proposed=None,
            reason=reason,
        )
    for row_id in sorted(after_ids - before_ids):
        proposed = {
            column: value for column, value in after_rows[row_id].items() if column != ROW_ID_COLUMN
        }
        collector.add(
            kind=ChangeKind.ROW_ADDED,
            step=step,
            key=key,
            file_name=file_name,
            row_id=row_id,
            row_number=after_positions[row_id],
            column=None,
            original=None,
            proposed=proposed,
            reason=reason,
        )
    before_columns = [column for column in before.columns if column != ROW_ID_COLUMN]
    after_columns = [column for column in after.columns if column != ROW_ID_COLUMN]
    common_columns = set(before_columns) & set(after_columns)
    for row_id in sorted(before_ids & after_ids):
        for column in sorted(common_columns):
            original = before_rows[row_id][column]
            proposed = after_rows[row_id][column]
            if not _same(original, proposed):
                collector.add(
                    kind=ChangeKind.CELL_CHANGED,
                    step=step,
                    key=key,
                    file_name=file_name,
                    row_id=row_id,
                    row_number=after_positions[row_id],
                    column=column,
                    original=original,
                    proposed=proposed,
                    reason=reason,
                )
        if before_positions[row_id] != after_positions[row_id]:
            collector.add(
                kind=ChangeKind.ROW_REORDERED,
                step=step,
                key=key,
                file_name=file_name,
                row_id=row_id,
                row_number=after_positions[row_id],
                column=None,
                original=before_positions[row_id],
                proposed=after_positions[row_id],
                reason=reason,
            )
        for column in sorted(set(before_columns) - set(after_columns)):
            collector.add(
                kind=ChangeKind.COLUMN_DELETED,
                step=step,
                key=key,
                file_name=file_name,
                row_id=row_id,
                row_number=before_positions[row_id],
                column=column,
                original=before_rows[row_id][column],
                proposed=None,
                reason=reason,
            )
        for column in sorted(set(after_columns) - set(before_columns)):
            collector.add(
                kind=ChangeKind.COLUMN_ADDED,
                step=step,
                key=key,
                file_name=file_name,
                row_id=row_id,
                row_number=after_positions[row_id],
                column=column,
                original=None,
                proposed=after_rows[row_id][column],
                reason=reason,
            )


class PlanRunner:
    """Apply validated registry operations in order to immutable Polars tables."""

    def __init__(self, registry: OperationRegistry, *, max_preview_records: int = 10_000) -> None:
        self.registry = registry
        self.max_preview_records = max_preview_records

    def _resolve_key(self, step: PlanStep, tables: dict[DatasetKey, pl.DataFrame]) -> DatasetKey:
        candidates = [key for key in tables if key.source_id == step.target.source_id]
        if step.target.sheet is not None:
            requested = DatasetKey(step.target.source_id, step.target.sheet)
            if requested not in tables:
                raise InvalidPlanError(f"Target worksheet is missing: {step.target.sheet}")
            return requested
        if len(candidates) != 1:
            raise InvalidPlanError("A target sheet is required when a source has multiple tables.")
        return candidates[0]

    def run(
        self,
        plan: OperationPlan,
        input_tables: dict[DatasetKey, pl.DataFrame],
    ) -> PlanRunResult:
        typed_parameters = PlanValidator(self.registry).validate(plan)
        tables: dict[DatasetKey, pl.DataFrame] = {}
        next_row_id = 1
        for key in sorted(input_tables):
            validate_table_columns(input_tables[key])
            frame = _with_row_ids(input_tables[key], offset=next_row_id)
            tables[key] = frame
            next_row_id += frame.height
        original_tables = dict(tables)
        source_names = {source.source_id: source.file_name for source in plan.source_files}
        collector = _ChangeCollector(self.max_preview_records)
        summaries: list[StepRunSummary] = []
        validation_reports: list[ValidationReport] = []
        for step in plan.steps:
            if not step.enabled:
                continue
            key = self._resolve_key(step, tables)
            before = tables[key]
            missing_targets = sorted(set(step.target.columns) - set(before.columns))
            if missing_targets:
                raise InvalidPlanError(f"Target columns are missing: {', '.join(missing_targets)}")
            operation = self.registry.get(step.operation)
            parameters = typed_parameters[step.step_id]
            if _contains_reserved_name(parameters.model_dump(mode="python")):
                raise InvalidPlanError("A plan cannot reference reserved row metadata.")
            if isinstance(operation, MergeTablesOperation):
                if not isinstance(parameters, MergeTablesParameters):
                    raise InvalidPlanError("Merge parameters failed typed validation.")
                merge_input = {
                    dataset_key.sheet: frame
                    for dataset_key, frame in tables.items()
                    if dataset_key.source_id == key.source_id
                }
                raw_result = operation.execute(merge_input, parameters)
            else:
                raw_result = operation.execute(before, parameters)
            if not isinstance(raw_result, TableOperationResult):
                raise InvalidPlanError("The registered operation returned an invalid result type.")
            if raw_result.validation_report is not None:
                if not isinstance(raw_result.validation_report, ValidationReport):
                    raise InvalidPlanError(
                        "The registered operation returned an invalid validation report."
                    )
                validation_reports.append(raw_result.validation_report)
            after = raw_result.frame
            _validate_plan_table(after)
            if ROW_ID_COLUMN not in after.columns and after.height == before.height:
                after = after.with_columns(before.get_column(ROW_ID_COLUMN))
            elif ROW_ID_COLUMN not in after.columns:
                after = _with_row_ids(after, offset=next_row_id)
                next_row_id += after.height
            after, next_row_id = _repair_duplicate_row_ids(after, next_row_id=next_row_id)
            tables[key] = after
            before_total = collector.total
            _capture_diff(
                before,
                after,
                step=step,
                key=key,
                file_name=source_names[key.source_id],
                reason=operation.audit_description,
                collector=collector,
            )
            for table_name, auxiliary in raw_result.auxiliary_tables.items():
                auxiliary_key = DatasetKey(key.source_id, table_name)
                if auxiliary_key in tables:
                    raise InvalidPlanError(
                        f"An operation created a duplicate table name: {table_name}"
                    )
                auxiliary = strip_internal_columns(auxiliary)
                validate_table_columns(auxiliary)
                tables[auxiliary_key] = _with_row_ids(auxiliary, offset=next_row_id)
                next_row_id += auxiliary.height
                _capture_diff(
                    pl.DataFrame(schema=strip_internal_columns(auxiliary).schema),
                    tables[auxiliary_key],
                    step=step,
                    key=auxiliary_key,
                    file_name=source_names[key.source_id],
                    reason=operation.audit_description,
                    collector=collector,
                )
            summaries.append(
                StepRunSummary(
                    step_id=step.step_id,
                    operation=step.operation,
                    metrics=raw_result.metrics,
                    warnings=raw_result.warnings,
                    change_count=collector.total - before_total,
                )
            )
        return PlanRunResult(
            original_tables=original_tables,
            tables=tables,
            changes=tuple(collector.records),
            total_change_count=collector.total,
            steps=tuple(summaries),
            validation_reports=tuple(validation_reports),
        )
