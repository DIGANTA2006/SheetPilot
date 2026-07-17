"""Cross-model safety validation for restricted plans."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.core.plan_schema import OperationPlan

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


def _reject_executable_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in _FORBIDDEN_PARAMETER_KEYS:
                raise InvalidPlanError(f"Executable plan parameter is forbidden: {key}")
            _reject_executable_keys(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _reject_executable_keys(item)


class PlanValidator:
    """Validate registry membership, typed parameters, and metadata consistency."""

    def __init__(self, registry: OperationRegistry) -> None:
        self._registry = registry

    def validate(self, plan: OperationPlan) -> dict[str, object]:
        validated: dict[str, object] = {}
        for step in plan.steps:
            if not step.enabled:
                continue
            _reject_executable_keys(step.parameters)
            operation = self._registry.get(step.operation)
            if step.destructive != operation.destructive:
                raise InvalidPlanError(
                    f"Destructive metadata does not match operation {step.operation}."
                )
            validated[step.step_id] = self._registry.validate_parameters(
                step.operation, step.parameters
            )
        return validated
