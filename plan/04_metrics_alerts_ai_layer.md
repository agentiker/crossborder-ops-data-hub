---
status: completed
owner: codex
---

# 04 Metrics Alerts AI Layer

## Goal

Expose stable, precomputed business metrics and alerts for the AI assistant instead of asking AI to infer core financial logic from raw tables.

## Scope

- Add daily profit and alert models.
- Add deterministic metric formulas for gross profit and alert severity.
- Add a read-only AI-facing service surface.
- Keep formula code platform-neutral.

## Done When

- Profit and alert rows can be generated from structured inputs.
- The AI assistant can query summaries and open alerts through a narrow read-only interface.
- Tests cover the metric formulas.
