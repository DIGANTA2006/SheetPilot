"""Input-format, magic-byte, and resource-limit checks."""

from __future__ import annotations

from pathlib import Path

from sheetpilot.app.config import SecurityLimits
from sheetpilot.core.exceptions import (
    FileLimitError,
    PasswordProtectedWorkbookError,
    UnsupportedFormatError,
)

SUPPORTED_SUFFIXES = frozenset({".csv", ".xlsx", ".xlsm"})
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


def validate_input_file(path: Path, limits: SecurityLimits) -> str:
    """Validate an input before a parser sees it and return its normalized type."""
    if not path.is_file():
        raise UnsupportedFormatError("The selected source is not a readable file.")
    suffix = path.suffix.casefold()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedFormatError("Supported inputs are .xlsx, .xlsm, and .csv.")
    size = path.stat().st_size
    if size > limits.max_file_bytes:
        raise FileLimitError("The source file exceeds the configured size limit.")
    if suffix in {".xlsx", ".xlsm"}:
        with path.open("rb") as stream:
            magic = stream.read(8)
        if magic.startswith(_OLE_MAGIC):
            raise PasswordProtectedWorkbookError(
                "The workbook is encrypted or uses an unsupported binary format."
            )
        if not magic.startswith(_ZIP_MAGIC):
            raise UnsupportedFormatError("The workbook extension does not match its content.")
    return suffix.removeprefix(".")
