"""Transactional execution progress and reconciliation results page."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sheetpilot.app.config import AppConfig
from sheetpilot.core.backup_service import BackupReceipt, BackupService
from sheetpilot.core.executor import ExecutionResult, JobExecutor
from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.core.plan_schema import OperationPlan
from sheetpilot.core.preview_engine import ExecutionApproval, PreviewResult
from sheetpilot.ui.workers.analysis_worker import CancellationToken
from sheetpilot.ui.workers.job_workers import ExecutionWorker, RestoreWorker
from sheetpilot.ui.workflow_models import PreparedJob


class ResultsPage(QWidget):
    """Run execution in the background and show durable completion evidence."""

    back_requested = Signal()
    repeat_requested = Signal()
    execution_completed = Signal(object)

    def __init__(
        self,
        config: AppConfig,
        registry: OperationRegistry,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._executor = JobExecutor(config, registry)
        self._backup_service = BackupService(config.backup_dir)
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._token: CancellationToken | None = None
        self._execution_worker: ExecutionWorker | None = None
        self._restore_worker: RestoreWorker | None = None
        self._result: ExecutionResult | None = None
        self._diagnostic = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        title = QLabel("Results · Validation and reconciliation")
        title.setObjectName("title")
        subtitle = QLabel(
            "A result appears only after output validation, atomic publication, and audit creation."
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
        self.status.setObjectName("resultStatus")
        self.diagnostic_button = QPushButton("Diagnostic details")
        self.diagnostic_button.setVisible(False)
        self.diagnostic_button.clicked.connect(self._show_diagnostic)
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.diagnostic_button)
        layout.addLayout(status_row)

        self.summary = QTableWidget(0, 2)
        self.summary.setHorizontalHeaderLabels(["Reconciliation measure", "Result"])
        self.summary.horizontalHeader().setStretchLastSection(True)
        self.summary.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.summary.setVisible(False)
        layout.addWidget(self.summary, 1)

        self.paths = QLabel()
        self.paths.setWordWrap(True)
        self.paths.setTextInteractionFlags(self.paths.textInteractionFlags())
        self.paths.setVisible(False)
        layout.addWidget(self.paths)

        actions = QHBoxLayout()
        self.cancel_button = QPushButton("Request safe cancellation")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel_execution)
        self.open_folder = QPushButton("Open output folder")
        self.open_folder.clicked.connect(self._open_output_folder)
        self.open_audit = QPushButton("Open audit report")
        self.open_audit.clicked.connect(self._open_audit)
        self.backup_choice = QComboBox()
        self.backup_choice.setAccessibleName("Backup to restore")
        self.restore = QPushButton("Restore backup to new file")
        self.restore.clicked.connect(self._restore_backup)
        self.back = QPushButton("Back to preview")
        self.back.clicked.connect(self.back_requested)
        self.repeat = QPushButton("Repeat with new files")
        self.repeat.setObjectName("primaryButton")
        self.repeat.clicked.connect(self.repeat_requested)
        for widget in (
            self.open_folder,
            self.open_audit,
            self.backup_choice,
            self.restore,
            self.repeat,
        ):
            widget.setVisible(False)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.back)
        actions.addStretch(1)
        actions.addWidget(self.open_folder)
        actions.addWidget(self.open_audit)
        actions.addWidget(self.backup_choice)
        actions.addWidget(self.restore)
        actions.addWidget(self.repeat)
        layout.addLayout(actions)

    def start_execution(
        self,
        prepared: PreparedJob,
        plan: OperationPlan,
        preview: PreviewResult,
        approval: ExecutionApproval,
    ) -> None:
        self._result = None
        self.summary.setVisible(False)
        self.paths.setVisible(False)
        self.diagnostic_button.setVisible(False)
        self.back.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.cancel_button.setVisible(True)
        self.cancel_button.setEnabled(True)
        self.status.setText(
            "Starting the protected transaction. Cancellation is available until execution "
            "begins; an active atomic commit will finish safely."
        )
        self._token = CancellationToken()
        self._execution_worker = ExecutionWorker(
            self._executor,
            plan,
            prepared.bindings,
            preview,
            approval,
            prepared.draft.output_directory,
            self._token,
        )
        self._execution_worker.signals.progress.connect(self._on_progress)
        self._execution_worker.signals.completed.connect(self._on_completed)
        self._execution_worker.signals.failed.connect(self._on_failed)
        self._execution_worker.signals.cancelled.connect(self._on_cancelled)
        self._thread_pool.start(self._execution_worker)

    def cancel_execution(self) -> None:
        if self._token is None:
            return
        self._token.cancel()
        self.cancel_button.setEnabled(False)
        self.status.setText(
            "Cancellation requested. If the protected transaction already started, it will "
            "finish validation and atomic publication before reporting the result."
        )

    def _on_progress(self, percent: int, label: str) -> None:
        self.progress.setValue(percent)
        self.status.setText(label)

    def _on_completed(self, result: object) -> None:
        if not isinstance(result, ExecutionResult):
            self._on_failed(
                "invalid_worker_result",
                "Execution returned an invalid result.",
                "ExecutionResult type check failed",
            )
            return
        self._result = result
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)
        self.back.setEnabled(False)
        reconciliation = result.reconciliation
        self.status.setText(
            f"Completed safely · reconciliation {reconciliation.status.value.replace('_', ' ')}."
        )
        values: tuple[tuple[str, object], ...] = (
            ("Original rows", reconciliation.original_row_count),
            ("Final rows", reconciliation.final_row_count),
            ("Rows changed", reconciliation.rows_changed),
            ("Rows removed", reconciliation.rows_removed),
            ("Rows added", reconciliation.rows_added),
            ("Duplicates removed", reconciliation.duplicates_removed),
            ("Invalid records", reconciliation.invalid_records_found),
            ("Missing required values", reconciliation.missing_required_values),
            ("Sheets created", ", ".join(reconciliation.sheets_created) or "None"),
            ("Sheets modified", ", ".join(reconciliation.sheets_modified) or "None"),
            ("Formulas added", reconciliation.formulas_added),
            ("Warnings", reconciliation.warning_count),
            ("Total accepted changes", reconciliation.total_changes),
            ("Reconciliation", reconciliation.status.value.replace("_", " ").title()),
            ("Output SHA-256", result.output.fingerprint.sha256),
            ("Source SHA-256", ", ".join(item.sha256 for item in result.audit_report.sources)),
        )
        self.summary.setRowCount(len(values))
        for row, (label, value) in enumerate(values):
            self.summary.setItem(row, 0, QTableWidgetItem(label))
            self.summary.setItem(row, 1, QTableWidgetItem(str(value)))
        self.summary.resizeColumnsToContents()
        self.summary.setVisible(True)
        self.paths.setText(
            f"Output: {result.output.path}\n"
            f"Audit report: {result.audit.path}\n"
            f"Backup(s): {', '.join(str(item.backup_path) for item in result.backups)}"
        )
        self.paths.setVisible(True)
        self.backup_choice.clear()
        for index, receipt in enumerate(result.backups):
            self.backup_choice.addItem(receipt.source_name, index)
        for widget in (
            self.open_folder,
            self.open_audit,
            self.backup_choice,
            self.restore,
            self.repeat,
        ):
            widget.setVisible(True)
        self.execution_completed.emit(result)

    def _on_failed(self, code: str, message: str, details: str) -> None:
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)
        self.back.setEnabled(True)
        self._diagnostic = f"Diagnostic code: {code}\nType: {details}"
        self.diagnostic_button.setVisible(True)
        self.status.setText(
            f"{message} Review the diagnostic code, then return to the preview or choose a "
            "different output name."
        )

    def _on_cancelled(self) -> None:
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)
        self.back.setEnabled(True)
        self.status.setText("Execution cancelled before the transaction began. No output was made.")

    def _open_output_folder(self) -> None:
        if self._result is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result.output.path.parent)))

    def _open_audit(self) -> None:
        if self._result is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result.audit.path)))

    def _selected_backup(self) -> BackupReceipt | None:
        if self._result is None:
            return None
        index = self.backup_choice.currentData()
        if not isinstance(index, int) or not (0 <= index < len(self._result.backups)):
            return None
        return self._result.backups[index]

    def _restore_backup(self) -> None:
        receipt = self._selected_backup()
        if receipt is None:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Restore verified backup to a new file",
            str(receipt.backup_path.parent / f"restored_{receipt.source_name}"),
        )
        if not destination:
            return
        target = Path(destination)
        if target.exists():
            self.status.setText("Choose a new restore path; existing files are never overwritten.")
            return
        self.restore.setEnabled(False)
        self.status.setText("Restoring a hash-verified backup in the background…")
        self._restore_worker = RestoreWorker(self._backup_service, receipt, target)
        self._restore_worker.signals.progress.connect(self._on_progress)
        self._restore_worker.signals.completed.connect(self._on_restored)
        self._restore_worker.signals.failed.connect(self._on_restore_failed)
        self._thread_pool.start(self._restore_worker)

    def _on_restored(self, destination: object) -> None:
        self.restore.setEnabled(True)
        self.status.setText(f"Backup restored to a new file: {destination}")

    def _on_restore_failed(self, code: str, message: str, details: str) -> None:
        self.restore.setEnabled(True)
        self._diagnostic = f"Diagnostic code: {code}\nType: {details}"
        self.diagnostic_button.setVisible(True)
        self.status.setText(message)

    def _show_diagnostic(self) -> None:
        QMessageBox.information(self, "Diagnostic details", self._diagnostic)
