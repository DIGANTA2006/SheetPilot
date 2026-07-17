from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from sheetpilot.core.atomic_output import AtomicOutputWriter
from sheetpilot.core.exceptions import OutputCollisionError, OutputFailureError, SourceChangedError


def test_atomic_output_validates_before_publication(tmp_path: Path) -> None:
    output = tmp_path / "output"
    failed = tmp_path / "failed"
    writer = AtomicOutputWriter(output, failed)
    destination = output / "result.txt"

    receipt = writer.write(
        destination,
        job_id=uuid4(),
        writer=lambda path: path.write_text("valid", encoding="utf-8"),
        validator=lambda path: path.read_text(encoding="utf-8") == "valid",
    )
    assert receipt.path.read_text(encoding="utf-8") == "valid"
    assert not list(output.glob("*.partial*"))


def test_validator_failure_quarantines_stage_and_never_creates_final(tmp_path: Path) -> None:
    output = tmp_path / "output"
    failed = tmp_path / "failed"
    writer = AtomicOutputWriter(output, failed)
    destination = output / "result.txt"

    def reject(path: Path) -> None:
        raise ValueError(path.name)

    with pytest.raises(OutputFailureError):
        writer.write(
            destination,
            job_id=uuid4(),
            writer=lambda path: path.write_text("malformed", encoding="utf-8"),
            validator=reject,
        )
    assert not destination.exists()
    quarantined = list(failed.rglob("*.txt"))
    assert len(quarantined) == 1 and quarantined[0].read_text() == "malformed"


def test_precommit_source_change_prevents_publication(tmp_path: Path) -> None:
    output = tmp_path / "output"
    writer = AtomicOutputWriter(output, tmp_path / "failed")

    def changed() -> None:
        raise SourceChangedError("changed")

    with pytest.raises(OutputFailureError):
        writer.write(
            output / "result.txt",
            job_id=uuid4(),
            writer=lambda path: path.write_text("valid", encoding="utf-8"),
            validator=lambda path: path.stat(),
            pre_commit=changed,
        )
    assert not (output / "result.txt").exists()


def test_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    destination = output / "result.txt"
    destination.write_text("existing", encoding="utf-8")
    writer = AtomicOutputWriter(output, tmp_path / "failed")
    with pytest.raises(OutputCollisionError):
        writer.write(
            destination,
            job_id=uuid4(),
            writer=lambda path: path.write_text("new", encoding="utf-8"),
            validator=lambda path: path.stat(),
        )
    assert destination.read_text(encoding="utf-8") == "existing"
