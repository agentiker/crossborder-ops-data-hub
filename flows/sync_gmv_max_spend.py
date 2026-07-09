"""GMV Max 广告花费同步 flow（TikTok Marketing API 口径）。

数据源：/gmv_max/report/get/（business-api.tiktok.com），与 Finance 结算口径隔离，
落 fact_gmv_max_spend_daily。见 plan/tiktok-marketing-api-gmv-max.md、memory roi-roas-alert-data-source。

编排（两个官方硬约束的工程处理）：
  1. store_ids 单次最多 1 个 → 逐 (advertiser, store) 循环调用；
  2. 含 stat_time_day 维度时 start~end 窗口 ≤30 天 → 长区间自动按 ≤30 天分片。
每片响应经 normalize 解析 → upsert 入库。raw 响应存审计摘要（复用 record_raw_response）。

时区：报表 stat_time_day 基于广告账户时区（第 4 个时区），normalize 原样保留、本 flow 不搬移
（account_tz 对齐口径待真打确认，见 normalize 顶注 + memory 时区条，勿擅改）。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# 含 stat_time_day 维度时官方窗口上限 30 天，留 1 天余量按 29 天分片（闭区间，含首尾）
MAX_WINDOW_DAYS = 30
SHARD_DAYS = 29


def iter_date_shards(start: date, end: date, shard_days: int = SHARD_DAYS):
    """把 [start, end] 闭区间切成 ≤shard_days+1 天的闭区间片，逐片 yield (s, e)。"""
    if start > end:
        return
    cur = start
    while cur <= end:
        shard_end = min(cur + timedelta(days=shard_days), end)
        yield cur, shard_end
        cur = shard_end + timedelta(days=1)


def sync_gmv_max_spend_flow(
    *,
    account_id: Optional[str] = None,
    advertiser_id: Optional[str] = None,
    store_ids: Optional[list[str]] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    lookback_days: int = 7,
    client=None,
    country: str = "GLOBAL",
    session_factory=None,
) -> int:
    """同步某 account 下 GMV Max 花费到 fact_gmv_max_spend_daily。

    Args:
        account_id: 租户；不传回落 DEFAULT_ACCOUNT。
        advertiser_id: 广告主；不传则用 client.get_advertisers() 自动枚举。
        store_ids: 店铺列表；不传则用 get_gmv_max_stores 自动枚举（筛 is_gmv_max_available）。
        start_date/end_date: 报表窗口（闭区间）；不传则 [today-lookback_days, today]。
        client: 可注入的 TikTokBusinessClient（测试用 mock）；不传则按 account 新建。
    Returns:
        写入的日级行数（跨店/跨片累加）。
    """
    from core.db import SessionLocal, init_db
    from core.tenancy import DEFAULT_ACCOUNT
    from core.timezone import business_today
    from platforms.tiktok_business.client import TikTokBusinessClient
    from platforms.tiktok_business.normalize import parse_gmv_max_report
    from services.gmv_max_spend_store import upsert_gmv_max_spend_daily

    account_id = account_id or DEFAULT_ACCOUNT
    end_date = end_date or business_today()
    start_date = start_date or (end_date - timedelta(days=lookback_days))

    make_session = session_factory or SessionLocal

    if client is None:
        init_db()
        client = TikTokBusinessClient(account_id=account_id, advertiser_id=advertiser_id)
        if not client.access_token:
            client.load_token(advertiser_id=advertiser_id)

    # 枚举广告主
    advertisers = [advertiser_id] if advertiser_id else [
        str(a.get("advertiser_id")) for a in client.get_advertisers()
    ]
    if not advertisers:
        logger.warning("[sync_gmv_max] account=%s 无授权广告主，跳过", account_id)
        return 0

    total_written = 0
    for adv in advertisers:
        # 枚举店铺（筛可用于 GMV Max 的）
        stores = store_ids
        if not stores:
            listed = client.get_gmv_max_stores(adv)
            stores = [
                str(s.get("store_id") or s.get("id"))
                for s in listed
                if s.get("is_gmv_max_available") in (True, "true", None)
            ]
        if not stores:
            logger.warning("[sync_gmv_max] advertiser=%s 无可用店铺，跳过", adv)
            continue

        for store_id in stores:
            for s, e in iter_date_shards(start_date, end_date):
                data = client.get_gmv_max_report(adv, store_id, s.isoformat(), e.isoformat())
                rows = parse_gmv_max_report(data, store_id=store_id, advertiser_id=adv)
                session = make_session()
                try:
                    written = upsert_gmv_max_spend_daily(
                        session, rows,
                        country=country, shop_id=store_id, seller_id=adv, account_id=account_id,
                    )
                    session.commit()
                    total_written += written
                    logger.info(
                        "[sync_gmv_max] adv=%s store=%s %s~%s → %d 行",
                        adv, store_id, s, e, written,
                    )
                except Exception:
                    session.rollback()
                    raise
                finally:
                    session.close()

    logger.info("[sync_gmv_max] account=%s 完成，共写 %d 行", account_id, total_written)
    return total_written


def discover_business_accounts() -> list[dict]:
    """扫全租户 platform_tokens，返回已授权 Marketing API 的 [{account_id, advertiser_id}, ...]。

    我们的 business token 把 advertiser_id 存在 seller_id 槽（见 client.save_token）。
    一个 account 可有多 advertiser → 各一行。空列表 = 尚无授权（审核/授权未完成）。
    """
    from core.db import SessionLocal
    from core.tenancy import TENANT_BYPASS, set_current_account
    from models.base_models import PlatformToken
    from platforms.tiktok_business.client import PLATFORM

    set_current_account(TENANT_BYPASS)
    session = SessionLocal()
    try:
        rows = (
            session.query(PlatformToken)
            .filter(PlatformToken.platform == PLATFORM)
            .all()
        )
        return [
            {"account_id": t.account_id, "advertiser_id": t.seller_id}
            for t in rows
        ]
    finally:
        session.close()


if __name__ == "__main__":
    import argparse

    from core.db import init_db
    from core.tenancy import set_current_account

    parser = argparse.ArgumentParser(
        description="GMV Max 花费同步（Marketing API 口径）。无参数=近 7 天增量；回填用 --since-days N。"
    )
    parser.add_argument("--since-days", type=int, metavar="N", default=7,
                        help="回填最近 N 天（默认 7；含按天维度时单次窗口官方≤30，本 flow 自动分片）")
    args = parser.parse_args()

    init_db()
    accounts = discover_business_accounts()
    if not accounts:
        logger.warning(
            "[sync_gmv_max] platform_tokens 无 tiktok_business 授权，跳过"
            "（客户建 App 过审 + 授权后才有数据）"
        )
    for acc in accounts:
        set_current_account(acc["account_id"])
        sync_gmv_max_spend_flow(
            account_id=acc["account_id"],
            advertiser_id=acc["advertiser_id"],
            lookback_days=args.since_days,
        )
