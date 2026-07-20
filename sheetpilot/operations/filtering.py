"""Typed, deterministic row filtering."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from functools import reduce
from operator import and_, or_
from typing import Annotated, Literal

import polars as pl
from pydantic import Field, model_validator

from sheetpilot.operations.base import OperationParameters
from sheetpilot.operations.tabular import TableOperationResult, TabularOperation, require_columns

Scalar = str | int | float | bool | date | datetime | None


class ExactCondition(OperationParameters):
    kind: Literal["exact"]
    column: str
    value: Scalar


class TextCondition(OperationParameters):
    kind: Literal["contains", "starts_with", "ends_with"]
    column: str
    value: str
    case_sensitive: bool = False


class NumericOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GE = "ge"
    LT = "lt"
    LE = "le"


class NumericCondition(OperationParameters):
    kind: Literal["numeric"]
    column: str
    operator: NumericOperator
    value: float


class DateRangeCondition(OperationParameters):
    kind: Literal["date_range"]
    column: str
    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def ordered_range(self) -> DateRangeCondition:
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("date-range start cannot be after end")
        return self


class BlankCondition(OperationParameters):
    kind: Literal["blank"]
    column: str
    is_blank: bool = True


class ListedValuesCondition(OperationParameters):
    kind: Literal["include_values", "exclude_values"]
    column: str
    values: list[Scalar] = Field(min_length=1)


FilterCondition = Annotated[
    ExactCondition
    | TextCondition
    | NumericCondition
    | DateRangeCondition
    | BlankCondition
    | ListedValuesCondition,
    Field(discriminator="kind"),
]


class ConditionMatch(StrEnum):
    ALL = "all"
    ANY = "any"


class FilterParameters(OperationParameters):
    conditions: list[FilterCondition] = Field(min_length=1)
    match: ConditionMatch = ConditionMatch.ALL
    keep_matching: bool = True


def _text_expression(condition: TextCondition) -> pl.Expr:
    expression = pl.col(condition.column).cast(pl.String, strict=False)
    value = condition.value
    if not condition.case_sensitive:
        expression = expression.str.to_lowercase()
        value = value.lower()
    if condition.kind == "contains":
        return expression.str.contains(value, literal=True)
    if condition.kind == "starts_with":
        return expression.str.starts_with(value)
    return expression.str.ends_with(value)


def _numeric_expression(condition: NumericCondition) -> pl.Expr:
    expression = pl.col(condition.column).cast(pl.Float64, strict=False)
    comparisons = {
        NumericOperator.EQ: expression == condition.value,
        NumericOperator.NE: expression != condition.value,
        NumericOperator.GT: expression > condition.value,
        NumericOperator.GE: expression >= condition.value,
        NumericOperator.LT: expression < condition.value,
        NumericOperator.LE: expression <= condition.value,
    }
    return comparisons[condition.operator]


def _date_expression(condition: DateRangeCondition, schema: pl.Schema) -> pl.Expr:
    expression = pl.col(condition.column)
    if schema[condition.column] == pl.String:
        parsed = expression.str.to_date(strict=False)
    else:
        parsed = expression.cast(pl.Date, strict=False)
    parts: list[pl.Expr] = []
    if condition.start is not None:
        parts.append(parsed >= condition.start)
    if condition.end is not None:
        parts.append(parsed <= condition.end)
    return reduce(and_, parts) if parts else parsed.is_not_null()


def _condition_expression(condition: FilterCondition, schema: pl.Schema) -> pl.Expr:
    if isinstance(condition, ExactCondition):
        return (
            pl.col(condition.column).is_null()
            if condition.value is None
            else pl.col(condition.column) == condition.value
        )
    if isinstance(condition, TextCondition):
        return _text_expression(condition)
    if isinstance(condition, NumericCondition):
        return _numeric_expression(condition)
    if isinstance(condition, DateRangeCondition):
        return _date_expression(condition, schema)
    if isinstance(condition, BlankCondition):
        blank = pl.col(condition.column).is_null() | (
            pl.col(condition.column).cast(pl.String, strict=False).str.strip_chars() == ""
        )
        return blank if condition.is_blank else ~blank
    listed = pl.col(condition.column).is_in(condition.values, nulls_equal=True)
    return listed if condition.kind == "include_values" else ~listed


class FilterRowsOperation(TabularOperation[FilterParameters]):
    """Keep or exclude rows using an allowlisted condition tree."""

    name = "rows.filter"
    parameters_model = FilterParameters
    destructive = True

    def execute(self, data: pl.DataFrame, parameters: FilterParameters) -> TableOperationResult:
        columns = [condition.column for condition in parameters.conditions]
        require_columns(data, columns)
        expressions = [
            _condition_expression(condition, data.schema).fill_null(False)
            for condition in parameters.conditions
        ]
        combine = and_ if parameters.match == ConditionMatch.ALL else or_
        mask = reduce(combine, expressions)
        if not parameters.keep_matching:
            mask = ~mask
        result = data.filter(mask)
        return TableOperationResult(
            frame=result,
            metrics={
                "input_rows": data.height,
                "output_rows": result.height,
                "removed_rows": data.height - result.height,
            },
        )
