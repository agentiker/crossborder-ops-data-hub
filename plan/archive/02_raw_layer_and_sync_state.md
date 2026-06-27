---
status: completed
owner: codex
---

# 02 Raw Layer and Sync State

## Goal

Persist raw API payloads and sync cursors so failed transforms, backfills, and audit checks are possible.

## Scope

- Add raw API response model.
- Add per-interface sync cursor model.
- Store request metadata, response payload, status, and errors.
- Provide small service helpers for recording raw responses and cursor progress.

## Done When

- Every sync flow can record raw payloads before transformation.
- Cursor state can be read and updated idempotently.
- Tests cover raw write and cursor update behavior.
