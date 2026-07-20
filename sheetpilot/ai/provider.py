"""Provider contract and disclosure approval models."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Protocol

from pydantic import Field, model_validator

from sheetpilot.ai.models import FrozenStrictModel, PlanningRequest


class PlanningProvider(Protocol):
    """Minimal adapter contract; providers receive strings and return JSON text only."""

    provider_id: str
    is_remote: bool

    def generate_plan(self, request: ProviderRequest) -> str:
        """Return a JSON plan response without accessing files or execution services."""
        ...


class ProviderRequest(FrozenStrictModel):
    """Exact bounded content handed to a provider adapter."""

    system_instruction: str = Field(min_length=1, max_length=8_000)
    context_json: str = Field(min_length=2, max_length=500_000)
    operation_catalog_json: str = Field(min_length=2, max_length=500_000)
    response_schema_json: str = Field(min_length=2, max_length=500_000)


def provider_request_digest(request: ProviderRequest) -> str:
    """Return the canonical digest shown in the disclosure approval screen."""
    canonical = json.dumps(
        request.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DisclosureScope(str):
    """Compatibility-free marker base; use DisclosureLevel from privacy_filter."""


class DisclosureManifest(FrozenStrictModel):
    """Human-readable facts about the exact provider request awaiting approval."""

    provider_id: str = Field(min_length=1, max_length=100)
    remote: bool
    disclosure_level: str
    source_count: int = Field(ge=1)
    sheet_count: int = Field(ge=1)
    sample_row_count: int = Field(ge=0)
    includes_raw_values: bool
    provider_request_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class PreparedProviderCall(FrozenStrictModel):
    """Prepared but not dispatched provider call, suitable for a review dialog."""

    planning_request: PlanningRequest
    provider_request: ProviderRequest
    disclosure: DisclosureManifest

    @model_validator(mode="after")
    def disclosure_matches_request(self) -> PreparedProviderCall:
        if self.disclosure.provider_request_digest != provider_request_digest(
            self.provider_request
        ):
            raise ValueError("provider request digest does not match the disclosure manifest")
        return self


class ProviderCallApproval(FrozenStrictModel):
    """Explicit decision bound to the exact provider request shown to the user."""

    provider_request_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved: bool
    approved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
