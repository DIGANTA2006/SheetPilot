from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import openpyxl
import polars as pl
import pytest

from sheetpilot.app.config import AppConfig
from sheetpilot.core.exceptions import InvalidPlanError, OutputFailureError, SourceChangedError
from sheetpilot.core.executor import JobExecutor
from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.core.plan_runner import ROW_ID_COLUMN, DatasetKey
from sheetpilot.core.plan_schema import (
    OperationPlan,
    OutputSettings,
    PlanStep,
    SourceReference,
    StepTarget,
    ValidationRule,
)
from sheetpilot.core.preview_engine import ExecutionApproval, PreviewEngine, SourceBinding
from sheetpilot.operations.base import OperationParameters
from sheetpilot.operations.registry import build_default_registry
from sheetpilot.operations.tabular import TableOperationResult, TabularOperation
from sheetpilot.security.hashing import fingerprint_file


def _config(tmp_path: Path) -> AppConfig:
    data = tmp_path / "data"
    return AppConfig(
        data_dir=data,
        backup_dir=data / "backups",
        temp_dir=data / "temp",
        database_path=data / "metadata.sqlite3",
        log_dir=data / "logs",
    )


def _text_plan(
    source: Path, *, retain_formulas: bool = False
) -> tuple[OperationPlan, SourceBinding]:
    source_id = uuid4()
    fingerprint = fingerprint_file(source)
    plan = OperationPlan(
        job_name="Clean client names",
        source_files=[
            SourceReference(
                source_id=source_id,
                file_name=source.name,
                sha256=fingerprint.sha256,
                sheet_names=["CSV"],
            )
        ],
        steps=[
            PlanStep(
                step_id="clean-names",
                operation="text.clean",
                parameters={"columns": ["Name"], "actions": [{"kind": "trim"}]},
                target=StepTarget(source_id=source_id, sheet="CSV", columns=["Name"]),
                explanation="Trim approved name whitespace",
            )
        ],
        output=OutputSettings(
            output_name="cleaned",
            format="csv",
            preserve_formatting=False,
            retain_calculation_formulas=retain_formulas,
        ),
    )
    return (
        plan,
        SourceBinding(source_id=source_id, path=source, fingerprint=fingerprint),
    )


def _approve(plan: OperationPlan, preview_digest: str, **kwargs: Any) -> ExecutionApproval:
    return ExecutionApproval(
        job_id=plan.job_id,
        preview_digest=preview_digest,
        approved=True,
        **kwargs,
    )


def test_end_to_end_csv_transaction_preserves_source_and_audits_aggregates(
    tmp_path: Path,
) -> None:
    source = tmp_path / "client.csv"
    original = b"ID,Name\n1, Ada \n2, Grace \n"
    source.write_bytes(original)
    source_stat = source.stat()
    plan, binding = _text_plan(source)
    registry = build_default_registry()
    preview, _ = PreviewEngine(registry).generate(plan, (binding,))
    assert preview.total_change_count == 2
    assert all(change.original_value in {" Ada ", " Grace "} for change in preview.changes)

    result = JobExecutor(_config(tmp_path), registry).execute(
        plan,
        (binding,),
        preview,
        _approve(plan, preview.preview_digest),
        tmp_path / "output",
    )

    assert source.read_bytes() == original
    assert source.stat().st_mtime_ns == source_stat.st_mtime_ns
    assert result.output.path.exists()
    assert pl.read_csv(result.output.path).get_column("Name").to_list() == ["Ada", "Grace"]
    assert result.audit.path.exists()
    audit_text = result.audit.path.read_text(encoding="utf-8")
    assert " Ada " not in audit_text and " Grace " not in audit_text
    audit = json.loads(audit_text)
    assert audit["reconciliation"]["rows_changed"] == 2
    assert audit["output"]["sha256"] == result.output.fingerprint.sha256
    assert result.backups[0].backup_path.read_bytes() == original
    assert result.reconciliation.status == "passed"


def test_individual_cell_rejection_is_applied_by_stable_row_id(tmp_path: Path) -> None:
    source = tmp_path / "client.csv"
    source.write_text("ID,Name\n1, Ada \n2, Grace \n", encoding="utf-8")
    plan, binding = _text_plan(source)
    registry = build_default_registry()
    preview, _ = PreviewEngine(registry).generate(plan, (binding,))
    rejected = preview.changes[0]
    result = JobExecutor(_config(tmp_path), registry).execute(
        plan,
        (binding,),
        preview,
        _approve(
            plan,
            preview.preview_digest,
            rejected_change_ids=frozenset({rejected.change_id}),
        ),
        tmp_path / "output",
    )
    values = pl.read_csv(result.output.path).get_column("Name").to_list()
    assert values == [" Ada ", "Grace"]
    assert result.reconciliation.rows_changed == 1


