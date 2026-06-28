"""Order sync flow (TTS order/202309)。

增量策略：按订单创建时间窗口拉取。游标记录上次窗口结束时间，下次从该时间回退 1 小时
（overlap 缓冲，防止边界漏单），到当前时间。重复运行同一窗口靠 order_id/line_item_id
幂等 upsert，不产生重复。
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from core.retry import retry

from core.db import SessionLocal
from core.domain import DomainOrder
from flows.network import log_egress_ip
from platforms.tiktok_shop.client import PLATFORM as TIKTOK_PLATFORM
from platforms.tiktok_shop.client import TikTokShopClient
from platforms.tiktok_shop.normalize import to_domain_orders
from services.order_store import upsert_orders
from services.sync_state import get_cursor, record_raw_response, upsert_cursor

RESOURCE = "orders"
OVERLAP = timedelta(hours=1)
DEFAULT_LOOKBACK = timedelta(days=7)

# 订单 search 的分页/排序参数（fetch 与 raw 审计记录共用，保证一致）
PAGE_SIZE = 50
SORT_FIELD = "create_time"
SORT_ORDER = "ASC"


def _resolve_window(
    session,
    *,
    country: str,
    shop_id: Optional[str],
    seller_id: Optional[str],
    account_id: Optional[str],
    since_days: Optional[int] = None,
) -> tuple[int, int]:
    """Return (create_time_ge, create_time_lt) Unix seconds for this run.

    since_days 不为空时为显式回填：start = now - since_days 天（忽略游标），end = now；
    跑完仍照常更新游标（不破坏后续增量）。默认 None = 游标增量行为。
    """
    now = datetime.now(timezone.utc)
    if since_days is not None:
        start = now - timedelta(days=since_days)
        return int(start.timestamp()), int(now.timestamp())
    cursor = get_cursor(
        session,
        platform=TIKTOK_PLATFORM,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
        resource=RESOURCE,
    )
    if cursor and cursor.window_end:
        start = cursor.window_end.replace(tzinfo=timezone.utc) - OVERLAP
    else:
        start = now - DEFAULT_LOOKBACK
    return int(start.timestamp()), int(now.timestamp())


@retry(retries=3, delay_seconds=60)
def fetch_orders(
    *,
    create_time_ge: int,
    create_time_lt: int,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """拉取订单分页（每页含 orders[]）。"""
    client = TikTokShopClient(
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    pages = []
    for page in client.iter_orders(
        create_time_ge=create_time_ge,
        create_time_lt=create_time_lt,
        page_size=PAGE_SIZE,
        sort_field=SORT_FIELD,
        sort_order=SORT_ORDER,
    ):
        pages.append(page)
    return pages


def validate_orders(pages: list[dict[str, Any]]) -> list[DomainOrder]:
    """原始分页 dict → 平台中立 DomainOrder（展平/校验/容错均下沉到 normalize 边界）。"""
    return to_domain_orders(pages)


@retry(retries=2, delay_seconds=30)
def save_orders_to_db(
    pages: list[dict[str, Any]],
    orders: list[DomainOrder],
    *,
    create_time_ge: int,
    create_time_lt: int,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> tuple[int, int]:
    """写入 MySQL（幂等 upsert）并记录 raw payload 与 cursor。"""
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
            method="POST",
            path="/order/202309/orders/search",
            request_params={
                "page_size": PAGE_SIZE,
                "sort_field": SORT_FIELD,
                "sort_order": SORT_ORDER,
            },
            request_body={
                "create_time_ge": create_time_ge,
                "create_time_lt": create_time_lt,
            },
            response_payload={"pages": pages},
            http_status=200,
            business_code="0",
        )
        order_count, line_count = upsert_orders(
            session,
            orders,
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
            window_end=datetime.fromtimestamp(create_time_lt, tz=timezone.utc),
            extra={"order_count": order_count, "line_item_count": line_count,
                   "page_count": len(pages)},
        )
        session.commit()
        return order_count, line_count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def sync_orders_flow(
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    since_days: Optional[int] = None,
):
    """订单增量同步主流程。since_days 不为空 = 显式回填该天数（不破坏游标增量）。"""
    log_egress_ip()
    session = SessionLocal()
    try:
        create_time_ge, create_time_lt = _resolve_window(
            session,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            since_days=since_days,
        )
    finally:
        session.close()

    pages = fetch_orders(
        create_time_ge=create_time_ge,
        create_time_lt=create_time_lt,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    orders = validate_orders(pages)
    order_count, line_count = save_orders_to_db(
        pages,
        orders,
        create_time_ge=create_time_ge,
        create_time_lt=create_time_lt,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    print(f"订单同步完成: {order_count} 单, {line_count} 行")
    return order_count, line_count


if __name__ == "__main__":
    import argparse
    from functools import partial

    from flows._shop_discovery import run_for_all_shops

    parser = argparse.ArgumentParser(
        description="订单同步。无参数=游标增量；回填用 --since-days N（忽略游标拉最近 N 天，跑完仍更新游标）。"
    )
    parser.add_argument("--since-days", type=int, metavar="N",
                        help="忽略游标，回填最近 N 天订单")
    args = parser.parse_args()

    flow = (partial(sync_orders_flow, since_days=args.since_days)
            if args.since_days else sync_orders_flow)
    run_for_all_shops(flow)
