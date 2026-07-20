from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

from sheetpilot.app.version import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXACT_PIN = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^;=\s]+)(?:\s*;.+)?$")


def _normalized_package_name(value: str) -> str:
    return value.casefold().replace("_", "-")


def _call_named(tree: ast.AST, name: str) -> ast.Call:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name:
            return node
    raise AssertionError(f"The PyInstaller specification has no {name} call.")


def _literal_keyword(call: ast.Call, name: str) -> object:
    for keyword in call.keywords:
        if keyword.arg == name:
            return ast.literal_eval(keyword.value)
    raise AssertionError(f"The {name} keyword is missing from the PyInstaller specification.")


def test_release_version_metadata_has_one_consistent_version() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == __version__

    resource = (PROJECT_ROOT / "resources" / "windows_version_info.txt").read_text(encoding="utf-8")
    major, minor, patch = (int(part) for part in __version__.split("."))
    assert f"filevers=({major}, {minor}, {patch}, 0)" in resource
    assert f"prodvers=({major}, {minor}, {patch}, 0)" in resource
    assert f"StringStruct('FileVersion', '{__version__}')" in resource
    assert f"StringStruct('ProductVersion', '{__version__}')" in resource


def test_lock_file_is_exact_and_covers_declared_dependencies() -> None:
    lock_lines = [
        line.strip()
        for line in (PROJECT_ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    locked_versions: dict[str, str] = {}
    for line in lock_lines:
        match = EXACT_PIN.fullmatch(line)
        assert match is not None, f"Dependency is not exactly pinned: {line}"
        name = _normalized_package_name(match.group("name"))
        assert name not in locked_versions, f"Dependency is pinned more than once: {name}"
        locked_versions[name] = match.group("version")

    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = list(project["project"]["dependencies"])
    for group in project["project"]["optional-dependencies"].values():
        declared.extend(group)
    for requirement in declared:
        match = EXACT_PIN.fullmatch(requirement)
        assert match is not None, f"Declared dependency is not exactly pinned: {requirement}"
        requirement_name = _normalized_package_name(match.group("name"))
        assert requirement_name in locked_versions, (
            f"Declared dependency is missing from lock: {requirement_name}"
        )
        assert locked_versions[requirement_name] == match.group("version"), (
            f"Declared and locked versions differ for {requirement_name}"
        )


def test_pyinstaller_configuration_is_one_folder_with_upx_disabled() -> None:
    spec_path = PROJECT_ROOT / "SheetPilot.spec"
    tree = ast.parse(spec_path.read_text(encoding="utf-8"), filename=str(spec_path))
    executable = _call_named(tree, "EXE")
    collection = _call_named(tree, "COLLECT")

    assert _literal_keyword(executable, "exclude_binaries") is True
    assert _literal_keyword(executable, "upx") is False
    assert _literal_keyword(collection, "upx") is False


def test_release_scripts_enforce_platform_gates_and_frozen_evidence() -> None:
    setup_script = (PROJECT_ROOT / "scripts" / "setup.ps1").read_text(encoding="utf-8")
    build_script = (PROJECT_ROOT / "scripts" / "build.ps1").read_text(encoding="utf-8")
    package_script = (PROJECT_ROOT / "scripts" / "package.ps1").read_text(encoding="utf-8")
    start_script = (PROJECT_ROOT / "Start-SheetPilot.ps1").read_text(encoding="utf-8")

    assert "pip install --disable-pip-version-check --requirement $LockFile" in setup_script
    assert "-m pip check" in setup_script
    assert "Test-CompatiblePython -Path $Python" in setup_script
    assert "Quarantined an unusable project environment" in setup_script
    for script in (setup_script, build_script):
        assert "-ne 'win32'" in script
        assert "-ne '64'" in script

    assert build_script.index("'setup.ps1'") < build_script.index("Test-Path -LiteralPath $Python")
    assert "SheetPilot.spec" in build_script
    assert "--noconfirm --clean" in build_script
    assert "--smoke-test" in package_script
    assert "--workflow-self-test" in package_script
    assert "--invalid-workflow-self-test" in package_script
    assert "$env:SHEETPILOT_FROZEN_EXE = $ReleaseExecutable" in package_script
    assert "$env:SHEETPILOT_DATA_DIR = $SelfTestData" in package_script
    assert "status --porcelain --untracked-files=all" in package_script
    assert "CreateFromDirectory" in package_script
    assert "README-FIRST.txt" in package_script
    assert "Get-FileHash" in package_script
    assert "SHA256SUMS.txt" in package_script
    assert "import PySide6, polars, pydantic, sheetpilot" in start_script
    assert "Set-ExecutionPolicy" not in start_script