def test_multiple_rejections_on_one_cell_restore_the_initial_value(tmp_path: Path) -> None:
    source = tmp_path / "client.csv"
    source.write_text("Name\nA\n", encoding="utf-8")
    source_id = uuid4()
    fingerprint = fingerprint_file(source)
    plan = OperationPlan(
        job_name="Reject chained changes",
        source_files=[
            SourceReference(
                source_id=source_id,
                file_name=source.name,
                sha256=fingerprint.sha256,
                sheet_names=["CSV"],
            )
        ],
        steps=[
            PlanStep(
                step_id="first",
                operation="text.clean",
                parameters={
                    "columns": ["Name"],
                    "actions": [{"kind": "replace", "find": "A", "replacement": "B"}],
                },
                target=StepTarget(source_id=source_id, sheet="CSV", columns=["Name"]),
                explanation="Apply the first reviewed replacement",
            ),
            PlanStep(
                step_id="second",
                operation="text.clean",
                parameters={
                    "columns": ["Name"],
                    "actions": [{"kind": "replace", "find": "B", "replacement": "C"}],
                },
                target=StepTarget(source_id=source_id, sheet="CSV", columns=["Name"]),
                explanation="Apply the second reviewed replacement",
                depends_on=["first"],
            ),
        ],
        output=OutputSettings(output_name="rejected", format="csv"),
    )
    binding = SourceBinding(source_id=source_id, path=source, fingerprint=fingerprint)
    registry = build_default_registry()
    preview, _ = PreviewEngine(registry).generate(plan, (binding,))

    result = JobExecutor(_config(tmp_path), registry).execute(
        plan,
        (binding,),
        preview,
        _approve(
            plan,
            preview.preview_digest,
            rejected_change_ids=frozenset(change.change_id for change in preview.changes),
        ),
        tmp_path / "output",
    )

    assert pl.read_csv(result.output.path).item(0, "Name") == "A"
    assert result.reconciliation.rows_changed == 0


def test_sources_with_the_same_filename_export_to_distinct_sheets(tmp_path: Path) -> None:
    first = tmp_path / "first" / "client.csv"
    second = tmp_path / "second" / "client.csv"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("ID\n1\n", encoding="utf-8")
    second.write_text("ID\n2\n", encoding="utf-8")
    first_id, second_id = uuid4(), uuid4()
    first_fingerprint = fingerprint_file(first)
    second_fingerprint = fingerprint_file(second)
    plan = OperationPlan(
        job_name="Same-name source export",
        source_files=[
            SourceReference(
                source_id=first_id,
                file_name=first.name,
                sha256=first_fingerprint.sha256,
                sheet_names=["CSV"],
            ),
            SourceReference(
                source_id=second_id,
                file_name=second.name,
                sha256=second_fingerprint.sha256,
                sheet_names=["CSV"],
            ),
        ],
        output=OutputSettings(output_name="combined", format="xlsx"),
    )
    bindings = (
        SourceBinding(source_id=first_id, path=first, fingerprint=first_fingerprint),
        SourceBinding(source_id=second_id, path=second, fingerprint=second_fingerprint),
    )
    registry = build_default_registry()
    preview, _ = PreviewEngine(registry).generate(plan, bindings)

    result = JobExecutor(_config(tmp_path), registry).execute(
        plan,
        bindings,
        preview,
        _approve(plan, preview.preview_digest),
        tmp_path / "output",
    )

    workbook = openpyxl.load_workbook(result.output.path, data_only=True)
    try:
        assert workbook.sheetnames == ["client - CSV", "client - CSV (2)"]
        assert {workbook[name]["A2"].value for name in workbook.sheetnames} == {1, 2}
    finally:
        workbook.close()


