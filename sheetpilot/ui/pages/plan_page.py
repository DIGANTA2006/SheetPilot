"""Registered-operation plan builder and strict JSON review page."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sheetpilot.core.exceptions import SheetPilotError
from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.core.plan_schema import OperationPlan, PlanStep, RiskLevel, StepTarget
from sheetpilot.core.plan_validator import PlanValidator
from sheetpilot.ui.workflow_models import PreparedJob


class PlanPage(QWidget):
    """Allow only schema-validated data plans backed by registered operations."""

    back_requested = Signal()
    preview_requested = Signal(object)

    def __init__(self, registry: OperationRegistry) -> None:
        super().__init__()
        self._registry = registry
        self._prepared: PreparedJob | None = None
        self._diagnostic = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        title = QLabel("Plan · Review deterministic operations")
        title.setObjectName("title")
        subtitle = QLabel(
            "Only allowlisted operations and validated JSON parameters can reach execution."
        )
        subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        self.instruction_summary = QLabel()
        self.instruction_summary.setWordWrap(True)
        self.instruction_summary.setObjectName("instructionCard")
        layout.addWidget(self.instruction_summary)

        splitter = QSplitter()
        builder = QGroupBox("Add a registered operation")
        builder_layout = QVBoxLayout(builder)
        form = QFormLayout()
        self.operation = QComboBox()
        self.operation.addItems(self._registry.names())
        self.operation.currentTextChanged.connect(self._operation_changed)
        form.addRow("Operation", self.operation)
        self.source = QComboBox()
        self.source.currentIndexChanged.connect(self._source_changed)
        form.addRow("Source", self.source)
        self.sheet = QComboBox()
        form.addRow("Sheet", self.sheet)
        self.target_columns = QLineEdit()
        self.target_columns.setPlaceholderText("Optional comma-separated review metadata")
        form.addRow("Target columns", self.target_columns)
        self.explanation = QLineEdit()
        self.explanation.setPlaceholderText("Why this deterministic step is needed")
        form.addRow("Explanation", self.explanation)
        builder_layout.addLayout(form)
        builder_layout.addWidget(QLabel("Parameters (JSON data only)"))
        self.parameters = QPlainTextEdit("{}")
        self.parameters.setMaximumHeight(120)
        builder_layout.addWidget(self.parameters)
        self.schema_help = QPlainTextEdit()
        self.schema_help.setReadOnly(True)
        self.schema_help.setAccessibleName("Selected operation parameter schema")
        builder_layout.addWidget(self.schema_help, 1)
        self.add_step = QPushButton("Validate and add step")
        self.add_step.clicked.connect(self._add_step)
        builder_layout.addWidget(self.add_step)
        splitter.addWidget(builder)

        review = QWidget()
        review_layout = QVBoxLayout(review)
        review_layout.setContentsMargins(10, 0, 0, 0)
        review_layout.addWidget(QLabel("Plan steps"))
        self.steps_table = QTableWidget(0, 5)
        self.steps_table.setHorizontalHeaderLabels(
            ["Step", "Operation", "Target", "Risk", "Enabled"]
        )
        self.steps_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.steps_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.steps_table.setMaximumHeight(150)
        review_layout.addWidget(self.steps_table)
        review_layout.addWidget(QLabel("Validated plan JSON (review or edit)"))
        self.plan_editor = QPlainTextEdit()
        self.plan_editor.setAccessibleName("Operation plan JSON")
        review_layout.addWidget(self.plan_editor, 1)
        self.plan_status = QLabel()
        self.plan_status.setWordWrap(True)
        review_layout.addWidget(self.plan_status)
        splitter.addWidget(review)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        back = QPushButton("Back to analysis")
        back.clicked.connect(self.back_requested)
        self.diagnostic_button = QPushButton("Diagnostic details")
        self.diagnostic_button.setVisible(False)
        self.diagnostic_button.clicked.connect(self._show_diagnostic)
        validate = QPushButton("Validate plan")
        validate.clicked.connect(self.validate_plan)
        self.preview_button = QPushButton("Generate safe preview")
        self.preview_button.setObjectName("primaryButton")
        self.preview_button.clicked.connect(self._request_preview)
        buttons.addWidget(back)
        buttons.addWidget(self.diagnostic_button)
        buttons.addStretch(1)
        buttons.addWidget(validate)
        buttons.addWidget(self.preview_button)
        layout.addLayout(buttons)
        self._operation_changed(self.operation.currentText())

    def load_job(self, prepared: PreparedJob) -> None:
        self._prepared = prepared
        self.instruction_summary.setText(
            f"Client request: {prepared.draft.instructions}\n"
            "Planning mode: local/manual — no client data will be uploaded."
        )
        self.source.clear()
        for source in prepared.plan.source_files:
            self.source.addItem(source.file_name, str(source.source_id))
        self.plan_editor.setPlainText(prepared.plan.model_dump_json(indent=2))
        self._source_changed(self.source.currentIndex())
        self._refresh_steps(prepared.plan)
        self.plan_status.setText(
            "Analysis is complete. Add at least one operation, then validate the plan."
        )
        self.diagnostic_button.setVisible(False)

    def _operation_changed(self, name: str) -> None:
        if not name:
            self.schema_help.clear()
            return
        operation = self._registry.get(name)
        schema = operation.parameters_model.model_json_schema()
        self.schema_help.setPlainText(json.dumps(schema, indent=2, ensure_ascii=False))
        first_column = self._first_selected_column()
        if name == "text.clean" and first_column:
            example: dict[str, Any] = {
                "columns": [first_column],
                "actions": [{"kind": "trim"}],
            }
            self.parameters.setPlainText(json.dumps(example, indent=2))
            self.target_columns.setText(first_column)
            self.explanation.setText("Trim surrounding whitespace")
        else:
            self.parameters.setPlainText("{}")

    def _source_changed(self, index: int) -> None:
        self.sheet.clear()
        if self._prepared is None or index < 0:
            return
        source_id = self.source.itemData(index)
        source = next(
            (item for item in self._prepared.plan.source_files if str(item.source_id) == source_id),
            None,
        )
        if source is not None:
            self.sheet.addItems(source.sheet_names)
        self._operation_changed(self.operation.currentText())

    def _first_selected_column(self) -> str | None:
        if self._prepared is None or self.source.currentIndex() < 0:
            return None
        source_index = self.source.currentIndex()
        if source_index >= len(self._prepared.draft.profiles):
            return None
        profile = self._prepared.draft.profiles[source_index]
        sheet_name = self.sheet.currentText()
        sheet = next((item for item in profile.sheets if item.name == sheet_name), None)
        return sheet.headers[0] if sheet is not None and sheet.headers else None

    def _parse_json_object(self) -> dict[str, Any]:
        try:
            value = json.loads(self.parameters.toPlainText())
        except json.JSONDecodeError as error:
            raise ValueError(f"Parameters are not valid JSON: {error.msg}") from error
        if not isinstance(value, dict):
            raise ValueError("Operation parameters must be one JSON object.")
        return value

    def _add_step(self) -> None:
        if self._prepared is None:
            return
        try:
            plan = self._parse_plan(validate=False)
            parameters = self._parse_json_object()
            operation_name = self.operation.currentText()
            operation = self._registry.get(operation_name)
            typed_parameters = self._registry.validate_parameters(operation_name, parameters)
            destructive = operation.is_destructive(typed_parameters)
            explanation = self.explanation.text().strip()
            if not explanation:
                raise ValueError("Enter a concise explanation for this step.")
            source_id = UUID(str(self.source.currentData()))
            existing = {step.step_id for step in plan.steps}
            number = len(plan.steps) + 1
            step_id = f"step_{number}"
            while step_id in existing:
                number += 1
                step_id = f"step_{number}"
            columns = [
                item.strip() for item in self.target_columns.text().split(",") if item.strip()
            ]
            step = PlanStep(
                step_id=step_id,
                operation=operation_name,
                parameters=parameters,
                target=StepTarget(
                    source_id=source_id,
                    sheet=self.sheet.currentText() or None,
                    columns=columns,
                ),
                risk_level=RiskLevel.HIGH if destructive else RiskLevel.LOW,
                destructive=destructive,
                confirmation_required=destructive,
                explanation=explanation,
                depends_on=[plan.steps[-1].step_id] if plan.steps else [],
            )
            updated = plan.model_copy(update={"steps": [*plan.steps, step]})
            updated = OperationPlan.model_validate(updated.model_dump())
            PlanValidator(self._registry).validate(updated)
        except (SheetPilotError, ValidationError, ValueError) as error:
            self._show_error("The operation was not added.", error)
            return
        self.plan_editor.setPlainText(updated.model_dump_json(indent=2))
        self._refresh_steps(updated)
        self.plan_status.setText(f"Added {step.step_id}: {step.operation}.")

    def _ensure_source_binding(self, plan: OperationPlan) -> None:
        if self._prepared is None:
            raise ValueError("No analysed job is loaded.")
        expected = {
            source.source_id: (source.file_name, source.sha256, tuple(source.sheet_names))
            for source in self._prepared.plan.source_files
        }
        actual = {
            source.source_id: (source.file_name, source.sha256, tuple(source.sheet_names))
            for source in plan.source_files
        }
        if actual != expected:
            raise ValueError("Plan sources must remain exactly bound to the analysed files.")
        for step in plan.steps:
            source = next(
                item for item in plan.source_files if item.source_id == step.target.source_id
            )
            if step.target.sheet is not None and step.target.sheet not in source.sheet_names:
                raise ValueError(f"Step {step.step_id} targets a sheet not found during analysis.")

    def _parse_plan(self, *, validate: bool = True) -> OperationPlan:
        plan = OperationPlan.model_validate_json(self.plan_editor.toPlainText())
        self._ensure_source_binding(plan)
        if validate:
            enabled = [step for step in plan.steps if step.enabled]
            if not enabled:
                raise ValueError("Add at least one enabled operation before preview.")
            PlanValidator(self._registry).validate(plan)
        return plan

    def validate_plan(self) -> OperationPlan | None:
        try:
            plan = self._parse_plan()
        except (SheetPilotError, ValidationError, ValueError) as error:
            self._show_error("Plan validation failed.", error)
            return None
        self._diagnostic = ""
        self.diagnostic_button.setVisible(False)
        self._refresh_steps(plan)
        destructive = sum(step.enabled and step.destructive for step in plan.steps)
        self.plan_status.setText(
            f"Plan is valid: {sum(step.enabled for step in plan.steps)} enabled step(s), "
            f"{destructive} destructive confirmation(s)."
        )
        return plan

    def _request_preview(self) -> None:
        plan = self.validate_plan()
        if plan is not None:
            self.preview_requested.emit(plan)

    def _refresh_steps(self, plan: OperationPlan) -> None:
        self.steps_table.setRowCount(len(plan.steps))
        names = {source.source_id: source.file_name for source in plan.source_files}
        for row, step in enumerate(plan.steps):
            values = (
                step.step_id,
                step.operation,
                f"{names[step.target.source_id]} · {step.target.sheet or 'default'}",
                step.risk_level.value.title(),
                "Yes" if step.enabled else "No",
            )
            for column, value in enumerate(values):
                self.steps_table.setItem(row, column, QTableWidgetItem(value))

    def _show_error(self, headline: str, error: BaseException) -> None:
        code = error.code if isinstance(error, SheetPilotError) else "invalid_plan_data"
        self._diagnostic = f"Diagnostic code: {code}\nType: {type(error).__name__}"
        self.diagnostic_button.setVisible(True)
        self.plan_status.setText(f"{headline} {error}")

    def _show_diagnostic(self) -> None:
        QMessageBox.information(self, "Diagnostic details", self._diagnostic)
