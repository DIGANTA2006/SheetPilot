"""Before/after preview, filtering, rejection, and approval page."""

from __future__ import annotations

from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.core.plan_schema import OperationPlan
from sheetpilot.core.preview_engine import ExecutionApproval, PreviewEngine, PreviewResult
from sheetpilot.ui.models.preview_model import PreviewFilterModel, PreviewTableModel
from sheetpilot.ui.workers.analysis_worker import CancellationToken
from sheetpilot.ui.workers.job_workers import PreviewWorker
from sheetpilot.ui.workflow_models import PreparedJob


class PreviewPage(QWidget):
    """Generate and review a hash-bound preview before any source backup or output."""

    back_requested = Signal()
    execute_requested = Signal(object)
    preview_completed = Signal(object)

    def __init__(
        self,
        registry: OperationRegistry,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__()
        self._engine = PreviewEngine(registry)
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._prepared: PreparedJob | None = None
        self._plan: OperationPlan | None = None
        self._preview: PreviewResult | None = None
        self._token: CancellationToken | None = None
        self._worker: PreviewWorker | None = None
        self._diagnostic = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        title = QLabel("Preview · Approve exact changes")
        title.setObjectName("title")
        subtitle = QLabel(
            "Preview values stay in memory. Uncheck supported cell changes to reject them."
        )
        subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        status_row = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.diagnostic_button = QPushButton("Diagnostic details")
        self.diagnostic_button.setVisible(False)
        self.diagnostic_button.clicked.connect(self._show_diagnostic)
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.diagnostic_button)
        layout.addLayout(status_row)

        self.warning_heading = QLabel("Operation warnings")
        self.warning_heading.setObjectName("sectionTitle")
        self.warning_heading.setVisible(False)
        layout.addWidget(self.warning_heading)
        self.warning_list = QListWidget()
        self.warning_list.setAccessibleName("Operation warning details")
        self.warning_list.setMaximumHeight(105)
        self.warning_list.setVisible(False)
        layout.addWidget(self.warning_list)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search file, sheet, value, reason, or step")
        self.search.setAccessibleName("Search preview changes")
        self.category = QComboBox()
        self.category.addItem("All proposed changes", "all")
        self.category.addItem("Changed values only", "changed")
        self.category.addItem("Deleted rows only", "deleted")
        self.category.addItem("High-risk warnings", "warnings")
        filters.addWidget(self.search, 1)
        filters.addWidget(self.category)
        layout.addLayout(filters)

        self.model = PreviewTableModel()
        self.proxy = PreviewFilterModel()
        self.proxy.setSourceModel(self.model)
        self.search.textChanged.connect(self.proxy.set_search)
        self.category.currentIndexChanged.connect(self._filter_changed)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        self.destructive_confirmation = QCheckBox(
            "I confirm all enabled destructive steps listed in this approved preview"
        )
        self.destructive_confirmation.setObjectName("destructiveConfirmation")
        self.destructive_confirmation.setVisible(False)
        layout.addWidget(self.destructive_confirmation)
        self.approval = QCheckBox(
            "I approve the reviewed plan and accepted changes for deterministic execution"
        )
        self.approval.setObjectName("executionApproval")
        layout.addWidget(self.approval)

        buttons = QHBoxLayout()
        self.back_button = QPushButton("Back to plan")
        self.back_button.clicked.connect(self.back_requested)
        self.cancel_button = QPushButton("Cancel preview")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel_preview)
        self.execute_button = QPushButton("Execute approved plan")
        self.execute_button.setObjectName("dangerButton")
        self.execute_button.setEnabled(False)
        self.execute_button.clicked.connect(self._request_execution)
        self.approval.toggled.connect(self._update_execute_enabled)
        self.destructive_confirmation.toggled.connect(self._update_execute_enabled)
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        buttons.addWidget(self.execute_button)
        layout.addLayout(buttons)

    @property
    def preview(self) -> PreviewResult | None:
        return self._preview

    def load_plan(self, prepared: PreparedJob, plan: OperationPlan) -> None:
        self._prepared = prepared
        self._plan = plan
        self._preview = None
        self.model.set_changes(())
        self.warning_list.clear()
        self.warning_list.setVisible(False)
        self.warning_heading.setVisible(False)
        self.approval.setChecked(False)
        self.destructive_confirmation.setChecked(False)
        self.destructive_confirmation.setVisible(
            any(step.enabled and step.destructive for step in plan.steps)
        )
        self.diagnostic_button.setVisible(False)
        self.start_preview()

    def start_preview(self) -> None:
        if self._prepared is None or self._plan is None:
            return
        self._set_running(True)
        self.status.setText("Preparing a deterministic before-and-after preview…")
        self.progress.setValue(0)
        self._token = CancellationToken()
        self._worker = PreviewWorker(
            self._engine,
            self._plan,
            self._prepared.bindings,
            self._token,
        )
        self._worker.signals.progress.connect(self._on_progress)
        self._worker.signals.completed.connect(self._on_completed)
        self._worker.signals.failed.connect(self._on_failed)
        self._worker.signals.cancelled.connect(self._on_cancelled)
        self._thread_pool.start(self._worker)

    def cancel_preview(self) -> None:
        if self._token is not None:
            self._token.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText("Cancelling preview safely…")

    def _set_running(self, running: bool) -> None:
        self.progress.setVisible(running)
        self.cancel_button.setVisible(running)
        self.cancel_button.setEnabled(running)
        self.back_button.setEnabled(not running)
        if running:
            self.execute_button.setEnabled(False)

    def _on_progress(self, percent: int, label: str) -> None:
        self.progress.setValue(percent)
        self.status.setText(label)

    def _on_completed(self, result: object) -> None:
        if not isinstance(result, PreviewResult):
            self._on_failed(
                "invalid_worker_result",
                "Preview returned an invalid result.",
                "PreviewResult type check failed",
            )
            return
        self._preview = result
        self.model.set_changes(result.changes)
        self.warning_list.clear()
        for step in result.steps:
            for warning in step.warnings:
                self.warning_list.addItem(f"{step.step_id} · {step.operation}: {warning}")
        has_warnings = self.warning_list.count() > 0
        self.warning_heading.setVisible(has_warnings)
        self.warning_list.setVisible(has_warnings)
        self.table.resizeColumnsToContents()
        warning_count = sum(len(step.warnings) for step in result.steps)
        sample_note = (
            f" Showing a representative sample of {len(result.changes)}." if result.sampled else ""
        )
        self.status.setText(
            f"{result.total_change_count} proposed change(s) across "
            f"{len(result.steps)} step(s); {warning_count} warning(s).{sample_note}"
        )
        self._set_running(False)
        self._update_execute_enabled()
        self.preview_completed.emit(result)

    def _on_failed(self, code: str, message: str, details: str) -> None:
        self._set_running(False)
        self._diagnostic = f"Diagnostic code: {code}\nType: {details}"
        self.diagnostic_button.setVisible(True)
        self.status.setText(message)

    def _on_cancelled(self) -> None:
        self._set_running(False)
        self.status.setText("Preview cancelled safely. Source files were not changed.")

    def _filter_changed(self) -> None:
        value = self.category.currentData()
        self.proxy.set_category(value if isinstance(value, str) else "all")

    def _update_execute_enabled(self) -> None:
        destructive_ok = (
            not self.destructive_confirmation.isVisible()
            or self.destructive_confirmation.isChecked()
        )
        self.execute_button.setEnabled(
            self._preview is not None and self.approval.isChecked() and destructive_ok
        )

    def _request_execution(self) -> None:
        if self._preview is None or self._plan is None:
            return
        if not self.approval.isChecked():
            self.status.setText("Explicit approval is required before execution.")
            return
        destructive_steps = frozenset(
            step.step_id
            for step in self._plan.steps
            if step.enabled and step.destructive and self.destructive_confirmation.isChecked()
        )
        approval = ExecutionApproval(
            job_id=self._plan.job_id,
            preview_digest=self._preview.preview_digest,
            approved=True,
            confirmed_destructive_steps=destructive_steps,
            rejected_change_ids=self.model.rejected_change_ids,
        )
        self.execute_requested.emit(approval)

    def _show_diagnostic(self) -> None:
        QMessageBox.information(self, "Diagnostic details", self._diagnostic)
