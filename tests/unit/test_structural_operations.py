from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl
import pytest

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.core.plan_runner import ROW_ID_COLUMN
from sheetpilot.operations.calculations import (
    CalculateColumnOperation,
    CalculateColumnParameters,
    SummaryStatisticsOperation,
    SummaryStatisticsParameters,
)
from sheetpilot.operations.columns import (
    ColumnTransformOperation,
    ColumnTransformParameters,
)
from sheetpilot.operations.duplicates import (
    DuplicateParameters,
    FuzzyDuplicateReviewOperation,
    FuzzyReviewParameters,
    HandleDuplicatesOperation,
)
from sheetpilot.operations.validation import (
    ValidateDataOperation,
    ValidateDataParameters,
    ValidationContext,
    parse_quality_rules,
    validate_table,
)


def test_duplicate_mark_remove_keep_first_and_last() -> None:
    frame = pl.DataFrame({"ID": [1, 1, 2], "Name": ["first", "last", "only"]})
    operation = HandleDuplicatesOperation()
    marked = operation.execute(
        frame, DuplicateParameters(keys=["ID"], mode="mark", marker_column="Duplicate")
    )
    assert marked.frame.get_column("Duplicate").to_list() == [True, True, False]
    first = operation.execute(frame, DuplicateParameters(keys=["ID"], mode="remove", keep="first"))
    last = operation.execute(frame, DuplicateParameters(keys=["ID"], mode="remove", keep="last"))
    assert first.frame.get_column("Name").to_list() == ["first", "only"]
    assert last.frame.get_column("Name").to_list() == ["last", "only"]
    assert operation.is_destructive(DuplicateParameters(keys=["ID"], mode="remove"))


def test_duplicate_move_and_exact_full_row_mode() -> None:
    frame = pl.DataFrame({"ID": [1, 1, 2], "Name": ["same", "same", "other"]})
    result = HandleDuplicatesOperation().execute(
        frame,
        DuplicateParameters(mode="move", duplicate_table_name="Duplicate Rows"),
    )
    assert result.frame.height == 2
    assert result.auxiliary_tables["Duplicate Rows"].height == 1


def test_duplicate_handling_ignores_internal_preview_identity() -> None:
    frame = pl.DataFrame({"ID": [1, 1], "Name": ["same", "same"], ROW_ID_COLUMN: [10, 11]})
    operation = HandleDuplicatesOperation()

    removed = operation.execute(frame, DuplicateParameters(mode="remove"))
    assert removed.frame.height == 1
    assert removed.frame.get_column(ROW_ID_COLUMN).to_list() == [10]

    merged = operation.execute(
        frame,
        DuplicateParameters(
            keys=["ID"],
            mode="merge_complementary",
            merge_rules=[{"column": "Name", "strategy": "require_equal"}],
        ),
    )
    assert merged.frame.to_dicts() == [{"ID": 1, "Name": "same", ROW_ID_COLUMN: 10}]


def test_complementary_merge_requires_explicit_rules() -> None:
    frame = pl.DataFrame(
        {"ID": [1, 1], "Email": ["ada@example.com", None], "Phone": [None, "1234567890"]}
    )
    parameters = DuplicateParameters.model_validate(
        {
            "keys": ["ID"],
            "mode": "merge_complementary",
            "merge_rules": [
                {"column": "Email", "strategy": "first_non_blank"},
                {"column": "Phone", "strategy": "last_non_blank"},
            ],
        }
    )
    result = HandleDuplicatesOperation().execute(frame, parameters)
    assert result.frame.to_dicts() == [{"ID": 1, "Email": "ada@example.com", "Phone": "1234567890"}]
    with pytest.raises(InvalidPlanError, match="exactly one explicit rule"):
        HandleDuplicatesOperation().execute(
            frame,
            DuplicateParameters.model_validate(
                {
                    "keys": ["ID"],
                    "mode": "merge_complementary",
                    "merge_rules": [{"column": "Email", "strategy": "first_non_blank"}],
                }
            ),
        )


