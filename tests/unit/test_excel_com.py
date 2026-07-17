from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from sheetpilot.core.exceptions import OutputCollisionError, TrustedMacroError
from sheetpilot.integrations.excel_com import ExcelComEngine, TrustedMacroApproval
from sheetpilot.security.hashing import fingerprint_file


class FakePivotCache:
    def __init__(self) -> None:
        self.refreshed = False

    def Refresh(self) -> None:  # noqa: N802
        self.refreshed = True


class FakePivot:
    def __init__(self) -> None:
        self.cache = FakePivotCache()

    def PivotCache(self) -> FakePivotCache:  # noqa: N802
        return self.cache


class FakeWorksheet:
    def __init__(self) -> None:
        self.pivots: dict[str, FakePivot] = {}

    def PivotTables(self, name: str) -> FakePivot:  # noqa: N802
        return self.pivots.setdefault(name, FakePivot())

    def ExportAsFixedFormat(self, format_code: int, destination: str) -> None:  # noqa: N802
        assert format_code == 0
        Path(destination).write_bytes(b"%PDF-1.4\n")


class FakeWorkbook:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.Name = path.name
        self.sheets: dict[str, FakeWorksheet] = {}
        self.closed = False

    def SaveCopyAs(self, destination: str) -> None:  # noqa: N802
        shutil.copy2(self.path, destination)

    def Worksheets(self, name: str) -> FakeWorksheet:  # noqa: N802
        return self.sheets.setdefault(name, FakeWorksheet())

    def Close(self, **kwargs: Any) -> None:  # noqa: N802
        assert not kwargs["SaveChanges"]
        self.closed = True


class FakeWorkbooks:
    def __init__(self) -> None:
        self.opened: FakeWorkbook | None = None

    def Open(self, path: str, **kwargs: Any) -> FakeWorkbook:  # noqa: N802
        assert kwargs["UpdateLinks"] == 0
        assert not kwargs["ReadOnly"]
        self.opened = FakeWorkbook(Path(path))
        return self.opened


class FakeExcel:
    def __init__(self) -> None:
        self.Workbooks = FakeWorkbooks()
        self.AutomationSecurity = 0
        self.Visible = True
        self.DisplayAlerts = True
        self.EnableEvents = True
        self.recalculated = False
        self.runs: list[str] = []
        self.quit_called = False

    def CalculateFullRebuild(self) -> None:  # noqa: N802
        self.recalculated = True

    def Run(self, macro: str) -> None:  # noqa: N802
        self.runs.append(macro)

    def Quit(self) -> None:  # noqa: N802
        self.quit_called = True


def _workbook(tmp_path: Path, suffix: str = ".xlsx") -> tuple[Path, Path, Path]:
    workspace = tmp_path / "workspace"
    output = tmp_path / "output"
    workspace.mkdir()
    output.mkdir()
    source = workspace / f"working{suffix}"
    source.write_bytes(b"test workbook copy")
    return source, workspace, output


def test_recalculation_uses_disabled_macros_and_distinct_copy(tmp_path: Path) -> None:
    source, workspace, output = _workbook(tmp_path)
    application = FakeExcel()
    engine = ExcelComEngine(lambda: application)

    artifact = engine.recalculate_to_copy(
        source,
        output / "recalculated.xlsx",
        workspace_root=workspace,
        approved_output_root=output,
    )

    assert application.recalculated
    assert application.AutomationSecurity == 3
    assert application.quit_called
    assert artifact.path.read_bytes() == source.read_bytes()


def test_pivot_refresh_is_limited_to_explicit_names(tmp_path: Path) -> None:
    source, workspace, output = _workbook(tmp_path)
    application = FakeExcel()
    engine = ExcelComEngine(lambda: application)

    engine.refresh_approved_pivots_to_copy(
        source,
        output / "refreshed.xlsx",
        ("Summary!SalesPivot",),
        workspace_root=workspace,
        approved_output_root=output,
    )

    workbook = application.Workbooks.opened
    assert workbook is not None
    assert workbook.sheets["Summary"].pivots["SalesPivot"].cache.refreshed


def test_trusted_macro_is_hash_bound_and_opened_with_macros_disabled(tmp_path: Path) -> None:
    source, workspace, output = _workbook(tmp_path, ".xlsm")
    application = FakeExcel()
    engine = ExcelComEngine(lambda: application)
    fingerprint = fingerprint_file(source)
    approval = TrustedMacroApproval(
        workbook_sha256=fingerprint.sha256,
        macro_name="ApprovedModule.Clean",
        explicitly_confirmed=True,
    )

    engine.run_trusted_macro_to_copy(
        source,
        output / "macro-result.xlsm",
        approval,
        workspace_root=workspace,
        approved_output_root=output,
    )

    assert application.runs == ["'working.xlsm'!ApprovedModule.Clean"]
    assert application.AutomationSecurity == 3


def test_trusted_macro_rejects_a_different_workbook_hash(tmp_path: Path) -> None:
    source, workspace, output = _workbook(tmp_path, ".xlsm")
    engine = ExcelComEngine(FakeExcel)
    approval = TrustedMacroApproval(
        workbook_sha256="0" * 64,
        macro_name="ApprovedModule.Clean",
        explicitly_confirmed=True,
    )

    with pytest.raises(TrustedMacroError):
        engine.run_trusted_macro_to_copy(
            source,
            output / "macro-result.xlsm",
            approval,
            workspace_root=workspace,
            approved_output_root=output,
        )


def test_com_outputs_never_overwrite_existing_files(tmp_path: Path) -> None:
    source, workspace, output = _workbook(tmp_path)
    destination = output / "existing.xlsx"
    destination.write_bytes(b"existing")
    engine = ExcelComEngine(FakeExcel)

    with pytest.raises(OutputCollisionError):
        engine.recalculate_to_copy(
            source,
            destination,
            workspace_root=workspace,
            approved_output_root=output,
        )

    assert destination.read_bytes() == b"existing"
