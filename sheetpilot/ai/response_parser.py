"""Fail-closed parsing for untrusted provider JSON."""

from __future__ import annotations

import json
from typing import Any, NoReturn

from pydantic import ValidationError

from sheetpilot.ai.models import PlannerResponse
from sheetpilot.core.exceptions import InvalidPlanError

_MAX_RESPONSE_BYTES = 1_000_000
_MAX_JSON_DEPTH = 40
_MAX_JSON_NODES = 50_000


def _invalid_constant(value: str) -> NoReturn:
    raise ValueError(f"Non-finite JSON number is forbidden: {value}")


def _validate_tree(value: object, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    counter = nodes if nodes is not None else [0]
    counter[0] += 1
    if counter[0] > _MAX_JSON_NODES:
        raise InvalidPlanError("The provider response contains too many JSON values.")
    if depth > _MAX_JSON_DEPTH:
        raise InvalidPlanError("The provider response is nested too deeply.")
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise InvalidPlanError("The provider response contains a non-text JSON key.")
            _validate_tree(nested, depth=depth + 1, nodes=counter)
    elif isinstance(value, list):
        for nested in value:
            _validate_tree(nested, depth=depth + 1, nodes=counter)


def parse_planner_response(response_text: str) -> PlannerResponse:
    """Accept one bounded JSON object and reject prose, Markdown, and unknown fields."""
    if len(response_text.encode("utf-8")) > _MAX_RESPONSE_BYTES:
        raise InvalidPlanError("The provider response exceeds the allowed size.")
    try:
        payload: Any = json.loads(response_text, parse_constant=_invalid_constant)
    except (json.JSONDecodeError, UnicodeError, ValueError) as error:
        raise InvalidPlanError("The planning provider did not return valid JSON.") from error
    if not isinstance(payload, dict):
        raise InvalidPlanError("The planning provider must return one JSON object.")
    _validate_tree(payload)
    try:
        return PlannerResponse.model_validate(payload)
    except ValidationError as error:
        raise InvalidPlanError("The planning provider returned an invalid plan schema.") from error
