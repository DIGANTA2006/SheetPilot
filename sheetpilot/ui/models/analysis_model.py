"""Table model for file and sheet analysis summaries."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt

from sheetpilot.core.profile_models import FileProfile, WarningSeverity

_HEADERS = ("File", "Type", "Sheet", "Rows", "Columns", "Warnings", "Risk")
_RISK_ORDER = {
    WarningSeverity.INFO: 0,
    WarningSeverity.WARNING: 1,
    WarningSeverity.HIGH: 2,
    WarningSeverity.BLOCKING: 3,
}
_ROOT_INDEX = QModelIndex()


class AnalysisTableModel(QAbstractTableModel):
    """Flatten profile summaries without exposing client cell values."""

    def __init__(self, profiles: tuple[FileProfile, ...] = ()) -> None:
        super().__init__()
        self._profiles = profiles
        self._rows = [(profile, sheet) for profile in profiles for sheet in profile.sheets]

    def set_profiles(self, profiles: tuple[FileProfile, ...]) -> None:
        self.beginResetModel()
        self._profiles = profiles
        self._rows = [(profile, sheet) for profile in profiles for sheet in profile.sheets]
        self.endResetModel()

    def rowCount(  # noqa: N802
        self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX
    ) -> int:
        return 0 if parent.isValid() else len(self._rows)

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
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        profile, sheet = self._rows[index.row()]
        sheet_warnings = [
            warning for warning in profile.warnings if warning.sheet in {None, sheet.name}
        ]
        severity = (
            max(sheet_warnings, key=lambda warning: _RISK_ORDER[warning.severity]).severity.value
            if sheet_warnings
            else "none"
        )
        values: tuple[object, ...] = (
            profile.file_name,
            profile.file_type.upper(),
            sheet.name,
            sheet.used_rows,
            sheet.used_columns,
            len(sheet_warnings),
            severity.title(),
        )
        return values[index.column()]
