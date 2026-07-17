"""Lifecycle helpers for openpyxl workbooks opened with VBA preservation."""

from __future__ import annotations

from openpyxl.workbook.workbook import Workbook


def close_workbook(workbook: Workbook) -> None:
    """Close both the workbook archive and openpyxl's separate VBA archive."""
    vba_archive = workbook.vba_archive
    workbook.close()
    if vba_archive is not None:
        vba_archive.close()
