from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.packaging
def test_frozen_application_startup_when_build_is_supplied() -> None:
    executable_value = os.environ.get("SHEETPILOT_FROZEN_EXE")
    if not executable_value:
        pytest.skip("Set SHEETPILOT_FROZEN_EXE to exercise an existing one-folder build.")
    executable = Path(executable_value).resolve()
    assert executable.is_file()
    completed = subprocess.run(
        [str(executable), "--smoke-test"],
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0
