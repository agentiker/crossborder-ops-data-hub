"""未结算订单预估费用同步 flow（GET /finance/202507/orders/unsettled）。

为「今早出昨日预估利润」供数：拉近 unsettled_lookback_days 天创建的未结算订单的 TikTok 官方
预估费用，写 fact_unsettled_fee。**全量替换**语义（每店每业务日先 DELETE 旧行再插全量），订单
结算后从接口消失即自然消退，无需过期任务，故不走 cursor 增量、每次重拉窗口。

与 sync_ad_spend（结算口径）解耦并存：本表是预估额、那表是真实结算额，3b 按 order_id JOIN 校准。
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from core.retry import retry

logger = logging.getLogger(__name__)

from core.config import settings
from core.db import SessionLocal
from flows.network import log_egress_ip
from platforms.tiktok_shop.client import PLATFORM as TIKTOK_PLATFORM
from platforms.tiktok_shop.client import TikTokShopClient
from services.sync_state import record_raw_response, upsert_cursor
from services.unsettled_fee_store import parse_unsettled_fees, replace_unsettled_for_day

RESOURCE = "unsettled_fee"
PAGE_SIZE = 50
SORT_FIELD = "order_create_time"
SORT_ORDER = "ASC"


def _resolve_window(lookback_days: Optional[int] = None) -> tuple[int, int]:
    """按 order_create_time 取 [今天 − lookback, 今天] 的 Unix 秒窗口（全量替换，不用游标）。

    lookback_days 不为空则覆盖 settings.unsettled_lookback_days（回填可拉更长窗口）。
    """
    now = datetime.now(timezone.utc)
    days = lookback_days if lookback_days is not None else settings.unsettled_lookback_days
    start = now - timedelta(days=days)
    return int(start.timestamp()), int(now.timestamp())


@retry(retries=3, delay_seconds=60)
def fetch_unsettled(
    *,
    search_time_ge: int,
    search_time_lt: int,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """翻页拉未结算交易，收集成 [{"transactions": [...]}]。沙箱 total_count=0 → 空列表。"""
    client = TikTokShopClient(
        country=country, shop_id=shop_id, seller_id=seller_id, account_id=account_id
    )
    pages: list[dict[str, Any]] = []
    for data in client.iter_unsettled_transactions(
        search_time_ge=search_time_ge,
        search_time_lt=search_time_lt,
        page_size=PAGE_SIZE,
        sort_field=SORT_FIELD,
        sort_order=SORT_ORDER,
    ):
        # 字段名生产店复验：优先 transactions，兜底 unsettled_transactions
        txns = data.get("transactions") or data.get("unsettled_transactions") or []
        pages.append({"transactions": txns})
    return pages


def parse_unsettled_task(pages: list[dict[str, Any]]) -> list[dict]:
    """把每笔未结算交易解析成预估费用行（委托 unsettled_fee_store）。"""
    return parse_unsettled_fees(pages)


@retry(retries=2, delay_seconds=30)
def save_unsettled_to_db(
    pages: list[dict[str, Any]],
    rows: list[dict],
    *,
    search_time_ge: int,
    search_time_lt: int,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> int:
    """单事务：记 raw 审计 + 按业务日全量替换 fact_unsettled_fee + 更新 cursor（审计用）。"""
    session = SessionLocal()
    try:
        raw_record = record_raw_response(
            session,
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            resource=RESOURCE,
            method="GET",
            path="/finance/202507/orders/unsettled",
            request_params={"page_size": PAGE_SIZE, "sort_field": SORT_FIELD, "sort_order": SORT_ORDER},
            request_body={"search_time_ge": search_time_ge, "search_time_lt": search_time_lt},
            response_payload={"pages": pages},
            http_status=200,
            business_code="0",
        )

        # 按业务日分组，逐日全量替换（metric_date=None 的行丢弃，记日志）
        by_day: dict[Any, list[dict]] = {}
        skipped = 0
        for row in rows:
            md = row.get("metric_date")
            if md is None:
                skipped += 1
                continue
            by_day.setdefault(md, []).append(row)
        if skipped:
            logger.warning("unsettled: %d 行无 order_create_time，已跳过", skipped)

        total = 0
        for md, day_rows in by_day.items():
            total += replace_unsettled_for_day(
                session,
                day_rows,
                metric_date=md,
                platform=TIKTOK_PLATFORM,
                country=country,
                shop_id=shop_id,
                seller_id=seller_id,
                account_id=account_id,
                raw_response_id=raw_record.id,
            )

        upsert_cursor(
            session,
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            resource=RESOURCE,
            window_end=datetime.fromtimestamp(search_time_lt, tz=timezone.utc),
            extra={"row_count": total, "day_count": len(by_day), "page_count": len(pages)},
        )
        session.commit()
        return total
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def sync_unsettled_fees_flow(
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    lookback_days: Optional[int] = None,
):
    """未结算预估费用同步主流程。lookback_days 不为空 = 覆盖默认回看窗口（回填用）。"""
    log_egress_ip()
    search_time_ge, search_time_lt = _resolve_window(lookback_days)
    pages = fetch_unsettled(
        search_time_ge=search_time_ge,
        search_time_lt=search_time_lt,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    rows = parse_unsettled_task(pages)
    count = save_unsettled_to_db(
        pages,
        rows,
        search_time_ge=search_time_ge,
        search_time_lt=search_time_lt,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    print(f"未结算预估费用同步完成: {count} 笔交易级预估行")
    return count


if __name__ == "__main__":
    import argparse
    from functools import partial

    from flows._shop_discovery import run_for_all_shops

    parser = argparse.ArgumentParser(
        description="未结算预估费用同步（全量替换）。回填用 --lookback-days N 覆盖默认窗口。"
    )
    parser.add_argument("--lookback-days", type=int, metavar="N",
                        help="覆盖 settings.unsettled_lookback_days，拉最近 N 天未结算窗口")
    args = parser.parse_args()

    flow = (partial(sync_unsettled_fees_flow, lookback_days=args.lookback_days)
            if args.lookback_days else sync_unsettled_fees_flow)
    run_for_all_shops(flow)
