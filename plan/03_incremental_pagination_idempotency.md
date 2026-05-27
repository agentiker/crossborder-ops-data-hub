---
status: completed
owner: codex
---

# 03 Incremental Pagination and Idempotency

## Goal

Make TikTok inventory sync safe for repeated runs and ready for APIs that use pagination or time-window incrementality.

## Scope

- Add sync window helpers.
- Add pagination loop support in the TikTok client.
- Make inventory persistence use deterministic upsert semantics.
- Record sync cursor progress after successful writes.

## Done When

- Re-running a sync does not duplicate inventory facts.
- The flow records raw payloads and cursor progress.
- Tests cover repeated inventory upsert.
