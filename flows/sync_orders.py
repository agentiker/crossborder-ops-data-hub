"""Prefect order sync flow (TTS order/202309)。

增量策略：按订单创建时间窗口拉取。游标记录上次窗口结束时间，下次从该时间回退 1 小时
（overlap 缓冲，防止边界漏单），到当前时间。重复运行同一窗口靠 order_id/line_item_id
幂等 upsert，不产生重复。
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from prefect import flow, task

from core.db import SessionLocal
from models.base_models import OrderHeader
from platforms.tiktok_shop.client import PLATFORM as TIKTOK_PLATFORM
from platforms.tiktok_shop.client import TikTokShopClient
from platforms.tiktok_shop.schemas import OrderSchema
from services.order_store import upsert_orders
from services.sync_state import get_cursor, record_raw_response, upsert_cursor

RESOURCE = "orders"
OVERLAP = timedelta(hours=1)
DEFAULT_LOOKBACK = timedelta(days=7)

# 订单 search 的分页/排序参数（fetch 与 raw 审计记录共用，保证一致）
PAGE_SIZE = 50
SORT_FIELD = "create_time"
SORT_ORDER = "ASC"


def _log_egress_ip() -> None:
    """打印当前出口 IP（与 TikTok 请求同一代理链路），方便核对 IP 白名单。

    仅用于排查 36009033（IP not in allow list）。查询失败不影响同步主流程。
    """
    import requests

    try:
        # 与 TikTokShopClient 一致：直连、不走代理，确保打印的就是 TikTok 实际看到的出口 IP
        ip = requests.get(
            "https://ifconfig.co/ip", timeout=10, proxies={"http": None, "https": None}
        ).text.strip()
        print(f"出口 IP（需在 TikTok IP 白名单中）: {ip}")
    except Exception as e:  # noqa: BLE001
        print(f"出口 IP 查询失败（不影响同步）: {e}")


def _resolve_window(
    session,
    *,
    country: str,
    shop_id: Optional[str],
    seller_id: Optional[str],
    account_id: Optional[str],
) -> tuple[int, int]:
    """Return (create_time_ge, create_time_lt) Unix seconds for this run."""
    now = datetime.now(timezone.utc)
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


@task(name="fetch-tiktok-orders", retries=3, retry_delay_seconds=60)
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


def flatten_order_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract order dicts from paginated payloads."""
    orders = []
    for page in pages:
        orders.extend(page.get("orders", []))
    return orders


@task(name="validate-orders")
def validate_orders(pages: list[dict[str, Any]]) -> list[OrderSchema]:
    """Pydantic 校验清洗。"""
    valid = []
    for raw in flatten_order_pages(pages):
        try:
            valid.append(OrderSchema.model_validate(raw))
        except Exception as e:  # noqa: BLE001
            print(f"订单校验失败: {e}")
    return valid


@task(name="save-orders-to-db", retries=2, retry_delay_seconds=30)
def save_orders_to_db(
    pages: list[dict[str, Any]],
    orders: list[OrderSchema],
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


@flow(name="tiktok-order-sync", log_prints=True)
def sync_orders_flow(
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
):
    """订单增量同步主流程。"""
    _log_egress_ip()
    session = SessionLocal()
    try:
        create_time_ge, create_time_lt = _resolve_window(
            session,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
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
    sync_orders_flow()
