"""Same-directory staging, validation, quarantine, and no-clobber publication."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from sheetpilot.core.exceptions import OutputCollisionError, OutputFailureError
from sheetpilot.core.no_clobber import publish_new_file
from sheetpilot.security.hashing import FileFingerprint, fingerprint_file
from sheetpilot.security.path_guard import ensure_within

Writer = Callable[[Path], object]
Validator = Callable[[Path], object]


class AtomicOutputReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    fingerprint: FileFingerprint
    byte_count: int
    committed_at: datetime


class AtomicOutputWriter:
    """Publish a validated file only once and preserve malformed stages separately."""

    def __init__(self, approved_root: Path, failed_root: Path) -> None:
        self.approved_root = approved_root.resolve()
        self.failed_root = failed_root.resolve()
        self.approved_root.mkdir(parents=True, exist_ok=True)
        self.failed_root.mkdir(parents=True, exist_ok=True)

    def _quarantine(self, stage: Path, job_id: UUID) -> Path | None:
        if not stage.exists():
            return None
        job_root = ensure_within(self.failed_root / str(job_id), self.failed_root)
        job_root.mkdir(parents=True, exist_ok=True)
        destination = ensure_within(
            job_root / f"{stage.stem}.{uuid4().hex}.failed{stage.suffix}", self.failed_root
        )
        stage.replace(destination)
        return destination

    def quarantine_committed(self, path: Path, job_id: UUID) -> Path | None:
        """Remove an uncompleted published file from results and retain it as failed."""
        guarded = ensure_within(path, self.approved_root)
        return self._quarantine(guarded, job_id)

    def write(
        self,
        destination: Path,
        *,
        job_id: UUID,
        writer: Writer,
        validator: Validator,
        pre_commit: Callable[[], object] | None = None,
    ) -> AtomicOutputReceipt:
        final = ensure_within(destination, self.approved_root)
        if final.exists():
            raise OutputCollisionError("The final output already exists.")
        final.parent.mkdir(parents=True, exist_ok=True)
        stage = ensure_within(
            final.parent / f".{final.stem}.{uuid4().hex}.partial{final.suffix}",
            self.approved_root,
        )
        try:
            writer(stage)
            if not stage.is_file():
                raise OutputFailureError("The output writer did not produce a file.")
            with stage.open("r+b") as stream:
                stream.flush()
                os.fsync(stream.fileno())
            validator(stage)
            fingerprint = fingerprint_file(stage)
            if pre_commit is not None:
                pre_commit()
            try:
                publish_new_file(stage, final)
            except FileExistsError as error:
                raise OutputCollisionError(
                    "Another process created the output destination."
                ) from error
            except OSError as error:
                raise OutputFailureError(
                    "The filesystem could not safely publish the validated output."
                ) from error
            return AtomicOutputReceipt(
                path=final,
                fingerprint=fingerprint,
                byte_count=fingerprint.size_bytes,
                committed_at=datetime.now(UTC),
            )
        except (OutputCollisionError, OutputFailureError):
            self._quarantine(stage, job_id)
            raise
        except BaseException as error:
            self._quarantine(stage, job_id)
            raise OutputFailureError("Output failed validation and was not published.") from error
