# SheetPilot release checklist

## Build evidence

- Build from a clean Git worktree with Python 3.12 and the repository `.venv`.
- Run `scripts\package.ps1`; do not bypass setup or checks for a release candidate.
- Retain the complete Ruff, mypy, pytest, Bandit, PyInstaller, and frozen smoke-test logs.
- Require the frozen startup, disposable normal-workflow, and unknown-operation rejection
  modes to return zero, then require every packaging regression to run without a skip.
- Confirm the packaged PE machine is AMD64 and its file/product versions match
  `sheetpilot\app\version.py`.
- Verify every file against `SHA256SUMS.txt` after transferring the release folder.
- Verify the outer `SheetPilot-<version>-win64.zip` against its `.zip.sha256` sidecar before
  extraction; a sidecar distributed from the same untrusted location is not proof of publisher identity.
- Confirm the release contains no `.env`, client workbook, SQLite database, log, backup, or temp file.
- Confirm frozen self-tests use a disposable `SHEETPILOT_DATA_DIR` and do not create or prune
  the tester's normal `%LOCALAPPDATA%\SheetPilot` history.

## Clean-machine verification

Use a supported Windows 10 or Windows 11 machine without Python installed:

1. Extract the complete `SheetPilot-0.1.3-win64.zip`; do not copy only the executable.
2. Verify `SHA256SUMS.txt` with an independent SHA-256 tool.
3. Run `SheetPilot.exe --smoke-test`, `SheetPilot.exe --workflow-self-test`, and
   `SheetPilot.exe --invalid-workflow-self-test` from PowerShell; require exit code zero for
   all three.
4. Point `SHEETPILOT_FROZEN_EXE` at the release executable and run
   `pytest tests\packaging\test_frozen_smoke.py`; require every test to pass without a skip.
5. Launch `SheetPilot.exe` normally and analyse benign `.csv`, `.xlsx`, and `.xlsm` fixtures.
6. Execute one local-mode workflow, confirm preview/approval, and verify source hashes are unchanged.
7. Confirm output, JSON audit, verified backup, restore, history, and saved-template behaviour.
8. Repeat with Microsoft Excel absent. SheetPilot must still start and all deterministic operations must work.
9. If Excel is installed, separately run the tests marked `excel_com`; never use client files
   or unreviewed macros. Confirm that no new `EXCEL.EXE` process remains afterward.

## Release decision

Do not describe the build as production-ready if a core quality gate, frozen startup, source-preservation
test, clean-machine run, or security review is incomplete. Record optional integration limits separately
from deterministic local functionality.
