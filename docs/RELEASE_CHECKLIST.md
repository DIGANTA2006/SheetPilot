# SheetPilot release checklist

## Build evidence

- Build from a clean Git worktree with Python 3.12 and the repository `.venv`.
- Run `scripts\package.ps1`; do not bypass setup or checks for a release candidate.
- Retain the complete Ruff, mypy, pytest, Bandit, PyInstaller, and frozen smoke-test logs.
- Verify every file against `SHA256SUMS.txt` after transferring the release folder.
- Confirm the release contains no `.env`, client workbook, SQLite database, log, backup, or temp file.

## Clean-machine verification

Use a supported Windows 10 or Windows 11 machine without Python installed:

1. Copy the complete `SheetPilot-0.1.0-win64` folder; do not copy only the executable.
2. Verify `SHA256SUMS.txt` with an independent SHA-256 tool.
3. Run `SheetPilot.exe --smoke-test` from PowerShell and require exit code zero.
4. Launch `SheetPilot.exe` normally and analyse benign `.csv`, `.xlsx`, and `.xlsm` fixtures.
5. Execute one local-mode workflow, confirm preview/approval, and verify source hashes are unchanged.
6. Confirm output, JSON audit, verified backup, restore, history, and saved-template behaviour.
7. Repeat with Microsoft Excel absent. SheetPilot must still start and all deterministic operations must work.
8. If Excel is installed, separately run the tests marked `excel_com`; never use client files.

## Release decision

Do not describe the build as production-ready if a core quality gate, frozen startup, source-preservation
test, clean-machine run, or security review is incomplete. Record optional integration limits separately
from deterministic local functionality.
