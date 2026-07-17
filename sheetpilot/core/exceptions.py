"""Typed application failures with safe user-facing messages."""


class SheetPilotError(Exception):
    """Base class for expected SheetPilot failures."""

    code = "sheetpilot_error"


class SecurityError(SheetPilotError):
    """A requested action violates a security boundary."""

    code = "security_error"


class InvalidPlanError(SheetPilotError):
    """A plan is invalid or unsafe."""

    code = "invalid_plan"


class UnknownOperationError(InvalidPlanError):
    """A plan names an operation that is not registered."""

    code = "unknown_operation"


class DuplicateOperationError(InvalidPlanError):
    """An operation name was registered more than once."""

    code = "duplicate_operation"


class SourceChangedError(SecurityError):
    """A source hash differs from the analysed fingerprint."""

    code = "source_changed"


class PathSecurityError(SecurityError):
    """A path escapes its approved boundary or has an unsafe name."""

    code = "unsafe_path"


class BackupError(SheetPilotError):
    """A backup or restore operation could not be verified."""

    code = "backup_error"


class OutputCollisionError(SecurityError):
    """An output already exists or aliases another protected file."""

    code = "output_collision"


class UnsupportedFormatError(SheetPilotError):
    """The selected file format is not supported."""

    code = "unsupported_format"


class CorruptWorkbookError(SheetPilotError):
    """A workbook is malformed or cannot be read safely."""

    code = "corrupt_workbook"


class PasswordProtectedWorkbookError(CorruptWorkbookError):
    """A workbook is encrypted and cannot be inspected safely."""

    code = "password_protected_workbook"


class FileLimitError(SecurityError):
    """An input exceeds a configured resource limit."""

    code = "file_limit_exceeded"


class ArchiveSecurityError(SecurityError):
    """An OOXML archive is malformed or unsafe to open."""

    code = "unsafe_archive"


class UserCancelledError(SheetPilotError):
    """The user safely cancelled an operation."""

    code = "user_cancelled"
