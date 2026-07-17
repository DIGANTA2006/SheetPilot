"""Main application window and workflow shell."""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow

from sheetpilot.app.version import __version__
from sheetpilot.core.file_profiler import FileProfiler
from sheetpilot.ui.pages.analysis_page import AnalysisPage
from sheetpilot.ui.theme import LIGHT_STYLESHEET


class MainWindow(QMainWindow):
    """SheetPilot's responsive desktop shell."""

    def __init__(self, profiler: FileProfiler | None = None) -> None:
        super().__init__()
        self.setObjectName("mainWindow")
        self.setWindowTitle(f"SheetPilot {__version__}")
        self.setMinimumSize(760, 500)
        self.resize(960, 640)
        self.setStyleSheet(LIGHT_STYLESHEET)

        self.analysis_page = AnalysisPage(profiler or FileProfiler())
        self.setCentralWidget(self.analysis_page)
