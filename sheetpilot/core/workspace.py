"""Job-scoped isolated workspaces and verified source copies."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from types import TracebackType
from uuid import UUID, uuid4

from sheetpilot.security.hashing import FileFingerprint, verify_fingerprint
from sheetpilot.security.path_guard import ensure_within, sanitize_filename


class IsolatedWorkspace:
    """Own temporary copies so engines never receive client source paths."""

    def __init__(self, temp_root: Path, job_id: UUID) -> None:
        self.temp_root = temp_root.resolve()
        self.job_id = job_id
        self.path: Path | None = None
        self.input_dir: Path | None = None
        self.work_dir: Path | None = None
        self._temporary: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> IsolatedWorkspace:
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix=f"sheetpilot-{self.job_id}-", dir=self.temp_root
        )
        self.path = ensure_within(Path(self._temporary.name), self.temp_root)
        self.input_dir = self.path / "input"
        self.work_dir = self.path / "work"
        self.input_dir.mkdir()
        self.work_dir.mkdir()
        (self.path / ".owner").write_text(str(uuid4()), encoding="ascii")
        return self

    def copy_source(self, source: Path, expected: FileFingerprint) -> Path:
        if self.input_dir is None or self.work_dir is None:
            raise RuntimeError("Workspace is not active.")
        verify_fingerprint(source, expected)
        name = sanitize_filename(source.name)
        immutable_copy = ensure_within(self.input_dir / name, self.input_dir)
        working_copy = ensure_within(self.work_dir / name, self.work_dir)
        shutil.copy2(source, immutable_copy)
        verify_fingerprint(immutable_copy, expected)
        shutil.copy2(immutable_copy, working_copy)
        verify_fingerprint(working_copy, expected)
        return working_copy

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        if self._temporary is not None:
            self._temporary.cleanup()
        self.path = None
        self.input_dir = None
        self.work_dir = None
