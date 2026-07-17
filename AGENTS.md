# AGENTS.md — SheetPilot Repository Instructions

## Mission

Build and maintain SheetPilot, a Windows-first desktop application that safely converts Excel/CSV client instructions into reviewable, deterministic data-processing workflows.

Read `SPECIFICATION.md` completely before implementing or changing product behaviour.

## Mandatory technology choices

- Python 3.12
- PySide6 for the desktop interface
- Pydantic for validated schemas
- SQLite for local metadata, workflow templates, settings, and job history
- Polars as the primary tabular engine
- pandas only where compatibility is useful
- openpyxl for modifying existing Excel workbooks
- XlsxWriter for newly generated polished workbooks
- DuckDB for large analytical datasets
- Optional pywin32/Excel COM integration on Windows
- pytest, pytest-qt, Ruff, and Pyright or mypy
- PyInstaller for Windows packaging

Do not replace the core stack without documenting a concrete engineering reason.

## Architectural rules

1. Keep the AI planner separate from the deterministic execution engine.
2. AI output must be restricted, schema-validated JSON—not executable code.
3. Never execute arbitrary AI-generated Python, VBA, PowerShell, shell commands, SQL, or formulas.
4. Register supported spreadsheet operations in an explicit operation registry.
5. Reject unknown operations and unknown parameters.
6. Keep UI, domain logic, file-format handling, security, storage, and AI integration in separate modules.
7. Prefer small, testable functions and typed models.
8. Keep the application usable without internet access or AI.
9. Avoid global mutable state.
10. Never present placeholders or fake implementations as completed features.

## Source-file safety

- Never overwrite a client source file.
- Compute SHA-256 before analysis and verify it before execution.
- Create a timestamped backup before processing.
- Work only on copies inside an isolated temporary workspace.
- Write output atomically to a temporary file, validate it, then rename it.
- Require explicit confirmation for destructive operations.
- Provide restore-from-backup functionality.
- Preserve failed outputs separately and never label them complete.
- Sanitize all paths and file names.
- Prevent path traversal and output outside approved directories.

## Spreadsheet security

Treat every workbook and CSV as untrusted input.

- Detect macros, external links, hidden sheets, protected sheets, formulas, merged cells, and unsupported workbook features.
- Never run unknown macros automatically.
- Detect formula injection beginning with `=`, `+`, `-`, or `@`.
- Protect against archive/XML decompression bombs and malformed files.
- Limit file size, row count, column count, and memory use.
- Do not log sensitive cell values.
- Redact personal information and credentials from diagnostics.
- Do not upload client data to AI services without explicit permission.
- Prefer metadata, headers, and anonymized samples for AI planning.

## Development workflow

Treat the work as one engineering mission executed internally in phases.

For each phase:

1. Inspect existing code and Git status.
2. Define the smallest complete vertical slice.
3. Create or update tests before declaring completion.
4. Run Ruff formatting and linting.
5. Run static type checking.
6. Run relevant unit and integration tests.
7. Fix failures within scope.
8. Update documentation.
9. Create a clearly named Git commit.
10. Continue automatically only when core checks pass.

Do not stop for approval between successful internal phases unless a real external decision or credential is required.

## Git rules

- Preserve existing work.
- Do not use destructive Git commands unless explicitly required and safe.
- Never force-push.
- Commit one coherent phase or fix at a time.
- Use clear commit messages such as:
  - `feat: add workbook profiling`
  - `feat: implement deterministic cleaning operations`
  - `test: add workbook safety regressions`
  - `fix: preserve source workbook during failed export`
- Do not commit secrets, `.env` files, client data, temporary files, build output, or virtual environments.

## Testing requirements

Test all core and security behaviour, including:

- Plan-schema validation
- Unknown operation rejection
- Backup and restore
- Hash verification
- Path traversal protection
- Formula-injection detection
- Text cleaning
- Date and phone normalization
- Filtering and sorting
- Duplicate detection
- Splitting and merging
- Validation and reconciliation
- Atomic output writing
- Source preservation after success and failure
- Workbook feature warnings
- UI background workers and cancellation where practical
- PyInstaller startup smoke test

When fixing a workbook bug, add a permanent regression fixture and test.

## UI requirements

- Professional, responsive PySide6 interface
- Background workers for long-running operations
- Clear progress and safe cancellation
- No frozen UI during normal processing
- Before-and-after preview
- Risk badges and warnings
- Explicit confirmation for destructive actions
- Useful user-facing errors
- Diagnostic details available separately
- Clean light theme, structured for future dark mode

## Completion standard

Do not claim a feature is complete unless:

- Its implementation exists
- Tests cover material behaviour
- Relevant checks pass
- Documentation is updated
- Known limitations are reported honestly

Do not claim the product is production-ready while critical paths remain untested or materially incomplete.

