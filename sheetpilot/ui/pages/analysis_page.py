"""New-job intake and non-destructive background source analysis."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import cast

from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from sheetpilot.core.file_profiler import FileProfiler
from sheetpilot.core.plan_schema import OutputFormat, PrivacyMetadata, PrivacyMode
from sheetpilot.core.profile_models import FileProfile
from sheetpilot.security.file_guard import SUPPORTED_SUFFIXES
from sheetpilot.ui.models.analysis_model import AnalysisTableModel
from sheetpilot.ui.workers.analysis_worker import AnalysisWorker, CancellationToken
from sheetpilot.ui.workflow_models import JobDraft


class AnalysisPage(QWidget):
    """Collect a job and inspect every source without modifying it."""

    analysis_completed = Signal(object)
    job_ready = Signal(object)
    busy_changed = Signal(bool)

    def __init__(self, profiler: FileProfiler, thread_pool: QThreadPool | None = None) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self._profiler = profiler
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._paths: list[Path] = []
        self._profiles: tuple[FileProfile, ...] = ()
        self._token: CancellationToken | None = None
        self._worker: AnalysisWorker | None = None
        self._diagnostic = ""
        self._analysis_running = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(10)
        title = QLabel("New job · Source analysis")
        title.setObjectName("title")
        subtitle = QLabel(
            "Add the client request and source files. Analysis is read-only and stays local."
        )
        subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        form = QFormLayout()
        self.job_name = QLineEdit()
        self.job_name.setPlaceholderText("Example: July customer cleanup")
        self.job_name.setAccessibleName("Job name")
        form.addRow("Job name", self.job_name)
        self.instructions = QPlainTextEdit()
        self.instructions.setMaximumHeight(90)
        self.instructions.setPlaceholderText("Paste the client's requested outcome and rules")
        self.instructions.setAccessibleName("Client instructions")
        form.addRow("Client instructions", self.instructions)

        output_row = QHBoxLayout()
        self.output_format = QComboBox()
        self.output_format.addItem("Excel workbook (.xlsx)", OutputFormat.XLSX)
        self.output_format.addItem("CSV (.csv)", OutputFormat.CSV)
        self.output_name = QLineEdit("processed_output")
        self.output_name.setAccessibleName("Output file name")
        output_row.addWidget(self.output_format)
        output_row.addWidget(self.output_name, 1)
        form.addRow("Output", output_row)

        directory_row = QHBoxLayout()
        self.output_directory = QLineEdit()
        self.output_directory.setAccessibleName("Output directory")
        self.output_directory.setPlaceholderText("Choose an approved output folder")
        self.output_browse = QPushButton("Choose folder")
        self.output_browse.clicked.connect(self._browse_output_directory)
        directory_row.addWidget(self.output_directory, 1)
        directory_row.addWidget(self.output_browse)
        form.addRow("Output folder", directory_row)

        options_row = QHBoxLayout()
        self.deadline = QLineEdit()
        self.deadline.setPlaceholderText("Optional YYYY-MM-DD")
        self.deadline.setMaximumWidth(170)
        self.preserve_formatting = QCheckBox("Preserve workbook formatting")
        self.preserve_formatting.setChecked(True)
        options_row.addWidget(self.deadline)
        options_row.addWidget(self.preserve_formatting)
        options_row.addStretch(1)
        form.addRow("Deadline and format", options_row)

        privacy_row = QHBoxLayout()
        self.privacy_mode = QComboBox()
        self.privacy_mode.addItem("Local / offline", PrivacyMode.LOCAL)
        self.privacy_mode.addItem("AI-assisted (provider required)", PrivacyMode.AI_ASSISTED)
        model = cast(QStandardItemModel, self.privacy_mode.model())
        ai_item = model.item(1)
        if ai_item is not None:
            ai_item.setEnabled(False)
            ai_item.setToolTip("Configure an AI provider before using AI-assisted planning.")
        self.metadata_consent = QCheckBox("Allow anonymized metadata")
        self.raw_data_consent = QCheckBox("Allow raw client data")
        self.metadata_consent.setEnabled(False)
        self.raw_data_consent.setEnabled(False)
        privacy_row.addWidget(self.privacy_mode)
        privacy_row.addWidget(self.metadata_consent)
        privacy_row.addWidget(self.raw_data_consent)
        privacy_row.addStretch(1)
        form.addRow("Privacy", privacy_row)
        layout.addLayout(form)

        drop_hint = QLabel("Drop .xlsx, .xlsm, or UTF-8 .csv files below")
        drop_hint.setObjectName("sectionTitle")
        layout.addWidget(drop_hint)
        self.file_list = QListWidget()
        self.file_list.setObjectName("sourceFileList")
        self.file_list.setMaximumHeight(110)
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setAccessibleName("Selected source files")
        layout.addWidget(self.file_list)

        buttons = QHBoxLayout()
        self.browse_button = QPushButton("Browse files")
        self.browse_button.clicked.connect(self._browse)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.clicked.connect(self._remove_selected)
        self.analyse_button = QPushButton("Analyse job safely")
        self.analyse_button.setObjectName("primaryButton")
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
        status_row = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setObjectName("analysisStatus")
        self.status_label.setWordWrap(True)
        self.diagnostic_button = QPushButton("Diagnostic details")
        self.diagnostic_button.setVisible(False)
        self.diagnostic_button.clicked.connect(self._show_diagnostic)
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.diagnostic_button)
        layout.addLayout(status_row)

        self.model = AnalysisTableModel()
        self.results = QTableView()
        self.results.setObjectName("analysisResults")
        self.results.setModel(self.model)
        self.results.setAlternatingRowColors(True)
        self.results.horizontalHeader().setStretchLastSection(True)
        self.results.setVisible(False)
        layout.addWidget(self.results, 2)
        self.continue_button = QPushButton("Review operation plan")
        self.continue_button.setObjectName("primaryButton")
        self.continue_button.setVisible(False)
        self.continue_button.clicked.connect(self._continue_to_plan)
        layout.addWidget(self.continue_button)

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(self._paths)

    def _invalidate_analysis(self) -> None:
        self._profiles = ()
        self.model.set_profiles(())
        self.results.setVisible(False)
        self.continue_button.setVisible(False)

    def add_files(self, paths: tuple[Path, ...]) -> None:
        if self._analysis_running:
            self.status_label.setText(
                "Analysis is already running. Wait for it to finish or cancel before changing "
                "the source files."
            )
            return
        existing = {str(path).casefold() for path in self._paths}
        rejected = 0
        added = 0
        for path in paths:
            resolved = path.resolve()
            if resolved.suffix.casefold() not in SUPPORTED_SUFFIXES or not resolved.is_file():
                rejected += 1
                continue
            if str(resolved).casefold() not in existing:
                self._paths.append(resolved)
                item_name = f"{resolved.name}  —  {resolved.parent}"
                self.file_list.addItem(item_name)
                existing.add(str(resolved).casefold())
                added += 1
                if not self.output_directory.text().strip():
                    self.output_directory.setText(str(resolved.parent))
        if added:
            self._invalidate_analysis()
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

    def _browse_output_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if selected:
            self.output_directory.setText(selected)

    def _remove_selected(self) -> None:
        selected_rows = sorted(
            {index.row() for index in self.file_list.selectedIndexes()}, reverse=True
        )
        for row in selected_rows:
            self.file_list.takeItem(row)
            del self._paths[row]
        if selected_rows:
            self._invalidate_analysis()

    def _set_running(self, running: bool) -> None:
        changed = self._analysis_running != running
        self._analysis_running = running
        self.setAcceptDrops(not running)
        for widget in (
            self.browse_button,
            self.remove_button,
            self.analyse_button,
            self.output_browse,
        ):
            widget.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.progress.setVisible(running)
        if changed:
            self.busy_changed.emit(running)

    def _validate_intake(self) -> bool:
        if not self.job_name.text().strip():
            self.status_label.setText("Enter a job name before analysis.")
            return False
        if not self.instructions.toPlainText().strip():
            self.status_label.setText("Paste the client instructions before analysis.")
            return False
        if not self._paths:
            self.status_label.setText("Add at least one supported source file before analysis.")
            return False
        if not self.output_name.text().strip():
            self.status_label.setText("Enter a safe output file name.")
            return False
        directory = Path(self.output_directory.text().strip()).expanduser()
        if not directory.is_dir():
            self.status_label.setText("Choose an existing output folder.")
            return False
        deadline_text = self.deadline.text().strip()
        if deadline_text:
            try:
                date.fromisoformat(deadline_text)
            except ValueError:
                self.status_label.setText("Deadline must use YYYY-MM-DD format.")
                return False
        return True

    def start_analysis(self) -> None:
        if not self._validate_intake():
            return
        self._set_running(True)
        self._invalidate_analysis()
        self._diagnostic = ""
        self.diagnostic_button.setVisible(False)
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
        self._profiles = typed_profiles
        self.model.set_profiles(typed_profiles)
        self.results.resizeColumnsToContents()
        self.results.setVisible(True)
        warning_count = sum(profile.warning_count for profile in typed_profiles)
        self.status_label.setText(
            f"Analysed {len(typed_profiles)} file(s) safely · {warning_count} warning(s) to review."
        )
        self._set_running(False)
        self.continue_button.setVisible(True)
        self.analysis_completed.emit(typed_profiles)

    def _on_failed(self, code: str, message: str) -> None:
        self._set_running(False)
        self._diagnostic = f"Diagnostic code: {code}\nWorker: AnalysisWorker"
        self.diagnostic_button.setVisible(True)
        self.status_label.setText(message)

    def _on_cancelled(self) -> None:
        self._set_running(False)
        self.status_label.setText("Analysis cancelled safely. Source files were not changed.")

    def _show_diagnostic(self) -> None:
        QMessageBox.information(self, "Diagnostic details", self._diagnostic)

    def _continue_to_plan(self) -> None:
        if not self._profiles or not self._validate_intake():
            return
        deadline_text = self.deadline.text().strip()
        try:
            output_format = OutputFormat(self.output_format.currentData())
            privacy_mode = PrivacyMode(self.privacy_mode.currentData())
        except (TypeError, ValueError):
            self.status_label.setText("The selected output or privacy mode is invalid.")
            return
        draft = JobDraft(
            job_name=self.job_name.text().strip(),
            instructions=self.instructions.toPlainText().strip(),
            output_format=output_format,
            output_name=self.output_name.text().strip(),
            output_directory=Path(self.output_directory.text().strip()).resolve(),
            deadline=date.fromisoformat(deadline_text) if deadline_text else None,
            preserve_formatting=self.preserve_formatting.isChecked(),
            privacy=PrivacyMetadata(mode=privacy_mode),
            profiles=self._profiles,
        )
        self.job_ready.emit(draft)

    def reset_job(self) -> None:
        if self._analysis_running:
            self.status_label.setText(
                "Cancel the active analysis before starting or loading another job."
            )
            return
        self._paths.clear()
        self.file_list.clear()
        self.job_name.clear()
        self.instructions.clear()
        self.deadline.clear()
        self.output_name.setText("processed_output")
        self.output_directory.clear()
        self.status_label.clear()
        self._invalidate_analysis()

    def prepare_for_workflow(
        self,
        *,
        name: str,
        description: str,
        output_format: OutputFormat,
        output_name: str,
        preserve_formatting: bool,
        expected_source_count: int,
        default_output_directory: Path | None = None,
    ) -> None:
        """Reset source state while retaining a concrete reusable workflow intent."""
        self.reset_job()
        self.job_name.setText(f"{name} repeat")
        request = description.strip() or f"Run the saved workflow '{name}' with new files."
        self.instructions.setPlainText(request)
        format_index = self.output_format.findData(output_format)
        if format_index >= 0:
            self.output_format.setCurrentIndex(format_index)
        self.output_name.setText(output_name)
        self.preserve_formatting.setChecked(preserve_formatting)
        if default_output_directory is not None and default_output_directory.is_dir():
            self.output_directory.setText(str(default_output_directory))
        self.status_label.setText(
            f"Saved workflow loaded. Add exactly {expected_source_count} new source file(s), "
            "then analyse them before reviewing the rebound plan."
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if (
            not self._analysis_running
            and event.mimeData().hasUrls()
            and all(url.isLocalFile() for url in event.mimeData().urls())
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if self._analysis_running:
            self.status_label.setText(
                "Analysis is already running. Wait for it to finish or cancel before changing "
                "the source files."
            )
            event.ignore()
            return
        self.add_files(tuple(Path(url.toLocalFile()) for url in event.mimeData().urls()))
        event.acceptProposedAction()
