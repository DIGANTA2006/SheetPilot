"""Typed deterministic column transformations."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal

import polars as pl
from pydantic import Field, model_validator

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.operations.base import OperationParameters
from sheetpilot.operations.tabular import TableOperationResult, TabularOperation, require_columns

Scalar = str | int | float | bool | None


class RenameColumnsAction(OperationParameters):
    kind: Literal["rename"]
    mapping: dict[str, str] = Field(min_length=1)


class ReorderColumnsAction(OperationParameters):
    kind: Literal["reorder"]
    columns: list[str] = Field(min_length=1)
    append_unlisted: bool = True


class AddColumnAction(OperationParameters):
    kind: Literal["add"]
    name: str
    value: Scalar = None


class RemoveColumnsAction(OperationParameters):
    kind: Literal["remove"]
    columns: list[str] = Field(min_length=1)


class SplitColumnAction(OperationParameters):
    kind: Literal["split"]
    source: str
    delimiter: str = Field(min_length=1)
    output_columns: list[str] = Field(min_length=2)
    keep_source: bool = True


class CombineColumnsAction(OperationParameters):
    kind: Literal["combine"]
    columns: list[str] = Field(min_length=1)
    output: str
    delimiter: str = " "
    skip_nulls: bool = True
    drop_sources: bool = False


class ExtractDelimiterAction(OperationParameters):
    kind: Literal["extract_before", "extract_after"]
    source: str
    output: str
    delimiter: str = Field(min_length=1)
    keep_source: bool = True


class FixedPositionAction(OperationParameters):
    kind: Literal["extract_fixed"]
    source: str
    output: str
    start: int = Field(ge=0)
    length: int = Field(gt=0)
    keep_source: bool = True


class UnmatchedPolicy(StrEnum):
    LEAVE = "leave"
    NULL = "null"
    REJECT = "reject"


class LookupAction(OperationParameters):
    kind: Literal["lookup"]
    source: str
    output: str
    mapping: dict[str, Scalar] = Field(min_length=1)
    unmatched_policy: UnmatchedPolicy = UnmatchedPolicy.LEAVE


class RowNumberAction(OperationParameters):
    kind: Literal["row_number"]
    output: str = "Row Number"
    start: int = 1
    step: int = 1

    @model_validator(mode="after")
    def nonzero_step(self) -> RowNumberAction:
        if self.step == 0:
            raise ValueError("row-number step cannot be zero")
        return self


class DeterministicIdAction(OperationParameters):
    kind: Literal["deterministic_id"]
    output: str
    columns: list[str] = Field(min_length=1)
    namespace: str = Field(default="sheetpilot", min_length=1, max_length=100)
    prefix: str = Field(default="", max_length=20)
    length: int = Field(default=16, ge=8, le=64)


ColumnAction = Annotated[
    RenameColumnsAction
    | ReorderColumnsAction
    | AddColumnAction
    | RemoveColumnsAction
    | SplitColumnAction
    | CombineColumnsAction
    | ExtractDelimiterAction
    | FixedPositionAction
    | LookupAction
    | RowNumberAction
    | DeterministicIdAction,
    Field(discriminator="kind"),
]


class ColumnTransformParameters(OperationParameters):
    actions: list[ColumnAction] = Field(min_length=1)


def _ensure_new_columns(
    frame: pl.DataFrame, columns: list[str], *, replacing: set[str] | None = None
) -> None:
    allowed = replacing or set()
    collisions = sorted(
        column for column in columns if column in frame.columns and column not in allowed
    )
    if len(columns) != len(set(columns)) or collisions:
        raise InvalidPlanError("A column operation would create duplicate column names.")


def _split(frame: pl.DataFrame, action: SplitColumnAction) -> pl.DataFrame:
    require_columns(frame, [action.source])
    _ensure_new_columns(frame, action.output_columns)
    output_values: list[list[str | None]] = [[] for _ in action.output_columns]
    for value in frame.get_column(action.source).cast(pl.String, strict=False).to_list():
        pieces = (
            [] if value is None else value.split(action.delimiter, len(action.output_columns) - 1)
        )
        for index, values in enumerate(output_values):
            values.append(pieces[index] if index < len(pieces) else None)
    expressions = [
        pl.Series(name, values, dtype=pl.String)
        for name, values in zip(action.output_columns, output_values, strict=True)
    ]
    result = frame.with_columns(expressions)
    return result if action.keep_source else result.drop(action.source)


def _combine(frame: pl.DataFrame, action: CombineColumnsAction) -> pl.DataFrame:
    require_columns(frame, action.columns)
    _ensure_new_columns(
        frame, [action.output], replacing=set(action.columns) if action.drop_sources else set()
    )
    values: list[str] = []
    for row in frame.select(action.columns).iter_rows():
        parts = [
            str(value) if value is not None else ""
            for value in row
            if value is not None or not action.skip_nulls
        ]
        values.append(action.delimiter.join(parts))
    result = frame.with_columns(pl.Series(action.output, values, dtype=pl.String))
    drops = [column for column in action.columns if action.drop_sources and column != action.output]
    return result.drop(drops) if drops else result


def _extract_delimiter(frame: pl.DataFrame, action: ExtractDelimiterAction) -> pl.DataFrame:
    require_columns(frame, [action.source])
    _ensure_new_columns(
        frame, [action.output], replacing={action.source} if not action.keep_source else set()
    )
    values: list[str | None] = []
    for value in frame.get_column(action.source).cast(pl.String, strict=False).to_list():
        if value is None:
            values.append(None)
        elif action.delimiter not in value:
            values.append(value if action.kind == "extract_before" else "")
        elif action.kind == "extract_before":
            values.append(value.split(action.delimiter, 1)[0])
        else:
            values.append(value.split(action.delimiter, 1)[1])
    result = frame.with_columns(pl.Series(action.output, values, dtype=pl.String))
    return (
        result
        if action.keep_source or action.output == action.source
        else result.drop(action.source)
    )


def _extract_fixed(frame: pl.DataFrame, action: FixedPositionAction) -> pl.DataFrame:
    require_columns(frame, [action.source])
    _ensure_new_columns(
        frame, [action.output], replacing={action.source} if not action.keep_source else set()
    )
    values = [
        None if value is None else value[action.start : action.start + action.length]
        for value in frame.get_column(action.source).cast(pl.String, strict=False).to_list()
    ]
    result = frame.with_columns(pl.Series(action.output, values, dtype=pl.String))
    return (
        result
        if action.keep_source or action.output == action.source
        else result.drop(action.source)
    )


def _lookup(frame: pl.DataFrame, action: LookupAction) -> tuple[pl.DataFrame, int]:
    require_columns(frame, [action.source])
    _ensure_new_columns(frame, [action.output], replacing={action.source})
    output: list[Scalar] = []
    unmatched = 0
    for value in frame.get_column(action.source).to_list():
        key = str(value)
        if key in action.mapping:
            output.append(action.mapping[key])
        else:
            unmatched += 1
            if action.unmatched_policy == UnmatchedPolicy.REJECT:
                raise InvalidPlanError(
                    f"{unmatched} lookup value(s) are missing from the approved map."
                )
            output.append(None if action.unmatched_policy == UnmatchedPolicy.NULL else value)
    return (frame.with_columns(pl.Series(action.output, output, strict=False)), unmatched)


def _deterministic_ids(frame: pl.DataFrame, action: DeterministicIdAction) -> pl.Series:
    require_columns(frame, action.columns)
    values: list[str] = []
    for row in frame.select(action.columns).iter_rows():
        canonical = json.dumps(
            {"namespace": action.namespace, "values": row},
            ensure_ascii=True,
            default=str,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[: action.length]
        values.append(f"{action.prefix}{digest}")
    return pl.Series(action.output, values, dtype=pl.String)


class ColumnTransformOperation(TabularOperation[ColumnTransformParameters]):
    """Apply an ordered sequence of allowlisted structural column actions."""

    name = "columns.transform"
    parameters_model = ColumnTransformParameters

    def is_destructive(self, parameters: ColumnTransformParameters) -> bool:
        return any(
            isinstance(action, RemoveColumnsAction)
            or (
                isinstance(action, (SplitColumnAction, ExtractDelimiterAction, FixedPositionAction))
                and not action.keep_source
            )
            or (isinstance(action, CombineColumnsAction) and action.drop_sources)
            or (
                isinstance(action, LookupAction) and action.unmatched_policy == UnmatchedPolicy.NULL
            )
            for action in parameters.actions
        )

    def execute(
        self, data: pl.DataFrame, parameters: ColumnTransformParameters
    ) -> TableOperationResult:
        frame = data
        unmatched_total = 0
        for action in parameters.actions:
            if isinstance(action, RenameColumnsAction):
                require_columns(frame, list(action.mapping))
                targets = [action.mapping.get(column, column) for column in frame.columns]
                if len(targets) != len(set(targets)):
                    raise InvalidPlanError("Rename would create duplicate column names.")
                frame = frame.rename(action.mapping)
            elif isinstance(action, ReorderColumnsAction):
                require_columns(frame, action.columns)
                remaining = [column for column in frame.columns if column not in action.columns]
                frame = frame.select(action.columns + (remaining if action.append_unlisted else []))
            elif isinstance(action, AddColumnAction):
                _ensure_new_columns(frame, [action.name])
                frame = frame.with_columns(pl.lit(action.value).alias(action.name))
            elif isinstance(action, RemoveColumnsAction):
                require_columns(frame, action.columns)
                frame = frame.drop(action.columns)
            elif isinstance(action, SplitColumnAction):
                frame = _split(frame, action)
            elif isinstance(action, CombineColumnsAction):
                frame = _combine(frame, action)
            elif isinstance(action, ExtractDelimiterAction):
                frame = _extract_delimiter(frame, action)
            elif isinstance(action, FixedPositionAction):
                frame = _extract_fixed(frame, action)
            elif isinstance(action, LookupAction):
                frame, unmatched = _lookup(frame, action)
                unmatched_total += unmatched
            elif isinstance(action, RowNumberAction):
                _ensure_new_columns(frame, [action.output])
                values = range(action.start, action.start + action.step * frame.height, action.step)
                frame = frame.with_columns(pl.Series(action.output, values, dtype=pl.Int64))
            else:
                _ensure_new_columns(frame, [action.output])
                frame = frame.with_columns(_deterministic_ids(frame, action))
        warnings = (
            (f"{unmatched_total} lookup value(s) were not present in the approved mapping.",)
            if unmatched_total
            else ()
        )
        return TableOperationResult(
            frame=frame,
            warnings=warnings,
            metrics={
                "input_columns": data.width,
                "output_columns": frame.width,
                "unmatched_lookups": unmatched_total,
            },
        )
