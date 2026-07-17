"""Main application window and end-to-end job workflow shell."""

from __future__ import annotations

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from sheetpilot.app.config import AppConfig
from sheetpilot.app.version import __version__
from sheetpilot.core.file_profiler import FileProfiler
from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.core.plan_schema import OperationPlan
from sheetpilot.core.preview_engine import ExecutionApproval
from sheetpilot.operations.registry import build_default_registry
from sheetpilot.ui.pages.analysis_page import AnalysisPage
from sheetpilot.ui.pages.plan_page import PlanPage
from sheetpilot.ui.pages.preview_page import PreviewPage
from sheetpilot.ui.pages.results_page import ResultsPage
from sheetpilot.ui.theme import LIGHT_STYLESHEET
from sheetpilot.ui.workflow_models import JobDraft, PreparedJob, prepare_job


class MainWindow(QMainWindow):
    """Coordinate intake, analysis, plan review, preview, approval, and results."""

    def __init__(
        self,
        profiler: FileProfiler | None = None,
        *,
        config: AppConfig | None = None,
        registry: OperationRegistry | None = None,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__()
        self._config = config or AppConfig.default()
        self._config.ensure_directories()
        self._registry = registry or build_default_registry()
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._prepared: PreparedJob | None = None
        self._plan: OperationPlan | None = None

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
        sidebar.setFixedWidth(205)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(20, 26, 20, 26)
        product = QLabel("SheetPilot")
        product.setObjectName("productName")
        side_layout.addWidget(product)
        side_layout.addWidget(QLabel("Safe spreadsheet workflows"))
        side_layout.addSpacing(28)
        self.stage_labels: list[QLabel] = []
        for label in ("1  New job & analysis", "2  Plan", "3  Preview", "4  Results"):
            stage = QLabel(label)
            stage.setObjectName("workflowStage")
            stage.setProperty("active", False)
            self.stage_labels.append(stage)
            side_layout.addWidget(stage)
        side_layout.addStretch(1)
        privacy = QLabel("LOCAL MODE\nNo client data leaves this device")
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
        )
        for page in (
            self.analysis_page,
            self.plan_page,
            self.preview_page,
            self.results_page,
        ):
            self.stack.addWidget(page)
        root_layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.analysis_page.job_ready.connect(self._on_job_ready)
        self.plan_page.back_requested.connect(lambda: self._show_stage(0))
        self.plan_page.preview_requested.connect(self._on_preview_requested)
        self.preview_page.back_requested.connect(lambda: self._show_stage(1))
        self.preview_page.execute_requested.connect(self._on_execute_requested)
        self.results_page.back_requested.connect(lambda: self._show_stage(2))
        self.results_page.repeat_requested.connect(self._repeat_job)
        self._show_stage(0)

    def _show_stage(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for position, label in enumerate(self.stage_labels):
            label.setProperty("active", position == index)
            label.style().unpolish(label)
            label.style().polish(label)

    def _on_job_ready(self, value: object) -> None:
        if not isinstance(value, JobDraft):
            return
        self._prepared = prepare_job(value)
        self._plan = self._prepared.plan
        self.plan_page.load_job(self._prepared)
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
        self._show_stage(3)
        self.results_page.start_execution(self._prepared, self._plan, preview, value)

    def _repeat_job(self) -> None:
        self._prepared = None
        self._plan = None
        self.analysis_page.reset_job()
        self._show_stage(0)
