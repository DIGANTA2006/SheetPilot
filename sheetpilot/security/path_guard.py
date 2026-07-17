"""Windows-aware path and filename boundary enforcement."""

from __future__ import annotations

import re
from pathlib import Path

from sheetpilot.core.exceptions import OutputCollisionError, PathSecurityError

_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str, *, fallback: str = "output") -> str:
    """Return a safe basename without accepting path syntax or device names."""
    basename = Path(name).name
    cleaned = _UNSAFE_CHARS.sub("_", basename).strip().rstrip(". ")
    if cleaned in {"", ".", ".."}:
        cleaned = fallback
    stem = cleaned.split(".", maxsplit=1)[0].upper()
    if stem in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:180]


def ensure_within(path: Path, approved_root: Path) -> Path:
    """Resolve a path and require it to remain beneath an approved root."""
    resolved_root = approved_root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise PathSecurityError("The path is outside the approved directory.") from error
    return resolved


def safe_output_path(
    approved_root: Path,
    requested_name: str,
    *,
    extension: str,
    protected_paths: tuple[Path, ...] = (),
) -> Path:
    """Build a non-colliding output path from a basename and forced extension."""
    if Path(requested_name).name != requested_name or requested_name in {".", ".."}:
        raise PathSecurityError("Output names must not contain directories.")
    suffix = extension if extension.startswith(".") else f".{extension}"
    safe_stem = Path(sanitize_filename(requested_name)).stem
    candidate = ensure_within(approved_root / f"{safe_stem}{suffix.lower()}", approved_root)
    canonical = str(candidate).casefold()
    if any(str(path.resolve()).casefold() == canonical for path in protected_paths):
        raise OutputCollisionError("The output path aliases a protected file.")
    if candidate.exists():
        raise OutputCollisionError("The output file already exists.")
    return candidate
