---
status: completed
owner: codex
---

# 05 Testing and Delivery Workflow

## Goal

Make the MVP safe to extend by requiring focused tests for sync behavior, data contracts, and financial formulas.

## Scope

- Add pytest as a development dependency.
- Add isolated SQLite-based tests for ORM behavior.
- Document development and verification rules in AGENTS.md.
- Run the full test suite before delivery.

## Done When

- Tests run locally without a MySQL server or platform credentials.
- All active plans are updated to completed after implementation.
- The final handoff includes the test command and result.
