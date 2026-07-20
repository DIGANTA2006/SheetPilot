from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from sheetpilot.core.backup_service import BackupService
from sheetpilot.core.exceptions import (
    BackupError,
    OutputCollisionError,
    PathSecurityError,
    SourceChangedError,
)
from sheetpilot.core.workspace import IsolatedWorkspace
from sheetpilot.security.formula_guard import is_formula_injection, neutralize_formula_text
from sheetpilot.security.hashing import fingerprint_file, verify_fingerprint
from sheetpilot.security.path_guard import ensure_within, safe_output_path, sanitize_filename


def test_hash_verification_detects_changed_source(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_bytes(b"name\nAda\n")
    fingerprint = fingerprint_file(source)
    assert len(fingerprint.sha256) == 64
    source.write_bytes(b"name\nGrace\n")
    with pytest.raises(SourceChangedError):
        verify_fingerprint(source, fingerprint)


@pytest.mark.parametrize(
    "value",
    ["=SUM(A1:A2)", "+cmd", "-1+2", "@lookup", " \t\ufeff=unsafe", "\u200b+unsafe"],
)
def test_formula_injection_detection(value: str) -> None:
    assert is_formula_injection(value)
    neutralized = neutralize_formula_text(value)
    assert neutralized.startswith("'")
    assert neutralize_formula_text(neutralized) == neutralized


@pytest.mark.parametrize("value", [42, -12, "ordinary text", "'already text"])
def test_safe_formula_values_are_not_flagged(value: object) -> None:
    assert not is_formula_injection(value)


def test_windows_filename_sanitization() -> None:
    assert sanitize_filename("CON.csv") == "_CON.csv"
    assert "/" not in sanitize_filename("unsafe/name?.csv")
    assert "?" not in sanitize_filename("unsafe/name?.csv")


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "approved"
    root.mkdir()
    with pytest.raises(PathSecurityError):
        ensure_within(root / ".." / "escaped.csv", root)
    with pytest.raises(PathSecurityError):
        safe_output_path(root, "../escaped", extension="csv")


def test_output_collision_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "result.csv").write_text("existing", encoding="utf-8")
    with pytest.raises(OutputCollisionError):
        safe_output_path(tmp_path, "result", extension="csv")


def test_backup_restore_and_workspace_preserve_source(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    original = b"id,name\n1,Ada\n"
    source.write_bytes(original)
    expected = fingerprint_file(source)
    service = BackupService(tmp_path / "backups")
    receipt = service.create_backup(source, job_id=uuid4(), expected=expected)
    assert receipt.backup_path.read_bytes() == original
    assert receipt.manifest_path.exists()

    with IsolatedWorkspace(tmp_path / "temp", uuid4()) as workspace:
        working_copy = workspace.copy_source(source, expected)
        working_copy.write_bytes(b"changed working copy")
        active_path = workspace.path
        assert active_path is not None and active_path.exists()
    assert active_path is not None and not active_path.exists()
    assert source.read_bytes() == original

    restored = service.restore_to_new_file(receipt, tmp_path / "restored.csv")
    assert restored.read_bytes() == original
    assert source.read_bytes() == original


def test_restore_does_not_overwrite_a_destination_created_during_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.csv"
    source.write_text("ID\n1\n", encoding="utf-8")
    service = BackupService(tmp_path / "backups")
    receipt = service.create_backup(source, job_id=uuid4())
    destination = tmp_path / "restored.csv"
    real_copy = shutil.copy2

    def raced_copy(source_path: Path, partial_path: Path) -> Path:
        copied = real_copy(source_path, partial_path)
        destination.write_text("created by another process", encoding="utf-8")
        return copied

    monkeypatch.setattr("sheetpilot.core.backup_service.shutil.copy2", raced_copy)

    with pytest.raises(BackupError):
        service.restore_to_new_file(receipt, destination)
    assert destination.read_text(encoding="utf-8") == "created by another process"
