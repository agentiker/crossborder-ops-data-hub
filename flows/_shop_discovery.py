"""Shared helpers for flow entry points: discover authorized TikTok shops.

When `__main__` runs without explicit `country`/`shop_id`, defaulting to GLOBAL/None
hits an unauthorized scope and the token-load path silently fails (then the API
returns 403). Instead, look at `platform_tokens` (one row per authorized shop) and
use that scope.

Multi-shop / multi-tenant: `discover_all_shops` + `run_for_all_shops` iterate every
authorized shop (across all tenants), so one `python -m flows.sync_*` covers N shops
under M accounts. `discover_single_shop` is kept for callers that still assume a
single shop (raises on >1).
"""
from __future__ import annotations

from typing import Callable

from core.db import SessionLocal
from core.tenancy import TENANT_BYPASS, set_current_account
from models.base_models import PlatformToken


def discover_all_shops(platform: str = "tiktok_shop") -> list[dict]:
    """Return [{country, shop_id, seller_id, account_id}, ...] for every authorized shop.

    Scans across all tenants (TENANT_BYPASS). Empty list if none authorized.
    """
    set_current_account(TENANT_BYPASS)  # 需扫描全租户 token 来发现 shop
    session = SessionLocal()
    try:
        rows = (
            session.query(PlatformToken)
            .filter(PlatformToken.platform == platform)
            .filter(PlatformToken.shop_id.isnot(None))
            .all()
        )
        return [
            {
                "country": t.country,
                "shop_id": t.shop_id,
                "seller_id": t.seller_id,
                "account_id": t.account_id,
            }
            for t in rows
        ]
    finally:
        session.close()


def discover_single_shop(platform: str = "tiktok_shop") -> dict:
    """Return the only authorized shop's scope dict.

    Raises if zero or >1 shops are authorized — caller must then pass explicit args.
    """
    shops = discover_all_shops(platform)
    if not shops:
        raise RuntimeError(
            f"No authorized shop found in platform_tokens for {platform}. "
            "Run the OAuth flow first, or pass explicit --country/--shop-id."
        )
    if len(shops) > 1:
        raise RuntimeError(
            f"Multiple authorized shops for {platform}: "
            f"{[s['shop_id'] for s in shops]}. Pass --country and --shop-id explicitly."
        )
    return shops[0]


def run_for_all_shops(flow_fn: Callable, platform: str = "tiktok_shop") -> None:
    """Run `flow_fn(**scope)` for every authorized shop, one tenant at a time.

    Per-shop error isolation: one shop failing does not abort the rest. If any shop
    failed, raise SystemExit at the end so systemd `OnFailure` still fires the alert.
    """
    shops = discover_all_shops(platform)
    print(f"Discovered {len(shops)} shop(s) for {platform}")
    failed = []
    for scope in shops:
        try:
            set_current_account(scope["account_id"])  # 逐店切租户：写入/SELECT 隔离正确
            print(f"Syncing shop {scope['shop_id']} (account={scope['account_id']})")
            flow_fn(**scope)
        except Exception as e:  # noqa: BLE001 — 一店失败不阻断其余店
            print(f"[ERROR] shop {scope.get('shop_id')} ({scope.get('account_id')}) failed: {e}")
            failed.append(scope.get("shop_id"))
    if failed:
        raise SystemExit(f"{len(failed)}/{len(shops)} shop(s) failed: {failed}")
