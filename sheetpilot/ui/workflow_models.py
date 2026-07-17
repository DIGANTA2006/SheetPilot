"""UI-only job draft state and conversion to trusted domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from sheetpilot.core.plan_schema import (
    OperationPlan,
    OutputFormat,
    OutputSettings,
    PrivacyMetadata,
    SourceReference,
)
from sheetpilot.core.preview_engine import SourceBinding
from sheetpilot.core.profile_models import FileProfile
from sheetpilot.storage.models import WorkflowTemplate


@dataclass(frozen=True, slots=True)
class JobDraft:
    """Validated intake values that are not persisted until execution succeeds."""

    job_name: str
    instructions: str
    output_format: OutputFormat
    output_name: str
    output_directory: Path
    deadline: date | None
    preserve_formatting: bool
    privacy: PrivacyMetadata
    profiles: tuple[FileProfile, ...]


@dataclass(frozen=True, slots=True)
class PreparedJob:
    """An initial plan and exact source bindings derived from one analysis."""

    draft: JobDraft
    plan: OperationPlan
    bindings: tuple[SourceBinding, ...]


@dataclass(frozen=True, slots=True)
class WorkflowRunRequest:
    """A saved blueprint and user-reviewed parameter values awaiting new sources."""

    template: WorkflowTemplate
    parameter_values: dict[str, Any]


def prepare_job(draft: JobDraft) -> PreparedJob:
    """Create stable source identities shared by the plan and local bindings."""
    sources: list[SourceReference] = []
    bindings: list[SourceBinding] = []
    for profile in draft.profiles:
        source_id = uuid4()
        sources.append(
            SourceReference(
                source_id=source_id,
                file_name=profile.file_name,
                sha256=profile.fingerprint.sha256,
                sheet_names=[sheet.name for sheet in profile.sheets],
            )
        )
        bindings.append(
            SourceBinding(
                source_id=source_id,
                path=profile.source_path,
                fingerprint=profile.fingerprint,
            )
        )
    plan = OperationPlan(
        job_name=draft.job_name,
        source_files=sources,
        output=OutputSettings(
            output_name=draft.output_name,
            format=draft.output_format,
            preserve_formatting=draft.preserve_formatting,
        ),
        privacy=draft.privacy,
    )
    return PreparedJob(draft=draft, plan=plan, bindings=tuple(bindings))
