"""In-memory preview change records; cell values are never persisted to audit logs."""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sheetpilot.core.plan_schema import RiskLevel


class ChangeKind(StrEnum):
    CELL_CHANGED = "cell_changed"
    ROW_DELETED = "row_deleted"
    ROW_ADDED = "row_added"
    ROW_REORDERED = "row_reordered"
    COLUMN_ADDED = "column_added"
    COLUMN_DELETED = "column_deleted"


class ApprovalState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ChangeRecord(BaseModel):
    """A review record that may contain client values only in process memory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    change_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: ChangeKind
    source_id: UUID
    file_name: str
    sheet: str
    row: int | None = Field(default=None, ge=1)
    internal_row_id: int | None = Field(default=None, exclude=True)
    column: str | None = None
    original_value: Any = None
    proposed_value: Any = None
    reason: str
    risk_level: RiskLevel
    step_id: str
    approval_state: ApprovalState = ApprovalState.PENDING
