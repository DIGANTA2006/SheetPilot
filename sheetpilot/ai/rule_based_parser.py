"""Small, honest local parser for common deterministic operations."""

from __future__ import annotations

import re

from sheetpilot.ai.models import PlannerResponse, PlanningRequest, PlanningSheetContext
from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.core.plan_schema import PlanStep, RiskLevel, StepTarget

_CLAUSE_SPLIT = re.compile(r"(?:;|\r?\n|\bthen\b)", re.IGNORECASE)
_STANDARDIZE_TRIGGER = re.compile(
    r"\b(standardize|standardise|normalize|normalise|convert)\b", re.IGNORECASE
)
_FILTER_PREFIX = re.compile(
    r"^\s*(?P<action>keep|include|remove|exclude|filter)\s+(?:the\s+)?rows?\s+where\s+"
    r"(?P<body>.+?)\s*$",
    re.IGNORECASE,
)


def _mentioned_headers(text: str, sheet: PlanningSheetContext) -> list[str]:
    hits: list[tuple[int, str]] = []
    claimed_spans: list[tuple[int, int]] = []
    for header in sorted(sheet.headers, key=len, reverse=True):
        for match in re.finditer(rf"(?<!\w){re.escape(header)}(?!\w)", text, flags=re.IGNORECASE):
            start, end = match.span()
            if any(
                start < claimed_end and end > claimed_start
                for claimed_start, claimed_end in claimed_spans
            ):
                continue
            claimed_spans.append((start, end))
            hits.append((start, header))
    ordered: list[str] = []
    for _, header in sorted(hits):
        if header not in ordered:
            ordered.append(header)
    return ordered


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
    if not columns:
        raise InvalidPlanError("Name at least one analysed column for local sorting.")
    descending = bool(
        re.search(r"\b(descending|desc|newest first|largest first|z to a)\b", clause, re.I)
    )
    source, _ = request.default_target()
    return PlanStep(
        step_id=f"sort-{sequence}",
        operation="rows.sort",
        parameters={
            "keys": [
                {"column": column, "descending": descending, "nulls_last": True}
                for column in columns
            ]
        },
        target=StepTarget(
            source_id=source.reference.source_id,
            sheet=sheet.name,
            columns=columns,
        ),
        risk_level=RiskLevel.LOW,
        explanation="Sort rows by the named columns using a stable deterministic order.",
    )


def _build_standardize_step(
    clause: str,
    request: PlanningRequest,
    sheet: PlanningSheetContext,
    sequence: int,
) -> PlanStep | None:
    if not _STANDARDIZE_TRIGGER.search(clause):
        return None
    mode_patterns = {
        "state": r"\bstate\b",
        "telephone": r"\b(phone|telephone|mobile)\b",
        "email": r"\b(e-?mail)\b",
        "date": r"\bdate\b",
        "numeric_text": r"\b(numeric|number|integer)\b",
    }
    modes = [
        mode for mode, pattern in mode_patterns.items() if re.search(pattern, clause, re.IGNORECASE)
    ]
    if not modes:
        return None
    if len(modes) != 1:
        raise InvalidPlanError("Standardize one supported value type per instruction clause.")
    columns = _mentioned_headers(clause, sheet)
    if len(columns) != 1:
        raise InvalidPlanError("Local standardization requires exactly one named column.")
    mode = modes[0]
    parameters: dict[str, object] = {
        "column": columns[0],
        "mode": mode,
        "invalid_policy": "leave",
    }
    if mode == "telephone":
        country_code = re.search(
            r"\bcountry\s+code\s*\+?(?P<code>\d{1,4})\b", clause, re.IGNORECASE
        )
        if country_code:
            parameters["default_country_code"] = country_code.group("code")
    if mode == "numeric_text" and re.search(r"\binteger\b", clause, re.IGNORECASE):
        parameters["numeric_target"] = "integer"
    source, _ = request.default_target()
    return PlanStep(
        step_id=f"standardize-{sequence}",
        operation="values.standardize",
        parameters=parameters,
        target=StepTarget(
            source_id=source.reference.source_id,
            sheet=sheet.name,
            columns=columns,
        ),
        risk_level=RiskLevel.LOW,
        explanation=(
            "Apply deterministic value standardization and leave uncertain values for review."
        ),
    )


