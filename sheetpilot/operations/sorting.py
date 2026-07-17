"""Stable multi-column sorting."""

from __future__ import annotations

import polars as pl
from pydantic import Field

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.operations.base import OperationParameters
from sheetpilot.operations.tabular import TableOperationResult, TabularOperation, require_columns

_ORDER_COLUMN = "_sheetpilot_original_order"


class SortKey(OperationParameters):
    column: str
    descending: bool = False
    nulls_last: bool = True


class SortParameters(OperationParameters):
    keys: list[SortKey] = Field(min_length=1)
    preserve_original_order_metadata: bool = False


class SortRowsOperation(TabularOperation[SortParameters]):
    """Sort by one or more keys while preserving equal-key stability."""

    name = "rows.sort"
    parameters_model = SortParameters

    def execute(self, data: pl.DataFrame, parameters: SortParameters) -> TableOperationResult:
        columns = [key.column for key in parameters.keys]
        require_columns(data, columns)
        frame = data
        if parameters.preserve_original_order_metadata:
            if _ORDER_COLUMN in data.columns:
                raise InvalidPlanError(f"Reserved metadata column already exists: {_ORDER_COLUMN}")
            frame = data.with_row_index(_ORDER_COLUMN, offset=1)
        result = frame.sort(
            by=columns,
            descending=[key.descending for key in parameters.keys],
            nulls_last=[key.nulls_last for key in parameters.keys],
            maintain_order=True,
        )
        return TableOperationResult(
            frame=result,
            metrics={"sorted_rows": data.height, "sort_keys": len(parameters.keys)},
        )
