"""Non-destructive file analysis orchestrator."""

from __future__ import annotations

from pathlib import Path

from sheetpilot.app.config import SecurityLimits
from sheetpilot.core.profile_models import (
    FeatureWarning,
    FileProfile,
    SheetProfile,
    WarningSeverity,
)
from sheetpilot.engines.openpyxl_engine import profile_workbook
from sheetpilot.engines.polars_engine import profile_csv
from sheetpilot.security.archive_guard import ArchiveInspection, inspect_ooxml_archive
from sheetpilot.security.file_guard import validate_input_file
from sheetpilot.security.hashing import fingerprint_file, verify_fingerprint


def _sheet_warnings(sheet: SheetProfile) -> list[FeatureWarning]:
    warnings: list[FeatureWarning] = []
    if sheet.visibility != "visible":
        warnings.append(
            FeatureWarning(
                code="hidden_sheet",
                severity=WarningSeverity.WARNING,
                message="The workbook contains a hidden sheet.",
                sheet=sheet.name,
            )
        )
    if sheet.protected:
        warnings.append(
            FeatureWarning(
                code="protected_sheet",
                severity=WarningSeverity.WARNING,
                message="The worksheet is protected; edits may not preserve its intent.",
                sheet=sheet.name,
            )
        )
    if sheet.duplicate_headers:
        warnings.append(
            FeatureWarning(
                code="duplicate_headers",
                severity=WarningSeverity.HIGH,
                message="Duplicate headers must be resolved before deterministic execution.",
                sheet=sheet.name,
            )
        )
    if sheet.formula_cells:
        warnings.append(
            FeatureWarning(
                code="formulas_present",
                severity=WarningSeverity.INFO,
                message="Formula cells are present and will not be executed during analysis.",
                sheet=sheet.name,
            )
        )
    if sheet.formula_error_cells:
        warnings.append(
            FeatureWarning(
                code="formula_errors",
                severity=WarningSeverity.HIGH,
                message="Spreadsheet error cells were detected.",
                sheet=sheet.name,
            )
        )
    if sheet.merged_ranges:
        warnings.append(
            FeatureWarning(
                code="merged_cells",
                severity=WarningSeverity.WARNING,
                message="Merged cells may restrict safe table operations.",
                sheet=sheet.name,
            )
        )
    if any(column.mixed_types for column in sheet.columns):
        warnings.append(
            FeatureWarning(
                code="mixed_column_types",
                severity=WarningSeverity.WARNING,
                message="One or more columns contain mixed inferred data types.",
                sheet=sheet.name,
            )
        )
    if sum(column.formula_injection_count for column in sheet.columns):
        warnings.append(
            FeatureWarning(
                code="formula_injection",
                severity=WarningSeverity.HIGH,
                message="Potential formula-injection text was detected.",
                sheet=sheet.name,
            )
        )
    if sum(
        column.suspicious_email_count + column.suspicious_phone_count + column.invalid_date_count
        for column in sheet.columns
    ):
        warnings.append(
            FeatureWarning(
                code="invalid_contact_or_date_values",
                severity=WarningSeverity.WARNING,
                message="Potentially invalid contact or date values require review.",
                sheet=sheet.name,
            )
        )
    if sum(
        column.inconsistent_category_count + column.possible_spelling_variation_count
        for column in sheet.columns
    ):
        warnings.append(
            FeatureWarning(
                code="category_variations",
                severity=WarningSeverity.INFO,
                message="Possible category or spelling variations were detected.",
                sheet=sheet.name,
            )
        )
    return warnings


class FileProfiler:
    """Profile supported inputs while proving the source hash remains unchanged."""

    def __init__(self, limits: SecurityLimits | None = None) -> None:
        self.limits = limits or SecurityLimits()

    def profile(self, path: Path) -> FileProfile:
        source = path.resolve()
        fingerprint = fingerprint_file(source)
        file_type = validate_input_file(source, self.limits)
        archive: ArchiveInspection | None = None
        named_ranges: tuple[str, ...] = ()
        workbook_protected = False
        macro_present = False
        external_links_present = False
        unsupported_features: tuple[str, ...] = ()
        sheets: tuple[SheetProfile, ...]
        if file_type == "csv":
            sheet, estimated_memory = profile_csv(source, self.limits)
            sheets = (sheet,)
        else:
            archive = inspect_ooxml_archive(source, self.limits)
            workbook = profile_workbook(source, self.limits)
            sheets = workbook.sheets
            named_ranges = workbook.named_ranges
            workbook_protected = workbook.workbook_protected
            macro_present = archive.macro_present
            external_links_present = (
                archive.external_links_present or workbook.external_formula_present
            )
            unsupported_features = archive.unsupported_features
            estimated_memory = workbook.estimated_memory_bytes
        verify_fingerprint(source, fingerprint)

        warnings: list[FeatureWarning] = []
        for sheet in sheets:
            warnings.extend(_sheet_warnings(sheet))
        if macro_present:
            warnings.append(
                FeatureWarning(
                    code="macros_present",
                    severity=WarningSeverity.HIGH,
                    message="Macros were detected and will never run automatically.",
                )
            )
        if external_links_present:
            warnings.append(
                FeatureWarning(
                    code="external_links",
                    severity=WarningSeverity.HIGH,
                    message="External workbook links were detected and will not be refreshed.",
                )
            )
        if workbook_protected:
            warnings.append(
                FeatureWarning(
                    code="protected_workbook",
                    severity=WarningSeverity.WARNING,
                    message="Workbook structure protection is enabled.",
                )
            )
        for feature in unsupported_features:
            warnings.append(
                FeatureWarning(
                    code=f"unsupported_{feature}",
                    severity=WarningSeverity.WARNING,
                    message="An advanced workbook feature may not be preserved by all operations.",
                )
            )
        visible = tuple(sheet.name for sheet in sheets if sheet.visibility == "visible")
        hidden = tuple(sheet.name for sheet in sheets if sheet.visibility != "visible")
        return FileProfile(
            source_path=source,
            file_name=source.name,
            file_type=file_type,
            size_bytes=fingerprint.size_bytes,
            fingerprint=fingerprint,
            sheets=sheets,
            visible_sheets=visible,
            hidden_sheets=hidden,
            named_ranges=named_ranges,
            workbook_protected=workbook_protected,
            macro_present=macro_present,
            external_links_present=external_links_present,
            unsupported_features=unsupported_features,
            estimated_memory_bytes=estimated_memory,
            warnings=tuple(warnings),
        )
