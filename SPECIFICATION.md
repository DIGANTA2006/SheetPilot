# SPECIFICATION.md — SheetPilot Product and Engineering Specification

## 1. Product definition

SheetPilot is a Windows-first Excel and structured-data automation desktop application for freelancers and small teams.

A user should be able to:

1. Add Excel or CSV files.
2. Paste a client's instructions.
3. Analyse the source files without modifying them.
4. Convert the instructions into a restricted operation plan.
5. Review and edit that plan.
6. Preview proposed changes.
7. Approve or reject changes.
8. Execute only registered deterministic operations.
9. Validate and reconcile the result.
10. Export the final workbook and an audit report.
11. Restore the source from a backup when needed.
12. Save a successful workflow as a reusable template.

The application must not promise to complete every possible data-entry job. It should focus on routine, structured Excel and CSV work and clearly flag uncertain or unsupported tasks.

## 2. Core product principles

- Accuracy before speed
- Source-file preservation
- Deterministic execution
- Human approval before destructive work
- Clear before-and-after previews
- Reproducible workflows
- Strong validation and reconciliation
- Privacy by default
- Local/offline capability
- Modular architecture
- Automated tests
- Honest limitation reporting

## 3. Technology stack

### Application

- Python 3.12
- PySide6
- Pydantic
- SQLite
- SQLAlchemy or a small repository layer
- Structured Python logging

### Spreadsheet and data processing

- Polars: primary table operations
- pandas: compatibility fallback
- openpyxl: modify existing `.xlsx` and `.xlsm`
- XlsxWriter: create new professional reports
- DuckDB: large CSV and analytical operations
- pywin32: optional Microsoft Excel automation

### Quality and packaging

- pytest
- pytest-qt
- Ruff
- Pyright or mypy
- Bandit or an equivalent basic security scanner
- PyInstaller
- PowerShell build and test scripts

## 4. Required architecture

Use this workflow:

Source files + client instructions
→ non-destructive file analysis
→ restricted operation plan
→ schema and safety validation
→ plan review
→ before-and-after preview
→ explicit approval
→ deterministic execution
→ validation and reconciliation
→ atomic export
→ audit report and workflow template

Separate these subsystems:

- Desktop UI
- Job orchestration
- File profiling
- Operation-plan schema
- Operation registry
- Preview engine
- Execution engine
- Spreadsheet engines
- Validation and reconciliation
- Storage
- Security
- AI planner
- Reporting

The AI planner must never directly modify files.

## 5. Initial supported input and output

### Inputs

- `.xlsx`
- `.xlsm`
- `.csv`

### Outputs

- `.xlsx`
- `.csv`
- Structured JSON audit report
- Optional HTML/PDF audit report later

### Deferred formats

Do not include these in the initial MVP:

- Legacy `.xls`
- OCR
- Handwritten forms
- Scanned PDF extraction
- CAPTCHA work
- Browser form automation
- SAP, Tally, or arbitrary CRM automation

## 6. New Job screen

Provide:

- Drag-and-drop file area
- Browse button
- Multiple-file list
- Remove-file action
- Client instruction text box
- Output format selection
- Deadline field
- Preserve-formatting option
- Local mode / AI-assisted mode
- Privacy consent controls
- Analyse Job button

## 7. Non-destructive file analysis

For each source file report:

- File name
- File size
- SHA-256 hash
- File type
- Sheet names
- Visible and hidden sheets
- Used row and column counts
- Headers
- Duplicate headers
- Data types
- Mixed-type columns
- Blank percentages
- Duplicate counts
- Candidate key columns
- Formula cells
- Formula errors when detectable
- Merged cells
- Protected sheets/workbook
- External links
- Named ranges
- Macro presence
- Suspicious mobile numbers
- Suspicious email addresses
- Invalid or mixed dates
- Inconsistent category values
- Possible spelling variations
- Estimated memory requirements
- Unsupported workbook features

Analysis must never alter the source file.

## 8. Restricted operation-plan schema

Represent plans using versioned Pydantic models.

Top-level fields should include:

- Schema version
- Job ID
- Job name
- Source files
- Steps
- Validations
- Output settings
- Privacy metadata
- Creation time

Each step should include:

- Step ID
- Registered operation name
- Enabled state
- Parameters
- Target file/sheet/columns
- Risk level
- Destructive flag
- Confirmation requirement
- Explanation
- Estimated affected rows
- Dependencies on previous steps

Validation rules:

- Reject unknown operations.
- Reject unknown parameters.
- Reject missing sheets or columns.
- Detect duplicate headers.
- Detect conflicting steps.
- Detect invalid ordering.
- Detect output collisions.
- Detect potential workbook-feature loss.
- Require confirmation for destructive steps.
- Reject arbitrary code and arbitrary SQL.

## 9. Operation registry

