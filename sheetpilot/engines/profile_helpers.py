"""Shared aggregate-only profiling helpers."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, time
from typing import Any

from sheetpilot.core.profile_models import ColumnProfile, SheetProfile
from sheetpilot.security.formula_guard import is_formula_injection

_EMAIL_PATTERN = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)
_PHONE_HEADER = re.compile(r"phone|mobile|telephone|contact", re.IGNORECASE)
_DATE_HEADER = re.compile(r"date|dob|birth", re.IGNORECASE)
_INTEGER_TEXT = re.compile(r"^[+-]?\d+$")
_NUMBER_TEXT = re.compile(r"^[+-]?(?:\d+\.\d*|\d*\.\d+)$")


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, time):
        return "time"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        stripped = value.strip()
        if _INTEGER_TEXT.fullmatch(stripped) or _NUMBER_TEXT.fullmatch(stripped):
            return "numeric_text"
        return "text"
    return type(value).__name__.casefold()


def _hashable(value: Any) -> tuple[str, str]:
    return (_value_type(value), repr(value))


def _parses_as_date(value: Any) -> bool:
    if isinstance(value, (date, datetime)):
        return True
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate:
        return True
    try:
        datetime.fromisoformat(candidate)
        return True
    except ValueError:
        pass
    for date_format in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            datetime.strptime(candidate, date_format)
            return True
        except ValueError:
            continue
    return False


def _variation_counts(values: list[Any]) -> tuple[int, int]:
    text_values = [value.strip() for value in values if isinstance(value, str) and value.strip()]
    if len(text_values) < 2:
        return (0, 0)
    unique_values = set(text_values)
    if len(unique_values) > max(50, int(math.sqrt(len(text_values))) + 5):
        return (0, 0)
    category_groups: dict[str, set[str]] = defaultdict(set)
    spelling_groups: dict[str, set[str]] = defaultdict(set)
    for value in unique_values:
        category_key = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
        spelling_key = "".join(character for character in category_key if character.isalnum())
        category_groups[category_key].add(value)
        spelling_groups[spelling_key].add(value)
    categories = sum(len(group) for group in category_groups.values() if len(group) > 1)
    spellings = sum(len(group) for group in spelling_groups.values() if len(group) > 1)
    return (categories, spellings)


def profile_rows(
    *,
    name: str,
    visibility: str,
    raw_headers: list[Any],
    rows: list[tuple[Any, ...]],
    used_rows: int,
    used_columns: int,
    formula_cells: int = 0,
    formula_error_cells: int = 0,
    merged_ranges: tuple[str, ...] = (),
    protected: bool = False,
) -> SheetProfile:
    """Produce aggregate quality metadata without returning client cell values."""
    headers = [
        str(value).strip() if not _is_blank(value) else f"Column_{index + 1}"
        for index, value in enumerate(raw_headers)
    ]
    if len(headers) < used_columns:
        headers.extend(f"Column_{index + 1}" for index in range(len(headers), used_columns))
    folded_counts: dict[str, int] = defaultdict(int)
    for header in headers:
        folded_counts[header.casefold()] += 1
    duplicate_headers = tuple(header for header in headers if folded_counts[header.casefold()] > 1)

    normalized_rows = [
        tuple(_hashable(value) for value in row[:used_columns])
        for row in rows
        if not all(_is_blank(value) for value in row[:used_columns])
    ]
    duplicate_rows = len(normalized_rows) - len(set(normalized_rows))
    blank_rows = len(rows) - len(normalized_rows)
    data_row_count = len(rows)
    columns: list[ColumnProfile] = []
    for index, header in enumerate(headers[:used_columns]):
        values = [row[index] if index < len(row) else None for row in rows]
        nonblank = [value for value in values if not _is_blank(value)]
        inferred_types = tuple(sorted({_value_type(value) for value in nonblank}))
        unique_count = len({_hashable(value) for value in nonblank})
        blank_percentage = 0.0 if not values else (len(values) - len(nonblank)) * 100 / len(values)
        suspicious_email_count = sum(
            1
            for value in nonblank
            if isinstance(value, str)
            and "@" in value
            and not _EMAIL_PATTERN.fullmatch(value.strip())
        )
        suspicious_phone_count = 0
        if _PHONE_HEADER.search(header):
            for value in nonblank:
                text = str(value)
                digits = "".join(character for character in text if character.isdigit())
                if not 10 <= len(digits) <= 15:
                    suspicious_phone_count += 1
        invalid_date_count = (
            sum(1 for value in nonblank if not _parses_as_date(value))
            if _DATE_HEADER.search(header)
            else 0
        )
        category_count, spelling_count = _variation_counts(values)
        columns.append(
            ColumnProfile(
                name=header,
                inferred_types=inferred_types,
                mixed_types=len(inferred_types) > 1,
                blank_percentage=round(blank_percentage, 2),
                unique_count=unique_count,
                candidate_key=bool(nonblank)
                and len(nonblank) == data_row_count
                and unique_count == data_row_count,
                formula_injection_count=sum(is_formula_injection(value) for value in nonblank),
                suspicious_email_count=suspicious_email_count,
                suspicious_phone_count=suspicious_phone_count,
                invalid_date_count=invalid_date_count,
                inconsistent_category_count=category_count,
                possible_spelling_variation_count=spelling_count,
            )
        )
    return SheetProfile(
        name=name,
        visibility=visibility,
        used_rows=used_rows,
        used_columns=used_columns,
        headers=tuple(headers[:used_columns]),
        duplicate_headers=tuple(dict.fromkeys(duplicate_headers)),
        data_rows=data_row_count,
        blank_rows=blank_rows,
        duplicate_rows=duplicate_rows,
        formula_cells=formula_cells,
        formula_error_cells=formula_error_cells,
        merged_ranges=merged_ranges,
        protected=protected,
        columns=tuple(columns),
    )
