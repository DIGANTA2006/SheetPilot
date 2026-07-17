"""Polars operation result and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

import polars as pl

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.operations.base import Operation, OperationParameters


@dataclass(frozen=True)
class TableOperationResult:
    """One primary table plus optional named tables and aggregate-only metadata."""

    frame: pl.DataFrame
    auxiliary_tables: dict[str, pl.DataFrame] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    metrics: dict[str, int | float | str] = field(default_factory=dict)


def require_columns(frame: pl.DataFrame, columns: list[str] | tuple[str, ...]) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise InvalidPlanError(f"Missing required columns: {', '.join(missing)}")


class TabularOperation[ParametersT: OperationParameters](Operation[ParametersT]):
    """Operation base for immutable Polars transforms."""

    supported_engines = frozenset({"polars"})

    def preview(self, data: pl.DataFrame, parameters: ParametersT) -> TableOperationResult:
        return cast(TableOperationResult, self.execute(data, parameters))
