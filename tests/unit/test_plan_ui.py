from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtTest import QSignalSpy
from pytestqt.qtbot import QtBot

from sheetpilot.core.file_profiler import FileProfiler
from sheetpilot.core.plan_schema import OperationPlan, OutputFormat, PrivacyMetadata, RiskLevel
from sheetpilot.operations.registry import build_default_registry
from sheetpilot.ui.pages.plan_page import PlanPage
from sheetpilot.ui.workflow_models import JobDraft, PreparedJob, prepare_job


def _prepared_job(
    tmp_path: Path,
    *,
    instruction: str = "Trim Name",
    sources: tuple[tuple[str, str], ...] = (
        ("customers.csv", "ID,Name,Email\n1,Ada,ada@example.com\n"),
    ),
) -> PreparedJob:
    profiler = FileProfiler()
    profiles = []
    for name, contents in sources:
        path = tmp_path / name
        path.write_text(contents, encoding="utf-8")
        profiles.append(profiler.profile(path))
    return prepare_job(
        JobDraft(
            job_name="Local planner test",
            instructions=instruction,
            output_format=OutputFormat.CSV,
            output_name="planned-output",
            output_directory=tmp_path,
            deadline=None,
            preserve_formatting=False,
            privacy=PrivacyMetadata(),
            profiles=tuple(profiles),
        )
    )


def _page(qtbot: QtBot, prepared: PreparedJob) -> PlanPage:
    page = PlanPage(build_default_registry())
    qtbot.addWidget(page)
    page.load_job(prepared)
    page.show()
    return page


def test_local_generation_requires_digest_bound_approval_before_preview(
    tmp_path: Path,
    qtbot: QtBot,
) -> None:
    prepared = _prepared_job(
        tmp_path,
        instruction="Trim Name then sort Email descending",
    )
    page = _page(qtbot, prepared)
    spy = QSignalSpy(page.preview_requested)

    page.generate_local_plan.click()

    generated = OperationPlan.model_validate_json(page.plan_editor.toPlainText())
    assert [step.operation for step in generated.steps] == ["text.clean", "rows.sort"]
    assert generated.source_files == prepared.plan.source_files
    assert page.approve_generated_plan.isEnabled()
    assert "review required" in page.generated_approval_status.text().casefold()
    assert (
        "local vocabulary"
        in page.findChild(type(page.provider_status), "localPlannerVocabulary").text().casefold()
    )
    assert "unavailable" in page.provider_status.text().casefold()
    assert "nothing will be uploaded" in page.provider_status.text().casefold()

    page.preview_button.click()
    assert spy.count() == 0
    assert "explicit approval" in page.plan_status.text().casefold()

    page.approve_generated_plan.click()
    assert "approved for preview" in page.generated_approval_status.text().casefold()
    with qtbot.waitSignal(page.preview_requested, timeout=2_000) as emitted:
        page.preview_button.click()
    approved = emitted.args[0]
    assert isinstance(approved, OperationPlan)
    assert approved.model_dump(mode="json") == generated.model_dump(mode="json")


def test_any_generated_plan_editor_mutation_invalidates_approval(
    tmp_path: Path,
    qtbot: QtBot,
) -> None:
    page = _page(qtbot, _prepared_job(tmp_path))
    page.generate_local_plan.click()
    page.approve_generated_plan.click()
    spy = QSignalSpy(page.preview_requested)

    page.plan_editor.insertPlainText(" ")

    assert "approval is invalid" in page.generated_approval_status.text().casefold()
    page.preview_button.click()
    assert spy.count() == 0
    assert "explicit approval" in page.plan_status.text().casefold()


def test_instruction_mutation_requires_local_plan_regeneration(
    tmp_path: Path,
    qtbot: QtBot,
) -> None:
    page = _page(qtbot, _prepared_job(tmp_path))
    page.generate_local_plan.click()
    assert page.approve_generated_plan.isEnabled()

    page.local_instruction.appendPlainText("then sort Email")

    assert not page.approve_generated_plan.isEnabled()
    assert "generate a new" in page.generated_approval_status.text().casefold()


def test_unsupported_local_instruction_fails_closed_without_a_proposal(
    tmp_path: Path,
    qtbot: QtBot,
) -> None:
    page = _page(
        qtbot,
        _prepared_job(
            tmp_path,
            instruction="Email the workbook to the client and run their macro",
        ),
    )

    page.generate_local_plan.click()

    assert page.steps_table.rowCount() == 0
    assert not page.approve_generated_plan.isEnabled()
    assert "could not safely interpret" in page.plan_status.text().casefold()


def test_local_planner_uses_selected_analysed_source_and_rejects_rebinding(
    tmp_path: Path,
    qtbot: QtBot,
) -> None:
    prepared = _prepared_job(
        tmp_path,
        sources=(
            ("first.csv", "ID,Name\n1,First\n"),
            ("second.csv", "ID,Name\n2,Second\n"),
        ),
    )
    page = _page(qtbot, prepared)
    page.source.setCurrentIndex(1)

    page.generate_local_plan.click()

    generated = OperationPlan.model_validate_json(page.plan_editor.toPlainText())
    assert generated.steps[0].target.source_id == prepared.plan.source_files[1].source_id
    assert generated.steps[0].target.sheet == "CSV"
    assert generated.source_files == prepared.plan.source_files

    payload = json.loads(page.plan_editor.toPlainText())
    payload["source_files"][0]["sha256"] = "f" * 64
    page.plan_editor.setPlainText(json.dumps(payload))
    page.approve_generated_plan.click()
    assert "sources must remain exactly bound" in page.plan_status.text().casefold()


def test_local_duplicate_removal_preserves_destructive_confirmation_metadata(
    tmp_path: Path,
    qtbot: QtBot,
) -> None:
    page = _page(
        qtbot,
        _prepared_job(tmp_path, instruction="Remove duplicates using Email"),
    )

    page.generate_local_plan.click()

    generated = OperationPlan.model_validate_json(page.plan_editor.toPlainText())
    step = generated.steps[0]
    assert step.operation == "duplicates.handle"
    assert step.parameters == {"keys": ["Email"], "mode": "remove", "keep": "first"}
    assert step.risk_level == RiskLevel.HIGH
    assert step.destructive
    assert step.confirmation_required
    assert "review required" in page.generated_approval_status.text().casefold()
