"""Review-safe deterministic value standardization."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

import polars as pl
from pydantic import Field, model_validator

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.operations.base import OperationParameters
from sheetpilot.operations.tabular import TableOperationResult, TabularOperation, require_columns

_EMAIL = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)
_CANONICAL_STATES = (
    "Andhra Pradesh",
    "Arunachal Pradesh",
    "Assam",
    "Bihar",
    "Chhattisgarh",
    "Goa",
    "Gujarat",
    "Haryana",
    "Himachal Pradesh",
    "Jharkhand",
    "Karnataka",
    "Kerala",
    "Madhya Pradesh",
    "Maharashtra",
    "Manipur",
    "Meghalaya",
    "Mizoram",
    "Nagaland",
    "Odisha",
    "Punjab",
    "Rajasthan",
    "Sikkim",
    "Tamil Nadu",
    "Telangana",
    "Tripura",
    "Uttar Pradesh",
    "Uttarakhand",
    "West Bengal",
    "Andaman and Nicobar Islands",
    "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu",
    "Delhi",
    "Jammu and Kashmir",
    "Ladakh",
    "Lakshadweep",
    "Puducherry",
)


class StandardizationMode(StrEnum):
    STATE = "state"
    DISTRICT = "district"
    STATUS_CATEGORY = "status_category"
    TELEPHONE = "telephone"
    EMAIL = "email"
    DATE = "date"
    NUMERIC_TEXT = "numeric_text"


class InvalidValuePolicy(StrEnum):
    LEAVE = "leave"
    REJECT = "reject"
    NULL = "null"


class NumericTarget(StrEnum):
    INTEGER = "integer"
    NUMBER = "number"


class StandardizeParameters(OperationParameters):
    column: str
    mode: StandardizationMode
    mapping: dict[str, str] = Field(default_factory=dict)
    default_country_code: str | None = Field(default=None, pattern=r"^\d{1,4}$")
    input_date_formats: list[str] = Field(
        default_factory=lambda: ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"]
    )
    output_date_format: str = Field(default="%Y-%m-%d", min_length=2, max_length=40)
    invalid_policy: InvalidValuePolicy = InvalidValuePolicy.LEAVE
    numeric_target: NumericTarget = NumericTarget.NUMBER

    @model_validator(mode="after")
    def mappings_required_for_open_vocabularies(self) -> StandardizeParameters:
        if (
            self.mode
            in {
                StandardizationMode.DISTRICT,
                StandardizationMode.STATUS_CATEGORY,
            }
            and not self.mapping
        ):
            raise ValueError("district and category standardization require an approved mapping")
        return self


def _normal_key(value: str) -> str:
    return " ".join(value.casefold().strip().split())


def _state_mapping(custom: dict[str, str]) -> dict[str, str]:
    mapping = {_normal_key(value): value for value in _CANONICAL_STATES}
    mapping.update(
        {
            "orissa": "Odisha",
            "uttaranchal": "Uttarakhand",
            "nct of delhi": "Delhi",
            "new delhi": "Delhi",
            "pondicherry": "Puducherry",
        }
    )
    mapping.update({_normal_key(key): value for key, value in custom.items()})
    return mapping


def _approved_mapping(parameters: StandardizeParameters) -> dict[str, str]:
    if parameters.mode == StandardizationMode.STATE:
        return _state_mapping(parameters.mapping)
    return {_normal_key(key): value for key, value in parameters.mapping.items()}


def _parse_date(value: str, parameters: StandardizeParameters) -> str | None:
    candidate = value.strip()
    for date_format in parameters.input_date_formats:
        try:
            return datetime.strptime(candidate, date_format).strftime(parameters.output_date_format)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(candidate).strftime(parameters.output_date_format)
    except ValueError:
        return None


def _standardize_phone(value: str, country_code: str | None) -> str | None:
    stripped = value.strip()
    has_plus = stripped.startswith("+")
    digits = "".join(character for character in stripped if character.isdigit())
    if not 10 <= len(digits) <= 15:
        return None
    if has_plus:
        return f"+{digits}"
    if len(digits) == 10 and country_code:
        return f"+{country_code}{digits}"
    return digits


def _standardize_text_value(value: str, parameters: StandardizeParameters) -> str | None:
    if parameters.mode in {
        StandardizationMode.STATE,
        StandardizationMode.DISTRICT,
        StandardizationMode.STATUS_CATEGORY,
    }:
        return _approved_mapping(parameters).get(_normal_key(value))
    if parameters.mode == StandardizationMode.TELEPHONE:
        return _standardize_phone(value, parameters.default_country_code)
    if parameters.mode == StandardizationMode.EMAIL:
        candidate = value.strip().casefold()
        return candidate if _EMAIL.fullmatch(candidate) else None
    if parameters.mode == StandardizationMode.DATE:
        return _parse_date(value, parameters)
    raise AssertionError("numeric conversion is handled separately")


def _standardize_text_column(
    data: pl.DataFrame, parameters: StandardizeParameters
) -> TableOperationResult:
    column = parameters.column
    values = data.get_column(column).cast(pl.String, strict=False).to_list()
    converted: list[str | None] = []
    invalid = 0
    for value in values:
        if value is None or not value.strip():
            converted.append(value)
            continue
        result = _standardize_text_value(value, parameters)
        if result is None:
            invalid += 1
            if parameters.invalid_policy == InvalidValuePolicy.REJECT:
                raise InvalidPlanError(
                    f"{invalid} value(s) cannot be safely standardized in {column}."
                )
            converted.append(
                None if parameters.invalid_policy == InvalidValuePolicy.NULL else value
            )
        else:
            converted.append(result)
    warnings = (
        (
            f"{invalid} value(s) were left for review during "
            f"{parameters.mode.value} standardization.",
        )
        if invalid and parameters.invalid_policy == InvalidValuePolicy.LEAVE
        else ()
    )
    return TableOperationResult(
        frame=data.with_columns(pl.Series(column, converted, dtype=pl.String)),
        warnings=warnings,
        metrics={"invalid_or_uncertain_values": invalid, "processed_rows": data.height},
    )


def _convert_numeric(data: pl.DataFrame, parameters: StandardizeParameters) -> TableOperationResult:
    column = parameters.column
    numeric = data.get_column(column).cast(pl.Float64, strict=False)
    invalid_mask = data.get_column(column).is_not_null() & numeric.is_null()
    invalid = invalid_mask.sum()
    invalid_count = int(invalid or 0)
    if invalid_count and parameters.invalid_policy in {
        InvalidValuePolicy.LEAVE,
        InvalidValuePolicy.REJECT,
    }:
        raise InvalidPlanError(
            f"{invalid_count} value(s) are not numeric; no conversion was performed."
        )
    if parameters.numeric_target == NumericTarget.INTEGER:
        non_integral = numeric.drop_nulls().filter(numeric.drop_nulls() % 1 != 0).len()
        if non_integral:
            raise InvalidPlanError("Non-integral values cannot be converted to integers safely.")
        converted = numeric.cast(pl.Int64)
    else:
        converted = numeric
    return TableOperationResult(
        frame=data.with_columns(converted.alias(column)),
        metrics={"invalid_or_uncertain_values": invalid_count, "processed_rows": data.height},
    )


class StandardizeValuesOperation(TabularOperation[StandardizeParameters]):
    """Standardize only known values and report uncertain values by count."""

    name = "values.standardize"
    parameters_model = StandardizeParameters

    def is_destructive(self, parameters: StandardizeParameters) -> bool:
        return parameters.invalid_policy == InvalidValuePolicy.NULL

    def execute(
        self, data: pl.DataFrame, parameters: StandardizeParameters
    ) -> TableOperationResult:
        require_columns(data, [parameters.column])
        if parameters.mode == StandardizationMode.NUMERIC_TEXT:
            return _convert_numeric(data, parameters)
        return _standardize_text_column(data, parameters)
