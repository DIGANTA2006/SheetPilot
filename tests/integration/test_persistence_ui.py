from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog
from pytest import MonkeyPatch
from pytestqt.qtbot import QtBot

from sheetpilot.app.config import AppConfig
from sheetpilot.app.persistence import PersistenceServices
from sheetpilot.core.exceptions import StorageError
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
from sheetpilot.storage.database import Database
from sheetpilot.storage.models import JobStatus, SettingKey, WorkflowTemplate
from sheetpilot.ui.main_window import MainWindow
from sheetpilot.ui.pages.results_page import ResultsPage
from sheetpilot.ui.workflow_models import JobDraft, PreparedJob, prepare_job


class _CapturingPool:
    def __init__(self) -> None:
        self.runnable: object | None = None

    def start(self, runnable: object) -> None:
        self.runnable = runnable


def _config(root: Path) -> AppConfig:
    config = AppConfig(
        data_dir=root / "data",
        backup_dir=root / "backups",
        temp_dir=root / "temp",
        database_path=root / "data" / "sheetpilot.sqlite3",
        log_dir=root / "logs",
    )
    config.ensure_directories()
    return config


def _services(config: AppConfig) -> PersistenceServices:
    database = Database(config.database_path)
    database.initialize()
    return PersistenceServices.build(database, build_default_registry())


def _prepared_cleanup(source: Path, config: AppConfig, *, output_name: str) -> PreparedJob:
    profile = FileProfiler(config.limits).profile(source)
    prepared = prepare_job(
        JobDraft(
            job_name="Customer cleanup",
            instructions="Trim surrounding spaces from the Name column.",
            output_format=OutputFormat.CSV,
            output_name=output_name,
            output_directory=source.parent,
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
                        step_id="clean_names",
                        operation="text.clean",
                        parameters={
                            "columns": ["Name"],
                            "actions": [{"kind": "trim"}],
                        },
                        target=StepTarget(
                            source_id=source_id,
                            sheet="CSV",
                            columns=["Name"],
                        ),
                        explanation="Trim surrounding whitespace",
                    )
                ]
            }
        ).model_dump()
    )
    return PreparedJob(draft=prepared.draft, plan=plan, bindings=prepared.bindings)


def test_saved_default_output_directory_is_applied_on_initial_startup(
    tmp_path: Path,
    qtbot: QtBot,
) -> None:
    config = _config(tmp_path)
    services = _services(config)
    services.settings.set(SettingKey.DEFAULT_OUTPUT_DIRECTORY, str(tmp_path))

    window = MainWindow(
        FileProfiler(config.limits),
        config=config,
        registry=build_default_registry(),
        persistence=services,
    )
    qtbot.addWidget(window)

    assert window.analysis_page.output_directory.text() == str(tmp_path.resolve())


def test_navigation_cannot_reset_sources_during_active_analysis(
    tmp_path: Path,
    qtbot: QtBot,
) -> None:
    source = tmp_path / "source.csv"
    source.write_text("ID,Name\n1,Ada\n", encoding="utf-8")
    config = _config(tmp_path)
    pool = _CapturingPool()
    window = MainWindow(
        FileProfiler(config.limits),
        config=config,
        registry=build_default_registry(),
        thread_pool=pool,  # type: ignore[arg-type]
    )
    qtbot.addWidget(window)
    window.show()
    page = window.analysis_page
    page.add_files((source,))
    page.job_name.setText("Busy navigation test")
    page.instructions.setPlainText("Analyse without changing selected sources")
    page.output_directory.setText(str(tmp_path))

    page.start_analysis()

    assert page.paths == (source.resolve(),)
    assert all(not button.isEnabled() for button in window.nav_buttons.values())
    qtbot.mouseClick(window.nav_buttons["New Job"], Qt.MouseButton.LeftButton)
    assert page.paths == (source.resolve(),)
    assert page.job_name.text() == "Busy navigation test"

    page.cancel_analysis()
    worker = pool.runnable
    assert worker is not None
    worker.run()  # type: ignore[attr-defined]
    assert all(button.isEnabled() for button in window.nav_buttons.values())