def test_fuzzy_matching_only_marks_review_groups() -> None:
    frame = pl.DataFrame({"Name": ["Jon Smith", "John Smith", "Jane Doe"]})
    result = FuzzyDuplicateReviewOperation().execute(
        frame,
        FuzzyReviewParameters(columns=["Name"], threshold=0.8, marker_column="Review"),
    )
    markers = result.frame.get_column("Review").to_list()
    assert markers[0] == markers[1] == 1
    assert markers[2] is None
    assert result.frame.get_column("Name").to_list() == frame.get_column("Name").to_list()
    assert result.warnings


def _columns(frame: pl.DataFrame, actions: list[dict[str, Any]]) -> pl.DataFrame:
    parameters = ColumnTransformParameters.model_validate({"actions": actions})
    return ColumnTransformOperation().execute(frame, parameters).frame


def test_rename_reorder_add_and_remove_columns() -> None:
    frame = pl.DataFrame({"A": [1], "B": [2]})
    result = _columns(
        frame,
        [
            {"kind": "rename", "mapping": {"A": "Alpha"}},
            {"kind": "add", "name": "Added", "value": "x"},
            {"kind": "reorder", "columns": ["Added", "Alpha"]},
            {"kind": "remove", "columns": ["B"]},
        ],
    )
    assert result.columns == ["Added", "Alpha"]
    assert result.to_dicts() == [{"Added": "x", "Alpha": 1}]


def test_split_combine_and_extraction_actions() -> None:
    split = _columns(
        pl.DataFrame({"Full": ["Ada|Lovelace"]}),
        [
            {
                "kind": "split",
                "source": "Full",
                "delimiter": "|",
                "output_columns": ["First", "Last"],
            }
        ],
    )
    assert split.select("First", "Last").row(0) == ("Ada", "Lovelace")
    combined = _columns(
        split,
        [{"kind": "combine", "columns": ["Last", "First"], "output": "Display", "delimiter": ", "}],
    )
    assert combined.item(0, "Display") == "Lovelace, Ada"
    with_blanks = _columns(
        pl.DataFrame({"First": ["Ada"], "Middle": [None], "Last": ["Lovelace"]}),
        [
            {
                "kind": "combine",
                "columns": ["First", "Middle", "Last"],
                "output": "Full",
                "delimiter": "|",
                "skip_nulls": False,
            }
        ],
    )
    assert with_blanks.item(0, "Full") == "Ada||Lovelace"
    extracted = _columns(
        combined,
        [
            {"kind": "extract_before", "source": "Display", "output": "Before", "delimiter": ","},
            {"kind": "extract_after", "source": "Display", "output": "After", "delimiter": ", "},
            {
                "kind": "extract_fixed",
                "source": "Display",
                "output": "Fixed",
                "start": 0,
                "length": 4,
            },
        ],
    )
    assert extracted.select("Before", "After", "Fixed").row(0) == ("Lovelace", "Ada", "Love")


