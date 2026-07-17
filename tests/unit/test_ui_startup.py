from __future__ import annotations

from pytestqt.qtbot import QtBot

from sheetpilot.ui.main_window import MainWindow


def test_main_window_constructs(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window.windowTitle().startswith("SheetPilot")
    assert window.centralWidget() is not None
