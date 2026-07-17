"""Privacy-conscious file-analysis result models."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from sheetpilot.security.hashing import FileFingerprint


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WarningSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    BLOCKING = "blocking"


class FeatureWarning(ProfileModel):
    code: str
    severity: WarningSeverity
    message: str
    sheet: str | None = None


class ColumnProfile(ProfileModel):
    name: str
    inferred_types: tuple[str, ...]
    mixed_types: bool
    blank_percentage: float = Field(ge=0, le=100)
    unique_count: int = Field(ge=0)
    candidate_key: bool
    formula_injection_count: int = Field(ge=0)
    suspicious_email_count: int = Field(ge=0)
    suspicious_phone_count: int = Field(ge=0)
    invalid_date_count: int = Field(ge=0)
    inconsistent_category_count: int = Field(ge=0)
    possible_spelling_variation_count: int = Field(ge=0)


class SheetProfile(ProfileModel):
    name: str
    visibility: str
    used_rows: int = Field(ge=0)
    used_columns: int = Field(ge=0)
    headers: tuple[str, ...]
    duplicate_headers: tuple[str, ...]
    data_rows: int = Field(ge=0)
    blank_rows: int = Field(ge=0)
    duplicate_rows: int = Field(ge=0)
    formula_cells: int = Field(ge=0)
    formula_error_cells: int = Field(ge=0)
    merged_ranges: tuple[str, ...]
    protected: bool
    columns: tuple[ColumnProfile, ...]


class FileProfile(ProfileModel):
    source_path: Path
    file_name: str
    file_type: str
    size_bytes: int = Field(ge=0)
    fingerprint: FileFingerprint
    sheets: tuple[SheetProfile, ...]
    visible_sheets: tuple[str, ...]
    hidden_sheets: tuple[str, ...]
    named_ranges: tuple[str, ...]
    workbook_protected: bool
    macro_present: bool
    external_links_present: bool
    unsupported_features: tuple[str, ...]
    estimated_memory_bytes: int = Field(ge=0)
    warnings: tuple[FeatureWarning, ...]

    @property
    def warning_count(self) -> int:
        return len(self.warnings)
