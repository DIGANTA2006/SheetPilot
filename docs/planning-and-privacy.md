# Planning and privacy boundary

SheetPilot planning never has file-writing or execution authority. Both the offline parser and
provider-backed planner return an unapproved `PlanProposal`. A separate, digest-bound human
decision is required to create an `ApprovedPlan`, and execution still requires the normal preview
and destructive-step confirmations.

## Local parser

The local rule-based parser runs without a network connection and deliberately understands only a
small, explicit vocabulary. Select one source and sheet when the job is ambiguous, name analysed
columns exactly, and separate actions with `then`, a semicolon, or a new line. Supported clauses are:

- trim, collapse spaces, remove non-printing characters, normalize Unicode, or change text case;
- stable single-column sorting, ascending by default or explicitly descending;
- mark exact duplicates or remove exact duplicates while keeping the first row.

An unknown or partially understood clause rejects the whole draft. The parser never guesses an
operation and never generates Python, formulas, SQL, VBA, PowerShell, or shell commands.

## Provider-backed planning

Provider calls use a prepare-review-approve sequence:

1. The privacy filter creates a bounded payload containing aggregate profile metadata by default.
2. The UI can display the exact serialized context and disclosure manifest before sending it.
3. An approval is bound to the SHA-256 digest of the complete provider request.
4. The provider receives strings only and returns one JSON object matching the restricted response
   schema.
5. SheetPilot parses with size/depth limits, rejects extra fields, validates operation parameters
   against the explicit registry, verifies targets against analysed metadata, and injects the local
   job identity, source hashes, output settings, and privacy choices.

Remote providers require AI-assisted mode plus metadata-upload consent. Anonymized samples replace
values with type/shape markers. Raw samples require separate raw-data consent and are still bounded
and passed through credential, email, phone, and sensitive-column redaction. Client file names are
replaced by source labels. Local providers do not upload data, but use the same reviewable payload
and restricted JSON boundary.

There is intentionally no built-in cloud adapter or API key in this layer. A provider integration
must implement `PlanningProvider`, handle credentials outside plan data, and preserve this boundary.
