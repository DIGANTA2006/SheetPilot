from __future__ import annotations

import zipfile
from pathlib import Path

import openpyxl
import pytest

from sheetpilot.app.config import SecurityLimits
from sheetpilot.core.exceptions import ArchiveSecurityError, FileLimitError, UnsupportedFormatError
from sheetpilot.security.archive_guard import inspect_ooxml_archive
from sheetpilot.security.file_guard import validate_input_file


def _minimal_archive(path: Path, extra: dict[str, bytes] | None = None) -> None:
    members = {
        "[Content_Types].xml": b"<Types/>",
        "xl/workbook.xml": b"<workbook/>",
        **(extra or {}),
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in members.items():
            archive.writestr(name, value)


def test_archive_rejects_path_traversal(tmp_path: Path) -> None:
    source = tmp_path / "unsafe.xlsx"
    _minimal_archive(source, {"../escaped.xml": b"<x/>"})
    with pytest.raises(ArchiveSecurityError, match="traversal"):
        inspect_ooxml_archive(source, SecurityLimits())


def test_archive_rejects_dtd_and_entities(tmp_path: Path) -> None:
    source = tmp_path / "entity.xlsx"
    _minimal_archive(source, {"xl/worksheets/sheet1.xml": b"<!DOCTYPE x><x/>"})
    with pytest.raises(ArchiveSecurityError, match="DTD"):
        inspect_ooxml_archive(source, SecurityLimits())


def test_archive_enforces_configurable_member_count(tmp_path: Path) -> None:
    source = tmp_path / "large.xlsx"
    _minimal_archive(source)
    with pytest.raises(FileLimitError, match="too many"):
        inspect_ooxml_archive(source, SecurityLimits(max_archive_members=1))


def test_archive_detects_macros_and_advanced_features(tmp_path: Path) -> None:
    source = tmp_path / "features.xlsm"
    _minimal_archive(
        source,
        {
            "xl/vbaProject.bin": b"inert test marker",
            "xl/externalLinks/externalLink1.xml": b"<externalLink/>",
            "xl/activeX/activeX1.bin": b"inert",
            "xl/pivotTables/pivotTable1.xml": b"<pivotTable/>",
        },
    )
    inspection = inspect_ooxml_archive(source, SecurityLimits())
    assert inspection.macro_present
    assert inspection.external_links_present
    assert {"active_x", "pivot_tables"} <= set(inspection.unsupported_features)


def test_file_guard_rejects_extension_content_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "fake.xlsx"
    source.write_text("not a workbook", encoding="utf-8")
    with pytest.raises(UnsupportedFormatError, match="does not match"):
        validate_input_file(source, SecurityLimits())


def test_file_guard_allows_real_xlsx(tmp_path: Path) -> None:
    source = tmp_path / "real.xlsx"
    workbook = openpyxl.Workbook()
    workbook.save(source)
    workbook.close()
    assert validate_input_file(source, SecurityLimits()) == "xlsx"
