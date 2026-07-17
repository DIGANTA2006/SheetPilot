"""Drag/drop, browse, and background file-analysis page."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from sheetpilot.core.file_profiler import FileProfiler
from sheetpilot.core.profile_models import FileProfile
from sheetpilot.security.file_guard import SUPPORTED_SUFFIXES
from sheetpilot.ui.models.analysis_model import AnalysisTableModel
from sheetpilot.ui.workers.analysis_worker import AnalysisWorker, CancellationToken


class AnalysisPage(QWidget):
    """A complete non-destructive source selection and analysis workflow."""

    analysis_completed = Signal(object)

    def __init__(self, profiler: FileProfiler, thread_pool: QThreadPool | None = None) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self._profiler = profiler
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._paths: list[Path] = []
        self._token: CancellationToken | None = None
        self._worker: AnalysisWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(42, 34, 42, 34)
        layout.setSpacing(12)
        title = QLabel("New job · Source analysis")
        title.setObjectName("title")
        subtitle = QLabel(
            "Drop or browse for .xlsx, .xlsm, or UTF-8 .csv files. Analysis is read-only."
        )
        subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.file_list = QListWidget()
        self.file_list.setObjectName("sourceFileList")
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setAccessibleName("Selected source files")
        layout.addWidget(self.file_list, 1)

        buttons = QHBoxLayout()
        self.browse_button = QPushButton("Browse files")
        self.browse_button.clicked.connect(self._browse)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.clicked.connect(self._remove_selected)
        self.analyse_button = QPushButton("Analyse safely")
        self.analyse_button.setObjectName("analyseButton")
        self.analyse_button.clicked.connect(self.start_analysis)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_analysis)
        buttons.addWidget(self.browse_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.analyse_button)
        layout.addLayout(buttons)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.status_label = QLabel("")
        self.status_label.setObjectName("analysisStatus")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.model = AnalysisTableModel()
        self.results = QTableView()
        self.results.setObjectName("analysisResults")
        self.results.setModel(self.model)
        self.results.setAlternatingRowColors(True)
        self.results.horizontalHeader().setStretchLastSection(True)
        self.results.setVisible(False)
        layout.addWidget(self.results, 2)

    def add_files(self, paths: tuple[Path, ...]) -> None:
        existing = {str(path).casefold() for path in self._paths}
        rejected = 0
        for path in paths:
            resolved = path.resolve()
            if resolved.suffix.casefold() not in SUPPORTED_SUFFIXES or not resolved.is_file():
                rejected += 1
                continue
            if str(resolved).casefold() not in existing:
                self._paths.append(resolved)
                self.file_list.addItem(resolved.name)
                existing.add(str(resolved).casefold())
        if rejected:
            self.status_label.setText(
                "Some files were ignored because their format is unsupported."
            )

    def _browse(self) -> None:
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "Select source files",
            "",
            "Supported data (*.xlsx *.xlsm *.csv)",
        )
        self.add_files(tuple(Path(path) for path in selected))

    def _remove_selected(self) -> None:
        selected_rows = sorted(
            {index.row() for index in self.file_list.selectedIndexes()}, reverse=True
        )
        for row in selected_rows:
            self.file_list.takeItem(row)
            del self._paths[row]

    def _set_running(self, running: bool) -> None:
        self.browse_button.setEnabled(not running)
        self.remove_button.setEnabled(not running)
        self.analyse_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.progress.setVisible(running)

    def start_analysis(self) -> None:
        if not self._paths:
            self.status_label.setText("Add at least one supported source file before analysis.")
            return
        self._set_running(True)
        self.results.setVisible(False)
        self.status_label.setText("Preparing safe analysis…")
        self.progress.setRange(0, len(self._paths))
        self.progress.setValue(0)
        self._token = CancellationToken()
        self._worker = AnalysisWorker(self._profiler, tuple(self._paths), self._token)
        self._worker.signals.progress.connect(self._on_progress)
        self._worker.signals.completed.connect(self._on_completed)
        self._worker.signals.failed.connect(self._on_failed)
        self._worker.signals.cancelled.connect(self._on_cancelled)
        self._thread_pool.start(self._worker)

    def cancel_analysis(self) -> None:
        if self._token is not None:
            self._token.cancel()
            self.status_label.setText("Cancelling safely…")
            self.cancel_button.setEnabled(False)

    def _on_progress(self, current: int, total: int, label: str) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(current)
        self.status_label.setText(label)

    def _on_completed(self, profiles: object) -> None:
        typed_profiles = tuple(profiles) if isinstance(profiles, tuple) else ()
        if not all(isinstance(profile, FileProfile) for profile in typed_profiles):
            self._on_failed("invalid_worker_result", "Analysis returned an invalid result.")
            return
        self.model.set_profiles(typed_profiles)
        self.results.resizeColumnsToContents()
        self.results.setVisible(True)
        warning_count = sum(profile.warning_count for profile in typed_profiles)
        self.status_label.setText(
            f"Analysed {len(typed_profiles)} file(s) safely · {warning_count} warning(s) to review."
        )
        self._set_running(False)
        self.analysis_completed.emit(typed_profiles)

    def _on_failed(self, code: str, message: str) -> None:
        self._set_running(False)
        self.status_label.setText(f"{message} Diagnostic code: {code}.")

    def _on_cancelled(self) -> None:
        self._set_running(False)
        self.status_label.setText("Analysis cancelled safely. Source files were not changed.")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self.add_files(tuple(Path(url.toLocalFile()) for url in event.mimeData().urls()))
        event.acceptProposedAction()
