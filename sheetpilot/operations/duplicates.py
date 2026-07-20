"""Exact duplicate handling and review-only fuzzy grouping."""

from __future__ import annotations

from collections import Counter, defaultdict
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Any

import polars as pl
from pydantic import Field

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.operations.base import OperationParameters
from sheetpilot.operations.tabular import TableOperationResult, TabularOperation, require_columns

_PREVIEW_ROW_ID = "_sheetpilot_preview_row_id"


class DuplicateMode(StrEnum):
    MARK = "mark"
    REMOVE = "remove"
    MOVE = "move"
    MERGE_COMPLEMENTARY = "merge_complementary"


class KeepRule(StrEnum):
    FIRST = "first"
    LAST = "last"


class MergeStrategy(StrEnum):
    FIRST_NON_BLANK = "first_non_blank"
    LAST_NON_BLANK = "last_non_blank"
    REQUIRE_EQUAL = "require_equal"
    JOIN_UNIQUE = "join_unique"


class ComplementaryMergeRule(OperationParameters):
    column: str
    strategy: MergeStrategy
    delimiter: str = "; "


class DuplicateParameters(OperationParameters):
    keys: list[str] = Field(default_factory=list)
    mode: DuplicateMode
    keep: KeepRule = KeepRule.FIRST
    marker_column: str = "Is Duplicate"
    duplicate_table_name: str = "Duplicates"
    merge_rules: list[ComplementaryMergeRule] = Field(default_factory=list)


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _key(row: dict[str, Any], keys: list[str]) -> tuple[tuple[str, str], ...]:
    return tuple((type(row[column]).__name__, repr(row[column])) for column in keys)


