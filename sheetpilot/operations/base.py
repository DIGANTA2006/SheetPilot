"""Operation contracts shared by the registry and deterministic engines."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict


class OperationParameters(BaseModel):
    """Strict base for every operation's typed parameters."""

    model_config = ConfigDict(extra="forbid")


class Operation[ParametersT: OperationParameters](ABC):
    """Deterministic operation interface; implementations never receive a source path."""

    name: str
    version: str = "1.0"
    parameters_model: type[ParametersT]
    supported_engines: frozenset[str]
    destructive: bool = False

    @abstractmethod
    def preview(self, data: Any, parameters: ParametersT) -> Any:
        """Return a deterministic preview without mutating the input."""

    @abstractmethod
    def execute(self, data: Any, parameters: ParametersT) -> Any:
        """Return transformed data without executing user-supplied code."""

    def validate_result(self, before: Any, after: Any, parameters: ParametersT) -> list[str]:
        """Return aggregate validation warnings for an operation result."""
        return []

    def is_destructive(self, parameters: ParametersT) -> bool:
        """Return whether these validated parameters remove or overwrite data."""
        del parameters
        return self.destructive

    @property
    def audit_description(self) -> str:
        return f"{self.name} version {self.version}"
