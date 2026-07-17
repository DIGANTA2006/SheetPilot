"""Spreadsheet formula-injection detection and text neutralization."""

from __future__ import annotations

import unicodedata
from typing import Any

_FORMULA_PREFIXES = ("=", "+", "-", "@")
_IGNORABLE = {"\ufeff", "\u200b", "\u200c", "\u200d", "\u2060"}


def _visible_prefix(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return "".join(
        character
        for character in normalized
        if character not in _IGNORABLE and not character.isspace()
    )


def is_formula_injection(value: Any) -> bool:
    """Flag text that spreadsheet applications could interpret as a formula."""
    if not isinstance(value, str):
        return False
    visible = _visible_prefix(value)
    return bool(visible) and visible.startswith(_FORMULA_PREFIXES)


def neutralize_formula_text(value: str) -> str:
    """Prefix dangerous text with an apostrophe; safe text and repeats are stable."""
    if value.startswith("'") or not is_formula_injection(value):
        return value
    return f"'{value}"
