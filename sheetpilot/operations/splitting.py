"""Deterministic table-to-sheet splitting."""

from __future__ import annotations

from typing import Any

import polars as pl
from pydantic import Field

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.engines.xlsxwriter_engine import safe_sheet_name
from sheetpilot.operations.base import OperationParameters
from sheetpilot.operations.tabular import TableOperationResult, TabularOperation, require_columns


class SplitByCategoryParameters(OperationParameters):
    category_column: str
    table_prefix: str = ""
    include_blank: bool = True
    drop_category_column: bool = False
    max_tables: int = Field(default=200, gt=0, le=1000)


class SplitByCategoryOperation(TabularOperation[SplitByCategoryParameters]):
    """Create named tables for exact category values without fuzzy grouping."""

    name = "table.split_by_category"
    parameters_model = SplitByCategoryParameters

    def is_destructive(self, parameters: SplitByCategoryParameters) -> bool:
        return parameters.drop_category_column

    def execute(
        self, data: pl.DataFrame, parameters: SplitByCategoryParameters
    ) -> TableOperationResult:
        require_columns(data, [parameters.category_column])
        categories: list[Any] = (
            data.get_column(parameters.category_column).unique(maintain_order=True).to_list()
        )
        if not parameters.include_blank:
            categories = [value for value in categories if value is not None and str(value).strip()]
        if len(categories) > parameters.max_tables:
            raise InvalidPlanError("Category split exceeds the configured table limit.")
        tables: dict[str, pl.DataFrame] = {}
        existing: set[str] = set()
        for category in categories:
            label = "Blank" if category is None or not str(category).strip() else str(category)
            name = safe_sheet_name(f"{parameters.table_prefix}{label}", existing)
            if category is None:
                table = data.filter(pl.col(parameters.category_column).is_null())
            else:
                table = data.filter(pl.col(parameters.category_column) == category)
            if parameters.drop_category_column:
                table = table.drop(parameters.category_column)
            tables[name] = table
        return TableOperationResult(
            frame=data,
            auxiliary_tables=tables,
            metrics={"created_tables": len(tables), "source_rows": data.height},
        )
