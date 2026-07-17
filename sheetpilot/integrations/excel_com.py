"""Fail-closed, optional Microsoft Excel COM automation.

The integration is deliberately isolated from deterministic spreadsheet operations.
It accepts only application-defined actions and only opens job-owned working copies.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sheetpilot.core.exceptions import (
    ExcelComOperationError,
    ExcelComUnavailableError,
    OutputCollisionError,
    PathSecurityError,
    TrustedMacroError,
)
from sheetpilot.security.hashing import FileFingerprint, fingerprint_file
from sheetpilot.security.path_guard import ensure_within

_FORCE_DISABLE_MACROS = 3
_ENABLE_MACROS_AFTER_OPEN = 1
_PDF_FORMAT = 0


class TrustedMacroApproval(BaseModel):
    """Narrow approval for one named macro in one exact workbook copy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workbook_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    macro_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_.]*$",
    )
    explicitly_confirmed: bool


class ExcelComArtifact(BaseModel):
    """Fingerprint of an artifact created by an approved Excel action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    fingerprint: FileFingerprint


class ExcelComEngine:
    """Execute a small allow-list of Excel actions against working copies only."""

    def __init__(self, application_factory: Callable[[], Any] | None = None) -> None:
        self._application_factory = application_factory

    @property
    def available(self) -> bool:
        """Report whether the optional automation dependency can be loaded."""
        if self._application_factory is not None:
            return True
        try:
            importlib.import_module("win32com.client")
        except (ImportError, OSError):
            return False
        return True

    def _create_application(self) -> Any:
        try:
            if self._application_factory is not None:
                return self._application_factory()
            client = importlib.import_module("win32com.client")
            return client.DispatchEx("Excel.Application")
        except (ImportError, OSError) as error:
            raise ExcelComUnavailableError(
                "Microsoft Excel automation is unavailable. "
                "Local spreadsheet processing remains usable."
            ) from error

    @staticmethod
    def _working_copy(path: Path, workspace_root: Path) -> Path:
        resolved = ensure_within(path, workspace_root)
        if not resolved.is_file() or resolved.suffix.casefold() not in {".xlsx", ".xlsm"}:
            raise PathSecurityError("Excel automation requires an approved workbook working copy.")
        return resolved

    @staticmethod
    def _destination(
        path: Path,
        approved_output_root: Path,
        *,
        suffixes: frozenset[str],
    ) -> Path:
        resolved = ensure_within(path, approved_output_root)
        if resolved.suffix.casefold() not in suffixes:
            raise PathSecurityError("The requested Excel automation output format is not approved.")
        if resolved.exists():
            raise OutputCollisionError("Excel automation cannot overwrite an existing output.")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved

    @contextmanager
    def _session(self, working_copy: Path) -> Iterator[tuple[Any, Any]]:
        application: Any | None = None
        workbook: Any | None = None
        try:
            application = self._create_application()
            application.Visible = False
            application.DisplayAlerts = False
            application.EnableEvents = False
            application.AutomationSecurity = _FORCE_DISABLE_MACROS
            workbook = application.Workbooks.Open(
                str(working_copy),
                UpdateLinks=0,
                ReadOnly=False,
                IgnoreReadOnlyRecommended=True,
                AddToMru=False,
            )
            yield application, workbook
        except ExcelComUnavailableError:
            raise
        except BaseException as error:
            raise ExcelComOperationError(
                "Microsoft Excel could not complete the approved action. "
                "No client source was changed."
            ) from error
        finally:
            if application is not None:
                application.AutomationSecurity = _FORCE_DISABLE_MACROS
            if workbook is not None:
                with suppress(BaseException):
                    workbook.Close(SaveChanges=False)
            if application is not None:
                with suppress(BaseException):
                    application.Quit()

    @staticmethod
    def _artifact(destination: Path) -> ExcelComArtifact:
        if not destination.is_file():
            raise ExcelComOperationError("Microsoft Excel did not create the requested artifact.")
        return ExcelComArtifact(path=destination, fingerprint=fingerprint_file(destination))

    def recalculate_to_copy(
        self,
        working_copy: Path,
        destination: Path,
        *,
        workspace_root: Path,
        approved_output_root: Path,
    ) -> ExcelComArtifact:
        """Fully recalculate a working copy and save a distinct workbook copy."""
        source = self._working_copy(working_copy, workspace_root)
        output = self._destination(
            destination,
            approved_output_root,
            suffixes=frozenset({source.suffix.casefold()}),
        )
        try:
            with self._session(source) as (application, workbook):
                application.CalculateFullRebuild()
                workbook.SaveCopyAs(str(output))
            return self._artifact(output)
        except BaseException:
            output.unlink(missing_ok=True)
            raise

    def refresh_approved_pivots_to_copy(
        self,
        working_copy: Path,
        destination: Path,
        approved_pivots: tuple[str, ...],
        *,
        workspace_root: Path,
        approved_output_root: Path,
    ) -> ExcelComArtifact:
        """Refresh only explicitly named ``Sheet!Pivot`` tables, never all connections."""
        if not approved_pivots:
            raise ExcelComOperationError("At least one pivot table must be explicitly approved.")
        if len(approved_pivots) != len(set(approved_pivots)):
            raise ExcelComOperationError("Approved pivot names must be unique.")
        parsed: list[tuple[str, str]] = []
        for qualified_name in approved_pivots:
            parts = qualified_name.split("!", maxsplit=1)
            if len(parts) != 2 or not all(part.strip() for part in parts):
                raise ExcelComOperationError("Pivot approvals must use the form Sheet!Pivot.")
            parsed.append((parts[0].strip(), parts[1].strip()))

        source = self._working_copy(working_copy, workspace_root)
        output = self._destination(
            destination,
            approved_output_root,
            suffixes=frozenset({source.suffix.casefold()}),
        )
        try:
            with self._session(source) as (_, workbook):
                for sheet_name, pivot_name in parsed:
                    worksheet = workbook.Worksheets(sheet_name)
                    pivot = worksheet.PivotTables(pivot_name)
                    pivot.PivotCache().Refresh()
                workbook.SaveCopyAs(str(output))
            return self._artifact(output)
        except BaseException:
            output.unlink(missing_ok=True)
            raise

    def export_sheet_pdf(
        self,
        working_copy: Path,
        destination: Path,
        sheet_name: str,
        *,
        workspace_root: Path,
        approved_output_root: Path,
    ) -> ExcelComArtifact:
        """Export one explicitly selected sheet to a new PDF."""
        if not sheet_name.strip():
            raise ExcelComOperationError("A sheet name is required for PDF export.")
        source = self._working_copy(working_copy, workspace_root)
        output = self._destination(
            destination,
            approved_output_root,
            suffixes=frozenset({".pdf"}),
        )
        try:
            with self._session(source) as (_, workbook):
                worksheet = workbook.Worksheets(sheet_name)
                worksheet.ExportAsFixedFormat(_PDF_FORMAT, str(output))
            return self._artifact(output)
        except BaseException:
            output.unlink(missing_ok=True)
            raise

    def run_trusted_macro_to_copy(
        self,
        working_copy: Path,
        destination: Path,
        approval: TrustedMacroApproval,
        *,
        workspace_root: Path,
        approved_output_root: Path,
    ) -> ExcelComArtifact:
        """Run one hash-bound, named macro after opening with automatic macros disabled."""
        source = self._working_copy(working_copy, workspace_root)
        if source.suffix.casefold() != ".xlsm":
            raise TrustedMacroError(
                "Trusted macro execution requires a macro-enabled workbook copy."
            )
        fingerprint = fingerprint_file(source)
        if not approval.explicitly_confirmed or fingerprint.sha256 != approval.workbook_sha256:
            raise TrustedMacroError(
                "Trusted macro approval is missing or does not match this exact workbook."
            )
        output = self._destination(
            destination,
            approved_output_root,
            suffixes=frozenset({".xlsm"}),
        )
        try:
            with self._session(source) as (application, workbook):
                qualified_macro = f"'{workbook.Name}'!{approval.macro_name}"
                application.AutomationSecurity = _ENABLE_MACROS_AFTER_OPEN
                try:
                    application.Run(qualified_macro)
                finally:
                    application.AutomationSecurity = _FORCE_DISABLE_MACROS
                workbook.SaveCopyAs(str(output))
            return self._artifact(output)
        except BaseException:
            output.unlink(missing_ok=True)
            raise
