from __future__ import annotations

from uuid import uuid4

import polars as pl
import pytest

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.core.plan_runner import DatasetKey, PlanRunner, strip_internal_columns
from sheetpilot.core.plan_schema import (
    OperationPlan,
    OutputSettings,
    PlanStep,
    SourceReference,
    StepTarget,
)
from sheetpilot.operations.registry import build_default_registry


def test_plan_runner_rejects_case_insensitive_output_column_collisions() -> None:
    source_id = uuid4()
    plan = OperationPlan(
        job_name="Column collision",
        source_files=[SourceReference(source_id=source_id, file_name="input.csv", sha256="a" * 64)],
        steps=[
            PlanStep(
                step_id="add-column",
                operation="columns.transform",
                parameters={"actions": [{"kind": "add", "name": "name", "value": "x"}]},
                target=StepTarget(source_id=source_id, sheet="CSV", columns=["Name"]),
                explanation="Add a selected column.",
            )
        ],
        output=OutputSettings(output_name="cleaned", format="csv"),
    )

    with pytest.raises(InvalidPlanError, match="unique without regard to letter case"):
        PlanRunner(build_default_registry()).run(
            plan,
            {DatasetKey(source_id, "CSV"): pl.DataFrame({"Name": ["Ada"]})},
        )


def test_internal_columns_are_never_exposed_to_preview_or_export() -> None:
    frame = pl.DataFrame(
        {
            "Name": ["Ada"],
            "_sheetpilot_preview_row_id": [1],
            "_sheetpilot_original_order": [1],
        }
    )
    assert strip_internal_columns(frame).columns == ["Name"]
