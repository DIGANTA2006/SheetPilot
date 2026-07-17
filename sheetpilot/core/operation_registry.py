"""Allowlist registry for deterministic spreadsheet operations."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from sheetpilot.core.exceptions import (
    DuplicateOperationError,
    InvalidPlanError,
    UnknownOperationError,
)
from sheetpilot.operations.base import Operation


class OperationRegistry:
    """Explicit operation allowlist; runtime discovery is intentionally avoided."""

    def __init__(self, operations: Iterable[Operation[Any]] = ()) -> None:
        self._operations: dict[str, Operation[Any]] = {}
        for operation in operations:
            self.register(operation)

    def register(self, operation: Operation[Any]) -> None:
        if operation.name in self._operations:
            raise DuplicateOperationError(f"Operation already registered: {operation.name}")
        self._operations[operation.name] = operation

    def get(self, name: str) -> Operation[Any]:
        try:
            return self._operations[name]
        except KeyError as error:
            raise UnknownOperationError(f"Unknown operation: {name}") from error

    def validate_parameters(self, name: str, parameters: dict[str, Any]) -> BaseModel:
        operation = self.get(name)
        try:
            return cast(BaseModel, operation.parameters_model.model_validate(parameters))
        except ValidationError as error:
            raise InvalidPlanError(f"Invalid parameters for {name}: {error}") from error

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._operations))

    def __len__(self) -> int:
        return len(self._operations)
