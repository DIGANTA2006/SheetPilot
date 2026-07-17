"""Structured, privacy-filtered application logging."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sheetpilot.security.privacy import redact


class JsonFormatter(logging.Formatter):
    """Emit allowlisted structured fields without client data."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        fields = getattr(record, "safe_fields", None)
        if isinstance(fields, dict):
            payload["fields"] = redact(fields)
        if record.exc_info and record.exc_info[0] is not None:
            payload["error_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=True, sort_keys=True)


def configure_logging(log_dir: Path, *, verbose: bool = False) -> Path:
    """Configure a local JSON log for the current process."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "sheetpilot.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    return log_path
