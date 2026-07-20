"""Main application window and end-to-end job workflow shell."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from sheetpilot.app.config import AppConfig
from sheetpilot.app.persistence import PersistenceServices
from sheetpilot.app.version import __version__
from sheetpilot.core.exceptions import SheetPilotError
from sheetpilot.core.file_profiler import FileProfiler
from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.core.plan_schema import OperationPlan
from sheetpilot.core.plan_validator import PlanValidator
from sheetpilot.core.preview_engine import ExecutionApproval
from sheetpilot.operations.registry import build_default_registry
from sheetpilot.storage.database import Database
from sheetpilot.storage.models import SettingKey, WorkflowTemplate
from sheetpilot.ui.pages.analysis_page import AnalysisPage
from sheetpilot.ui.pages.persistence_pages import (
    HelpPage,
    JobHistoryPage,
    SettingsPage,
    ValidationReportsPage,
    WorkflowLibraryPage,
)
from sheetpilot.ui.pages.plan_page import PlanPage
from sheetpilot.ui.pages.preview_page import PreviewPage
from sheetpilot.ui.pages.results_page import ResultsPage
from sheetpilot.ui.theme import LIGHT_STYLESHEET
from sheetpilot.ui.workflow_models import (
    JobDraft,
    PreparedJob,
    WorkflowRunRequest,
    prepare_job,
)

_LOGGER = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Coordinate protected jobs and privacy-conscious local persistence screens."""

    _WORKFLOW_LABELS = (
        "New job & analysis",
        "Plan review",
        "Preview & approval",
        "Results & reconciliation",
    )
    _NAVIGATION = (
        "New Job",
        "Saved Workflows",
        "Job History",
        "Validation Reports",
        "Templates",
        "Settings",
        "Help",
    )

    def __init__(
        self,
        profiler: FileProfiler | None = None,
        *,
        config: AppConfig | None = None,
        registry: OperationRegistry | None = None,
        thread_pool: QThreadPool | None = None,
        persistence: PersistenceServices | None = None,
    ) -> None:
        super().__init__()
        self._config = config or AppConfig.default()
        self._config.ensure_directories()
        self._registry = registry or build_default_registry()
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        if persistence is None:
            database = Database(self._config.database_path)
            database.initialize()
            persistence = PersistenceServices.build(database, self._registry)
        self._persistence = persistence
        try:
            self._persistence.prune_expired_history()
        except (SheetPilotError, ValueError):
            _LOGGER.warning("Configured history maintenance failed.", exc_info=True)
        self._prepared: PreparedJob | None = None
        self._plan: OperationPlan | None = None
        self._pending_template: WorkflowTemplate | None = None
        self._pending_parameters: dict[str, Any] = {}
        self._pending_workflow_id: UUID | None = None
        self._active_template: WorkflowTemplate | None = None
        self._active_workflow_id: UUID | None = None

        self.setObjectName("mainWindow")
        self.setWindowTitle(f"SheetPilot {__version__}")
        self.setMinimumSize(980, 650)
        self.resize(1280, 800)
        self.setStyleSheet(LIGHT_STYLESHEET)

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(225)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(18, 24, 18, 24)
        product = QLabel("SheetPilot")
        product.setObjectName("productName")
        side_layout.addWidget(product)
        side_layout.addWidget(QLabel("Safe spreadsheet workflows"))
        side_layout.addSpacing(14)
        self.workflow_stage = QLabel()
        self.workflow_stage.setObjectName("workflowStage")
        self.workflow_stage.setWordWrap(True)
        side_layout.addWidget(self.workflow_stage)
        side_layout.addSpacing(8)
        self.nav_buttons: dict[str, QPushButton] = {}
        navigation_group = QButtonGroup(self)
        navigation_group.setExclusive(True)
        for name in self._NAVIGATION:
            button = QPushButton(name)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, page_name=name: self._show_navigation(page_name)
            )
            navigation_group.addButton(button)
            self.nav_buttons[name] = button
            side_layout.addWidget(button)
        side_layout.addStretch(1)
        privacy = QLabel("LOCAL-FIRST\nClient rows are never stored in history")
        privacy.setObjectName("privacyBadge")
        privacy.setWordWrap(True)
        side_layout.addWidget(privacy)
        root_layout.addWidget(sidebar)

        self.stack = QStackedWidget()
        self.analysis_page = AnalysisPage(
            profiler or FileProfiler(self._config.limits), self._thread_pool
        )
        self.plan_page = PlanPage(self._registry)
        self.preview_page = PreviewPage(self._registry, self._thread_pool)
        self.results_page = ResultsPage(
            self._config,
            self._registry,
            self._thread_pool,
            persistence=self._persistence,
        )
        self.saved_workflows_page = WorkflowLibraryPage(self._persistence)
        self.job_history_page = JobHistoryPage(self._persistence)
        self.validation_reports_page = ValidationReportsPage(self._persistence)
        self.templates_page = WorkflowLibraryPage(
            self._persistence,
            title="Templates",
            subtitle=(
                "Inspect each workflow's exposed input sheets, target columns, mappings, "
                "validation parameters, and output name before reuse."
            ),
        )
        self.settings_page = SettingsPage(self._persistence)
        self.help_page = HelpPage()
        pages = (
            self.analysis_page,
            self.plan_page,
            self.preview_page,
            self.results_page,
            self.saved_workflows_page,
            self.job_history_page,
            self.validation_reports_page,
            self.templates_page,
            self.settings_page,
            self.help_page,
        )
        for page in pages:
            self.stack.addWidget(page)
        self._page_indexes = {page: index for index, page in enumerate(pages)}
        root_layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.analysis_page.job_ready.connect(self._on_job_ready)
        self.analysis_page.busy_changed.connect(lambda busy: self._set_navigation_enabled(not busy))
        self.plan_page.back_requested.connect(lambda: self._show_stage(0))
        self.plan_page.preview_requested.connect(self._on_preview_requested)
        self.preview_page.back_requested.connect(lambda: self._show_stage(1))
        self.preview_page.execute_requested.connect(self._on_execute_requested)
        self.preview_page.busy_changed.connect(lambda busy: self._set_navigation_enabled(not busy))
        self.results_page.back_requested.connect(lambda: self._show_stage(2))
        self.results_page.repeat_requested.connect(self._repeat_job)
        self.results_page.workflow_saved.connect(self._on_workflow_saved)
        self.results_page.execution_completed.connect(self._on_execution_finished)
        self.results_page.execution_failed.connect(self._on_execution_failed)
        self.results_page.execution_cancelled.connect(
            lambda: self._on_execution_failed("cancelled")
        )
        self.results_page.busy_changed.connect(lambda busy: self._set_navigation_enabled(not busy))
        self.saved_workflows_page.workflow_requested.connect(self._run_saved_workflow)
        self.templates_page.workflow_requested.connect(self._run_saved_workflow)
        self.saved_workflows_page.library_changed.connect(self.templates_page.refresh)
        self.templates_page.library_changed.connect(self.saved_workflows_page.refresh)
        self.job_history_page.repeat_requested.connect(self._repeat_history_job)
        self.settings_page.settings_saved.connect(self._refresh_persistence_pages)
        self._new_job()

    def _show_stage(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.workflow_stage.setText(f"Workflow stage\n{index + 1}. {self._WORKFLOW_LABELS[index]}")
        self.nav_buttons["New Job"].setChecked(True)

    def _show_navigation(self, name: str) -> None:
        if name == "New Job":
            self._new_job()
            return
        mapping = {
            "Saved Workflows": self.saved_workflows_page,
            "Job History": self.job_history_page,
            "Validation Reports": self.validation_reports_page,
            "Templates": self.templates_page,
            "Settings": self.settings_page,
            "Help": self.help_page,
        }
        page = mapping[name]
        if isinstance(page, WorkflowLibraryPage | JobHistoryPage | ValidationReportsPage):
            page.refresh()
        elif isinstance(page, SettingsPage):
            page.load()
        self.stack.setCurrentIndex(self._page_indexes[page])
        self.workflow_stage.setText(name)
        self.nav_buttons[name].setChecked(True)

    def _new_job(self) -> None:
        self._prepared = None
        self._plan = None
        self._pending_template = None
        self._pending_parameters = {}
        self._pending_workflow_id = None
        self._active_template = None
        self._active_workflow_id = None
        self.analysis_page.reset_job()
        default_directory = self._default_output_directory()
        if default_directory is not None:
            self.analysis_page.output_directory.setText(str(default_directory))
        self._show_stage(0)

    def _default_output_directory(self) -> Path | None:
        try:
            value = self._persistence.settings.get(SettingKey.DEFAULT_OUTPUT_DIRECTORY)
        except SheetPilotError:
            return None
        if isinstance(value, str):
            path = Path(value).expanduser()
            if path.is_dir():
                return path.resolve()
        return None

    def _on_job_ready(self, value: object) -> None:
        if not isinstance(value, JobDraft):
            return
        prepared = prepare_job(value)
        if self._pending_template is not None:
            template = self._pending_template
            if len(prepared.plan.source_files) != len(template.source_slots):
                self.analysis_page.status_label.setText(
                    f"This workflow requires exactly {len(template.source_slots)} source file(s); "
                    f"{len(prepared.plan.source_files)} were analysed."
                )
                return
            sources = {
                slot.slot_id: source
                for slot, source in zip(
                    template.source_slots,
                    prepared.plan.source_files,
                    strict=True,
                )
            }
            parameters = dict(self._pending_parameters)
            if any(parameter.key == "output_name" for parameter in template.parameters):
                parameters["output_name"] = value.output_name
            try:
                rebound = template.instantiate(
                    sources,
                    parameters,
                    job_name=value.job_name,
                )
                rebound_output = rebound.output.model_copy(
                    update={
                        "output_name": value.output_name,
                        "format": value.output_format,
                        "preserve_formatting": value.preserve_formatting,
                    }
                )
                rebound = OperationPlan.model_validate(
                    rebound.model_copy(
                        update={"output": rebound_output, "privacy": value.privacy}
                    ).model_dump()
                )
                PlanValidator(self._registry).validate(rebound)
            except (SheetPilotError, ValueError) as error:
                self.analysis_page.status_label.setText(
                    "The saved workflow could not be bound to these analysed files. "
                    f"Review its sheet, column, mapping, and validation parameters: {error}"
                )
                return
            prepared = PreparedJob(
                draft=value,
                plan=rebound,
                bindings=prepared.bindings,
            )
            self._active_template = template
            self._active_workflow_id = self._pending_workflow_id
            self._pending_template = None
            self._pending_parameters = {}
            self._pending_workflow_id = None
        else:
            self._active_template = None
            self._active_workflow_id = None
        self._prepared = prepared
        self._plan = prepared.plan
        self.plan_page.load_job(prepared)
        self._show_stage(1)

    def _on_preview_requested(self, value: object) -> None:
        if self._prepared is None or not isinstance(value, OperationPlan):
            return
        self._plan = value
        self.preview_page.load_plan(self._prepared, value)
        self._show_stage(2)

    def _on_execute_requested(self, value: object) -> None:
        preview = self.preview_page.preview
        if (
            self._prepared is None
            or self._plan is None
            or preview is None
            or not isinstance(value, ExecutionApproval)
        ):
            return
        self._set_navigation_enabled(False)
        self._show_stage(3)
        self.results_page.start_execution(
            self._prepared,
            self._plan,
            preview,
            value,
            workflow_template=self._active_template,
            workflow_id=self._active_workflow_id,
        )

    def _set_navigation_enabled(self, enabled: bool) -> None:
        for button in self.nav_buttons.values():
            button.setEnabled(enabled)

    def _on_execution_finished(self, _result: object) -> None:
        self._set_navigation_enabled(True)
        self._refresh_persistence_pages()

    def _on_execution_failed(self, _code: str) -> None:
        self._set_navigation_enabled(True)
        self._refresh_persistence_pages()

    def _refresh_persistence_pages(self) -> None:
        self.saved_workflows_page.refresh()
        self.templates_page.refresh()
        self.job_history_page.refresh()
        self.validation_reports_page.refresh()

    def _run_saved_workflow(self, value: object) -> None:
        if not isinstance(value, WorkflowRunRequest):
            return
        self._begin_workflow(
            value.template,
            value.parameter_values,
            workflow_id=value.template.workflow_id,
        )

    def _begin_workflow(
        self,
        template: WorkflowTemplate,
        parameter_values: dict[str, Any],
        *,
        workflow_id: UUID | None,
    ) -> None:
        self._prepared = None
        self._plan = None
        self._pending_template = template
        self._pending_parameters = dict(parameter_values)
        self._pending_workflow_id = workflow_id
        output_name = template.output.output_name
        supplied_name = parameter_values.get("output_name")
        if isinstance(supplied_name, str) and supplied_name:
            output_name = supplied_name
        self.analysis_page.prepare_for_workflow(
            name=template.name,
            description=template.description,
            output_format=template.output.format,
            output_name=output_name,
            preserve_formatting=template.output.preserve_formatting,
            expected_source_count=len(template.source_slots),
            default_output_directory=self._default_output_directory(),
        )
        self._show_stage(0)

    def _repeat_job(self, value: object) -> None:
        if not isinstance(value, WorkflowTemplate):
            return
        persisted_id = (
            self._active_workflow_id
            if self._active_template is not None
            and self._active_template.workflow_id == value.workflow_id
            else None
        )
        defaults = {
            parameter.key: parameter.default_value
            for parameter in value.parameters
            if parameter.default_value is not None
        }
        self._begin_workflow(value, defaults, workflow_id=persisted_id)

    def _repeat_history_job(self, value: object) -> None:
        if not isinstance(value, UUID):
            return
        try:
            record = self._persistence.history.get_required(value)
            if record.workflow_id is None:
                raise ValueError("This job has no saved workflow.")
            template = self._persistence.templates.get_required(record.workflow_id)
        except (SheetPilotError, ValueError) as error:
            self.job_history_page.refresh()
            self.job_history_page.status.setText(
                f"This job cannot be repeated because its workflow is unavailable: {error}"
            )
            return
        defaults = {
            parameter.key: parameter.default_value
            for parameter in template.parameters
            if parameter.default_value is not None
        }
        self._begin_workflow(template, defaults, workflow_id=template.workflow_id)

    def _on_workflow_saved(self, value: object) -> None:
        if not isinstance(value, WorkflowTemplate):
            return
        self._active_template = value
        self._active_workflow_id = value.workflow_id
        self._refresh_persistence_pages()
