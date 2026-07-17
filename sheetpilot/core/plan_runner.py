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
from sheetpilot.operations.tabular import TableOperationResult

ROW_ID_COLUMN = "_sheetpilot_preview_row_id"


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
    tables: dict[DatasetKey, pl.DataFrame]
    changes: tuple[ChangeRecord, ...]
    total_change_count: int
    steps: tuple[StepRunSummary, ...]


def _with_row_ids(frame: pl.DataFrame) -> pl.DataFrame:
    if ROW_ID_COLUMN in frame.columns:
        raise InvalidPlanError(f"Input contains a reserved column: {ROW_ID_COLUMN}")
    return frame.with_row_index(ROW_ID_COLUMN, offset=1)


def strip_internal_columns(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.drop(ROW_ID_COLUMN) if ROW_ID_COLUMN in frame.columns else frame


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
        rows[row_id] = row
        order.append(row_id)
    return (rows, order)


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
        tables = {key: _with_row_ids(frame) for key, frame in input_tables.items()}
        source_names = {source.source_id: source.file_name for source in plan.source_files}
        collector = _ChangeCollector(self.max_preview_records)
        summaries: list[StepRunSummary] = []
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
            after = raw_result.frame
            if ROW_ID_COLUMN not in after.columns and after.height == before.height:
                after = after.with_columns(before.get_column(ROW_ID_COLUMN))
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
                tables[auxiliary_key] = _with_row_ids(auxiliary)
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
            tables=tables,
            changes=tuple(collector.records),
            total_change_count=collector.total,
            steps=tuple(summaries),
        )
