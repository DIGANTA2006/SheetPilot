from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path, PurePosixPath

import pefile
import pytest

from sheetpilot.app.version import __version__


def _frozen_executable() -> Path:
    executable_value = os.environ.get("SHEETPILOT_FROZEN_EXE")
    if not executable_value:
        pytest.skip("Set SHEETPILOT_FROZEN_EXE to exercise an existing one-folder build.")
    executable = Path(executable_value).resolve()
    assert executable.is_file()
    return executable


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.mark.packaging
def test_frozen_release_is_a_versioned_win64_one_folder_build() -> None:
    executable = _frozen_executable()
    assert (executable.parent / "_internal").is_dir()

    image = pefile.PE(str(executable), fast_load=False)
    try:
        assert image.FILE_HEADER.Machine == 0x8664
        assert image.VS_FIXEDFILEINFO
        fixed = image.VS_FIXEDFILEINFO[0]
        expected = (*[int(part) for part in __version__.split(".")], 0)
        file_version = (
            fixed.FileVersionMS >> 16,
            fixed.FileVersionMS & 0xFFFF,
            fixed.FileVersionLS >> 16,
            fixed.FileVersionLS & 0xFFFF,
        )
        product_version = (
            fixed.ProductVersionMS >> 16,
            fixed.ProductVersionMS & 0xFFFF,
            fixed.ProductVersionLS >> 16,
            fixed.ProductVersionLS & 0xFFFF,
        )
        assert file_version == expected
        assert product_version == expected
    finally:
        image.close()


@pytest.mark.packaging
@pytest.mark.parametrize(
    "argument",
    ["--smoke-test", "--workflow-self-test", "--invalid-workflow-self-test"],
)
def test_frozen_release_self_tests_when_build_is_supplied(argument: str) -> None:
    executable = _frozen_executable()
    completed = subprocess.run(
        [str(executable), argument],
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0


@pytest.mark.packaging
def test_release_checksum_manifest_covers_and_verifies_every_payload_file() -> None:
    executable = _frozen_executable()
    release_root = executable.parent
    checksum_path = release_root / "SHA256SUMS.txt"
    if not checksum_path.is_file():
        pytest.skip("Point SHEETPILOT_FROZEN_EXE at a packaged release to verify checksums.")

    checksum_entries: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        digest, relative_value = line.split("  ", maxsplit=1)
        assert re.fullmatch(r"[0-9a-f]{64}", digest)
        relative = PurePosixPath(relative_value)
        assert not relative.is_absolute()
        assert ".." not in relative.parts
        normalized = relative.as_posix()
        assert normalized not in checksum_entries
        checksum_entries[normalized] = digest

    payload_files = {
        path.relative_to(release_root).as_posix()
        for path in release_root.rglob("*")
        if path.is_file() and path != checksum_path
    }
    assert checksum_entries
    assert set(checksum_entries) == payload_files
    for relative, expected_digest in checksum_entries.items():
        path = release_root.joinpath(*PurePosixPath(relative).parts)
        assert _sha256(path) == expected_digest