def _filter_column(body: str, sheet: PlanningSheetContext) -> str | None:
    for header in sorted(sheet.headers, key=len, reverse=True):
        if re.match(rf"^\s*{re.escape(header)}(?=\s|[<>=])", body, re.IGNORECASE):
            return header
    return None


def _build_filter_step(
    clause: str,
    request: PlanningRequest,
    sheet: PlanningSheetContext,
    sequence: int,
) -> PlanStep | None:
    prefix = _FILTER_PREFIX.fullmatch(clause)
    if prefix is None:
        return None
    body = prefix.group("body")
    column = _filter_column(body, sheet)
    if column is None:
        raise InvalidPlanError("Start a local filter condition with one analysed column name.")
    escaped_column = re.escape(column)
    condition: dict[str, object] | None = None
    blank = re.fullmatch(
        rf"\s*{escaped_column}\s+is\s+(?P<not>not\s+)?(?:blank|empty)\s*",
        body,
        re.IGNORECASE,
    )
    if blank:
        condition = {
            "kind": "blank",
            "column": column,
            "is_blank": blank.group("not") is None,
        }
    text = re.fullmatch(
        rf"\s*{escaped_column}\s+(?P<operator>contains|starts\s+with|ends\s+with)\s+"
        rf"(?P<quote>['\"])(?P<value>.*)(?P=quote)\s*",
        body,
        re.IGNORECASE,
    )
    if text:
        kinds = {
            "contains": "contains",
            "starts with": "starts_with",
            "ends with": "ends_with",
        }
        operator = " ".join(text.group("operator").casefold().split())
        condition = {
            "kind": kinds[operator],
            "column": column,
            "value": text.group("value"),
            "case_sensitive": False,
        }
    exact = re.fullmatch(
        rf"\s*{escaped_column}\s+(?:equals|is)\s+(?P<quote>['\"])(?P<value>.*)"
        rf"(?P=quote)\s*",
        body,
        re.IGNORECASE,
    )
    if exact:
        condition = {"kind": "exact", "column": column, "value": exact.group("value")}
    numeric = re.fullmatch(
        rf"\s*{escaped_column}\s*(?P<operator>>=|<=|==|=|>|<)\s*"
        rf"(?P<value>-?(?:\d+(?:\.\d*)?|\.\d+))\s*",
        body,
    )
    if numeric:
        operators = {">": "gt", ">=": "ge", "<": "lt", "<=": "le", "=": "eq", "==": "eq"}
        condition = {
            "kind": "numeric",
            "column": column,
            "operator": operators[numeric.group("operator")],
            "value": float(numeric.group("value")),
        }
    if condition is None:
        raise InvalidPlanError(
            "Use a supported local filter: blank, quoted exact/text match, or numeric comparison."
        )
    keep_matching = prefix.group("action").casefold() in {"keep", "include", "filter"}
    source, _ = request.default_target()
    return PlanStep(
        step_id=f"filter-{sequence}",
        operation="rows.filter",
        parameters={
            "conditions": [condition],
            "match": "all",
            "keep_matching": keep_matching,
        },
        target=StepTarget(
            source_id=source.reference.source_id,
            sheet=sheet.name,
            columns=[column],
        ),
        risk_level=RiskLevel.HIGH,
        destructive=True,
        confirmation_required=True,
        explanation="Keep or remove rows using the explicit deterministic filter condition.",
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
            _build_filter_step(clause, request, sheet, sequence),
            _build_sort_step(clause, request, sheet, sequence),
            _build_standardize_step(clause, request, sheet, sequence),
            _build_text_step(clause, request, sheet, sequence),
        ]
        matched = [step for step in candidates if step is not None]
        if len(matched) != 1:
            raise InvalidPlanError(
                "The local planner could not safely interpret one instruction clause. "
                "Use separate clauses for cleaning, standardization, filtering, sorting, "
                "and duplicate handling."
            )
        step = matched[0]
        if steps:
            step.depends_on = [steps[-1].step_id]
        steps.append(step)
    return PlannerResponse(steps=steps)