def test_lookup_row_numbers_and_deterministic_ids() -> None:
    frame = pl.DataFrame({"Code": ["A", "B"], "Name": ["Ada", "Grace"]})
    actions = [
        {"kind": "lookup", "source": "Code", "output": "Status", "mapping": {"A": "Active"}},
        {"kind": "row_number", "output": "Row", "start": 10, "step": 2},
        {
            "kind": "deterministic_id",
            "output": "Key",
            "columns": ["Code", "Name"],
            "namespace": "customers",
            "prefix": "C-",
            "length": 12,
        },
    ]
    operation = ColumnTransformOperation()
    parameters = ColumnTransformParameters.model_validate({"actions": actions})
    first = operation.execute(frame, parameters)
    second = operation.execute(frame, parameters)
    assert first.frame.get_column("Status").to_list() == ["Active", "B"]
    assert first.frame.get_column("Row").to_list() == [10, 12]
    assert first.frame.get_column("Key").to_list() == second.frame.get_column("Key").to_list()
    assert first.metrics["unmatched_lookups"] == 1


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (
            {"kind": "addition", "output": "Out", "operands": [{"column": "A"}, {"column": "B"}]},
            [12.0, 24.0],
        ),
        (
            {
                "kind": "subtraction",
                "output": "Out",
                "operands": [{"column": "B"}, {"column": "A"}],
            },
            [8.0, 16.0],
        ),
        (
            {
                "kind": "multiplication",
                "output": "Out",
                "operands": [{"column": "A"}, {"column": "B"}],
            },
            [20.0, 80.0],
        ),
        (
            {"kind": "division", "output": "Out", "operands": [{"column": "B"}, {"column": "A"}]},
            [5.0, 5.0],
        ),
        (
            {
                "kind": "percentage",
                "output": "Out",
                "numerator": {"column": "A"},
                "denominator": {"column": "B"},
            },
            [20.0, 20.0],
        ),
        (
            {
                "kind": "quantity_price",
                "output": "Out",
                "quantity_column": "A",
                "price_column": "B",
            },
            [20, 80],
        ),
        ({"kind": "gst", "output": "Out", "base_column": "B", "rate_percent": 18}, [1.8, 3.6]),
    ],
)
def test_arithmetic_calculation_families(action: dict[str, Any], expected: list[float]) -> None:
    frame = pl.DataFrame({"A": [2, 4], "B": [10, 20]})
    parameters = CalculateColumnParameters.model_validate({"action": action})
    result = CalculateColumnOperation().execute(frame, parameters)
    assert result.frame.get_column("Out").to_list() == expected


def test_date_age_classification_group_running_and_lookup_calculations() -> None:
    operation = CalculateColumnOperation()
    dates = pl.DataFrame({"Start": ["2020-01-01"], "End": ["2022-03-05"], "Birth": ["2000-07-18"]})
    diff = operation.execute(
        dates,
        CalculateColumnParameters.model_validate(
            {
                "action": {
                    "kind": "date_difference",
                    "output": "Months",
                    "start_column": "Start",
                    "end_column": "End",
                    "unit": "months",
                }
            }
        ),
    )
    assert diff.frame.item(0, "Months") == 26
    age = operation.execute(
        dates,
        CalculateColumnParameters.model_validate(
            {
                "action": {
                    "kind": "age",
                    "output": "Age",
                    "birth_date_column": "Birth",
                    "as_of": date(2026, 7, 17),
                }
            }
        ),
    )
    assert age.frame.item(0, "Age") == 25

    frame = pl.DataFrame({"Group": ["A", "A", "B"], "Amount": [10, 20, 5], "Code": ["x", "y", "z"]})
    classified = operation.execute(
        frame,
        CalculateColumnParameters.model_validate(
            {
                "action": {
                    "kind": "conditional_classification",
                    "output": "Band",
                    "rules": [
                        {
                            "conditions": [
                                {
                                    "kind": "numeric",
                                    "column": "Amount",
                                    "operator": "ge",
                                    "value": 20,
                                }
                            ],
                            "value": "High",
                        }
                    ],
                    "default": "Normal",
                }
            }
        ),
    )
    assert classified.frame.get_column("Band").to_list() == ["Normal", "High", "Normal"]
    grouped = operation.execute(
        frame,
        CalculateColumnParameters.model_validate(
            {
                "action": {
                    "kind": "group_total",
                    "output": "Group Total",
                    "group_by": ["Group"],
                    "value_column": "Amount",
                }
            }
        ),
    )
    assert grouped.frame.get_column("Group Total").to_list() == [30, 30, 5]
    running = operation.execute(
        frame,
        CalculateColumnParameters.model_validate(
            {
                "action": {
                    "kind": "running_total",
                    "output": "Running",
                    "value_column": "Amount",
                    "group_by": ["Group"],
                }
            }
        ),
    )
    assert running.frame.get_column("Running").to_list() == [10.0, 30.0, 5.0]
    looked_up = operation.execute(
        frame,
        CalculateColumnParameters.model_validate(
            {
                "action": {
                    "kind": "lookup_value",
                    "output": "Label",
                    "source_column": "Code",
                    "mapping": {"x": "Known"},
                    "default": "Other",
                }
            }
        ),
    )
    assert looked_up.frame.get_column("Label").to_list() == ["Known", "Other", "Other"]


