"""Typed aggregate data-quality validation and reconciliation checks."""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.operations.base import OperationParameters
from sheetpilot.operations.tabular import TableOperationResult, TabularOperation, require_columns

Scalar = str | int | float | bool | None
_EMAIL = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)
_FORMULA_ERRORS = {"#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A"}


class RequiredFieldsRule(OperationParameters):
    kind: Literal["required_fields"]
    columns: list[str] = Field(min_length=1)


class TelephoneRule(OperationParameters):
    kind: Literal["telephone"]
    column: str
    min_digits: int = Field(default=10, ge=1, le=20)
    max_digits: int = Field(default=15, ge=1, le=20)

    @model_validator(mode="after")
    def valid_digit_range(self) -> TelephoneRule:
        if self.min_digits > self.max_digits:
            raise ValueError("minimum telephone digits cannot exceed maximum digits")
        return self


class EmailRule(OperationParameters):
    kind: Literal["email"]
    column: str


class DateRangeRule(OperationParameters):
    kind: Literal["date_range"]
    column: str
    minimum: date | None = None
    maximum: date | None = None

    @model_validator(mode="after")
    def valid_date_range(self) -> DateRangeRule:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum date cannot be after maximum date")
        return self


class NumericRangeRule(OperationParameters):
    kind: Literal["numeric_range"]
    column: str
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def valid_numeric_range(self) -> NumericRangeRule:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum value cannot exceed maximum value")
        return self


class AllowedValuesRule(OperationParameters):
    kind: Literal["allowed_values"]
    column: str
    values: list[Scalar] = Field(min_length=1)


class DuplicateIdRule(OperationParameters):
    kind: Literal["duplicate_id"]
    columns: list[str] = Field(min_length=1)


class MissingLookupRule(OperationParameters):
    kind: Literal["missing_lookup"]
    column: str
    allowed_keys: list[Scalar] = Field(min_length=1)


class RowCountRule(OperationParameters):
    kind: Literal["row_count"]
    expected: int = Field(ge=0)


class TotalReconciliationRule(OperationParameters):
    kind: Literal["total"]
    column: str
    expected: float
    tolerance: float = Field(default=0.001, ge=0)


class FormulaErrorRule(OperationParameters):
    kind: Literal["formula_errors"]
    columns: list[str] = Field(default_factory=list)


class BlankRowRule(OperationParameters):
    kind: Literal["blank_rows"]


class InvalidHeaderRule(OperationParameters):
    kind: Literal["invalid_headers"]


QualityRule = Annotated[
    RequiredFieldsRule
    | TelephoneRule
    | EmailRule
    | DateRangeRule
    | NumericRangeRule
    | AllowedValuesRule
    | DuplicateIdRule
    | MissingLookupRule
    | RowCountRule
    | TotalReconciliationRule
    | FormulaErrorRule
    | BlankRowRule
    | InvalidHeaderRule,
    Field(discriminator="kind"),
]
_QUALITY_RULE_ADAPTER = TypeAdapter(list[QualityRule])


def parse_quality_rules(values: list[dict[str, object]]) -> list[QualityRule]:
    """Reject unknown validation kinds and parameters through one strict adapter."""
    try:
        return _QUALITY_RULE_ADAPTER.validate_python(values)
    except ValidationError as error:
        raise InvalidPlanError(f"Invalid validation rules: {error}") from error


class ValidationSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    severity: ValidationSeverity
    message: str
    column: str | None = None
    affected_rows: int = Field(ge=0)
    representative_row_numbers: tuple[int, ...] = ()


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    checks_run: int = Field(ge=0)
    issues: tuple[ValidationIssue, ...]

    @property
    def error_count(self) -> int:
        return sum(issue.severity == ValidationSeverity.ERROR for issue in self.issues)


class ValidationContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    original_headers: tuple[str, ...] | None = None


def _blank(expression: pl.Expr) -> pl.Expr:
    return expression.is_null() | (expression.cast(pl.String, strict=False).str.strip_chars() == "")


def _row_numbers(frame: pl.DataFrame, mask: pl.Expr | pl.Series) -> tuple[int, tuple[int, ...]]:
    indices = (
        frame.with_row_index("_validation_row", offset=2)
        .filter(mask.fill_null(False))
        .get_column("_validation_row")
        .to_list()
    )
    return (len(indices), tuple(int(index) for index in indices[:1000]))


