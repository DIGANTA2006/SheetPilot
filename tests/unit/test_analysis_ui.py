from __future__ import annotations

from pathlib import Path

from pytestqt.qtbot import QtBot

from sheetpilot.core.file_profiler import FileProfiler
from sheetpilot.ui.pages.analysis_page import AnalysisPage


def test_analysis_page_runs_profiler_off_ui_thread(tmp_path: Path, qtbot: QtBot) -> None:
    source = tmp_path / "source.csv"
    source.write_text("ID,Name\n1,Ada\n", encoding="utf-8")
    page = AnalysisPage(FileProfiler())
    qtbot.addWidget(page)
    page.show()
    page.add_files((source,))
    page.job_name.setText("Source analysis test")
    page.instructions.setPlainText("Review this source safely")
    page.output_directory.setText(str(tmp_path))

    with qtbot.waitSignal(page.analysis_completed, timeout=5000):
        page.start_analysis()

    assert page.model.rowCount() == 1
    assert page.results.isVisible()
    assert page.analyse_button.isEnabled()
    assert "Analysed 1 file" in page.status_label.text()


def test_analysis_page_rejects_unsupported_files(tmp_path: Path, qtbot: QtBot) -> None:
    source = tmp_path / "source.txt"
    source.write_text("not supported", encoding="utf-8")
    page = AnalysisPage(FileProfiler())
    qtbot.addWidget(page)
    page.add_files((source,))
    assert page.file_list.count() == 0
    assert "ignored" in page.status_label.text()