Implement operations as independent, typed, testable modules.

Each registered operation must define:

- Name
- Version
- Input schema
- Supported engines
- Destructive status
- Preview method
- Execution method
- Validation method
- Audit description

## 10. Version 1 operations

### Text cleaning

- Trim leading and trailing spaces
- Collapse repeated internal spaces
- Remove non-printing characters
- Normalize Unicode
- Proper case
- Uppercase
- Lowercase
- Exact find-and-replace
- Approved mapping replacement
- Remove selected characters
- Remove approved prefixes or suffixes

### Data standardization

- State-name standardization
- District-name standardization
- Status/category standardization
- Telephone normalization
- Email normalization
- Date normalization
- Numeric-text conversion
- Number-format standardization
- Date-format standardization

Uncertain corrections must be flagged for review, not silently applied.

### Filtering

- Exact match
- Contains
- Starts with
- Ends with
- Numeric comparisons
- Date range
- Blank/non-blank
- Include/exclude listed values

### Sorting

- One or more columns
- Ascending/descending
- Preserve original row order metadata where required

### Duplicate handling

- Exact duplicate rows
- Duplicate keys using selected columns
- Keep first
- Keep last
- Mark only
- Move duplicate rows to another sheet
- Remove with confirmation
- Merge complementary records only using explicit approved rules

Fuzzy matching must remain review-based.

### Column operations

- Rename
- Reorder
- Add
- Remove with confirmation
- Split by delimiter
- Combine columns
- Extract before/after delimiter
- Fixed-position extraction
- Lookup/map from another table
- Add row numbers
- Generate deterministic IDs

### Calculations

- Addition
- Subtraction
- Multiplication
- Division
- Percentage
- Quantity × price
- GST
- Date difference
- Age
- Conditional classification
- Group totals
- Running totals
- Lookup values
- Summary statistics

Support formulas when the output must retain formulas and static values when the client requires final calculated data.

### File and sheet operations

- Merge CSV files
- Merge Excel files
- Combine selected worksheets
- Split table into sheets by category
- Split workbook into separate files
- Rename sheets
- Create summary sheet
- Export selected sheets
- CSV-to-Excel
- Create clean output workbook

### Validation

- Required fields
- Telephone length/pattern
- Email pattern
- Date range
- Numeric range
- Allowed values
- Duplicate ID
- Missing lookup values
- Row-count reconciliation
- Total reconciliation
- Formula-error detection
- Blank-row detection
- Invalid-header detection

## 11. Spreadsheet engines

### Polars engine

Use for:

- Filtering
- Sorting
- Grouping
- Joining
- Aggregation
- Deduplication
- Column transformations
- Large CSV processing

### pandas engine

Use only where compatibility or library support is stronger.

### openpyxl engine

Use for modification of existing workbooks when preserving:

- Worksheets
- Formulas
- Styles
- Borders
- Number formats
- Alignment
- Comments
- Merged cells
- Workbook structure

Detect and report unsupported feature-preservation risks.

### XlsxWriter engine

Use for newly generated files with:

- Styled headers
- Tables
- Freeze panes
- Conditional formatting
- Charts
- Summary dashboards
- Print settings
- Number formats

### DuckDB engine

Use for large CSV data, joins, and aggregations. Only trusted application code may build queries.

### Optional Excel COM engine

Use only when Excel is installed.

Supported purposes:

- Recalculate formulas
- Refresh explicitly approved pivots
- Export sheets to PDF
- Preserve advanced Excel behaviour
- Run only explicitly trusted macros

The application must remain usable without Microsoft Excel.

## 12. Preview system

Before execution show:

- File
- Sheet
- Row
- Column
- Original value
- Proposed value
- Reason
- Risk/warning
- Operation step
- Approval state

Provide:

- Changed-only view
- Deleted-row view
- Warning view
- Search and filter
- Representative sampling
- Total affected counts
- Individual accept/reject where practical
- Mandatory confirmation for destructive operations

## 13. Execution workflow

1. Recalculate the source hash.
2. Abort if the source changed since analysis.
3. Create a timestamped backup.
4. Create an isolated temporary workspace.
5. Copy source files into the workspace.
6. Execute validated enabled steps in order.
7. Create detailed change records.
8. Run validations and reconciliation.
9. Write the output to a temporary output path.
10. Re-open and validate the output.
11. Atomically rename to the final output.
12. Create an audit report.
13. Record job metadata.
14. Clean temporary files safely.
15. Leave the source untouched.

## 14. Result and reconciliation screen

Display:

- Original row count
- Final row count
- Rows changed
- Rows removed
- Rows added
- Duplicates removed
- Invalid records found
- Missing required values
- Sheets created/modified
- Formulas added
- Warning count
- Reconciliation status
- Source and output hashes
- Backup path
- Output path
- Audit report path

