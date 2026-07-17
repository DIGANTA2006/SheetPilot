"""Strict, privacy-conscious models for local metadata persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sheetpilot.core.plan_schema import (
    OperationPlan,
    OutputSettings,
    PlanStep,
    PrivacyMetadata,
    SourceReference,
    StepTarget,
    ValidationRule,
)


def utc_now() -> datetime:
    """Return a timezone-aware timestamp suitable for durable records."""
    return datetime.now(UTC)


class StorageModel(BaseModel):
    """Base for storage payloads; extra keys are never silently accepted."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class JobStatus(StrEnum):
    QUEUED = "queued"
    ANALYSING = "analysing"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ValidationResult(StrEnum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    PASSED_WITH_WARNINGS = "passed_with_warnings"
    FAILED = "failed"


class FileHistoryMetadata(StorageModel):
    """A file identity without its directory or any spreadsheet contents."""

    file_name: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("file_name")
    @classmethod
    def require_plain_file_name(cls, value: str) -> str:
        if (
            value in {".", ".."}
            or "\x00" in value
            or PurePosixPath(value).name != value
            or PureWindowsPath(value).name != value
        ):
            raise ValueError("file history records accept a file name, not a path")
        return value


class AuditHistoryMetadata(FileHistoryMetadata):
    """Aggregate audit artifact identity; audit contents stay in the report file."""


class JobHistoryRecord(StorageModel):
    job_id: UUID
    name: str = Field(min_length=1, max_length=200)
    status: JobStatus
    created_at: datetime
    completed_at: datetime | None = None
    source_files: tuple[FileHistoryMetadata, ...] = Field(min_length=1)
    output_file: FileHistoryMetadata | None = None
    workflow_id: UUID | None = None
    step_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    validation_result: ValidationResult = ValidationResult.NOT_RUN
    backup_path: str | None = Field(default=None, max_length=4096)
    audit: AuditHistoryMetadata | None = None
    application_version: str = Field(min_length=1, max_length=50)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def require_terminal_evidence(self) -> JobHistoryRecord:
        terminal = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
        if self.status in terminal and self.completed_at is None:
            raise ValueError("terminal jobs require a completion timestamp")
        if self.status == JobStatus.SUCCEEDED and self.output_file is None:
            raise ValueError("successful jobs require output metadata")
        if self.status != JobStatus.SUCCEEDED and self.output_file is not None:
            raise ValueError("only successful jobs may publish output metadata")
        return self


class ValidationSummary(StorageModel):
    """Aggregate-only validation history; offending values are deliberately absent."""

    validation_id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    passed: bool
    checks_run: int = Field(ge=0)
    error_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    affected_row_count: int = Field(ge=0)
    issue_code_counts: dict[str, int] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("issue_code_counts")
    @classmethod
    def validate_issue_counts(cls, value: dict[str, int]) -> dict[str, int]:
        for code, count in value.items():
            if not code or len(code) > 100 or count < 0:
                raise ValueError("validation issue counts must use bounded codes and counts")
        return value


class SettingKey(StrEnum):
    LOCAL_ONLY = "local_only"
    DEFAULT_OUTPUT_DIRECTORY = "default_output_directory"
    HISTORY_RETENTION_DAYS = "history_retention_days"
    RECENT_JOB_LIMIT = "recent_job_limit"
    THEME = "theme"


SettingValue = bool | int | str


class SettingRecord(StorageModel):
    key: SettingKey
    value: SettingValue
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_value_for_key(self) -> SettingRecord:
        value = self.value
        if self.key == SettingKey.LOCAL_ONLY and type(value) is not bool:
            raise ValueError("local_only must be a boolean")
        if self.key == SettingKey.DEFAULT_OUTPUT_DIRECTORY and (
            not isinstance(value, str) or not value or len(value) > 4096
        ):
            raise ValueError("default_output_directory must be a non-empty path string")
        if self.key == SettingKey.HISTORY_RETENTION_DAYS and (
            type(value) is not int or not 1 <= value <= 3650
        ):
            raise ValueError("history_retention_days must be between 1 and 3650")
        if self.key == SettingKey.RECENT_JOB_LIMIT and (
            type(value) is not int or not 1 <= value <= 500
        ):
            raise ValueError("recent_job_limit must be between 1 and 500")
        if self.key == SettingKey.THEME and value not in {"light", "dark", "system"}:
            raise ValueError("theme must be light, dark, or system")
        return self


DEFAULT_SETTINGS: dict[SettingKey, SettingValue] = {
    SettingKey.LOCAL_ONLY: True,
    SettingKey.HISTORY_RETENTION_DAYS: 365,
    SettingKey.RECENT_JOB_LIMIT: 100,
    SettingKey.THEME: "light",
}


class TemplateParameterKind(StrEnum):
    TEXT = "text"
    SHEET = "sheet"
    COLUMN = "column"
    COLUMNS = "columns"
    MAPPING = "mapping"
    OUTPUT_NAME = "output_name"


class TemplateBindingLocation(StrEnum):
    OUTPUT_NAME = "output_name"
    STEP_PARAMETER = "step_parameter"
    STEP_SHEET = "step_sheet"
    STEP_COLUMNS = "step_columns"
    VALIDATION_PARAMETER = "validation_parameter"


class TemplateParameterBinding(StorageModel):
    location: TemplateBindingLocation
    step_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")
    validation_index: int | None = Field(default=None, ge=0)
    parameter_name: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_location_selector(self) -> TemplateParameterBinding:
        if self.location in {
            TemplateBindingLocation.STEP_PARAMETER,
            TemplateBindingLocation.STEP_SHEET,
            TemplateBindingLocation.STEP_COLUMNS,
        }:
            if self.step_id is None:
                raise ValueError("step bindings require step_id")
        elif self.step_id is not None:
            raise ValueError("step_id is only valid for step bindings")
        if self.location == TemplateBindingLocation.STEP_PARAMETER:
            if self.parameter_name is None:
                raise ValueError("step parameter bindings require parameter_name")
        elif self.location == TemplateBindingLocation.VALIDATION_PARAMETER:
            if self.validation_index is None or self.parameter_name is None:
                raise ValueError("validation bindings require an index and parameter_name")
        elif self.validation_index is not None or self.parameter_name is not None:
            raise ValueError("this binding does not accept a parameter selector")
        return self


class WorkflowParameter(StorageModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=120)
    kind: TemplateParameterKind
    binding: TemplateParameterBinding
    required: bool = False
    default_value: Any = None


class WorkflowSourceSlot(StorageModel):
    slot_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=120)
    expected_sheet_names: tuple[str, ...] = ()


