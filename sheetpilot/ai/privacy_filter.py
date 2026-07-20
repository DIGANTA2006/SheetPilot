"""Consent enforcement and bounded disclosure for planner providers."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from enum import StrEnum
from pathlib import PurePath
from uuid import UUID

from sheetpilot.ai.models import PlanningRequest
from sheetpilot.ai.provider import (
    DisclosureManifest,
    ProviderRequest,
    provider_request_digest,
)
from sheetpilot.core.exceptions import SecurityError
from sheetpilot.core.plan_schema import PrivacyMode
from sheetpilot.security.privacy import REDACTED, redact, redact_text

type SampleRows = Mapping[UUID, Mapping[str, Sequence[Mapping[str, object]]]]

_MAX_SAMPLE_ROWS_PER_SHEET = 10
_MAX_SAMPLE_COLUMNS = 100
_MAX_CELL_CHARACTERS = 512


class DisclosureLevel(StrEnum):
    """Value disclosure requested for a provider call."""

    METADATA = "metadata"
    ANONYMIZED_SAMPLES = "anonymized_samples"
    RAW_SAMPLES = "raw_samples"


class PrivacyConsentError(SecurityError):
    """A provider disclosure lacks the consent required by the privacy mode."""

    code = "privacy_consent_required"


def _file_label(index: int, file_name: str) -> str:
    """Retain only the extension; client file names are not needed by a provider."""
    suffix = PurePath(file_name).suffix.casefold()
    return f"source_{index}{suffix}" if suffix else f"source_{index}"


def _anonymize_cell(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "<boolean>"
    if isinstance(value, int):
        return "<integer>"
    if isinstance(value, float):
        return "<number>"
    if isinstance(value, (datetime, date, time)):
        return f"<{type(value).__name__}>"
    if isinstance(value, str):
        if not value:
            return ""
        if redact_text(value) != value:
            return REDACTED
        return f"<text:length={min(len(value), _MAX_CELL_CHARACTERS)}>"
    return f"<{type(value).__name__.casefold()}>"


def _raw_cell(value: object, *, key: str) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        safe_value: object = value
        if isinstance(value, float) and not math.isfinite(value):
            safe_value = None
        if isinstance(safe_value, str):
            safe_value = safe_value[:_MAX_CELL_CHARACTERS]
        filtered = redact(safe_value, key=key)
        if filtered is None or isinstance(filtered, (str, int, float, bool)):
            return filtered
        return REDACTED
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return REDACTED


def _sample_payload(
    request: PlanningRequest,
    level: DisclosureLevel,
    samples: SampleRows | None,
) -> tuple[dict[str, dict[str, list[dict[str, object]]]], int]:
    if level == DisclosureLevel.METADATA:
        return {}, 0
    if samples is None:
        raise PrivacyConsentError("The selected disclosure level requires explicit sample rows.")
    allowed = {
        source.reference.source_id: {sheet.name: set(sheet.headers) for sheet in source.sheets}
        for source in request.sources
    }
    output: dict[str, dict[str, list[dict[str, object]]]] = {}
    row_count = 0
    for source_id, by_sheet in samples.items():
        if source_id not in allowed:
            raise PrivacyConsentError("Sample rows reference an unanalysed source.")
        source_output: dict[str, list[dict[str, object]]] = {}
        for sheet_name, rows in by_sheet.items():
            if sheet_name not in allowed[source_id]:
                raise PrivacyConsentError("Sample rows reference an unanalysed sheet.")
            if len(rows) > _MAX_SAMPLE_ROWS_PER_SHEET:
                raise PrivacyConsentError(
                    f"At most {_MAX_SAMPLE_ROWS_PER_SHEET} sample rows may be disclosed per sheet."
                )
            safe_rows: list[dict[str, object]] = []
            for row in rows:
                if len(row) > _MAX_SAMPLE_COLUMNS:
                    raise PrivacyConsentError(
                        f"A sample row may contain at most {_MAX_SAMPLE_COLUMNS} columns."
                    )
                unknown = set(row) - allowed[source_id][sheet_name]
                if unknown:
                    raise PrivacyConsentError("Sample rows contain unanalysed columns.")
                safe_row: dict[str, object] = {}
                for column, value in row.items():
                    safe_row[column] = (
                        _anonymize_cell(value)
                        if level == DisclosureLevel.ANONYMIZED_SAMPLES
                        else _raw_cell(value, key=column)
                    )
                safe_rows.append(safe_row)
            source_output[sheet_name] = safe_rows
            row_count += len(safe_rows)
        output[str(source_id)] = source_output
    return output, row_count


def build_disclosed_context(
    request: PlanningRequest,
    *,
    provider_id: str,
    provider_is_remote: bool,
    level: DisclosureLevel,
    samples: SampleRows | None = None,
) -> tuple[dict[str, object], int]:
    """Build exactly the context a provider may see after enforcing consent."""
    if provider_is_remote:
        if request.privacy.mode != PrivacyMode.AI_ASSISTED:
            raise PrivacyConsentError("Local mode cannot call a remote planning provider.")
        if not request.privacy.metadata_upload_consent:
            raise PrivacyConsentError("Metadata upload consent is required before AI planning.")
        if level == DisclosureLevel.RAW_SAMPLES and not request.privacy.raw_data_upload_consent:
            raise PrivacyConsentError("Raw-data upload consent is required for raw samples.")
    sample_payload, sample_count = _sample_payload(request, level, samples)
    sources: list[dict[str, object]] = []
    for index, source in enumerate(request.sources, start=1):
        sheets: list[dict[str, object]] = []
        for sheet in source.sheets:
            sheets.append(
                {
                    "name": redact_text(sheet.name),
                    "used_rows": sheet.used_rows,
                    "used_columns": sheet.used_columns,
                    "duplicate_headers": [redact_text(item) for item in sheet.duplicate_headers],
                    "columns": [
                        {
                            "name": redact_text(column.name),
                            "inferred_types": list(column.inferred_types),
                            "mixed_types": column.mixed_types,
                            "blank_percentage": column.blank_percentage,
                        }
                        for column in sheet.columns
                    ],
                }
            )
        source_context: dict[str, object] = {
            "source_id": str(source.reference.source_id),
            "file_label": _file_label(index, source.reference.file_name),
            "sheets": sheets,
        }
        disclosed_samples = sample_payload.get(str(source.reference.source_id))
        if disclosed_samples is not None:
            source_context["samples"] = disclosed_samples
        sources.append(source_context)
    context: dict[str, object] = {
        "job_name": redact_text(request.job_name),
        "instruction": redact_text(request.instruction),
        "sources": sources,
        "output_format": request.output.format.value,
        "disclosure_level": level.value,
        "provider_id": provider_id,
    }
    return context, sample_count


def disclosure_manifest(
    request: PlanningRequest,
    provider_request: ProviderRequest,
    *,
    provider_id: str,
    provider_is_remote: bool,
    level: DisclosureLevel,
    sample_row_count: int,
) -> DisclosureManifest:
    """Summarize and bind the exact content a user is asked to approve."""
    return DisclosureManifest(
        provider_id=provider_id,
        remote=provider_is_remote,
        disclosure_level=level.value,
        source_count=len(request.sources),
        sheet_count=sum(len(source.sheets) for source in request.sources),
        sample_row_count=sample_row_count,
        includes_raw_values=level == DisclosureLevel.RAW_SAMPLES,
        provider_request_digest=provider_request_digest(provider_request),
    )
