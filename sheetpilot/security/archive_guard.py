"""Bounded OOXML ZIP inspection performed before openpyxl is invoked."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict

from sheetpilot.app.config import SecurityLimits
from sheetpilot.core.exceptions import ArchiveSecurityError, CorruptWorkbookError, FileLimitError

_DRIVE_QUALIFIED = re.compile(r"^[A-Za-z]:")
_XML_DECLARATION_RISKS = (b"<!DOCTYPE", b"<!ENTITY")


class ArchiveInspection(BaseModel):
    """Feature inventory from an OOXML package without extracting its members."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    macro_present: bool
    external_links_present: bool
    unsupported_features: tuple[str, ...]
    member_count: int
    total_uncompressed_bytes: int


def _normalized_member(name: str) -> str:
    if "\\" in name or name.startswith(("/", "\\")) or _DRIVE_QUALIFIED.match(name):
        raise ArchiveSecurityError("The workbook contains an unsafe archive member name.")
    path = PurePosixPath(name)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ArchiveSecurityError("The workbook contains archive path traversal.")
    return path.as_posix().casefold()


def inspect_ooxml_archive(path: Path, limits: SecurityLimits) -> ArchiveInspection:
    """Validate ZIP structure, limits, XML declarations, and risky workbook parts."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > limits.max_archive_members:
                raise FileLimitError("The workbook contains too many archive members.")
            normalized: set[str] = set()
            total = 0
            names: set[str] = set()
            for member in members:
                safe_name = _normalized_member(member.filename)
                if safe_name in normalized:
                    raise ArchiveSecurityError(
                        "The workbook contains duplicate normalized archive members."
                    )
                normalized.add(safe_name)
                names.add(safe_name)
                if member.flag_bits & 0x1:
                    raise ArchiveSecurityError("Encrypted workbook archive members are blocked.")
                if member.file_size > limits.max_archive_member_bytes:
                    raise FileLimitError("A workbook archive member exceeds the size limit.")
                total += member.file_size
                if total > limits.max_archive_uncompressed_bytes:
                    raise FileLimitError("The workbook expands beyond the safe size limit.")
                if member.compress_size > 0:
                    ratio = member.file_size / member.compress_size
                    if ratio > limits.max_compression_ratio and member.file_size > 1024 * 1024:
                        raise FileLimitError(
                            "A workbook member has a suspicious compression ratio."
                        )
                if safe_name.endswith((".xml", ".rels")):
                    content = archive.read(member)
                    upper = content.upper()
                    if any(marker in upper for marker in _XML_DECLARATION_RISKS):
                        raise ArchiveSecurityError(
                            "DTD and entity declarations are not permitted in workbooks."
                        )
            required = {"[content_types].xml", "xl/workbook.xml"}
            if not required <= names:
                raise CorruptWorkbookError("The file is not a valid OOXML workbook.")
            bad_member = archive.testzip()
            if bad_member is not None:
                raise CorruptWorkbookError("The workbook archive failed its CRC check.")
    except zipfile.BadZipFile as error:
        raise CorruptWorkbookError("The workbook is not a valid ZIP package.") from error
    except OSError as error:
        raise CorruptWorkbookError("The workbook could not be read.") from error

    macro_present = any(name.endswith("vbaproject.bin") or "macrosheets/" in name for name in names)
    external_links_present = any(
        "externallinks/" in name or name.endswith("connections.xml") for name in names
    )
    unsupported: set[str] = set()
    feature_parts = {
        "active_x": "activex/",
        "embedded_objects": "embeddings/",
        "custom_ui": "customui/",
        "pivot_tables": "pivottables/",
        "slicers": "slicer",
        "data_connections": "connections.xml",
    }
    for feature, fragment in feature_parts.items():
        if any(fragment in name for name in names):
            unsupported.add(feature)
    return ArchiveInspection(
        macro_present=macro_present,
        external_links_present=external_links_present,
        unsupported_features=tuple(sorted(unsupported)),
        member_count=len(members),
        total_uncompressed_bytes=total,
    )
