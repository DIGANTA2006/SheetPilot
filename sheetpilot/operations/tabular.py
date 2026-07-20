"""Polars operation result and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import polars as pl

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.operations.base import Operation, OperationParameters

if TYPE_CHECKING:
    from sheetpilot.operations.validation import ValidationReport


@dataclass(frozen=True)
class TableOperationResult:
    """One primary table plus optional named tables and aggregate-only metadata."""

    frame: pl.DataFrame
    auxiliary_tables: dict[str, pl.DataFrame] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    metrics: dict[str, int | float | str] = field(default_factory=dict)
    validation_report: ValidationReport | None = None


def require_columns(frame: pl.DataFrame, columns: list[str] | tuple[str, ...]) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise InvalidPlanError(f"Missing required columns: {', '.join(missing)}")


def validate_table_columns(frame: pl.DataFrame, *, excluded_prefixes: tuple[str, ...] = ()) -> None:
    """Require portable, user-visible column names before preview or export."""
    folded_prefixes = tuple(prefix.casefold() for prefix in excluded_prefixes)
    columns = [
        column for column in frame.columns if not column.casefold().startswith(folded_prefixes)
    ]
    if not columns:
        raise InvalidPlanError("A table must contain at least one user-visible column.")
    if any(not column.strip() or "\x00" in column or len(column) > 255 for column in columns):
        raise InvalidPlanError(
            "Table columns must be non-blank text, contain no NUL characters, and be at most "
            "255 characters."
        )
    if len(columns) != len({column.casefold() for column in columns}):
        raise InvalidPlanError("Table columns must be unique without regard to letter case.")


class TabularOperation[ParametersT: OperationParameters](Operation[ParametersT]):
    """Operation base for immutable Polars transforms."""

    supported_engines = frozenset({"polars"})

    def preview(self, data: pl.DataFrame, parameters: ParametersT) -> TableOperationResult:
        return cast(TableOperationResult, self.execute(data, parameters))
