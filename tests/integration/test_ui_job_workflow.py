from __future__ import annotations

import json
from pathlib import Path

from pytestqt.qtbot import QtBot

from sheetpilot.app.config import AppConfig
from sheetpilot.core.executor import ExecutionResult
from sheetpilot.core.file_profiler import FileProfiler
from sheetpilot.core.plan_schema import OutputFormat, PrivacyMetadata
from sheetpilot.operations.registry import build_default_registry
from sheetpilot.ui.main_window import MainWindow
from sheetpilot.ui.workflow_models import JobDraft


def _test_config(root: Path) -> AppConfig:
    return AppConfig(
        data_dir=root / "data",
        backup_dir=root / "backups",
        temp_dir=root / "temp",
        database_path=root / "data" / "sheetpilot.sqlite3",
        log_dir=root / "logs",
    )


def test_ui_drives_safe_job_from_analysis_to_audited_result(tmp_path: Path, qtbot: QtBot) -> None:
    source = tmp_path / "source.csv"
    original = 'ID,Name\n1," Ada "\n'
    source.write_text(original, encoding="utf-8")
    config = _test_config(tmp_path)
    config.ensure_directories()
    profiler = FileProfiler(config.limits)
    profile = profiler.profile(source)
    window = MainWindow(
        profiler,
        config=config,
        registry=build_default_registry(),
    )
    qtbot.addWidget(window)
    window.show()

    draft = JobDraft(
        job_name="Trim customer names",
        instructions="Trim surrounding whitespace from customer names.",
        output_format=OutputFormat.CSV,
        output_name="ui_result",
        output_directory=tmp_path,
        deadline=None,
        preserve_formatting=False,
        privacy=PrivacyMetadata(),
        profiles=(profile,),
    )
    window._on_job_ready(draft)
    assert window.stack.currentWidget() is window.plan_page

    window.plan_page.operation.setCurrentText("text.clean")
    window.plan_page.parameters.setPlainText(
        json.dumps({"columns": ["Name"], "actions": [{"kind": "trim"}]})
    )
    window.plan_page.target_columns.setText("Name")
    window.plan_page.explanation.setText("Trim surrounding whitespace")
    window.plan_page.add_step.click()
    assert window.plan_page.steps_table.rowCount() == 1

    with qtbot.waitSignal(window.preview_page.preview_completed, timeout=10_000):
        window.plan_page.preview_button.click()
    assert window.stack.currentWidget() is window.preview_page
    assert window.preview_page.model.rowCount() == 1

    window.preview_page.approval.setChecked(True)
    assert window.preview_page.execute_button.isEnabled()
    with qtbot.waitSignal(window.results_page.execution_completed, timeout=15_000) as completed:
        window.preview_page.execute_button.click()

    result = completed.args[0]
    assert isinstance(result, ExecutionResult)
    assert result.output.path == tmp_path / "ui_result.csv"
    assert result.output.path.is_file()
    assert result.audit.path.is_file()
    assert result.backups[0].backup_path.is_file()
    assert result.reconciliation.rows_changed == 1
    assert source.read_text(encoding="utf-8") == original
    assert window.stack.currentWidget() is window.results_page
