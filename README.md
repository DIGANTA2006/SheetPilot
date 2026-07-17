# SheetPilot

SheetPilot is a Windows-first desktop application for safe, reviewable, deterministic
Excel and CSV workflows. Client source files are treated as untrusted input and are
never modified in place.

## Current development status

Phases 1 and 2 establish the safety foundation and non-destructive analysis workflow:
a strict Pydantic plan schema, explicit operation registry, local SQLite metadata,
privacy-filtered logging, hashing, verified backup/restore, isolated workspaces, path and
formula guards, bounded OOXML archive inspection, Polars CSV profiling, openpyxl workbook
profiling, and a background PySide6 source-analysis screen.

Analysis reports aggregate metadata only: sheets, dimensions, headers, inferred types,
blanks, duplicates, candidate keys, formulas, merged/protected/hidden content, macros,
external links, named ranges, formula-injection risk, and data-quality warning counts.
It verifies the source SHA-256 again after analysis and aborts if the file changed.

Phase 3A adds explicit Polars operations for the complete text-cleaning action set,
review-safe state/district/category/telephone/email/date/numeric standardization, typed
exact/text/numeric/date/blank/list filtering, and stable multi-key sorting. Uncertain
standardizations are left unchanged and counted for review; filtering and any explicit
null replacement are dynamically marked destructive for confirmation.

Phase 3B adds exact duplicate mark/remove/move and explicit complementary merging,
review-only fuzzy groups, rename/reorder/add/remove/split/combine/extract/lookup/row-number
and deterministic-ID column actions, static arithmetic/percentage/quantity-price/GST/date
difference/age/classification/group/running-total/lookup calculations, summary statistics,
and typed quality/reconciliation rules. Plans cannot provide executable formulas or code.

Phase 3C completes the deterministic engine layer with strict/union table merges,
category-to-sheet and workbook splitting, selected-sheet export, CSV merge and
CSV-to-Excel, sheet rename and summary-copy workflows, formula-injection-safe CSV/XLSX
writers, polished new workbooks, copy-only style-preserving edits, trusted DuckDB unions,
and structured formula templates. Formula text is generated only by application code;
plans never contain raw Excel formulas.

No cloud provider is required. The application starts in local/offline mode and the AI
planner remains separate from the deterministic executor.

## Development setup

The project requires Python 3.12 and uses the repository `.venv`.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m sheetpilot.app.main
```

Quality checks:

```powershell
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy sheetpilot
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m bandit -c pyproject.toml -r sheetpilot
```

## Safety boundary

Operation plans contain schema-validated data only. Unknown operations, unknown
parameters, executable-code fields, arbitrary SQL, formulas, VBA, shell, and PowerShell
instructions are rejected. Spreadsheet engines receive only verified working copies
inside isolated job workspaces.

See `AGENTS.md` and `SPECIFICATION.md` for the authoritative engineering requirements.
