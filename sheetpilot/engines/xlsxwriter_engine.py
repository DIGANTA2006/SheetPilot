"""Polished new-workbook generation with formula-injection-safe text output."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl
import xlsxwriter
from xlsxwriter.worksheet import Worksheet

from sheetpilot.core.exceptions import InvalidPlanError, OutputCollisionError
from sheetpilot.engines.formulas import GeneratedFormulaSpec, render_formula
from sheetpilot.operations.tabular import validate_table_columns
from sheetpilot.security.path_guard import sanitize_filename


def safe_sheet_name(name: str, existing: set[str]) -> str:
    """Return a non-empty, case-insensitively unique Excel sheet name."""
    cleaned = sanitize_filename(name).replace("[", "_").replace("]", "_")[:31].strip("'")
    cleaned = cleaned or "Sheet"
    candidate = cleaned
    counter = 2
    while candidate.casefold() in existing:
        suffix = f" ({counter})"
        candidate = f"{cleaned[: 31 - len(suffix)]}{suffix}"
        counter += 1
    existing.add(candidate.casefold())
    return candidate


def _write_value(worksheet: Worksheet, row: int, column: int, value: Any) -> None:
    if value is None:
        worksheet.write_blank(row, column, None)
    elif isinstance(value, bool):
        worksheet.write_boolean(row, column, value)
    elif isinstance(value, (int, float)):
        worksheet.write_number(row, column, value)
    elif isinstance(value, (datetime, date)):
        converted = (
            value if isinstance(value, datetime) else datetime.combine(value, datetime.min.time())
        )
        worksheet.write_datetime(row, column, converted)
    else:
        worksheet.write_string(row, column, str(value))


def write_new_workbook(
    tables: dict[str, pl.DataFrame],
    destination: Path,
    *,
    formula_specs: dict[str, list[GeneratedFormulaSpec]] | None = None,
) -> Path:
    """Create a styled workbook at a new path; existing files are never replaced."""
    if destination.exists():
        raise OutputCollisionError("A new workbook cannot overwrite an existing file.")
    if not tables:
        raise InvalidPlanError("At least one table is required for an Excel output.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook = xlsxwriter.Workbook(
        destination,
        {"strings_to_formulas": False, "strings_to_urls": False, "constant_memory": False},
    )
    try:
        header_format = workbook.add_format(
            {"bold": True, "font_color": "#FFFFFF", "bg_color": "#1D4ED8", "border": 1}
        )
        date_format = workbook.add_format({"num_format": "yyyy-mm-dd"})
        formula_formats: dict[str, Any] = {}
        existing_names: set[str] = set()
        for requested_name, frame in tables.items():
            validate_table_columns(frame)
            sheet_name = safe_sheet_name(requested_name, existing_names)
            worksheet = workbook.add_worksheet(sheet_name)
            formulas = (formula_specs or {}).get(requested_name, [])
            headers = list(frame.columns)
            if len(headers) != len(set(header.casefold() for header in headers)):
                raise InvalidPlanError("Excel output requires unique headers.")
            output_headers = headers + [spec.output_column for spec in formulas]
            if len(output_headers) != len(set(header.casefold() for header in output_headers)):
                raise InvalidPlanError("Formula outputs would create duplicate headers.")
            for column_index, header in enumerate(output_headers):
                worksheet.write(0, column_index, header, header_format)
            for row_index, row in enumerate(frame.iter_rows(), start=1):
                for column_index, value in enumerate(row):
                    if isinstance(value, (date, datetime)):
                        worksheet.write_datetime(
                            row_index,
                            column_index,
                            value
                            if isinstance(value, datetime)
                            else datetime.combine(value, datetime.min.time()),
                            date_format,
                        )
                    else:
                        _write_value(worksheet, row_index, column_index, value)
                for formula_index, spec in enumerate(formulas, start=len(headers)):
                    number_format = spec.number_format or "General"
                    if number_format not in formula_formats:
                        formula_formats[number_format] = workbook.add_format(
                            {"num_format": number_format}
                        )
                    worksheet.write_formula(
                        row_index,
                        formula_index,
                        render_formula(spec, headers, row_index + 1),
                        formula_formats[number_format],
                    )
            worksheet.freeze_panes(1, 0)
            worksheet.autofilter(0, 0, max(frame.height, 1), len(output_headers) - 1)
            for column_index, header in enumerate(output_headers):
                sample = (
                    frame.get_column(header).head(200).to_list() if header in frame.columns else []
                )
                width = min(
                    42, max(10, len(header) + 2, *(len(str(value)) + 2 for value in sample))
                )
                worksheet.set_column(column_index, column_index, width)
            if frame.height:
                worksheet.conditional_format(
                    1,
                    0,
                    frame.height,
                    len(output_headers) - 1,
                    {"type": "blanks", "format": workbook.add_format({"bg_color": "#FFF4CC"})},
                )
            worksheet.set_landscape()
            worksheet.fit_to_pages(1, 0)
    except BaseException:
        workbook.close()
        destination.unlink(missing_ok=True)
        raise
    workbook.close()
    return destination