def test_summary_statistics_are_an_auxiliary_table() -> None:
    frame = pl.DataFrame({"Amount": [1, 2, None]})
    parameters = SummaryStatisticsParameters(
        columns=["Amount"], statistics=["count", "null_count", "sum", "mean"]
    )
    result = SummaryStatisticsOperation().execute(frame, parameters)
    summary = result.auxiliary_tables["Summary Statistics"]
    assert summary.height == 4
    assert result.frame.equals(frame)


def test_all_validation_rule_families_report_aggregate_issues() -> None:
    frame = pl.DataFrame(
        {
            "ID": [1, 1, 3],
            "Required": ["", "ok", None],
            "Phone": ["123", "+919876543210", None],
            "Email": ["bad@", "ok@example.com", None],
            "Date": ["bad", "2026-01-01", "2030-01-01"],
            "Amount": [5, 50, "bad"],
            "Status": ["X", "A", "B"],
            "Lookup": ["missing", "known", "known"],
            "Formula": ["#REF!", "ok", "ok"],
            "Blank": [None, "x", None],
        },
        strict=False,
    )
    parameters = ValidateDataParameters.model_validate(
        {
            "rules": [
                {"kind": "required_fields", "columns": ["Required"]},
                {"kind": "telephone", "column": "Phone"},
                {"kind": "email", "column": "Email"},
                {"kind": "date_range", "column": "Date", "maximum": date(2028, 1, 1)},
                {"kind": "numeric_range", "column": "Amount", "minimum": 10, "maximum": 100},
                {"kind": "allowed_values", "column": "Status", "values": ["A", "B"]},
                {"kind": "duplicate_id", "columns": ["ID"]},
                {"kind": "missing_lookup", "column": "Lookup", "allowed_keys": ["known"]},
                {"kind": "row_count", "expected": 4},
                {"kind": "total", "column": "ID", "expected": 99},
                {"kind": "formula_errors", "columns": ["Formula"]},
                {"kind": "blank_rows"},
            ]
        }
    )
    result = ValidateDataOperation().execute(frame, parameters)
    codes = set(result.auxiliary_tables["Validation Issues"].get_column("Code"))
    assert {
        "missing_required",
        "invalid_telephone",
        "invalid_email",
        "invalid_date_range",
        "invalid_numeric_range",
        "value_not_allowed",
        "duplicate_id",
        "missing_lookup",
        "row_count_mismatch",
        "total_mismatch",
        "formula_error",
    } <= codes
    assert result.metrics["validation_errors"] >= 11


@pytest.mark.parametrize(
    "rule",
    [
        {"kind": "telephone", "column": "Phone", "min_digits": 15, "max_digits": 10},
        {
            "kind": "date_range",
            "column": "Date",
            "minimum": "2026-12-31",
            "maximum": "2026-01-01",
        },
        {"kind": "numeric_range", "column": "Amount", "minimum": 100, "maximum": 10},
    ],
)
def test_validation_ranges_reject_inverted_bounds(rule: dict[str, object]) -> None:
    with pytest.raises(InvalidPlanError, match="Invalid validation rules"):
        parse_quality_rules([rule])


def test_invalid_header_validation_uses_original_headers() -> None:
    rules = ValidateDataParameters.model_validate({"rules": [{"kind": "invalid_headers"}]}).rules
    report = validate_table(
        pl.DataFrame({"A": [1], "B": [2]}),
        rules,
        ValidationContext(original_headers=("A", "a", "")),
    )
    assert not report.passed
    assert report.issues[0].code == "invalid_headers"
