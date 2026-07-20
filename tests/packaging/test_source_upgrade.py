from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERSION_0_1_2 = b'"""Application version information."""\n\n__version__ = "0.1.2"\n'
PAYLOAD_PATTERN = re.compile(
    r'^\$PayloadBase64 = @"\r?\n(?P<payload>.*?)^"@$',
    re.MULTILINE | re.DOTALL,
)
MANIFEST_PATTERN = re.compile(
    r'^\$ManifestJson = @"\r?\n(?P<manifest>.*?)^"@$',
    re.MULTILINE | re.DOTALL,
)


def _latest_upgrade_script() -> Path:
    candidates = [
        path
        for path in PROJECT_ROOT.glob("Upgrade-SheetPilot-*.ps1")
        if path.name != "Upgrade-SheetPilot-0.1.2.ps1"
    ]
    assert candidates, "A generated cumulative upgrade script is required."
    return max(
        candidates,
        key=lambda path: tuple(int(part) for part in path.stem.split("-")[-1].split(".")),
    )


def _extract_target_project(tmp_path: Path) -> tuple[Path, Path, list[dict[str, object]]]:
    upgrade = _latest_upgrade_script()
    source = upgrade.read_text(encoding="utf-8")
    payload_match = PAYLOAD_PATTERN.search(source)
    manifest_match = MANIFEST_PATTERN.search(source)
    assert payload_match is not None
    assert manifest_match is not None
    payload = base64.b64decode("".join(payload_match.group("payload").split()), validate=True)
    manifest = json.loads(manifest_match.group("manifest"))
    project = tmp_path / "SheetPilot source"
    project.mkdir()
    archive_path = tmp_path / "payload.zip"
    archive_path.write_bytes(payload)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert names == [entry["Path"] for entry in manifest]
        assert all(".." not in Path(name).parts for name in names)
        archive.extractall(project)
    shutil.copy2(PROJECT_ROOT / "requirements.lock", project / "requirements.lock")
    copied_upgrade = project / upgrade.name
    shutil.copy2(upgrade, copied_upgrade)
    return project, copied_upgrade, manifest


def _run_upgrade(script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *arguments,
        ],
        cwd=script.parent,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.skipif(os.name != "nt", reason="The source upgrader is Windows-only.")
def test_generated_source_upgrade_defaults_to_its_own_folder_and_is_idempotent(
    tmp_path: Path,
) -> None:
    _project, upgrade, _manifest = _extract_target_project(tmp_path)

    result = _run_upgrade(upgrade, "-SkipSetup", "-SkipChecks")

    assert result.returncode == 0, result.stderr
    assert "source files were already current" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="The source upgrader is Windows-only.")
def test_generated_source_upgrade_patches_known_baseline_and_keeps_backup(
    tmp_path: Path,
) -> None:
    project, upgrade, manifest = _extract_target_project(tmp_path)
    version_entry = next(
        entry for entry in manifest if entry["Path"] == "sheetpilot/app/version.py"
    )
    version_path = project / "sheetpilot" / "app" / "version.py"
    version_path.write_bytes(VERSION_0_1_2)

    result = _run_upgrade(upgrade, "-SkipSetup", "-SkipChecks")

    assert result.returncode == 0, result.stderr
    assert version_path.read_bytes() == (PROJECT_ROOT / "sheetpilot/app/version.py").read_bytes()
    assert hashlib.sha256(VERSION_0_1_2).hexdigest() in version_entry["BaselineHashes"]
    assert "backed up outside" in result.stdout
    backups = list(tmp_path.glob("_sheetpilot_upgrade_backup_*"))
    assert len(backups) == 1
    assert (backups[0] / "sheetpilot/app/version.py").read_bytes() == VERSION_0_1_2
    assert (backups[0] / "upgrade-backup-manifest.json").is_file()


@pytest.mark.skipif(os.name != "nt", reason="The source upgrader is Windows-only.")
def test_generated_source_upgrade_restores_sources_when_post_patch_checks_fail(
    tmp_path: Path,
) -> None:
    project, upgrade, _manifest = _extract_target_project(tmp_path)
    version_path = project / "sheetpilot" / "app" / "version.py"
    version_path.write_bytes(VERSION_0_1_2)

    result = _run_upgrade(upgrade, "-SkipSetup")

    assert result.returncode != 0
    assert "changed source files were restored" in result.stderr
    assert version_path.read_bytes() == VERSION_0_1_2
    assert not (project / ".sheetpilot-upgrade.lock").exists()