Actions:

- Open output folder
- Open audit report
- Restore backup
- Save workflow template
- Repeat with new files

## 15. Saved workflows

Users can save a validated plan as a reusable workflow.

Examples:

- Customer Data Cleanup
- District-Wise Split
- Product Catalogue Cleanup
- Monthly Sales Consolidation
- Invoice Validation
- Duplicate Customer Detection
- CSV Merge and Summary

Templates must expose parameters such as:

- Input sheet
- Target columns
- Grouping column
- Replacement map
- Validation rules
- Output name

Do not permanently bind templates to one file.

## 16. Storage

Use SQLite for:

- Jobs
- Workflow templates
- Settings
- Validation summaries
- Audit metadata
- Application migrations

Do not store complete client spreadsheet content in SQLite.

Job history fields:

- Job ID
- Name
- Created/completed times
- Status
- Source file names and hashes
- Output file names and hashes
- Workflow ID
- Step count
- Warning count
- Validation result
- Backup path
- Application version

## 17. Security requirements

Mandatory:

1. Never overwrite source files.
2. Timestamped backups.
3. SHA-256 verification.
4. Source-change detection.
5. Isolated temporary workspaces.
6. Path and filename sanitization.
7. Path traversal prevention.
8. Approved output directories.
9. No arbitrary Python execution.
10. No arbitrary VBA execution.
11. No arbitrary PowerShell/shell execution.
12. No arbitrary SQL execution.
13. Formula-injection detection.
14. Macro detection and blocking.
15. External-link warnings.
16. Protected/hidden sheet warnings.
17. File-size limits.
18. Row/column limits.
19. Memory estimation.
20. Sensitive-value redaction.
21. Explicit permission before AI upload.
22. Local-only mode.
23. Secure API-key storage where available.
24. Restore functionality.
25. Privacy-conscious audit trails.
26. Archive/XML bomb protection.
27. Atomic output writing.
28. Safe failure behaviour.
29. Unsupported-feature warnings.
30. Client files treated as untrusted.

## 18. Privacy modes

### Local mode

- No cloud AI
- Manual operation selection
- Optional local rule-based parser
- Full functionality for supported deterministic operations

### AI-assisted mode

- Explicit opt-in
- Clearly show what data will be sent
- Prefer headers, schema, metadata, and anonymized samples
- Redact sensitive values
- Confirm before sending raw data
- Provider abstraction for API or local LLM
- AI output restricted to the plan schema

## 19. UI requirements

Navigation:

- New Job
- Saved Workflows
- Job History
- Validation Reports
- Templates
- Settings
- Help

Behaviour:

- Responsive UI
- Background workers
- Progress reporting
- Safe cancellation
- Disable conflicting actions
- Friendly errors
- Separate diagnostic details
- Clean light theme
- Future-ready theme system
- Keyboard accessibility where practical

## 20. Error handling

Typed exceptions:

- Unsupported format
- Corrupt workbook
- Password-protected workbook
- Missing sheet
- Missing column
- Duplicate headers
- Invalid plan
- Conflicting operations
- Insufficient disk space
- Insufficient memory
- Output failure
- Excel COM unavailable
- Formula-preservation risk
- Macro-preservation risk
- User cancellation

On failure:

- Preserve source
- Preserve backup
- Clean temp files where safe
- Keep diagnostic logs
- Explain recovery action
- Do not present partial output as complete

## 21. Logging and audit

Use structured logs.

Do not log:

- Full client rows
- Passwords
- API keys
- OTPs
- Banking information
- Sensitive personal fields

Audit report should include:

- Job summary
- Source metadata
- Operations performed
- Rows affected
- Duplicates removed
- Validation warnings
- Reconciliation
- Output files
- Hashes
- Timestamp
- Application version

JSON audit is mandatory initially. HTML and PDF may be added later.

## 22. Testing

### Unit tests

- Schema validation
- Unknown-operation rejection
- Path security
- Backup and restore
- Hashing
- Formula injection
- Text cleaning
- Date normalization
- Phone/email validation
- Filtering/sorting
- Duplicates
- Columns
- Splitting/merging
- Reconciliation
- Atomic output
- Filename sanitization

### Integration fixtures

Include:

- Multiple sheets
- Formulas
- Styles
- Merged cells
- Hidden sheets
- Duplicate rows
- Blank rows
- Invalid dates
- Mixed types
- External links
- Macro-enabled workbook
- Large CSV

Verify:

- Source unchanged
- Backup created
- Output opens
- Counts reconcile
- Formatting preserved where promised
- Destructive steps require approval
- Failed jobs do not produce misleading output

### Regression policy

Every confirmed workbook defect must receive:

1. A reproducing fixture
2. A failing test
3. A fix
4. A permanent regression test

## 23. Proposed repository structure

