"""Transactional execution progress and reconciliation results page."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import UUID

from PySide6.QtCore import QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
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
from sheetpilot.app.persistence import PersistenceServices
from sheetpilot.core.backup_service import BackupReceipt, BackupService
from sheetpilot.core.exceptions import SheetPilotError
from sheetpilot.core.executor import ExecutionResult, JobExecutor
from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.core.plan_schema import OperationPlan
from sheetpilot.core.preview_engine import ExecutionApproval, PreviewResult
from sheetpilot.storage.database import Database
from sheetpilot.storage.models import WorkflowTemplate
from sheetpilot.ui.workers.analysis_worker import CancellationToken
from sheetpilot.ui.workers.job_workers import ExecutionWorker, RestoreWorker
from sheetpilot.ui.workflow_models import PreparedJob


class ResultsPage(QWidget):
    """Run execution in the background and show durable completion evidence."""

    back_requested = Signal()
    repeat_requested = Signal(object)
    workflow_saved = Signal(object)
    execution_completed = Signal(object)
    execution_failed = Signal(str)
    execution_cancelled = Signal()
    busy_changed = Signal(bool)

    def __init__(
        self,
        config: AppConfig,
        registry: OperationRegistry,
        thread_pool: QThreadPool | None = None,
        persistence: PersistenceServices | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._executor = JobExecutor(config, registry)
        self._backup_service = BackupService(config.backup_dir)
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        if persistence is None:
            database = Database(config.database_path)
            database.initialize()
            persistence = PersistenceServices.build(database, registry)
        self._persistence = persistence
        self._token: CancellationToken | None = None
        self._execution_worker: ExecutionWorker | None = None
        self._restore_worker: RestoreWorker | None = None
        self._result: ExecutionResult | None = None
        self._available_backups: tuple[BackupReceipt, ...] = ()
        self._plan: OperationPlan | None = None
        self._workflow_template: WorkflowTemplate | None = None
        self._workflow_id: UUID | None = None
        self._history_started = False
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
        self.repeat.clicked.connect(self._repeat_workflow)
        self.save_workflow = QPushButton("Save workflow")
        self.save_workflow.clicked.connect(self._save_workflow)
        for widget in (
            self.open_folder,
            self.open_audit,
            self.backup_choice,
            self.restore,
            self.save_workflow,
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
        actions.addWidget(self.save_workflow)
        actions.addWidget(self.repeat)
        layout.addLayout(actions)

    def start_execution(
        self,
        prepared: PreparedJob,
        plan: OperationPlan,
        preview: PreviewResult,
        approval: ExecutionApproval,
        *,
        workflow_template: WorkflowTemplate | None = None,
        workflow_id: UUID | None = None,
    ) -> None:
        self._result = None
        self._available_backups = ()
        self._plan = plan
        self._workflow_template = workflow_template
        self._workflow_id = workflow_id
        self._history_started = False
        self.summary.setVisible(False)
        self.paths.setVisible(False)
        self.diagnostic_button.setVisible(False)
        self.backup_choice.clear()
        self.restore.setEnabled(True)
        for widget in (
            self.open_folder,
            self.open_audit,
            self.backup_choice,
            self.restore,
            self.save_workflow,
            self.repeat,
        ):
            widget.setVisible(False)
        self.back.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.cancel_button.setVisible(True)
        self.cancel_button.setEnabled(True)
        self.status.setText(
            "Starting the protected transaction. Cancellation is available until execution "
            "begins; an active atomic commit will finish safely."
        )
        try:
            self._persistence.jobs.record_started(plan, workflow_id=workflow_id)
        except (SheetPilotError, ValueError) as error:
            self.progress.setVisible(False)
            self.cancel_button.setVisible(False)
            self.back.setEnabled(True)
            self._diagnostic = (
                f"Diagnostic code: history_start_failed\nType: {type(error).__name__}"
            )
            self.diagnostic_button.setVisible(True)
            self.status.setText(
                "Execution did not start because its local history record could not be "
                "created. No source or output file was changed."
            )
            self.execution_failed.emit("history_start_failed")
            return
        self._history_started = True
        self.busy_changed.emit(True)
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
        self._execution_worker.signals.failed_with_backups.connect(self._on_failed_with_backups)
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
        self.busy_changed.emit(False)
        metadata_warning = self._record_success(result)
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)
        self.back.setEnabled(False)
        reconciliation = result.reconciliation
        self.status.setText(
            f"Completed safely · reconciliation {reconciliation.status.value.replace('_', ' ')}."
            + metadata_warning
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
        self._set_available_backups(result.backups)
        for widget in (
            self.open_folder,
            self.open_audit,
            self.backup_choice,
            self.restore,
            self.save_workflow,
            self.repeat,
        ):
            widget.setVisible(True)
        if self._workflow_id is not None:
            self.save_workflow.setText("Workflow already saved")
            self.save_workflow.setEnabled(False)
        else:
            self.save_workflow.setText("Save workflow")
            self.save_workflow.setEnabled(True)
        self.execution_completed.emit(result)

    def _record_success(self, result: ExecutionResult) -> str:
        if not self._history_started:
            return " Local history was not started."
        try:
            self._persistence.jobs.record_success(result)
            self._persistence.jobs.record_validation(result.job_id, result.validation)
        except (SheetPilotError, ValueError) as error:
            self._diagnostic = (
                f"Diagnostic code: history_completion_failed\nType: {type(error).__name__}"
            )
            self.diagnostic_button.setVisible(True)
            return " Output is valid, but local history metadata could not be completed."
        finally:
            self._history_started = False
        return ""

    def _on_failed(self, code: str, message: str, details: str) -> None:
        self._on_failed_with_backups(code, message, details, ())

    def _on_failed_with_backups(
        self, code: str, message: str, details: str, backups: object
    ) -> None:
        self.busy_changed.emit(False)
        self._record_failure()
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)
        self.back.setEnabled(True)
        self._diagnostic = f"Diagnostic code: {code}\nType: {details}"
        self.diagnostic_button.setVisible(True)
        if isinstance(backups, tuple) and all(isinstance(item, BackupReceipt) for item in backups):
            typed_backups = cast(tuple[BackupReceipt, ...], backups)
        else:
            typed_backups = ()
        self._set_available_backups(typed_backups)
        if typed_backups:
            self.paths.setText(
                "Verified backup(s) preserved after the failed execution:\n"
                + "\n".join(str(item.backup_path) for item in typed_backups)
            )
            self.paths.setVisible(True)
            self.backup_choice.setVisible(True)
            self.restore.setVisible(True)
            recovery = (
                f" {len(typed_backups)} verified backup(s) remain available below; restore "
                "always creates a new file."
            )
        else:
            recovery = ""
        self.status.setText(
            f"{message}{recovery} Review the diagnostic code, then return to the preview or "
            "choose a different output name."
        )
        self.execution_failed.emit(code)

    def _on_cancelled(self) -> None:
        self.busy_changed.emit(False)
        self._record_cancelled()
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)
        self.back.setEnabled(True)
        self.status.setText("Execution cancelled before the transaction began. No output was made.")
        self.execution_cancelled.emit()

    def _record_failure(self) -> None:
        if not self._history_started or self._plan is None:
            return
        try:
            self._persistence.jobs.record_failure(self._plan.job_id)
        except (SheetPilotError, ValueError):
            pass
        finally:
            self._history_started = False

    def _record_cancelled(self) -> None:
        if not self._history_started or self._plan is None:
            return
        try:
            self._persistence.jobs.record_cancelled(self._plan.job_id)
        except (SheetPilotError, ValueError):
            pass
        finally:
            self._history_started = False

    def _open_output_folder(self) -> None:
        if self._result is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result.output.path.parent)))

    def _open_audit(self) -> None:
        if self._result is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result.audit.path)))

    def _selected_backup(self) -> BackupReceipt | None:
        index = self.backup_choice.currentData()
        if not isinstance(index, int) or not (0 <= index < len(self._available_backups)):
            return None
        return self._available_backups[index]

    def _set_available_backups(self, backups: tuple[BackupReceipt, ...]) -> None:
        self._available_backups = backups
        self.backup_choice.clear()
        for index, receipt in enumerate(backups):
            created = receipt.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            self.backup_choice.addItem(f"{receipt.source_name} · {created}", index)

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
        self.busy_changed.emit(True)
        self._restore_worker = RestoreWorker(self._backup_service, receipt, target)
        self._restore_worker.signals.progress.connect(self._on_progress)
        self._restore_worker.signals.completed.connect(self._on_restored)
        self._restore_worker.signals.failed.connect(self._on_restore_failed)
        self._thread_pool.start(self._restore_worker)

    def _on_restored(self, destination: object) -> None:
        self.busy_changed.emit(False)
        self.restore.setEnabled(True)
        self.status.setText(f"Backup restored to a new file: {destination}")

    def _on_restore_failed(self, code: str, message: str, details: str) -> None:
        self.busy_changed.emit(False)
        self.restore.setEnabled(True)
        self._diagnostic = f"Diagnostic code: {code}\nType: {details}"
        self.diagnostic_button.setVisible(True)
        self.status.setText(message)

    def _show_diagnostic(self) -> None:
        QMessageBox.information(self, "Diagnostic details", self._diagnostic)

    def _save_workflow(self) -> None:
        if self._result is None or self._plan is None or self._workflow_id is not None:
            return
        name, accepted = QInputDialog.getText(
            self,
            "Save reusable workflow",
            "Workflow name",
            text=self._plan.job_name,
        )
        if not accepted or not name.strip():
            return
        description, description_accepted = QInputDialog.getMultiLineText(
            self,
            "Workflow description",
            "Describe when this workflow should be reused",
            self._plan.job_name,
        )
        if not description_accepted:
            return
        try:
            template = self._persistence.workflows.save_validated(
                self._plan,
                name=name.strip(),
                description=description.strip(),
            )
            self._persistence.jobs.link_workflow(self._plan.job_id, template.workflow_id)
        except (SheetPilotError, ValueError) as error:
            self._diagnostic = (
                f"Diagnostic code: workflow_save_failed\nType: {type(error).__name__}"
            )
            self.diagnostic_button.setVisible(True)
            self.status.setText(f"The workflow could not be saved: {error}")
            return
        self._workflow_template = template
        self._workflow_id = template.workflow_id
        self.save_workflow.setText("Workflow saved")
        self.save_workflow.setEnabled(False)
        self.status.setText(
            "Workflow saved without source paths, hashes, spreadsheet rows, or cloud transfer."
        )
        self.workflow_saved.emit(template)

    def _repeat_workflow(self) -> None:
        if self._plan is None or self._result is None:
            return
        template = self._workflow_template
        if template is None:
            template = WorkflowTemplate.from_plan(
                self._plan,
                name=self._plan.job_name,
                description="Repeat this reviewed deterministic workflow with new files.",
            )
        self.repeat_requested.emit(template)
