"""Validated application configuration."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class SecurityLimits(BaseModel):
    """Conservative, centrally configurable untrusted-input limits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_file_bytes: int = Field(default=250 * 1024 * 1024, gt=0)
    max_rows: int = Field(default=2_000_000, gt=0)
    max_columns: int = Field(default=16_384, gt=0)
    max_archive_members: int = Field(default=10_000, gt=0)
    max_archive_member_bytes: int = Field(default=256 * 1024 * 1024, gt=0)
    max_archive_uncompressed_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)
    max_compression_ratio: float = Field(default=200.0, gt=1)


class AppConfig(BaseModel):
    """Runtime paths and safety settings for one application context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data_dir: Path
    backup_dir: Path
    temp_dir: Path
    database_path: Path
    log_dir: Path
    limits: SecurityLimits = Field(default_factory=SecurityLimits)

    @classmethod
    def default(cls) -> AppConfig:
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) / "SheetPilot" if local else Path.home() / ".sheetpilot"
        return cls(
            data_dir=base,
            backup_dir=base / "backups",
            temp_dir=base / "temp",
            database_path=base / "sheetpilot.sqlite3",
            log_dir=base / "logs",
        )

    def ensure_directories(self) -> None:
        """Create only application-owned runtime directories."""
        for directory in (self.data_dir, self.backup_dir, self.temp_dir, self.log_dir):
            directory.mkdir(parents=True, exist_ok=True)
