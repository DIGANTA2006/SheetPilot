"""Background preview, execution, and restore workers."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from sheetpilot.core.backup_service import BackupReceipt, BackupService
from sheetpilot.core.exceptions import SheetPilotError, UserCancelledError
from sheetpilot.core.executor import JobExecutor
from sheetpilot.core.plan_schema import OperationPlan
from sheetpilot.core.preview_engine import (
    ExecutionApproval,
    PreviewEngine,
    PreviewResult,
    SourceBinding,
)
from sheetpilot.ui.workers.analysis_worker import CancellationToken


class JobWorkerSignals(QObject):
    """Signals with privacy-safe user messages and separate diagnostic metadata."""

    progress = Signal(int, str)
    completed = Signal(object)
    failed = Signal(str, str, str)
    cancelled = Signal()


def _emit_failure(signals: JobWorkerSignals, error: BaseException, fallback: str) -> None:
    if isinstance(error, SheetPilotError):
        signals.failed.emit(error.code, str(error), type(error).__name__)
    else:
        signals.failed.emit("unexpected_job_error", fallback, type(error).__name__)


class PreviewWorker(QRunnable):
    """Generate a deterministic preview away from the GUI thread."""

    def __init__(
        self,
        engine: PreviewEngine,
        plan: OperationPlan,
        bindings: tuple[SourceBinding, ...],
        token: CancellationToken,
    ) -> None:
        super().__init__()
        self.engine = engine
        self.plan = plan
        self.bindings = bindings
        self.token = token
        self.signals = JobWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.token.raise_if_cancelled()
            self.signals.progress.emit(15, "Verifying source fingerprints")
            preview, _ = self.engine.generate(self.plan, self.bindings)
            self.token.raise_if_cancelled()
            self.signals.progress.emit(100, "Preview ready")
            self.signals.completed.emit(preview)
        except UserCancelledError:
            self.signals.cancelled.emit()
        except BaseException as error:
            _emit_failure(
                self.signals,
                error,
                "Preview failed safely. Source files were not modified.",
            )


class ExecutionWorker(QRunnable):
    """Run the fail-closed execution transaction away from the GUI thread."""

    def __init__(
        self,
        executor: JobExecutor,
        plan: OperationPlan,
        bindings: tuple[SourceBinding, ...],
        preview: PreviewResult,
        approval: ExecutionApproval,
        output_directory: Path,
        token: CancellationToken,
    ) -> None:
        super().__init__()
        self.executor = executor
        self.plan = plan
        self.bindings = bindings
        self.preview = preview
        self.approval = approval
        self.output_directory = output_directory
        self.token = token
        self.signals = JobWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.token.raise_if_cancelled()
            self.signals.progress.emit(10, "Verifying approval and source files")
            result = self.executor.execute(
                self.plan,
                self.bindings,
                self.preview,
                self.approval,
                self.output_directory,
            )
            # Once execution begins, the atomic transaction is allowed to finish. A late
            # cancellation must never hide a safely committed output from the user.
            self.signals.progress.emit(100, "Execution and validation complete")
            self.signals.completed.emit(result)
        except UserCancelledError:
            self.signals.cancelled.emit()
        except BaseException as error:
            _emit_failure(
                self.signals,
                error,
                "Execution failed safely. Source files were not modified.",
            )


class RestoreWorker(QRunnable):
    """Restore a verified backup to a new destination without blocking the UI."""

    def __init__(
        self,
        service: BackupService,
        receipt: BackupReceipt,
        destination: Path,
    ) -> None:
        super().__init__()
        self.service = service
        self.receipt = receipt
        self.destination = destination
        self.signals = JobWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.progress.emit(20, "Verifying backup")
            restored = self.service.restore_to_new_file(self.receipt, self.destination)
            self.signals.progress.emit(100, "Restore complete")
            self.signals.completed.emit(restored)
        except BaseException as error:
            _emit_failure(
                self.signals,
                error,
                "Restore failed safely. No existing file was overwritten.",
            )