def test_saved_workflow_parameters_rebind_to_new_analysed_sources(
    tmp_path: Path,
    qtbot: QtBot,
) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    first.write_text('ID,Name\n1," Ada "\n', encoding="utf-8")
    second.write_text('ID,Name\n2," Grace "\n', encoding="utf-8")
    config = _config(tmp_path)
    services = _services(config)
    prepared = _prepared_cleanup(first, config, output_name="first_cleaned")
    template = services.workflows.save_validated(
        prepared.plan,
        name="Reusable customer cleanup",
        description="Clean customer names on the selected input sheet.",
    )
    window = MainWindow(
        FileProfiler(config.limits),
        config=config,
        registry=build_default_registry(),
        persistence=services,
    )
    qtbot.addWidget(window)
    window.show()

    window._show_navigation("Saved Workflows")
    library = window.saved_workflows_page
    assert library.table.rowCount() == 1
    parameter_values = json.loads(library.parameter_values.toPlainText())
    assert parameter_values["output_name"] == "first_cleaned"
    assert "CSV" in parameter_values.values()
    assert ["Name"] in parameter_values.values()
    parameter_values["output_name"] = "second_cleaned"
    library.parameter_values.setPlainText(json.dumps(parameter_values))
    library.use_button.click()

    assert window.stack.currentWidget() is window.analysis_page
    assert window._pending_template == template
    assert "exactly 1 new source" in window.analysis_page.status_label.text()

    second_profile = FileProfiler(config.limits).profile(second)
    replacement = JobDraft(
        job_name="Customer cleanup repeat",
        instructions="Run the reviewed cleanup with a new source.",
        output_format=OutputFormat.CSV,
        output_name="second_cleaned",
        output_directory=tmp_path,
        deadline=None,
        preserve_formatting=False,
        privacy=PrivacyMetadata(),
        profiles=(second_profile,),
    )
    window._on_job_ready(replacement)

    assert window.stack.currentWidget() is window.plan_page
    assert window._plan is not None
    assert window._plan.job_id != prepared.plan.job_id
    assert window._plan.steps[0].operation == "text.clean"
    assert window._plan.steps[0].target.source_id == window._plan.source_files[0].source_id
    assert window._plan.source_files[0].file_name == "second.csv"
    assert window._plan.output.output_name == "second_cleaned"


def test_execution_records_validation_and_saved_workflow_link(
    tmp_path: Path,
    qtbot: QtBot,
    monkeypatch: MonkeyPatch,
) -> None:
    source = tmp_path / "source.csv"
    source.write_text('ID,Name\n1," Ada "\n', encoding="utf-8")
    config = _config(tmp_path)
    registry = build_default_registry()
    services = _services(config)
    prepared = _prepared_cleanup(source, config, output_name="history_result")
    preview, _ = PreviewEngine(registry).generate(prepared.plan, prepared.bindings)
    approval = ExecutionApproval(
        job_id=prepared.plan.job_id,
        preview_digest=preview.preview_digest,
        approved=True,
    )
    page = ResultsPage(config, registry, persistence=services)
    qtbot.addWidget(page)
    page.show()

    with qtbot.waitSignal(page.execution_completed, timeout=10_000):
        page.start_execution(prepared, prepared.plan, preview, approval)

    record = services.history.get_required(prepared.plan.job_id)
    summaries = services.validations.list_recent()
    assert record.status == JobStatus.SUCCEEDED
    assert record.output_file is not None
    assert len(summaries) == 1
    assert summaries[0].job_id == prepared.plan.job_id
    assert summaries[0].passed is True
    assert summaries[0].checks_run == 0

    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("Saved cleanup", True),
    )
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        lambda *_args, **_kwargs: ("Repeatable deterministic cleanup", True),
    )
    with qtbot.waitSignal(page.workflow_saved, timeout=1000) as saved:
        page.save_workflow.click()
    template = saved.args[0]
    assert isinstance(template, WorkflowTemplate)
    linked = services.history.get_required(prepared.plan.job_id)
    assert linked.workflow_id == template.workflow_id
    assert services.templates.get_required(template.workflow_id) == template

    with qtbot.waitSignal(page.repeat_requested, timeout=1000) as repeated:
        page.repeat.click()
    assert repeated.args[0] == template


def test_storage_failure_prevents_execution_before_worker_start(
    tmp_path: Path,
    qtbot: QtBot,
    monkeypatch: MonkeyPatch,
) -> None:
    source = tmp_path / "source.csv"
    original = 'ID,Name\n1," Ada "\n'
    source.write_text(original, encoding="utf-8")
    config = _config(tmp_path)
    registry = build_default_registry()
    services = _services(config)
    prepared = _prepared_cleanup(source, config, output_name="never_written")
    preview, _ = PreviewEngine(registry).generate(prepared.plan, prepared.bindings)
    approval = ExecutionApproval(
        job_id=prepared.plan.job_id,
        preview_digest=preview.preview_digest,
        approved=True,
    )
    pool = _CapturingPool()

    def fail_history(*_args: object, **_kwargs: Any) -> None:
        raise StorageError("Local metadata is unavailable.")

    monkeypatch.setattr(services.jobs, "record_started", fail_history)
    page = ResultsPage(
        config,
        registry,
        pool,  # type: ignore[arg-type]
        persistence=services,
    )
    qtbot.addWidget(page)
    page.show()

    with qtbot.waitSignal(page.execution_failed, timeout=1000) as failed:
        page.start_execution(prepared, prepared.plan, preview, approval)

    assert failed.args == ["history_start_failed"]
    assert pool.runnable is None
    assert source.read_text(encoding="utf-8") == original
    assert not (tmp_path / "never_written.csv").exists()
    assert "No source or output file was changed" in page.status.text()
