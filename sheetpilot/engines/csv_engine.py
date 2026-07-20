"""UTF-8 CSV import/export with formula-injection neutralization."""

from __future__ import annotations

import csv
from pathlib import Path

import polars as pl

from sheetpilot.core.exceptions import OutputCollisionError
from sheetpilot.operations.tabular import validate_table_columns
from sheetpilot.security.formula_guard import neutralize_formula_text


def write_safe_csv(frame: pl.DataFrame, destination: Path) -> Path:
    """Write text safely for spreadsheet import without overwriting a file."""
    validate_table_columns(frame)
    if destination.exists():
        raise OutputCollisionError("CSV export cannot overwrite an existing file.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow([neutralize_formula_text(column) for column in frame.columns])
            for row in frame.iter_rows():
                writer.writerow(
                    [
                        neutralize_formula_text(value) if isinstance(value, str) else value
                        for value in row
                    ]
                )
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def read_csv(path: Path) -> pl.DataFrame:
    """Read a UTF-8 CSV through the primary Polars engine."""
    return pl.read_csv(path, encoding="utf8", infer_schema_length=None, try_parse_dates=True)
