# Development Guide

## Mission

This repository is the data foundation for an AI ecommerce operations assistant. The pipeline should pull platform data through official APIs first, preserve raw payloads, normalize into trusted business tables, then expose stable read-only metrics for AI analysis.

## Implementation Rules

- Prefer official platform APIs. Use RPA only as a fallback and keep RPA credentials isolated from API credentials.
- Never let AI be the source of truth for core formulas such as profit, ROI, refund rate, or inventory coverage. Implement formulas in deterministic Python or SQL and let AI explain the results.
- Every platform record must be scoped by `platform`, `country`, and the most specific available account identifier such as `shop_id`, `seller_id`, `ad_account_id`, or `warehouse_id`.
- Preserve raw API responses before transformation so backfills and audits can replay the source data.
- All sync tasks must be idempotent. Re-running the same window should update existing rows instead of creating duplicates.
- Store sync cursors per platform/account/resource so incremental jobs can resume safely.
- Add tests for data contracts, upsert behavior, sync cursor behavior, and financial formulas whenever those areas change.

## Code Style

- Keep platform-specific API logic under `platforms/<platform>/`.
- Keep cross-platform business logic under `services/`.
- Keep AI-facing read helpers under `ai_tools/`; they should be read-only and narrow.
- Use Pydantic models for external payload validation.
- Use SQLAlchemy models for persisted tables.
- Avoid broad refactors while adding a platform connector. Match the existing project shape unless a shared abstraction removes real duplication.

## Verification

- Run `uv run pytest` before handoff.
- If a test requires real credentials or network access, mark it separately and keep the default test suite offline.
- Update the relevant file in `plan/` from `status: active` to `status: completed` only after code and tests for that plan pass.
