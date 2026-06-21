"""Shared helper for flow entry points: discover the single authorized TikTok shop.

When `__main__` runs without explicit `country`/`shop_id`, defaulting to GLOBAL/None
hits an unauthorized scope and the token-load path silently fails (then the API
returns 403). Instead, look at `platform_tokens` (one row per authorized shop) and
use that scope.

Once there are multiple authorized shops, callers must pass explicit args — this
helper raises rather than guess.
"""
from __future__ import annotations

from core.db import SessionLocal
from core.tenancy import TENANT_BYPASS, set_current_account
from models.base_models import PlatformToken


def discover_single_shop(platform: str = "tiktok_shop") -> dict:
    """Return {country, shop_id, seller_id, account_id} for the only authorized shop.

    Raises if zero or >1 shops are authorized — caller must then pass explicit args.
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
        if not rows:
            raise RuntimeError(
                f"No authorized shop found in platform_tokens for {platform}. "
                "Run the OAuth flow first, or pass explicit --country/--shop-id."
            )
        if len(rows) > 1:
            raise RuntimeError(
                f"Multiple authorized shops for {platform}: "
                f"{[r.shop_id for r in rows]}. Pass --country and --shop-id explicitly."
            )
        t = rows[0]
        return {
            "country": t.country,
            "shop_id": t.shop_id,
            "seller_id": t.seller_id,
            "account_id": t.account_id,
        }
    finally:
        session.close()
