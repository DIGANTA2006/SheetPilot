"""Explicit construction of the supported operation registry."""

from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.operations.cleaning import TextCleanOperation
from sheetpilot.operations.filtering import FilterRowsOperation
from sheetpilot.operations.sorting import SortRowsOperation
from sheetpilot.operations.standardisation import StandardizeValuesOperation


def build_default_registry() -> OperationRegistry:
    """Build the allowlist without runtime module discovery."""
    return OperationRegistry(
        [
            TextCleanOperation(),
            StandardizeValuesOperation(),
            FilterRowsOperation(),
            SortRowsOperation(),
        ]
    )