def test_merge_assigns_unique_row_identity_across_worksheets(tmp_path: Path) -> None:
    source = tmp_path / "multi.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active.title = "North"
    workbook["North"].append(["ID"])
    workbook["North"].append([1])
    workbook.create_sheet("South").append(["ID"])
    workbook["South"].append([2])
    workbook.save(source)
    workbook.close()
    source_id = uuid4()
    fingerprint = fingerprint_file(source)
    plan = OperationPlan(
        job_name="Merge regions",
        source_files=[
            SourceReference(
                source_id=source_id,
                file_name=source.name,
                sha256=fingerprint.sha256,
                sheet_names=["North", "South"],
            )
        ],
        steps=[
            PlanStep(
                step_id="merge",
                operation="tables.merge",
                parameters={"table_names": ["North", "South"]},
                target=StepTarget(source_id=source_id, sheet="North"),
                explanation="Merge the selected regional worksheets",
            )
        ],
        output=OutputSettings(output_name="merged", format="xlsx"),
    )
    binding = SourceBinding(source_id=source_id, path=source, fingerprint=fingerprint)

    preview, run = PreviewEngine(build_default_registry()).generate(plan, (binding,))

    merged = run.tables[DatasetKey(source_id, "North")]
    identities = merged.get_column(ROW_ID_COLUMN).to_list()
    assert identities == list(dict.fromkeys(identities))
    assert preview.total_change_count == 1


def test_non_day_date_difference_remains_static_when_formulas_requested(tmp_path: Path) -> None:
    source = tmp_path / "dates.csv"
    source.write_text("Start,End\n2026-01-31,2026-02-01\n", encoding="utf-8")
    source_id = uuid4()
    fingerprint = fingerprint_file(source)
    plan = OperationPlan(
        job_name="Month difference",
        source_files=[
            SourceReference(source_id=source_id, file_name=source.name, sha256=fingerprint.sha256)
        ],
        steps=[
            PlanStep(
                step_id="months",
                operation="calculate.column",
                parameters={
                    "action": {
                        "kind": "date_difference",
                        "output": "Months",
                        "start_column": "Start",
                        "end_column": "End",
                        "unit": "months",
                    }
                },
                target=StepTarget(source_id=source_id, sheet="CSV", columns=["Start", "End"]),
                explanation="Calculate the complete calendar-month difference",
            )
        ],
        output=OutputSettings(
            output_name="dates",
            format="xlsx",
            preserve_formatting=False,
            retain_calculation_formulas=True,
        ),
    )
    binding = SourceBinding(source_id=source_id, path=source, fingerprint=fingerprint)
    registry = build_default_registry()
    preview, _ = PreviewEngine(registry).generate(plan, (binding,))

    result = JobExecutor(_config(tmp_path), registry).execute(
        plan,
        (binding,),
        preview,
        _approve(plan, preview.preview_digest),
        tmp_path / "output",
    )

    workbook = openpyxl.load_workbook(result.output.path, data_only=False)
    try:
        assert workbook["CSV"]["C2"].data_type != "f"
        assert workbook["CSV"]["C2"].value == 1
    finally:
        workbook.close()


