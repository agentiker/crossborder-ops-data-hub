"""Token refresh flow for TikTok Shop.

This module provides a Prefect flow to automatically refresh
TikTok Shop access tokens before they expire.
"""

from datetime import datetime, timedelta, timezone

from prefect import flow, task

from core.db import SessionLocal, init_db
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
    init_db()
    session = SessionLocal()

    results = {"success": [], "failed": [], "skipped": []}

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

    finally:
        session.close()

    # Print summary
    print(f"\n刷新完成:")
    print(f"  成功: {len(results['success'])}")
    print(f"  失败: {len(results['failed'])}")
    print(f"  跳过: {len(results['skipped'])}")

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
