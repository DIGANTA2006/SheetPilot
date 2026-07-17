from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog
from pytestqt.qtbot import QtBot

from sheetpilot.app.config import AppConfig
from sheetpilot.core.executor import ExecutionResult
from sheetpilot.core.file_profiler import FileProfiler
from sheetpilot.core.plan_schema import (
    OperationPlan,
    OutputFormat,
    PlanStep,
    PrivacyMetadata,
    StepTarget,
)
from sheetpilot.core.preview_engine import ExecutionApproval, PreviewEngine
from sheetpilot.operations.registry import build_default_registry
from sheetpilot.ui.main_window import MainWindow
from sheetpilot.ui.pages.results_page import ResultsPage
from sheetpilot.ui.workflow_models import JobDraft, prepare_job


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


def test_failed_execution_exposes_verified_backup_and_restores_to_new_file(
    tmp_path: Path,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.csv"
    original = 'ID,Name\n1," Ada "\n'
    source.write_text(original, encoding="utf-8")
    config = _test_config(tmp_path)
    config.ensure_directories()
    registry = build_default_registry()
    profile = FileProfiler(config.limits).profile(source)
    prepared = prepare_job(
        JobDraft(
            job_name="Protected failure",
            instructions="Trim names without overwriting an existing output.",
            output_format=OutputFormat.CSV,
            output_name="existing_output",
            output_directory=tmp_path,
            deadline=None,
            preserve_formatting=False,
            privacy=PrivacyMetadata(),
            profiles=(profile,),
        )
    )
    source_id = prepared.plan.source_files[0].source_id
    plan = OperationPlan.model_validate(
        prepared.plan.model_copy(
            update={
                "steps": [
                    PlanStep(
                        step_id="trim_names",
                        operation="text.clean",
                        parameters={"columns": ["Name"], "actions": [{"kind": "trim"}]},
                        target=StepTarget(source_id=source_id, sheet="CSV", columns=["Name"]),
                        explanation="Trim surrounding whitespace",
                    )
                ]
            }
        ).model_dump()
    )
    preview, _ = PreviewEngine(registry).generate(plan, prepared.bindings)
    approval = ExecutionApproval(
        job_id=plan.job_id,
        preview_digest=preview.preview_digest,
        approved=True,
    )
    (tmp_path / "existing_output.csv").write_text("existing", encoding="utf-8")
    page = ResultsPage(config, registry)
    qtbot.addWidget(page)
    page.show()

    with qtbot.waitSignal(page.execution_failed, timeout=10_000):
        page.start_execution(prepared, plan, preview, approval)

    assert source.read_text(encoding="utf-8") == original
    assert page.backup_choice.isVisible()
    assert page.backup_choice.count() == 1
    assert page.restore.isVisible()
    assert "verified backup" in page.status.text().casefold()
    receipt = page._available_backups[0]
    assert receipt.backup_path.is_file()

    restored = tmp_path / "restored_source.csv"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(restored), ""),
    )
    page.restore.click()
    qtbot.waitUntil(restored.is_file, timeout=5_000)
    assert restored.read_text(encoding="utf-8") == original
