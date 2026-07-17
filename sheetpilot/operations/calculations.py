"""Allowlisted static-value calculations and summary statistics."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from functools import reduce
from operator import add, and_, mul
from typing import Annotated, Literal

import polars as pl
from pydantic import Field, model_validator

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.operations.base import OperationParameters
from sheetpilot.operations.filtering import (
    ConditionMatch,
    FilterCondition,
    _condition_expression,
)
from sheetpilot.operations.tabular import TableOperationResult, TabularOperation, require_columns

Scalar = str | int | float | bool | None


class Operand(OperationParameters):
    column: str | None = None
    value: float | None = None

    @model_validator(mode="after")
    def exactly_one_source(self) -> Operand:
        if (self.column is None) == (self.value is None):
            raise ValueError("an operand requires exactly one of column or value")
        return self


class ArithmeticAction(OperationParameters):
    kind: Literal["addition", "subtraction", "multiplication", "division"]
    output: str
    operands: list[Operand] = Field(min_length=2)

    @model_validator(mode="after")
    def binary_only_when_order_matters(self) -> ArithmeticAction:
        if self.kind in {"subtraction", "division"} and len(self.operands) != 2:
            raise ValueError("subtraction and division require exactly two operands")
        return self


class PercentageAction(OperationParameters):
    kind: Literal["percentage"]
    output: str
    numerator: Operand
    denominator: Operand


class QuantityPriceAction(OperationParameters):
    kind: Literal["quantity_price"]
    output: str
    quantity_column: str
    price_column: str


class GstAction(OperationParameters):
    kind: Literal["gst"]
    output: str
    base_column: str
    rate_percent: float = Field(ge=0, le=100)
    include_base: bool = False


class DateDifferenceUnit(StrEnum):
    DAYS = "days"
    MONTHS = "months"
    YEARS = "years"


class DateDifferenceAction(OperationParameters):
    kind: Literal["date_difference"]
    output: str
    start_column: str
    end_column: str
    unit: DateDifferenceUnit = DateDifferenceUnit.DAYS


class AgeAction(OperationParameters):
    kind: Literal["age"]
    output: str
    birth_date_column: str
    as_of: date


class ClassificationRule(OperationParameters):
    conditions: list[FilterCondition] = Field(min_length=1)
    match: ConditionMatch = ConditionMatch.ALL
    value: Scalar


class ConditionalAction(OperationParameters):
    kind: Literal["conditional_classification"]
    output: str
    rules: list[ClassificationRule] = Field(min_length=1)
    default: Scalar = None


class Aggregation(StrEnum):
    SUM = "sum"
    MEAN = "mean"
    MIN = "min"
    MAX = "max"
    COUNT = "count"


class GroupTotalAction(OperationParameters):
    kind: Literal["group_total"]
    output: str
    group_by: list[str] = Field(min_length=1)
    value_column: str
    aggregation: Aggregation = Aggregation.SUM


class RunningTotalAction(OperationParameters):
    kind: Literal["running_total"]
    output: str
    value_column: str
    group_by: list[str] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)


class CalculationLookupAction(OperationParameters):
    kind: Literal["lookup_value"]
    output: str
    source_column: str
    mapping: dict[str, Scalar] = Field(min_length=1)
    default: Scalar = None


CalculationAction = Annotated[
    ArithmeticAction
    | PercentageAction
    | QuantityPriceAction
    | GstAction
    | DateDifferenceAction
    | AgeAction
    | ConditionalAction
    | GroupTotalAction
    | RunningTotalAction
    | CalculationLookupAction,
    Field(discriminator="kind"),
]


class CalculateColumnParameters(OperationParameters):
    action: CalculationAction


def _operand_expression(operand: Operand) -> pl.Expr:
    if operand.column is not None:
        return pl.col(operand.column).cast(pl.Float64, strict=False)
    return pl.lit(operand.value, dtype=pl.Float64)


def _operand_columns(operands: list[Operand]) -> list[str]:
    return [operand.column for operand in operands if operand.column is not None]


def _ensure_output(frame: pl.DataFrame, output: str) -> None:
    if output in frame.columns:
        raise InvalidPlanError(f"Calculation output column already exists: {output}")


def _parse_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip()).date()
        except ValueError:
            return None
    return None


def _date_differences(frame: pl.DataFrame, action: DateDifferenceAction) -> pl.Series:
    values: list[int | None] = []
    for start_value, end_value in frame.select(action.start_column, action.end_column).iter_rows():
        start, end = _parse_date(start_value), _parse_date(end_value)
        if start is None or end is None:
            values.append(None)
        elif action.unit == DateDifferenceUnit.DAYS:
            values.append((end - start).days)
        elif action.unit == DateDifferenceUnit.MONTHS:
            values.append((end.year - start.year) * 12 + end.month - start.month)
        else:
            years = end.year - start.year - ((end.month, end.day) < (start.month, start.day))
            values.append(years)
    return pl.Series(action.output, values, dtype=pl.Int64)


def _ages(frame: pl.DataFrame, action: AgeAction) -> pl.Series:
    values: list[int | None] = []
    for value in frame.get_column(action.birth_date_column).to_list():
        birth = _parse_date(value)
        if birth is None or birth > action.as_of:
            values.append(None)
        else:
            values.append(
                action.as_of.year
                - birth.year
                - ((action.as_of.month, action.as_of.day) < (birth.month, birth.day))
            )
    return pl.Series(action.output, values, dtype=pl.Int64)


def _conditional_expression(frame: pl.DataFrame, action: ConditionalAction) -> pl.Expr:
    expression: pl.Expr = pl.lit(action.default)
    for rule in reversed(action.rules):
        columns = [condition.column for condition in rule.conditions]
        require_columns(frame, columns)
        masks = [
            _condition_expression(condition, frame.schema).fill_null(False)
            for condition in rule.conditions
        ]
        mask = (
            reduce(and_, masks)
            if rule.match == ConditionMatch.ALL
            else reduce(lambda a, b: a | b, masks)
        )
        expression = pl.when(mask).then(pl.lit(rule.value)).otherwise(expression)
    return expression.alias(action.output)


def _group_total_expression(action: GroupTotalAction) -> pl.Expr:
    value = pl.col(action.value_column)
    aggregations = {
        Aggregation.SUM: value.sum(),
        Aggregation.MEAN: value.mean(),
        Aggregation.MIN: value.min(),
        Aggregation.MAX: value.max(),
        Aggregation.COUNT: value.count(),
    }
    return aggregations[action.aggregation].over(action.group_by).alias(action.output)


def _running_total(frame: pl.DataFrame, action: RunningTotalAction) -> pl.DataFrame:
    temporary = "_sheetpilot_calculation_order"
    if temporary in frame.columns:
        raise InvalidPlanError("Reserved calculation metadata column already exists.")
    indexed = frame.with_row_index(temporary)
    sort_columns = action.group_by + action.order_by
    ordered = indexed.sort(sort_columns, maintain_order=True) if sort_columns else indexed
    expression = pl.col(action.value_column).cast(pl.Float64, strict=False).cum_sum()
    if action.group_by:
        expression = expression.over(action.group_by)
    calculated = ordered.with_columns(expression.alias(action.output))
    return calculated.sort(temporary).drop(temporary)


class CalculateColumnOperation(TabularOperation[CalculateColumnParameters]):
    """Calculate static values from an allowlisted calculation schema."""

    name = "calculate.column"
    parameters_model = CalculateColumnParameters

    def execute(
        self, data: pl.DataFrame, parameters: CalculateColumnParameters
    ) -> TableOperationResult:
        action = parameters.action
        _ensure_output(data, action.output)
        division_by_zero = 0
        if isinstance(action, ArithmeticAction):
            require_columns(data, _operand_columns(action.operands))
            expressions = [_operand_expression(operand) for operand in action.operands]
            if action.kind == "addition":
                output = reduce(add, expressions)
            elif action.kind == "multiplication":
                output = reduce(mul, expressions)
            elif action.kind == "subtraction":
                output = expressions[0] - expressions[1]
            else:
                denominator = expressions[1]
                division_by_zero = data.select((denominator == 0).sum()).item()
                output = (
                    pl.when(denominator == 0).then(None).otherwise(expressions[0] / denominator)
                )
            frame = data.with_columns(output.alias(action.output))
        elif isinstance(action, PercentageAction):
            require_columns(data, _operand_columns([action.numerator, action.denominator]))
            numerator = _operand_expression(action.numerator)
            denominator = _operand_expression(action.denominator)
            division_by_zero = data.select((denominator == 0).sum()).item()
            output = pl.when(denominator == 0).then(None).otherwise(numerator / denominator * 100)
            frame = data.with_columns(output.alias(action.output))
        elif isinstance(action, QuantityPriceAction):
            require_columns(data, [action.quantity_column, action.price_column])
            output = pl.col(action.quantity_column) * pl.col(action.price_column)
            frame = data.with_columns(output.alias(action.output))
        elif isinstance(action, GstAction):
            require_columns(data, [action.base_column])
            gst = pl.col(action.base_column) * action.rate_percent / 100
            output = pl.col(action.base_column) + gst if action.include_base else gst
            frame = data.with_columns(output.alias(action.output))
        elif isinstance(action, DateDifferenceAction):
            require_columns(data, [action.start_column, action.end_column])
            frame = data.with_columns(_date_differences(data, action))
        elif isinstance(action, AgeAction):
            require_columns(data, [action.birth_date_column])
            frame = data.with_columns(_ages(data, action))
        elif isinstance(action, ConditionalAction):
            frame = data.with_columns(_conditional_expression(data, action))
        elif isinstance(action, GroupTotalAction):
            require_columns(data, [*action.group_by, action.value_column])
            frame = data.with_columns(_group_total_expression(action))
        elif isinstance(action, RunningTotalAction):
            require_columns(data, action.group_by + action.order_by + [action.value_column])
            frame = _running_total(data, action)
        else:
            require_columns(data, [action.source_column])
            values = [
                action.mapping.get(str(value), action.default)
                for value in data.get_column(action.source_column)
            ]
            frame = data.with_columns(pl.Series(action.output, values, strict=False))
        warnings = (
            (f"{division_by_zero} division-by-zero result(s) were left blank.",)
            if division_by_zero
            else ()
        )
        return TableOperationResult(
            frame=frame,
            warnings=warnings,
            metrics={"calculated_rows": data.height, "division_by_zero": division_by_zero},
        )


class SummaryStatistic(StrEnum):
    COUNT = "count"
    NULL_COUNT = "null_count"
    UNIQUE_COUNT = "unique_count"
    SUM = "sum"
    MEAN = "mean"
    MIN = "min"
    MAX = "max"
    MEDIAN = "median"


class SummaryStatisticsParameters(OperationParameters):
    columns: list[str] = Field(min_length=1)
    statistics: list[SummaryStatistic] = Field(min_length=1)
    table_name: str = "Summary Statistics"


class SummaryStatisticsOperation(TabularOperation[SummaryStatisticsParameters]):
    """Produce a named aggregate table without changing the primary data."""

    name = "calculate.summary"
    parameters_model = SummaryStatisticsParameters

    def execute(
        self, data: pl.DataFrame, parameters: SummaryStatisticsParameters
    ) -> TableOperationResult:
        require_columns(data, parameters.columns)
        rows: list[dict[str, str | int | float | None]] = []
        for column in parameters.columns:
            series = data.get_column(column)
            for statistic in parameters.statistics:
                if statistic == SummaryStatistic.COUNT:
                    value = series.len() - series.null_count()
                elif statistic == SummaryStatistic.NULL_COUNT:
                    value = series.null_count()
                elif statistic == SummaryStatistic.UNIQUE_COUNT:
                    value = series.n_unique()
                else:
                    numeric = series.cast(pl.Float64, strict=False)
                    value = getattr(numeric, statistic.value)()
                rows.append({"Column": column, "Statistic": statistic.value, "Value": value})
        summary = pl.DataFrame(rows, strict=False)
        return TableOperationResult(
            frame=data,
            auxiliary_tables={parameters.table_name: summary},
            metrics={"summary_rows": summary.height},
        )
