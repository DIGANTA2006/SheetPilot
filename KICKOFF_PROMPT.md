Read `AGENTS.md` and `SPECIFICATION.md` completely before making any code changes.

Build SheetPilot as one complete engineering mission. Execute the specification internally in sequential phases, but do not stop for my approval after each successful phase.

Before development:

1. Inspect the complete repository and Windows development environment.
2. Verify that `AGENTS.md` and `SPECIFICATION.md` are readable and complete.
3. Inspect Git status and create a safe checkpoint if needed.
4. Briefly report the architecture and internal phase order.

During development:

- Follow the two repository instruction files as authoritative.
- Use the existing `.venv`.
- Implement complete working vertical slices.
- Do not add fake buttons, placeholder implementations, hard-coded demo results, or unfinished features represented as complete.
- Keep AI planning separate from deterministic execution.
- Never execute arbitrary AI-generated code or commands.
- Never overwrite client source files.
- Implement backup, preview, confirmation, validation, reconciliation, audit, and restore protections.
- Keep local/offline mode fully usable.

After every internal phase:

1. Run Ruff.
2. Run static type checking.
3. Run relevant unit and integration tests.
4. Fix failures within scope.
5. Update documentation.
6. Create a clearly named Git commit.
7. Continue automatically only when core checks pass.

When an external credential or unavailable application blocks one feature, implement a safe abstraction or fallback, document it, and continue with unaffected work.

Complete all feasible phases, including the PyInstaller one-folder build and startup smoke test.

At completion provide:

- completed and incomplete phases;
- exact test, lint, type-check, and security results;
- Git commits;
- source launch command;
- executable location;
- known limitations;
- next recommended actions.

Do not claim completion unless the implementation and evidence support it.

Begin by inspecting the repository now.