def test_destructive_execution_requires_step_specific_confirmation(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("ID\n1\n2\n", encoding="utf-8")
    source_id = uuid4()
    fingerprint = fingerprint_file(source)
    plan = OperationPlan(
        job_name="Filter rows",
        source_files=[
            SourceReference(source_id=source_id, file_name=source.name, sha256=fingerprint.sha256)
        ],
        steps=[
            PlanStep(
                step_id="filter",
                operation="rows.filter",
                parameters={
                    "conditions": [
                        {"kind": "numeric", "column": "ID", "operator": "gt", "value": 1}
                    ]
                },
                target=StepTarget(source_id=source_id, sheet="CSV", columns=["ID"]),
                explanation="Keep IDs above one",
                destructive=True,
                confirmation_required=True,
                risk_level="high",
            )
        ],
        output=OutputSettings(output_name="filtered", format="csv"),
    )
    binding = SourceBinding(source_id=source_id, path=source, fingerprint=fingerprint)
    registry = build_default_registry()
    preview, _ = PreviewEngine(registry).generate(plan, (binding,))
    with pytest.raises(InvalidPlanError, match="require confirmation"):
        JobExecutor(_config(tmp_path), registry).execute(
            plan,
            (binding,),
            preview,
            _approve(plan, preview.preview_digest),
            tmp_path / "output",
        )


def test_source_change_after_preview_aborts_before_output(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("ID,Name\n1, Ada \n", encoding="utf-8")
    plan, binding = _text_plan(source)
    registry = build_default_registry()
    preview, _ = PreviewEngine(registry).generate(plan, (binding,))
    source.write_text("ID,Name\n1,Changed\n", encoding="utf-8")
    with pytest.raises(SourceChangedError):
        JobExecutor(_config(tmp_path), registry).execute(
            plan,
            (binding,),
            preview,
            _approve(plan, preview.preview_digest),
            tmp_path / "output",
        )
    assert not (tmp_path / "output").exists()


class EmptyParameters(OperationParameters):
    pass


class FlakyOperation(TabularOperation[EmptyParameters]):
    name = "test.flaky"
    parameters_model = EmptyParameters

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, data: pl.DataFrame, parameters: EmptyParameters) -> TableOperationResult:
        del parameters
        self.calls += 1
        if self.calls > 1:
            raise InvalidPlanError("Synthetic execution failure")
        return TableOperationResult(frame=data)


def test_failed_execution_preserves_source_and_verified_backup(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    original = b"ID\n1\n"
    source.write_bytes(original)
    source_id = uuid4()
    fingerprint = fingerprint_file(source)
    plan = OperationPlan(
        job_name="Fail safely",
        source_files=[
            SourceReference(source_id=source_id, file_name=source.name, sha256=fingerprint.sha256)
        ],
        steps=[
            PlanStep(
                step_id="flaky",
                operation="test.flaky",
                target=StepTarget(source_id=source_id, sheet="CSV"),
                explanation="Synthetic operation",
            )
        ],
        output=OutputSettings(output_name="result", format="csv"),
    )
    binding = SourceBinding(source_id=source_id, path=source, fingerprint=fingerprint)
    registry = OperationRegistry([FlakyOperation()])
    preview, _ = PreviewEngine(registry).generate(plan, (binding,))
    with pytest.raises(InvalidPlanError, match="Synthetic"):
        JobExecutor(_config(tmp_path), registry).execute(
            plan,
            (binding,),
            preview,
            _approve(plan, preview.preview_digest),
            tmp_path / "output",
        )
    assert source.read_bytes() == original
    backups = list((_config(tmp_path).backup_dir).rglob("*.csv"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert not (tmp_path / "output" / "result.csv").exists()


def test_failed_validation_is_not_published_as_complete(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("ID,Name\n1,\n", encoding="utf-8")
    source_id = uuid4()
    fingerprint = fingerprint_file(source)
    plan = OperationPlan(
        job_name="Validate",
        source_files=[
            SourceReference(source_id=source_id, file_name=source.name, sha256=fingerprint.sha256)
        ],
        validations=[ValidationRule(name="required_fields", parameters={"columns": ["Name"]})],
        output=OutputSettings(output_name="validated", format="csv"),
    )
    binding = SourceBinding(source_id=source_id, path=source, fingerprint=fingerprint)
    registry = build_default_registry()
    preview, _ = PreviewEngine(registry).generate(plan, (binding,))
    with pytest.raises(OutputFailureError, match="retained only as a failed artifact"):
        JobExecutor(_config(tmp_path), registry).execute(
            plan,
            (binding,),
            preview,
            _approve(plan, preview.preview_digest),
            tmp_path / "output",
        )
    assert not (tmp_path / "output" / "validated.csv").exists()
    failed = list((tmp_path / "output" / "SheetPilot Failed").rglob("*.csv"))
    assert len(failed) == 1


def test_formula_retention_uses_generated_formula_cells(tmp_path: Path) -> None:
    source = tmp_path / "sales.csv"
    source.write_text("Qty,Price\n2,5\n", encoding="utf-8")
    source_id = uuid4()
    fingerprint = fingerprint_file(source)
    plan = OperationPlan(
        job_name="Sales formula",
        source_files=[
            SourceReference(source_id=source_id, file_name=source.name, sha256=fingerprint.sha256)
        ],
        steps=[
            PlanStep(
                step_id="total",
                operation="calculate.column",
                parameters={
                    "action": {
                        "kind": "quantity_price",
                        "output": "Total",
                        "quantity_column": "Qty",
                        "price_column": "Price",
                    }
                },
                target=StepTarget(source_id=source_id, sheet="CSV", columns=["Qty", "Price"]),
                explanation="Calculate quantity times price",
            )
        ],
        output=OutputSettings(
            output_name="sales",
            format="xlsx",
            preserve_formatting=False,
            retain_calculation_formulas=True,
        ),
    )
    binding = SourceBinding(source_id=source_id, path=source, fingerprint=fingerprint)
    registry = build_default_registry()
    preview, _ = PreviewEngine(registry).generate(plan, (binding,))
    result = JobExecutor(_config(tmp_path), registry).execute(
        plan,
        (binding,),
        preview,
        _approve(plan, preview.preview_digest),
        tmp_path / "output",
    )
    workbook = openpyxl.load_workbook(result.output.path, data_only=False)
    try:
        assert workbook["CSV"]["C2"].data_type == "f"
        assert workbook["CSV"]["C2"].value == "=A2*B2"
    finally:
        workbook.close()
