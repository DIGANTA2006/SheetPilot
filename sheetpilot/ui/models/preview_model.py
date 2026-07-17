"""Filterable preview model with practical per-cell approval controls."""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)

from sheetpilot.core.change_models import ChangeKind, ChangeRecord
from sheetpilot.core.plan_schema import RiskLevel

_HEADERS = (
    "Accept",
    "File",
    "Sheet",
    "Row",
    "Column",
    "Original",
    "Proposed",
    "Reason",
    "Risk",
    "Step",
)
_ROOT_INDEX = QModelIndex()


def _display_value(value: object) -> str:
    if value is None:
        return ""
    rendered = str(value)
    return rendered if len(rendered) <= 240 else f"{rendered[:237]}..."


class PreviewTableModel(QAbstractTableModel):
    """Hold preview values only in process memory and track explicit rejections."""

    def __init__(self) -> None:
        super().__init__()
        self._changes: tuple[ChangeRecord, ...] = ()
        self._rejected: set[str] = set()

    def set_changes(self, changes: tuple[ChangeRecord, ...]) -> None:
        self.beginResetModel()
        self._changes = changes
        self._rejected.clear()
        self.endResetModel()

    @property
    def rejected_change_ids(self) -> frozenset[str]:
        return frozenset(self._rejected)

    def change_at(self, row: int) -> ChangeRecord:
        return self._changes[row]

    def rowCount(  # noqa: N802
        self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX
    ) -> int:
        return 0 if parent.isValid() else len(self._changes)

    def columnCount(  # noqa: N802
        self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX
    ) -> int:
        return 0 if parent.isValid() else len(_HEADERS)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return _HEADERS[section]
        return None

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if not index.isValid():
            return None
        change = self._changes[index.row()]
        if index.column() == 0 and role == Qt.ItemDataRole.CheckStateRole:
            if change.kind != ChangeKind.CELL_CHANGED:
                return None
            return (
                Qt.CheckState.Unchecked
                if change.change_id in self._rejected
                else Qt.CheckState.Checked
            )
        if role == Qt.ItemDataRole.ToolTipRole:
            return change.reason
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        values = (
            "" if change.kind == ChangeKind.CELL_CHANGED else "Required",
            change.file_name,
            change.sheet,
            change.row or "",
            change.column or "",
            _display_value(change.original_value),
            _display_value(change.proposed_value),
            change.reason,
            change.risk_level.value.title(),
            change.step_id,
        )
        return values[index.column()]

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        flags = super().flags(index)
        if (
            index.isValid()
            and index.column() == 0
            and self._changes[index.row()].kind == ChangeKind.CELL_CHANGED
        ):
            return flags | Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(  # noqa: N802
        self,
        index: QModelIndex | QPersistentModelIndex,
        value: object,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if not index.isValid() or index.column() != 0 or role != Qt.ItemDataRole.CheckStateRole:
            return False
        change = self._changes[index.row()]
        if change.kind != ChangeKind.CELL_CHANGED:
            return False
        if value == Qt.CheckState.Unchecked.value:
            self._rejected.add(change.change_id)
        else:
            self._rejected.discard(change.change_id)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        return True


class PreviewFilterModel(QSortFilterProxyModel):
    """Combine search and review-category filters without duplicating client values."""

    def __init__(self) -> None:
        super().__init__()
        self._search = ""
        self._category = "all"

    def set_search(self, value: str) -> None:
        self._search = value.casefold().strip()
        self.invalidateRowsFilter()

    def set_category(self, value: str) -> None:
        self._category = value
        self.invalidateRowsFilter()

    def filterAcceptsRow(  # noqa: N802
        self,
        source_row: int,
        source_parent: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        source = self.sourceModel()
        if not isinstance(source, PreviewTableModel):
            return False
        change = source.change_at(source_row)
        if self._category == "changed" and change.kind != ChangeKind.CELL_CHANGED:
            return False
        if self._category == "deleted" and change.kind != ChangeKind.ROW_DELETED:
            return False
        if self._category == "warnings" and change.risk_level not in {
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        }:
            return False
        if not self._search:
            return True
        haystack = " ".join(
            (
                change.file_name,
                change.sheet,
                change.column or "",
                _display_value(change.original_value),
                _display_value(change.proposed_value),
                change.reason,
                change.step_id,
            )
        ).casefold()
        return self._search in haystack
