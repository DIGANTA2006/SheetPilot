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

Phase 4 adds the transactional workflow: deterministic previews and change digests,
step-specific destructive confirmation, practical per-cell rejection, source rehashing,
verified backups, isolated re-execution, validation and reconciliation, same-directory
staging, reopen validation, no-overwrite publication, failed-artifact quarantine, and an
aggregate-only JSON audit. A validation or audit failure never produces a completed job.

Phase 5 provides the complete local desktop job flow: validated intake and background
analysis, a registry-backed plan builder and strict JSON review, searchable before/after
preview, individual value-change rejection, destructive-step confirmation, background
transactional execution, reconciliation results, diagnostic details, and hash-verified
backup restore. Long-running actions disable conflicting controls and offer safe
cancellation at defined boundaries; once an atomic execution transaction begins, it is
allowed to finish so a committed result is never hidden from the user.

Phase 6 adds durable, privacy-conscious local operations: transactional SQLite
migrations, aggregate job and validation history, audit/output identities, allowlisted
settings, file-independent workflow templates, and typed reusable parameters for sheets,
columns, groupings, mappings, validation rules, and output names. The desktop navigation
provides saved-workflow, template, history, validation, settings, and offline help screens.
Successful plans can be saved without client paths, hashes, rows, or upload consents, then
rebound to newly analysed files and reviewed through the same preview and approval flow.

Phase 7 adds an offline instruction planner for an intentionally small, documented
vocabulary of text cleaning, sorting, and exact duplicate handling. Generated proposals
contain restricted schema-validated JSON only, stay separate from execution, remain bound
to analysed source identities, and require explicit digest-bound human approval before
preview. The provider abstraction, bounded JSON parser, metadata disclosure manifest,
redaction, and consent gates are implemented and tested; no remote provider or credential
is bundled, so provider-backed planning is visibly unavailable and local/manual planning
continues to work without internet access.

Phase 8 hardens advanced workbook support behind an optional Windows Excel COM service.
Recalculation, explicitly named pivot refresh, single-sheet PDF export, and hash-bound
trusted-macro execution operate only on validated job-owned working copies, with automatic
macros disabled on open. Every artifact uses same-directory staging, format validation,
atomic no-clobber publication, and failed-stage quarantine. Synthetic `.xlsm` regressions
prove VBA payload preservation without execution or extension spoofing. On the development
machine, a real installed Excel instance successfully recalculates a disposable workbook,
exports and validates a PDF, preserves the source hash and timestamp, and exits without an
orphan process; pivot and trusted-macro execution remain fixture-driven safety tests so no
unknown macro or client workbook is ever run.

No cloud provider is required. The application starts in local/offline mode and the AI
planner remains separate from the deterministic executor. Excel is optional and local
processing remains usable when it is absent. Frozen packaging is the remaining phase and
is not described as complete until its build and executable tests pass.

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
