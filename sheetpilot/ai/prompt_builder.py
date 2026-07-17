"""Deterministic prompts and schemas for restricted planning providers."""

from __future__ import annotations

import json

from sheetpilot.ai.models import PlannerResponse
from sheetpilot.ai.provider import ProviderRequest
from sheetpilot.core.operation_registry import OperationRegistry

_SYSTEM_INSTRUCTION = """You are a spreadsheet workflow planner with no execution authority.
Return exactly one JSON object matching the supplied response schema. Use only operation names
and parameter schemas in the supplied catalog. Target only listed source IDs, sheets, and columns.
Never return Markdown, code, formulas, SQL, macros, shell commands, or extra keys. Mark every
destructive step destructive=true and confirmation_required=true. If the request cannot be
represented safely with the catalog, return no invented operation; use a quality validation when
applicable or let the caller reject an empty response. All execution, preview, and approval happen
outside this provider."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def build_provider_request(
    context: dict[str, object], registry: OperationRegistry
) -> ProviderRequest:
    """Build the exact strings sent through the provider boundary."""
    operation_catalog: dict[str, object] = {}
    for name in registry.names():
        operation = registry.get(name)
        operation_catalog[name] = {
            "version": operation.version,
            "parameters_schema": operation.parameters_model.model_json_schema(),
            "supported_engines": sorted(operation.supported_engines),
            "statically_destructive": operation.destructive,
        }
    return ProviderRequest(
        system_instruction=_SYSTEM_INSTRUCTION,
        context_json=_canonical_json(context),
        operation_catalog_json=_canonical_json(operation_catalog),
        response_schema_json=_canonical_json(PlannerResponse.model_json_schema()),
    )
