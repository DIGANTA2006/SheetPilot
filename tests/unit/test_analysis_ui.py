from __future__ import annotations

from pathlib import Path

from pytestqt.qtbot import QtBot

from sheetpilot.core.file_profiler import FileProfiler
from sheetpilot.ui.pages.analysis_page import AnalysisPage
from sheetpilot.ui.workflow_models import JobDraft


class _CapturingPool:
    def __init__(self) -> None:
        self.runnable: object | None = None

    def start(self, runnable: object) -> None:
        self.runnable = runnable


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

    with qtbot.waitSignal(page.job_ready, timeout=1000) as ready:
        page.continue_button.click()
    draft = ready.args[0]
    assert isinstance(draft, JobDraft)
    assert draft.job_name == "Source analysis test"
    assert draft.instructions == "Review this source safely"
    assert draft.profiles[0].source_path == source.resolve()


def test_analysis_page_rejects_unsupported_files(tmp_path: Path, qtbot: QtBot) -> None:
    source = tmp_path / "source.txt"
    source.write_text("not supported", encoding="utf-8")
    page = AnalysisPage(FileProfiler())
    qtbot.addWidget(page)
    page.add_files((source,))
    assert page.file_list.count() == 0
    assert "ignored" in page.status_label.text()


def test_analysis_blocks_source_mutation_and_honours_early_cancellation(
    tmp_path: Path, qtbot: QtBot
) -> None:
    source = tmp_path / "source.csv"
    added_while_running = tmp_path / "late.csv"
    source.write_text("ID,Name\n1,Ada\n", encoding="utf-8")
    added_while_running.write_text("ID,Name\n2,Grace\n", encoding="utf-8")
    pool = _CapturingPool()
    page = AnalysisPage(FileProfiler(), pool)  # type: ignore[arg-type]
    qtbot.addWidget(page)
    page.show()
    page.add_files((source,))
    page.job_name.setText("Cancellation test")
    page.instructions.setPlainText("Analyse without changing the selected sources")
    page.output_directory.setText(str(tmp_path))

    page.start_analysis()
    assert not page.acceptDrops()
    page.add_files((added_while_running,))
    assert page.paths == (source.resolve(),)
    assert "already running" in page.status_label.text()

    page.cancel_analysis()
    worker = pool.runnable
    assert worker is not None
    with qtbot.waitSignal(page._worker.signals.cancelled, timeout=1000):
        worker.run()  # type: ignore[attr-defined]
    assert page.acceptDrops()
    assert "cancelled safely" in page.status_label.text()
