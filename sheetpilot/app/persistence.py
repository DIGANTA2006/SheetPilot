"""Composition bundle for local, privacy-conscious persistence services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.storage.database import Database
from sheetpilot.storage.models import SettingKey, utc_now
from sheetpilot.storage.repositories import (
    JobHistoryRepository,
    SettingsRepository,
    ValidationSummaryRepository,
    WorkflowTemplateRepository,
)
from sheetpilot.storage.services import JobHistoryService, WorkflowTemplateService


@dataclass(frozen=True, slots=True)
class PersistenceServices:
    """Repositories and domain services sharing one initialized SQLite database."""

    templates: WorkflowTemplateRepository
    history: JobHistoryRepository
    validations: ValidationSummaryRepository
    settings: SettingsRepository
    workflows: WorkflowTemplateService
    jobs: JobHistoryService

    def prune_expired_history(self, *, now: datetime | None = None) -> int:
        """Apply the configured terminal-history retention policy."""
        configured = self.settings.get(SettingKey.HISTORY_RETENTION_DAYS)
        retention_days = configured if type(configured) is int else 365
        current = now or utc_now()
        if current.tzinfo is None:
            raise ValueError("history maintenance requires a timezone-aware timestamp")
        return self.history.prune_completed_before(current - timedelta(days=retention_days))

    @classmethod
    def build(
        cls,
        database: Database,
        registry: OperationRegistry,
    ) -> PersistenceServices:
        """Build one coherent service graph without global mutable state."""
        templates = WorkflowTemplateRepository(database)
        history = JobHistoryRepository(database)
        validations = ValidationSummaryRepository(database)
        settings = SettingsRepository(database)
        return cls(
            templates=templates,
            history=history,
            validations=validations,
            settings=settings,
            workflows=WorkflowTemplateService(registry, templates, history),
            jobs=JobHistoryService(history, validations),
        )