def _duplicate_indices(
    rows: list[dict[str, Any]], keys: list[str], keep: KeepRule
) -> tuple[list[int], list[int], set[int]]:
    grouped: dict[tuple[tuple[str, str], ...], list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[_key(row, keys)].append(index)
    duplicate_groups = [indices for indices in grouped.values() if len(indices) > 1]
    marked = {index for indices in duplicate_groups for index in indices}
    kept: list[int] = []
    moved: list[int] = []
    for indices in grouped.values():
        chosen = indices[0] if keep == KeepRule.FIRST else indices[-1]
        kept.append(chosen)
        moved.extend(index for index in indices if index != chosen)
    return (sorted(kept), sorted(moved), marked)


def _merge_value(values: list[Any], rule: ComplementaryMergeRule) -> Any:
    nonblank = [value for value in values if not _blank(value)]
    if not nonblank:
        return None
    if rule.strategy == MergeStrategy.FIRST_NON_BLANK:
        return nonblank[0]
    if rule.strategy == MergeStrategy.LAST_NON_BLANK:
        return nonblank[-1]
    unique = list(dict.fromkeys((type(value).__name__, repr(value)) for value in nonblank))
    if rule.strategy == MergeStrategy.REQUIRE_EQUAL:
        if len(unique) > 1:
            raise InvalidPlanError(f"Conflicting values cannot be merged in {rule.column}.")
        return nonblank[0]
    return rule.delimiter.join(dict.fromkeys(str(value) for value in nonblank))


def _merge_complementary(
    data: pl.DataFrame,
    keys: list[str],
    rules: list[ComplementaryMergeRule],
    keep: KeepRule,
) -> pl.DataFrame:
    non_key_columns = [
        column for column in data.columns if column not in keys and column != _PREVIEW_ROW_ID
    ]
    rule_columns = [rule.column for rule in rules]
    if set(rule_columns) != set(non_key_columns) or len(rule_columns) != len(set(rule_columns)):
        raise InvalidPlanError(
            "Complementary merge requires exactly one explicit rule per non-key column."
        )
    by_column = {rule.column: rule for rule in rules}
    grouped: dict[tuple[tuple[str, str], ...], list[dict[str, Any]]] = {}
    for row in data.iter_rows(named=True):
        grouped.setdefault(_key(row, keys), []).append(row)
    merged: list[dict[str, Any]] = []
    for group in grouped.values():
        chosen = group[0] if keep == KeepRule.FIRST else group[-1]
        output: dict[str, Any] = {}
        for column in data.columns:
            if column in keys or column == _PREVIEW_ROW_ID:
                output[column] = chosen[column]
            else:
                output[column] = _merge_value([row[column] for row in group], by_column[column])
        merged.append(output)
    return pl.DataFrame(merged, schema=data.schema)


class HandleDuplicatesOperation(TabularOperation[DuplicateParameters]):
    """Mark, remove, move, or explicitly merge exact duplicate groups."""

    name = "duplicates.handle"
    parameters_model = DuplicateParameters

    def is_destructive(self, parameters: DuplicateParameters) -> bool:
        return parameters.mode != DuplicateMode.MARK

    def execute(self, data: pl.DataFrame, parameters: DuplicateParameters) -> TableOperationResult:
        if _PREVIEW_ROW_ID in parameters.keys:
            raise InvalidPlanError("Internal preview identity cannot be a duplicate key.")
        keys = parameters.keys or [column for column in data.columns if column != _PREVIEW_ROW_ID]
        if not keys:
            raise InvalidPlanError("Duplicate handling requires at least one client column.")
        require_columns(data, keys)
        rows = list(data.iter_rows(named=True))
        kept, moved, marked = _duplicate_indices(rows, keys, parameters.keep)
        duplicate_count = len(moved)
        if parameters.mode == DuplicateMode.MARK:
            if parameters.marker_column in data.columns:
                raise InvalidPlanError("The duplicate marker column already exists.")
            marker = pl.Series(
                parameters.marker_column, [index in marked for index in range(data.height)]
            )
            return TableOperationResult(
                frame=data.with_columns(marker),
                metrics={"duplicate_rows": duplicate_count, "duplicate_group_rows": len(marked)},
            )
        if parameters.mode == DuplicateMode.MERGE_COMPLEMENTARY:
            result = _merge_complementary(data, keys, parameters.merge_rules, parameters.keep)
            return TableOperationResult(
                frame=result,
                metrics={
                    "input_rows": data.height,
                    "output_rows": result.height,
                    "merged_rows": data.height - result.height,
                },
            )
        primary = data[kept]
        auxiliary = (
            {parameters.duplicate_table_name: data[moved]}
            if parameters.mode == DuplicateMode.MOVE
            else {}
        )
        return TableOperationResult(
            frame=primary,
            auxiliary_tables=auxiliary,
            metrics={
                "input_rows": data.height,
                "output_rows": primary.height,
                "removed_or_moved_rows": duplicate_count,
            },
        )


class FuzzyReviewParameters(OperationParameters):
    columns: list[str] = Field(min_length=1)
    threshold: float = Field(default=0.88, ge=0.5, le=1.0)
    marker_column: str = "Fuzzy Review Group"
    max_rows: int = Field(default=10_000, gt=0, le=25_000)
    max_comparisons: int = Field(default=1_000_000, gt=0, le=5_000_000)


def _normalized_fuzzy_text(row: dict[str, Any], columns: list[str]) -> str:
    return " ".join(
        " ".join(str(row[column] or "").casefold().split()) for column in columns
    ).strip()


class FuzzyDuplicateReviewOperation(TabularOperation[FuzzyReviewParameters]):
    """Assign review groups without removing or merging fuzzy matches."""

    name = "duplicates.fuzzy_review"
    parameters_model = FuzzyReviewParameters

    def execute(
        self, data: pl.DataFrame, parameters: FuzzyReviewParameters
    ) -> TableOperationResult:
        require_columns(data, parameters.columns)
        if parameters.marker_column in data.columns:
            raise InvalidPlanError("The fuzzy-review marker column already exists.")
        if data.height > parameters.max_rows:
            raise InvalidPlanError("Fuzzy review is limited to the configured review row count.")
        rows = list(data.iter_rows(named=True))
        texts = [_normalized_fuzzy_text(row, parameters.columns) for row in rows]
        required_comparisons = len(texts) * (len(texts) - 1) // 2
        if required_comparisons > parameters.max_comparisons:
            raise InvalidPlanError(
                "Fuzzy review exceeds the configured comparison budget; filter or split the "
                "data before fuzzy matching."
            )
        parents = list(range(len(rows)))

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left: int, right: int) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parents[right_root] = left_root

        comparison_count = 0
        for left in range(len(texts)):
            for right in range(left + 1, len(texts)):
                comparison_count += 1
                if (
                    texts[left]
                    and SequenceMatcher(None, texts[left], texts[right]).ratio()
                    >= parameters.threshold
                ):
                    union(left, right)
        roots = [find(index) for index in range(len(rows))]
        counts = Counter(roots)
        group_numbers: dict[int, int] = {}
        next_group = 1
        markers: list[int | None] = []
        for root in roots:
            if counts[root] < 2:
                markers.append(None)
                continue
            if root not in group_numbers:
                group_numbers[root] = next_group
                next_group += 1
            markers.append(group_numbers[root])
        return TableOperationResult(
            frame=data.with_columns(pl.Series(parameters.marker_column, markers, dtype=pl.Int64)),
            warnings=("Fuzzy matches are suggestions only and require human review.",),
            metrics={"review_groups": len(group_numbers), "comparisons": comparison_count},
        )
