from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pytestqt.qtbot import QtBot

from sheetpilot.core.preview_engine import PreviewResult, PreviewStepSummary
from sheetpilot.operations.registry import build_default_registry
from sheetpilot.ui.pages.preview_page import PreviewPage


def test_preview_exposes_operation_warning_details(qtbot: QtBot) -> None:
    page = PreviewPage(build_default_registry())
    qtbot.addWidget(page)
    page.show()
    preview = PreviewResult(
        job_id=uuid4(),
        created_at=datetime.now(UTC),
        changes=(),
        total_change_count=0,
        sampled=False,
        steps=(
            PreviewStepSummary(
                step_id="review_duplicates",
                operation="duplicate.handle",
                change_count=0,
                warnings=("Fuzzy matches require human review.",),
            ),
        ),
        plan_digest="a" * 64,
        source_digest="b" * 64,
        result_digest="c" * 64,
        preview_digest="d" * 64,
    )

    page._on_completed(preview)

    assert page.warning_heading.isVisible()
    assert page.warning_list.isVisible()
    assert page.warning_list.count() == 1
    assert "review_duplicates" in page.warning_list.item(0).text()
    assert "Fuzzy matches require human review." in page.warning_list.item(0).text()
