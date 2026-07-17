"""Deterministic text-cleaning operations."""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from typing import Annotated, Literal

import polars as pl
from pydantic import Field

from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.operations.base import OperationParameters
from sheetpilot.operations.tabular import TableOperationResult, TabularOperation, require_columns


class UnicodeForm(StrEnum):
    NFC = "NFC"
    NFKC = "NFKC"


class TrimAction(OperationParameters):
    kind: Literal["trim"]


class CollapseSpacesAction(OperationParameters):
    kind: Literal["collapse_spaces"]


class RemoveNonPrintingAction(OperationParameters):
    kind: Literal["remove_non_printing"]


class NormalizeUnicodeAction(OperationParameters):
    kind: Literal["normalize_unicode"]
    form: UnicodeForm = UnicodeForm.NFKC


class ChangeCaseAction(OperationParameters):
    kind: Literal["proper_case", "uppercase", "lowercase"]


class ReplaceAction(OperationParameters):
    kind: Literal["replace"]
    find: str = Field(min_length=1)
    replacement: str


class MappingReplaceAction(OperationParameters):
    kind: Literal["mapping_replace"]
    mapping: dict[str, str] = Field(min_length=1)


class RemoveCharactersAction(OperationParameters):
    kind: Literal["remove_characters"]
    characters: str = Field(min_length=1)


class RemoveAffixAction(OperationParameters):
    kind: Literal["remove_prefixes", "remove_suffixes"]
    values: list[str] = Field(min_length=1)
    case_sensitive: bool = True


TextAction = Annotated[
    TrimAction
    | CollapseSpacesAction
    | RemoveNonPrintingAction
    | NormalizeUnicodeAction
    | ChangeCaseAction
    | ReplaceAction
    | MappingReplaceAction
    | RemoveCharactersAction
    | RemoveAffixAction,
    Field(discriminator="kind"),
]


class TextCleanParameters(OperationParameters):
    columns: list[str] = Field(min_length=1)
    actions: list[TextAction] = Field(min_length=1)


def _remove_affix(value: str, action: RemoveAffixAction) -> str:
    candidate = value if action.case_sensitive else value.casefold()
    for affix in action.values:
        compared_affix = affix if action.case_sensitive else affix.casefold()
        if action.kind == "remove_prefixes" and candidate.startswith(compared_affix):
            return value[len(affix) :]
        if action.kind == "remove_suffixes" and candidate.endswith(compared_affix):
            return value[: -len(affix)] if affix else value
    return value


def _apply_action(value: str, action: TextAction) -> str:
    if action.kind == "trim":
        return value.strip()
    if action.kind == "collapse_spaces":
        return re.sub(r"\s+", " ", value)
    if action.kind == "remove_non_printing":
        return "".join(character for character in value if character.isprintable())
    if action.kind == "normalize_unicode":
        return unicodedata.normalize(action.form.value, value)
    if action.kind == "proper_case":
        return value.title()
    if action.kind == "uppercase":
        return value.upper()
    if action.kind == "lowercase":
        return value.lower()
    if action.kind == "replace":
        return value.replace(action.find, action.replacement)
    if action.kind == "mapping_replace":
        return action.mapping.get(value, value)
    if action.kind == "remove_characters":
        return value.translate(str.maketrans("", "", action.characters))
    return _remove_affix(value, action)


def _clean_value(value: str, actions: list[TextAction]) -> str:
    result = value
    for action in actions:
        result = _apply_action(result, action)
    return result


class TextCleanOperation(TabularOperation[TextCleanParameters]):
    """Apply an ordered, allowlisted text-cleaning pipeline."""

    name = "text.clean"
    parameters_model = TextCleanParameters

    def execute(self, data: pl.DataFrame, parameters: TextCleanParameters) -> TableOperationResult:
        require_columns(data, parameters.columns)
        for column in parameters.columns:
            if data.schema[column] not in {pl.String, pl.Null}:
                raise InvalidPlanError(f"Text cleaning requires a text column: {column}")

        def clean_value(value: str) -> str:
            return _clean_value(value, parameters.actions)

        expressions = [
            pl.col(column)
            .map_elements(
                clean_value,
                return_dtype=pl.String,
            )
            .alias(column)
            for column in parameters.columns
        ]
        return TableOperationResult(
            frame=data.with_columns(expressions),
            metrics={"processed_rows": data.height, "processed_columns": len(parameters.columns)},
        )
