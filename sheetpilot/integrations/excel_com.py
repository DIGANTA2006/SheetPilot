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
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from sheetpilot.app.config import SecurityLimits
from sheetpilot.core.atomic_output import AtomicOutputWriter
from sheetpilot.core.exceptions import (
    ExcelComOperationError,
    ExcelComUnavailableError,
    OutputCollisionError,
    OutputFailureError,
    PathSecurityError,
    TrustedMacroError,
)
from sheetpilot.engines.tabular_io import validate_output_file
from sheetpilot.security.archive_guard import ArchiveInspection, inspect_ooxml_archive
from sheetpilot.security.file_guard import validate_input_file
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

    def __init__(
        self,
        application_factory: Callable[[], Any] | None = None,
        *,
        limits: SecurityLimits | None = None,
    ) -> None:
        self._application_factory = application_factory
        self._limits = limits or SecurityLimits()

    @property
    def available(self) -> bool:
        """Probe whether a private Excel application instance can actually be created."""
        application: Any | None = None
        try:
            with self._com_apartment():
                application = self._create_application()
                try:
                    with suppress(BaseException):
                        application.Quit()
                finally:
                    application = None
        except ExcelComUnavailableError:
            return False
        return True

    @contextmanager
    def _com_apartment(self) -> Iterator[None]:
        """Initialize COM on the calling thread only for the real Windows adapter."""
        if self._application_factory is not None:
            yield
            return
        try:
            pythoncom = importlib.import_module("pythoncom")
            pythoncom.CoInitialize()
        except Exception as error:
            raise ExcelComUnavailableError(
                "The optional Windows COM runtime could not be initialized. "
                "Local spreadsheet processing remains usable."
            ) from error
        try:
            yield
        finally:
            with suppress(BaseException):
                pythoncom.CoUninitialize()

    def _create_application(self) -> Any:
        try:
            if self._application_factory is not None:
                application = self._application_factory()
            else:
                client = importlib.import_module("win32com.client")
                application = client.DispatchEx("Excel.Application")
            if application is None:
                raise RuntimeError("Excel application factory returned no application")
            return application
        except ExcelComUnavailableError:
            raise
        except Exception as error:
            raise ExcelComUnavailableError(
                "Microsoft Excel could not be started through its optional COM adapter. "
                "Local spreadsheet processing remains usable."
            ) from error

    def _working_copy(self, path: Path, workspace_root: Path) -> tuple[Path, ArchiveInspection]:
        resolved = ensure_within(path, workspace_root)
        if not resolved.is_file() or resolved.suffix.casefold() not in {".xlsx", ".xlsm"}:
            raise PathSecurityError("Excel automation requires an approved workbook working copy.")
        validate_input_file(resolved, self._limits)
        inspection = inspect_ooxml_archive(resolved, self._limits)
        if resolved.suffix.casefold() == ".xlsx" and inspection.macro_present:
            raise PathSecurityError("Macro content is not permitted inside an .xlsx working copy.")
        return resolved, inspection

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

    @staticmethod
    def _pdf_validator(path: Path) -> None:
        """Require a bounded PDF header and terminal marker before publication."""
        if not path.is_file() or path.stat().st_size < 12:
            raise ExcelComOperationError("Microsoft Excel produced an empty or truncated PDF.")
        with path.open("rb") as stream:
            header = stream.read(8)
            stream.seek(max(0, path.stat().st_size - 2048))
            trailer = stream.read(2048)
        if not header.startswith(b"%PDF-") or b"%%EOF" not in trailer:
            raise ExcelComOperationError("Microsoft Excel produced an invalid PDF artifact.")

    def _workbook_validator(
        self,
        path: Path,
        *,
        expected_suffix: str,
        source_had_macros: bool,
    ) -> None:
        if path.suffix.casefold() != expected_suffix:
            raise ExcelComOperationError("Excel changed the approved workbook output format.")
        validate_output_file(path, self._limits)
        inspection = inspect_ooxml_archive(path, self._limits)
        if expected_suffix == ".xlsx" and inspection.macro_present:
            raise ExcelComOperationError("Excel produced macro content in an .xlsx artifact.")
        if source_had_macros and not inspection.macro_present:
            raise ExcelComOperationError("Excel did not preserve the approved workbook's macros.")

    @staticmethod
    def _publish_artifact(
        destination: Path,
        approved_output_root: Path,
        *,
        producer: Callable[[Path], object],
        validator: Callable[[Path], object],
    ) -> ExcelComArtifact:
        """Stage beside the destination, validate, then publish without clobbering."""
        atomic_writer = AtomicOutputWriter(
            approved_output_root,
            approved_output_root / "SheetPilot Failed" / "Excel COM",
        )
        try:
            receipt = atomic_writer.write(
                destination,
                job_id=uuid4(),
                writer=producer,
                validator=validator,
            )
        except OutputFailureError as error:
            cause = error.__cause__
            if isinstance(
                cause,
                (ExcelComOperationError, ExcelComUnavailableError, TrustedMacroError),
            ):
                if cause.__cause__ is not None:
                    raise cause from cause.__cause__
                raise cause from error
            raise ExcelComOperationError(
                "Microsoft Excel produced an artifact that failed validation."
            ) from error
        return ExcelComArtifact(path=receipt.path, fingerprint=receipt.fingerprint)

    @contextmanager
    def _session(self, working_copy: Path) -> Iterator[tuple[Any, Any]]:
        with self._com_apartment(), self._opened_session(working_copy) as session:
            yield session

    @contextmanager
    def _opened_session(self, working_copy: Path) -> Iterator[tuple[Any, Any]]:
        application: Any | None = None
        workbook: Any | None = None
        primary_error: BaseException | None = None
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
        except BaseException as error:
            primary_error = error
            if isinstance(error, ExcelComUnavailableError):
                raise
            if not isinstance(error, Exception):
                raise
            if isinstance(error, ExcelComOperationError):
                raise
            raise ExcelComOperationError(
                "Microsoft Excel could not complete the approved action. "
                "No client source was changed."
            ) from error
        finally:
            cleanup_error: BaseException | None = None
            if application is not None:
                try:
                    application.AutomationSecurity = _FORCE_DISABLE_MACROS
                except BaseException as error:
                    cleanup_error = cleanup_error or error
            if workbook is not None:
                try:
                    workbook.Close(SaveChanges=False)
                except BaseException as error:
                    cleanup_error = cleanup_error or error
                finally:
                    workbook = None
            if application is not None:
                try:
                    application.Quit()
                except BaseException as error:
                    cleanup_error = cleanup_error or error
                finally:
                    application = None
            if primary_error is None and cleanup_error is not None:
                raise ExcelComOperationError(
                    "Microsoft Excel created the artifact but did not close cleanly; "
                    "the staged artifact was not published."
                ) from cleanup_error

    def recalculate_to_copy(
        self,
        working_copy: Path,
        destination: Path,
        *,
        workspace_root: Path,
        approved_output_root: Path,
    ) -> ExcelComArtifact:
        """Fully recalculate a working copy and save a distinct workbook copy."""
        source, inspection = self._working_copy(working_copy, workspace_root)
        output = self._destination(
            destination,
            approved_output_root,
            suffixes=frozenset({source.suffix.casefold()}),
        )

        def produce(stage: Path) -> None:
            with self._session(source) as (application, workbook):
                try:
                    application.CalculateFullRebuild()
                    workbook.SaveCopyAs(str(stage))
                finally:
                    workbook = None
                    del application

        return self._publish_artifact(
            output,
            approved_output_root.resolve(),
            producer=produce,
            validator=lambda path: self._workbook_validator(
                path,
                expected_suffix=source.suffix.casefold(),
                source_had_macros=inspection.macro_present,
            ),
        )

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

        source, inspection = self._working_copy(working_copy, workspace_root)
        output = self._destination(
            destination,
            approved_output_root,
            suffixes=frozenset({source.suffix.casefold()}),
        )

        def produce(stage: Path) -> None:
            with self._session(source) as (application, workbook):
                try:
                    for sheet_name, pivot_name in parsed:
                        worksheet: Any | None = None
                        pivot: Any | None = None
                        pivot_cache: Any | None = None
                        try:
                            worksheet = workbook.Worksheets(sheet_name)
                            pivot = worksheet.PivotTables(pivot_name)
                            pivot_cache = pivot.PivotCache()
                            pivot_cache.Refresh()
                        finally:
                            pivot_cache = None
                            pivot = None
                            worksheet = None
                    workbook.SaveCopyAs(str(stage))
                finally:
                    workbook = None
                    del application

        return self._publish_artifact(
            output,
            approved_output_root.resolve(),
            producer=produce,
            validator=lambda path: self._workbook_validator(
                path,
                expected_suffix=source.suffix.casefold(),
                source_had_macros=inspection.macro_present,
            ),
        )

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
        source, _ = self._working_copy(working_copy, workspace_root)
        output = self._destination(
            destination,
            approved_output_root,
            suffixes=frozenset({".pdf"}),
        )

        def produce(stage: Path) -> None:
            with self._session(source) as (application, workbook):
                worksheet: Any | None = None
                try:
                    worksheet = workbook.Worksheets(sheet_name)
                    worksheet.ExportAsFixedFormat(_PDF_FORMAT, str(stage))
                finally:
                    worksheet = None
                    workbook = None
                    del application

        return self._publish_artifact(
            output,
            approved_output_root.resolve(),
            producer=produce,
            validator=self._pdf_validator,
        )

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
        source, inspection = self._working_copy(working_copy, workspace_root)
        if source.suffix.casefold() != ".xlsm":
            raise TrustedMacroError(
                "Trusted macro execution requires a macro-enabled workbook copy."
            )
        if not inspection.macro_present:
            raise TrustedMacroError(
                "The approved .xlsm working copy does not contain a VBA project."
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

        def produce(stage: Path) -> None:
            with self._session(source) as (application, workbook):
                try:
                    escaped_workbook_name = str(workbook.Name).replace("'", "''")
                    qualified_macro = f"'{escaped_workbook_name}'!{approval.macro_name}"
                    application.AutomationSecurity = _ENABLE_MACROS_AFTER_OPEN
                    application.Run(qualified_macro)
                    application.AutomationSecurity = _FORCE_DISABLE_MACROS
                    workbook.SaveCopyAs(str(stage))
                finally:
                    workbook = None
                    application = None

        return self._publish_artifact(
            output,
            approved_output_root.resolve(),
            producer=produce,
            validator=lambda path: self._workbook_validator(
                path,
                expected_suffix=".xlsm",
                source_had_macros=True,
            ),
        )
