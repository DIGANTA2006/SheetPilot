from __future__ import annotations

from sheetpilot.app.workflow_self_test import (
    run_invalid_workflow_self_test,
    run_normal_workflow_self_test,
)


def test_normal_xlsx_workflow_self_test_produces_complete_safety_evidence() -> None:
    evidence = run_normal_workflow_self_test()

    assert evidence.source_sha256 == evidence.backup_sha256
    assert evidence.output_sha256 != evidence.source_sha256
    assert evidence.audit_sha256 not in {evidence.source_sha256, evidence.output_sha256}
    assert evidence.original_row_count == 2
    assert evidence.final_row_count == 2
    assert evidence.rows_changed == 2
    assert evidence.reconciliation_status == "passed"


def test_invalid_workflow_self_test_rejects_unknown_operation_without_artifacts() -> None:
    evidence = run_invalid_workflow_self_test()

    assert evidence.rejected_operation == "selftest.unknown-operation"
    assert len(evidence.source_sha256) == 64
    assert evidence.artifact_count == 0
