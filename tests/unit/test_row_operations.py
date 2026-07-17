from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl
import pytest

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.operations.cleaning import TextCleanOperation, TextCleanParameters
from sheetpilot.operations.filtering import FilterParameters, FilterRowsOperation
from sheetpilot.operations.registry import build_default_registry
from sheetpilot.operations.sorting import SortParameters, SortRowsOperation
from sheetpilot.operations.standardisation import (
    StandardizeParameters,
    StandardizeValuesOperation,
)


@pytest.mark.parametrize(
    ("value", "action", "expected"),
    [
        ("  hello  ", {"kind": "trim"}, "hello"),
        ("a\t  b", {"kind": "collapse_spaces"}, "a b"),
        ("a\x00b", {"kind": "remove_non_printing"}, "ab"),
        ("\uff21", {"kind": "normalize_unicode", "form": "NFKC"}, "A"),
        ("hello WORLD", {"kind": "proper_case"}, "Hello World"),
        ("hello", {"kind": "uppercase"}, "HELLO"),
        ("HELLO", {"kind": "lowercase"}, "hello"),
        ("red-red", {"kind": "replace", "find": "red", "replacement": "blue"}, "blue-blue"),
        ("NY", {"kind": "mapping_replace", "mapping": {"NY": "New York"}}, "New York"),
        ("a-b/c", {"kind": "remove_characters", "characters": "-/"}, "abc"),
        (
            "mr. Ada",
            {"kind": "remove_prefixes", "values": ["Mr. "], "case_sensitive": False},
            "Ada",
        ),
        ("Ada Ltd", {"kind": "remove_suffixes", "values": [" Ltd"]}, "Ada"),
    ],
)
def test_all_text_cleaning_actions(value: str, action: dict[str, Any], expected: str) -> None:
    operation = TextCleanOperation()
    parameters = TextCleanParameters.model_validate({"columns": ["Text"], "actions": [action]})
    frame = pl.DataFrame({"Text": [value, None]})
    result = operation.execute(frame, parameters)
    assert result.frame.get_column("Text").to_list() == [expected, None]


def test_text_cleaning_rejects_non_text_column() -> None:
    parameters = TextCleanParameters.model_validate(
        {"columns": ["Value"], "actions": [{"kind": "trim"}]}
    )
    with pytest.raises(InvalidPlanError, match="requires a text column"):
        TextCleanOperation().execute(pl.DataFrame({"Value": [1]}), parameters)


@pytest.mark.parametrize(
    ("parameters", "values", "expected"),
    [
        ({"column": "Value", "mode": "state"}, ["orissa", "Goa"], ["Odisha", "Goa"]),
        (
            {"column": "Value", "mode": "district", "mapping": {"blr": "Bengaluru"}},
            [" BLR "],
            ["Bengaluru"],
        ),
        (
            {"column": "Value", "mode": "status_category", "mapping": {"a": "Active"}},
            ["A"],
            ["Active"],
        ),
        (
            {"column": "Value", "mode": "telephone", "default_country_code": "91"},
            ["98765 43210"],
            ["+919876543210"],
        ),
        ({"column": "Value", "mode": "email"}, [" ADA@EXAMPLE.COM "], ["ada@example.com"]),
        (
            {"column": "Value", "mode": "date", "input_date_formats": ["%d/%m/%Y"]},
            ["17/07/2026"],
            ["2026-07-17"],
        ),
    ],
)
def test_value_standardization_modes(
    parameters: dict[str, Any], values: list[str], expected: list[str]
) -> None:
    typed = StandardizeParameters.model_validate(parameters)
    result = StandardizeValuesOperation().execute(pl.DataFrame({"Value": values}), typed)
    assert result.frame.get_column("Value").to_list() == expected


def test_uncertain_mapping_is_left_for_review() -> None:
    parameters = StandardizeParameters(column="Value", mode="state")
    result = StandardizeValuesOperation().execute(
        pl.DataFrame({"Value": ["Unknown State"]}), parameters
    )
    assert result.frame.item(0, "Value") == "Unknown State"
    assert result.metrics["invalid_or_uncertain_values"] == 1
    assert result.warnings