class WorkflowStep(StorageModel):
    step_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    operation: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.-]+$")
    enabled: bool = True
    parameters: dict[str, Any] = Field(default_factory=dict)
    source_slot: str
    sheet: str | None = None
    columns: list[str] = Field(default_factory=list)
    risk_level: str
    destructive: bool = False
    confirmation_required: bool = False
    explanation: str = Field(min_length=1, max_length=1000)
    estimated_affected_rows: int | None = Field(default=None, ge=0)
    depends_on: list[str] = Field(default_factory=list)


class WorkflowTemplate(StorageModel):
    """A reusable plan blueprint that is never bound to a client file identity."""

    schema_version: Literal["1.0"] = "1.0"
    workflow_id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    source_slots: tuple[WorkflowSourceSlot, ...] = Field(min_length=1)
    steps: tuple[WorkflowStep, ...]
    validations: tuple[ValidationRule, ...] = ()
    output: OutputSettings
    privacy: PrivacyMetadata = Field(default_factory=PrivacyMetadata)
    parameters: tuple[WorkflowParameter, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_blueprint_references(self) -> WorkflowTemplate:
        slot_ids = [slot.slot_id for slot in self.source_slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("workflow source slot IDs must be unique")
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("workflow step IDs must be unique")
        if any(step.source_slot not in slot_ids for step in self.steps):
            raise ValueError("workflow steps must reference a declared source slot")
        parameter_keys = [parameter.key for parameter in self.parameters]
        if len(parameter_keys) != len(set(parameter_keys)):
            raise ValueError("workflow parameter keys must be unique")
        step_set = set(step_ids)
        for parameter in self.parameters:
            binding = parameter.binding
            if binding.step_id is not None and binding.step_id not in step_set:
                raise ValueError("workflow parameter refers to an unknown step")
            if binding.validation_index is not None and binding.validation_index >= len(
                self.validations
            ):
                raise ValueError("workflow parameter refers to an unknown validation")
        return self

    @classmethod
    def from_plan(
        cls,
        plan: OperationPlan,
        *,
        name: str,
        description: str = "",
        parameters: tuple[WorkflowParameter, ...] | None = None,
    ) -> WorkflowTemplate:
        """Remove file identities from a validated plan and create source slots."""
        source_slots = tuple(
            WorkflowSourceSlot(
                slot_id=f"source_{index}",
                label=f"Input {index}",
                expected_sheet_names=tuple(source.sheet_names),
            )
            for index, source in enumerate(plan.source_files, start=1)
        )
        source_map = {
            source.source_id: slot.slot_id
            for source, slot in zip(plan.source_files, source_slots, strict=True)
        }
        steps = tuple(
            WorkflowStep(
                step_id=step.step_id,
                operation=step.operation,
                enabled=step.enabled,
                parameters=step.parameters,
                source_slot=source_map[step.target.source_id],
                sheet=step.target.sheet,
                columns=step.target.columns,
                risk_level=step.risk_level.value,
                destructive=step.destructive,
                confirmation_required=step.confirmation_required,
                explanation=step.explanation,
                estimated_affected_rows=step.estimated_affected_rows,
                depends_on=step.depends_on,
            )
            for step in plan.steps
        )
        resolved_parameters = parameters or cls._default_parameters(steps)
        return cls(
            name=name,
            description=description,
            source_slots=source_slots,
            steps=steps,
            validations=tuple(plan.validations),
            output=plan.output,
            privacy=plan.privacy,
            parameters=resolved_parameters,
        )

    @staticmethod
    def _default_parameters(steps: tuple[WorkflowStep, ...]) -> tuple[WorkflowParameter, ...]:
        parameters: list[WorkflowParameter] = [
            WorkflowParameter(
                key="output_name",
                label="Output name",
                kind=TemplateParameterKind.OUTPUT_NAME,
                binding=TemplateParameterBinding(location=TemplateBindingLocation.OUTPUT_NAME),
            )
        ]
        for step in steps:
            key_prefix = step.step_id.casefold().replace("-", "_")
            if step.sheet is not None:
                parameters.append(
                    WorkflowParameter(
                        key=f"{key_prefix}_input_sheet",
                        label=f"{step.step_id} input sheet",
                        kind=TemplateParameterKind.SHEET,
                        binding=TemplateParameterBinding(
                            location=TemplateBindingLocation.STEP_SHEET,
                            step_id=step.step_id,
                        ),
                    )
                )
            if step.columns:
                parameters.append(
                    WorkflowParameter(
                        key=f"{key_prefix}_target_columns",
                        label=f"{step.step_id} target columns",
                        kind=TemplateParameterKind.COLUMNS,
                        binding=TemplateParameterBinding(
                            location=TemplateBindingLocation.STEP_COLUMNS,
                            step_id=step.step_id,
                        ),
                    )
                )
        return tuple(parameters)

    def instantiate(
        self,
        sources: dict[str, SourceReference],
        parameter_values: dict[str, Any] | None = None,
        *,
        job_name: str | None = None,
    ) -> OperationPlan:
        """Bind new files and typed parameter values to a fresh operation plan."""
        expected_slots = {slot.slot_id for slot in self.source_slots}
        if set(sources) != expected_slots:
            missing = sorted(expected_slots - set(sources))
            unexpected = sorted(set(sources) - expected_slots)
            raise ValueError(
                f"source slots do not match (missing={missing}, unexpected={unexpected})"
            )
        values = parameter_values or {}
        definitions = {parameter.key: parameter for parameter in self.parameters}
        unknown = sorted(set(values) - set(definitions))
        if unknown:
            raise ValueError(f"unknown workflow parameters: {', '.join(unknown)}")

        step_payloads = [step.model_dump(mode="python") for step in self.steps]
        validation_payloads = [rule.model_dump(mode="python") for rule in self.validations]
        output_payload = self.output.model_dump(mode="python")
        for key, definition in definitions.items():
            if key in values:
                value = values[key]
            elif definition.default_value is not None:
                value = definition.default_value
            elif definition.required:
                raise ValueError(f"required workflow parameter was not supplied: {key}")
            else:
                continue
            self._validate_parameter_value(definition.kind, value)
            binding = definition.binding
            if binding.location == TemplateBindingLocation.OUTPUT_NAME:
                output_payload["output_name"] = value
                continue
            if binding.step_id is not None:
                step = next(item for item in step_payloads if item["step_id"] == binding.step_id)
                if binding.location == TemplateBindingLocation.STEP_PARAMETER:
                    step["parameters"][binding.parameter_name] = value
                elif binding.location == TemplateBindingLocation.STEP_SHEET:
                    step["sheet"] = value
                elif binding.location == TemplateBindingLocation.STEP_COLUMNS:
                    step["columns"] = value
                continue
            if binding.location == TemplateBindingLocation.VALIDATION_PARAMETER:
                index = binding.validation_index
                if index is None:
                    raise ValueError("validation binding index is missing")
                validation_payloads[index]["parameters"][binding.parameter_name] = value

        plan_steps = [
            PlanStep(
                step_id=step["step_id"],
                operation=step["operation"],
                enabled=step["enabled"],
                parameters=step["parameters"],
                target=StepTarget(
                    source_id=sources[step["source_slot"]].source_id,
                    sheet=step["sheet"],
                    columns=step["columns"],
                ),
                risk_level=step["risk_level"],
                destructive=step["destructive"],
                confirmation_required=step["confirmation_required"],
                explanation=step["explanation"],
                estimated_affected_rows=step["estimated_affected_rows"],
                depends_on=step["depends_on"],
            )
            for step in step_payloads
        ]
        ordered_sources = [sources[slot.slot_id] for slot in self.source_slots]
        return OperationPlan(
            job_name=job_name or self.name,
            source_files=ordered_sources,
            steps=plan_steps,
            validations=[ValidationRule.model_validate(item) for item in validation_payloads],
            output=OutputSettings.model_validate(output_payload),
            privacy=self.privacy,
        )

    @staticmethod
    def _validate_parameter_value(kind: TemplateParameterKind, value: Any) -> None:
        if kind in {
            TemplateParameterKind.TEXT,
            TemplateParameterKind.SHEET,
            TemplateParameterKind.COLUMN,
            TemplateParameterKind.OUTPUT_NAME,
        }:
            if not isinstance(value, str) or not value:
                raise ValueError(f"{kind.value} parameters require a non-empty string")
        elif kind == TemplateParameterKind.COLUMNS:
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(item, str) and item for item in value)
            ):
                raise ValueError("columns parameters require a non-empty string list")
        elif kind == TemplateParameterKind.MAPPING and (
            not isinstance(value, dict) or not all(isinstance(key, str) for key in value)
        ):
            raise ValueError("mapping parameters require an object with string keys")
