"""Polars-first bounded CSV profiling."""

from __future__ import annotations

import csv
from pathlib import Path

import polars as pl

from sheetpilot.app.config import SecurityLimits
from sheetpilot.core.exceptions import CorruptWorkbookError, FileLimitError
from sheetpilot.core.profile_models import SheetProfile
from sheetpilot.engines.profile_helpers import profile_rows


def _csv_shape(path: Path, limits: SecurityLimits) -> tuple[str, list[str], int, int]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            sample = stream.read(8192)
            stream.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                delimiter = dialect.delimiter
            except csv.Error:
                delimiter = ","
            reader = csv.reader(stream, delimiter=delimiter)
            try:
                headers = next(reader)
            except StopIteration as error:
                raise CorruptWorkbookError("The CSV file is empty.") from error
            width = len(headers)
            if width > limits.max_columns:
                raise FileLimitError("The CSV contains too many columns.")
            row_count = 0
            for row in reader:
                row_count += 1
                width = max(width, len(row))
                if row_count > limits.max_rows:
                    raise FileLimitError("The CSV contains too many rows.")
                if width > limits.max_columns:
                    raise FileLimitError("The CSV contains too many columns.")
    except UnicodeDecodeError as error:
        raise CorruptWorkbookError("The initial release requires UTF-8 CSV input.") from error
    except csv.Error as error:
        raise CorruptWorkbookError("The CSV structure is malformed.") from error
    estimated = row_count * max(width, 1) * 80
    if estimated > limits.max_estimated_memory_bytes:
        raise FileLimitError("The CSV exceeds the configured memory estimate limit.")
    return (delimiter, headers, row_count, width)


def profile_csv(path: Path, limits: SecurityLimits) -> tuple[SheetProfile, int]:
    """Read a validated CSV with Polars and return aggregate profile metadata."""
    delimiter, headers, raw_row_count, width = _csv_shape(path, limits)
    generated_names = [f"column_{index + 1}" for index in range(width)]
    try:
        frame = pl.read_csv(
            path,
            separator=delimiter,
            has_header=False,
            skip_rows=1,
            new_columns=generated_names,
            infer_schema_length=None,
            try_parse_dates=True,
            raise_if_empty=False,
            truncate_ragged_lines=False,
            encoding="utf8",
        )
    except (pl.exceptions.PolarsError, OSError) as error:
        raise CorruptWorkbookError("The CSV could not be parsed safely.") from error
    rows = list(frame.iter_rows())
    if len(rows) < raw_row_count:
        rows.extend(tuple(None for _ in range(width)) for _ in range(raw_row_count - len(rows)))
    sheet = profile_rows(
        name="CSV",
        visibility="visible",
        raw_headers=headers,
        rows=rows,
        used_rows=raw_row_count + 1,
        used_columns=width,
    )
    return (sheet, raw_row_count * max(width, 1) * 80)
