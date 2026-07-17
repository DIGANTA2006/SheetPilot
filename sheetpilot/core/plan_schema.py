"""Versioned, strict operation-plan models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Base model that rejects AI- or user-supplied unknown fields."""

    model_config = ConfigDict(extra="forbid")


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PrivacyMode(StrEnum):
    LOCAL = "local"
    AI_ASSISTED = "ai_assisted"


class OutputFormat(StrEnum):
    XLSX = "xlsx"
    CSV = "csv"


class SourceReference(StrictModel):
    source_id: UUID = Field(default_factory=uuid4)
    file_name: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sheet_names: list[str] = Field(default_factory=list)


class StepTarget(StrictModel):
    source_id: UUID
    sheet: str | None = None
    columns: list[str] = Field(default_factory=list)


class PlanStep(StrictModel):
    step_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    operation: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.-]+$")
    enabled: bool = True
    parameters: dict[str, Any] = Field(default_factory=dict)
    target: StepTarget
    risk_level: RiskLevel = RiskLevel.LOW
    destructive: bool = False
    confirmation_required: bool = False
    explanation: str = Field(min_length=1, max_length=1000)
    estimated_affected_rows: int | None = Field(default=None, ge=0)
    depends_on: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_confirmation_for_destructive_step(self) -> PlanStep:
        if self.destructive and not self.confirmation_required:
            raise ValueError("destructive steps must require confirmation")
        return self


class ValidationRule(StrictModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.-]+$")
    parameters: dict[str, Any] = Field(default_factory=dict)


class OutputSettings(StrictModel):
    output_name: str = Field(min_length=1, max_length=180)
    format: OutputFormat
    preserve_formatting: bool = True


class PrivacyMetadata(StrictModel):
    mode: PrivacyMode = PrivacyMode.LOCAL
    metadata_upload_consent: bool = False
    raw_data_upload_consent: bool = False

    @model_validator(mode="after")
    def local_mode_never_has_upload_consent(self) -> PrivacyMetadata:
        if self.mode == PrivacyMode.LOCAL and (
            self.metadata_upload_consent or self.raw_data_upload_consent
        ):
            raise ValueError("local mode cannot grant upload consent")
        if self.raw_data_upload_consent and not self.metadata_upload_consent:
            raise ValueError("raw-data consent requires metadata consent")
        return self


class OperationPlan(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    job_id: UUID = Field(default_factory=uuid4)
    job_name: str = Field(min_length=1, max_length=200)
    source_files: list[SourceReference] = Field(min_length=1)
    steps: list[PlanStep] = Field(default_factory=list)
    validations: list[ValidationRule] = Field(default_factory=list)
    output: OutputSettings
    privacy: PrivacyMetadata = Field(default_factory=PrivacyMetadata)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_step_graph(self) -> OperationPlan:
        identifiers = [step.step_id for step in self.steps]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("step IDs must be unique")
        positions = {identifier: index for index, identifier in enumerate(identifiers)}
        for index, step in enumerate(self.steps):
            for dependency in step.depends_on:
                if dependency not in positions:
                    raise ValueError(f"unknown step dependency: {dependency}")
                if positions[dependency] >= index:
                    raise ValueError("step dependencies must refer to earlier steps")
        source_ids = {source.source_id for source in self.source_files}
        if any(step.target.source_id not in source_ids for step in self.steps):
            raise ValueError("step target must reference a source in the plan")
        return self
