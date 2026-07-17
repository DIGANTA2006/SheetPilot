"""Composition root for local application services."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QApplication

from sheetpilot.app.config import AppConfig
from sheetpilot.app.logging_config import configure_logging
from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.storage.database import Database
from sheetpilot.ui.main_window import MainWindow


@dataclass(frozen=True)
class ApplicationContext:
    config: AppConfig
    database: Database
    registry: OperationRegistry


def build_context(config: AppConfig | None = None) -> ApplicationContext:
    """Initialize local-only services without starting external integrations."""
    resolved = config or AppConfig.default()
    resolved.ensure_directories()
    configure_logging(resolved.log_dir)
    database = Database(resolved.database_path)
    database.initialize()
    return ApplicationContext(
        config=resolved,
        database=database,
        registry=OperationRegistry(),
    )


def build_main_window(application: QApplication, context: ApplicationContext) -> MainWindow:
    """Construct the real main window from an initialized context."""
    del application, context
    return MainWindow()