def _issue_from_mask(
    frame: pl.DataFrame,
    mask: pl.Expr | pl.Series,
    *,
    code: str,
    message: str,
    column: str | None = None,
) -> ValidationIssue | None:
    count, rows = _row_numbers(frame, mask)
    if not count:
        return None
    return ValidationIssue(
        code=code,
        severity=ValidationSeverity.ERROR,
        message=message,
        column=column,
        affected_rows=count,
        representative_row_numbers=rows,
    )


def _parse_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip()).date()
        except ValueError:
            return None
    return None


def _date_mask(frame: pl.DataFrame, rule: DateRangeRule) -> pl.Series:
    invalid: list[bool] = []
    for value in frame.get_column(rule.column).to_list():
        parsed = _parse_date(value)
        invalid.append(
            parsed is None
            or (rule.minimum is not None and parsed < rule.minimum)
            or (rule.maximum is not None and parsed > rule.maximum)
        )
    return pl.Series("_invalid_date", invalid, dtype=pl.Boolean)


def _contact_mask(frame: pl.DataFrame, rule: TelephoneRule | EmailRule) -> pl.Series:
    invalid: list[bool] = []
    for value in frame.get_column(rule.column).to_list():
        if value is None or not str(value).strip():
            invalid.append(False)
        elif isinstance(rule, EmailRule):
            invalid.append(_EMAIL.fullmatch(str(value).strip()) is None)
        else:
            digits = "".join(character for character in str(value) if character.isdigit())
            invalid.append(not rule.min_digits <= len(digits) <= rule.max_digits)
    return pl.Series("_invalid_contact", invalid, dtype=pl.Boolean)


