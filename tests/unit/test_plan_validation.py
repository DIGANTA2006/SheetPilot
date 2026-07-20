from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sheetpilot.core.exceptions import InvalidPlanError, UnknownOperationError
from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.core.plan_schema import (
    OperationPlan,
    OutputSettings,
    PlanStep,
    SourceReference,
    StepTarget,
)
from sheetpilot.core.plan_validator import PlanValidator
from sheetpilot.operations.base import Operation, OperationParameters


class RenameParameters(OperationParameters):
    old_name: str
    new_name: str


class RenameOperation(Operation[RenameParameters]):
    name = "column.rename"
    parameters_model = RenameParameters
    supported_engines = frozenset({"polars"})

    def preview(self, data: Any, parameters: RenameParameters) -> Any:
        return data

    def execute(self, data: Any, parameters: RenameParameters) -> Any:
        return data


def make_plan(operation: str = "column.rename", **parameters: Any) -> OperationPlan:
    source_id = uuid4()
    return OperationPlan(
        job_name="Rename column",
        source_files=[SourceReference(source_id=source_id, file_name="input.csv", sha256="a" * 64)],
        steps=[
            PlanStep(
                step_id="rename-1",
                operation=operation,
                parameters=parameters,
                target=StepTarget(source_id=source_id, columns=["old"]),
                explanation="Rename a selected column",
            )
        ],
        output=OutputSettings(output_name="cleaned", format="csv"),
    )


def test_plan_rejects_unknown_top_level_field() -> None:
    payload = make_plan(old_name="old", new_name="new").model_dump(mode="json")
    payload["python"] = "print('unsafe')"
    with pytest.raises(ValidationError):
        OperationPlan.model_validate(payload)


def test_destructive_step_requires_confirmation() -> None:
    with pytest.raises(ValidationError, match="must require confirmation"):
        PlanStep(
            step_id="remove",
            operation="column.remove",
            target=StepTarget(source_id=uuid4()),
            explanation="Remove a column",
            destructive=True,
        )


def test_registry_rejects_unknown_operation() -> None:
    validator = PlanValidator(OperationRegistry([RenameOperation()]))
    with pytest.raises(UnknownOperationError):
        validator.validate(make_plan("unknown.operation", value="x"))


def test_disabled_steps_still_require_known_safe_operations() -> None:
    plan = make_plan("unknown.operation", command="ignored")
    plan.steps[0].enabled = False
    validator = PlanValidator(OperationRegistry([RenameOperation()]))

    with pytest.raises(InvalidPlanError, match="forbidden"):
        validator.validate(plan)


def test_registry_rejects_unknown_parameter() -> None:
    validator = PlanValidator(OperationRegistry([RenameOperation()]))
    with pytest.raises(InvalidPlanError, match="Invalid parameters"):
        validator.validate(make_plan(old_name="old", new_name="new", unexpected=True))


def test_validator_rejects_reserved_internal_column_references() -> None:
    validator = PlanValidator(OperationRegistry([RenameOperation()]))
    with pytest.raises(InvalidPlanError, match="Reserved application columns"):
        validator.validate(make_plan(old_name="_sheetpilot_preview_row_id", new_name="Visible"))


@pytest.mark.parametrize("forbidden", ["python", "sql", "command", "formula", "vba"])
def test_validator_rejects_executable_parameter_keys(forbidden: str) -> None:
    validator = PlanValidator(OperationRegistry([RenameOperation()]))
    with pytest.raises(InvalidPlanError, match="forbidden"):
        validator.validate(make_plan(old_name="old", new_name="new", **{forbidden: "x"}))


def test_valid_plan_returns_typed_parameters() -> None:
    validator = PlanValidator(OperationRegistry([RenameOperation()]))
    result = validator.validate(make_plan(old_name="old", new_name="new"))
    assert result["rename-1"] == RenameParameters(old_name="old", new_name="new")


def test_plan_source_ids_must_be_unique() -> None:
    payload = make_plan(old_name="old", new_name="new").model_dump(mode="json")
    payload["source_files"].append(dict(payload["source_files"][0]))

    with pytest.raises(ValidationError, match="source IDs must be unique"):
        OperationPlan.model_validate(payload)


def test_output_name_must_be_a_plain_name() -> None:
    with pytest.raises(ValidationError, match="plain file name"):
        OutputSettings(output_name="../escaped", format="csv")
