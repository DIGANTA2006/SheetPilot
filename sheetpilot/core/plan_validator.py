"""Cross-model safety validation for restricted plans."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.core.plan_schema import OperationPlan
from sheetpilot.operations.validation import parse_quality_rules

_FORBIDDEN_PARAMETER_KEYS = {
    "code",
    "command",
    "formula",
    "macro",
    "powershell",
    "python",
    "shell",
    "sql",
    "vba",
}
_RESERVED_PREFIX = "_sheetpilot_"


def _reject_executable_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in _FORBIDDEN_PARAMETER_KEYS:
                raise InvalidPlanError(f"Executable plan parameter is forbidden: {key}")
            _reject_executable_keys(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _reject_executable_keys(item)


def _reject_reserved_names(value: Any) -> None:
    if isinstance(value, str):
        if value.casefold().startswith(_RESERVED_PREFIX):
            raise InvalidPlanError("Reserved application columns cannot be referenced by a plan.")
    elif isinstance(value, Mapping):
        for key, nested in value.items():
            _reject_reserved_names(key)
            _reject_reserved_names(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _reject_reserved_names(item)


class PlanValidator:
    """Validate registry membership, typed parameters, and metadata consistency."""

    def __init__(self, registry: OperationRegistry) -> None:
        self._registry = registry

    def validate(self, plan: OperationPlan) -> dict[str, BaseModel]:
        validated: dict[str, BaseModel] = {}
        enabled_step_ids = {step.step_id for step in plan.steps if step.enabled}
        for rule in plan.validations:
            _reject_executable_keys(rule.parameters)
            _reject_reserved_names(rule.parameters)
        parse_quality_rules([{"kind": rule.name, **rule.parameters} for rule in plan.validations])
        for step in plan.steps:
            if any(
                column.casefold().startswith(_RESERVED_PREFIX) for column in step.target.columns
            ):
                raise InvalidPlanError("Reserved application columns cannot be plan targets.")
            _reject_executable_keys(step.parameters)
            _reject_reserved_names(step.parameters)
            operation = self._registry.get(step.operation)
            typed_parameters = self._registry.validate_parameters(step.operation, step.parameters)
            if step.destructive != operation.is_destructive(typed_parameters):
                raise InvalidPlanError(
                    f"Destructive metadata does not match operation {step.operation}."
                )
            if step.enabled:
                missing_dependencies = sorted(set(step.depends_on) - enabled_step_ids)
                if missing_dependencies:
                    raise InvalidPlanError(
                        f"Enabled step {step.step_id} depends on a disabled step: "
                        + ", ".join(missing_dependencies)
                    )
                validated[step.step_id] = typed_parameters
        return validated
