"""Prefect 待发货快照同步 flow（TTS order/202309，order_status=AWAITING_SHIPMENT）。

快照式（参考 flows/sync_inventory.py，无时间窗口）：每次全量拉当前所有待发货单，覆盖
pending_fulfillments 表并删除已离开待发货态的旧行。订单增量同步走 create_time 窗口、抓不到
老订单的状态迁移，故待发货必须独立快照拉取。同批 DomainOrder 顺手写一份进 orders 表，
让历史订单表里这些单的 status/update_time 保持新鲜。
"""

from datetime import datetime, timezone
from typing import Any, Optional

from prefect import flow, task

from core.db import SessionLocal
from core.domain import DomainOrder
from flows.network import log_egress_ip
from platforms.tiktok_shop.client import PLATFORM as TIKTOK_PLATFORM
from platforms.tiktok_shop.client import TikTokShopClient
from platforms.tiktok_shop.normalize import to_domain_orders
from services.fulfillment_store import replace_pending_fulfillments
from services.order_store import upsert_orders
from services.sync_state import record_raw_response, upsert_cursor

RESOURCE = "pending_fulfillments"
ORDERS_PATH = "/order/202309/orders/search"
PENDING_STATUS = "AWAITING_SHIPMENT"

# 订单 search 的分页/排序参数（fetch 与 raw 审计记录共用，保证一致）
PAGE_SIZE = 100
SORT_FIELD = "create_time"
SORT_ORDER = "ASC"


@task(name="fetch-pending-orders", retries=3, retry_delay_seconds=60)
def fetch_pending_orders(
    *,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """全量拉取当前待发货订单分页（按 order_status 过滤、不带时间窗口）。"""
    client = TikTokShopClient(
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    pages = []
    for page in client.iter_orders(
        order_status=PENDING_STATUS,
        page_size=PAGE_SIZE,
        sort_field=SORT_FIELD,
        sort_order=SORT_ORDER,
    ):
        pages.append(page)
    return pages


@task(name="validate-pending-orders")
def validate_orders(pages: list[dict[str, Any]]) -> list[DomainOrder]:
    """原始分页 dict → 平台中立 DomainOrder（展平/校验/容错下沉到 normalize 边界）。"""
    return to_domain_orders(pages)


@task(name="save-pending-fulfillments-to-db", retries=2, retry_delay_seconds=30)
def save_to_db(
    pages: list[dict[str, Any]],
    orders: list[DomainOrder],
    *,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> tuple[int, int]:
    """快照覆盖 pending_fulfillments，顺手刷新 orders 表，记 raw payload 与 cursor。

    单事务：raw 审计 → 快照覆盖（upsert+删 stale）→ orders 表刷新 → cursor。
    Returns (pending_count, removed_count)。
    """
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
            path=ORDERS_PATH,
            request_params={
                "page_size": PAGE_SIZE,
                "sort_field": SORT_FIELD,
                "sort_order": SORT_ORDER,
            },
            request_body={"order_status": PENDING_STATUS},
            response_payload={"pages": pages},
            http_status=200,
            business_code="0",
        )
        pending_count, removed_count = replace_pending_fulfillments(
            session,
            orders,
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            raw_response_id=raw_record.id,
        )
        # 顺手把这批待发货单刷进 orders 表，保持历史订单表里 status/update_time 新鲜。
        upsert_orders(
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
            window_end=datetime.now(timezone.utc),
            extra={
                "pending_count": pending_count,
                "removed_count": removed_count,
                "page_count": len(pages),
            },
        )
        session.commit()
        return pending_count, removed_count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@flow(name="tiktok-fulfillment-sync", log_prints=True)
def sync_fulfillments_flow(
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
):
    """待发货快照同步主流程：拉 AWAITING_SHIPMENT → 校验 → 快照覆盖入库。"""
    log_egress_ip()
    pages = fetch_pending_orders(
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    orders = validate_orders(pages)
    pending_count, removed_count = save_to_db(
        pages,
        orders,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    print(f"待发货快照同步完成: {pending_count} 单待发货, 清理 {removed_count} 已发走")
    return pending_count, removed_count


if __name__ == "__main__":
    from flows._shop_discovery import run_for_all_shops

    run_for_all_shops(sync_fulfillments_flow)
