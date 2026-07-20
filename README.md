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

Phase 3C provides tested deterministic services for strict/union table merges,
category-to-sheet and workbook splitting, selected-sheet export, CSV merge and
CSV-to-Excel, sheet rename and summary-copy workflows, formula-injection-safe CSV/XLSX
writers, polished new workbooks, copy-only style-preserving edits, trusted DuckDB unions,
and structured formula templates. The desktop plan currently exposes the registered table
operations; several whole-file workflow services still require dedicated UI. Formula text
is generated only by application code; plans never contain raw Excel formulas.

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

Phase 7 adds an offline instruction planner for a deliberately bounded, documented
vocabulary of text cleaning, value standardization, row filtering, multi-column sorting,
and exact duplicate handling. Generated proposals
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
processing remains usable when it is absent. Packaging evidence below does not replace the
manual clean-machine checklist required before any production-readiness claim.

Phase 9 provides an exact Windows 64-bit dependency lock, consistent application and PE
version metadata, a UPX-free PyInstaller one-folder build, generated icon resources,
release checksums, and frozen startup, normal-workflow, and invalid-workflow self-tests.
The normal frozen test executes a disposable XLSX transaction and verifies source
preservation, backup manifests, atomic output, reopen validation, reconciliation, and the
aggregate audit. The invalid test proves an unknown operation creates no output, backup,
or audit artifact.

Version 0.1.1 adds a professional hardening pass based on adversarial regression testing:
literal text filters now behave correctly, duplicate handling ignores private preview
metadata, cross-worksheet row identities remain unique through merges, output sheet-name
collisions cannot discard tables, and atomic publication fails closed during destination
races. Plan and provider-request integrity checks are stricter, invalid ranges and unsafe
file names are rejected early, formula retention preserves calculation semantics, and the
configured history-retention policy is applied automatically.

Version 0.1.2 closes additional validation and resource-exhaustion gaps. CSV formula
neutralization now covers headers as well as values, null allowlist checks fail closed,
fuzzy matching has an explicit comparison budget, and preview rejects output schemas that
Excel would reject later. It also expands the safe offline planner, creates `.venv`
automatically, and adds a single-command launcher.

Version 0.1.3 prevents successful-looking workbook corruption: existing formulas are
retained only when their exact formula text and coordinates survive the approved plan, and
formula movement or merged-sheet replacement now fails with a typed preservation error.
`quality.validate` step failures are part of the execution gate instead of warnings only.
This release also repairs stale virtual environments, isolates frozen self-tests from real
user history, applies saved output settings on first launch, produces a checksum-bearing
release ZIP, and adds a transactional cumulative PowerShell source upgrader.

## Development setup

The project requires 64-bit Python 3.12 on Windows. The launcher discovers it, safely
recreates a stale project `.venv`, installs the locked environment, and starts SheetPilot.
Use a process-scoped execution-policy bypass when Windows blocks local scripts; the scripts
never change the machine-wide policy.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start-SheetPilot.ps1 -InstallPythonIfMissing
```

To apply this cumulative release to a newly extracted older SheetPilot source ZIP, verify
the script hash you received and run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Upgrade-SheetPilot-0.1.3.ps1 -InstallPythonIfMissing -BuildRelease
```

The upgrader verifies its embedded payload, refuses unexpected local modifications unless
`-Force` is explicitly supplied, keeps changed-file backups outside the project, restores
source files if setup/check/build fails, and is safe to rerun.

Quality checks:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test.ps1
```

## Windows one-folder package

Run the locked quality gate, build, executable self-tests, PE/version checks, and checksum
generation with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\package.ps1 -InstallPythonIfMissing
```

The development build is written to `dist\SheetPilot\SheetPilot.exe`. The versioned
checksum-bearing release is written to
`release\SheetPilot-0.1.3-win64\SheetPilot.exe`. The distributable archive and its sidecar
digest are `release\SheetPilot-0.1.3-win64.zip` and
`release\SheetPilot-0.1.3-win64.zip.sha256`.

## Known limitations

- A separate clean Windows machine without Python has not yet completed the manual release
  checklist, so this repository should not be described as production-ready.
- No remote planning provider or credential adapter is bundled. The offline rule parser
  intentionally supports only documented cleaning, standardization, filtering, sorting,
  and exact-duplicate clauses.
- Existing formulas are preserved only when the approved workflow leaves their exact text
  and cell coordinates intact. Plans that would move/rebase formulas, and table replacement
  on merged sheets, fail closed and require a different output strategy.
- Top-level validation rules currently target the plan's primary result table. Use an
  explicit `quality.validate` step for each additional sheet that must gate publication.
- Real Excel recalculation and PDF export are exercised on this development machine.
  Approved pivot and trusted-macro paths are covered with controlled fakes and synthetic
  macro-preservation fixtures; no real macro was executed.
- Advanced Excel COM actions are currently a typed service API rather than a dedicated
  desktop screen. All normal CSV/XLSX processing remains available without Excel.
- Whole-file merge/split/rename/export services are tested APIs but do not yet have complete
  desktop workflow screens.
- Release scripts and executables are not Authenticode-signed unless a trusted signing step
  is applied outside this repository; SHA-256 manifests provide integrity, not publisher
  identity.
- Legacy `.xls`, `.xlsb`, `.ods`, scanned PDFs/OCR, and browser automation are deferred.

## Safety boundary

Operation plans contain schema-validated data only. Unknown operations, unknown
parameters, executable-code fields, arbitrary SQL, formulas, VBA, shell, and PowerShell
instructions are rejected. Spreadsheet engines receive only verified working copies
inside isolated job workspaces.

See `AGENTS.md` and `SPECIFICATION.md` for the authoritative engineering requirements.
