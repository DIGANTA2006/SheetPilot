"""Explicit construction of the supported operation registry."""

from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.operations.calculations import (
    CalculateColumnOperation,
    SummaryStatisticsOperation,
)
from sheetpilot.operations.cleaning import TextCleanOperation
from sheetpilot.operations.columns import ColumnTransformOperation
from sheetpilot.operations.duplicates import (
    FuzzyDuplicateReviewOperation,
    HandleDuplicatesOperation,
)
from sheetpilot.operations.filtering import FilterRowsOperation
from sheetpilot.operations.sorting import SortRowsOperation
from sheetpilot.operations.standardisation import StandardizeValuesOperation
from sheetpilot.operations.validation import ValidateDataOperation


def build_default_registry() -> OperationRegistry:
    """Build the allowlist without runtime module discovery."""
    return OperationRegistry(
        [
            TextCleanOperation(),
            StandardizeValuesOperation(),
            FilterRowsOperation(),
            SortRowsOperation(),
            HandleDuplicatesOperation(),
            FuzzyDuplicateReviewOperation(),
            ColumnTransformOperation(),
            CalculateColumnOperation(),
            SummaryStatisticsOperation(),
            ValidateDataOperation(),
        ]
    )