def test_numeric_text_conversion_and_rejection() -> None:
    operation = StandardizeValuesOperation()
    converted = operation.execute(
        pl.DataFrame({"Value": ["1", "2.5"]}),
        StandardizeParameters(column="Value", mode="numeric_text"),
    )
    assert converted.frame.get_column("Value").to_list() == [1.0, 2.5]
    with pytest.raises(InvalidPlanError, match="not numeric"):
        operation.execute(
            pl.DataFrame({"Value": ["1", "unknown"]}),
            StandardizeParameters(column="Value", mode="numeric_text"),
        )


def _filter(frame: pl.DataFrame, condition: dict[str, Any]) -> list[int]:
    parameters = FilterParameters.model_validate({"conditions": [condition]})
    result = FilterRowsOperation().execute(frame, parameters)
    return result.frame.get_column("ID").to_list()


def test_filter_condition_variants() -> None:
    frame = pl.DataFrame(
        {
            "ID": [1, 2, 3, 4],
            "Text": ["Alpha", "alphabet", "Beta", None],
            "Amount": [5, 10, 15, 20],
            "Date": ["2026-01-01", "2026-02-01", "bad", "2026-04-01"],
            "Blank": [None, "", "x", "y"],
            "Group": ["A", "B", "A", "C"],
        }
    )
    assert _filter(frame, {"kind": "exact", "column": "ID", "value": 2}) == [2]
    assert _filter(frame, {"kind": "contains", "column": "Text", "value": "PHA"}) == [1, 2]
    assert _filter(frame, {"kind": "starts_with", "column": "Text", "value": "be"}) == [3]
    assert _filter(frame, {"kind": "ends_with", "column": "Text", "value": "bet"}) == [2]
    assert _filter(
        frame, {"kind": "numeric", "column": "Amount", "operator": "ge", "value": 15}
    ) == [3, 4]
    assert _filter(
        frame,
        {
            "kind": "date_range",
            "column": "Date",
            "start": date(2026, 2, 1),
            "end": date(2026, 3, 1),
        },
    ) == [2]
    assert _filter(frame, {"kind": "blank", "column": "Blank", "is_blank": True}) == [1, 2]
    assert _filter(frame, {"kind": "include_values", "column": "Group", "values": ["A", "C"]}) == [
        1,
        3,
        4,
    ]
    assert _filter(frame, {"kind": "exclude_values", "column": "Group", "values": ["A", "C"]}) == [
        2
    ]


def test_filter_all_any_and_inverse() -> None:
    frame = pl.DataFrame({"ID": [1, 2, 3], "A": [1, 1, 2], "B": ["x", "y", "y"]})
    parameters = FilterParameters.model_validate(
        {
            "conditions": [
                {"kind": "exact", "column": "A", "value": 1},
                {"kind": "exact", "column": "B", "value": "y"},
            ],
            "match": "all",
            "keep_matching": False,
        }
    )
    result = FilterRowsOperation().execute(frame, parameters)
    assert result.frame.get_column("ID").to_list() == [1, 3]
    assert result.metrics["removed_rows"] == 1


def test_stable_multi_column_sort_and_order_metadata() -> None:
    frame = pl.DataFrame({"Name": ["b", "a", "a"], "Score": [2, 1, 3]})
    parameters = SortParameters.model_validate(
        {
            "keys": [
                {"column": "Name"},
                {"column": "Score", "descending": True},
            ],
            "preserve_original_order_metadata": True,
        }
    )
    result = SortRowsOperation().execute(frame, parameters)
    assert result.frame.get_column("Score").to_list() == [3, 1, 2]
    assert result.frame.get_column("_sheetpilot_original_order").to_list() == [3, 2, 1]


def test_default_registry_is_explicit() -> None:
    assert build_default_registry().names() == (
        "rows.filter",
        "rows.sort",
        "text.clean",
        "values.standardize",
    )
