"""Typed models shared by local and provider-backed planners."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field, model_validator

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.core.plan_schema import (
    OperationPlan,
    OutputSettings,
    PlanStep,
    PrivacyMetadata,
    SourceReference,
    StrictModel,
    ValidationRule,
)
from sheetpilot.core.profile_models import FileProfile


class FrozenStrictModel(StrictModel):
    """Immutable boundary model for data shown before an external action."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanningColumnContext(FrozenStrictModel):
    """Aggregate-only column metadata suitable for planning."""

    name: str = Field(min_length=1, max_length=255)
    inferred_types: tuple[str, ...] = ()
    mixed_types: bool = False
    blank_percentage: float = Field(default=0, ge=0, le=100)


class PlanningSheetContext(FrozenStrictModel):
    """Aggregate-only sheet metadata; it never contains cell values."""

    name: str = Field(min_length=1, max_length=255)
    used_rows: int = Field(ge=0)
    used_columns: int = Field(ge=0)
    duplicate_headers: tuple[str, ...] = ()
    columns: tuple[PlanningColumnContext, ...]

    @property
    def headers(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)


class PlanningSourceContext(FrozenStrictModel):
    """Plan identity plus non-sensitive profiling aggregates for one source."""

    reference: SourceReference
    sheets: tuple[PlanningSheetContext, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def sheet_metadata_matches_reference(self) -> PlanningSourceContext:
        actual = tuple(sheet.name for sheet in self.sheets)
        declared = tuple(self.reference.sheet_names)
        if declared and actual != declared:
            raise ValueError("planning sheets must match the analysed source reference")
        return self


class PlanningRequest(FrozenStrictModel):
    """Complete local input needed to draft, but never execute, a plan."""

    job_id: UUID = Field(default_factory=uuid4)
    job_name: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=20_000)
    sources: tuple[PlanningSourceContext, ...] = Field(min_length=1)
    output: OutputSettings
    privacy: PrivacyMetadata
    default_source_id: UUID | None = None
    default_sheet: str | None = Field(default=None, min_length=1, max_length=255)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def defaults_reference_analysed_context(self) -> PlanningRequest:
        by_id = {source.reference.source_id: source for source in self.sources}
        if len(by_id) != len(self.sources):
            raise ValueError("planning source IDs must be unique")
        if self.default_source_id is not None and self.default_source_id not in by_id:
            raise ValueError("default source must reference an analysed source")
        if self.default_sheet is not None:
            if self.default_source_id is None:
                raise ValueError("a default sheet requires a default source")
            source = by_id[self.default_source_id]
            if self.default_sheet not in {sheet.name for sheet in source.sheets}:
                raise ValueError("default sheet must reference an analysed sheet")
        return self

    @property
    def source_references(self) -> list[SourceReference]:
        return [source.reference for source in self.sources]

    def default_target(self) -> tuple[PlanningSourceContext, PlanningSheetContext]:
        """Resolve an unambiguous local-parser target or fail closed."""
        if self.default_source_id is not None:
            source = next(
                item for item in self.sources if item.reference.source_id == self.default_source_id
            )
        elif len(self.sources) == 1:
            source = self.sources[0]
        else:
            raise InvalidPlanError("Select a source before using the local rule-based planner.")
        if self.default_sheet is not None:
            sheet = next(item for item in source.sheets if item.name == self.default_sheet)
        elif len(source.sheets) == 1:
            sheet = source.sheets[0]
        else:
            raise InvalidPlanError("Select a sheet before using the local rule-based planner.")
        if sheet.duplicate_headers:
            raise InvalidPlanError(
                "Resolve duplicate headers before generating a column-targeted plan."
            )
        return source, sheet


class PlannerResponse(StrictModel):
    """The only JSON shape a provider is allowed to return."""

    schema_version: Literal["1.0"] = "1.0"
    steps: list[PlanStep] = Field(default_factory=list)
    validations: list[ValidationRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def response_has_work(self) -> PlannerResponse:
        if not self.steps and not self.validations:
            raise ValueError("a planner response must contain a step or validation")
        return self


class PlanningOrigin(StrEnum):
    LOCAL_RULES = "local_rules"
    LOCAL_PROVIDER = "local_provider"
    REMOTE_PROVIDER = "remote_provider"


def plan_digest(plan: OperationPlan) -> str:
    """Create a stable digest used to bind review approval to one plan."""
    canonical = json.dumps(
        plan.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PlanProposal(FrozenStrictModel):
    """Unapproved planner result; callers must not pass it to execution."""

    plan: OperationPlan
    origin: PlanningOrigin
    provider_id: str | None = Field(default=None, max_length=100)
    plan_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    requires_human_approval: Literal[True] = True

    @model_validator(mode="after")
    def digest_matches_plan(self) -> PlanProposal:
        if self.plan_digest != plan_digest(self.plan):
            raise ValueError("proposal digest does not match its plan")
        return self


class PlanApproval(FrozenStrictModel):
    """Explicit human decision bound to the reviewed proposal digest."""

    plan_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved: bool
    approved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ApprovedPlan(FrozenStrictModel):
    """Plan plus approval evidence; this type still has no execution methods."""

    plan: OperationPlan
    plan_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved_at: datetime

    @model_validator(mode="after")
    def digest_matches_plan(self) -> ApprovedPlan:
        if self.plan_digest != plan_digest(self.plan):
            raise ValueError("approved-plan digest does not match its plan")
        return self


def planning_source_from_profile(
    profile: FileProfile, *, source_id: UUID | None = None
) -> PlanningSourceContext:
    """Convert aggregate profiler output into a planner context without cell values."""
    reference = SourceReference(
        source_id=source_id or uuid4(),
        file_name=profile.file_name,
        sha256=profile.fingerprint.sha256,
        sheet_names=[sheet.name for sheet in profile.sheets],
    )
    return PlanningSourceContext(
        reference=reference,
        sheets=tuple(
            PlanningSheetContext(
                name=sheet.name,
                used_rows=sheet.used_rows,
                used_columns=sheet.used_columns,
                duplicate_headers=sheet.duplicate_headers,
                columns=tuple(
                    PlanningColumnContext(
                        name=column.name,
                        inferred_types=column.inferred_types,
                        mixed_types=column.mixed_types,
                        blank_percentage=column.blank_percentage,
                    )
                    for column in sheet.columns
                ),
            )
            for sheet in profile.sheets
        ),
    )
