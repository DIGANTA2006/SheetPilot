from __future__ import annotations

import json
import logging
from pathlib import Path

from pytest import MonkeyPatch

from sheetpilot.app.config import AppConfig
from sheetpilot.app.logging_config import JsonFormatter
from sheetpilot.security.privacy import REDACTED, redact
from sheetpilot.storage.database import Database


def test_database_migrations_are_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "metadata.sqlite3")
    database.initialize()
    database.initialize()
    assert {
        "schema_migrations",
        "jobs",
        "workflow_templates",
        "settings",
        "validation_summaries",
    } <= database.table_names()


def test_default_config_honours_isolated_data_directory(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    isolated = tmp_path / "isolated app data"
    monkeypatch.setenv("SHEETPILOT_DATA_DIR", str(isolated))

    config = AppConfig.default()

    assert config.data_dir == isolated.resolve()
    assert config.database_path == isolated.resolve() / "sheetpilot.sqlite3"
    assert config.backup_dir == isolated.resolve() / "backups"


def test_recursive_redaction() -> None:
    value = {
        "api_key": "sk-secretsecretsecret",
        "nested": {"email": "person@example.com", "phone": "+91 98765 43210"},
    }
    redacted = redact(value)
    assert redacted["api_key"] == REDACTED
    assert redacted["nested"]["email"] == REDACTED
    assert redacted["nested"]["phone"] == REDACTED


def test_json_formatter_does_not_emit_sensitive_values() -> None:
    record = logging.LogRecord(
        name="sheetpilot.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Contact person@example.com",
        args=(),
        exc_info=None,
    )
    record.safe_fields = {"password": "super-secret", "job_id": "job-1"}
    payload = json.loads(JsonFormatter().format(record))
    serialized = json.dumps(payload)
    assert "person@example.com" not in serialized
    assert "super-secret" not in serialized
    assert payload["fields"]["job_id"] == "job-1"
