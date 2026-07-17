"""Verified, non-destructive backup and restore operations."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from sheetpilot.app.version import __version__
from sheetpilot.core.exceptions import BackupError, OutputCollisionError
from sheetpilot.security.hashing import FileFingerprint, fingerprint_file, verify_fingerprint
from sheetpilot.security.path_guard import ensure_within, sanitize_filename


class BackupReceipt(BaseModel):
    """Immutable, serializable evidence for a verified backup."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    source_name: str
    source_fingerprint: FileFingerprint
    backup_path: Path
    manifest_path: Path
    created_at: datetime
    application_version: str


class BackupService:
    """Publish only hash-verified backups and never consume them during restore."""

    def __init__(self, backup_root: Path) -> None:
        self.backup_root = backup_root.resolve()
        self.backup_root.mkdir(parents=True, exist_ok=True)

    def create_backup(
        self,
        source: Path,
        *,
        job_id: UUID,
        expected: FileFingerprint | None = None,
    ) -> BackupReceipt:
        source_fingerprint = (
            verify_fingerprint(source, expected) if expected else fingerprint_file(source)
        )
        timestamp = datetime.now(UTC)
        job_dir = ensure_within(self.backup_root / str(job_id), self.backup_root)
        job_dir.mkdir(parents=True, exist_ok=True)
        name = sanitize_filename(source.name)
        stamp = timestamp.strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = ensure_within(job_dir / f"{stamp}_{name}", self.backup_root)
        partial_path = backup_path.with_suffix(f"{backup_path.suffix}.partial")
        manifest_path = backup_path.with_suffix(f"{backup_path.suffix}.manifest.json")
        if backup_path.exists() or partial_path.exists() or manifest_path.exists():
            raise OutputCollisionError("A backup path unexpectedly already exists.")
        try:
            shutil.copy2(source, partial_path)
            verify_fingerprint(partial_path, source_fingerprint)
            partial_path.replace(backup_path)
            receipt = BackupReceipt(
                job_id=job_id,
                source_name=source.name,
                source_fingerprint=source_fingerprint,
                backup_path=backup_path,
                manifest_path=manifest_path,
                created_at=timestamp,
                application_version=__version__,
            )
            manifest_partial = manifest_path.with_suffix(".partial")
            manifest_partial.write_text(receipt.model_dump_json(indent=2), encoding="utf-8")
            manifest_partial.replace(manifest_path)
            return receipt
        except OSError as error:
            partial_path.unlink(missing_ok=True)
            raise BackupError("The source could not be backed up safely.") from error

    def load_receipt(self, manifest_path: Path) -> BackupReceipt:
        guarded = ensure_within(manifest_path, self.backup_root)
        try:
            return BackupReceipt.model_validate_json(guarded.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise BackupError("The backup manifest is invalid.") from error

    def restore_to_new_file(self, receipt: BackupReceipt, destination: Path) -> Path:
        if destination.exists():
            raise OutputCollisionError("Restore never overwrites an existing file.")
        verify_fingerprint(receipt.backup_path, receipt.source_fingerprint)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(f"{destination.suffix}.restore.partial")
        try:
            shutil.copy2(receipt.backup_path, partial)
            verify_fingerprint(partial, receipt.source_fingerprint)
            partial.replace(destination)
            return destination
        except OSError as error:
            partial.unlink(missing_ok=True)
            raise BackupError("The backup could not be restored safely.") from error
