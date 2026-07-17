"""Streaming file fingerprints and source-change verification."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from sheetpilot.core.exceptions import SourceChangedError


class FileFingerprint(BaseModel):
    """Immutable metadata recorded during source analysis."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)


def fingerprint_file(path: Path, *, chunk_size: int = 1024 * 1024) -> FileFingerprint:
    """Hash a file without loading it into memory, detecting concurrent mutation."""
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise SourceChangedError("The source changed while it was being read.")
    return FileFingerprint(sha256=digest.hexdigest(), size_bytes=after.st_size)


def verify_fingerprint(path: Path, expected: FileFingerprint) -> FileFingerprint:
    """Re-hash a source and fail closed when it differs from analysis."""
    actual = fingerprint_file(path)
    if actual != expected:
        raise SourceChangedError("The source changed after analysis; execution was stopped.")
    return actual
