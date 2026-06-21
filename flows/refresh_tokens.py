"""Token refresh flow for TikTok Shop.

This module provides a Prefect flow to automatically refresh
TikTok Shop access tokens before they expire.
"""

from datetime import datetime, timedelta, timezone

from prefect import flow, task
from sqlalchemy import or_

from core.db import SessionLocal, init_db
from core.tenancy import TENANT_BYPASS, set_current_account
from flows.network import log_egress_ip
from models.base_models import PlatformToken
from platforms.tiktok_shop.client import TikTokShopClient


@task(name="refresh-single-token", log_prints=True)
def refresh_single_token(token_record: PlatformToken) -> dict:
    """Refresh a single TikTok Shop token.

    Args:
        token_record: The PlatformToken record to refresh.

    Returns:
        Token refresh result from TikTok API.
    """
    client = TikTokShopClient(
        country=token_record.country,
        shop_id=token_record.shop_id,
        seller_id=token_record.seller_id,
        account_id=token_record.account_id,
        auto_load_token=False,
    )
    client.refresh_token = token_record.refresh_token
    # 刷新响应不含 shop_cipher，预先载入 DB 旧值，避免刷新后内存态丢 cipher
    # （持久层 save_token 也已对空值兜底，二者双保险）。
    client.shop_cipher = token_record.shop_cipher

    result = client.refresh_access_token()
    return result


@flow(name="refresh-tiktok-tokens", log_prints=True)
def refresh_tokens_flow(hours_before_expiry: int = 24) -> dict:
    """Refresh all TikTok Shop tokens that are about to expire.

    This flow queries all TikTok Shop tokens from the database and
    refreshes those that will expire within the specified hours.

    Args:
        hours_before_expiry: Refresh tokens expiring within this many hours.

    Returns:
        Summary of refresh results.
    """
    log_egress_ip()
    init_db()
    set_current_account(TENANT_BYPASS)  # 扫描全租户 token，不按 account_id 过滤
    session = SessionLocal()

    results = {"success": [], "failed": [], "skipped": [], "needs_reauth": []}

    try:
        # Query tokens expiring within the threshold
        threshold = datetime.now(timezone.utc) + timedelta(hours=hours_before_expiry)
        tokens = (
            session.query(PlatformToken)
            .filter(
                PlatformToken.platform == "tiktok_shop",
                PlatformToken.token_expire_at < threshold,
                PlatformToken.refresh_token.isnot(None),
            )
            .all()
        )

        print(f"找到 {len(tokens)} 个需要刷新的 token")

        for token in tokens:
            try:
                # Check if refresh token itself is expired
                if token.refresh_token_expire_at:
                    refresh_expire = token.refresh_token_expire_at
                    if refresh_expire.tzinfo is None:
                        refresh_expire = refresh_expire.replace(tzinfo=timezone.utc)
                    if refresh_expire < datetime.now(timezone.utc):
                        results["skipped"].append({
                            "scope_key": token.scope_key,
                            "reason": "refresh_token已过期",
                        })
                        continue

                result = refresh_single_token(token)
                results["success"].append({
                    "scope_key": token.scope_key,
                    "shop_cipher": token.shop_cipher,
                })
                print(f"刷新成功: {token.scope_key}")

            except Exception as e:
                results["failed"].append({
                    "scope_key": token.scope_key,
                    "error": str(e),
                })
                print(f"刷新失败: {token.scope_key}, 错误: {e}")

        # 加固告警：已/即将过期但 refresh_token 缺失（NULL/空）的 token——它们被上面的主查询
        # (refresh_token IS NOT NULL) 排除，无法自动刷新，会静默漏刷直到 access_token 到期、
        # 数据同步报错才暴露（2026-06-21 烧了数天无人知）。单独捞出来，flow 末尾抛错触发
        # systemd OnFailure → 飞书告警，提示人工重新授权。
        stuck = (
            session.query(PlatformToken)
            .filter(
                PlatformToken.platform == "tiktok_shop",
                PlatformToken.token_expire_at < threshold,
                or_(
                    PlatformToken.refresh_token.is_(None),
                    PlatformToken.refresh_token == "",
                ),
            )
            .all()
        )
        results["needs_reauth"] = [t.scope_key for t in stuck]

    finally:
        session.close()

    # Print summary
    print(f"\n刷新完成:")
    print(f"  成功: {len(results['success'])}")
    print(f"  失败: {len(results['failed'])}")
    print(f"  跳过: {len(results['skipped'])}")
    print(f"  需重新授权: {len(results['needs_reauth'])}")

    # 有 token 无法自动刷新（无 refresh_token）→ 抛错，让 systemd OnFailure 发飞书告警。
    # 放在 summary 之后：能刷的已尽量刷完、统计已打印，再以失败态收尾提示人工介入。
    if results["needs_reauth"]:
        raise RuntimeError(
            f"{len(results['needs_reauth'])} 个 TikTok token 已/即将过期且无 refresh_token，"
            f"无法自动刷新，需人工重新授权：{results['needs_reauth']}"
        )

    return results


if __name__ == "__main__":
    # Run directly for testing
    result = refresh_tokens_flow()
    print("\n详细结果:")
    for key, values in result.items():
        if values:
            print(f"\n{key}:")
            for item in values:
                print(f"  - {item}")