def _validate_rule(
    frame: pl.DataFrame, rule: QualityRule, context: ValidationContext
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if isinstance(rule, RequiredFieldsRule):
        require_columns(frame, rule.columns)
        for column in rule.columns:
            issue = _issue_from_mask(
                frame,
                _blank(pl.col(column)),
                code="missing_required",
                message="Required values are missing.",
                column=column,
            )
            if issue:
                issues.append(issue)
    elif isinstance(rule, (TelephoneRule, EmailRule)):
        require_columns(frame, [rule.column])
        contact_mask = _contact_mask(frame, rule)
        issue = _issue_from_mask(
            frame,
            contact_mask,
            code=f"invalid_{rule.kind}",
            message=f"Invalid {rule.kind} values were detected.",
            column=rule.column,
        )
        if issue:
            issues.append(issue)
    elif isinstance(rule, DateRangeRule):
        require_columns(frame, [rule.column])
        issue = _issue_from_mask(
            frame,
            _date_mask(frame, rule),
            code="invalid_date_range",
            message="Invalid or out-of-range dates were detected.",
            column=rule.column,
        )
        if issue:
            issues.append(issue)
    elif isinstance(rule, NumericRangeRule):
        require_columns(frame, [rule.column])
        numeric = pl.col(rule.column).cast(pl.Float64, strict=False)
        numeric_mask = numeric.is_null() & pl.col(rule.column).is_not_null()
        if rule.minimum is not None:
            numeric_mask = numeric_mask | (numeric < rule.minimum)
        if rule.maximum is not None:
            numeric_mask = numeric_mask | (numeric > rule.maximum)
        issue = _issue_from_mask(
            frame,
            numeric_mask,
            code="invalid_numeric_range",
            message="Invalid or out-of-range numeric values were detected.",
            column=rule.column,
        )
        if issue:
            issues.append(issue)
    elif isinstance(rule, AllowedValuesRule):
        require_columns(frame, [rule.column])
        issue = _issue_from_mask(
            frame,
            ~pl.col(rule.column).is_in(rule.values, nulls_equal=True),
            code="value_not_allowed",
            message="Values outside the approved list were detected.",
            column=rule.column,
        )
        if issue:
            issues.append(issue)
    elif isinstance(rule, DuplicateIdRule):
        require_columns(frame, rule.columns)
        issue = _issue_from_mask(
            frame,
            pl.struct(rule.columns).is_duplicated(),
            code="duplicate_id",
            message="Duplicate identifiers were detected.",
        )
        if issue:
            issues.append(issue)
    elif isinstance(rule, MissingLookupRule):
        require_columns(frame, [rule.column])
        issue = _issue_from_mask(
            frame,
            ~pl.col(rule.column).is_in(rule.allowed_keys, nulls_equal=True),
            code="missing_lookup",
            message="Lookup keys are missing from the approved lookup set.",
            column=rule.column,
        )
        if issue:
            issues.append(issue)
    elif isinstance(rule, RowCountRule):
        if frame.height != rule.expected:
            issues.append(
                ValidationIssue(
                    code="row_count_mismatch",
                    severity=ValidationSeverity.ERROR,
                    message="The final row count does not match the expected count.",
                    affected_rows=abs(frame.height - rule.expected),
                )
            )
    elif isinstance(rule, TotalReconciliationRule):
        require_columns(frame, [rule.column])
        actual = frame.get_column(rule.column).cast(pl.Float64, strict=False).sum()
        if actual is None or abs(float(actual) - rule.expected) > rule.tolerance:
            issues.append(
                ValidationIssue(
                    code="total_mismatch",
                    severity=ValidationSeverity.ERROR,
                    message="The calculated total does not reconcile with the expected total.",
                    column=rule.column,
                    affected_rows=1,
                )
            )
    elif isinstance(rule, FormulaErrorRule):
        columns = rule.columns or list(frame.columns)
        require_columns(frame, columns)
        formula_mask = pl.any_horizontal(
            [
                pl.col(column).cast(pl.String, strict=False).is_in(_FORMULA_ERRORS)
                for column in columns
            ]
        )
        issue = _issue_from_mask(
            frame,
            formula_mask,
            code="formula_error",
            message="Spreadsheet error values were detected.",
        )
        if issue:
            issues.append(issue)
    elif isinstance(rule, BlankRowRule):
        blank_mask = pl.all_horizontal([_blank(pl.col(column)) for column in frame.columns])
        issue = _issue_from_mask(
            frame,
            blank_mask,
            code="blank_row",
            message="Completely blank rows were detected.",
        )
        if issue:
            issues.append(issue)
    else:
        headers = context.original_headers or tuple(frame.columns)
        normalized = [header.strip().casefold() for header in headers]
        invalid_count = (
            sum(not header for header in normalized) + len(normalized) - len(set(normalized))
        )
        if invalid_count:
            issues.append(
                ValidationIssue(
                    code="invalid_headers",
                    severity=ValidationSeverity.ERROR,
                    message="Blank or duplicate headers were detected.",
                    affected_rows=invalid_count,
                )
            )
    return issues


def validate_table(
    frame: pl.DataFrame,
    rules: list[QualityRule],
    context: ValidationContext | None = None,
) -> ValidationReport:
    """Run allowlisted checks and return only aggregate issues and row numbers."""
    resolved = context or ValidationContext()
    issues = [issue for rule in rules for issue in _validate_rule(frame, rule, resolved)]
    return ValidationReport(
        passed=not any(issue.severity == ValidationSeverity.ERROR for issue in issues),
        checks_run=len(rules),
        issues=tuple(issues),
    )


class ValidateDataParameters(OperationParameters):
    rules: list[QualityRule] = Field(min_length=1)


class ValidateDataOperation(TabularOperation[ValidateDataParameters]):
    """Registry operation that leaves data unchanged and emits a validation table."""

    name = "quality.validate"
    parameters_model = ValidateDataParameters

    def execute(
        self, data: pl.DataFrame, parameters: ValidateDataParameters
    ) -> TableOperationResult:
        report = validate_table(data, parameters.rules)
        issue_rows = [
            {
                "Code": issue.code,
                "Severity": issue.severity.value,
                "Column": issue.column,
                "Affected Rows": issue.affected_rows,
            }
            for issue in report.issues
        ]
        issue_table = (
            pl.DataFrame(issue_rows, strict=False)
            if issue_rows
            else pl.DataFrame(
                schema={
                    "Code": pl.String,
                    "Severity": pl.String,
                    "Column": pl.String,
                    "Affected Rows": pl.Int64,
                }
            )
        )
        return TableOperationResult(
            frame=data,
            auxiliary_tables={"Validation Issues": issue_table},
            warnings=tuple(issue.message for issue in report.issues),
            metrics={"checks_run": report.checks_run, "validation_errors": report.error_count},
            validation_report=report,
        )
