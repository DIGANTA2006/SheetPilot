"""Verified, non-destructive backup and restore operations."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from sheetpilot.app.version import __version__
from sheetpilot.core.exceptions import BackupError, OutputCollisionError, SheetPilotError
from sheetpilot.core.no_clobber import publish_new_file
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
            publish_new_file(partial_path, backup_path)
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
            publish_new_file(manifest_partial, manifest_path)
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

    def list_verified_receipts(self, job_id: UUID) -> tuple[BackupReceipt, ...]:
        """Load only intact receipts and backups contained in one trusted job directory."""
        job_dir = ensure_within(self.backup_root / str(job_id), self.backup_root)
        if not job_dir.is_dir():
            return ()
        receipts: list[BackupReceipt] = []
        for manifest_path in sorted(job_dir.glob("*.manifest.json")):
            try:
                receipt = self.load_receipt(manifest_path)
                guarded_backup = ensure_within(receipt.backup_path, job_dir)
                guarded_manifest = ensure_within(receipt.manifest_path, job_dir)
                expected_manifest = guarded_backup.with_suffix(
                    f"{guarded_backup.suffix}.manifest.json"
                )
                if (
                    receipt.job_id != job_id
                    or guarded_manifest != manifest_path.resolve()
                    or guarded_manifest != expected_manifest
                ):
                    raise BackupError("The backup manifest does not match its job or file.")
                verify_fingerprint(guarded_backup, receipt.source_fingerprint)
            except (OSError, SheetPilotError):
                continue
            receipts.append(receipt)
        return tuple(receipts)

    def restore_to_new_file(self, receipt: BackupReceipt, destination: Path) -> Path:
        if destination.exists():
            raise OutputCollisionError("Restore never overwrites an existing file.")
        verify_fingerprint(receipt.backup_path, receipt.source_fingerprint)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.parent / f".{destination.name}.{uuid4().hex}.restore.partial"
        try:
            shutil.copy2(receipt.backup_path, partial)
            verify_fingerprint(partial, receipt.source_fingerprint)
            publish_new_file(partial, destination)
            return destination
        except OSError as error:
            partial.unlink(missing_ok=True)
            raise BackupError("The backup could not be restored safely.") from error
