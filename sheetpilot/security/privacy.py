"""Privacy-safe diagnostic redaction."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_SENSITIVE_KEYS = re.compile(
    r"password|passwd|secret|api[_-]?key|token|otp|bank|account|card|credential",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d .()-]{7,}\d)(?!\d)")
_LONG_SECRET = re.compile(r"\b(?:sk-|pk_)[A-Za-z0-9_-]{12,}\b")
REDACTED = "[REDACTED]"


def redact_text(value: str) -> str:
    """Remove common personal and credential-like patterns from diagnostics."""
    value = _EMAIL.sub(REDACTED, value)
    value = _PHONE.sub(REDACTED, value)
    return _LONG_SECRET.sub(REDACTED, value)


def redact(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact structured diagnostic metadata."""
    if key is not None and _SENSITIVE_KEYS.search(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(item_key): redact(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact(item) for item in value]
    return value
