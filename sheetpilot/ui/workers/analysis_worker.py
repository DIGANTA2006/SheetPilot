"""Background file-analysis worker with cooperative cancellation."""

from __future__ import annotations

from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from sheetpilot.core.exceptions import SheetPilotError, UserCancelledError
from sheetpilot.core.file_profiler import FileProfiler
from sheetpilot.core.profile_models import FileProfile


class CancellationToken:
    """Thread-safe cooperative cancellation flag."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise UserCancelledError("Analysis was cancelled safely.")


class AnalysisSignals(QObject):
    progress = Signal(int, int, str)
    completed = Signal(object)
    failed = Signal(str, str)
    cancelled = Signal()


class AnalysisWorker(QRunnable):
    """Profile files away from the GUI thread and emit immutable results."""

    def __init__(
        self,
        profiler: FileProfiler,
        paths: tuple[Path, ...],
        token: CancellationToken,
    ) -> None:
        super().__init__()
        self.profiler = profiler
        self.paths = paths
        self.token = token
        self.signals = AnalysisSignals()

    @Slot()
    def run(self) -> None:
        profiles: list[FileProfile] = []
        try:
            for index, path in enumerate(self.paths, start=1):
                self.token.raise_if_cancelled()
                self.signals.progress.emit(index - 1, len(self.paths), path.name)
                profiles.append(self.profiler.profile(path))
            self.token.raise_if_cancelled()
            self.signals.progress.emit(len(self.paths), len(self.paths), "Analysis complete")
            self.signals.completed.emit(tuple(profiles))
        except UserCancelledError:
            self.signals.cancelled.emit()
        except SheetPilotError as error:
            self.signals.failed.emit(error.code, str(error))
        except Exception:
            self.signals.failed.emit(
                "unexpected_analysis_error",
                "Analysis failed safely. No source file was modified.",
            )
