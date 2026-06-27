---
status: completed
owner: codex
---

# 01 Multi-shop Token and Auth

## Goal

Support country/platform/shop/ad-account scoped credentials instead of one token per platform.

## Scope

- Add account-scoped token storage.
- Update TikTok Shop auth and refresh flow so first-time OAuth does not require an existing token.
- Keep token refresh reusable for other platforms.
- Update CLI auth arguments so operators can bind tokens to a country and shop.

## Done When

- Tokens can be saved and loaded by platform, country, shop, seller, and account identifiers.
- TikTok token fetch/refresh uses auth endpoints without pre-existing access token.
- Tests cover token scope behavior.
