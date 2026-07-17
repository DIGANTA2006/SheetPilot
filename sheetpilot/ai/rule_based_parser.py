"""Small, honest local parser for common deterministic operations."""

from __future__ import annotations

import re

from sheetpilot.ai.models import PlannerResponse, PlanningRequest, PlanningSheetContext
from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.core.plan_schema import PlanStep, RiskLevel, StepTarget

_CLAUSE_SPLIT = re.compile(r"(?:;|\r?\n|\bthen\b)", re.IGNORECASE)


def _mentioned_headers(text: str, sheet: PlanningSheetContext) -> list[str]:
    mentioned: list[str] = []
    for header in sorted(sheet.headers, key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(header)}(?!\w)", text, flags=re.IGNORECASE):
            mentioned.append(header)
    return [header for header in sheet.headers if header in mentioned]


def _text_columns(sheet: PlanningSheetContext) -> list[str]:
    return [
        column.name
        for column in sheet.columns
        if not column.inferred_types
        or bool({"text", "numeric_text"}.intersection(column.inferred_types))
    ]


def _text_actions(clause: str) -> list[dict[str, str]]:
    lowered = clause.casefold()
    actions: list[dict[str, str]] = []
    if re.search(r"\b(trim|strip)\b", lowered) or "clean whitespace" in lowered:
        actions.append({"kind": "trim"})
    if "collapse" in lowered and ("space" in lowered or "whitespace" in lowered):
        actions.append({"kind": "collapse_spaces"})
    if "non-printing" in lowered or "nonprinting" in lowered:
        actions.append({"kind": "remove_non_printing"})
    if "normalize unicode" in lowered or "normalise unicode" in lowered:
        actions.append({"kind": "normalize_unicode"})
    if "proper case" in lowered or "title case" in lowered:
        actions.append({"kind": "proper_case"})
    if re.search(r"\b(uppercase|upper case)\b", lowered):
        actions.append({"kind": "uppercase"})
    if re.search(r"\b(lowercase|lower case)\b", lowered):
        actions.append({"kind": "lowercase"})
    return actions


def _build_text_step(
    clause: str,
    request: PlanningRequest,
    sheet: PlanningSheetContext,
    sequence: int,
) -> PlanStep | None:
    actions = _text_actions(clause)
    if not actions:
        return None
    columns = (
        _text_columns(sheet)
        if re.search(r"\ball (?:text|string) columns\b", clause, re.IGNORECASE)
        else _mentioned_headers(clause, sheet)
    )
    if not columns:
        raise InvalidPlanError("Name at least one analysed text column for local text cleaning.")
    source, _ = request.default_target()
    return PlanStep(
        step_id=f"text-clean-{sequence}",
        operation="text.clean",
        parameters={"columns": columns, "actions": actions},
        target=StepTarget(
            source_id=source.reference.source_id,
            sheet=sheet.name,
            columns=columns,
        ),
        risk_level=RiskLevel.LOW,
        explanation="Apply the selected deterministic text-cleaning actions.",
    )


def _build_sort_step(
    clause: str,
    request: PlanningRequest,
    sheet: PlanningSheetContext,
    sequence: int,
) -> PlanStep | None:
    if not re.search(r"\bsort\b", clause, re.IGNORECASE):
        return None
    columns = _mentioned_headers(clause, sheet)
    if len(columns) != 1:
        raise InvalidPlanError("Local sorting currently requires exactly one named column.")
    descending = bool(
        re.search(r"\b(descending|desc|newest first|largest first|z to a)\b", clause, re.I)
    )
    source, _ = request.default_target()
    column = columns[0]
    return PlanStep(
        step_id=f"sort-{sequence}",
        operation="rows.sort",
        parameters={"keys": [{"column": column, "descending": descending, "nulls_last": True}]},
        target=StepTarget(
            source_id=source.reference.source_id,
            sheet=sheet.name,
            columns=[column],
        ),
        risk_level=RiskLevel.LOW,
        explanation=f"Sort rows by {column} using a stable deterministic order.",
    )


def _build_duplicate_step(
    clause: str,
    request: PlanningRequest,
    sheet: PlanningSheetContext,
    sequence: int,
) -> PlanStep | None:
    if not re.search(r"\bduplicates?\b|\bdeduplicate\b", clause, re.IGNORECASE):
        return None
    remove = bool(re.search(r"\b(remove|delete|deduplicate)\b", clause, re.IGNORECASE))
    mark = bool(re.search(r"\b(mark|flag|identify)\b", clause, re.IGNORECASE))
    if remove == mark:
        raise InvalidPlanError("Say whether duplicate rows should be marked or removed.")
    keys = _mentioned_headers(clause, sheet)
    source, _ = request.default_target()
    return PlanStep(
        step_id=f"duplicates-{sequence}",
        operation="duplicates.handle",
        parameters={"keys": keys, "mode": "remove" if remove else "mark", "keep": "first"},
        target=StepTarget(
            source_id=source.reference.source_id,
            sheet=sheet.name,
            columns=keys,
        ),
        risk_level=RiskLevel.HIGH if remove else RiskLevel.LOW,
        destructive=remove,
        confirmation_required=remove,
        explanation=(
            "Remove exact duplicate rows while retaining the first row."
            if remove
            else "Mark exact duplicate rows for review without removing data."
        ),
    )


def parse_local_instruction(request: PlanningRequest) -> PlannerResponse:
    """Translate a deliberately small rule vocabulary and reject partial guesses."""
    _, sheet = request.default_target()
    clauses = [item.strip(" ,.") for item in _CLAUSE_SPLIT.split(request.instruction)]
    clauses = [item for item in clauses if item]
    if not clauses:
        raise InvalidPlanError("Enter an instruction before planning.")
    steps: list[PlanStep] = []
    for clause in clauses:
        sequence = len(steps) + 1
        candidates = [
            _build_duplicate_step(clause, request, sheet, sequence),
            _build_sort_step(clause, request, sheet, sequence),
            _build_text_step(clause, request, sheet, sequence),
        ]
        matched = [step for step in candidates if step is not None]
        if len(matched) != 1:
            raise InvalidPlanError(
                "The local planner could not safely interpret one instruction clause. "
                "Use separate clauses for cleaning, sorting, and duplicate handling."
            )
        step = matched[0]
        if steps:
            step.depends_on = [steps[-1].step_id]
        steps.append(step)
    return PlannerResponse(steps=steps)
