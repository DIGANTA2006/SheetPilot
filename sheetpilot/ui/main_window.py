"""Minimal real application shell used by the foundation phase."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QMainWindow, QVBoxLayout, QWidget

from sheetpilot.app.version import __version__
from sheetpilot.ui.theme import LIGHT_STYLESHEET


class MainWindow(QMainWindow):
    """SheetPilot's responsive desktop shell."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("mainWindow")
        self.setWindowTitle(f"SheetPilot {__version__}")
        self.setMinimumSize(760, 500)
        self.resize(960, 640)
        self.setStyleSheet(LIGHT_STYLESHEET)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(48, 44, 48, 44)
        layout.setSpacing(14)

        title = QLabel("SheetPilot")
        title.setObjectName("title")
        subtitle = QLabel("Safe, deterministic Excel and CSV workflows")
        subtitle.setObjectName("subtitle")

        status_card = QFrame()
        status_card.setObjectName("statusCard")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(24, 22, 24, 22)
        status_title = QLabel("Local foundation ready")
        status_title.setObjectName("statusTitle")
        status_body = QLabel(
            "Source preservation, restricted plans, registry validation, backups, "
            "isolated workspaces, and local metadata storage are active."
        )
        status_body.setObjectName("statusBody")
        status_body.setWordWrap(True)
        status_layout.addWidget(status_title)
        status_layout.addWidget(status_body)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(22)
        layout.addWidget(status_card)
        layout.addStretch(1)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setCentralWidget(root)
