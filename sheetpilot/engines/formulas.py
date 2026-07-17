"""Structured, application-generated Excel formula templates."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from openpyxl.utils import get_column_letter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sheetpilot.core.exceptions import InvalidPlanError


class FormulaKind(StrEnum):
    ADDITION = "addition"
    SUBTRACTION = "subtraction"
    MULTIPLICATION = "multiplication"
    DIVISION = "division"
    PERCENTAGE = "percentage"
    QUANTITY_PRICE = "quantity_price"
    GST = "gst"
    DATE_DIFFERENCE = "date_difference"
    AGE = "age"


class GeneratedFormulaSpec(BaseModel):
    """An allowlisted formula request with no raw formula text field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    output_column: str
    kind: FormulaKind
    operand_columns: list[str] = Field(min_length=1)
    rate_percent: float | None = Field(default=None, ge=0, le=100)
    include_base: bool = False
    as_of: date | None = None
    number_format: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def validate_shape(self) -> GeneratedFormulaSpec:
        binary = {
            FormulaKind.SUBTRACTION,
            FormulaKind.DIVISION,
            FormulaKind.PERCENTAGE,
            FormulaKind.QUANTITY_PRICE,
            FormulaKind.DATE_DIFFERENCE,
        }
        if self.kind in binary and len(self.operand_columns) != 2:
            raise ValueError(f"{self.kind.value} requires exactly two operand columns")
        if self.kind == FormulaKind.GST and (
            len(self.operand_columns) != 1 or self.rate_percent is None
        ):
            raise ValueError("GST requires one base column and a rate")
        if self.kind == FormulaKind.AGE and (len(self.operand_columns) != 1 or self.as_of is None):
            raise ValueError("age requires one birth-date column and an explicit as-of date")
        return self


def render_formula(spec: GeneratedFormulaSpec, headers: list[str], row_number: int) -> str:
    """Render only trusted templates from validated column references."""
    if spec.output_column in headers:
        raise InvalidPlanError(f"Formula output column already exists: {spec.output_column}")
    positions = {header: index + 1 for index, header in enumerate(headers)}
    missing = sorted(set(spec.operand_columns) - set(positions))
    if missing:
        raise InvalidPlanError(f"Formula operands are missing: {', '.join(missing)}")
    cells = [
        f"{get_column_letter(positions[column])}{row_number}" for column in spec.operand_columns
    ]
    if spec.kind == FormulaKind.ADDITION:
        expression = "+".join(cells)
    elif spec.kind in {FormulaKind.MULTIPLICATION, FormulaKind.QUANTITY_PRICE}:
        expression = "*".join(cells)
    elif spec.kind == FormulaKind.SUBTRACTION:
        expression = f"{cells[0]}-{cells[1]}"
    elif spec.kind == FormulaKind.DIVISION:
        expression = f'IFERROR({cells[0]}/{cells[1]},"")'
    elif spec.kind == FormulaKind.PERCENTAGE:
        expression = f'IFERROR({cells[0]}/{cells[1]}*100,"")'
    elif spec.kind == FormulaKind.GST:
        gst = f"{cells[0]}*{spec.rate_percent}/100"
        expression = f"{cells[0]}+{gst}" if spec.include_base else gst
    elif spec.kind == FormulaKind.DATE_DIFFERENCE:
        expression = f"{cells[1]}-{cells[0]}"
    else:
        assert spec.as_of is not None
        expression = (
            f'DATEDIF({cells[0]},DATE({spec.as_of.year},{spec.as_of.month},{spec.as_of.day}),"Y")'
        )
    return f"={expression}"
