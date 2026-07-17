"""Deterministic table merging with explicit schema policy."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

import polars as pl
from pydantic import Field

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.operations.base import Operation, OperationParameters
from sheetpilot.operations.tabular import TableOperationResult


class SchemaMode(StrEnum):
    STRICT = "strict"
    UNION = "union"


class MergeTablesParameters(OperationParameters):
    table_names: list[str] = Field(default_factory=list)
    schema_mode: SchemaMode = SchemaMode.STRICT
    source_column: str | None = None


class MergeTablesOperation(Operation[MergeTablesParameters]):
    """Vertically merge selected in-memory tables; no paths or SQL are accepted."""

    name = "tables.merge"
    parameters_model = MergeTablesParameters
    supported_engines = frozenset({"polars", "duckdb"})

    def preview(
        self, data: dict[str, pl.DataFrame], parameters: MergeTablesParameters
    ) -> TableOperationResult:
        return self.execute(data, parameters)

    def execute(
        self, data: dict[str, pl.DataFrame], parameters: MergeTablesParameters
    ) -> TableOperationResult:
        selected = parameters.table_names or list(data)
        missing = sorted(set(selected) - set(data))
        if missing:
            raise InvalidPlanError(f"Tables to merge are missing: {', '.join(missing)}")
        if not selected:
            raise InvalidPlanError("At least one table is required for merging.")
        frames: list[pl.DataFrame] = []
        expected_columns = data[selected[0]].columns
        for name in selected:
            frame = data[name]
            if parameters.schema_mode == SchemaMode.STRICT and frame.columns != expected_columns:
                raise InvalidPlanError("Strict table merge requires identical ordered columns.")
            if parameters.source_column:
                if parameters.source_column in frame.columns:
                    raise InvalidPlanError("Source metadata column already exists.")
                frame = frame.with_columns(pl.lit(name).alias(parameters.source_column))
            frames.append(frame)
        how: Literal["vertical", "diagonal_relaxed"] = (
            "vertical" if parameters.schema_mode == SchemaMode.STRICT else "diagonal_relaxed"
        )
        merged = pl.concat(frames, how=how)
        return TableOperationResult(
            frame=merged,
            metrics={"input_tables": len(frames), "output_rows": merged.height},
        )

    def validate_result(
        self,
        before: Any,
        after: Any,
        parameters: MergeTablesParameters,
    ) -> list[str]:
        del before, after, parameters
        return []
