from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

import openpyxl
import polars as pl
import pytest

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.engines.openpyxl_export import modify_workbook_copy

_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_VBA_CONTENT_TYPE = "application/vnd.ms-office.vbaProject"
_VBA_RELATIONSHIP = "http://schemas.microsoft.com/office/2006/relationships/vbaProject"
_MACRO_WORKBOOK_TYPE = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"


def _macro_enabled_fixture(path: Path) -> bytes:
    """Create a minimal deterministic macro archive without executing VBA."""
    base = path.with_suffix(".xlsx")
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "Data"
    worksheet.append(["ID", "Name"])
    worksheet.append([1, "Original"])
    workbook.save(base)
    workbook.close()

    with ZipFile(base) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}

    content_types = ElementTree.fromstring(members["[Content_Types].xml"])
    for node in content_types:
        if node.attrib.get("PartName") == "/xl/workbook.xml":
            node.set("ContentType", _MACRO_WORKBOOK_TYPE)
    ElementTree.SubElement(
        content_types,
        f"{{{_CONTENT_TYPES_NS}}}Override",
        PartName="/xl/vbaProject.bin",
        ContentType=_VBA_CONTENT_TYPE,
    )
    members["[Content_Types].xml"] = ElementTree.tostring(
        content_types,
        encoding="utf-8",
        xml_declaration=True,
    )

    relationships = ElementTree.fromstring(members["xl/_rels/workbook.xml.rels"])
    ElementTree.SubElement(
        relationships,
        f"{{{_RELATIONSHIPS_NS}}}Relationship",
        Id="rIdSheetPilotVba",
        Type=_VBA_RELATIONSHIP,
        Target="vbaProject.bin",
    )
    members["xl/_rels/workbook.xml.rels"] = ElementTree.tostring(
        relationships,
        encoding="utf-8",
        xml_declaration=True,
    )
    vba_payload = b"SheetPilot regression macro payload; never executed."
    members["xl/vbaProject.bin"] = vba_payload

    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    base.unlink()
    return vba_payload


def test_copy_edit_preserves_vba_project_without_running_it(tmp_path: Path) -> None:
    source = tmp_path / "source.xlsm"
    destination = tmp_path / "result.xlsm"
    expected_vba = _macro_enabled_fixture(source)

    modify_workbook_copy(
        source,
        destination,
        replacements={"Data": pl.DataFrame({"ID": [1], "Name": ["Updated"]})},
    )

    with ZipFile(destination) as archive:
        assert archive.read("xl/vbaProject.bin") == expected_vba
    reopened = openpyxl.load_workbook(destination, keep_vba=True, data_only=False)
    vba_archive = reopened.vba_archive
    try:
        assert reopened["Data"]["B2"].value == "Updated"
        assert vba_archive is not None
    finally:
        reopened.close()
        if vba_archive is not None:
            vba_archive.close()


def test_macro_enabled_copy_cannot_be_mislabeled_as_xlsx(tmp_path: Path) -> None:
    source = tmp_path / "source.xlsm"
    destination = tmp_path / "unsafe.xlsx"
    _macro_enabled_fixture(source)

    with pytest.raises(InvalidPlanError, match="retain the source extension"):
        modify_workbook_copy(
            source,
            destination,
            replacements={"Data": pl.DataFrame({"ID": [1], "Name": ["Updated"]})},
        )

    assert not destination.exists()
