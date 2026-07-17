"""Local workflow library, history, validation, settings, and help screens."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sheetpilot.app.persistence import PersistenceServices
from sheetpilot.core.exceptions import SheetPilotError
from sheetpilot.storage.models import (
    JobHistoryRecord,
    SettingKey,
    TemplateParameterKind,
    ValidationSummary,
    WorkflowTemplate,
)
from sheetpilot.ui.workflow_models import WorkflowRunRequest


def _configure_table(table: QTableWidget) -> None:
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.horizontalHeader().setStretchLastSection(True)


class WorkflowLibraryPage(QWidget):
    """Browse file-independent workflows and review typed parameters before reuse."""

    workflow_requested = Signal(object)
    library_changed = Signal()

    def __init__(
        self,
        services: PersistenceServices,
        *,
        title: str = "Saved workflows",
        subtitle: str = (
            "Reusable plans contain operation metadata and parameters, never source paths, "
            "hashes, or spreadsheet rows."
        ),
    ) -> None:
        super().__init__()
        self._services = services
        self._templates: tuple[WorkflowTemplate, ...] = ()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        heading = QLabel(title)
        heading.setObjectName("title")
        description = QLabel(subtitle)
        description.setObjectName("subtitle")
        description.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(description)

        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search workflow names and descriptions")
        self.search.setAccessibleName("Workflow search")
        self.search.returnPressed.connect(self.refresh)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(refresh)
        layout.addLayout(search_row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Workflow", "Description", "Inputs", "Steps", "Updated"]
        )
        _configure_table(self.table)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 2)

        parameter_heading = QLabel("Editable parameters for the selected workflow")
        parameter_heading.setObjectName("sectionTitle")
        layout.addWidget(parameter_heading)
        self.parameters = QTableWidget(0, 5)
        self.parameters.setHorizontalHeaderLabels(["Key", "Type", "Required", "Default", "Binding"])
        _configure_table(self.parameters)
        self.parameters.setMaximumHeight(150)
        layout.addWidget(self.parameters)
        self.parameter_values = QPlainTextEdit("{}")
        self.parameter_values.setAccessibleName("Workflow parameter values JSON")
        self.parameter_values.setPlaceholderText(
            "JSON object of reviewed values. Sheet and column values may be changed for new files."
        )
        self.parameter_values.setMaximumHeight(105)
        layout.addWidget(self.parameter_values)

        action_row = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.delete_button = QPushButton("Delete selected")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.clicked.connect(self._delete_selected)
        self.use_button = QPushButton("Use with new files")
        self.use_button.setObjectName("primaryButton")
        self.use_button.clicked.connect(self._use_selected)
        action_row.addWidget(self.status, 1)
        action_row.addWidget(self.delete_button)
        action_row.addWidget(self.use_button)
        layout.addLayout(action_row)
        self._set_selection_actions(False)

    @property
    def selected_template(self) -> WorkflowTemplate | None:
        row = self.table.currentRow()
        return self._templates[row] if 0 <= row < len(self._templates) else None

    def refresh(self) -> None:
        try:
            self._templates = self._services.templates.list(search=self.search.text())
        except (SheetPilotError, ValueError) as error:
            self._templates = ()
            self.table.setRowCount(0)
            self.parameters.setRowCount(0)
            self.parameter_values.setPlainText("{}")
            self._set_selection_actions(False)
            self.status.setText(f"Workflow library could not be loaded: {error}")
            return
        self.table.setRowCount(len(self._templates))
        for row, template in enumerate(self._templates):
            values = (
                template.name,
                template.description or "—",
                len(template.source_slots),
                len(template.steps),
                template.updated_at.astimezone().strftime("%Y-%m-%d %H:%M"),
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        self.parameters.setRowCount(0)
        self.parameter_values.setPlainText("{}")
        self._set_selection_actions(False)
        if self._templates:
            self.table.selectRow(0)
            self.status.setText(f"{len(self._templates)} saved workflow(s) available locally.")
        else:
            self.status.setText("No saved workflows yet. Complete a job, then save its plan.")

    def _set_selection_actions(self, enabled: bool) -> None:
        self.delete_button.setEnabled(enabled)
        self.use_button.setEnabled(enabled)
        self.parameter_values.setEnabled(enabled)

    def _selection_changed(self) -> None:
        template = self.selected_template
        self._set_selection_actions(template is not None)
        if template is None:
            self.parameters.setRowCount(0)
            return
        self.parameters.setRowCount(len(template.parameters))
        defaults: dict[str, Any] = {}
        for row, parameter in enumerate(template.parameters):
            if parameter.default_value is not None:
                defaults[parameter.key] = parameter.default_value
            binding = parameter.binding.location.value.replace("_", " ")
            if parameter.binding.step_id:
                binding = f"{binding}: {parameter.binding.step_id}"
            if parameter.binding.validation_index is not None:
                binding = f"{binding}: rule {parameter.binding.validation_index + 1}"
            values = (
                parameter.key,
                parameter.kind.value,
                "Yes" if parameter.required else "No",
                json.dumps(parameter.default_value, ensure_ascii=False)
                if parameter.default_value is not None
                else "—",
                binding,
            )
            for column, value in enumerate(values):
                self.parameters.setItem(row, column, QTableWidgetItem(value))
        self.parameters.resizeColumnsToContents()
        self.parameter_values.setPlainText(json.dumps(defaults, indent=2, ensure_ascii=False))

    def _parse_parameter_values(self, template: WorkflowTemplate) -> dict[str, Any]:
        try:
            value = json.loads(self.parameter_values.toPlainText() or "{}")
        except json.JSONDecodeError as error:
            raise ValueError(f"Parameter values are not valid JSON: {error.msg}") from error
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise ValueError("Workflow parameters must be one JSON object.")
        definitions = {parameter.key: parameter for parameter in template.parameters}
        unknown = sorted(set(value) - set(definitions))
        if unknown:
            raise ValueError(f"Unknown workflow parameters: {', '.join(unknown)}")
        missing = sorted(
            parameter.key
            for parameter in template.parameters
            if parameter.required and parameter.key not in value and parameter.default_value is None
        )
        if missing:
            raise ValueError(f"Required workflow parameters are missing: {', '.join(missing)}")
        for key, supplied in value.items():
            self._validate_parameter_type(definitions[key].kind, supplied)
        return value

    @staticmethod
    def _validate_parameter_type(kind: TemplateParameterKind, value: Any) -> None:
        string_kinds = {
            TemplateParameterKind.TEXT,
            TemplateParameterKind.SHEET,
            TemplateParameterKind.COLUMN,
            TemplateParameterKind.OUTPUT_NAME,
        }
        if kind in string_kinds and (not isinstance(value, str) or not value):
            raise ValueError(f"{kind.value} parameters require a non-empty string.")
        if kind == TemplateParameterKind.COLUMNS and (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item for item in value)
        ):
            raise ValueError("columns parameters require a non-empty string list.")
        if kind == TemplateParameterKind.MAPPING and (
            not isinstance(value, dict) or not all(isinstance(key, str) for key in value)
        ):
            raise ValueError("mapping parameters require an object with string keys.")

    def _use_selected(self) -> None:
        template = self.selected_template
        if template is None:
            return
        try:
            parameter_values = self._parse_parameter_values(template)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.workflow_requested.emit(
            WorkflowRunRequest(template=template, parameter_values=parameter_values)
        )

    def _delete_selected(self) -> None:
        template = self.selected_template
        if template is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete saved workflow",
            f"Delete '{template.name}'? Job history and completed outputs are not deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            deleted = self._services.templates.delete(template.workflow_id)
        except SheetPilotError as error:
            self.status.setText(f"The workflow was not deleted: {error}")
            return
        if deleted:
            self.library_changed.emit()
            self.refresh()


class JobHistoryPage(QWidget):
    """Display aggregate job evidence without source directories or cell contents."""

    repeat_requested = Signal(object)

    def __init__(self, services: PersistenceServices) -> None:
        super().__init__()
        self._services = services
        self._records: tuple[JobHistoryRecord, ...] = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        title = QLabel("Job history")
        title.setObjectName("title")
        subtitle = QLabel(
            "Only aggregate metadata, file names, hashes, and artifact identities are stored."
        )
        subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                "Created",
                "Job",
                "Status",
                "Sources",
                "Output",
                "Validation",
                "Warnings",
                "Workflow",
            ]
        )
        _configure_table(self.table)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        self.repeat = QPushButton("Repeat selected with new files")
        self.repeat.setObjectName("primaryButton")
        self.repeat.clicked.connect(self._repeat_selected)
        self.repeat.setEnabled(False)
        actions.addWidget(self.status, 1)
        actions.addWidget(refresh)
        actions.addWidget(self.repeat)
        layout.addLayout(actions)

    @property
    def selected_record(self) -> JobHistoryRecord | None:
        row = self.table.currentRow()
        return self._records[row] if 0 <= row < len(self._records) else None

    def refresh(self) -> None:
        try:
            configured_limit = self._services.settings.get(SettingKey.RECENT_JOB_LIMIT)
            limit = configured_limit if type(configured_limit) is int else 100
            self._records = self._services.history.list(limit=limit)
        except (SheetPilotError, ValueError) as error:
            self._records = ()
            self.table.setRowCount(0)
            self.repeat.setEnabled(False)
            self.status.setText(f"Job history could not be loaded: {error}")
            return
        self.table.setRowCount(len(self._records))
        for row, record in enumerate(self._records):
            source_names = ", ".join(item.file_name for item in record.source_files)
            values = (
                record.created_at.astimezone().strftime("%Y-%m-%d %H:%M"),
                record.name,
                record.status.value.replace("_", " ").title(),
                source_names,
                record.output_file.file_name if record.output_file else "—",
                record.validation_result.value.replace("_", " ").title(),
                record.warning_count,
                "Saved" if record.workflow_id else "—",
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        self.repeat.setEnabled(False)
        self.status.setText(
            f"{len(self._records)} job record(s) shown."
            if self._records
            else "No jobs have been recorded yet."
        )

    def _selection_changed(self) -> None:
        record = self.selected_record
        self.repeat.setEnabled(record is not None and record.workflow_id is not None)
        if record is not None and record.workflow_id is None:
            self.status.setText("Save this job as a workflow before repeating it from history.")

    def _repeat_selected(self) -> None:
        record = self.selected_record
        if record is not None and record.workflow_id is not None:
            self.repeat_requested.emit(record.job_id)


class ValidationReportsPage(QWidget):
    """Show aggregate validation history without retaining offending values."""

    def __init__(self, services: PersistenceServices) -> None:
        super().__init__()
        self._services = services
        self._summaries: tuple[ValidationSummary, ...] = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        title = QLabel("Validation reports")
        title.setObjectName("title")
        subtitle = QLabel(
            "Counts and issue codes are retained locally; cell values and representative "
            "rows are not."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Created", "Job ID", "Result", "Checks", "Errors", "Warnings", "Rows", "Codes"]
        )
        _configure_table(self.table)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.status = QLabel()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        row.addWidget(self.status, 1)
        row.addWidget(refresh)
        layout.addLayout(row)

    def refresh(self) -> None:
        try:
            configured_limit = self._services.settings.get(SettingKey.RECENT_JOB_LIMIT)
            limit = configured_limit if type(configured_limit) is int else 100
            self._summaries = self._services.validations.list_recent(limit=limit)
        except (SheetPilotError, ValueError) as error:
            self._summaries = ()
            self.table.setRowCount(0)
            self.status.setText(f"Validation history could not be loaded: {error}")
            return
        self.table.setRowCount(len(self._summaries))
        for row, summary in enumerate(self._summaries):
            codes = ", ".join(
                f"{code}: {count}" for code, count in sorted(summary.issue_code_counts.items())
            )
            values = (
                summary.created_at.astimezone().strftime("%Y-%m-%d %H:%M"),
                str(summary.job_id),
                "Passed" if summary.passed else "Failed",
                summary.checks_run,
                summary.error_count,
                summary.warning_count,
                summary.affected_row_count,
                codes or "None",
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        self.status.setText(
            f"{len(self._summaries)} aggregate validation report(s) shown."
            if self._summaries
            else "No validation reports have been recorded yet."
        )


class SettingsPage(QWidget):
    """Edit allowlisted application settings stored in local SQLite."""

    settings_saved = Signal()

    def __init__(self, services: PersistenceServices) -> None:
        super().__init__()
        self._services = services
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        title = QLabel("Settings")
        title.setObjectName("title")
        subtitle = QLabel(
            "Only allowlisted preferences are stored. Credentials and client spreadsheet "
            "content are not."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        form = QFormLayout()
        self.local_only = QCheckBox("Keep planning and processing local")
        form.addRow("Privacy default", self.local_only)
        output_row = QHBoxLayout()
        self.output_directory = QLineEdit()
        self.output_directory.setPlaceholderText("Optional default output directory")
        browse = QPushButton("Choose folder")
        browse.clicked.connect(self._browse_output_directory)
        output_row.addWidget(self.output_directory, 1)
        output_row.addWidget(browse)
        form.addRow("Default output folder", output_row)
        self.retention_days = QSpinBox()
        self.retention_days.setRange(1, 3650)
        self.retention_days.setSuffix(" days")
        form.addRow("History retention", self.retention_days)
        self.recent_limit = QSpinBox()
        self.recent_limit.setRange(1, 500)
        form.addRow("Records per history view", self.recent_limit)
        self.theme = QComboBox()
        self.theme.addItem("Light", "light")
        form.addRow("Theme", self.theme)
        layout.addLayout(form)
        note = QLabel(
            "Deleting expired history removes local metadata and validation summaries only; "
            "it never deletes client sources, outputs, audits, or backups."
        )
        note.setWordWrap(True)
        note.setObjectName("instructionCard")
        layout.addWidget(note)
        layout.addStretch(1)
        actions = QHBoxLayout()
        self.status = QLabel()
        save = QPushButton("Save settings")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.save)
        actions.addWidget(self.status, 1)
        actions.addWidget(save)
        layout.addLayout(actions)

    def load(self) -> None:
        try:
            values = self._services.settings.all()
        except SheetPilotError as error:
            self.status.setText(f"Settings could not be loaded: {error}")
            return
        self.local_only.setChecked(values.get(SettingKey.LOCAL_ONLY) is not False)
        output = values.get(SettingKey.DEFAULT_OUTPUT_DIRECTORY)
        self.output_directory.setText(output if isinstance(output, str) else "")
        retention = values.get(SettingKey.HISTORY_RETENTION_DAYS)
        self.retention_days.setValue(retention if type(retention) is int else 365)
        limit = values.get(SettingKey.RECENT_JOB_LIMIT)
        self.recent_limit.setValue(limit if type(limit) is int else 100)
        self.theme.setCurrentIndex(0)
        self.status.setText("Settings loaded from this device.")

    def save(self) -> None:
        directory_text = self.output_directory.text().strip()
        if directory_text and not Path(directory_text).expanduser().is_dir():
            self.status.setText("Choose an existing directory or leave the output default blank.")
            return
        try:
            self._services.settings.set(SettingKey.LOCAL_ONLY, self.local_only.isChecked())
            self._services.settings.set(
                SettingKey.HISTORY_RETENTION_DAYS, self.retention_days.value()
            )
            self._services.settings.set(SettingKey.RECENT_JOB_LIMIT, self.recent_limit.value())
            self._services.settings.set(SettingKey.THEME, "light")
            if directory_text:
                self._services.settings.set(
                    SettingKey.DEFAULT_OUTPUT_DIRECTORY,
                    str(Path(directory_text).expanduser().resolve()),
                )
            else:
                self._services.settings.delete(SettingKey.DEFAULT_OUTPUT_DIRECTORY)
        except (SheetPilotError, ValueError) as error:
            self.status.setText(f"Settings could not be saved: {error}")
            return
        self.status.setText("Settings saved locally.")
        self.settings_saved.emit()

    def _browse_output_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose default output folder")
        if selected:
            self.output_directory.setText(selected)


class HelpPage(QWidget):
    """Concise offline operating and recovery guidance."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        title = QLabel("Help")
        title.setObjectName("title")
        subtitle = QLabel("Safe, deterministic spreadsheet workflows—available offline.")
        subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        guidance = QLabel(
            "<h3>Run a job</h3>"
            "<ol><li>Add .xlsx, .xlsm, or UTF-8 .csv sources.</li>"
            "<li>Review read-only analysis warnings.</li>"
            "<li>Build or load only registered operations.</li>"
            "<li>Review the preview and explicitly approve destructive steps.</li>"
            "<li>Check reconciliation, hashes, backups, output, and the JSON audit.</li></ol>"
            "<h3>Recovery</h3>"
            "Sources are never overwritten. Restore always writes a verified backup to a new "
            "path. Failed artifacts are segregated and are never labelled complete."
            "<h3>Privacy and limits</h3>"
            "Local mode sends nothing to an AI service. Macros are detected and never run "
            "automatically. Legacy .xls, OCR, scanned PDFs, browser forms, and arbitrary code, "
            "SQL, VBA, PowerShell, or shell commands are outside the supported workflow."
        )
        guidance.setWordWrap(True)
        guidance.setTextFormat(guidance.textFormat())
        guidance.setTextInteractionFlags(guidance.textInteractionFlags())
        layout.addWidget(guidance)
        layout.addStretch(1)