sheetpilot/
├── app/
│   ├── main.py
│   ├── bootstrap.py
│   ├── config.py
│   └── version.py
├── ui/
│   ├── main_window.py
│   ├── pages/
│   ├── dialogs/
│   ├── models/
│   ├── workers/
│   └── widgets/
├── core/
│   ├── job_manager.py
│   ├── file_profiler.py
│   ├── plan_schema.py
│   ├── plan_validator.py
│   ├── operation_registry.py
│   ├── executor.py
│   ├── preview_engine.py
│   ├── reconciliation.py
│   ├── quality_checker.py
│   └── audit_report.py
├── engines/
│   ├── base.py
│   ├── polars_engine.py
│   ├── pandas_engine.py
│   ├── openpyxl_engine.py
│   ├── xlsxwriter_engine.py
│   ├── duckdb_engine.py
│   └── excel_com_engine.py
├── operations/
│   ├── base.py
│   ├── cleaning.py
│   ├── standardisation.py
│   ├── filtering.py
│   ├── sorting.py
│   ├── duplicates.py
│   ├── columns.py
│   ├── calculations.py
│   ├── validation.py
│   ├── splitting.py
│   └── merging.py
├── ai/
│   ├── provider.py
│   ├── planner.py
│   ├── prompt_builder.py
│   ├── response_parser.py
│   ├── privacy_filter.py
│   └── rule_based_parser.py
├── storage/
│   ├── database.py
│   ├── models.py
│   ├── repositories.py
│   └── migrations/
├── security/
│   ├── file_guard.py
│   ├── path_guard.py
│   ├── macro_guard.py
│   ├── formula_guard.py
│   ├── archive_guard.py
│   ├── privacy.py
│   └── hashing.py
├── resources/
│   ├── icons/
│   ├── themes/
│   └── templates/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── regression/
│   └── fixtures/
├── scripts/
│   ├── setup.ps1
│   ├── test.ps1
│   ├── build.ps1
│   └── package.ps1
├── pyproject.toml
├── README.md
├── AGENTS.md
├── SPECIFICATION.md
├── LICENSE
└── .gitignore

## 24. Development phases

### Phase 1 — Foundation

- Project structure
- Configuration
- Logging
- Plan models
- Operation registry
- SQLite initialization
- Hashing
- Backup
- Temporary workspace
- Path security
- Formula-injection detection
- Minimal UI
- Tests

### Phase 2 — File analysis

- Excel/CSV import
- Workbook profiling
- Sheet and column profiling
- Duplicate/blank detection
- Formula/macro/hidden-sheet/external-link detection
- Analysis UI
- Tests

### Phase 3 — Deterministic operations

- Cleaning
- Standardization
- Filtering
- Sorting
- Duplicate handling
- Column operations
- Validation
- Export
- Tests

### Phase 4 — Preview and reconciliation

- Change records
- Before-and-after preview
- Confirmation
- Row and total reconciliation
- Audit report
- Atomic writing
- Tests

### Phase 5 — Professional UI

- New Job
- Analysis
- Plan
- Preview
- Results
- Workers
- Progress
- Cancellation
- Error handling
- Tests

### Phase 6 — Saved workflows and history

- Templates
- Parameters
- Job history
- Repeat job
- Validation history
- Tests

### Phase 7 — Natural-language planner

- Provider abstraction
- Local rule-based parser
- Privacy controls
- Restricted JSON
- Schema validation
- Human approval
- Tests

### Phase 8 — Advanced Excel support

- Optional COM engine
- Recalculation
- Approved pivot refresh
- PDF export
- `.xlsm` preservation tests
- Trusted macro controls

### Phase 9 — Packaging and audit

- PyInstaller one-folder build
- Icon and version metadata
- Build scripts
- Clean-machine test guidance
- Startup smoke test
- Security audit
- Release checklist

Codex may execute these as one continuous mission, but must keep separate tests and Git commits per phase.

## 25. Build process

Use a PowerShell build script that:

1. Verifies Python 3.12.
2. Activates `.venv`.
3. Installs locked dependencies.
4. Runs Ruff.
5. Runs type checking.
6. Runs tests.
7. Runs security checks.
8. Stops on failure.
9. Builds only after checks pass.
10. Produces a one-folder build first.
11. Generates SHA-256 checksums.
12. Reports exact output location.

Initial command:

`pyinstaller --noconfirm --clean --windowed --name SheetPilot app/main.py`

Add one-file packaging only after one-folder packaging is reliable.

## 26. Final completion report

At the end report:

- Completed phases
- Partial/incomplete features
- Changed files
- Git commits
- Exact lint/type/test/security results
- Source launch command
- Build command
- Executable path
- Startup test result
- Known limitations
- Classification: development, personal testing, beta, or production

Never claim full completion without implementation and evidence.

